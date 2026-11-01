/*
 * A link stub for the real vtrux/twai_port (port step 1b): the tick count, which
 * the shim's FreeRTOS stand-ins did not have. It is not reached with port 0's
 * settings (spec 3.3): they keep remote frames, so the wait twai_port_receive()
 * measures is never shortened. The driver's calls, twai_initiate_recovery()
 * among them, are the shared fake's (shared/twai_port/test/), and behaviour
 * belongs there or in shim_harness.c, not here (fake_can.c's rule).
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

TickType_t xTaskGetTickCount(void)
{
    return 0;
}
