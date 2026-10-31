#ifndef TWAI_FAKE_HOST_FREERTOS_TASK_H
#define TWAI_FAKE_HOST_FREERTOS_TASK_H

#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif
/* host_platform.c: the fake's clock, in ticks. */
TickType_t xTaskGetTickCount(void);
#ifdef __cplusplus
}
#endif

#endif
