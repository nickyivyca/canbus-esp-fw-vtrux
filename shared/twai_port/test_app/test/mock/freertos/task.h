/* Host stand-in, compile only (test_stack.py): the task calls the probe
 * makes. Nothing links against it. */
#ifndef MOCK_FREERTOS_TASK_H
#define MOCK_FREERTOS_TASK_H
#include "freertos/FreeRTOS.h"
typedef void *TaskHandle_t;
TaskHandle_t xTaskGetCurrentTaskHandle(void);
UBaseType_t uxTaskPriorityGet(TaskHandle_t t);
void vTaskPrioritySet(TaskHandle_t t, UBaseType_t prio);
void vTaskDelay(TickType_t ticks);
UBaseType_t uxTaskGetStackHighWaterMark(TaskHandle_t t);
#endif
