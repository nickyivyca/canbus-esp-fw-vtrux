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
//
// SINCE PORT STEP 2B, THE VEHICLE PORT TOO (tester, 2026-11-09; the
// charge-interposer reviewer's ruling on the 2b fit). The vehicle port is
// vtrux/twai_port behind vtrux/can_port, reached as the bridge reaches it:
// a PortView for its index, through `CanPort&`, on the shared TWAI driver
// fake. Spec 2.4 says which of the port's counters (twai_port T7) feeds
// which of the bridge's: the RX overflow count includes the frames lost at
// the restart after automatic bus-off recovery (D3); the vehicle bus-error
// count is the driver's, one per error (D1); the send-failure count is the
// sends refused for a full queue plus those refused for any other reason,
// the port down included (D2). Each is judged against what the test did to
// the port (frames delivered and taken, sends the caller saw refused,
// errors injected), never against the port's own figure; the T7 parts are
// read only as the premise that the loss happened the way the case means.
// Its can-fail is ../../harness_checks/l2_mutations.sh's Q-X, one mutant
// per line of port_view.h's mapping.

#include <cstdio>
#include <cstring>

#include "host_clock.h"
#include "mcp2515_fake_chip.h"
#include "port_mcp2515.h"
#include "port_view.h"
#include "twai_port.h"      // the vehicle port's own counters (T7), premises only
#include "twai_fake.h"      // the shared fake's C++ interface (cpp/)
#include "vehicle_port.h"

using namespace interposer;
using mcpfake::Mcp2515Fake;
using twaifake::fake;

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

// ---- the vehicle port (port step 2b, spec 2.4) ---------------------------

can_port_status_t vstatus() {
  can_port_status_t s;
  std::memset(&s, 0, sizeof(s));
  if (twai_port_status(&s) != ESP_OK) std::memset(&s, 0xFF, sizeof(s));
  return s;
}

// The frames the caller can take, all of them.
uint32_t drain(CanPort& p) {
  uint32_t n = 0;
  RxFrame f;
  p.service(hostclock::millis());
  while (p.receive(f)) {
    n++;
    p.service(hostclock::millis());
  }
  return n;
}

void deliverN(uint32_t n, uint8_t tag) {
  const uint8_t d[8] = {tag, 0, 0, 0, 0, 0, 0, 0};
  for (uint32_t i = 0; i < n; i++) fake().deliver(0x18FF4BE5, true, d, 8);
}

// RX: each way a frame is lost on receive raises rxDropped() by the frames
// lost. `lost` is counted by the test: delivered minus taken.
void judgeRxLoss(uint32_t lost, uint32_t before, uint32_t after,
                 const char* premise_ok, bool premise, const char* what) {
  std::printf("  --  vehicle: frames lost %u; rxDropped %u -> %u\n", lost,
              before, after);
  check(premise, premise_ok);
  check(lost >= 1 && after - before == lost, what);
}

void vehicleContract() {
  Mcp2515Fake chip;                 // the clock's owner; no charger here
  hostclock::attach(&chip, CS_PIN);
  fake().hardReset();               // no airtime, auto-flush, recovery by test
  check(vehicleSetup(CAN_PORT_MODE_NORMAL),
        "setup: the vehicle port is set up and registered with can_port");
  PortView view(VEHICLE_PORT);
  CanPort& p = view;
  const uint8_t d[8] = {0x24, 0, 0, 0, 0, 0, 0, 0};

  // D2, refused for a reason other than a full queue: the port down.
  {
    const can_port_status_t s0 = vstatus();
    const uint32_t t0 = p.txDropped();
    uint32_t refused = 0;
    for (int i = 0; i < 3; i++)
      if (!p.send(0x18FF00E5, true, d, 8)) refused++;
    const can_port_status_t s1 = vstatus();
    const uint32_t t1 = p.txDropped();
    std::printf("  --  vehicle, port down: sends refused %u; tx_refused +%u, "
                "tx_dropped +%u; txDropped %u -> %u\n", refused,
                s1.tx_refused - s0.tx_refused, s1.tx_dropped - s0.tx_dropped,
                t0, t1);
    check(refused == 3 && s1.tx_refused - s0.tx_refused == 3 &&
              s1.tx_dropped == s0.tx_dropped,
          "setup: every send with the port down was refused, and the port "
          "booked them as refused, not as a full queue");
    check(t1 - t0 == refused,
          "spec 2.4 (D2): a send refused with the port down raises "
          "txDropped() by one each");
  }

  check(p.begin(500000), "setup: the vehicle port comes up at 500 kbit/s");
  check(fake().state() == TWAI_STATE_RUNNING,
        "setup: the vehicle controller is RUNNING");

  // D2, refused for a full queue: nothing leaves the queue (auto-flush held),
  // so sends past its depth are refused.
  {
    fake().setAutoFlush(false);
    const can_port_status_t s0 = vstatus();
    const uint32_t t0 = p.txDropped();
    uint32_t refused = 0;
    for (uint32_t i = 0; i < VEHICLE_TX_QUEUE + 8; i++)
      if (!p.send(0x18FF00E5, true, d, 8)) refused++;
    const can_port_status_t s1 = vstatus();
    const uint32_t t1 = p.txDropped();
    std::printf("  --  vehicle, TX queue held: sends refused %u; tx_dropped "
                "+%u, tx_refused +%u; txDropped %u -> %u\n", refused,
                s1.tx_dropped - s0.tx_dropped, s1.tx_refused - s0.tx_refused,
                t0, t1);
    check(refused >= 1 && s1.tx_dropped - s0.tx_dropped == refused &&
              s1.tx_refused == s0.tx_refused,
          "setup: sends past the TX queue's depth were refused, and the port "
          "booked them as a full queue");
    check(t1 - t0 == refused,
          "spec 2.4 (D2): a send refused for a full queue raises txDropped() "
          "by one each");
    fake().setAutoFlush(true);
    fake().flushTx();
    p.service(hostclock::millis());
  }

  drain(p);

  // RX, the receive queue full: more frames than its depth, none taken.
  {
    const can_port_status_t s0 = vstatus();
    const uint32_t r0 = p.rxDropped();
    const uint32_t n = VEHICLE_RX_QUEUE + 6;
    deliverN(n, 0x51);
    const uint32_t got = drain(p);
    const can_port_status_t s1 = vstatus();
    const uint32_t lost = n - got;
    judgeRxLoss(lost, r0, p.rxDropped(),
                "setup: the frames past the receive queue's depth were lost "
                "to the full queue (rx_lost_queue_full), and to nothing else",
                s1.rx_lost_queue_full - s0.rx_lost_queue_full == lost &&
                    s1.rx_lost_overrun == s0.rx_lost_overrun &&
                    s1.rx_lost_restart == s0.rx_lost_restart,
                "spec 2.4 / 8.2: frames lost to the full receive queue raise "
                "rxDropped() by as many");
  }

  // RX, the controller's FIFO overran.
  {
    const can_port_status_t s0 = vstatus();
    const uint32_t r0 = p.rxDropped();
    const uint32_t n = 3;
    fake().injectRxFifoOverrun(n);
    drain(p);
    const can_port_status_t s1 = vstatus();
    judgeRxLoss(n, r0, p.rxDropped(),
                "setup: the port booked the overrun's frames as a FIFO "
                "overrun (rx_lost_overrun), and nothing else",
                s1.rx_lost_overrun - s0.rx_lost_overrun == n &&
                    s1.rx_lost_queue_full == s0.rx_lost_queue_full &&
                    s1.rx_lost_restart == s0.rx_lost_restart,
                "spec 2.4 / 8.2: frames lost to a FIFO overrun raise "
                "rxDropped() by as many");
  }

  // D1: the driver's bus-error count, one per error, not an RX loss.
  {
    const can_port_status_t s0 = vstatus();
    const uint32_t b0 = p.busErrors();
    const uint32_t r0 = p.rxDropped();
    const uint32_t n = 4;
    fake().injectBusError(n);
    p.service(hostclock::millis());
    const can_port_status_t s1 = vstatus();
    const uint32_t b1 = p.busErrors();
    std::printf("  --  vehicle: bus errors injected %u; bus_errors +%u; "
                "busErrors %u -> %u\n", n, s1.bus_errors - s0.bus_errors, b0,
                b1);
    check(s1.bus_errors - s0.bus_errors == n && !s1.bus_off,
          "setup: the port counted the injected bus errors and is not "
          "bus-off");
    check(b1 - b0 == n,
          "spec 2.4 (D1): busErrors() rises by one per bus error (the "
          "driver's count)");
    check(p.rxDropped() == r0, "...and a bus error is not an RX loss");
  }

  // D3 and bus-off: frames waiting in the receive queue when the controller
  // goes bus-off, then automatic recovery, which restarts it and resets the
  // queue. The port's service is called with the frames still waiting, as
  // test_l2_bridge's rx-lost-at-recovery does: what is judged is the
  // mapping, not whether the bridge's loop can reach this interleaving.
  {
    drain(p);
    const can_port_status_t s0 = vstatus();
    const uint32_t r0 = p.rxDropped();
    const uint32_t e0 = p.busOffEvents();
    const bool off_before = p.busOff();
    const uint32_t n = 16;
    deliverN(n, 0xD3);
    fake().enterBusOff();
    p.service(hostclock::millis());   // sees bus-off, starts the recovery
    const bool off_during = p.busOff();
    const uint32_t e_during = p.busOffEvents();
    fake().completeRecovery();
    p.service(hostclock::millis());   // ...and restarts the controller
    const uint32_t got = drain(p);
    const can_port_status_t s1 = vstatus();
    const bool running = fake().state() == TWAI_STATE_RUNNING;
    const bool off_after = p.busOff();
    const uint32_t e1 = p.busOffEvents();
    const uint32_t lost = n - got;
    std::printf("  --  vehicle bus-off: busOff %d/%d/%d (before/during/after), "
                "busOffEvents %u -> %u -> %u; running again %d\n",
                off_before, off_during, off_after, e0, e_during, e1, running);
    judgeRxLoss(lost, r0, p.rxDropped(),
                "setup: the frames waiting at the bus-off were lost at the "
                "restart after recovery (rx_lost_restart), and to nothing else",
                running && s1.rx_lost_restart - s0.rx_lost_restart == lost &&
                    s1.rx_lost_queue_full == s0.rx_lost_queue_full &&
                    s1.rx_lost_overrun == s0.rx_lost_overrun,
                "spec 2.4 (D3) / 8.2: frames lost at the restart after bus-off "
                "recovery raise rxDropped() by as many");
    check(!off_before && off_during,
          "busOff() is set while the controller is bus-off, and not before");
    check(!off_after, "busOff() is clear once the controller has recovered");
    check(e_during - e0 == 1 && e1 == e_during,
          "busOffEvents() rises by one for the bus-off episode");

    // A second episode, with nothing waiting: one more.
    fake().enterBusOff();
    p.service(hostclock::millis());
    fake().completeRecovery();
    p.service(hostclock::millis());
    const uint32_t e2 = p.busOffEvents();
    std::printf("  --  vehicle: busOffEvents %u after a second episode\n", e2);
    check(e2 - e1 == 1 && !p.busOff(),
          "busOffEvents() rises by one per bus-off episode (a second one)");
  }
  hostclock::detach();
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

  // --- the vehicle port, through the interface (port step 2b) -----------
  vehicleContract();

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_canport_contract: all checks OK\n");
  return 0;
}
