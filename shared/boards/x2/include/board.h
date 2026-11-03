/*
 * board.h -- the board layer's interface (shared/boards/SPEC.md B5), as x2
 * gives it: Autosport Labs ESP32-CAN-X2 (section 3).
 *
 * Project code asks the board for a port, never for a pin. The pins are in
 * board_pins.h, which project code does not include (B6, shared/tools R30).
 *
 * Return values: ESP_OK; ESP_ERR_INVALID_ARG for a port the board does not
 * have or a NULL out; ESP_ERR_NOT_SUPPORTED for a port that has no such
 * thing (an SPI CAN port's TWAI pins, a TWAI port's SPI controller, a port
 * with no standby line).
 */
#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "hal/gpio_types.h"
#include "hal/spi_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The number of CAN ports, numbered from 0. x2: 2. */
int board_can_ports(void);

/* A CAN port's kind (B5): which driver takes it off the bus. */
typedef enum {
    BOARD_CAN_NONE = 0,     /* a port the board does not have */
    BOARD_CAN_TWAI,         /* the ESP's own TWAI controller */
    BOARD_CAN_SPI,          /* a CAN controller on SPI */
} board_can_kind_t;

/* The port's kind. x2: port 0 is TWAI, port 1 is SPI; any other port is
 * NONE. */
board_can_kind_t board_can_kind(int port);

/* board_can_ports() and board_can_kind() are safe to call from the restart
 * path, with interrupts off and the cache disabled: in IRAM, no locks, no
 * allocation, no logging, constants only (B5). The attribute is on the
 * definitions only: IRAM_ATTR names a fresh section at each use, and a
 * second one on the declaration conflicts. */

/* The ESP's own TWAI controller's pins, for a TWAI port. x2: port 0. */
typedef struct {
    gpio_num_t tx;
    gpio_num_t rx;
} board_twai_pins_t;

esp_err_t board_twai_pins(int port, board_twai_pins_t *out);

/* The controller, SPI host, pins and crystal, for an SPI CAN port.
 * x2: port 1, an MCP2515 on SPI3. */
typedef enum {
    BOARD_SPI_CAN_MCP2515,
} board_spi_can_controller_t;

typedef struct {
    board_spi_can_controller_t controller;
    spi_host_device_t          host;
    gpio_num_t                 cs;
    gpio_num_t                 sck;
    gpio_num_t                 miso;
    gpio_num_t                 mosi;
    gpio_num_t                 irq;         /* active low */
    uint32_t                   crystal_hz;
} board_spi_can_t;

esp_err_t board_spi_can(int port, board_spi_can_t *out);

/* Sets up the port's standby line and puts the transceiver in standby, once
 * at start-up. x2: neither port has a standby line, so "not supported" for
 * both. */
esp_err_t board_can_init(int port);

/* Puts the port's transceiver into standby (on) or normal operation (off).
 * Safe to call from the restart path, with interrupts off and the cache
 * disabled: in IRAM, no locks, no allocation, no logging. x2: "not
 * supported" for both ports. */
esp_err_t board_can_standby(int port, bool on);

/* Offered outputs (B3.3), by name. Each sets its pin up on first use. */
typedef enum {
    BOARD_LED_LED1,         /* GPIO2 */
} board_led_t;

esp_err_t board_led(board_led_t led, bool on);

/* The hardware name, which a status page shows: "ESP32-CAN-X2". */
const char *board_hw_name(void);

#ifdef __cplusplus
}
#endif

#endif
