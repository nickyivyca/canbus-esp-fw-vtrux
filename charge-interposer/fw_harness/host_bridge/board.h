/*
 * Host stand-in for the board layer's interface, board.h (shared/boards
 * SPEC B5), as the x2 component gives it at shared tag 2026.11.03, for the
 * L2 host tests that compile the firmware's main.cpp (port step 2a: main.cpp
 * asks the board for its CAN ports through main/board_ports.h).
 *
 * The declarations are x2's own. What differs is only what a PC build has to
 * supply itself: esp_err_t, gpio_num_t and spi_host_device_t, which the real
 * header takes from ESP-IDF's esp_err.h, hal/gpio_types.h and
 * hal/spi_types.h. They are plain ints and an enum with ESP-IDF's values, the
 * same types the shared fake's driver/twai.h and host headers give
 * (shared/twai_port/test/include and host), so a test that includes both
 * builds. The answers are in board_fake.cpp, from shared/boards SPEC
 * section 3; since port step 2b twai_port.c asks them for the vehicle
 * port's pins and standby line too.
 *
 * It is found ahead of any other board.h: l2_build.sh puts this folder first
 * on the include path, and the shared fake's own host board.h (for its
 * host_platform.c, which is not built here) comes after.
 */
#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stdint.h>

/* ESP-IDF's types and codes, as plain C. A repeated identical typedef or
 * macro is allowed, so this header and driver/twai.h can both be seen. */
typedef int esp_err_t;
#ifndef ESP_OK
#define ESP_OK 0
#endif
#ifndef ESP_ERR_INVALID_ARG
#define ESP_ERR_INVALID_ARG 0x102
#endif
#ifndef ESP_ERR_NOT_SUPPORTED
#define ESP_ERR_NOT_SUPPORTED 0x106
#endif
typedef int gpio_num_t;
/* hal/spi_types.h on the S3: SPI1 is the flash's, SPI3 is arduino-esp32's
 * HSPI. */
typedef enum {
    SPI1_HOST = 0,
    SPI2_HOST = 1,
    SPI3_HOST = 2,
} spi_host_device_t;

#ifdef __cplusplus
extern "C" {
#endif

int board_can_ports(void);

typedef enum {
    BOARD_CAN_NONE = 0,
    BOARD_CAN_TWAI,
    BOARD_CAN_SPI,
} board_can_kind_t;

board_can_kind_t board_can_kind(int port);

typedef struct {
    gpio_num_t tx;
    gpio_num_t rx;
} board_twai_pins_t;

esp_err_t board_twai_pins(int port, board_twai_pins_t *out);

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

esp_err_t board_can_init(int port);
esp_err_t board_can_standby(int port, bool on);

typedef enum {
    BOARD_LED_LED1,
} board_led_t;

esp_err_t board_led(board_led_t led, bool on);

const char *board_hw_name(void);

#ifdef __cplusplus
}
#endif

#endif
