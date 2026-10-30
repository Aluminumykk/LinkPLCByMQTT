#pragma once

#include "CoreMinimal.h"
#include "UObject/ObjectMacros.h"

/**
 * MQTT 多连接管理器（静态阻塞 API，最小原语）。
 *
 * 只做同步连接/发布/订阅原语，不做异步、轮询、点表、UI、断线重连
 * ——异步策略由调用方决定。
 *
 * 与 S7/Modbus 插件的关键差异：MQTT 的入站消息不是"读"出来的，
 * 而是网络循环驱动回调派发的。本管理器把回调收到的消息存入每条连接的
 * 收件队列，调用方必须定期调用 Pump()（例如在 USCADA 的每设备工作线程里）
 * 驱动网络循环，再用 TryReadMessage() 逐条取出。
 *
 * 线程安全：连接表由全局锁保护（仅覆盖查找/增删），每条连接的阻塞 I/O
 * 由各自的互斥锁保护——不同连接上的操作可真正并行；同一连接上的操作
 * 串行执行。Pump 最长阻塞其 TimeoutMs；Connect 最长阻塞一个 TCP 连接超时。
 *
 * 错误反馈：所有操作返回 bool；失败原因写入该连接的 LastError，
 * 用 GetLastErrorMessage 查询。
 */
/** MQTT 连接参数（含 LWT 遗嘱与会话模式），CreateMQTTConnectionWithSettings 的输入。 */
struct FMQTTConnectSettings
{
    FString Host;
    int32 Port = 1883;
    FString ClientId;
    int32 KeepAliveSeconds = 60;
    FString Username;
    FString Password;

    /** true = 清理会话（默认）；false = 持久会话（必须指定固定 ClientId）。 */
    bool bCleanSession = true;

    /** LWT 遗嘱：连接异常中断（非主动断开）时由 broker 代为发布。 */
    bool bUseLWT = false;
    FString LWTTopic;
    TArray<uint8> LWTPayload;
    int32 LWTQoS = 0;
    bool bLWTRetain = false;

    /** TLS（端口通常 8883）：CA 证书/客户端证书均为 PEM 文件路径。 */
    bool bUseTLS = false;
    /** CA 证书（PEM，用于校验 broker；留空走 OpenSSL 默认信任路径，一般为空即校验失败） */
    FString CACertPath;
    /** 客户端证书/私钥（PEM，broker 要求双向认证时使用，如 test.mosquitto.org:8884） */
    FString ClientCertPath;
    FString ClientKeyPath;
    /** 跳过 broker 证书校验（仅限测试环境；与 CACertPath 二选一） */
    bool bAllowInsecure = false;
};

class LINKPLCBYMQTT_API FLinkPLCByMQTTManager
{
public:
    // 创建 MQTT 连接（clean session；ClientId 为空时由 broker 分配），返回连接 ID（大于 0），失败返回 -1
    static int32 CreateMQTTConnection(const FString& Host, int32 Port, const FString& ClientId, int32 KeepAliveSeconds, const FString& Username, const FString& Password, FString& OutErrorMessage);

    // 按完整设置创建 MQTT 连接（支持持久会话与 LWT 遗嘱），返回连接 ID（大于 0），失败返回 -1
    static int32 CreateMQTTConnectionWithSettings(const FMQTTConnectSettings& Settings, FString& OutErrorMessage);

    // 发布消息（QoS 0-2；发送后顺带驱动一次零等待网络循环把报文推出去）
    static bool Publish(int32 ConnectionId, const FString& Topic, const TArray<uint8>& Payload, int32 QoS, bool bRetain, FString& OutErrorMessage);

    // 订阅主题（支持 MQTT 通配符 +/#）
    static bool Subscribe(int32 ConnectionId, const FString& Topic, int32 QoS, FString& OutErrorMessage);

    // 取消订阅
    static bool Unsubscribe(int32 ConnectionId, const FString& Topic, FString& OutErrorMessage);

    // 驱动网络循环：收发待办报文、派发入站消息到收件队列、维持心跳。
    // TimeoutMs 为最长阻塞毫秒数（0 = 立即返回）。调用方必须定期调用。
    static bool Pump(int32 ConnectionId, int32 TimeoutMs, FString& OutErrorMessage);

    // 从收件队列取一条入站消息；无消息返回 false（不是错误）
    static bool TryReadMessage(int32 ConnectionId, FString& OutTopic, TArray<uint8>& OutPayload, int32& OutQoS, bool& bOutRetain);

    // 关闭 MQTT 连接
    static void CloseMQTTConnection(int32 ConnectionId);

    // 检查连接状态
    static bool IsConnected(int32 ConnectionId);

    // 关闭所有连接
    static void CloseAllConnections();

    // 查询指定连接最近一次失败操作的错误文本（无错误或无此连接时返回空串）
    static FString GetLastErrorMessage(int32 ConnectionId);

    // ---- 默认连接（单连接场景，ID 固定为 0，供无 ConnectionId 的接口使用）----

    // 重建默认连接：先断开旧默认连接，再建立新连接
    static bool ReconnectDefaultConnection(const FString& Host, int32 Port, const FString& ClientId, int32 KeepAliveSeconds, const FString& Username, const FString& Password);

    // 按完整设置重建默认连接（支持持久会话与 LWT 遗嘱）
    static bool ReconnectDefaultConnectionWithSettings(const FMQTTConnectSettings& Settings);

    // 获取默认连接 ID（不存在时为 0）
    static int32 GetDefaultConnectionId();

    // 默认连接是否已建立且处于连接状态
    static bool IsDefaultConnectionActive();

    // 关闭默认连接
    static void CloseDefaultConnection();
};
