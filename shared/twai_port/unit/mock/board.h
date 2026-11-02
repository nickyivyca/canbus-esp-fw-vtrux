/*
 * Host stand-in for shared/boards' board.h: the two calls the port makes, and
 * the port count and kind of B5 that can_port's restart stop (C7) and the
 * restart guard ask for. Each suite defines the calls it uses.
 */
#ifndef MOCK_BOARD_H
#define MOCK_BOARD_H
#include <stdbool.h>
#include "esp_err.h"
typedef struct { int tx; int rx; } board_twai_pins_t;
typedef enum {
    BOARD_CAN_NONE = 0,
    BOARD_CAN_TWAI,
    BOARD_CAN_SPI,
} board_can_kind_t;
int board_can_ports(void);
board_can_kind_t board_can_kind(int port);
esp_err_t board_twai_pins(int port, board_twai_pins_t *out);
esp_err_t board_can_standby(int port, bool on);
#endif
