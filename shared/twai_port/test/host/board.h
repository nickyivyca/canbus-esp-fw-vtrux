/* Minimal host board.h (shared/boards B5's two calls twai_port makes), for
 * the fake's own build of the real port. host_platform.c implements them. */
#ifndef TWAI_FAKE_HOST_BOARD_H
#define TWAI_FAKE_HOST_BOARD_H

#include <stdbool.h>
#include "esp_err.h"
#include "driver/gpio.h"

typedef struct {
    gpio_num_t tx;
    gpio_num_t rx;
} board_twai_pins_t;

#ifdef __cplusplus
extern "C" {
#endif
esp_err_t board_twai_pins(int port, board_twai_pins_t *out);
esp_err_t board_can_standby(int port, bool on);
#ifdef __cplusplus
}
#endif

#endif
