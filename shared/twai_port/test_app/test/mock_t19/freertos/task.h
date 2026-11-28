/* Host stand-in for test_t19.c: the unit mock's task.h, and the task calls
 * the T19 app's restart command makes. The test defines them. */
#ifndef MOCK_T19_FREERTOS_TASK_H
#define MOCK_T19_FREERTOS_TASK_H
#include_next "freertos/task.h"
#include <stdint.h>
#ifndef pdPASS
#define pdPASS 1
#endif
typedef void *TaskHandle_t;
typedef void (*TaskFunction_t)(void *);
BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char *name,
                                   uint32_t stack, void *arg,
                                   unsigned prio, TaskHandle_t *out,
                                   BaseType_t core);
void vTaskDelay(TickType_t ticks);
#endif
