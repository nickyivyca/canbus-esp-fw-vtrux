// The CanPort contract, tested through the base-class reference.
//
// `can_port.h` documents what each counter means. A port that books a
// frame loss somewhere else still compiles, still passes its own tests,
// and quietly breaks every caller written to the contract -- which is what
// happened: `main.cpp` feeds spec 8.2's `bridge_ok` from
// `vehicle.rxDropped() + charger.rxDropped() + emit_overflow`, so a frame
// the MCP2515 lost to a controller overrun did not clear the flag.
//
// Everything here takes `CanPort&`, never `Mcp2515Port&`. A third port
// cannot then repeat the mistake without failing this file.

#include <cstdio>

#include "mcp2515_fake_chip.h"
#include "port_mcp2515.h"

using namespace interposer;
using mcpfake::Mcp2515Fake;

static int failures = 0;

static void check(bool cond, const char* what) {
  std::printf(cond ? "  ok  %s\n" : "FAIL  %s\n", what);
  if (!cond) failures++;
}

namespace {

const uint8_t CS_PIN = 10;
const uint8_t INT_PIN = 3;

struct Rig {
  Mcp2515Fake chip;
  SPIClass spi;
  Mcp2515Port mcp;

  Rig() : spi(0), mcp(spi, CS_PIN, INT_PIN) {
    hostclock::attach(&chip, CS_PIN);
    spi.attach(&chip);
    mcp.begin(500000);
  }
  ~Rig() { hostclock::detach(); }

  // The port as a caller sees it: through the interface, not the class.
  CanPort& port() { return mcp; }

  void run(uint64_t us, uint64_t step = 100) {
    const uint64_t until = chip.micros() + us;
    while (chip.micros() < until) {
      const uint64_t left = until - chip.micros();
      chip.advance(left < step ? left : step);
      if (chip.micros() >= until) break;
      mcp.service(chip.millis());
    }
  }
};

// The claim under test, stated once and applied to a port by reference.
//
// can_port.h: "rxDropped() -- controller overrun, frames lost".
void checkOverrunContract(CanPort& p, Mcp2515Fake& chip, Rig& rig,
                          const char* who) {
  const uint32_t before = p.rxDropped();
  chip.injectRxOverrun(false);          // RXB0 overran
  rig.run(3000);
  const uint32_t after_first = p.rxDropped();
  std::printf("  --  %s rxDropped %u -> %u after one overrun\n",
              who, before, after_first);
  check(after_first == before + 1,
        "a controller overrun raises rxDropped() by one (can_port.h:52)");

  // The repeat. This is the half that was invisible: the driver stored
  // EFLG including the OVR bits it had just cleared on the chip, so a
  // later overrun with nothing else changed compared equal and was
  // neither counted nor cleared -- and every one after it too.
  chip.injectRxOverrun(false);
  rig.run(3000);
  const uint32_t after_second = p.rxDropped();
  std::printf("  --  %s rxDropped %u -> %u after a SECOND overrun\n",
              who, after_first, after_second);
  check(after_second == after_first + 1,
        "and so does the next one, with nothing else in EFLG changing");

  chip.injectRxOverrun(true);           // the other buffer
  rig.run(3000);
  check(p.rxDropped() == after_second + 1,
        "and an RXB1 overrun counts the same way");
}

}  // namespace

int main() {
  // --- the contract, through the interface ------------------------------
  {
    Rig r;
    checkOverrunContract(r.port(), r.chip, r, "MCP2515");
  }

  // --- a controller overrun is NOT a bus error --------------------------
  //
  // They are different counters in the contract and different conditions
  // on the wire: an overrun means a frame arrived and we were too slow,
  // a bus error means the frame was malformed or the bus is unhealthy.
  // Booking one as the other hides both.
  {
    Rig r;
    const uint32_t be = r.port().busErrors();
    r.chip.injectRxOverrun(false);
    r.run(3000);
    check(r.port().rxDropped() == 1, "the overrun lands in rxDropped()");
    check(r.port().busErrors() == be,
          "...and not in busErrors(), which means something else");
  }

  // --- the flag the whole thing feeds -----------------------------------
  //
  // Spec 8.2: bridge_ok means no RX overflow since the previous status
  // frame, and main.cpp sums the ports' rxDropped() to decide. If a
  // controller overrun does not appear there, a frame the charger sent
  // and the VCU never saw leaves bridge_ok set.
  {
    Rig r;
    r.chip.injectRxOverrun(false);
    r.run(3000);
    check(r.port().rxDropped() > 0,
          "an overrun is visible to bridge_ok's input (spec 8.2)");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_canport_contract: all checks OK\n");
  return 0;
}
