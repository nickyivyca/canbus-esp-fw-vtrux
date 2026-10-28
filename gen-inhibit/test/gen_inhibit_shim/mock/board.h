/* Mock board.h: the part of the board layer's interface (shared/boards SPEC
 * B5) that the host-built sources reach, can.c directly and the rest through
 * hw_config.h. fake_can.c answers as wican_v3 does: port 0's TWAI pins,
 * and its standby line, recorded so a case can assert on it. */
#ifndef MOCK_BOARD_H
#define MOCK_BOARD_H
#include <stdbool.h>
#include "esp_err.h"
#include "driver/gpio.h"

typedef struct {
    gpio_num_t tx;
    gpio_num_t rx;
} board_twai_pins_t;

esp_err_t board_twai_pins(int port, board_twai_pins_t *out);
esp_err_t board_can_standby(int port, bool on);

/* What a case reads: the last level board_can_standby() set on port 0, 1 for
 * standby. -1 before any call. */
int fake_board_standby(void);

#define FS_MOUNT_POINT              "/spiffs"
#define BOARD_AP_IP_ADDR            "192.168.80.1"
#define BOARD_AP_NETMASK            "255.255.255.0"

#endif
