#include "LinkPLCByMQTTBPLibrary.h"
#include "LinkPLCByMQTTManager.h"

namespace
{
    /** 蓝图选项 -> 原生连接设置（LWT 字符串按 UTF-8 编码为字节）。 */
    FMQTTConnectSettings ToNativeSettings(const FMQTTConnectOptions& Options)
    {
        FMQTTConnectSettings Settings;
        Settings.Host = Options.Host;
        Settings.Port = Options.Port;
        Settings.ClientId = Options.ClientId;
        Settings.KeepAliveSeconds = Options.KeepAliveSeconds;
        Settings.Username = Options.Username;
        Settings.Password = Options.Password;
        Settings.bCleanSession = Options.bCleanSession;
        Settings.bUseLWT = Options.bUseLWT;
        Settings.LWTTopic = Options.LWTTopic;
        Settings.LWTQoS = static_cast<int32>(Options.LWTQoS);
        Settings.bLWTRetain = Options.bLWTRetain;
        Settings.bUseTLS = Options.bUseTLS;
        Settings.CACertPath = Options.CACertPath;
        Settings.ClientCertPath = Options.ClientCertPath;
        Settings.ClientKeyPath = Options.ClientKeyPath;
        Settings.bAllowInsecure = Options.bAllowInsecure;
        if (Options.bUseLWT)
        {
            FTCHARToUTF8 Utf8(*Options.LWTPayload);
            Settings.LWTPayload.SetNumUninitialized(Utf8.Length());
            if (Utf8.Length() > 0)
            {
                FMemory::Memcpy(Settings.LWTPayload.GetData(), Utf8.Get(), Utf8.Length());
            }
        }
        return Settings;
    }
}

bool ULinkPLCByMQTTBPLibrary::InitMQTTConnection(const FString& Host, int32 Port, const FString& ClientId, int32 KeepAliveSeconds, const FString& Username, const FString& Password)
{
    return FLinkPLCByMQTTManager::ReconnectDefaultConnection(Host, Port, ClientId, KeepAliveSeconds, Username, Password);
}

int32 ULinkPLCByMQTTBPLibrary::ConnectToMQTT(const FString& Host, int32 Port, const FString& ClientId, int32 KeepAliveSeconds, const FString& Username, const FString& Password)
{
    FString ErrorMessage;
    return FLinkPLCByMQTTManager::CreateMQTTConnection(Host, Port, ClientId, KeepAliveSeconds, Username, Password, ErrorMessage);
}

bool ULinkPLCByMQTTBPLibrary::InitMQTTConnectionWithOptions(const FMQTTConnectOptions& Options)
{
    return FLinkPLCByMQTTManager::ReconnectDefaultConnectionWithSettings(ToNativeSettings(Options));
}

int32 ULinkPLCByMQTTBPLibrary::ConnectToMQTTWithOptions(const FMQTTConnectOptions& Options)
{
    FString ErrorMessage;
    return FLinkPLCByMQTTManager::CreateMQTTConnectionWithSettings(ToNativeSettings(Options), ErrorMessage);
}

void ULinkPLCByMQTTBPLibrary::DisconnectMQTT(int32 ConnectionId)
{
    FLinkPLCByMQTTManager::CloseMQTTConnection(ConnectionId);
}

bool ULinkPLCByMQTTBPLibrary::IsMQTTConnected(int32 ConnectionId)
{
    return FLinkPLCByMQTTManager::IsConnected(ConnectionId);
}

void ULinkPLCByMQTTBPLibrary::CloseMQTTConnection()
{
    FLinkPLCByMQTTManager::CloseDefaultConnection();
}

bool ULinkPLCByMQTTBPLibrary::IsConnected()
{
    return FLinkPLCByMQTTManager::IsDefaultConnectionActive();
}

FString ULinkPLCByMQTTBPLibrary::GetLastErrorMessage(int32 ConnectionId)
{
    return FLinkPLCByMQTTManager::GetLastErrorMessage(ConnectionId);
}

bool ULinkPLCByMQTTBPLibrary::PublishMQTTMessage(const FString& Topic, const TArray<uint8>& Payload, EMQTTQoS QoS, bool bRetain, int32 ConnectionId)
{
    FString ErrorMessage;
    return FLinkPLCByMQTTManager::Publish(ConnectionId, Topic, Payload, static_cast<int32>(QoS), bRetain, ErrorMessage);
}

bool ULinkPLCByMQTTBPLibrary::PublishMQTTString(const FString& Topic, const FString& Message, EMQTTQoS QoS, bool bRetain, int32 ConnectionId)
{
    FTCHARToUTF8 Utf8(*Message);
    TArray<uint8> Payload;
    Payload.SetNumUninitialized(Utf8.Length());
    if (Utf8.Length() > 0)
    {
        FMemory::Memcpy(Payload.GetData(), Utf8.Get(), Utf8.Length());
    }

    FString ErrorMessage;
    return FLinkPLCByMQTTManager::Publish(ConnectionId, Topic, Payload, static_cast<int32>(QoS), bRetain, ErrorMessage);
}

bool ULinkPLCByMQTTBPLibrary::SubscribeMQTT(const FString& Topic, EMQTTQoS QoS, int32 ConnectionId)
{
    FString ErrorMessage;
    return FLinkPLCByMQTTManager::Subscribe(ConnectionId, Topic, static_cast<int32>(QoS), ErrorMessage);
}

bool ULinkPLCByMQTTBPLibrary::UnsubscribeMQTT(const FString& Topic, int32 ConnectionId)
{
    FString ErrorMessage;
    return FLinkPLCByMQTTManager::Unsubscribe(ConnectionId, Topic, ErrorMessage);
}

bool ULinkPLCByMQTTBPLibrary::PumpMQTT(int32 TimeoutMs, int32 ConnectionId)
{
    FString ErrorMessage;
    return FLinkPLCByMQTTManager::Pump(ConnectionId, TimeoutMs, ErrorMessage);
}

bool ULinkPLCByMQTTBPLibrary::TryReadMQTTMessage(FString& OutTopic, TArray<uint8>& OutPayload, int32& OutQoS, bool& bOutRetain, int32 ConnectionId)
{
    return FLinkPLCByMQTTManager::TryReadMessage(ConnectionId, OutTopic, OutPayload, OutQoS, bOutRetain);
}

bool ULinkPLCByMQTTBPLibrary::TryReadMQTTString(FString& OutTopic, FString& OutMessage, int32& OutQoS, bool& bOutRetain, int32 ConnectionId)
{
    TArray<uint8> Payload;
    if (!FLinkPLCByMQTTManager::TryReadMessage(ConnectionId, OutTopic, Payload, OutQoS, bOutRetain))
    {
        return false;
    }

    if (Payload.Num() > 0)
    {
        // 追加 NUL 终止后按 UTF-8 解码
        Payload.Add(0);
        OutMessage = FString(UTF8_TO_TCHAR(reinterpret_cast<const char*>(Payload.GetData())));
    }
    else
    {
        OutMessage.Empty();
    }
    return true;
}
