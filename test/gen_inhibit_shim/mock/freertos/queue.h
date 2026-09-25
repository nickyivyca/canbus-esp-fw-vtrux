/* Mock freertos/queue.h. Declarations only; can.c's queues are unused here. */
#ifndef MOCK_QUEUE_H
#define MOCK_QUEUE_H
#include "freertos/FreeRTOS.h"
typedef struct mq_s *QueueHandle_t;
#endif
