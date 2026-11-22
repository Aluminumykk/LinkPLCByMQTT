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
            out += struct.pack("<I", text_key_hash(src))  # SourceStringHash
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
                ent["src_hash"] == text_key_hash(e["en"]) and actual_tr == expected_tr,
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
