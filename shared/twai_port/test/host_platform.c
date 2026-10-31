/*
 * The platform calls the real twai_port makes besides the driver, for the
 * fake's own build of it (test_port_on_fake.c): the board's two calls
 * (shared/boards B5), the tick count on the fake's clock, logging, and
 * what ESP_ERROR_CHECK does. A project building the port on the fake
 * brings its own; none of this is the driver model.
 */
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>

#include "board.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "twai_fake.h"

int host_log_e, host_log_w;
int host_error_checks;
int host_standby = -1;          /* the transceiver's standby line, -1 unset */
int host_pins_fail;             /* board_twai_pins() fails while set */

const char *esp_err_to_name(esp_err_t e)
{
    switch (e)
    {
    case ESP_OK:                return "ESP_OK";
    case ESP_FAIL:              return "ESP_FAIL";
    case ESP_ERR_NO_MEM:        return "ESP_ERR_NO_MEM";
    case ESP_ERR_INVALID_ARG:   return "ESP_ERR_INVALID_ARG";
    case ESP_ERR_INVALID_STATE: return "ESP_ERR_INVALID_STATE";
    case ESP_ERR_NOT_SUPPORTED: return "ESP_ERR_NOT_SUPPORTED";
    case ESP_ERR_TIMEOUT:       return "ESP_ERR_TIMEOUT";
    default:                    return "ESP_ERR_?";
    }
}

void host_error_check_failed(esp_err_t e, const char *expr)
{
    host_error_checks++;
    printf("    ESP_ERROR_CHECK(%s) failed: %s (the device would restart)\n",
           expr, esp_err_to_name(e));
}

void host_log(char level, const char *tag, const char *fmt, ...)
{
    char buf[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (level == 'E') { host_log_e++; }
    if (level == 'W') { host_log_w++; }
    printf("    %8lld %c %s: %s\n", (long long)twai_fake_now(), level, tag, buf);
}

TickType_t xTaskGetTickCount(void)
{
    return (TickType_t)(twai_fake_now() / 1000);
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    if (port != 0 || host_pins_fail) { return ESP_ERR_NOT_FOUND; }
    out->tx = 21;
    out->rx = 20;
    return ESP_OK;
}

esp_err_t board_can_standby(int port, bool on)
{
    if (port != 0) { return ESP_ERR_NOT_FOUND; }
    host_standby = on ? 1 : 0;
    return ESP_OK;
}
