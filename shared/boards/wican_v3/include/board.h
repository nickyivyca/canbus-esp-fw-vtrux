/*
 * board.h -- the board layer's interface (shared/boards/SPEC.md B5), as
 * wican_v3 gives it: MeatPi WiCAN OBD, hardware v3 (section 2).
 *
 * Project code asks the board for a port, never for a pin. The pins are in
 * board_pins.h, which project code does not include (B6, shared/tools R30).
 * The per-model values stock code uses at compile time are macros here.
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

/* The number of CAN ports, numbered from 0. wican_v3: 1. */
int board_can_ports(void);

/* The ESP's own TWAI controller's pins, for a TWAI port. wican_v3: port 0. */
typedef struct {
    gpio_num_t tx;
    gpio_num_t rx;
} board_twai_pins_t;

esp_err_t board_twai_pins(int port, board_twai_pins_t *out);

/* The controller, SPI host, pins and crystal, for an SPI CAN port.
 * wican_v3 has none. */
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

/* Sets up the port's standby line as an output and puts the transceiver in
 * standby. Call it once at start-up, before board_can_standby(), and not
 * from the restart path. wican_v3: GPIO6, driven high. */
esp_err_t board_can_init(int port);

/* Puts the port's transceiver into standby (on) or normal operation (off).
 * Safe to call from the restart path, with interrupts off and the cache
 * disabled: in IRAM, no locks, no allocation, no logging, one register
 * write. wican_v3: GPIO6, high is standby, with the receiver and
 * transmitter off. */
esp_err_t board_can_standby(int port, bool on);

/* Offered outputs and inputs (B3.3), by name. Each sets its pin up on first
 * use. */
typedef enum {
    BOARD_LED_CONNECTED,    /* GPIO8 */
    BOARD_LED_ACTIVE,       /* GPIO9 */
    BOARD_LED_POWER,        /* GPIO7 */
} board_led_t;

esp_err_t board_led(board_led_t led, bool on);

/* The Bluetooth-enable input (GPIO5): true when it asks for Bluetooth on,
 * which is the pin low. */
bool board_ble_enable_input(void);

/* The hardware name, which a status page shows: "OBD". */
const char *board_hw_name(void);

/* Per-model values (B5, section 2), which upstream code sets per WiCAN model
 * and uses at compile time. FS_MOUNT_POINT is upstream's name; the hotspot's
 * have none upstream, and are dotted strings for esp_netif_str_to_ip4(). */
#define FS_MOUNT_POINT              "/spiffs"           /* the storage partition */
#define BOARD_AP_IP_ADDR            "192.168.80.1"      /* also the gateway */
#define BOARD_AP_NETMASK            "255.255.255.0"

#ifdef __cplusplus
}
#endif

#endif
