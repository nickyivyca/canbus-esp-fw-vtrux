/* One timer that runs only when the case fires it (stub_fire_timer()). */
#ifndef MOCK_TIMERS_H
#define MOCK_TIMERS_H
#include "freertos/FreeRTOS.h"
struct mock_timer;
typedef struct mock_timer *TimerHandle_t;
typedef void (*TimerCallbackFunction_t)(TimerHandle_t);
TimerHandle_t xTimerCreate(const char *name, TickType_t period, BaseType_t reload,
                           void *id, TimerCallbackFunction_t cb);
BaseType_t xTimerIsTimerActive(TimerHandle_t t);
BaseType_t xTimerStart(TimerHandle_t t, TickType_t wait);
BaseType_t xTimerStop(TimerHandle_t t, TickType_t wait);
BaseType_t xTimerReset(TimerHandle_t t, TickType_t wait);
#endif
