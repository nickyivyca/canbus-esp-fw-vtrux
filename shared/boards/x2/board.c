/*
 * board.c -- x2: Autosport Labs ESP32-CAN-X2 (shared/boards/SPEC.md section
 * 3), behind the B5 interface in board.h.
 *
 * Two CAN ports: port 0 is the S3's TWAI, TX GPIO7, RX GPIO6; port 1 is an
 * MCP2515 on SPI3, CS GPIO10, SCK GPIO12, MISO GPIO13, MOSI GPIO11, INT
 * GPIO3 (active low), 16 MHz crystal. Both MCP2562 transceivers have STBY
 * hardwired, so neither port has a standby line.
 */
#include <stddef.h>

#include "esp_attr.h"
#include "esp_bit_defs.h"
#include "driver/gpio.h"

#include "board.h"
#include "board_pins.h"

/*
 * The restart path calls these two (shared/can_port C7, shared/restart_guard
 * G3), so IRAM and constants only. No switch: a jump table would be in
 * flash.
 */
int IRAM_ATTR board_can_ports(void)
{
    return 2;
}

board_can_kind_t IRAM_ATTR board_can_kind(int port)
{
    return port == 0 ? BOARD_CAN_TWAI
         : port == 1 ? BOARD_CAN_SPI
         : BOARD_CAN_NONE;
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    if ((port != 0 && port != 1) || out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (port != 0)
    {
        return ESP_ERR_NOT_SUPPORTED;
    }
    out->tx = (gpio_num_t)TWAI_TX_GPIO_NUM;
    out->rx = (gpio_num_t)TWAI_RX_GPIO_NUM;
    return ESP_OK;
}

esp_err_t board_spi_can(int port, board_spi_can_t *out)
{
    if ((port != 0 && port != 1) || out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (port != 1)
    {
        return ESP_ERR_NOT_SUPPORTED;
    }
    out->controller = BOARD_SPI_CAN_MCP2515;
    out->host       = SPI3_HOST;    /* arduino-esp32's HSPI on the S3 */
    out->cs         = (gpio_num_t)MCP2515_CS_GPIO_NUM;
    out->sck        = (gpio_num_t)MCP2515_SCK_GPIO_NUM;
    out->miso       = (gpio_num_t)MCP2515_MISO_GPIO_NUM;
    out->mosi       = (gpio_num_t)MCP2515_MOSI_GPIO_NUM;
    out->irq        = (gpio_num_t)MCP2515_INT_GPIO_NUM;
    out->crystal_hz = MCP2515_CRYSTAL_HZ;
    return ESP_OK;
}

esp_err_t board_can_init(int port)
{
    if (port != 0 && port != 1)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_ERR_NOT_SUPPORTED;
}

/* The restart path may call this with interrupts off and the cache
 * disabled, so IRAM. There is nothing to write. */
esp_err_t IRAM_ATTR board_can_standby(int port, bool on)
{
    (void)on;
    if (port != 0 && port != 1)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_ERR_NOT_SUPPORTED;
}

/* Offered outputs: each pin set up on its first use. Setting one up twice
 * does no harm, so two first uses at once need no lock. */
static uint32_t s_ready;

static void ready_output(gpio_num_t pin)
{
    if (s_ready & BIT(pin))
    {
        return;
    }
    gpio_config_t c = {
        .pin_bit_mask = 1ULL << pin,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&c);
    s_ready |= BIT(pin);
}

esp_err_t board_led(board_led_t led, bool on)
{
    gpio_num_t pin;
    switch (led)
    {
    case BOARD_LED_LED1: pin = (gpio_num_t)LED1_GPIO_NUM; break;
    default:             return ESP_ERR_INVALID_ARG;
    }
    ready_output(pin);
    return gpio_set_level(pin, on ? 1 : 0);
}

const char *board_hw_name(void)
{
    return "ESP32-CAN-X2";
}
