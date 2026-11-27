/* Host stand-in: the critical section is counted on the mock controller, so
 * a case can check the abort command is written inside it. */
#ifndef MOCK_FREERTOS_H
#define MOCK_FREERTOS_H
#include "hal/twai_ll.h"
typedef struct { int unused; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
#define taskENTER_CRITICAL(m) ((void)(m), mock_twai_dev.in_critical++)
#define taskEXIT_CRITICAL(m)  ((void)(m), mock_twai_dev.in_critical--)
#endif
