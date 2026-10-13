// Spec 2.2's 0x7F6 B7 counter, on the host.
//
// The rule has four parts and each one fails in a way that looks like
// working code: the counter is the SUM of three disjoint tallies, each
// failure is counted ONCE, the VCU's flow bit gates it, and a delta that
// arrives while the flow bit is 0 is DISCARDED rather than banked for the
// next session. The last of those is the one with no visible symptom --
// an implementation that moves its baseline only when it counts reports a
// plausible number that includes failures from outside the session.
//
// Build and run (WSL on Windows; see test/host_unit/run.sh):
//     g++ -std=c++11 -O2 -I ../../src -o /tmp/tctf test_charger_tx_failures.cpp
//     /tmp/tctf

#include <cstdio>

#include "charger_tx_failures.h"

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

// The charger port as the loop sees it: three monotonic tallies and the
// flow bit, with one call per pass.
struct Port {
  ChargerTxFailures txf;
  uint32_t dropped = 0, failed = 0, aged = 0;
  bool flow = true;
  void pass() { txf.accrue(dropped, failed, aged, flow); }
  uint32_t count() const { return txf.whileExpected(); }
};

int main() {
  // --- one case per tally, which is the sum the spec asks for -----------
  {
    Port p;
    p.pass();
    check(p.count() == 0, "an idle pass counts nothing");

    p.dropped++;                 // the ring was full
    p.pass();
    check(p.count() == 1, "a queue-full drop counts");

    p.failed++;                  // loaded, abandoned, TXnIF clear
    p.pass();
    check(p.count() == 2, "a controller failure counts");

    p.aged++;                    // discarded at the head, never loaded
    p.pass();
    check(p.count() == 3,
          "a frame aged out of the queue counts -- user, 2026-10-13");

    p.pass();
    p.pass();
    check(p.count() == 3, "and passes with nothing moving add nothing");
  }

  // --- counted ONCE each, however many arrive in one pass --------------
  {
    Port p;
    p.pass();
    p.dropped += 2;
    p.failed += 3;
    p.aged += 4;
    p.pass();
    check(p.count() == 9,
          "nine failures across all three tallies in one pass count nine");
    check(p.count() == 9, "reading it again does not move it");
  }

  // --- the flow bit gates it, and the delta is DISCARDED ---------------
  {
    Port p;
    p.flow = false;              // driving: the Bel unit is offline
    p.pass();
    p.aged += 500;               // frames bouncing off nothing
    p.dropped += 500;
    p.pass();
    check(p.count() == 0,
          "1,000 failures while the flow bit is 0 count nothing");

    p.flow = true;               // a session starts
    p.pass();
    check(p.count() == 0,
          "and they are NOT banked: the session starts at 0 even though "
          "the tallies stand at 1,000");

    p.aged++;
    p.pass();
    check(p.count() == 1,
          "only what fails inside the session counts");

    p.flow = false;              // the session ends
    p.pass();
    p.failed += 7;
    p.pass();
    check(p.count() == 1, "failures after it count nothing either");
    p.flow = true;
    p.pass();
    check(p.count() == 1, "and are not added when the next one opens");
  }

  // --- a board that boots mid-session (spec 2.2, last sentence) --------
  {
    // The flow bit is unknown until the VCU's next page 00, which the
    // core reports as 0. The port tallies may already be moving.
    Port p;
    p.flow = false;
    p.dropped = 12;
    p.aged = 3;
    p.pass();
    check(p.count() == 0,
          "a standing backlog at boot counts nothing while flow is "
          "unknown");
    p.flow = true;
    p.pass();
    check(p.count() == 0, "nor on the first pass after page 00 arrives");
    p.failed++;
    p.pass();
    check(p.count() == 1, "the session counts from there");
  }

  // --- the baseline starts at zero, like the ports ---------------------
  {
    // Unreachable on the board, where the tallies start at 0 with the
    // port, but it is the behaviour the zero baseline implies and the
    // first pass is the only one it can affect.
    Port p;
    p.aged = 40;
    p.pass();
    check(p.count() == 40,
          "the first pass measures against zero, so a standing total "
          "inside a session counts in full");
  }

  // --- the unsigned subtraction, which is why a wrap is harmless -------
  {
    Port p;
    p.flow = false;
    p.dropped = 0xFFFFFFFEu;     // one short of the top, absorbed by the
    p.pass();                    // baseline with nothing counted
    check(p.count() == 0, "the standing total is not counted outside a "
                          "session");
    p.flow = true;
    p.dropped += 4;              // the SUM wraps past 0xFFFFFFFF to 2
    p.pass();
    check(p.count() == 4,
          "a tally wrapping past 0xFFFFFFFF still gives a delta of 4, "
          "because the subtraction is unsigned");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_charger_tx_failures: all checks OK\n");
  return 0;
}
