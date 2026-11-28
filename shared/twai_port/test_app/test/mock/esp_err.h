/* Host stand-in, compile only (test_stack.py): ESP-IDF v5.4.2's error
 * codes the probe uses. */
#ifndef MOCK_ESP_ERR_H
#define MOCK_ESP_ERR_H
typedef int esp_err_t;
#define ESP_OK                      0
#define ESP_FAIL                    -1
#define ESP_ERR_NOT_FOUND           0x105
#define ESP_ERR_HTTPD_RESULT_TRUNC  0xb007
#endif
