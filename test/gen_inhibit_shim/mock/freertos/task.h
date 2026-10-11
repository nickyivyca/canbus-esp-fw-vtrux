/*
 * Mock freertos/task.h.
 *
 * vTaskDelay yields to the test and advances virtual time, exactly as a
 * blocking receive does -- if it did not, the worker's OFF branch (which
 * delays 50 ms and loops) would spin for ever on the host.
 */
#ifndef MOCK_FREERTOS_TASK_H
#define MOCK_FREERTOS_TASK_H
#include "freertos/FreeRTOS.h"
typedef void *TaskHandle_t;
typedef void (*TaskFunction_t)(void *);
void vTaskDelay(TickType_t ticks);
/*
 * Task notifications, for spec 3's wake on a mode change. Declared with int
 * where the real API uses BaseType_t, which this mock does not define.
 */
uint32_t ulTaskNotifyTake(int clear_on_exit, TickType_t ticks);
int xTaskNotifyGive(TaskHandle_t task);
TaskHandle_t xTaskGetCurrentTaskHandle(void);
int xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack,
                void *arg, uint32_t prio, TaskHandle_t *out);
#endif
