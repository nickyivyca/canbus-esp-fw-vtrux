/* Host stand-in for esp_err.h: the codes the port uses, ESP-IDF 5.4.1's values. */
#ifndef MOCK_ESP_ERR_H
#define MOCK_ESP_ERR_H
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK                  0
#define ESP_FAIL                -1
#define ESP_ERR_NO_MEM          0x101
#define ESP_ERR_INVALID_ARG     0x102
#define ESP_ERR_INVALID_STATE   0x103
#define ESP_ERR_NOT_SUPPORTED   0x106
#define ESP_ERR_TIMEOUT         0x107
const char *esp_err_to_name(esp_err_t e);
/* The abort path: the stub records the error and jumps back to the case. */
void stub_error_check_failed(esp_err_t e);
#define ESP_ERROR_CHECK(x) do { esp_err_t e_ = (x); if (e_ != ESP_OK) stub_error_check_failed(e_); } while (0)
#endif
