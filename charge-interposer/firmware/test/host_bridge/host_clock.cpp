#include "host_clock.h"

#include "mcp2515_fake_chip.h"

namespace {
mcpfake::Mcp2515Fake* g_chip = nullptr;
uint8_t g_cs_pin = 0xFF;
uint8_t g_int_pin = 0xFF;
uint8_t g_pins[64];
bool g_cs_low = false;
}  // namespace

namespace hostclock {

uint64_t micros() { return g_chip ? g_chip->micros() : 0; }
uint32_t millis() { return g_chip ? g_chip->millis() : 0; }

void advance(uint64_t us) {
  if (g_chip) g_chip->advance(us);
}

void attach(mcpfake::Mcp2515Fake* chip, uint8_t cs_pin) {
  g_chip = chip;
  g_cs_pin = cs_pin;
  g_cs_low = false;
  for (unsigned i = 0; i < sizeof(g_pins); i++) g_pins[i] = 1;
}

void attachInt(uint8_t int_pin) { g_int_pin = int_pin; }

void detach() {
  g_chip = nullptr;
  g_cs_pin = 0xFF;
  g_int_pin = 0xFF;
}

}  // namespace hostclock

namespace hostpins {

void mode(uint8_t pin, uint8_t m) {
  (void)pin;
  (void)m;
}

uint8_t get(uint8_t pin) {
  // The MCP2515's INT is open-drain and ACTIVE LOW, and level-triggered:
  // it stays asserted while any enabled CANINTF flag is pending, which is
  // exactly what the driver's drain loop relies on.
  if (g_chip && pin == g_int_pin) return g_chip->intAsserted() ? 0 : 1;
  return pin < sizeof(g_pins) ? g_pins[pin] : 1;
}

void set(uint8_t pin, uint8_t level) {
  if (pin < sizeof(g_pins)) g_pins[pin] = level;
  if (!g_chip || pin != g_cs_pin) return;
  // CS is the instruction framing. Guarded against a repeated level
  // because a driver that writes HIGH twice must not look like two
  // instruction boundaries -- and READ RX clears RXnIF on the rising edge
  // (p67 Table 12-1), so a spurious edge would clear a flag the driver
  // had not read.
  const bool want_low = (level == 0);
  if (want_low == g_cs_low) return;
  g_cs_low = want_low;
  if (want_low) {
    g_chip->csLow();
  } else {
    g_chip->csHigh();
  }
}

}  // namespace hostpins

// --------------------------------------------------------------------------
// The process-global chip for SPIClass instances a test cannot reach --
// `main.cpp`'s file-static `mcp_spi` being the one that matters. Declared
// in SPI.h; defined here so the shim stays header-only.
// --------------------------------------------------------------------------
namespace hostspi {

static mcpfake::Mcp2515Fake* g_chip = nullptr;

void attachGlobal(mcpfake::Mcp2515Fake* c) { g_chip = c; }
mcpfake::Mcp2515Fake* global() { return g_chip; }

}  // namespace hostspi
