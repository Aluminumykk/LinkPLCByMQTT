// Copyright Epic Games, Inc. All Rights Reserved.

#include "LinkPLCByMQTT.h"
#include "LinkPLCByMQTTManager.h"

THIRD_PARTY_INCLUDES_START
#include "mosquitto.h"
THIRD_PARTY_INCLUDES_END

#define LOCTEXT_NAMESPACE "FLinkPLCByMQTTModule"

DEFINE_LOG_CATEGORY(LogLinkPLCByMQTT);

void FLinkPLCByMQTTModule::StartupModule()
{
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
