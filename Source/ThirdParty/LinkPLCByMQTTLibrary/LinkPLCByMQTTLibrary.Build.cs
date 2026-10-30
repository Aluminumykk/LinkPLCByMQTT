// Copyright Epic Games, Inc. All Rights Reserved.

using System.IO;
using UnrealBuildTool;

// libmosquitto 2.1.2 (EPL-2.0 OR EDL-1.0，本插件按 EDL-1.0/BSD-3-Clause 使用) 客户端库源码直接编入本模块。
// 保留 TCP + TLS 客户端能力：WITH_SRV / WITH_WEBSOCKETS / WITH_THREADING 关闭
// (对应源码文件已移除或经 #ifdef 编译为空)，线程模型由 UE 侧管理器决定。
// TLS 依赖 OpenSSL 3.5.9 LTS 预编译包（FireDaemon 发行，Apache-2.0），
// 头文件/导入库/运行时 DLL 随插件置于 Source/ThirdParty/OpenSSL/。
// config.h 由仓库根目录的 Makefile 版改写，授权文本见本目录 LICENSE.txt / edl-v10 / epl-v20。
public class LinkPLCByMQTTLibrary : ModuleRules
{
	public LinkPLCByMQTTLibrary(ReadOnlyTargetRules Target) : base(Target)
	{
		// 普通模块:libmosquitto 的 .c 源文件由 UBT 直接编译
		PCHUsage = ModuleRules.PCHUsageMode.NoPCHs;

		// 多个 .c 文件里有同名 static 内部函数,禁用 unity 避免合并冲突
		bUseUnity = false;

		// 第三方 C 源码,不做 UE 警告等级/告警即错误要求
		bWarningsAsErrors = false;
		CppCompileWarningSettings.UndefinedIdentifierWarningLevel = WarningLevel.Off;

		// 头文件布局:
		//   include/            -> <mosquitto.h> <mosquitto/...>
		//   lib/, libcommon/    -> 内部头(mosquitto_internal.h, property_common.h 等)
		//   deps/               -> uthash.h / utlist.h
		//   模块根              -> "config.h"
		PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "mosquitto", "include"));
		PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "mosquitto", "lib"));
		PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "mosquitto", "libcommon"));
		PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "mosquitto", "deps"));
		PublicIncludePaths.Add(ModuleDirectory);

		// 编本模块时导出 libmosquitto / libmosquitto_common 符号;
		// 上层模块经 mosquitto.h / libcommon.h 的条件分支得到 dllimport
		PrivateDefinitions.Add("libmosquitto_EXPORTS");
		PrivateDefinitions.Add("libmosquitto_common_EXPORTS");

		// CMake 构建时由工程注入的版本号,这里保持与源码版本一致
		PrivateDefinitions.Add("VERSION=\"2.1.2\"");

		// 启用 TLS（OpenSSL）；TLS-PSK 不启用
		PrivateDefinitions.Add("WITH_TLS");

		PublicDefinitions.Add("_CRT_SECURE_NO_WARNINGS");

		// UBT 生成的 Definitions.c.h 会把 LINKPLCBYMQTTLIBRARY_API 定义为 DLLEXPORT,
		// 但纯 C 文件不包含 UE 平台头,DLLEXPORT 本身没有定义,导致导出符号丢失。
		// 给本模块的 .c 编译补上 DLLEXPORT 的定义(消费模块的 .cpp 经 UE 头已有此宏,不受影响)。
		PrivateDefinitions.Add("DLLEXPORT=__declspec(dllexport)");

		if (Target.Platform == UnrealTargetPlatform.Win64)
		{
			// libmosquitto TCP 走 Winsock2
			PublicSystemLibraries.Add("ws2_32.lib");

			// ---- TLS: OpenSSL 3.5.9 LTS（FireDaemon 预编译，/MT 静态 CRT）----
			string OpenSSLDir = Path.Combine(ModuleDirectory, "..", "OpenSSL");
			PrivateIncludePaths.Add(Path.Combine(OpenSSLDir, "include"));
			PublicAdditionalLibraries.Add(Path.Combine(OpenSSLDir, "lib", "Win64", "libssl.lib"));
			PublicAdditionalLibraries.Add(Path.Combine(OpenSSLDir, "lib", "Win64", "libcrypto.lib"));

			// 运行时 DLL 随插件模块目录（Binaries/Win64）部署；延迟加载，仅在用到 TLS 时才载入
			PublicDelayLoadDLLs.Add("libssl-3-x64.dll");
			PublicDelayLoadDLLs.Add("libcrypto-3-x64.dll");
			string BinDir = Path.Combine(PluginDirectory, "Binaries", "Win64");
			RuntimeDependencies.Add(Path.Combine(BinDir, "libssl-3-x64.dll"),
				Path.Combine(OpenSSLDir, "bin", "Win64", "libssl-3-x64.dll"));
			RuntimeDependencies.Add(Path.Combine(BinDir, "libcrypto-3-x64.dll"),
				Path.Combine(OpenSSLDir, "bin", "Win64", "libcrypto-3-x64.dll"));
		}
	}
}
