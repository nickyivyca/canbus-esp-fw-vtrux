// The x2 board's answers for the L2 host tests (board.h in this folder), as
// shared/boards SPEC section 3 gives them, not as board_pins.h spells them:
// the harness tests the spec.
//
//   port 0: the S3's TWAI, TX GPIO7, RX GPIO6
//   port 1: an MCP2515 on SPI3 (SPI3_HOST), CS GPIO10, SCK GPIO12,
//           MISO GPIO13, MOSI GPIO11, INT GPIO3 (active low), 16 MHz crystal
//   neither port has a standby line ("not supported")
//   offered: LED1 on GPIO2; hardware name "ESP32-CAN-X2"
//
// Return values as board.h states them: ESP_ERR_INVALID_ARG for a port the
// board does not have or a NULL out, ESP_ERR_NOT_SUPPORTED for a port with
// no such thing.

#include "board.h"

extern "C" {

int board_can_ports(void) { return 2; }

board_can_kind_t board_can_kind(int port) {
  if (port == 0) return BOARD_CAN_TWAI;
  if (port == 1) return BOARD_CAN_SPI;
  return BOARD_CAN_NONE;
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out) {
  if (out == nullptr || port < 0 || port > 1) return ESP_ERR_INVALID_ARG;
  if (port != 0) return ESP_ERR_NOT_SUPPORTED;
  out->tx = 7;
  out->rx = 6;
  return ESP_OK;
}

esp_err_t board_spi_can(int port, board_spi_can_t *out) {
  if (out == nullptr || port < 0 || port > 1) return ESP_ERR_INVALID_ARG;
  if (port != 1) return ESP_ERR_NOT_SUPPORTED;
  out->controller = BOARD_SPI_CAN_MCP2515;
  out->host = SPI3_HOST;
  out->cs = 10;
  out->sck = 12;
  out->miso = 13;
  out->mosi = 11;
  out->irq = 3;
  out->crystal_hz = 16000000UL;
  return ESP_OK;
}

esp_err_t board_can_init(int port) {
  if (port < 0 || port > 1) return ESP_ERR_INVALID_ARG;
  return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_can_standby(int port, bool on) {
  (void)on;
  if (port < 0 || port > 1) return ESP_ERR_INVALID_ARG;
  return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_led(board_led_t led, bool on) {
  (void)on;
  return led == BOARD_LED_LED1 ? ESP_OK : ESP_ERR_INVALID_ARG;
}

const char *board_hw_name(void) { return "ESP32-CAN-X2"; }

}  // extern "C"
