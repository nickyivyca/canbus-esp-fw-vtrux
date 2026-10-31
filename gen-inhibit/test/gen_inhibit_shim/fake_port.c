/*
 * Link stubs for the real vtrux/twai_port (port step 1b): the two calls it
 * makes that the shim's platform stand-ins did not have. Neither is reached
 * with port 0's settings (spec 3.3): it never recovers from bus-off, and keeps
 * remote frames, so the wait twai_port_receive() measures is never shortened.
 * Behaviour belongs in fake_twai.c, not here (fake_can.c's rule).
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/twai.h"

/* What the driver returns when the controller is not bus-off. */
esp_err_t twai_initiate_recovery(void)
{
    return ESP_ERR_INVALID_STATE;
}

TickType_t xTaskGetTickCount(void)
{
    return 0;
}
