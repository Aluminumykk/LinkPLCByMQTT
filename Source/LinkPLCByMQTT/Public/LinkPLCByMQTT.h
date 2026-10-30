// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "Modules/ModuleManager.h"

/** Dedicated log category for the whole LinkPLCByMQTT plugin (never use LogTemp here). */
DECLARE_LOG_CATEGORY_EXTERN(LogLinkPLCByMQTT, Log, All);

class FLinkPLCByMQTTModule : public IModuleInterface
{
public:

	/** IModuleInterface implementation */
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
};
