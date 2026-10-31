/* Minimal host esp_log.h: errors and warnings are counted by host_platform.c
 * and printed, so a case can see that the port logged one. */
#ifndef TWAI_FAKE_HOST_ESP_LOG_H
#define TWAI_FAKE_HOST_ESP_LOG_H

#ifdef __cplusplus
extern "C" {
#endif
void host_log(char level, const char *tag, const char *fmt, ...);
#ifdef __cplusplus
}
#endif
#define ESP_LOGE(tag, ...) host_log('E', tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) host_log('W', tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) host_log('I', tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) ((void)(tag))

#endif
