/* Host stand-in for esp_log.h: every line is counted by level, not printed. */
#ifndef MOCK_ESP_LOG_H
#define MOCK_ESP_LOG_H
extern int stub_log_e, stub_log_w;
#define ESP_LOGE(tag, ...) ((void)(tag), stub_log_e++)
#define ESP_LOGW(tag, ...) ((void)(tag), stub_log_w++)
#define ESP_LOGI(tag, ...) ((void)(tag))
#endif
