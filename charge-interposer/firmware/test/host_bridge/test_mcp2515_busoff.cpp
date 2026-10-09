// Bus-off on the CHARGER port: the chip's error-mode walk, and
// `Mcp2515Port::busOffEvents()` driven through it.
//
// Until now the MCP2515 fake declared bus-off NOT_MODELLED, which was
// honest but left spec 9.1's bus-off coverage half-done -- the TWAI side
// had it and the charger side did not. `Mcp2515Port::busOffEvents()` had
// never been executed.
//
// THE ERROR SOURCE MATTERS, and it is the reason this could not simply
// reuse the existing no-ACK path. Per ISO 11898 an error-passive
// transmitter stops counting ACKNOWLEDGE errors, so the no-ACK case pins
// TEC at 128 and bus-off is unreachable (the DEFERRED item in the status
// table, and the whole reason the 100 ms age-out exists). A form, bit or
// stuff error (p47 s6.3-6.5) carries no such exception, so TEC climbs by
// 8 per attempt until it exceeds 255 and the chip goes bus-off.
//
// Datasheet, confirmed by text extraction AND the rendered page:
//   p47 s6.7  error-active below 128; error-passive at >= 128;
//             bus-off when TEC EXCEEDS 255; recovery is 128 occurrences
//             of 11 consecutive recessive bits
//   p47 note  recovery happens "without any intervention by the MCU"
//   p48 Fig 6-1  the edge out of Bus-Off returns to ERROR-ACTIVE

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

  void run(uint64_t us, uint64_t step = 200) {
    const uint64_t until = chip.micros() + us;
    while (chip.micros() < until) {
      const uint64_t left = until - chip.micros();
      chip.advance(left < step ? left : step);
      if (chip.micros() >= until) break;
      mcp.service(chip.millis());
    }
    mcp.service(chip.millis());
  }

  void offer(uint32_t id) {
    const uint8_t d[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    mcp.send(id, false, d, 8);
  }

  // Step until `pred` or the budget runs out, recording the extremes on
  // the way. The first version of this test sampled at fixed times I had
  // worked out by hand -- 270 us a frame, 8 TEC an attempt -- and every
  // one of them landed after the whole entry-and-recovery cycle was
  // over, reporting TEC=0 and reading like the model did nothing. The
  // cycle is ~11 ms end to end, so a sample at 40 ms sees the aftermath.
  // Watching for the event beats predicting when it happens.
  struct Peaks {
    uint16_t max_tec = 0;
    uint8_t eflg_seen = 0;       // OR of every EFLG sampled
    bool saw_bus_off = false;
    bool reached = false;
    uint64_t at_us = 0;
  };

  template <typename Pred>
  Peaks until(Pred pred, uint64_t budget_us, uint64_t step_us = 50) {
    Peaks p;
    const uint64_t deadline = chip.micros() + budget_us;
    while (chip.micros() < deadline) {
      chip.advance(step_us);
      mcp.service(chip.millis());
      if (chip.tecInternal() > p.max_tec) p.max_tec = chip.tecInternal();
      p.eflg_seen = (uint8_t)(p.eflg_seen | chip.reg(0x2D));
      if (chip.busOff()) p.saw_bus_off = true;
      if (pred()) {
        p.reached = true;
        p.at_us = chip.micros();
        break;
      }
    }
    return p;
  }
};

}  // namespace

int main() {
  // ----------------------------------------------------------------
  // 1. The error-mode walk, against the CHIP directly.
  //
  // Per the folder's standing rule: a DATASHEET behaviour is asserted
  // against the model itself, not through the driver. The RTS defect is
  // why -- a rule only ever exercised through port_mcp2515.cpp is a rule
  // whose observability the driver's usage pattern gets to choose.
  // ----------------------------------------------------------------
  {
    Rig r;
    r.chip.setBusErrors(true);

    check(!r.chip.busOff(), "starts on the bus");
    check(r.chip.tecInternal() == 0, "...with TEC at 0");

    // THREE frames, not one. Bus-off unloads only the buffer that was
    // transmitting, so with a single frame offered there is nothing left
    // pending and the "no transmission while bus-off" check below cannot
    // fail however the engine behaves -- removing the guard entirely
    // still passed it. With other buffers still loaded and requested,
    // the guard is the only thing stopping them going out.
    r.offer(0x300);
    r.offer(0x3001);
    r.offer(0x3002);
    Rig::Peaks p = r.until([&] { return r.chip.busOff(); }, 60000);
    std::printf("  --  bus-off at t=%llu us; peak TEC=%u, EFLG seen=0x%02X\n",
                (unsigned long long)p.at_us, p.max_tec, p.eflg_seen);

    check(p.reached, "repeated bus errors drive it bus-off (p47 s6.7)");
    check(p.max_tec > 255,
          "...and only once TEC EXCEEDS 255, not at 255");
    check((p.eflg_seen & 0x01) != 0, "EWARN was seen, which trips at 96");
    check((p.eflg_seen & 0x10) != 0, "TXEP was seen, which trips at 128");
    check((p.eflg_seen & 0x20) != 0, "TXBO was seen");
    check(r.chip.busOffEntries() == 1, "counted as exactly one entry");

    // p47 s6.6: nothing is transmitted while bus-off. Checked over a
    // window SHORTER than the recovery time, so the chip is still off the
    // bus throughout -- a longer one would let it recover and start
    // sending again, and the check would fail for the wrong reason.
    const uint32_t wire_before = (uint32_t)r.chip.wire().size();
    r.chip.advance(1000);
    check((uint32_t)r.chip.wire().size() == wire_before,
          "nothing is transmitted while bus-off (p47 s6.6)");
  }

  // ----------------------------------------------------------------
  // 2. Recovery: 128 x 11 recessive bits, with NO driver involvement.
  // ----------------------------------------------------------------
  {
    Rig r;
    r.chip.setBusErrors(true);
    r.offer(0x301);
    // Stop AT the entry, so the window is measured from the instant it
    // actually went bus-off rather than from a time I guessed.
    Rig::Peaks p = r.until([&] { return r.chip.busOff(); }, 60000);
    check(p.reached, "bus-off reached");
    const uint64_t entered_us = r.chip.micros();

    const uint64_t window = r.chip.busOffRecoveryNs() / 1000;   // us
    std::printf("  --  entered at %llu us, recovery window %llu us\n",
                (unsigned long long)entered_us, (unsigned long long)window);
    check(window == 2816,
          "128 x 11 bits at 500 kbit is 2816 us (p47 s6.7, p48 Fig 6-1)");

    // Just before the window closes it must still be bus-off. Advancing
    // the CHIP directly, with no service() calls, also demonstrates the
    // driver plays no part in the recovery.
    r.chip.advance(window - 100);
    check(r.chip.busOff(), "still bus-off just before the window closes");

    // ...and just after, it is back -- without the driver doing anything.
    r.chip.advance(200);
    check(!r.chip.busOff(),
          "recovers on its own, no MCU intervention (p47 note box)");
    check(r.chip.tecInternal() == 0,
          "...returning to ERROR-ACTIVE with TEC cleared, not merely to "
          "error-passive (p48 Fig 6-1)");
    check((r.chip.reg(0x2D) & 0x20) == 0, "...and TXBO is clear");
  }

  // ----------------------------------------------------------------
  // 3. Mcp2515Port::busOffEvents() -- through the DRIVER this time.
  //
  // The driver edge-detects EFLG.TXBO. It never initiates recovery,
  // because this chip does not need it to -- which is a real difference
  // from the TWAI port and worth having a test say out loud.
  // ----------------------------------------------------------------
  {
    Rig r;
    CanPort& port = r.mcp;              // through the interface

    check(port.busOffEvents() == 0, "a healthy port reports no bus-off");
    check(!port.busOff(), "...and is not bus-off");

    r.chip.setBusErrors(true);
    r.offer(0x302);
    // Stop AT the entry. Running a fixed 250 ms here was wrong: the chip
    // enters bus-off at ~19 ms and recovers 2.8 ms later, so the sample
    // landed long after the flag had cleared and `busOff()` read false
    // while `busOffEvents()` had correctly recorded the event.
    Rig::Peaks p = r.until([&] { return port.busOff(); }, 60000);
    check(p.reached, "the port sees bus-off");
    check(port.busOffEvents() == 1, "...and counts one event");

    // Polling repeatedly must not re-count it. Kept inside the 2816 us
    // recovery window so the chip really is still bus-off throughout.
    for (int i = 0; i < 10; i++) {
      r.chip.advance(100);
      r.mcp.service(r.chip.millis());
    }
    check(r.chip.busOff(), "still bus-off for this stretch (precondition)");
    check(port.busOffEvents() == 1,
          "further polls while still bus-off add no events");

    // Recover, then drive it bus-off a second time.
    r.chip.setBusErrors(false);
    r.run(5000);
    check(!port.busOff(), "the port sees the recovery");
    check(port.busOffEvents() == 1, "...which is not itself an event");

    r.chip.setBusErrors(true);
    r.offer(0x303);
    r.until([&] { return port.busOffEvents() == 2; }, 60000);
    check(port.busOffEvents() == 2, "a second bus-off is a second event");
    std::printf("  --  chip entries=%u, port events=%u\n",
                r.chip.busOffEntries(), port.busOffEvents());
    check(r.chip.busOffEntries() == port.busOffEvents(),
          "the port's count agrees with the chip's own");
  }

  // ----------------------------------------------------------------
  // 4. The no-ACK path still cannot reach bus-off.
  //
  // The DEFERRED reading, and the guard against this new error source
  // quietly changing it. If no-ACK started reaching bus-off, the 100 ms
  // age-out's premise would change and spec 8.2's bridge_ok would clear
  // on every drive -- so the two sources must stay distinct.
  // ----------------------------------------------------------------
  {
    Rig r;
    r.chip.setAcknowledged(false);      // NOT setBusErrors
    r.offer(0x304);
    r.run(500000);
    std::printf("  --  no-ACK for 500 ms: TEC=%u busOff=%d\n",
                r.chip.tecInternal(), r.chip.busOff() ? 1 : 0);
    check(r.chip.tecInternal() == 128,
          "a no-ACK transmitter still pins at the error-passive limit");
    check(!r.chip.busOff(),
          "...and still never reaches bus-off (DEFERRED, p47 s6.7 -> ISO)");
    check(r.mcp.busOffEvents() == 0, "...so the port counts no event");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_mcp2515_busoff: all checks OK\n");
  return 0;
}
