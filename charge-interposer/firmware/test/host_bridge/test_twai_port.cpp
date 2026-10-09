// TwaiPort against the fake driver -- the first executed test of
// `rxDropped()` and `busOffEvents()` on this port.
//
// Both counters shipped untested. `busOffEvents()` was added to the
// `CanPort` interface with nothing calling it, and the rxDropped()
// delta-accumulation fix of 2026-10-04 was compile-verified only. The
// reviewer found that one by reading: the previous form took the MAXIMUM
// of the controller's cumulative count, which silently swallows every
// loss after a driver reinstall until the new count overtakes the old
// peak. Nothing could have caught it, because nothing ran it.
//
// The reinstall case is the whole reason the fix exists, so it is the
// first test here, and it is written so that the OLD logic demonstrably
// fails it -- see the comment on the arithmetic.

#include <cstdio>
#include <cstring>

#include "port_twai.h"
#include "twai_fake.h"

using namespace interposer;
using twaifake::fake;

static int failures = 0;

static void check(bool cond, const char* what) {
  std::printf(cond ? "  ok  %s\n" : "FAIL  %s\n", what);
  if (!cond) failures++;
}

namespace {

const int TX_GPIO = 6;
const int RX_GPIO = 7;

// Deliver n frames, which the port has not drained, so the RX queue fills
// and the rest are booked as missed by the REAL mechanism rather than an
// injected number.
void flood(uint32_t n) {
  const uint8_t payload[8] = {1, 2, 3, 4, 5, 6, 7, 8};
  for (uint32_t i = 0; i < n; i++)
    fake().deliver(0x300 + i, false, payload, 8);
}

void drainAll(TwaiPort& p) {
  RxFrame f;
  while (p.receive(f)) {
  }
}

}  // namespace

int main() {
  // ----------------------------------------------------------------
  // 1. The reinstall case: losses after a fresh install must still count.
  // ----------------------------------------------------------------
  {
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, /*rx_queue=*/4, /*tx_queue=*/4);
    check(p.begin(500000), "driver installs and starts");
    check(fake().state() == TWAI_STATE_RUNNING, "...and is RUNNING");
    check(fake().configuredRxQueueLen() == 4,
          "...with the RX queue sized from the port's config, not a default");

    // Overflow the queue: 4 fit, the rest are missed.
    flood(14);
    p.service(10);
    const uint32_t after_first = p.rxDropped();
    std::printf("  --  before reinstall: controller missed=%u, "
                "port rxDropped=%u\n", fake().rxMissed(), after_first);
    check(after_first == 10, "10 frames over a 4-deep queue are 10 losses");

    drainAll(p);
    p.end();
    check(!fake().installed(), "end() uninstalls the driver");

    // Reinstall. The controller's cumulative counters restart at zero.
    check(p.begin(500000), "the port reinstalls");
    check(fake().rxMissed() == 0,
          "a fresh install zeroes the controller's cumulative count");
    p.service(20);
    check(p.rxDropped() == after_first,
          "the reinstall itself loses nothing, and un-counts nothing");

    // THE CASE THE FIX EXISTS FOR.
    // Controller count is now 3 while the port last saw 10. The old form,
    // `if (raw > rx_dropped_) rx_dropped_ = raw`, compares 3 against 10
    // and books NOTHING -- and keeps booking nothing until the fresh
    // count passes 10. The delta form books 3.
    flood(7);
    p.service(30);
    std::printf("  --  after reinstall: controller missed=%u, "
                "port rxDropped=%u\n", fake().rxMissed(), p.rxDropped());
    check(fake().rxMissed() == 3, "3 more frames are lost post-reinstall");
    check(p.rxDropped() == after_first + 3,
          "...and the port counts them. The max-form would have booked 0 "
          "here, which is the defect");

    check(fake().invalidStateCalls() == 0,
          "no call was made in a state the real driver would reject");
    drainAll(p);
    p.end();
  }

  // ----------------------------------------------------------------
  // 2. A controller FIFO overrun is a lost frame too.
  // ----------------------------------------------------------------
  {
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    p.service(10);
    const uint32_t before = p.rxDropped();
    fake().injectRxFifoOverrun(2);
    p.service(20);
    check(p.rxDropped() == before + 2,
          "rx_overrun_count lands in rxDropped() as well as rx_missed_count "
          "(can_port.h: frames lost, however lost)");
    const uint32_t be = p.busErrors();
    check(be == 0, "...and an overrun is NOT booked as a bus error");
    p.end();
  }

  // ----------------------------------------------------------------
  // 3. busOffEvents(): an EVENT, not a poll count, and recovery works.
  // ----------------------------------------------------------------
  {
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    p.service(10);
    check(p.busOffEvents() == 0, "a healthy port reports no bus-off events");

    fake().enterBusOff();
    p.service(20);
    check(p.busOffEvents() == 1, "entering bus-off counts one event");
    check(fake().state() == TWAI_STATE_RECOVERING,
          "...and the port initiated recovery rather than sitting bus-off "
          "-- a bridge that stays bus-off has partitioned the bus");

    // THE COUNTER MUST NOT TICK PER POLL. If it did, bus_off_events_ would
    // measure how often service() ran, and spec 8.2's bridge_ok would be
    // clear forever after a single transient.
    for (int i = 0; i < 25; i++) p.service(30 + i);
    check(p.busOffEvents() == 1,
          "25 further polls while still recovering add no events");

    // 128 bus-free occurrences: recovery ends in STOPPED, and the port's
    // service() is what brings it back to RUNNING.
    fake().completeRecovery();
    check(fake().state() == TWAI_STATE_STOPPED,
          "recovery ends in STOPPED, per the ESP-IDF header");
    p.service(100);
    check(fake().state() == TWAI_STATE_RUNNING,
          "...and service() restarts it, which is why that branch exists");

    // A SECOND, genuinely separate bus-off must count again.
    fake().enterBusOff();
    p.service(110);
    check(p.busOffEvents() == 2, "a second bus-off is a second event");

    check(fake().invalidStateCalls() == 0,
          "the whole bus-off/recovery walk made no wrong-state call");
    p.end();
  }

  // ----------------------------------------------------------------
  // 4. Frames do not vanish: what is delivered is what is received.
  // ----------------------------------------------------------------
  {
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 64, 16);
    p.begin(500000);
    const uint8_t payload[8] = {0xDE, 0xAD, 0xBE, 0xEF, 1, 2, 3, 4};
    for (uint32_t i = 0; i < 20; i++)
      fake().deliver(0x18FF0000 + i, true, payload, 8);
    p.service(10);

    uint32_t got = 0;
    bool ids_ok = true, ext_ok = true, data_ok = true;
    RxFrame f;
    while (p.receive(f)) {
      if (f.id != 0x18FF0000 + got) ids_ok = false;
      if (!f.ext) ext_ok = false;
      if (f.len != 8 || std::memcmp(f.data, payload, 8) != 0) data_ok = false;
      got++;
    }
    check(got == 20, "all 20 frames come back out");
    check(ids_ok, "...in order, with their identifiers intact");
    check(ext_ok, "...keeping the extended flag");
    check(data_ok, "...and their payloads byte for byte");
    check(p.rxDropped() == 0, "nothing was lost, and nothing was counted");
    p.end();
  }

  // ----------------------------------------------------------------
  // 5. The CanPort contract, through the interface.
  // ----------------------------------------------------------------
  {
    fake().hardReset();
    TwaiPort twai(TX_GPIO, RX_GPIO, 4, 4);
    twai.begin(500000);
    CanPort& port = twai;          // the contract is on the BASE class
    port.service(10);

    const uint32_t before = port.rxDropped();
    fake().injectRxFifoOverrun(1);
    port.service(20);
    check(port.rxDropped() == before + 1,
          "CanPort::rxDropped() rises by one per lost frame (can_port.h:52)");

    fake().injectRxFifoOverrun(1);
    port.service(30);
    check(port.rxDropped() == before + 2,
          "and so does the next one -- the MCP2515 had a bug here where a "
          "repeat was invisible, so the same case is checked on both ports");
    twai.end();
  }

  // ----------------------------------------------------------------
  // 6. Frames the bus-off recovery throws away are counted.
  //
  // The ESP-IDF header says twai_initiate_recovery() "will reset the TX
  // queue, clearing any messages pending transmission". Those frames were
  // accepted by send(), so they are not in txDropped(); they never failed
  // an attempt, so the driver's tx_failed_count does not have them either.
  // Before this they vanished with nothing counting them anywhere.
  // ----------------------------------------------------------------
  {
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    p.service(10);
    check(p.txDiscardedBusOff() == 0, "nothing discarded on a healthy port");

    // Hold frames in the TX queue so there is something pending to lose.
    fake().setAutoFlush(false);
    const uint8_t payload[8] = {9, 9, 9, 9, 9, 9, 9, 9};
    for (int i = 0; i < 5; i++) p.send(0x400 + i, false, payload, 8);
    check(fake().pendingTx() == 5, "5 frames are queued for transmission");

    fake().enterBusOff();
    p.service(20);

    check(fake().txDiscardedByRecovery() == 5,
          "recovery threw away all 5 (header: 'reset the TX queue')");
    check(p.txDiscardedBusOff() == 5,
          "...and the port counted them, by reading msgs_to_tx BEFORE "
          "initiating recovery");
    check(p.txDropped() == 0,
          "...and they are NOT in txDropped(): send() accepted them, so "
          "counting them there too would double-book the same loss");

    // Ordering is the whole trick here. Prove the read really is before
    // the clear by checking the queue is empty afterwards -- if the port
    // had read status in the block below instead, it would have seen 0.
    check(fake().pendingTx() == 0,
          "the queue really is empty afterwards, so the count could only "
          "have come from reading it first");

    check(fake().invalidStateCalls() == 0, "no wrong-state call");
  }

  // ----------------------------------------------------------------
  // 7. end() from every state.
  //
  // It used to be `twai_stop(); twai_driver_uninstall();` with both
  // return values dropped, which is wrong from three of the four states.
  // The one that mattered: from RECOVERING both calls are rejected, so
  // the driver stayed installed while the port claimed to be down -- and
  // tests 1 and 6 above use end()/begin(), so a later test could have
  // silently reused a half-installed driver.
  // ----------------------------------------------------------------
  {
    // RUNNING -- the ordinary case.
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    check(fake().state() == TWAI_STATE_RUNNING, "end() from RUNNING: setup");
    check(p.end(), "end() from RUNNING succeeds");
    check(!fake().installed(), "...and the driver really is uninstalled");
    check(fake().invalidStateCalls() == 0,
          "...having made no call the real driver would reject");
  }
  {
    // BUS_OFF -- uninstall IS permitted from here, so end() must work and
    // must NOT call stop() (which would be rejected).
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    fake().enterBusOff();
    check(p.end(), "end() from BUS_OFF succeeds (uninstall is allowed there)");
    check(!fake().installed(), "...and the driver really is uninstalled");
    check(fake().invalidStateCalls() == 0,
          "...without attempting the stop() that state would reject");
  }
  {
    // RECOVERING -- cannot be torn down, and must say so.
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    fake().enterBusOff();
    p.service(10);                       // initiates recovery
    check(fake().state() == TWAI_STATE_RECOVERING, "end() from RECOVERING: setup");
    check(!p.end(), "end() from RECOVERING REPORTS FAILURE rather than "
                    "silently leaving the driver installed");
    check(fake().installed(),
          "...and the driver is indeed still installed, so the report is true");

    // And it works once recovery finishes -- the caller can retry.
    fake().completeRecovery();
    check(p.end(), "end() succeeds once recovery has completed");
    check(!fake().installed(), "...and the driver is uninstalled");
  }
  {
    // Already down: idempotent, and not a failure.
    fake().hardReset();
    TwaiPort p(TX_GPIO, RX_GPIO, 8, 8);
    p.begin(500000);
    p.end();
    check(p.end(), "end() twice is not a failure");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_twai_port: all checks OK\n");
  return 0;
}
