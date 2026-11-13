/*
 * test_mcp2515_reset.c -- the MCP2515's reset into configuration mode
 * (main/mcp2515_reset.c) on the host fakes. TEST EQUIPMENT, NOT FOR VEHICLE
 * (x2_test_images SPEC T8: at boot it resets the MCP2515 into configuration
 * mode, where it neither transmits nor ACKs, and reports CANSTAT).
 *
 * The SPI instructions are the MCP2515 datasheet's (DS20001801, table
 * 12-1). Only RESET (0xC0) and READ (0x03) may ever go out: WRITE (0x02),
 * BIT MODIFY (0x05), LOAD TX BUFFER (0x40-0x45), RTS (0x80-0x87), READ RX
 * BUFFER (0x90-0x96), READ STATUS (0xA0) and RX STATUS (0xB0) never.
 */
#include <string.h>

#include "check.h"
#include "fake_idf.h"
#include "mcp2515_reset.h"

static int allowed(int i)
{
    if (fake_spi_tx[i][0] == 0xC0) return fake_spi_len[i] == 1;
    if (fake_spi_tx[i][0] == 0x03) return fake_spi_len[i] == 3;
    return 0;
}

int main(void)
{
    uint8_t st = 0x55, ct = 0x55;

    fake_reset();
    CHECK(mcp2515_reset_config(1, &st, &ct) == ESP_OK, "reset on port 1");
    CHECK(st == 0x80 && ct == 0x87, "read back canstat 0x%02X canctrl 0x%02X",
          st, ct);
    CHECK(fake_spi_n == 3, "%d SPI transactions, not 3", fake_spi_n);
    for (int i = 0; i < fake_spi_n; i++)
        CHECK(allowed(i), "transaction %d sends 0x%02X (%d bytes)", i,
              fake_spi_tx[i][0], fake_spi_len[i]);
    CHECK(fake_spi_tx[0][0] == 0xC0, "the first instruction is not RESET");
    CHECK(fake_spi_tx[1][0] == 0x03 && fake_spi_tx[1][1] == 0x0E,
          "CANSTAT not read second");
    CHECK(fake_spi_tx[2][0] == 0x03 && fake_spi_tx[2][1] == 0x0F,
          "CANCTRL not read third");
    CHECK(fake_spi_n >= 2 && fake_spi_t[1] - fake_spi_t[0] >= 10000,
          "only %lld us between RESET and the first read",
          (long long)(fake_spi_t[1] - fake_spi_t[0]));
    CHECK(fake_spi_clock_hz == 1000000 && fake_spi_mode == 0,
          "SPI %d Hz mode %d", fake_spi_clock_hz, fake_spi_mode);
    CHECK(fake_log_find("spi_bus_initialize 2 sck=12 miso=13 mosi=11", 0) >= 0,
          "the board's SPI3 pins not used");
    CHECK(fake_log_find("spi_add_device 2 cs=10", 0) >= 0,
          "the board's chip select not used");

    /* A chip that stays out of configuration mode is reported as it is. */
    fake_reset();
    fake_mcp_canstat_after_reset = 0x00;
    CHECK(mcp2515_reset_config(1, &st, &ct) == ESP_OK, "reset");
    CHECK(st == 0x00, "canstat reported 0x%02X, not the chip's 0x00", st);
    CHECK((st & MCP2515_OPMOD_MASK) != MCP2515_OPMOD_CONFIG, "mask");

    /* A port that is not the SPI CAN port, or a bus that does not start:
     * an error and nothing sent. */
    fake_reset();
    CHECK(mcp2515_reset_config(0, &st, &ct) != ESP_OK, "port 0 accepted");
    CHECK(fake_spi_n == 0, "port 0: %d transactions", fake_spi_n);
    fake_reset();
    fake_spi_bus_init_result = ESP_FAIL;
    CHECK(mcp2515_reset_config(1, &st, &ct) != ESP_OK, "bus failure hidden");
    CHECK(fake_spi_n == 0, "bus failure: %d transactions", fake_spi_n);

    return check_end("test_mcp2515_reset");
}
