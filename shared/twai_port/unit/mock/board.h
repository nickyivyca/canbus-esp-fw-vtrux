/* Host stand-in for shared/boards' board.h: the two calls the port makes. */
#ifndef MOCK_BOARD_H
#define MOCK_BOARD_H
#include <stdbool.h>
#include "esp_err.h"
typedef struct { int tx; int rx; } board_twai_pins_t;
esp_err_t board_twai_pins(int port, board_twai_pins_t *out);
esp_err_t board_can_standby(int port, bool on);
#endif
