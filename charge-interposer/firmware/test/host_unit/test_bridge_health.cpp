// Spec 8.2's `bridge_ok` interval rule, on the host.
//
// The claim worth testing is narrow and easy to get wrong in a way that
// looks right: a failure inside one interval clears the flag for THAT frame
// and that frame only. Both halves matter. A flag that latches reports a
// bridge as broken forever after one overflow in hour one -- which is the
// "since boot" behaviour review item B-3 was about. A flag that never
// clears reports nothing at all.
//
// Build and run (WSL on Windows; see test/host_unit/run.sh):
//     g++ -std=c++11 -O2 -I ../../src -o /tmp/tbh test_bridge_health.cpp
//     /tmp/tbh

#include <cstdio>

#include "bridge_health.h"

using namespace interposer;

static int failures = 0;

static void check(bool cond, const char* what) {
  if (!cond) {
    std::printf("FAIL  %s\n", what);
    failures++;
  } else {
    std::printf("  ok  %s\n", what);
  }
}

// A running bench: the counters only ever go up, as the real ones do.
struct Bus {
  BridgeHealth h;
  uint32_t ovf = 0, boe = 0, vfail = 0;
  bool off_now = false;
  bool healthy = true;
  bool frame() { return h.sample(healthy, ovf, boe, vfail, off_now); }
};

int main() {
  // --- the vehicle-side input, which is what 2026-10-04 added ------------
  {
    Bus b;
    check(b.frame(), "first frame on a clean bridge is ok");
    check(b.frame(), "and so is the next");

    b.vfail++;                       // one bridged frame missed the vehicle
    check(!b.frame(), "a bridged frame missing the vehicle clears the flag");
    check(b.frame(),
          "and the NEXT frame is ok again -- it does not latch");
    check(b.frame(), "nor the one after");

    b.vfail += 3;
    check(!b.frame(), "three in one interval clear it once");
    check(b.frame(), "then clean again");
  }

  // --- it must not be satisfied by the other inputs ----------------------
  {
    Bus b;
    b.frame();
    b.ovf++;
    check(!b.frame(), "an RX overflow still clears it");
    check(b.frame(), "for one frame only");

    b.boe++;
    check(!b.frame(), "entering bus-off still clears it");
    check(b.frame(), "for one frame only");
  }

  // --- the level inputs latch for as long as they hold -------------------
  {
    Bus b;
    b.frame();
    b.off_now = true;
    check(!b.frame(), "bus-off NOW clears it");
    check(!b.frame(), "and keeps it clear while it holds");
    b.off_now = false;
    check(b.frame(), "until the controller recovers");

    b.healthy = false;
    check(!b.frame(), "a board that never started is never ok");
    check(!b.frame(), "and stays that way");
  }

  // --- several at once, and the no-op case -------------------------------
  {
    Bus b;
    b.frame();
    b.ovf++;
    b.boe++;
    b.vfail++;
    check(!b.frame(), "all three in one interval clear it");
    check(b.frame(), "and one clean interval restores it");

    // The counters are monotonic "since boot": a large standing value with
    // no change in the interval is health, not failure. Getting this wrong
    // is the "since boot" defect.
    Bus c;
    c.ovf = 99;
    c.boe = 5;
    c.vfail = 4000;
    check(!c.frame(),
          "the first frame compares against zero, so a standing backlog "
          "clears it once");
    check(c.frame(),
          "but large standing counts that are not MOVING are healthy");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_bridge_health: all checks OK\n");
  return 0;
}
