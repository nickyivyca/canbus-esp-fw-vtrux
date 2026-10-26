/*
 * Mock esp_timer.h. Time is VIRTUAL: it advances only when a test advances it,
 * which is what makes a run reproducible. A host clock here would make every
 * timing assertion flaky and every trace different.
 */
#ifndef MOCK_ESP_TIMER_H
#define MOCK_ESP_TIMER_H
#include <stdint.h>
int64_t esp_timer_get_time(void);
#endif
