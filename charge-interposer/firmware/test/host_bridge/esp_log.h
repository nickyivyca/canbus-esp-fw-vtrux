// Just enough of ESP-IDF's logging API for `main.cpp` to compile and link
// on the host (added 2026-10-13: spec 8.3's truck build calls
// esp_log_level_set("*", ESP_LOG_NONE) at boot, 0c4e300).
//
// The host has no framework logging to silence, so the call does nothing.
// It records the last level set, for any test that wants to read it; no
// L2 result rests on it, because the host build is not the image whose
// serial silence 8.3 is about -- that is checked on the bench.

#pragma once

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

static esp_log_level_t host_esp_log_level_last = ESP_LOG_VERBOSE;

static inline void esp_log_level_set(const char* tag, esp_log_level_t level) {
  (void)tag;
  host_esp_log_level_last = level;
}

#ifdef __cplusplus
}
#endif
