# -*- coding: utf-8 -*-
"""
LinkPLCByMQTT 插件本地化构建 / 校验脚本（纯离线，无 UE 依赖）。

用法：
  python build_linkplcbymqtt_localization.py          # 构建（默认，幂等）
  python build_linkplcbymqtt_localization.py verify   # 离线字节级校验 locres / locmeta

产出（Content/Localization/LinkPLCByMQTT/）：
  LinkPLCByMQTT.manifest            —— 注册 source 条目（GenerateTextLocalizationResource 只编译 manifest 里的条目）
  en/LinkPLCByMQTT.archive          —— 英文（= source 原文）
  en/LinkPLCByMQTT.locres           —— 英文编译资源
  zh-Hans/LinkPLCByMQTT.archive     —— 中文译文
  zh-Hans/LinkPLCByMQTT.po          —— 同步 PO，方便人工审校
  zh-Hans/LinkPLCByMQTT.locres      —— 中文编译资源
  LinkPLCByMQTT.locmeta             —— target 元数据（NativeCulture / 已编译文化列表）

locres / locmeta 由本脚本按 UE5.7 引擎源码格式直接生成（不跑 commandlet）：
  - Runtime/Core/Private/Internationalization/TextLocalizationResource.cpp
  - Runtime/Core/Private/Hash/CityHash.cpp
格式：ELocResVersion::Optimized_CityHash64_UTF16 (=3)，FTextKey = CityHash64(UTF-16LE) + GetTypeHash(u64)。
  注意（2026-11-22 核实）：v3 的 FTextKey 走 SerializeWithHash —— 写的是 4 字节 hash **加** 明文
  SaveKeyString（TextKey.cpp 实测，引擎生成物逐字节一致）；只写 hash 不写明文会让引擎按 FString
  长度误读 KeyCount、流错位加载失败。本脚本 hash + serialize_fstring 的写法是正确的，勿改。

条目来源（运行时 key 规则，参照 Charts extract_charts_entries.py / 插件优化.md 坑 #4/#5）：
  - 显示名 ns=UObjectDisplayNames，key=<UHT 剥前缀类名>:<属性名>（类本身 key=<类名>，
    枚举值 key=<枚举名>.<值名>，枚举名**带 E 前缀**——Charts 无头探针
    probe_enum_result.txt 3/3 实测，坑 #26）；无 DisplayName 元数据时 source =
    FName::NameToDisplayString 计算值（本脚本内置 Python 移植，参与 CRC32 哈希校验）；
  - EMQTTQoS 枚举值带 UMETA(DisplayName)（值为中文混排原文），source = 元数据原文；
  - Category ns=UObjectCategory，key = 原始 Category 字符串（本插件两段均为品牌段，译文保留英文）；
  - 本插件无任何 LOCTEXT/INVTEXT/FText 界面字符串（全量 grep 核实），无源码改动。
  - tooltip 源为 /** */ 横幅注释（无 meta ToolTip），不做 tooltip 注入。

背景与维护约定（仿 USCADAWidget 手工注入模式）：
  - .uplugin 已声明 LocalizationTargets（Name=LinkPLCByMQTT, LoadingPolicy=Editor,
    ConfigGenerationPolicy=Never）；声明后须重启编辑器才加载 locres。
  - 新增文案：改 ENTRIES → 跑本脚本 → 重启编辑器。勿对本插件跑完整 Gather 收集
    （GatherTextFromMetadata 找不到外部插件目录会重建空 manifest 并冲掉注入内容）。
  - 如需用官方管线重新编译 locres：先跑本脚本保住 manifest/archive 内容，再执行
    Config/Localization/LinkPLCByMQTT_Compile.ini（GenerateTextLocalizationResource）。
"""

import json
import os
import struct
import sys

TARGET = "LinkPLCByMQTT"
NATIVE_CULTURE = "en"
CULTURES = ["en", "zh-Hans"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOC_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "Content", "Localization", TARGET))

# ---------------------------------------------------------------------------
# FName::NameToDisplayString 的 Python 移植（与 Charts/EdgeDecalTools 同款，已验证）
# ---------------------------------------------------------------------------
_ARTICLES = ["In", "As", "To", "Or", "At", "On", "If", "Be", "By",
             "The", "For", "And", "With", "When", "From"]


def name_to_display_string(name, is_bool=False):
    out = []
    in_run = False
    was_space = False
    was_open_paren = False
    was_number = False
    was_minus = False
    n = len(name)
    i = 0
    while i < n:
        ch = name[i]
        lower = ch.islower()
        upper = ch.isupper()
        digit = ch.isdigit()
        underscore = ch == "_"

        if i == 0 and is_bool and ch == "b" and n > 1 and name[1].isupper():
            i += 1
            continue

        if (upper or (digit and not was_minus)) and not in_run and not was_open_paren and not was_number:
            if not was_space and len(out) > 0:
                out.append(" ")
                was_space = True
            in_run = True

        if lower:
            in_run = False

        if underscore:
            ch = " "
            in_run = True

        if len(out) == 0:
            ch = ch.upper()
        elif not digit and (was_space or was_open_paren):
            is_article = False
            for art in _ARTICLES:
                al = len(art)
                if (n - i) > al and not name[i + al].islower():
                    if name[i:i + al] == art:
                        is_article = True
                        break
            ch = ch.lower() if is_article else ch.upper()

        was_space = (ch == " ")
        was_open_paren = (ch == "(")
        was_minus = (ch == "-")
        potential_numerical = was_minus or (ch == ".")
        was_number = digit or (was_number and potential_numerical)

        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# 条目表
#   key / 英文 source（与运行时查找形式逐字节一致）/ 中文译文 / 源码位置
# 反射条目 en 由 name_to_display_string 现场计算，保证与引擎 CRC32 校验一致；
# 枚举值 en = UMETA(DisplayName) 原文（运行时 source 即元数据原文，见坑 #5）。
# ---------------------------------------------------------------------------
_DT = "Source/LinkPLCByMQTT/Public/MQTTDataTypes.h"
_BP = "Source/LinkPLCByMQTT/Public/LinkPLCByMQTTBPLibrary.h"


def dn(key, prop_name, zh, path, is_bool=False):
    return {"ns": "UObjectDisplayNames", "key": key,
            "en": name_to_display_string(prop_name, is_bool), "zh": zh, "path": path}


ENTRIES = [
    # ---- UCLASS ULinkPLCByMQTTBPLibrary（UHT 剥 U 前缀 → LinkPLCByMQTTBPLibrary）----
    dn("LinkPLCByMQTTBPLibrary", "LinkPLCByMQTTBPLibrary", "LinkPLCByMQTT 蓝图函数库", _BP),

    # ---- 蓝图函数（18 个，Category = LinkPLCByMQTT，品牌段保留英文）----
    dn("LinkPLCByMQTTBPLibrary:InitMQTTConnection", "InitMQTTConnection",
       "初始化 MQTT 连接", _BP),
    dn("LinkPLCByMQTTBPLibrary:ConnectToMQTT", "ConnectToMQTT",
       "连接 MQTT（多连接，返回连接 ID）", _BP),
    dn("LinkPLCByMQTTBPLibrary:InitMQTTConnectionWithOptions", "InitMQTTConnectionWithOptions",
       "初始化 MQTT 连接（完整选项）", _BP),
    dn("LinkPLCByMQTTBPLibrary:ConnectToMQTTWithOptions", "ConnectToMQTTWithOptions",
       "连接 MQTT（完整选项，返回连接 ID）", _BP),
    dn("LinkPLCByMQTTBPLibrary:DisconnectMQTT", "DisconnectMQTT",
       "断开 MQTT 连接", _BP),
    dn("LinkPLCByMQTTBPLibrary:IsMQTTConnected", "IsMQTTConnected",
       "MQTT 是否已连接", _BP),
    dn("LinkPLCByMQTTBPLibrary:CloseMQTTConnection", "CloseMQTTConnection",
       "关闭 MQTT 连接", _BP),
    dn("LinkPLCByMQTTBPLibrary:IsConnected", "IsConnected",
       "是否已连接", _BP),
    dn("LinkPLCByMQTTBPLibrary:GetLastErrorMessage", "GetLastErrorMessage",
       "获取最近错误信息", _BP),
    dn("LinkPLCByMQTTBPLibrary:PublishMQTTMessage", "PublishMQTTMessage",
       "发布 MQTT 消息（字节）", _BP),
    dn("LinkPLCByMQTTBPLibrary:PublishMQTTString", "PublishMQTTString",
       "发布 MQTT 消息（字符串）", _BP),
    dn("LinkPLCByMQTTBPLibrary:SubscribeMQTT", "SubscribeMQTT",
       "订阅 MQTT 主题", _BP),
    dn("LinkPLCByMQTTBPLibrary:UnsubscribeMQTT", "UnsubscribeMQTT",
       "取消订阅", _BP),
    dn("LinkPLCByMQTTBPLibrary:PumpMQTT", "PumpMQTT",
       "驱动网络循环（Pump）", _BP),
    dn("LinkPLCByMQTTBPLibrary:TryReadMQTTMessage", "TryReadMQTTMessage",
       "读取入站消息（字节）", _BP),
    dn("LinkPLCByMQTTBPLibrary:TryReadMQTTString", "TryReadMQTTString",
       "读取入站消息（字符串）", _BP),
    dn("LinkPLCByMQTTBPLibrary:GetBundledCACertPath", "GetBundledCACertPath",
       "获取内置 CA 证书 Bundle 路径", _BP),
    dn("LinkPLCByMQTTBPLibrary:EnumerateCACertificates", "EnumerateCACertificates",
       "枚举可用 CA 证书", _BP),

    # ---- USTRUCT FMQTTConnectOptions（UHT 剥 F 前缀 → MQTTConnectOptions）----
    dn("MQTTConnectOptions", "MQTTConnectOptions", "MQTT 连接选项", _DT),

    # ---- 结构体属性（18 个，Category = MQTT）----
    dn("MQTTConnectOptions:Host", "Host", "Broker 地址", _DT),
    dn("MQTTConnectOptions:Port", "Port", "端口", _DT),
    dn("MQTTConnectOptions:ClientId", "ClientId", "客户端 ID", _DT),
    dn("MQTTConnectOptions:KeepAliveSeconds", "KeepAliveSeconds", "心跳间隔（秒）", _DT),
    dn("MQTTConnectOptions:Username", "Username", "用户名", _DT),
    dn("MQTTConnectOptions:Password", "Password", "密码", _DT),
    dn("MQTTConnectOptions:bCleanSession", "bCleanSession", "清理会话", _DT, is_bool=True),
    dn("MQTTConnectOptions:bUseLWT", "bUseLWT", "启用 LWT 遗嘱", _DT, is_bool=True),
    dn("MQTTConnectOptions:LWTTopic", "LWTTopic", "遗嘱主题", _DT),
    dn("MQTTConnectOptions:LWTPayload", "LWTPayload", "遗嘱内容", _DT),
    dn("MQTTConnectOptions:LWTQoS", "LWTQoS", "遗嘱 QoS", _DT),
    dn("MQTTConnectOptions:bLWTRetain", "bLWTRetain", "遗嘱作为保留消息", _DT, is_bool=True),
    dn("MQTTConnectOptions:bUseTLS", "bUseTLS", "启用 TLS 加密", _DT, is_bool=True),
    dn("MQTTConnectOptions:CACertPath", "CACertPath", "CA 证书路径", _DT),
    dn("MQTTConnectOptions:CACertPem", "CACertPem", "CA 证书 PEM 文本", _DT),
    dn("MQTTConnectOptions:ClientCertPath", "ClientCertPath", "客户端证书路径", _DT),
    dn("MQTTConnectOptions:ClientKeyPath", "ClientKeyPath", "客户端私钥路径", _DT),
    dn("MQTTConnectOptions:bAllowInsecure", "bAllowInsecure", "跳过证书校验（仅限测试）", _DT, is_bool=True),

    # ---- UENUM EMQTTQoS 枚举值（source = UMETA(DisplayName) 原文）----
    {"ns": "UObjectDisplayNames", "key": "EMQTTQoS.AtMostOnce",
     "en": "QoS 0 - 最多一次 (At Most Once)", "zh": "QoS 0 - 最多一次", "path": _DT + "(12)"},
    {"ns": "UObjectDisplayNames", "key": "EMQTTQoS.AtLeastOnce",
     "en": "QoS 1 - 至少一次 (At Least Once)", "zh": "QoS 1 - 至少一次", "path": _DT + "(13)"},
    {"ns": "UObjectDisplayNames", "key": "EMQTTQoS.ExactlyOnce",
     "en": "QoS 2 - 恰好一次 (Exactly Once)", "zh": "QoS 2 - 恰好一次", "path": _DT + "(14)"},

    # ---- Category（UObjectCategory，key = 原始 Category 串；两段均为品牌段，译文保留英文）----
    {"ns": "UObjectCategory", "key": "MQTT", "en": "MQTT", "zh": "MQTT",
     "path": _DT + " / " + _BP},
    {"ns": "UObjectCategory", "key": "LinkPLCByMQTT", "en": "LinkPLCByMQTT", "zh": "LinkPLCByMQTT",
     "path": _BP},
]

# ---------------------------------------------------------------------------
# CityHash64 —— 逐行移植 UE Runtime/Core/Private/Hash/CityHash.cpp（小端）
# ---------------------------------------------------------------------------
_M64 = 0xFFFFFFFFFFFFFFFF
_K0 = 0xC3A5C85C97CB3127
_K1 = 0xB492B66FBE98F273
_K2 = 0x9AE16A3B2F90404F


def _fetch64(s, i):
    return int.from_bytes(s[i:i + 8], "little")


def _fetch32(s, i):
    return int.from_bytes(s[i:i + 4], "little")


def _rotate(val, shift):
    val &= _M64
    return val if shift == 0 else ((val >> shift) | (val << (64 - shift))) & _M64


def _shift_mix(val):
    return (val ^ (val >> 47)) & _M64


def _city_hash_128to_64(lo, hi):
    mul = 0x9DDFEA08EB382D69
    a = ((lo ^ hi) * mul) & _M64
    a ^= a >> 47
    b = ((hi ^ a) * mul) & _M64
    b ^= b >> 47
    b = (b * mul) & _M64
    return b


def _hash_len_16(u, v, mul=None):
    if mul is None:
        return _city_hash_128to_64(u, v)
    a = ((u ^ v) * mul) & _M64
    a ^= a >> 47
    b = ((v ^ a) * mul) & _M64
    b ^= b >> 47
    b = (b * mul) & _M64
    return b


def _hash_len_0_to_16(s):
    length = len(s)
    if length >= 8:
        mul = (_K2 + length * 2) & _M64
        a = (_fetch64(s, 0) + _K2) & _M64
        b = _fetch64(s, length - 8)
        c = (_rotate(b, 37) * mul + a) & _M64
        d = ((_rotate(a, 25) + b) * mul) & _M64
        return _hash_len_16(c, d, mul)
    if length >= 4:
        mul = (_K2 + length * 2) & _M64
        a = _fetch32(s, 0)
        return _hash_len_16((length + (a << 3)) & _M64, _fetch32(s, length - 4), mul)
    if length > 0:
        a = s[0]
        b = s[length >> 1]
        c = s[length - 1]
        y = a + (b << 8)
        z = length + (c << 2)
        return (_shift_mix(((y * _K2) & _M64) ^ ((z * _K0) & _M64)) * _K2) & _M64
    return _K2


def _hash_len_17_to_32(s):
    length = len(s)
    mul = (_K2 + length * 2) & _M64
    a = (_fetch64(s, 0) * _K1) & _M64
    b = _fetch64(s, 8)
    c = (_fetch64(s, length - 8) * mul) & _M64
    d = (_fetch64(s, length - 16) * _K2) & _M64
    return _hash_len_16(
        (_rotate((a + b) & _M64, 43) + _rotate(c, 30) + d) & _M64,
        (a + _rotate((b + _K2) & _M64, 18) + c) & _M64,
        mul,
    )


def _bswap64(v):
    return int.from_bytes(v.to_bytes(8, "little"), "big")


def _hash_len_33_to_64(s):
    length = len(s)
    mul = (_K2 + length * 2) & _M64
    a = (_fetch64(s, 0) * _K2) & _M64
    b = _fetch64(s, 8)
    c = _fetch64(s, length - 24)
    d = _fetch64(s, length - 32)
    e = (_fetch64(s, 16) * _K2) & _M64
    f = (_fetch64(s, 24) * 9) & _M64
    g = _fetch64(s, length - 8)
    h = (_fetch64(s, length - 16) * mul) & _M64
    u = (_rotate((a + g) & _M64, 43) + ((_rotate(b, 30) + c) * 9) & _M64) & _M64
    v = (((a + g) & _M64) ^ d) + f + 1 & _M64
    w = (_bswap64(((u + v) * mul) & _M64) + h) & _M64
    x = (_rotate((e + f) & _M64, 42) + c) & _M64
    y = ((_bswap64(((v + w) * mul) & _M64) + g) * mul) & _M64
    z = (e + f + c) & _M64
    a = (_bswap64(((x + z) * mul + y) & _M64) + b) & _M64
    b = (_shift_mix(((z + a) * mul + d + h) & _M64) * mul) & _M64
    return (b + x) & _M64


def _weak_hash_len_32_with_seeds(s, i, a, b):
    w = _fetch64(s, i)
    x = _fetch64(s, i + 8)
    y = _fetch64(s, i + 16)
    z = _fetch64(s, i + 24)
    a = (a + w) & _M64
    b = _rotate((b + a + z) & _M64, 21)
    c = a
    a = (a + x + y) & _M64
    b = (b + _rotate(a, 44)) & _M64
    return (a + z) & _M64, (b + c) & _M64


def city_hash64(s: bytes) -> int:
    length = len(s)
    if length <= 16:
        return _hash_len_0_to_16(s)
    if length <= 32:
        return _hash_len_17_to_32(s)
    if length <= 64:
        return _hash_len_33_to_64(s)

    x = _fetch64(s, length - 40)
    y = (_fetch64(s, length - 16) + _fetch64(s, length - 56)) & _M64
    z = _hash_len_16((_fetch64(s, length - 48) + length) & _M64, _fetch64(s, length - 24))
    v = _weak_hash_len_32_with_seeds(s, length - 64, length, z)
    w = _weak_hash_len_32_with_seeds(s, length - 32, (y + _K1) & _M64, x)
    x = (x * _K1 + _fetch64(s, 0)) & _M64

    pos = 0
    rem = (length - 1) & ~63
    while True:
        x = (_rotate((x + y + v[0] + _fetch64(s, pos + 8)) & _M64, 37) * _K1) & _M64
        y = (_rotate((y + v[1] + _fetch64(s, pos + 48)) & _M64, 42) * _K1) & _M64
        x ^= w[1]
        y = (y + v[0] + _fetch64(s, pos + 40)) & _M64
        z = (_rotate((z + w[0]) & _M64, 33) * _K1) & _M64
        v = _weak_hash_len_32_with_seeds(s, pos, (v[1] * _K1) & _M64, (x + w[0]) & _M64)
        w = _weak_hash_len_32_with_seeds(s, pos + 32, (z + w[1]) & _M64, (y + _fetch64(s, pos + 16)) & _M64)
        x, z = z, x
        pos += 64
        rem -= 64
        if rem == 0:
            break
    return _hash_len_16(
        (_hash_len_16(v[0], w[0]) + _shift_mix(y) * _K1 + z) & _M64,
        (_hash_len_16(v[1], w[1]) + x) & _M64,
    )


def ue_get_type_hash_u64(v: int) -> int:
    # UE Templates/TypeHash.h：GetTypeHash(uint64) = (uint32)v + (uint32)(v >> 32) * 23
    return ((v & 0xFFFFFFFF) + ((v >> 32) * 23)) & 0xFFFFFFFF


def text_key_hash(s: str) -> int:
    # TextKeyUtil::HashString：CityHash64(UTF-16LE 字节) → GetTypeHash
    return ue_get_type_hash_u64(city_hash64(s.encode("utf-16-le")))


# ---------------------------------------------------------------------------
# SourceStringHash 专用 = FCrc::StrCrc32<TCHAR>（FTextLocalizationResource::HashString，
# TextLocalizationResource.h），逐字节取自引擎 Crc.cpp 的 CRCTablesSB8 字面表；
# 勿与 FTextKey 的 CityHash 混用（2026-11-22 运行时探针实证）。
# ---------------------------------------------------------------------------
_CRCTABLES_SB8_0_3 = [[0, 1996959894, 3993919788, 2567524794, 124634137, 1886057615, 3915621685, 2657392035, 249268274, 2044508324, 3772115230, 2547177864, 162941995, 2125561021, 3887607047, 2428444049, 498536548, 1789927666, 4089016648, 2227061214, 450548861, 1843258603, 4107580753, 2211677639, 325883990, 1684777152, 4251122042, 2321926636, 335633487, 1661365465, 4195302755, 2366115317, 997073096, 1281953886, 3579855332, 2724688242, 1006888145, 1258607687, 3524101629, 2768942443, 901097722, 1119000684, 3686517206, 2898065728, 853044451, 1172266101, 3705015759, 2882616665, 651767980, 1373503546, 3369554304, 3218104598, 565507253, 1454621731, 3485111705, 3099436303, 671266974, 1594198024, 3322730930, 2970347812, 795835527, 1483230225, 3244367275, 3060149565, 1994146192, 31158534, 2563907772, 4023717930, 1907459465, 112637215, 2680153253, 3904427059, 2013776290, 251722036, 2517215374, 3775830040, 2137656763, 141376813, 2439277719, 3865271297, 1802195444, 476864866, 2238001368, 4066508878, 1812370925, 453092731, 2181625025, 4111451223, 1706088902, 314042704, 2344532202, 4240017532, 1658658271, 366619977, 2362670323, 4224994405, 1303535960, 984961486, 2747007092, 3569037538, 1256170817, 1037604311, 2765210733, 3554079995, 1131014506, 879679996, 2909243462, 3663771856, 1141124467, 855842277, 2852801631, 3708648649, 1342533948, 654459306, 3188396048, 3373015174, 1466479909, 544179635, 3110523913, 3462522015, 1591671054, 702138776, 2966460450, 3352799412, 1504918807, 783551873, 3082640443, 3233442989, 3988292384, 2596254646, 62317068, 1957810842, 3939845945, 2647816111, 81470997, 1943803523, 3814918930, 2489596804, 225274430, 2053790376, 3826175755, 2466906013, 167816743, 2097651377, 4027552580, 2265490386, 503444072, 1762050814, 4150417245, 2154129355, 426522225, 1852507879, 4275313526, 2312317920, 282753626, 1742555852, 4189708143, 2394877945, 397917763, 1622183637, 3604390888, 2714866558, 953729732, 1340076626, 3518719985, 2797360999, 1068828381, 1219638859, 3624741850, 2936675148, 906185462, 1090812512, 3747672003, 2825379669, 829329135, 1181335161, 3412177804, 3160834842, 628085408, 1382605366, 3423369109, 3138078467, 570562233, 1426400815, 3317316542, 2998733608, 733239954, 1555261956, 3268935591, 3050360625, 752459403, 1541320221, 2607071920, 3965973030, 1969922972, 40735498, 2617837225, 3943577151, 1913087877, 83908371, 2512341634, 3803740692, 2075208622, 213261112, 2463272603, 3855990285, 2094854071, 198958881, 2262029012, 4057260610, 1759359992, 534414190, 2176718541, 4139329115, 1873836001, 414664567, 2282248934, 4279200368, 1711684554, 285281116, 2405801727, 4167216745, 1634467795, 376229701, 2685067896, 3608007406, 1308918612, 956543938, 2808555105, 3495958263, 1231636301, 1047427035, 2932959818, 3654703836, 1088359270, 936918000, 2847714899, 3736837829, 1202900863, 817233897, 3183342108, 3401237130, 1404277552, 615818150, 3134207493, 3453421203, 1423857449, 601450431, 3009837614, 3294710456, 1567103746, 711928724, 3020668471, 3272380065, 1510334235, 755167117], [0, 421212481, 842424962, 724390851, 1684849924, 2105013317, 1448781702, 1329698503, 3369699848, 3519200073, 4210026634, 3824474571, 2897563404, 3048111693, 2659397006, 2274893007, 1254232657, 1406739216, 2029285587, 1643069842, 783210325, 934667796, 479770071, 92505238, 2182846553, 2600511768, 2955803355, 2838940570, 3866582365, 4285295644, 3561045983, 3445231262, 2508465314, 2359236067, 2813478432, 3198777185, 4058571174, 3908292839, 3286139684, 3670389349, 1566420650, 1145479147, 1869335592, 1987116393, 959540142, 539646703, 185010476, 303839341, 3745920755, 3327985586, 3983561841, 4100678960, 3140154359, 2721170102, 2300350837, 2416418868, 396344571, 243568058, 631889529, 1018359608, 1945336319, 1793607870, 1103436669, 1490954812, 4034481925, 3915546180, 3259968903, 3679722694, 2484439553, 2366552896, 2787371139, 3208174018, 950060301, 565965900, 177645455, 328046286, 1556873225, 1171730760, 1861902987, 2011255754, 3132841300, 2745199637, 2290958294, 2442530455, 3738671184, 3352078609, 3974232786, 4126854035, 1919080284, 1803150877, 1079293406, 1498383519, 370020952, 253043481, 607678682, 1025720731, 1711106983, 2095471334, 1472923941, 1322268772, 26324643, 411738082, 866634785, 717028704, 2904875439, 3024081134, 2668790573, 2248782444, 3376948395, 3495106026, 4219356713, 3798300520, 792689142, 908347575, 487136116, 68299317, 1263779058, 1380486579, 2036719216, 1618931505, 3890672638, 4278043327, 3587215740, 3435896893, 2206873338, 2593195963, 2981909624, 2829542713, 998479947, 580430090, 162921161, 279890824, 1609522511, 1190423566, 1842954189, 1958874764, 4082766403, 3930137346, 3245109441, 3631694208, 2536953671, 2385372678, 2768287173, 3155920004, 1900120602, 1750776667, 1131931800, 1517083097, 355290910, 204897887, 656092572, 1040194781, 3113746450, 2692952403, 2343461520, 2461357009, 3723805974, 3304059991, 4022511508, 4141455061, 2919742697, 3072101800, 2620513899, 2234183466, 3396041197, 3547351212, 4166851439, 3779471918, 1725839073, 2143618976, 1424512099, 1307796770, 45282277, 464110244, 813994343, 698327078, 3838160568, 4259225593, 3606301754, 3488152955, 2158586812, 2578602749, 2996767038, 2877569151, 740041904, 889656817, 506086962, 120682355, 1215357364, 1366020341, 2051441462, 1667084919, 3422213966, 3538019855, 4190942668, 3772220557, 2945847882, 3062702859, 2644537544, 2226864521, 52649286, 439905287, 823476164, 672009861, 1733269570, 2119477507, 1434057408, 1281543041, 2167981343, 2552493150, 3004082077, 2853541596, 3847487515, 4233048410, 3613549209, 3464057816, 1239502615, 1358593622, 2077699477, 1657543892, 764250643, 882293586, 532408465, 111204816, 1585378284, 1197851309, 1816695150, 1968414767, 974272232, 587794345, 136598634, 289367339, 2527558116, 2411481253, 2760973158, 3179948583, 4073438432, 3956313505, 3237863010, 3655790371, 347922877, 229101820, 646611775, 1066513022, 1892689081, 1774917112, 1122387515, 1543337850, 3697634229, 3313392372, 3998419255, 4148705398, 3087642289, 2702352368, 2319436851, 2468674930], [0, 29518391, 59036782, 38190681, 118073564, 114017003, 76381362, 89069189, 236147128, 265370511, 228034006, 206958561, 152762724, 148411219, 178138378, 190596925, 472294256, 501532999, 530741022, 509615401, 456068012, 451764635, 413917122, 426358261, 305525448, 334993663, 296822438, 275991697, 356276756, 352202787, 381193850, 393929805, 944588512, 965684439, 1003065998, 973863097, 1061482044, 1049003019, 1019230802, 1023561829, 912136024, 933002607, 903529270, 874031361, 827834244, 815125939, 852716522, 856752605, 611050896, 631869351, 669987326, 640506825, 593644876, 580921211, 551983394, 556069653, 712553512, 733666847, 704405574, 675154545, 762387700, 749958851, 787859610, 792175277, 1889177024, 1901651959, 1931368878, 1927033753, 2006131996, 1985040171, 1947726194, 1976933189, 2122964088, 2135668303, 2098006038, 2093965857, 2038461604, 2017599123, 2047123658, 2076625661, 1824272048, 1836991623, 1866005214, 1861914857, 1807058540, 1786244187, 1748062722, 1777547317, 1655668488, 1668093247, 1630251878, 1625932113, 1705433044, 1684323811, 1713505210, 1742760333, 1222101792, 1226154263, 1263738702, 1251046777, 1339974652, 1310460363, 1281013650, 1301863845, 1187289752, 1191637167, 1161842422, 1149379777, 1103966788, 1074747507, 1112139306, 1133218845, 1425107024, 1429406311, 1467333694, 1454888457, 1408811148, 1379576507, 1350309090, 1371438805, 1524775400, 1528845279, 1499917702, 1487177649, 1575719220, 1546255107, 1584350554, 1605185389, 3778354048, 3774312887, 3803303918, 3816007129, 3862737756, 3892238699, 3854067506, 3833203973, 4012263992, 4007927823, 3970080342, 3982554209, 3895452388, 3924658387, 3953866378, 3932773565, 4245928176, 4241609415, 4271336606, 4283762345, 4196012076, 4225268251, 4187931714, 4166823541, 4076923208, 4072833919, 4035198246, 4047918865, 4094247316, 4123732899, 4153251322, 4132437965, 3648544096, 3636082519, 3673983246, 3678331705, 3732010428, 3753090955, 3723829714, 3694611429, 3614117080, 3601426159, 3572488374, 3576541825, 3496125444, 3516976691, 3555094634, 3525581405, 3311336976, 3298595879, 3336186494, 3340255305, 3260503756, 3281337595, 3251864226, 3222399125, 3410866088, 3398419871, 3368647622, 3372945905, 3427010420, 3448139075, 3485520666, 3456284973, 2444203584, 2423127159, 2452308526, 2481530905, 2527477404, 2539934891, 2502093554, 2497740997, 2679949304, 2659102159, 2620920726, 2650438049, 2562027300, 2574714131, 2603727690, 2599670141, 2374579504, 2353749767, 2383274334, 2412743529, 2323684844, 2336421851, 2298759554, 2294686645, 2207933576, 2186809023, 2149495014, 2178734801, 2224278612, 2236720739, 2266437690, 2262135309, 2850214048, 2820717207, 2858812622, 2879680249, 2934667388, 2938704459, 2909776914, 2897069605, 2817622296, 2788420399, 2759153014, 2780249921, 2700618180, 2704950259, 2742877610, 2730399645, 3049550800, 3020298727, 3057690558, 3078802825, 2999835404, 3004150075, 2974355298, 2961925461, 3151438440, 3121956959, 3092510214, 3113327665, 3168701108, 3172786307, 3210370778, 3197646061], [0, 3099354981, 2852767883, 313896942, 2405603159, 937357362, 627793884, 2648127673, 3316918511, 2097696650, 1874714724, 3607201537, 1255587768, 4067088605, 3772741427, 1482887254, 1343838111, 3903140090, 4195393300, 1118632049, 3749429448, 1741137837, 1970407491, 3452858150, 2511175536, 756094997, 1067759611, 2266550430, 449832999, 2725482306, 2965774508, 142231497, 2687676222, 412010587, 171665333, 2995192016, 793786473, 2548850444, 2237264098, 1038456711, 1703315409, 3711623348, 3482275674, 1999841343, 3940814982, 1381529571, 1089329165, 4166106984, 4029413537, 1217896388, 1512189994, 3802027855, 2135519222, 3354724499, 3577784189, 1845280792, 899665998, 2367928107, 2677414085, 657096608, 3137160985, 37822588, 284462994, 2823350519, 2601801789, 598228824, 824021174, 2309093331, 343330666, 2898962447, 3195996129, 113467524, 1587572946, 3860600759, 4104763481, 1276501820, 3519211397, 1769898208, 2076913422, 3279374443, 3406630818, 1941006535, 1627703081, 3652755532, 1148164341, 4241751952, 3999682686, 1457141531, 247015245, 3053797416, 2763059142, 470583459, 2178658330, 963106687, 735213713, 2473467892, 992409347, 2207944806, 2435792776, 697522413, 3024379988, 217581361, 508405983, 2800865210, 4271038444, 1177467017, 1419450215, 3962007554, 1911572667, 3377213406, 3690561584, 1665525589, 1799331996, 3548628985, 3241568279, 2039091058, 3831314379, 1558270126, 1314193216, 4142438437, 2928380019, 372764438, 75645176, 3158189981, 568925988, 2572515393, 2346768303, 861712586, 3982079547, 1441124702, 1196457648, 4293663189, 1648042348, 3666298377, 3358779879, 1888390786, 686661332, 2421291441, 2196002399, 978858298, 2811169155, 523464422, 226935048, 3040519789, 3175145892, 100435649, 390670639, 2952089162, 841119475, 2325614998, 2553003640, 546822429, 2029308235, 3225988654, 3539796416, 1782671013, 4153826844, 1328167289, 1570739863, 3844338162, 1298864389, 4124540512, 3882013070, 1608431339, 3255406162, 2058742071, 1744848601, 3501990332, 2296328682, 811816591, 584513889, 2590678532, 129869501, 3204563416, 2914283062, 352848211, 494030490, 2781751807, 3078325777, 264757620, 2450577869, 715964072, 941166918, 2158327331, 3636881013, 1618608400, 1926213374, 3396585883, 1470427426, 4011365959, 4255988137, 1158766284, 1984818694, 3471935843, 3695453837, 1693991400, 4180638033, 1100160564, 1395044826, 3952793279, 3019491049, 189112716, 435162722, 2706139399, 1016811966, 2217162459, 2526189877, 774831696, 643086745, 2666061564, 2354934034, 887166583, 2838900430, 294275499, 54519365, 3145957664, 3823145334, 1532818963, 1240029693, 4048895640, 1820460577, 3560857924, 3331051178, 2117577167, 3598663992, 1858283101, 2088143283, 3301633750, 1495127663, 3785470218, 4078182116, 1269332353, 332098007, 2876706482, 3116540252, 25085497, 2628386432, 605395429, 916469259, 2384220526, 2254837415, 1054503362, 745528876, 2496903497, 151290352, 2981684885, 2735556987, 464596510, 1137851976, 4218313005, 3923506883, 1365741990, 3434129695, 1946996346, 1723425172, 3724871409]]

def source_string_hash(s):
    data=s.encode("utf-16-le")
    crc=0xFFFFFFFF
    t0,t1,t2,t3=_CRCTABLES_SB8_0_3
    for i in range(0,len(data),2):
        ch=data[i]|(data[i+1]<<8)
        v=crc^ch
        crc=t3[v&0xFF]^t2[(v>>8)&0xFF]^t1[(v>>16)&0xFF]^t0[v>>24]
    return (~crc)&0xFFFFFFFF


# ---------------------------------------------------------------------------
# UE FString 序列化（Ar << FString / SaveKeyString：纯 ANSI 存正长度 + 字节，否则负长度 + UTF-16LE）
# ---------------------------------------------------------------------------
def serialize_fstring(s: str) -> bytes:
    if all(ord(c) < 0x80 for c in s):
        raw = s.encode("ascii") + b"\x00"
        return struct.pack("<i", len(raw)) + raw
    raw = s.encode("utf-16-le") + b"\x00\x00"
    return struct.pack("<i", -(len(s) + 1)) + raw


def parse_fstring(data: bytes, off: int):
    (save_num,) = struct.unpack_from("<i", data, off)
    off += 4
    if save_num < 0:
        count = -save_num
        s = data[off:off + count * 2].decode("utf-16-le").rstrip("\x00")
        return s, off + count * 2
    s = data[off:off + save_num].decode("ascii").rstrip("\x00")
    return s, off + save_num


# ---------------------------------------------------------------------------
# LocRes v3 写入（FTextLocalizationResource::SaveToArchive）
# ---------------------------------------------------------------------------
LOC_RES_MAGIC = (
    struct.pack("<I", 0x7574140E) + struct.pack("<I", 0xFC034A67)
    + struct.pack("<I", 0x9D90154A) + struct.pack("<I", 0x1B7F37C3)
)
LOC_META_MAGIC = (
    struct.pack("<I", 0xA14CEE4F) + struct.pack("<I", 0x83554868)
    + struct.pack("<I", 0xBD464C6C) + struct.pack("<I", 0x7C50DA70)
)
LOC_RES_VERSION = 3   # ELocResVersion::Optimized_CityHash64_UTF16
LOC_META_VERSION = 2  # ELocMetaVersion::AddedIsUGC


def build_locres(entries):
    """entries: [(namespace, key, source, translation)]，返回字节串。"""
    out = bytearray()
    out += LOC_RES_MAGIC
    out += bytes([LOC_RES_VERSION])
    offset_pos = len(out)
    out += b"\x00" * 8  # LocalizedStringArrayOffset 占位（int64）

    lut = []        # [[string, refcount]]
    lut_index = {}

    def lut_idx(s):
        if s in lut_index:
            i = lut_index[s]
            lut[i][1] += 1
            return i
        i = len(lut)
        lut.append([s, 1])
        lut_index[s] = i
        return i

    ns_groups = {}
    for ns, key, src, tr in entries:
        ns_groups.setdefault(ns, []).append((key, src, tr))

    out += struct.pack("<I", len(entries))          # EntriesCount
    out += struct.pack("<I", len(ns_groups))        # NamespaceCount
    for ns, items in ns_groups.items():
        out += struct.pack("<I", text_key_hash(ns))  # FTextKey::SerializeWithHash
        out += serialize_fstring(ns)
        out += struct.pack("<I", len(items))
        for key, src, tr in items:
            out += struct.pack("<I", text_key_hash(key))
            out += serialize_fstring(key)
            out += struct.pack("<I", source_string_hash(src))  # SourceStringHash
            out += struct.pack("<i", lut_idx(tr))

    str_array_offset = len(out)
    out += struct.pack("<i", len(lut))
    for s, ref_count in lut:
        out += serialize_fstring(s)
        out += struct.pack("<i", ref_count)
    struct.pack_into("<q", out, offset_pos, str_array_offset)
    return bytes(out)


def build_locmeta():
    out = bytearray()
    out += LOC_META_MAGIC
    out += bytes([LOC_META_VERSION])
    out += serialize_fstring(NATIVE_CULTURE)
    out += serialize_fstring(f"{NATIVE_CULTURE}/{TARGET}.locres")
    out += struct.pack("<i", len(CULTURES))
    for culture in CULTURES:
        out += serialize_fstring(culture)
    out += b"\x00"  # bIsUGC
    return bytes(out)


# ---------------------------------------------------------------------------
# LocRes v3 独立解析（校验用，语义与引擎 LoadFromArchive 对齐）
# ---------------------------------------------------------------------------
def parse_locres(data: bytes):
    assert data[:16] == LOC_RES_MAGIC, "LocRes magic 不匹配"
    version = data[16]
    assert version == LOC_RES_VERSION, f"LocRes 版本 {version} != {LOC_RES_VERSION}"
    (str_array_offset,) = struct.unpack_from("<q", data, 17)
    off = 25
    (entries_count,) = struct.unpack_from("<I", data, off)
    off += 4
    (ns_count,) = struct.unpack_from("<I", data, off)
    off += 4

    entries = []
    for _ in range(ns_count):
        (ns_hash,) = struct.unpack_from("<I", data, off)
        off += 4
        ns, off = parse_fstring(data, off)
        assert ns_hash == text_key_hash(ns), f"namespace 哈希不符: {ns!r}"
        (key_count,) = struct.unpack_from("<I", data, off)
        off += 4
        for _ in range(key_count):
            (key_hash,) = struct.unpack_from("<I", data, off)
            off += 4
            key, off = parse_fstring(data, off)
            assert key_hash == text_key_hash(key), f"key 哈希不符: {key!r}"
            (src_hash,) = struct.unpack_from("<I", data, off)
            off += 4
            (lut_index,) = struct.unpack_from("<i", data, off)
            off += 4
            entries.append({"ns": ns, "key": key, "src_hash": src_hash, "lut_index": lut_index})

    assert off == str_array_offset, "字符串数组偏移与实际位置不符"
    (lut_count,) = struct.unpack_from("<i", data, off)
    off += 4
    lut = []
    for _ in range(lut_count):
        s, off = parse_fstring(data, off)
        (ref_count,) = struct.unpack_from("<i", data, off)
        off += 4
        lut.append([s, ref_count])

    assert len(entries) == entries_count, "EntriesCount 与实际条目数不符"
    for e in entries:
        assert 0 <= e["lut_index"] < lut_count, "LocalizedStringIndex 越界"
    return entries, lut


def parse_locmeta(data: bytes):
    assert data[:16] == LOC_META_MAGIC, "LocMeta magic 不匹配"
    assert data[16] == LOC_META_VERSION, "LocMeta 版本不符"
    off = 17
    native, off = parse_fstring(data, off)
    native_locres, off = parse_fstring(data, off)
    (count,) = struct.unpack_from("<i", data, off)
    off += 4
    cultures = []
    for _ in range(count):
        c, off = parse_fstring(data, off)
        cultures.append(c)
    b_is_ugc = data[off]
    return {"native": native, "native_locres": native_locres, "cultures": cultures, "is_ugc": b_is_ugc}


# ---------------------------------------------------------------------------
# Manifest / Archive / PO（UTF-16 JSON 参照引擎 GenerateGatherManifest/Archive 输出，按 namespace 分组）
# ---------------------------------------------------------------------------
def save_utf16_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-16", newline="") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def _group_by_ns(translation_of):
    groups = {}
    for e in ENTRIES:
        groups.setdefault(e["ns"], []).append({
            "Source": {"Text": e["en"]},
            "Translation": {"Text": translation_of(e)},
            "Key": e["key"],
        })
    return groups


def build_manifest():
    children = []
    for e in ENTRIES:
        children.append({
            "Source": {"Text": e["en"]},
            "Keys": [{"Key": e["key"], "Path": f"Manual injection: {e['path']}"}],
        })
    groups = {}
    for e, c in zip(ENTRIES, children):
        groups.setdefault(e["ns"], []).append(c)
    return {"FormatVersion": 1, "Namespace": "",
            "Subnamespaces": [{"Namespace": ns, "Children": cs} for ns, cs in groups.items()]}


def build_archive(translation_of):
    groups = _group_by_ns(translation_of)
    return {"FormatVersion": 2, "Namespace": "",
            "Subnamespaces": [{"Namespace": ns, "Children": cs} for ns, cs in groups.items()]}


def po_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def build_po():
    lines = [
        "# LinkPLCByMQTT Chinese (Simplified) translation.",
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: LinkPLCByMQTT\\n"',
        '"Language: zh-Hans\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
    ]
    for e in ENTRIES:
        lines.append("")
        lines.append(f"#. Key:\t{e['key']}")
        lines.append(f"#. SourceLocation:\tManual injection ({e['path']})")
        lines.append(f'msgctxt "{po_escape(e["ns"] + "," + e["key"])}"')
        lines.append(f'msgid "{po_escape(e["en"])}"')
        lines.append(f'msgstr "{po_escape(e["zh"])}"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 构建 / 校验
# ---------------------------------------------------------------------------
def do_build():
    os.makedirs(LOC_ROOT, exist_ok=True)

    save_utf16_json(os.path.join(LOC_ROOT, f"{TARGET}.manifest"), build_manifest())
    print(f"写入 {os.path.relpath(os.path.join(LOC_ROOT, TARGET + '.manifest'), SCRIPT_DIR)}")

    for culture in CULTURES:
        culture_dir = os.path.join(LOC_ROOT, culture)
        os.makedirs(culture_dir, exist_ok=True)

        if culture == NATIVE_CULTURE:
            translation_of = lambda e: e["en"]
        else:
            translation_of = lambda e: e["zh"]
        save_utf16_json(os.path.join(culture_dir, f"{TARGET}.archive"), build_archive(translation_of))

        entries = [(e["ns"], e["key"], e["en"], translation_of(e)) for e in ENTRIES]
        locres_path = os.path.join(culture_dir, f"{TARGET}.locres")
        with open(locres_path, "wb") as f:
            f.write(build_locres(entries))
        print(f"写入 {os.path.relpath(locres_path, SCRIPT_DIR)}")

        if culture != NATIVE_CULTURE:
            po_path = os.path.join(culture_dir, f"{TARGET}.po")
            with open(po_path, "w", encoding="utf-8", newline="") as f:
                f.write(build_po())
            print(f"写入 {os.path.relpath(po_path, SCRIPT_DIR)}")

    locmeta_path = os.path.join(LOC_ROOT, f"{TARGET}.locmeta")
    with open(locmeta_path, "wb") as f:
        f.write(build_locmeta())
    print(f"写入 {os.path.relpath(locmeta_path, SCRIPT_DIR)}")

    print("\n下一步：重启编辑器生效（.uplugin 已声明 LocalizationTargets，locres 只在启动时加载）。")


def do_verify():
    failures = 0

    def check(cond, msg):
        nonlocal failures
        if cond:
            print(f"  [OK] {msg}")
        else:
            failures += 1
            print(f"  [FAIL] {msg}")

    print("== locmeta ==")
    with open(os.path.join(LOC_ROOT, f"{TARGET}.locmeta"), "rb") as f:
        meta = parse_locmeta(f.read())
    check(meta["native"] == NATIVE_CULTURE, f"NativeCulture = {meta['native']!r}")
    check(meta["native_locres"] == f"{NATIVE_CULTURE}/{TARGET}.locres", f"NativeLocRes = {meta['native_locres']!r}")
    check(meta["cultures"] == CULTURES, f"CompiledCultures = {meta['cultures']!r}")
    check(meta["is_ugc"] == 0, "bIsUGC = false")

    for culture in CULTURES:
        print(f"== {culture}/{TARGET}.locres ==")
        with open(os.path.join(LOC_ROOT, culture, f"{TARGET}.locres"), "rb") as f:
            entries, lut = parse_locres(f.read())
        check(len(entries) == len(ENTRIES), f"条目数 {len(entries)} == {len(ENTRIES)}")
        by_key = {(e["ns"], e["key"]): e for e in entries}
        check(set(by_key) == {(e["ns"], e["key"]) for e in ENTRIES}, "(ns,key) 集合与 ENTRIES 一致")
        for e in ENTRIES:
            ent = by_key.get((e["ns"], e["key"]))
            if ent is None:
                check(False, f"缺条目 {e['ns']}:{e['key']}")
                continue
            expected_tr = e["en"] if culture == NATIVE_CULTURE else e["zh"]
            actual_tr = lut[ent["lut_index"]][0]
            check(
                ent["src_hash"] == source_string_hash(e["en"]) and actual_tr == expected_tr,
                f"{e['ns']}:{e['key']}: src-hash/译文 正确（译文={actual_tr[:30]!r}...）",
            )

    print(f"\n{failures} failures")
    return failures


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        sys.exit(1 if do_verify() else 0)
    do_build()


if __name__ == "__main__":
    main()
