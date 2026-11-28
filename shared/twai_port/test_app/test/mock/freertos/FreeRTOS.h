/* Host stand-in: the critical section is counted on the mock controller, so
 * a case can check the abort command is written inside it. The types and
 * pdMS_TO_TICKS() are for the compile-only builds of test_stack.py. */
#ifndef MOCK_FREERTOS_H
#define MOCK_FREERTOS_H
#include <stdint.h>
#include "hal/twai_ll.h"
typedef struct { int unused; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
#define taskENTER_CRITICAL(m) ((void)(m), mock_twai_dev.in_critical++)
#define taskEXIT_CRITICAL(m)  ((void)(m), mock_twai_dev.in_critical--)
typedef uint32_t TickType_t;
typedef int32_t  BaseType_t;
typedef uint32_t UBaseType_t;
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
#endif
