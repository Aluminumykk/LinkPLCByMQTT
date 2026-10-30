# LinkPLCByMQTT

基于 Unreal Engine 5.7 的 MQTT 客户端通信插件，底层为开源 [Eclipse Mosquitto](https://mosquitto.org/) 的 libmosquitto 2.1.2 客户端库（双授权 EPL-2.0 / EDL-1.0，本插件按 **EDL-1.0**（BSD-3-Clause）使用；客户端源码直接编入插件）。TLS 由 OpenSSL 3.5.x 提供（Apache-2.0，两个 DLL 随插件附带）。

- 同时支持 **C++ 调用** 与 **蓝图节点**
- 支持**多连接**管理（多 broker 并行）
- 连接选项齐全：**LWT 遗嘱**、**持久会话**（clean session 开关）、认证、心跳均可配置
- 只提供基础通信原语：连接、发布、订阅、取消订阅、手动网络循环（Pump）、入站消息队列。**不做异步、线程、轮询、点表、断线重连**——异步与重连策略由调用方自行组织
- 支持 **TCP 明文**（1883）与 **TLS**（8883，OpenSSL 3.5.x，随插件附带 DLL）；不含 WebSocket / SRV 查询（对应 libmosquitto 特性在编译期已关闭）

## 与 S7/Modbus 插件的关键差异

S7/Modbus 是"主站按地址轮询从站"的请求-响应模型；MQTT 是"客户端 ↔ broker"的发布/订阅模型：

- 连接目标是 **broker** 而不是设备本身，参数是 Host/Port/ClientID/KeepAlive/用户名密码，没有机架槽号或单元号
- 没有"读某个地址"的概念：数据通过订阅 topic 后由 broker 推送，因此入站消息必须先 **Pump**（驱动网络循环）再 **TryReadMessage**（逐条取出）
- 发布/订阅以 topic（支持 `+`/`#` 通配符）寻址，消息属性为 QoS（0/1/2）与 retain

## 目录结构

```
LinkPLCByMQTT/
├── LinkPLCByMQTT.uplugin        # 插件描述文件
├── Resources/Icon128.png        # 插件图标
└── Source/
    ├── LinkPLCByMQTT/           # 插件主模块
    │   ├── Public/              # Manager / BPLibrary / MQTTDataTypes
    │   └── Private/             # 实现
    └── ThirdParty/
        ├── LinkPLCByMQTTLibrary/  # libmosquitto 客户端源码模块（直接编译进插件）
        │   ├── LICENSE.txt / edl-v10 / epl-v20   # 授权文本
        │   ├── config.h
        │   └── mosquitto/{include,lib,libcommon,deps}
        └── OpenSSL/               # OpenSSL 3.5.x 预编译子集（TLS 用，Apache-2.0）
            ├── include/  lib/Win64/  bin/Win64/  LICENSE.txt
```

## 安装

将整个 `LinkPLCByMQTT` 文件夹复制到 UE 项目的 `Plugins/` 目录下，重新生成项目文件并编译即可。libmosquitto 源码已随仓库预置，**无需额外配置**。

## 使用方式

### 蓝图节点

`ULinkPLCByMQTTBPLibrary` 提供蓝图函数库，底层统一走 `FLinkPLCByMQTTManager` 的多连接管理。所有节点均为**同步**调用；失败原因用 `GetLastErrorMessage(ConnectionId)` 查询。

**连接管理**

- `InitMQTTConnection(Host, Port=1883, ClientId="", KeepAlive=60, Username="", Password="")` — 建立"默认连接"
- `ConnectToMQTT(...)` — 建立独立连接，返回 **连接 ID**（失败返回 -1）
- `InitMQTTConnectionWithOptions(Options)` / `ConnectToMQTTWithOptions(Options)` — 完整选项版（`FMQTTConnectOptions` 结构体）：额外支持 **持久会话**（`bCleanSession=false`，须固定 ClientId，离线期间的 QoS1/2 消息由 broker 保留、重连后补投）与 **LWT 遗嘱**（`bUseLWT` + `LWTTopic/LWTPayload/LWTQoS/bLWTRetain`，连接异常中断时由 broker 代为发布；主动 Disconnect 不触发）
- `DisconnectMQTT(ConnectionId)` / `CloseMQTTConnection` / `IsConnected` / `IsMQTTConnected(ConnectionId)`
- `GetLastErrorMessage(ConnectionId=0)` — 最近一次失败的错误文本（掉线时也记录断开原因）

**TLS（8883）**

`FMQTTConnectOptions`（即 `InitMQTTConnectionWithOptions` / `ConnectToMQTTWithOptions`）的 TLS 字段：

- `bUseTLS` — 启用 TLS 加密（端口一般为 8883）
- `CACertPath` — CA 证书（PEM 文件路径），用于校验 broker 证书。**留空时自动使用插件内置的 Mozilla 公共 CA bundle**（`Resources/Certificates/cacert.pem`，经 Build.cs `RuntimeDependencies` 部署到 `Binaries/Win64/Certificates/`，**打包产物自动携带**，公网 broker 开箱即用）；自签名 broker 必须显式提供其 CA 文件
- `CACertPem` — CA 证书的 **PEM 文本内容**（优先级高于 `CACertPath`）。运行时写入 `Saved/LinkPLCByMQTT/Certificates/` 缓存文件后使用——证书可以放在 ini / DataTable / 字符串常量里随包分发、运行时热替换，**完全不依赖文件部署**
- 证书工具：`GetBundledCACertPath()` 返回内置 bundle 路径；`EnumerateCACertificates()` 枚举内置 bundle + 项目根 `Certificates/` 约定目录下的所有证书（`.pem/.crt/.cer`），供"逐个证书试连"类测试工具使用
- `bAllowInsecure` — 跳过**全部**证书校验（证书链 + 主机名），仅限调试，公网慎用
- `ClientCertPath` / `ClientKeyPath` — 双向认证（mTLS）用的客户端证书/私钥（PEM），不需要则留空

注意：TLS 握手在首次 `PumpMQTT` 中完成，因此 TLS 连接建立后要先 Pump 再判断 `IsMQTTConnected`；连接 IP 字面量（如 `127.0.0.1`）时 broker 证书的 SAN 必须包含 `IP:xxx` 条目（仅 `DNS:localhost` 不够）。

**发布/订阅**（所有节点都带 `ConnectionId`，**0 = 默认连接**）

- `PublishMQTTMessage(Topic, Payload, QoS, Retain)` / `PublishMQTTString(...)` — 发布（字节 / UTF-8 字符串）
- `SubscribeMQTT(Topic, QoS)` / `UnsubscribeMQTT(Topic)` — 订阅 / 取消订阅（支持 `+`、`#` 通配符）

**入站消息（订阅后必做）**

- `PumpMQTT(TimeoutMs=0)` — 驱动网络循环：收发报文、派发入站消息、维持心跳。**订阅后必须定期调用**（例如 Tick 或定时器）
- `TryReadMQTTMessage(...)` / `TryReadMQTTString(...)` — 从收件队列取一条消息；返回 false 表示队列空（不是错误）。循环调用直到 false 可排空

典型蓝图流程：`InitMQTTConnection` → `SubscribeMQTT("sensors/#")` → 定时器每 100ms 调 `PumpMQTT` → 循环 `TryReadMQTTString` 处理消息。

### C++ 接口

```cpp
#include "LinkPLCByMQTTManager.h"

FString Error;
int32 ConnId = FLinkPLCByMQTTManager::CreateMQTTConnection(TEXT("192.168.0.20"), 1883, TEXT("ue-client"), 60, TEXT(""), TEXT(""), Error);
if (ConnId > 0)
{
    FLinkPLCByMQTTManager::Subscribe(ConnId, TEXT("plc/#"), 0, Error);
    // 在工作线程/定时器里：
    FLinkPLCByMQTTManager::Pump(ConnId, /*TimeoutMs=*/10, Error);
    FString Topic; TArray<uint8> Payload; int32 QoS; bool bRetain;
    while (FLinkPLCByMQTTManager::TryReadMessage(ConnId, Topic, Payload, QoS, bRetain)) { /* ... */ }
    FLinkPLCByMQTTManager::CloseMQTTConnection(ConnId);  // 或 CloseAllConnections()
}
```

TLS / LWT / 持久会话走完整设置版 `FMQTTConnectSettings`（蓝图对应 `FMQTTConnectOptions`）：

```cpp
FMQTTConnectSettings Settings;
Settings.Host = TEXT("broker.emqx.io");
Settings.Port = 8883;
Settings.ClientId = TEXT("ue-client-01");   // 持久会话时必须固定
Settings.KeepAliveSeconds = 60;
Settings.bUseTLS = true;
Settings.CACertPath = TEXT("C:/certs/ca-bundle.crt");  // 自签 broker 给它的 CA；公网 broker 可留空用插件内置 bundle
Settings.bCleanSession = true;              // false = 持久会话
// Settings.bUseLWT = true; Settings.LWTTopic = ...; Settings.LWTPayload = ...;

FString Error;
int32 ConnId = FLinkPLCByMQTTManager::CreateMQTTConnectionWithSettings(Settings, Error);
// TLS 握手在 Pump 中完成：connect 返回后先 Pump 若干轮再判断 IsConnected
```

线程模型：连接表由全局锁保护（仅覆盖查找/增删），每条连接的调用由各自的互斥锁串行化——不同连接可真正并行。libmosquitto 回调（connect/disconnect/message）全部在 `Pump`/连接调用持有的锁内同线程触发，入站消息存入每条连接的收件队列。

> 需要自动轮询、点表管理、断线重连等高级功能时，请以本插件为原语层在上层（如 SCADA 插件）自行实现，本插件刻意保持精简。

## 许可

- 本插件代码：MIT License
- libmosquitto：EPL-2.0 **或** EDL-1.0 双授权（本插件选择 EDL-1.0/BSD-3-Clause），授权文本见 `Source/ThirdParty/LinkPLCByMQTTLibrary/` 下的 `LICENSE.txt`、`edl-v10`、`epl-v20`；裁剪了未启用的特性文件（WS/SRV/threading），另有一处 Windows 修复补丁：`tls_mosq.c` 中 `InetPton` → `InetPtonA`（UE 构建全局定义 UNICODE，宏会解析成宽字符版导致 IP 字面量 SAN 校验失败）
- OpenSSL 3.5.x（FireDaemon 预编译）：Apache-2.0，授权文本见 `Source/ThirdParty/OpenSSL/LICENSE.txt`；`libssl-3-x64.dll` / `libcrypto-3-x64.dll` 随插件部署到 `Binaries/Win64` 并在模块启动时按全路径预载（避免被 PATH 中同名老 DLL 劫持）

## 平台说明

当前仅支持 **Win64**（libmosquitto TCP 走 Winsock2，编译配置见 `LinkPLCByMQTTLibrary.Build.cs`）。
