/*
 * Minimal cJSON forward-declaration stub for the UE LinkPLCByMQTTLibrary build.
 *
 * mosquitto's umbrella header <mosquitto/libcommon.h> unconditionally includes
 * <mosquitto/libcommon_cjson.h>, which includes <cjson/cJSON.h> and declares
 * mosquitto_properties_to_json(). We neither compile cjson_common.c nor call
 * that function, so an opaque type is sufficient to satisfy the declaration.
 * If JSON property export is ever needed, replace this stub with the real
 * cJSON (https://github.com/DaveGamble/cJSON) and add cjson_common.c.
 */
#ifndef CJSON_STUB_H
#define CJSON_STUB_H

typedef struct cJSON
{
	/* opaque: real cJSON layout intentionally not provided */
	char _unused;
} cJSON;

#endif
