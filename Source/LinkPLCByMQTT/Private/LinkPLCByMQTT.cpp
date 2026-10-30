// Copyright Epic Games, Inc. All Rights Reserved.

#include "LinkPLCByMQTT.h"
#include "LinkPLCByMQTTManager.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/Paths.h"

THIRD_PARTY_INCLUDES_START
#include "mosquitto.h"
THIRD_PARTY_INCLUDES_END

#define LOCTEXT_NAMESPACE "FLinkPLCByMQTTModule"

DEFINE_LOG_CATEGORY(LogLinkPLCByMQTT);

void FLinkPLCByMQTTModule::StartupModule()
{
#if PLATFORM_WINDOWS
	// 显式按全路径预载 OpenSSL DLL：libssl/libcrypto 是延迟加载的，
	// 若按裸名交给系统搜索，可能被 PATH 里其它软件（如 Git\mingw64\bin）的
	// 同名老版本 DLL 截胡，导致 TLS 校验行为异常。先 crypto 后 ssl。
	const FString BinDir = FPaths::Combine(
		IPluginManager::Get().FindPlugin(TEXT("LinkPLCByMQTT"))->GetBaseDir(),
		TEXT("Binaries"), TEXT("Win64"));
	for (const TCHAR* DllName : { TEXT("libcrypto-3-x64.dll"), TEXT("libssl-3-x64.dll") })
	{
		const FString FullPath = FPaths::Combine(BinDir, DllName);
		void* Handle = FPlatformProcess::GetDllHandle(*FullPath);
		UE_LOG(LogLinkPLCByMQTT, Log, TEXT("Preload %s: %s"), DllName,
			Handle ? TEXT("ok") : *FString::Printf(TEXT("FAILED (%s)"), *FullPath));
	}
#endif

	// libmosquitto 直接编译进 LinkPLCByMQTTLibrary 模块（静态链接，无 DLL 预加载）。
	// 库级初始化只做一次：初始化 Winsock 等全局状态。
	if (mosquitto_lib_init() != MOSQ_ERR_SUCCESS)
	{
		UE_LOG(LogLinkPLCByMQTT, Error, TEXT("mosquitto_lib_init failed"));
		return;
	}

	UE_LOG(LogLinkPLCByMQTT, Log, TEXT("LinkPLCByMQTT module started"));
}

void FLinkPLCByMQTTModule::ShutdownModule()
{
	// 先关掉所有连接，再清理库全局状态，保证没有 mosquitto 客户端比模块活得久。
	FLinkPLCByMQTTManager::CloseAllConnections();
	mosquitto_lib_cleanup();

	UE_LOG(LogLinkPLCByMQTT, Log, TEXT("LinkPLCByMQTT module shut down"));
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FLinkPLCByMQTTModule, LinkPLCByMQTT)
