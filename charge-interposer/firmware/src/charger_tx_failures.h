// Spec 2.2's "charger not acknowledging while expected" -- the 0x7F6 B7
// counter, and nothing else.
//
// Separated from main.cpp for the same reason as bridge_health.h: the rule
// is the whole of it, and the rest of main.cpp cannot be compiled on a
// host. Pure, no Arduino, no ESP-IDF -- test/host_unit/
// test_charger_tx_failures.cpp compiles this header directly. Until it was
// lifted out, the rule lived in a static function in main.cpp that no test
// compiles, so the one clause the counter exists for was pinned by reading
// alone (reviewer, 2026-10-13).
//
// THE SPEC'S COUNTER IS THE SUM OF THREE TALLIES, which is why this takes
// three arguments rather than a total a caller has already added up:
//
//   * queue full      -- the frame never entered the ring (txDropped);
//   * controller      -- it was loaded, abandoned past 100 ms, and the
//                        buffer then showed TXnIF clear (txFailed);
//   * the age-out     -- it sat at the head of the ring longer than
//                        100 ms and was discarded WITHOUT EVER BEING
//                        LOADED (txAged). Included by the user on
//                        2026-10-13: the controller was never asked to
//                        send it, so nothing it did failed, and the frame
//                        still did not reach the charger.
//
// THEY ARE DISJOINT -- a frame is dropped at enqueue, aged at the head, or
// resolved at a buffer, never two of them -- and that is what makes the sum
// "each failed transmission counted ONCE". Nothing else may add a tally on
// top: main.cpp once added its own bridge_drops and diag_tx_fail over the
// same sends, and intp_tx_fail reached its 255 ceiling before every bench
// run.
//
// THE GATE IS THE VCU'S OWN FLOW BIT (page 00 B1), not a judgement about
// the charger, so the counter reads 0 while driving however many frames
// bounce off a charger that is not there, and any non-zero value is a
// problem during a session.
//
// A DELTA THAT ARRIVES WHILE THE FLOW BIT IS 0 IS DISCARDED, NOT DEFERRED.
// The baseline moves on every call whether or not the delta is counted, so
// failures from outside a session can never be banked and then added to
// the next one. That also gives spec 2.2's last sentence for free: a board
// that boots mid-session reads flow 0 until the VCU's next page 00, and
// counts nothing until then.
//
// CALL IT EVERY LOOP PASS, not at the 1 Hz diagnostic cadence. The port
// counters are monotonic and know nothing about the flow bit, so sampling
// them once a second would attribute up to a second of failures to
// whichever side of a flow transition happened to be sampled.

#pragma once

#include <stdint.h>

namespace interposer {

class ChargerTxFailures {
 public:
  ChargerTxFailures() : seen_(0), while_expected_(0) {}

  // One pass. `expected` is the VCU's flow bit. The port tallies are
  // "since boot" and monotonic; starting the baseline at 0 is correct
  // because the ports start at 0 too.
  void accrue(uint32_t dropped, uint32_t failed, uint32_t aged,
              bool expected) {
    const uint32_t total = dropped + failed + aged;
    const uint32_t delta = total - seen_;   // unsigned: a wrap still gives
    seen_ = total;                          // the true distance
    if (delta && expected) while_expected_ += delta;
  }

  // What goes in 0x7F6 B7, before the caller saturates it to a byte.
  uint32_t whileExpected() const { return while_expected_; }

 private:
  uint32_t seen_;
  uint32_t while_expected_;
};

}  // namespace interposer
