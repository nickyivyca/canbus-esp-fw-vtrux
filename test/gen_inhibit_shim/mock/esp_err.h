/* Mock esp_err.h for the E1 host build. Values match ESP-IDF's. */
#ifndef MOCK_ESP_ERR_H
#define MOCK_ESP_ERR_H
typedef int esp_err_t;
#define ESP_OK                  0
#define ESP_FAIL               -1
#define ESP_ERR_NO_MEM          0x101
#define ESP_ERR_INVALID_ARG     0x102
#define ESP_ERR_INVALID_STATE   0x103
#define ESP_ERR_TIMEOUT         0x107
#define ESP_ERR_NOT_FOUND       0x105
/*
 * What the real twai_transmit() returns in listen-only: ESP-IDF documents
 * "ESP_ERR_NOT_SUPPORTED: Listen Only Mode does not support transmissions".
 * The value is ESP-IDF's own, not invented here -- a mock that renamed or
 * renumbered anything would be testing a different program.
 */
#define ESP_ERR_NOT_SUPPORTED   0x106
const char *esp_err_to_name(esp_err_t e);
#endif
