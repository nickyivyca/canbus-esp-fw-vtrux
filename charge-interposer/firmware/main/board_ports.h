// The X2's two CAN ports as this firmware wires them, asked of the board
// layer (shared/boards SPEC B5, section 3) rather than spelled out as pin
// constants. Every *_main.cpp takes its pins from here.
//
//   vehicle side: board port 0, the S3's TWAI          -> vtrux/twai_port
//                 (vtrux/twai_port takes these pins from the board itself,
//                 B5; vehiclePins() is the firmware's check that the board
//                 has the port)
//   charger side: board port 1, the MCP2515 on SPI3    -> Mcp2515Port
//
// The values are the board's, and today the same as the constants they
// replace. What this firmware still fixes for itself, it checks against the
// board: the charger side runs on SPIClass(HSPI), which is SPI3_HOST on the
// S3, and Mcp2515Port's bit-timing table is written for a 16 MHz crystal. A
// board whose answer does not match makes that side's ok false, and the
// caller treats the port as not started, never as started on other pins.

#ifndef INTERPOSER_BOARD_PORTS_H
#define INTERPOSER_BOARD_PORTS_H

#include <stdint.h>

#include "board.h"

namespace interposer {

static const int BOARD_PORT_VEHICLE = 0;
static const int BOARD_PORT_CHARGER = 1;

struct VehiclePins {
  bool ok;
  int tx;
  int rx;
};

struct ChargerPins {
  bool ok;
  uint8_t cs;
  uint8_t sck;
  uint8_t miso;
  uint8_t mosi;
  uint8_t irq;  // active low
};

inline VehiclePins vehiclePins() {
  board_twai_pins_t p = {};
  const bool ok = board_twai_pins(BOARD_PORT_VEHICLE, &p) == ESP_OK;
  return VehiclePins{ok, ok ? (int)p.tx : -1, ok ? (int)p.rx : -1};
}

inline ChargerPins chargerPins() {
  board_spi_can_t s = {};
  const bool ok = board_spi_can(BOARD_PORT_CHARGER, &s) == ESP_OK &&
                  s.controller == BOARD_SPI_CAN_MCP2515 &&
                  s.host == SPI3_HOST &&  // SPIClass(HSPI) on the S3
                  s.crystal_hz == 16000000UL;  // Mcp2515Port::setBitrate()
  if (!ok) return ChargerPins{false, 0, 0, 0, 0, 0};
  return ChargerPins{true, (uint8_t)s.cs, (uint8_t)s.sck, (uint8_t)s.miso,
                     (uint8_t)s.mosi, (uint8_t)s.irq};
}

}  // namespace interposer

#endif  // INTERPOSER_BOARD_PORTS_H
