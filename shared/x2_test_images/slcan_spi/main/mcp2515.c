/*
 * mcp2515.c -- register-level access to the X2's MCP2515 (mcp2515.h).
 * NOT FOR VEHICLE (x2_test_images SPEC T14). Written from the MCP2515
 * datasheet (DS20001801): section 12 for the SPI instructions, section 4
 * for the buffers' register layout.
 */
#include "mcp2515.h"

#include <string.h>
#include "board.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* SPI instructions (datasheet table 12-1) */
#define INS_RESET       0xC0
#define INS_READ        0x03
#define INS_WRITE       0x02
#define INS_BIT_MODIFY  0x05
#define INS_READ_RX0    0x90    /* READ RX BUFFER from RXB0SIDH */
#define INS_READ_RX1    0x94    /* READ RX BUFFER from RXB1SIDH */
#define INS_LOAD_TX0    0x40    /* LOAD TX BUFFER at TXB0SIDH */
#define INS_RTS_TX0     0x81

/* The datasheet's SPI limit is 10 MHz. */
#define SPI_HZ          10000000

static spi_device_handle_t s_dev;
static int s_int_gpio = -1;

static void xfer(const uint8_t *tx, uint8_t *rx, int n)
{
    spi_transaction_t t;
    memset(&t, 0, sizeof t);
    t.length = 8 * n;
    t.tx_buffer = tx;
    t.rx_buffer = rx;
    ESP_ERROR_CHECK(spi_device_polling_transmit(s_dev, &t));
}

esp_err_t mcp2515_attach(int port)
{
    board_spi_can_t sc;
    esp_err_t err = board_spi_can(port, &sc);
    if (err != ESP_OK) return err;
    if (sc.controller != BOARD_SPI_CAN_MCP2515) return ESP_ERR_NOT_SUPPORTED;

    spi_bus_config_t bus = {
        .mosi_io_num = sc.mosi,
        .miso_io_num = sc.miso,
        .sclk_io_num = sc.sck,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 32,
    };
    err = spi_bus_initialize(sc.host, &bus, SPI_DMA_DISABLED);
    if (err != ESP_OK) return err;

    spi_device_interface_config_t dev = {
        .mode = 0,
        .clock_speed_hz = SPI_HZ,
        .spics_io_num = sc.cs,
        .queue_size = 1,
    };
    err = spi_bus_add_device(sc.host, &dev, &s_dev);
    if (err != ESP_OK) return err;

    gpio_config_t io = {
        .pin_bit_mask = 1ULL << sc.irq,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    err = gpio_config(&io);
    if (err != ESP_OK) return err;
    s_int_gpio = sc.irq;
    return ESP_OK;
}

int mcp2515_int_level(void)
{
    return gpio_get_level(s_int_gpio);
}

int mcp2515_int_gpio(void)
{
    return s_int_gpio;
}

void mcp2515_reset(void)
{
    uint8_t tx = INS_RESET;
    xfer(&tx, NULL, 1);
    /* The oscillator start-up timer holds the chip for 128 oscillator
     * cycles after reset (8 us at 16 MHz); wait well past it. */
    vTaskDelay(pdMS_TO_TICKS(5));
}

uint8_t mcp2515_read(uint8_t reg)
{
    uint8_t tx[3] = {INS_READ, reg, 0};
    uint8_t rx[3];
    xfer(tx, rx, 3);
    return rx[2];
}

void mcp2515_read_n(uint8_t reg, uint8_t *out, int n)
{
    uint8_t tx[2 + 16] = {INS_READ, reg};
    uint8_t rx[2 + 16];
    if (n > 16) n = 16;
    xfer(tx, rx, 2 + n);
    memcpy(out, rx + 2, n);
}

void mcp2515_write(uint8_t reg, uint8_t val)
{
    uint8_t tx[3] = {INS_WRITE, reg, val};
    xfer(tx, NULL, 3);
}

void mcp2515_modify(uint8_t reg, uint8_t mask, uint8_t val)
{
    uint8_t tx[4] = {INS_BIT_MODIFY, reg, mask, val};
    xfer(tx, NULL, 4);
}

/* A buffer's five header bytes, SIDH SIDL EID8 EID0 DLC, then the data
 * (datasheet registers 3-3 to 3-7 and 4-4 to 4-9). */
void mcp2515_read_rx(int buf, can_frame_t *f)
{
    uint8_t tx[14] = {buf ? INS_READ_RX1 : INS_READ_RX0};
    uint8_t rx[14];
    xfer(tx, rx, 14);
    const uint8_t *h = rx + 1;
    memset(f, 0, sizeof *f);
    if (h[1] & 0x08) {                      /* SIDL.IDE */
        f->ext = 1;
        f->id = ((uint32_t)h[0] << 21) | ((uint32_t)(h[1] >> 5) << 18) |
                ((uint32_t)(h[1] & 0x03) << 16) | ((uint32_t)h[2] << 8) | h[3];
        f->rtr = (h[4] & 0x40) ? 1 : 0;     /* DLC.RTR */
    } else {
        f->id = ((uint32_t)h[0] << 3) | (h[1] >> 5);
        f->rtr = (h[1] & 0x10) ? 1 : 0;     /* SIDL.SRR: standard remote */
    }
    f->dlc = h[4] & 0x0F;
    if (f->dlc > 8) f->dlc = 8;
    if (!f->rtr) memcpy(f->data, h + 5, f->dlc);
}

void mcp2515_send_tx0(const can_frame_t *f)
{
    uint8_t tx[14];
    tx[0] = INS_LOAD_TX0;
    if (f->ext) {
        tx[1] = (uint8_t)(f->id >> 21);
        tx[2] = (uint8_t)((((f->id >> 18) & 0x07) << 5) | 0x08 | ((f->id >> 16) & 0x03));
        tx[3] = (uint8_t)(f->id >> 8);
        tx[4] = (uint8_t)f->id;
    } else {
        tx[1] = (uint8_t)(f->id >> 3);
        tx[2] = (uint8_t)((f->id & 0x07) << 5);
        tx[3] = 0;
        tx[4] = 0;
    }
    uint8_t dlc = f->dlc > 8 ? 8 : f->dlc;
    tx[5] = (uint8_t)((f->rtr ? 0x40 : 0) | dlc);
    memset(tx + 6, 0, 8);
    if (!f->rtr) memcpy(tx + 6, f->data, dlc);
    xfer(tx, NULL, 6 + (f->rtr ? 0 : dlc));
    uint8_t rts = INS_RTS_TX0;
    xfer(&rts, NULL, 1);
}

esp_err_t mcp2515_set_mode(uint8_t mode, int timeout_ms)
{
    mcp2515_modify(MCP_CANCTRL, MCP_MODE_MASK, mode);
    for (int i = 0; i <= timeout_ms * 10; i++) {
        if ((mcp2515_read(MCP_CANSTAT) & MCP_MODE_MASK) == mode) return ESP_OK;
        esp_rom_delay_us(100);
    }
    return ESP_ERR_TIMEOUT;
}
