/*
 * Mock freertos/event_groups.h. can.c uses an event group as a single bit
 * (CAN_ENABLE_BIT) guarding can_send(), so a plain word is a faithful model.
 */
#ifndef MOCK_EVENT_GROUPS_H
#define MOCK_EVENT_GROUPS_H
#include "freertos/FreeRTOS.h"
typedef uint32_t EventBits_t;
typedef struct meg_s *EventGroupHandle_t;
EventGroupHandle_t xEventGroupCreate(void);
EventBits_t xEventGroupSetBits(EventGroupHandle_t g, EventBits_t bits);
EventBits_t xEventGroupClearBits(EventGroupHandle_t g, EventBits_t bits);
EventBits_t xEventGroupGetBits(EventGroupHandle_t g);
EventBits_t xEventGroupWaitBits(EventGroupHandle_t g, EventBits_t bits,
                                int clear, int all, TickType_t wait);
#endif
