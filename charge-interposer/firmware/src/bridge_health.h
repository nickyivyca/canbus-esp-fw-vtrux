// Spec 8.2's `bridge_ok`: health SINCE THE PREVIOUS STATUS FRAME.
//
// Separated from main.cpp because the interval rule is the whole of it and
// the rest of main.cpp cannot be compiled on a host. Pure, no Arduino, no
// ESP-IDF -- test/host_unit/test_bridge_health.cpp compiles this header
// directly.
//
// The flag is clear when, over the interval just ended, any of these
// happened:
//
//   * an RX queue overflowed on either port, or the emit list overflowed;
//   * either controller ENTERED bus-off. The edge, not the level: recovery
//     is automatic (spec 2, B-7c), so a transient that begins and ends
//     between two status frames is invisible to a busOff() read;
//   * a bridged frame failed to reach the VEHICLE (added 2026-10-04). Once
//     the spec 2.2 counter became charger-only, a charger frame that never
//     reached the VCU was in no CAN field at all, and that is a
//     transparency failure. The board's own diagnostic frames -- the spec
//     8.1 mirrors and the 8.2 status frames -- do not count: they are not
//     bridged traffic, and a diagnostic that cannot be sent is already
//     visible by its own absence.
//
// ...or the controllers did not both start, or one is bus-off right now.
//
// Every input is a monotonic counter compared against its value at the
// previous frame, so nothing has to be reset by the reader and a missed
// call cannot leave a latch set.
//
// UNRESOLVED, and it decides whether this flag means anything on a drive
// (raised by the review sessions, 2026-10-04). The bus-off input assumes a
// charger port with nothing acknowledging does NOT reach bus-off: the
// premise is that the MCP2515 goes error-passive at 128 and stops counting
// acknowledgement errors, so TEC never passes 255. DS20001801J does not
// say -- p47 section 6.7 gives the thresholds and defers every increment
// rule to "the CAN bus specification" -- so it is DEFERRED, not confirmed.
//
// If it is wrong, a silent charger segment cycles bus-off and recovers
// every 128 x 11 bit times, about 2.8 ms at 500 kbit/s. busOffEvents()
// would then climb all through every drive, when the Bel unit is offline
// anyway, and bridge_ok would read clear on every status frame of every
// journey -- a flag that is always false says nothing.
//
// The existing bench logs cannot settle it: all 5,324 `LOSS` lines read
// `chg(... off=0`, but the charger was acknowledging in every one of those
// runs, and busOff() is a level sampled about once a second against a
// 2.8 ms cycle, so it would read 0 almost every time regardless. The
// conformance run takes TEC, EFLG, the TXREQ bits AND busOffEvents()
// across a second of no-ACK transmission; this counter is its own cheapest
// witness. If the premise fails, the fix is a spec 8.2 question, not a
// code change to be made here.

#pragma once

#include <stdint.h>

namespace interposer {

class BridgeHealth {
 public:
  BridgeHealth() : prev_ovf_(0), prev_bus_off_events_(0), prev_veh_fail_(0) {}

  // Call once per status frame. Returns the flag for THIS frame and moves
  // the baselines on. The counters are all "since boot" and monotonic;
  // starting them at 0 means the first frame's interval is "since boot",
  // which is correct for that one frame.
  bool sample(bool healthy, uint32_t ovf, uint32_t bus_off_events,
              uint32_t veh_bridge_fail, bool bus_off_now) {
    const bool ok = healthy && !bus_off_now && ovf == prev_ovf_ &&
                    bus_off_events == prev_bus_off_events_ &&
                    veh_bridge_fail == prev_veh_fail_;
    prev_ovf_ = ovf;
    prev_bus_off_events_ = bus_off_events;
    prev_veh_fail_ = veh_bridge_fail;
    return ok;
  }

 private:
  uint32_t prev_ovf_;
  uint32_t prev_bus_off_events_;
  uint32_t prev_veh_fail_;
};

}  // namespace interposer
