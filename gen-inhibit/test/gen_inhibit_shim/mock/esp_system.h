/*
 * Mock esp_system.h.
 *
 * can.c includes it and uses nothing from it. gen_inhibit.c uses one thing:
 * esp_reset_reason(), for spec 11's `reset_reason` page field.
 *
 * The enum is transcribed from the real esp_system.h IN ORDER, because the
 * order IS the wire format here -- the field reports an unrecognised value as
 * its NUMBER, so a mock whose POWERON is 2 where the target's is 1 would make
 * every name-to-number assertion in the shim agree with itself and disagree
 * with the device. Checked against ESP-IDF v5.4.1
 * (components/esp_system/include/esp_system.h:25-40) on 2026-10-09.
 */
#ifndef MOCK_esp_system_H
#define MOCK_esp_system_H

typedef enum {
    ESP_RST_UNKNOWN,
    ESP_RST_POWERON,
    ESP_RST_EXT,
    ESP_RST_SW,
    ESP_RST_PANIC,
    ESP_RST_INT_WDT,
    ESP_RST_TASK_WDT,
    ESP_RST_WDT,
    ESP_RST_DEEPSLEEP,
    ESP_RST_BROWNOUT,
    ESP_RST_SDIO,
    ESP_RST_USB,
    ESP_RST_JTAG,
    ESP_RST_EFUSE,
    ESP_RST_PWR_GLITCH,
    ESP_RST_CPU_LOCKUP,
} esp_reset_reason_t;

/* Implemented by fake_twai.c, beside the virtual clock. */
esp_reset_reason_t esp_reset_reason(void);

#endif
