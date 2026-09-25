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
#endif
