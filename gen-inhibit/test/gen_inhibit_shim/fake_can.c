/*
 * Platform stubs so the REAL main/can.c compiles and runs here.
 *
 * WHY THIS REPLACED A REIMPLEMENTATION. The first version of this file WAS a
 * reimplementation: can_enable/can_disable/can_send written out by hand, with
 * the spec 3.2 refusal copied in. The reviewing session mutated the refusal out
 * of the real main/can.c and every case stayed green -- because case 5 was
 * exercising the copy. A test that passes when the shipped code is deleted is
 * worse than no test: it reports safety it has not checked.
 *
 * So main/can.c is compiled as-is now, and this file provides only what the
 * platform would: an event group, a GPIO, and the queues it declares. If
 * can.c's dependencies grow, add stubs here rather than copying behaviour.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "driver/gpio.h"

#include <stdint.h>
#include <stddef.h>

/* One event group is enough: can.c uses a single CAN_ENABLE_BIT. */
static EventBits_t g_bits;

EventGroupHandle_t xEventGroupCreate(void)
{
    g_bits = 0;
    return (EventGroupHandle_t)&g_bits;
}

EventBits_t xEventGroupSetBits(EventGroupHandle_t g, EventBits_t bits)
{
    (void)g; g_bits |= bits; return g_bits;
}

EventBits_t xEventGroupClearBits(EventGroupHandle_t g, EventBits_t bits)
{
    (void)g; g_bits &= ~bits; return g_bits;
}

EventBits_t xEventGroupGetBits(EventGroupHandle_t g)
{
    (void)g; return g_bits;
}

EventBits_t xEventGroupWaitBits(EventGroupHandle_t g, EventBits_t bits,
                                int clear, int all, TickType_t wait)
{
    (void)g; (void)bits; (void)clear; (void)all; (void)wait;
    return g_bits;
}

/* The transceiver standby line. Recorded so a case could assert on it. */
static int g_gpio_level[64];

int gpio_set_level(gpio_num_t pin, uint32_t level)
{
    if (pin >= 0 && pin < 64) g_gpio_level[pin] = (int)level;
    return 0;
}

int gpio_get_level(gpio_num_t pin)
{
    return (pin >= 0 && pin < 64) ? g_gpio_level[pin] : 0;
}

/*
 * FreeRTOS software timers. can.c creates one to re-enable the bus after a
 * delay; nothing in E1 depends on it firing, and a timer that never fires is
 * the honest model rather than one that fires at an invented moment.
 */
#include "freertos/timers.h"

static int g_timer_active;

TimerHandle_t xTimerCreate(const char *name, TickType_t period, int reload,
                           void *id, TimerCallbackFunction_t cb)
{
    (void)name; (void)period; (void)reload; (void)id; (void)cb;
    return (TimerHandle_t)&g_timer_active;
}

int xTimerStart(TimerHandle_t t, TickType_t wait)
{
    (void)t; (void)wait; g_timer_active = 1; return 1;
}

int xTimerStop(TimerHandle_t t, TickType_t wait)
{
    (void)t; (void)wait; g_timer_active = 0; return 1;
}

int xTimerReset(TimerHandle_t t, TickType_t wait)
{
    (void)t; (void)wait; g_timer_active = 1; return 1;
}

int xTimerIsTimerActive(TimerHandle_t t) { (void)t; return g_timer_active; }
void *pvTimerGetTimerID(TimerHandle_t t) { (void)t; return NULL; }
