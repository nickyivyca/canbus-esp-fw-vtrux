/*
 * Mock esp_app_desc.h. gen_inhibit.c hashes the build's name for 0x7F3 from
 * esp_app_get_description()->version (spec 12.3 item 3, "The name enters the
 * build once"). "unknown" is the string the shim hashed before, when the name
 * was a -DGIT_SHA define the shim build never passed. Only the field read is
 * mocked; its size is the real one (ESP-IDF v5.4.1 esp_app_desc.h).
 */
#ifndef MOCK_ESP_APP_DESC_H
#define MOCK_ESP_APP_DESC_H

typedef struct {
    char version[32];
} esp_app_desc_t;

static inline const esp_app_desc_t *esp_app_get_description(void)
{
    static const esp_app_desc_t desc = { .version = "unknown" };
    return &desc;
}

#endif
