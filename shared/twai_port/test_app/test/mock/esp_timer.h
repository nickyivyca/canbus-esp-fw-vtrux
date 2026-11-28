/* Host stand-in, compile only (test_stack.py). */
#ifndef MOCK_ESP_TIMER_H
#define MOCK_ESP_TIMER_H
#include <stdint.h>
int64_t esp_timer_get_time(void);
#endif
