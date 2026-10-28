/*
 * board.c -- wican_v3: MeatPi WiCAN OBD, hardware v3 (shared/boards/SPEC.md
 * section 2), behind the B5 interface in board.h.
 *
 * One CAN port: port 0 is the C3's TWAI, TX GPIO0, RX GPIO3. Its MCP2561/2's
 * STBY is GPIO6: high is standby, with the receiver and transmitter off, and
 * low is normal; the transceiver's pull-up holds it in standby while the pin
 * floats. No SPI CAN.
 */
#include <stddef.h>

#include "esp_attr.h"
#include "driver/gpio.h"
#include "soc/soc.h"
#include "soc/gpio_reg.h"

#include "board.h"
#include "board_pins.h"

/* board_can_standby() drives STBY with one write to GPIO_OUT_W1TS_REG or
 * GPIO_OUT_W1TC_REG, which cover GPIO0-31. */
_Static_assert(CAN_STDBY_GPIO_NUM < 32,
               "board_can_standby writes GPIO_OUT_W1TS/W1TC, GPIO0-31 only");

int board_can_ports(void)
{
    return 1;
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    if (port != 0 || out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    out->tx = (gpio_num_t)TX_GPIO_NUM;
    out->rx = (gpio_num_t)RX_GPIO_NUM;
    return ESP_OK;
}

esp_err_t board_spi_can(int port, board_spi_can_t *out)
{
    if (port != 0 || out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_can_init(int port)
{
    if (port != 0)
    {
        return ESP_ERR_INVALID_ARG;
    }
    /* What upstream WiCAN's app_main does, in its order. */
    gpio_reset_pin(CAN_STDBY_GPIO_NUM);
    gpio_set_direction(CAN_STDBY_GPIO_NUM, GPIO_MODE_OUTPUT);
    gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
    return ESP_OK;
}

/*
 * The restart path calls this with interrupts off, before the real
 * esp_restart_noos() disables the cache: so IRAM, and nothing but the
 * register write. It needs board_can_init() to have made the pin an output;
 * before that the write sets the output latch only, and the transceiver's
 * pull-up holds it in standby.
 */
esp_err_t IRAM_ATTR board_can_standby(int port, bool on)
{
    if (port != 0)
    {
        return ESP_ERR_INVALID_ARG;
    }
    REG_WRITE(on ? GPIO_OUT_W1TS_REG : GPIO_OUT_W1TC_REG,
              BIT(CAN_STDBY_GPIO_NUM));
    return ESP_OK;
}

/* Offered outputs and inputs: each pin set up on its first use. Setting one
 * up twice does no harm, so two first uses at once need no lock. */
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

static void ready_input(gpio_num_t pin)
{
    if (s_ready & BIT(pin))
    {
        return;
    }
    gpio_config_t c = {
        .pin_bit_mask = 1ULL << pin,
        .mode         = GPIO_MODE_INPUT,
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
    case BOARD_LED_CONNECTED: pin = (gpio_num_t)CONNECTED_LED_GPIO_NUM; break;
    case BOARD_LED_ACTIVE:    pin = (gpio_num_t)ACTIVE_LED_GPIO_NUM;    break;
    case BOARD_LED_POWER:     pin = (gpio_num_t)PWR_LED_GPIO_NUM;       break;
    default:                  return ESP_ERR_INVALID_ARG;
    }
    ready_output(pin);
    return gpio_set_level(pin, on ? 1 : 0);
}

bool board_ble_enable_input(void)
{
    ready_input((gpio_num_t)BLE_EN_PIN_NUM);
    return gpio_get_level(BLE_EN_PIN_NUM) == 0;
}

const char *board_hw_name(void)
{
    return "OBD";
}
