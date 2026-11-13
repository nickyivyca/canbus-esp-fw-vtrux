/*
 * mcp2515_reset.c -- the MCP2515's reset into configuration mode
 * (mcp2515_reset.h). TEST EQUIPMENT, NOT FOR VEHICLE (x2_test_images SPEC
 * T8). From the MCP2515 datasheet (DS20001801), table 12-1 for the SPI
 * instructions and register 10-2 for CANSTAT; ported from the PlatformIO
 * jammer's mcp_reset(), the same 1 MHz SPI mode 0, 10 ms wait and reads.
 *
 * The MCP2515's reset is not tied to the S3's, so without this it could
 * still be in whatever mode the previous image left it.
 */
#include "mcp2515_reset.h"

#include <string.h>

#include "board.h"
#include "driver/spi_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define INS_RESET   0xC0
#define INS_READ    0x03
#define REG_CANSTAT 0x0E
#define REG_CANCTRL 0x0F
#define SPI_HZ      1000000

static esp_err_t xfer(spi_device_handle_t dev, const uint8_t *tx, uint8_t *rx,
                      int n)
{
    spi_transaction_t t;
    memset(&t, 0, sizeof t);
    t.length = 8 * n;
    t.tx_buffer = tx;
    t.rx_buffer = rx;
    return spi_device_polling_transmit(dev, &t);
}

static esp_err_t read_reg(spi_device_handle_t dev, uint8_t reg, uint8_t *v)
{
    uint8_t tx[3] = {INS_READ, reg, 0x00};
    uint8_t rx[3] = {0, 0, 0};
    esp_err_t err = xfer(dev, tx, rx, 3);
    *v = rx[2];
    return err;
}

esp_err_t mcp2515_reset_config(int port, uint8_t *canstat, uint8_t *canctrl)
{
    board_spi_can_t sc;
    esp_err_t err = board_spi_can(port, &sc);
    if (err != ESP_OK) return err;
    if (sc.controller != BOARD_SPI_CAN_MCP2515) return ESP_ERR_NOT_SUPPORTED;

    spi_bus_config_t bus;
    memset(&bus, 0, sizeof bus);
    bus.mosi_io_num = sc.mosi;
    bus.miso_io_num = sc.miso;
    bus.sclk_io_num = sc.sck;
    bus.quadwp_io_num = -1;
    bus.quadhd_io_num = -1;
    bus.max_transfer_sz = 16;
    err = spi_bus_initialize(sc.host, &bus, SPI_DMA_DISABLED);
    if (err != ESP_OK) return err;

    spi_device_interface_config_t devcfg;
    memset(&devcfg, 0, sizeof devcfg);
    devcfg.mode = 0;
    devcfg.clock_speed_hz = SPI_HZ;
    devcfg.spics_io_num = sc.cs;
    devcfg.queue_size = 1;
    spi_device_handle_t dev;
    err = spi_bus_add_device(sc.host, &devcfg, &dev);
    if (err != ESP_OK) return err;

    const uint8_t reset = INS_RESET;
    err = xfer(dev, &reset, NULL, 1);
    if (err != ESP_OK) return err;
    vTaskDelay(pdMS_TO_TICKS(10));
    err = read_reg(dev, REG_CANSTAT, canstat);
    if (err != ESP_OK) return err;
    return read_reg(dev, REG_CANCTRL, canctrl);
}
