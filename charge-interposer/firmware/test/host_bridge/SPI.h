// The SPI surface `port_mcp2515.cpp` uses, routed to the chip model.
//
// Found via -I so the driver's own `#include <SPI.h>` resolves here. The
// driver under test is the file that ships.
//
// transfer() goes straight to the model, which advances the clock by the
// byte's time on the wire. That is load-bearing: without it the window
// inside drainTx() between readStatus() and the readReg() after it is
// zero-width and the race it contains can never be reached.

#pragma once

#include <stdint.h>

#include "host_clock.h"
#include "mcp2515_fake_chip.h"

class SPISettings {
 public:
  SPISettings() {}
  SPISettings(uint32_t hz, uint8_t order, uint8_t mode)
      : hz_(hz), order_(order), mode_(mode) {}
  uint32_t hz_ = 1000000;
  uint8_t order_ = 1;
  uint8_t mode_ = 0;
};

// A process-global chip, for the case where the SPIClass instance is out
// of the test's reach.
//
// `main.cpp` owns its `SPIClass mcp_spi` as a FILE STATIC, so the L2
// harness -- which compiles main.cpp and calls its setup()/loop() -- has
// no way to call attach() on it. Rather than make the firmware expose the
// object purely for testing, the shim falls back to a globally attached
// model. Per-instance attach still wins where a test has the object.
namespace hostspi {
void attachGlobal(mcpfake::Mcp2515Fake* c);
mcpfake::Mcp2515Fake* global();
}  // namespace hostspi

class SPIClass {
 public:
  explicit SPIClass(int bus = 0) { (void)bus; }

  void begin(int sck = -1, int miso = -1, int mosi = -1, int ss = -1) {
    (void)sck; (void)miso; (void)mosi; (void)ss;
  }
  void beginTransaction(const SPISettings& s) { (void)s; }
  void endTransaction() {}

  uint8_t transfer(uint8_t b) {
    mcpfake::Mcp2515Fake* c = chip_ ? chip_ : hostspi::global();
    return c ? c->transfer(b) : 0xFF;
  }

  // Test wiring, not Arduino.
  void attach(mcpfake::Mcp2515Fake* c) { chip_ = c; }

 private:
  mcpfake::Mcp2515Fake* chip_ = nullptr;
};
