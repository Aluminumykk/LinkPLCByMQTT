#include "LinkPLCByMQTTManager.h"
#include "LinkPLCByMQTT.h"
#include "Containers/StringConv.h"

THIRD_PARTY_INCLUDES_START
#include "mosquitto.h"
THIRD_PARTY_INCLUDES_END

namespace
{
    /** 一条入站消息（由 on_message 回调在 Pump 内写入收件队列）。 */
    struct FMQTTQueuedMessage
    {
        FString Topic;
        TArray<uint8> Payload;
        int32 QoS = 0;
        bool bRetain = false;
    };

    /** 一条 MQTT 连接：libmosquitto 客户端 + 串行化该连接全部调用的互斥锁。 */
    struct FMQTTConnectionEntry
    {
        mosquitto* Mosq = nullptr;

        /** 序列化该连接上的所有 mosquitto_* 调用（mosquitto 客户端非线程安全）。 */
        FCriticalSection Mutex;

        /** libmosquitto 没有 IsConnected 查询；由 connect/disconnect 回调维护。 */
        std::atomic<bool> bConnected{ false };

        /** 最近一次失败操作的错误文本（仅在持有 Mutex 时读写）。 */
        FString LastError;

        /** 入站消息收件队列（仅在持有 Mutex 时读写；on_message 回调在 Pump 内同线程写入）。 */
        TArray<FMQTTQueuedMessage> Inbox;

        ~FMQTTConnectionEntry()
        {
            if (Mosq != nullptr)
            {
                mosquitto_disconnect(Mosq);
                mosquitto_destroy(Mosq);
                Mosq = nullptr;
            }
        }
    };

    /** 全局连接表：ID -> 连接。默认连接固定 ID 0，多连接从 1 递增，ID 不复用。 */
    TMap<int32, TUniquePtr<FMQTTConnectionEntry>> GConnections;
    FCriticalSection GConnectionsMutex;
    int32 GNextConnectionId = 1;

    FString MQTTErrorText(int32 ReturnCode)
    {
        return FString(ANSI_TO_TCHAR(mosquitto_strerror(ReturnCode)));
    }

    /** 取连接（全局锁仅覆盖查找）。失败时写 OutErrorMessage 并返回 nullptr。 */
    FMQTTConnectionEntry* FindConnection(int32 ConnectionId, FString& OutErrorMessage)
    {
        FScopeLock TableLock(&GConnectionsMutex);
        TUniquePtr<FMQTTConnectionEntry>* Found = GConnections.Find(ConnectionId);
        FMQTTConnectionEntry* Entry = Found ? Found->Get() : nullptr;
        if (Entry == nullptr)
        {
            OutErrorMessage = FString::Printf(TEXT("Connection %d not found."), ConnectionId);
        }
        return Entry;
    }

    /** 记录失败：写 LastError 与 OutErrorMessage。 */
    void RecordError(FMQTTConnectionEntry& Entry, int32 ReturnCode, FString& OutErrorMessage)
    {
        OutErrorMessage = MQTTErrorText(ReturnCode);
        Entry.LastError = OutErrorMessage;
    }

    // ---- libmosquitto 回调（全部在 Pump/Connect 持有的连接锁内同线程触发，无需再加锁）----

    void OnConnectCb(mosquitto* Mosq, void* Obj, int ReturnCode)
    {
        FMQTTConnectionEntry* Entry = static_cast<FMQTTConnectionEntry*>(Obj);
        if (Entry == nullptr)
        {
            return;
        }
        if (ReturnCode == 0)
        {
            Entry->bConnected.store(true);
        }
        else
        {
            Entry->bConnected.store(false);
            Entry->LastError = FString(ANSI_TO_TCHAR(mosquitto_connack_string(ReturnCode)));
        }
    }

    void OnDisconnectCb(mosquitto* Mosq, void* Obj, int ReturnCode)
    {
        FMQTTConnectionEntry* Entry = static_cast<FMQTTConnectionEntry*>(Obj);
        if (Entry == nullptr)
        {
            return;
        }
        Entry->bConnected.store(false);
        if (ReturnCode != 0)
        {
            // 非主动断开（掉线）：记录原因，重连策略由调用方决定
            Entry->LastError = FString::Printf(TEXT("Disconnected unexpectedly: %s"), ANSI_TO_TCHAR(mosquitto_strerror(ReturnCode)));
        }
    }

    void OnMessageCb(mosquitto* Mosq, void* Obj, const mosquitto_message* Message)
    {
        FMQTTConnectionEntry* Entry = static_cast<FMQTTConnectionEntry*>(Obj);
        if (Entry == nullptr || Message == nullptr || Message->topic == nullptr)
        {
            return;
        }

        FMQTTQueuedMessage Queued;
        Queued.Topic = FString(ANSI_TO_TCHAR(Message->topic));
        if (Message->payload != nullptr && Message->payloadlen > 0)
        {
            Queued.Payload.SetNumUninitialized(Message->payloadlen);
            FMemory::Memcpy(Queued.Payload.GetData(), Message->payload, Message->payloadlen);
        }
        Queued.QoS = Message->qos;
        Queued.bRetain = Message->retain;
        Entry->Inbox.Add(MoveTemp(Queued));
    }

    /** libmosquitto 日志回调：转发到 UE 日志（TLS 握手等内部错误的唯一可见渠道）。 */
    void OnLogCb(mosquitto* Mosq, void* Obj, int Level, const char* Message)
    {
        const FString Text = Message ? FString(ANSI_TO_TCHAR(Message)) : FString();
        if (Level == MOSQ_LOG_ERR)
        {
            UE_LOG(LogLinkPLCByMQTT, Warning, TEXT("mosquitto: %s"), *Text);
        }
        else
        {
            UE_LOG(LogLinkPLCByMQTT, Verbose, TEXT("mosquitto: %s"), *Text);
        }
    }
}

int32 FLinkPLCByMQTTManager::CreateMQTTConnection(const FString& Host, int32 Port, const FString& ClientId, int32 KeepAliveSeconds, const FString& Username, const FString& Password, FString& OutErrorMessage)
{
    FMQTTConnectSettings Settings;
    Settings.Host = Host;
    Settings.Port = Port;
    Settings.ClientId = ClientId;
    Settings.KeepAliveSeconds = KeepAliveSeconds;
    Settings.Username = Username;
    Settings.Password = Password;
    return CreateMQTTConnectionWithSettings(Settings, OutErrorMessage);
}

int32 FLinkPLCByMQTTManager::CreateMQTTConnectionWithSettings(const FMQTTConnectSettings& Settings, FString& OutErrorMessage)
{
    // MQTT 3.1.1：clean_session=false 时 ClientId 不能为空（broker 会直接拒绝）
    if (!Settings.bCleanSession && Settings.ClientId.IsEmpty())
    {
        OutErrorMessage = TEXT("Persistent session (bCleanSession=false) requires a non-empty ClientId.");
        return -1;
    }
    if (Settings.bUseLWT && Settings.LWTTopic.IsEmpty())
    {
        OutErrorMessage = TEXT("bUseLWT=true requires a non-empty LWTTopic.");
        return -1;
    }
    if (Settings.bUseTLS && (Settings.ClientCertPath.IsEmpty() != Settings.ClientKeyPath.IsEmpty()))
    {
        OutErrorMessage = TEXT("TLS client auth requires both ClientCertPath and ClientKeyPath.");
        return -1;
    }

    auto ClientIdAnsi = StringCast<ANSICHAR>(*Settings.ClientId);

    // ClientId 为空时传 nullptr，由 broker 分配（clean session 前提）
    TUniquePtr<FMQTTConnectionEntry> Entry = MakeUnique<FMQTTConnectionEntry>();
    Entry->Mosq = mosquitto_new(Settings.ClientId.IsEmpty() ? nullptr : ClientIdAnsi.Get(), Settings.bCleanSession, Entry.Get());
    if (Entry->Mosq == nullptr)
    {
        OutErrorMessage = TEXT("Failed to allocate mosquitto client (out of memory).");
        return -1;
    }

    mosquitto_connect_callback_set(Entry->Mosq, &OnConnectCb);
    mosquitto_disconnect_callback_set(Entry->Mosq, &OnDisconnectCb);
    mosquitto_message_callback_set(Entry->Mosq, &OnMessageCb);
    mosquitto_log_callback_set(Entry->Mosq, &OnLogCb);

    if (!Settings.Username.IsEmpty())
    {
        auto UsernameAnsi = StringCast<ANSICHAR>(*Settings.Username);
        auto PasswordAnsi = StringCast<ANSICHAR>(*Settings.Password);
        const int Rc = mosquitto_username_pw_set(Entry->Mosq, UsernameAnsi.Get(), PasswordAnsi.Get());
        if (Rc != MOSQ_ERR_SUCCESS)
        {
            OutErrorMessage = MQTTErrorText(Rc);
            return -1;
        }
    }

    // TLS 必须在 connect 之前设置；CA/客户端证书为 PEM 文件路径
    if (Settings.bUseTLS)
    {
        // 注意：全部证书路径为空时不能调 mosquitto_tls_set（会返回 MOSQ_ERR_INVAL），
        // 但此时必须设置 MOSQ_OPT_TLS_USE_OS_CERTS——libmosquitto 只有在
        // cafile/capath/psk/os_certs 任一存在时才创建 SSL_CTX，否则退化为明文 TCP
        // （TLS 端口上会握手失败 "wrong version number"）。
        const bool bHasCertPaths = !Settings.CACertPath.IsEmpty()
            || !Settings.ClientCertPath.IsEmpty() || !Settings.ClientKeyPath.IsEmpty();
        if (!bHasCertPaths)
        {
            mosquitto_int_option(Entry->Mosq, MOSQ_OPT_TLS_USE_OS_CERTS, 1);
        }
        if (bHasCertPaths)
        {
            auto CACertAnsi = StringCast<ANSICHAR>(*Settings.CACertPath);
            auto ClientCertAnsi = StringCast<ANSICHAR>(*Settings.ClientCertPath);
            auto ClientKeyAnsi = StringCast<ANSICHAR>(*Settings.ClientKeyPath);
            const int Rc = mosquitto_tls_set(Entry->Mosq,
                Settings.CACertPath.IsEmpty() ? nullptr : CACertAnsi.Get(), nullptr,
                Settings.ClientCertPath.IsEmpty() ? nullptr : ClientCertAnsi.Get(),
                Settings.ClientKeyPath.IsEmpty() ? nullptr : ClientKeyAnsi.Get(),
                nullptr);
            if (Rc != MOSQ_ERR_SUCCESS)
            {
                OutErrorMessage = MQTTErrorText(Rc);
                return -1;
            }
        }
        if (Settings.bAllowInsecure)
        {
            // mosquitto_tls_insecure_set 只跳过主机名检查（tls_insecure 标志），
            // 证书链验证模式由 tls_cert_reqs 决定（默认 SSL_VERIFY_PEER）。
            // 真正的"不验证"必须再把 cert_reqs 设为 SSL_VERIFY_NONE（=0，
            // 本文件不引 OpenSSL 头，直接传 0）。
            mosquitto_tls_insecure_set(Entry->Mosq, true);
            mosquitto_tls_opts_set(Entry->Mosq, 0, NULL, NULL);
        }
    }

    // LWT 遗嘱必须在 connect 之前设置；broker 只在该连接异常中断时发布
    if (Settings.bUseLWT)
    {
        auto LWTTopicAnsi = StringCast<ANSICHAR>(*Settings.LWTTopic);
        const int Rc = mosquitto_will_set(Entry->Mosq, LWTTopicAnsi.Get(),
            Settings.LWTPayload.Num(), Settings.LWTPayload.Num() > 0 ? Settings.LWTPayload.GetData() : nullptr,
            FMath::Clamp(Settings.LWTQoS, 0, 2), Settings.bLWTRetain);
        if (Rc != MOSQ_ERR_SUCCESS)
        {
            OutErrorMessage = MQTTErrorText(Rc);
            return -1;
        }
    }

    auto HostAnsi = StringCast<ANSICHAR>(*Settings.Host);
    const int Rc = mosquitto_connect(Entry->Mosq, HostAnsi.Get(), FMath::Clamp(Settings.Port, 1, 65535), FMath::Max(Settings.KeepAliveSeconds, 5));
    if (Rc != MOSQ_ERR_SUCCESS)
    {
        OutErrorMessage = MQTTErrorText(Rc);
        return -1;
    }

    // 注意：不要在连接建立前把 bConnected 置真——TLS 握手是在后续 Pump 驱动的
    // 网络循环里完成的，握手/证书校验失败由 on_disconnect 回调落地。
    // （明文 TCP 下阻塞式 connect 会同步触发 on_connect，由回调置位。）

    FScopeLock TableLock(&GConnectionsMutex);
    const int32 ConnectionId = GNextConnectionId++;
    GConnections.Add(ConnectionId, MoveTemp(Entry));

    UE_LOG(LogLinkPLCByMQTT, Log, TEXT("MQTT connection %d opened: %s:%d (ClientId=%s, CleanSession=%d, LWT=%d, TLS=%d)"),
        ConnectionId, *Settings.Host, Settings.Port,
        Settings.ClientId.IsEmpty() ? TEXT("<auto>") : *Settings.ClientId,
        Settings.bCleanSession ? 1 : 0, Settings.bUseLWT ? 1 : 0, Settings.bUseTLS ? 1 : 0);
    return ConnectionId;
}

bool FLinkPLCByMQTTManager::Publish(int32 ConnectionId, const FString& Topic, const TArray<uint8>& Payload, int32 QoS, bool bRetain, FString& OutErrorMessage)
{
    FMQTTConnectionEntry* Entry = FindConnection(ConnectionId, OutErrorMessage);
    if (Entry == nullptr)
    {
        return false;
    }

    FScopeLock Lock(&Entry->Mutex);
    if (Entry->Mosq == nullptr)
    {
        OutErrorMessage = TEXT("Connection not found or not valid.");
        Entry->LastError = OutErrorMessage;
        return false;
    }

    auto TopicAnsi = StringCast<ANSICHAR>(*Topic);
    const int Rc = mosquitto_publish(Entry->Mosq, nullptr, TopicAnsi.Get(),
        Payload.Num(), Payload.Num() > 0 ? Payload.GetData() : nullptr,
        FMath::Clamp(QoS, 0, 2), bRetain);
    if (Rc != MOSQ_ERR_SUCCESS)
    {
        RecordError(*Entry, Rc, OutErrorMessage);
        return false;
    }

    // 顺带驱动一次零等待网络循环，把报文立即推出去并处理回执
    mosquitto_loop(Entry->Mosq, 0, 1);
    return true;
}

bool FLinkPLCByMQTTManager::Subscribe(int32 ConnectionId, const FString& Topic, int32 QoS, FString& OutErrorMessage)
{
    FMQTTConnectionEntry* Entry = FindConnection(ConnectionId, OutErrorMessage);
    if (Entry == nullptr)
    {
        return false;
    }

    FScopeLock Lock(&Entry->Mutex);
    if (Entry->Mosq == nullptr)
    {
        OutErrorMessage = TEXT("Connection not found or not valid.");
        Entry->LastError = OutErrorMessage;
        return false;
    }

    auto TopicAnsi = StringCast<ANSICHAR>(*Topic);
    const int Rc = mosquitto_subscribe(Entry->Mosq, nullptr, TopicAnsi.Get(), FMath::Clamp(QoS, 0, 2));
    if (Rc != MOSQ_ERR_SUCCESS)
    {
        RecordError(*Entry, Rc, OutErrorMessage);
        return false;
    }

    mosquitto_loop(Entry->Mosq, 0, 1);
    return true;
}

bool FLinkPLCByMQTTManager::Unsubscribe(int32 ConnectionId, const FString& Topic, FString& OutErrorMessage)
{
    FMQTTConnectionEntry* Entry = FindConnection(ConnectionId, OutErrorMessage);
    if (Entry == nullptr)
    {
        return false;
    }

    FScopeLock Lock(&Entry->Mutex);
    if (Entry->Mosq == nullptr)
    {
        OutErrorMessage = TEXT("Connection not found or not valid.");
        Entry->LastError = OutErrorMessage;
        return false;
    }

    auto TopicAnsi = StringCast<ANSICHAR>(*Topic);
    const int Rc = mosquitto_unsubscribe(Entry->Mosq, nullptr, TopicAnsi.Get());
    if (Rc != MOSQ_ERR_SUCCESS)
    {
        RecordError(*Entry, Rc, OutErrorMessage);
        return false;
    }

    mosquitto_loop(Entry->Mosq, 0, 1);
    return true;
}

bool FLinkPLCByMQTTManager::Pump(int32 ConnectionId, int32 TimeoutMs, FString& OutErrorMessage)
{
    FMQTTConnectionEntry* Entry = FindConnection(ConnectionId, OutErrorMessage);
    if (Entry == nullptr)
    {
        return false;
    }

    FScopeLock Lock(&Entry->Mutex);
    if (Entry->Mosq == nullptr)
    {
        OutErrorMessage = TEXT("Connection not found or not valid.");
        Entry->LastError = OutErrorMessage;
        return false;
    }

    const int Rc = mosquitto_loop(Entry->Mosq, FMath::Max(TimeoutMs, 0), 1);
    if (Rc != MOSQ_ERR_SUCCESS)
    {
        RecordError(*Entry, Rc, OutErrorMessage);
        // 掉线时同步连接标志（on_disconnect 回调也会维护，这里兜底）
        if (Rc == MOSQ_ERR_CONN_LOST || Rc == MOSQ_ERR_NO_CONN)
        {
            Entry->bConnected.store(false);
        }
        return false;
    }
    return true;
}

bool FLinkPLCByMQTTManager::TryReadMessage(int32 ConnectionId, FString& OutTopic, TArray<uint8>& OutPayload, int32& OutQoS, bool& bOutRetain)
{
    FString IgnoredError;
    FMQTTConnectionEntry* Entry = FindConnection(ConnectionId, IgnoredError);
    if (Entry == nullptr)
    {
        return false;
    }

    FScopeLock Lock(&Entry->Mutex);
    if (Entry->Inbox.Num() == 0)
    {
        return false;
    }

    FMQTTQueuedMessage Message = MoveTemp(Entry->Inbox[0]);
    Entry->Inbox.RemoveAt(0, 1, EAllowShrinking::No);

    OutTopic = MoveTemp(Message.Topic);
    OutPayload = MoveTemp(Message.Payload);
    OutQoS = Message.QoS;
    bOutRetain = Message.bRetain;
    return true;
}

void FLinkPLCByMQTTManager::CloseMQTTConnection(int32 ConnectionId)
{
    FScopeLock TableLock(&GConnectionsMutex);
    GConnections.Remove(ConnectionId);
}

bool FLinkPLCByMQTTManager::IsConnected(int32 ConnectionId)
{
    FScopeLock TableLock(&GConnectionsMutex);
    const TUniquePtr<FMQTTConnectionEntry>* Found = GConnections.Find(ConnectionId);
    return Found != nullptr && (*Found)->bConnected.load();
}

void FLinkPLCByMQTTManager::CloseAllConnections()
{
    FScopeLock TableLock(&GConnectionsMutex);
    GConnections.Empty();
}

FString FLinkPLCByMQTTManager::GetLastErrorMessage(int32 ConnectionId)
{
    FScopeLock TableLock(&GConnectionsMutex);
    TUniquePtr<FMQTTConnectionEntry>* Found = GConnections.Find(ConnectionId);
    if (Found == nullptr)
    {
        return FString();
    }
    FMQTTConnectionEntry* Entry = Found->Get();
    FScopeLock Lock(&Entry->Mutex);
    return Entry->LastError;
}

// ---- 默认连接（ID 固定为 0）----

bool FLinkPLCByMQTTManager::ReconnectDefaultConnection(const FString& Host, int32 Port, const FString& ClientId, int32 KeepAliveSeconds, const FString& Username, const FString& Password)
{
    FMQTTConnectSettings Settings;
    Settings.Host = Host;
    Settings.Port = Port;
    Settings.ClientId = ClientId;
    Settings.KeepAliveSeconds = KeepAliveSeconds;
    Settings.Username = Username;
    Settings.Password = Password;
    return ReconnectDefaultConnectionWithSettings(Settings);
}

bool FLinkPLCByMQTTManager::ReconnectDefaultConnectionWithSettings(const FMQTTConnectSettings& Settings)
{
    CloseDefaultConnection();

    FString ErrorMessage;
    const int32 NewId = CreateMQTTConnectionWithSettings(Settings, ErrorMessage);
    if (NewId < 0)
    {
        UE_LOG(LogLinkPLCByMQTT, Warning, TEXT("Default MQTT connection to %s:%d failed: %s"),
            *Settings.Host, Settings.Port, *ErrorMessage);
        return false;
    }

    // 把刚建好的连接改挂到默认 ID 0（CreateMQTTConnection 总是发递增 ID）
    FScopeLock TableLock(&GConnectionsMutex);
    TUniquePtr<FMQTTConnectionEntry>* Found = GConnections.Find(NewId);
    check(Found != nullptr);
    TUniquePtr<FMQTTConnectionEntry> Entry = MoveTemp(*Found);
    GConnections.Remove(NewId);
    GConnections.Add(0, MoveTemp(Entry));
    return true;
}

int32 FLinkPLCByMQTTManager::GetDefaultConnectionId()
{
    FScopeLock TableLock(&GConnectionsMutex);
    return GConnections.Contains(0) ? 0 : 0;
}

bool FLinkPLCByMQTTManager::IsDefaultConnectionActive()
{
    return IsConnected(0);
}

void FLinkPLCByMQTTManager::CloseDefaultConnection()
{
    CloseMQTTConnection(0);
}
