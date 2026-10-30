#pragma once

#include "CoreMinimal.h"
#include "MQTTDataTypes.generated.h"

/**
 * MQTT 服务质量等级 (QoS)
 */
UENUM(BlueprintType)
enum class EMQTTQoS : uint8
{
	AtMostOnce UMETA(DisplayName = "QoS 0 - 最多一次 (At Most Once)"),   // 最多一次,可能丢失
	AtLeastOnce UMETA(DisplayName = "QoS 1 - 至少一次 (At Least Once)"), // 至少一次,可能重复
	ExactlyOnce UMETA(DisplayName = "QoS 2 - 恰好一次 (Exactly Once)")   // 恰好一次,开销最大
};

/**
 * MQTT 连接选项（含 LWT 遗嘱与会话模式）。
 *
 * 与 S7/Modbus 的点表式配置不同，MQTT 是 broker + 主题模型：
 * 配置项都在"连接"上（broker 地址、认证、心跳、会话模式、遗嘱），
 * 数据寻址（主题）在发布/订阅时逐次给出。
 */
USTRUCT(BlueprintType)
struct LINKPLCBYMQTT_API FMQTTConnectOptions
{
	GENERATED_BODY()

	/** broker 地址（IP 或域名） */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	FString Host = TEXT("127.0.0.1");

	/** 端口（明文默认 1883） */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	int32 Port = 1883;

	/** 客户端 ID；留空由 broker 分配（仅 bCleanSession=true 时允许留空） */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	FString ClientId;

	/** 心跳间隔（秒） */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	int32 KeepAliveSeconds = 60;

	/** 用户名（留空表示不认证） */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	FString Username;

	/** 密码 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	FString Password;

	/**
	 * true = 清理会话（断线后 broker 丢弃订阅与离线消息，默认）；
	 * false = 持久会话（必须指定固定 ClientId，broker 保留订阅，
	 * 断线期间的 QoS1/2 消息在重连后补投）
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT")
	bool bCleanSession = true;

	/** 是否设置 LWT 遗嘱：连接异常中断（非主动断开）时由 broker 代为发布 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT", meta = (InlineEditConditionToggle))
	bool bUseLWT = false;

	/** 遗嘱主题 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT", meta = (EditCondition = "bUseLWT"))
	FString LWTTopic;

	/** 遗嘱内容（字符串，按 UTF-8 编码） */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT", meta = (EditCondition = "bUseLWT"))
	FString LWTPayload;

	/** 遗嘱 QoS */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT", meta = (EditCondition = "bUseLWT"))
	EMQTTQoS LWTQoS = EMQTTQoS::AtMostOnce;

	/** 遗嘱是否作为保留消息 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MQTT", meta = (EditCondition = "bUseLWT"))
	bool bLWTRetain = false;
};
