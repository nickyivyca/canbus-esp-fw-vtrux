/* Minimal host FreeRTOS.h for builds on the twai_port fake (test/README.md,
 * "Headers"): one tick is one millisecond, as both projects configure. */
#ifndef TWAI_FAKE_HOST_FREERTOS_H
#define TWAI_FAKE_HOST_FREERTOS_H

#include <stdint.h>

typedef uint32_t TickType_t;
typedef int BaseType_t;
#define portMAX_DELAY           ((TickType_t)0xffffffffUL)
#define portTICK_PERIOD_MS      1
#define configTICK_RATE_HZ      1000
#define pdMS_TO_TICKS(ms)       ((TickType_t)(ms))
#define pdTRUE                  1
#define pdFALSE                 0

typedef struct { int unused; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
#define taskENTER_CRITICAL(m)   ((void)(m))
#define taskEXIT_CRITICAL(m)    ((void)(m))

#endif
