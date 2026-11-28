/* Minimal host esp_timer.h for builds on the twai_port fake with no stand-in
 * of their own (test/README.md, "Headers"): esp_timer_get_time(), as ESP-IDF
 * v5.4.2 declares it (components/esp_timer/include/esp_timer.h). twai_fake.c
 * gives a weak definition on the fake's clock, which also counts the busy
 * waits the clock did not see (twai_fake.h, "Busy waits"); a build that
 * defines its own keeps it. */
#ifndef TWAI_FAKE_HOST_ESP_TIMER_H
#define TWAI_FAKE_HOST_ESP_TIMER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
int64_t esp_timer_get_time(void);
#ifdef __cplusplus
}
#endif

#endif
