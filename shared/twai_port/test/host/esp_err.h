/* Minimal host esp_err.h for builds on the twai_port fake with no stand-in of
 * their own (test/README.md, "Headers"). Values are ESP-IDF v5.4.1's. */
#ifndef TWAI_FAKE_HOST_ESP_ERR_H
#define TWAI_FAKE_HOST_ESP_ERR_H

typedef int esp_err_t;
#define ESP_OK                  0
#define ESP_FAIL                -1
#define ESP_ERR_NO_MEM          0x101
#define ESP_ERR_INVALID_ARG     0x102
#define ESP_ERR_INVALID_STATE   0x103
#define ESP_ERR_INVALID_SIZE    0x104
#define ESP_ERR_NOT_FOUND       0x105
#define ESP_ERR_NOT_SUPPORTED   0x106
#define ESP_ERR_TIMEOUT         0x107

#ifdef __cplusplus
extern "C" {
#endif
const char *esp_err_to_name(esp_err_t e);
/* What ESP_ERROR_CHECK does on a failure: on the device, a message and a
 * restart; on the host, host_platform.c records it and returns. */
void host_error_check_failed(esp_err_t e, const char *expr);
#ifdef __cplusplus
}
#endif
#define ESP_ERROR_CHECK(x) do { esp_err_t e_ = (x); \
        if (e_ != ESP_OK) { host_error_check_failed(e_, #x); } } while (0)

#endif
