/* Mock freertos/FreeRTOS.h for the E1 host build. */
#ifndef MOCK_FREERTOS_H
#define MOCK_FREERTOS_H
#include <stdint.h>
typedef uint32_t TickType_t;
#define portTICK_PERIOD_MS 1
#define pdMS_TO_TICKS(ms)  ((TickType_t)(ms))
#define pdTRUE  1
#define pdFALSE 0
#define portMAX_DELAY ((TickType_t)0xFFFFFFFF)
#define BIT0 (1u << 0)
#define BIT1 (1u << 1)
#define BIT2 (1u << 2)
#define BIT3 (1u << 3)
#define configTICK_RATE_HZ 1000
/* can.c wraps driver calls in this; a failure here is a test bug. */
#define ESP_ERROR_CHECK(x) ((void)(x))
/*
 * ESP-IDF's FreeRTOS.h pulls the timer API in, and can.c relies on that rather
 * than including freertos/timers.h itself. A mock that did not would compile
 * differently from the target, which is the one thing a mock must not do.
 */
#include "freertos/timers.h"

#endif
