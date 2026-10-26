/*
 * Mock esp_log.h. Logs go to stdout with the virtual timestamp so a failing
 * case can be read against the trace, and are counted so a test can assert
 * that a refusal WAS logged -- spec 3.2 requires refusals to be logged rather
 * than silently dropped, which is only checkable if the harness can see them.
 */
#ifndef MOCK_ESP_LOG_H
#define MOCK_ESP_LOG_H
#include <stdio.h>
#include <stdint.h>
int64_t esp_timer_get_time(void);
void ml_note(char level, const char *fmt, ...);
int  ml_count(const char *needle);
void ml_clear(void);
#define ESP_LOGE(tag, fmt, ...) ml_note('E', fmt, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) ml_note('W', fmt, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) ml_note('I', fmt, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) ml_note('D', fmt, ##__VA_ARGS__)
#define ESP_LOG_BUFFER_HEXDUMP(tag, b, l, lvl) ((void)0)
#endif
