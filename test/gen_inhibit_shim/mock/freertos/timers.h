/*
 * Mock freertos/timers.h. can.c uses a one-shot timer to re-enable the bus;
 * the model never fires it, which is faithful enough for what E1 checks and is
 * stated rather than hidden.
 */
#ifndef MOCK_TIMERS_H
#define MOCK_TIMERS_H
#include "freertos/FreeRTOS.h"
typedef struct mt_s *TimerHandle_t;
typedef void (*TimerCallbackFunction_t)(TimerHandle_t);
TimerHandle_t xTimerCreate(const char *name, TickType_t period, int reload,
                           void *id, TimerCallbackFunction_t cb);
int xTimerStart(TimerHandle_t t, TickType_t wait);
int xTimerStop(TimerHandle_t t, TickType_t wait);
int xTimerReset(TimerHandle_t t, TickType_t wait);
int xTimerIsTimerActive(TimerHandle_t t);
void *pvTimerGetTimerID(TimerHandle_t t);
#endif
