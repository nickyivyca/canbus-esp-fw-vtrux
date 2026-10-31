/* Host stand-in: ticks, and critical sections that do nothing (one thread). */
#ifndef MOCK_FREERTOS_H
#define MOCK_FREERTOS_H
#include <stdint.h>
#include <stdbool.h>
typedef uint32_t TickType_t;
typedef int BaseType_t;
#define portMAX_DELAY ((TickType_t)0xffffffffUL)
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
typedef struct { int unused; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
#define taskENTER_CRITICAL(m) ((void)(m))
#define taskEXIT_CRITICAL(m)  ((void)(m))
#endif
