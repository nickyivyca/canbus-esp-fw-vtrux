/* One event group, a plain word: WaitBits returns at once with what is set. */
#ifndef MOCK_EVENT_GROUPS_H
#define MOCK_EVENT_GROUPS_H
#include "freertos/FreeRTOS.h"
typedef EventBits_t *EventGroupHandle_t;
EventGroupHandle_t xEventGroupCreate(void);
EventBits_t xEventGroupSetBits(EventGroupHandle_t g, EventBits_t b);
EventBits_t xEventGroupClearBits(EventGroupHandle_t g, EventBits_t b);
EventBits_t xEventGroupGetBits(EventGroupHandle_t g);
EventBits_t xEventGroupWaitBits(EventGroupHandle_t g, EventBits_t b, BaseType_t clear,
                                BaseType_t all, TickType_t wait);
#endif
