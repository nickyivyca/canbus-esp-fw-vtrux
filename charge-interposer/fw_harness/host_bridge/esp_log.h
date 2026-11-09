// Just enough of ESP-IDF's logging API for `main.cpp` and the shared
// vtrux/twai_port to compile and link on the host.
//
// esp_log_level_set (added 2026-10-13: spec 8.3's truck build calls
// esp_log_level_set("*", ESP_LOG_NONE) at boot, 0c4e300). The host has no
// framework logging to silence, so the call does nothing. It records the
// last level set, for any test that wants to read it; no L2 result rests on
// it, because the host build is not the image whose serial silence 8.3 is
// about -- that is checked on the bench.
//
// ESP_LOGE/W/I (port step 2b: twai_port.c logs through them, and this
// header is found ahead of the shared fake's host/esp_log.h). They go to
// host_log() in idf_host.cpp, which prints each one on a line of its own,
// indented, and counts errors and warnings, as the shared fake's
// host_platform.c does. ESP_LOGD prints nothing.
//
// C and C++: twai_port.c is compiled as C.

#ifndef HOST_BRIDGE_ESP_LOG_H
#define HOST_BRIDGE_ESP_LOG_H

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
  ESP_LOG_NONE = 0,
  ESP_LOG_ERROR,
  ESP_LOG_WARN,
  ESP_LOG_INFO,
  ESP_LOG_DEBUG,
  ESP_LOG_VERBOSE
} esp_log_level_t;

__attribute__((unused)) static esp_log_level_t host_esp_log_level_last =
    ESP_LOG_VERBOSE;

static inline void esp_log_level_set(const char* tag, esp_log_level_t level) {
  (void)tag;
  host_esp_log_level_last = level;
}

// idf_host.cpp: prints "    <ms> <level> <tag>: <text>" and counts.
void host_log(char level, const char* tag, const char* fmt, ...)
    __attribute__((format(printf, 3, 4)));
// Errors and warnings logged so far in this process.
extern int host_log_e;
extern int host_log_w;

#ifdef __cplusplus
}
#endif

#define ESP_LOGE(tag, ...) host_log('E', tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) host_log('W', tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) host_log('I', tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) ((void)(tag))

#endif  // HOST_BRIDGE_ESP_LOG_H
