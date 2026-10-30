#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MQTTDataTypes.h"
#include "LinkPLCByMQTTBPLibrary.generated.h"

/**
 * MQTT 蓝图函数库（FLinkPLCByMQTTManager 的薄封装）。
 *
 * 所有节点都是同步调用：失败原因可用 GetLastErrorMessage 查询。
 * ConnectionId 语义：0 = 默认连接（InitMQTTConnection 建立），
 * 大于 0 = ConnectToMQTT 返回的 ID。
 *
 * 注意：MQTT 的入站消息需要定期调用 PumpMQTT 驱动网络循环后，
 * 才能用 TryReadMQTTMessage 取出（例如放在 Tick 或定时器里轮询）。
 */
UCLASS()
class LINKPLCBYMQTT_API ULinkPLCByMQTTBPLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    /**
     * 初始化与 MQTT broker 的连接（默认连接，单连接场景使用）
     * 多次调用会先断开之前的默认连接再建立新连接
     * @param Host broker 地址（IP 或域名）
     * @param Port 端口（明文默认 1883）
     * @param ClientId 客户端 ID（留空由 broker 分配）
     * @param KeepAliveSeconds 心跳间隔（秒）
     * @param Username 用户名（留空表示不认证）
     * @param Password 密码
     * @return 是否连接成功
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool InitMQTTConnection(const FString& Host, int32 Port = 1883, const FString& ClientId = TEXT(""), int32 KeepAliveSeconds = 60, const FString& Username = TEXT(""), const FString& Password = TEXT(""));

    /**
     * 连接一台 MQTT broker 并返回连接 ID（多连接场景使用）
     * @return 连接 ID（大于 0），失败返回 -1；用此 ID 指定后续操作目标
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static int32 ConnectToMQTT(const FString& Host, int32 Port = 1883, const FString& ClientId = TEXT(""), int32 KeepAliveSeconds = 60, const FString& Username = TEXT(""), const FString& Password = TEXT(""));

    /** 按完整选项建立默认连接（支持持久会话与 LWT 遗嘱） */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool InitMQTTConnectionWithOptions(const FMQTTConnectOptions& Options);

    /**
     * 按完整选项连接一台 MQTT broker 并返回连接 ID（支持持久会话与 LWT 遗嘱）
     * @return 连接 ID（大于 0），失败返回 -1
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static int32 ConnectToMQTTWithOptions(const FMQTTConnectOptions& Options);

    /** 断开指定连接 */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static void DisconnectMQTT(int32 ConnectionId);

    /** 查询指定连接是否处于连接状态 */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool IsMQTTConnected(int32 ConnectionId);

    /** 关闭默认连接（InitMQTTConnection 建立的连接） */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static void CloseMQTTConnection();

    /** 检查默认连接状态 */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool IsConnected();

    /**
     * 查询指定连接最近一次失败操作的错误文本
     * @param ConnectionId 连接 ID，0 = 默认连接
     */
    UFUNCTION(BlueprintPure, Category = "LinkPLCByMQTT")
    static FString GetLastErrorMessage(int32 ConnectionId = 0);

    /**
     * 发布消息（原始字节）
     * @param Topic 主题（不含通配符）
     * @param Payload 消息内容
     * @param QoS 服务质量等级
     * @param bRetain 是否作为保留消息
     * @param ConnectionId 连接 ID，0 = 默认连接
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool PublishMQTTMessage(const FString& Topic, const TArray<uint8>& Payload, EMQTTQoS QoS = EMQTTQoS::AtMostOnce, bool bRetain = false, int32 ConnectionId = 0);

    /** 发布消息（字符串便捷版，按 UTF-8 编码） */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool PublishMQTTString(const FString& Topic, const FString& Message, EMQTTQoS QoS = EMQTTQoS::AtMostOnce, bool bRetain = false, int32 ConnectionId = 0);

    /**
     * 订阅主题（支持通配符 + 和 #）
     * 订阅后必须定期调用 PumpMQTT，入站消息才能到达
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool SubscribeMQTT(const FString& Topic, EMQTTQoS QoS = EMQTTQoS::AtMostOnce, int32 ConnectionId = 0);

    /** 取消订阅 */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool UnsubscribeMQTT(const FString& Topic, int32 ConnectionId = 0);

    /**
     * 驱动网络循环：收发待办报文、接收入站消息、维持心跳。
     * 订阅后必须定期调用（例如 Tick 或定时器）。
     * @param TimeoutMs 最长阻塞毫秒数（0 = 立即返回）
     * @param ConnectionId 连接 ID，0 = 默认连接
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool PumpMQTT(int32 TimeoutMs = 0, int32 ConnectionId = 0);

    /**
     * 取出一条入站消息（原始字节）；无消息返回 false（不是错误）
     * 循环调用直到返回 false 可排空收件队列
     */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool TryReadMQTTMessage(FString& OutTopic, TArray<uint8>& OutPayload, int32& OutQoS, bool& bOutRetain, int32 ConnectionId = 0);

    /** 取出一条入站消息（字符串便捷版，按 UTF-8 解码） */
    UFUNCTION(BlueprintCallable, Category = "LinkPLCByMQTT")
    static bool TryReadMQTTString(FString& OutTopic, FString& OutMessage, int32& OutQoS, bool& bOutRetain, int32 ConnectionId = 0);
};
