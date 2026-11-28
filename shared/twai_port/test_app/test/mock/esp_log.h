/* Host stand-in, compile only (test_stack.py): the log macros as a
 * variadic call, as on the target, so the caller's frame holds the same
 * argument spill. */
#ifndef MOCK_ESP_LOG_H
#define MOCK_ESP_LOG_H
void esp_log_write(int level, const char *tag, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
#define ESP_LOGE(tag, fmt, ...) esp_log_write(1, tag, fmt, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) esp_log_write(2, tag, fmt, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) esp_log_write(3, tag, fmt, ##__VA_ARGS__)
#endif
