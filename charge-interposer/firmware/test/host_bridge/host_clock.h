// The virtual clock, and the wiring that joins the Arduino shim to the
// chip model.
//
// ONE counter serves micros() and millis(), so the two can never disagree
// about when something happened -- which matters here because the abort
// bound is 366 us and the age-out is 100 ms, and a test has to reason
// about both in the same run.
//
// Nothing advances time by itself. The test grants it, and delay() /
// delayMicroseconds() / an SPI byte spend it. A polling loop in the driver
// that never gets a grant will spin forever rather than quietly pass,
// which is the behaviour we want: it is a real hang, and it should look
// like one.

#pragma once

#include <stdint.h>

namespace mcpfake {
class Mcp2515Fake;
}

namespace hostclock {

uint64_t micros();
uint32_t millis();

// Advance, driving the attached chip model as time passes so a frame
// finishing mid-advance is seen at the right moment.
void advance(uint64_t us);

// The model that owns the clock. Set once per test.
void attach(mcpfake::Mcp2515Fake* chip, uint8_t cs_pin);

// Tell the pin layer which GPIO is the MCP2515's INT line, so a read of
// it reflects the model's pending interrupts. Without this the pin reads
// HIGH forever and the driver's RX drain loop never executes -- which is
// how the receive path went untested until the L2 harness needed it.
void attachInt(uint8_t int_pin);
void detach();

}  // namespace hostclock
