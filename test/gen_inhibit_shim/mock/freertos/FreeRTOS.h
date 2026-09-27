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


/*
 * CRITICAL SECTIONS, for the transmit scheduler's device HAL (spec 5.2 item 4).
 *
 * A NO-OP HERE, AND THE REASON MATTERS. On the device this pair disables the TWAI
 * interrupt so that the status read and the abort command-register write are
 * indivisible -- the controller can otherwise enter arbitration between them and the
 * command lands where the measurement showed it has no effect. This harness cannot
 * reproduce that at all: fake_twai advances only when the test advances virtual time,
 * so nothing runs "between" two statements and there is no race to exclude.
 *
 * So these are empty rather than modelled, and a shim case must NOT be read as
 * evidence that the atomicity works. What covers the race is fb_race_next() in
 * test/gen_inhibit_sched, which forces the interleaving the hardware can produce.
 * Recording that here because an empty macro is exactly the kind of thing a later
 * session mistakes for "tested".
 */
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define taskENTER_CRITICAL(mux)  ((void)(mux))
#define taskEXIT_CRITICAL(mux)   ((void)(mux))

#endif
