/* Host stand-in for esp_timer.h: the switch's abort wait (T17) reads its
 * elapsed time from it. The stub's clock is the waits' microseconds plus
 * any time it is told passes at each read (stub.h, timer_per_read_us). */
#ifndef MOCK_ESP_TIMER_H
#define MOCK_ESP_TIMER_H
#include <stdint.h>
int64_t esp_timer_get_time(void);
#endif
