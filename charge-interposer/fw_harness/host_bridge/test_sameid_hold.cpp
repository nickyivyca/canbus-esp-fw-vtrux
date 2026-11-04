// Spec 2: the chip must never hold two frames of one identifier at once.
//
// WHY A DEDICATED FILE. `test_l2_bridge[replay]` asserts the property on
// real traffic, which is the right end-to-end check but a poor probe: the
// capture produced ONE same-identifier transposition in 22,117 frames, so
// a regression that halved the protection would still pass it most of the
// time. This drives the condition deliberately -- a burst of one
// identifier large enough to want all three buffers -- so the property is
// exercised on every frame rather than once in ten thousand.
//
// WHAT SPEC 2 ACTUALLY REQUIRES, and the three parts are separable:
//   1. frames of one identifier reach the wire in the order they were
//      queued;
//   2. at no moment does more than one hardware buffer hold a given
//      identifier;
//   3. a buffer whose frame has been given up on (2.2) still counts as
//      holding that identifier until its outcome is known, because the
//      frame may yet go out late.
// A driver could satisfy (1) by luck while violating (2), and could
// satisfy (1) and (2) while violating (3) -- the third only shows up when
// an abort is in flight. Each is checked separately.
//
// WHY ORDER IS NOT LEFT TO THE CHIP. DS20001801J p15 s3.2: selection
// among the chip's own buffers is TXP, then HIGHEST BUFFER NUMBER, and is
// "independent from ... the message arbitration scheme built into the CAN
// protocol". The driver never writes TXP, so the tie-break always
// decides and two frames of one identifier resident at once can leave in
// either order.

#include <cstdio>
#include <cstring>

#include <vector>

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
const uint32_t ONE_ID = 0x18EFC000;   // the paged command: order carries meaning

struct Rig {
  Mcp2515Fake chip;
  SPIClass spi;
  Mcp2515Port mcp;

  Rig() : spi(0), mcp(spi, CS_PIN, INT_PIN) {
    hostclock::attach(&chip, CS_PIN);
    hostclock::attachInt(INT_PIN);
    spi.attach(&chip);
    mcp.begin(500000);
  }
  ~Rig() { hostclock::detach(); }
};

// Payload byte 0 carries the sequence number, so the wire order is
// readable without depending on which buffer carried it -- the thing a
// dongle cannot see and a test should not need.
void sendSeq(Mcp2515Port& p, uint32_t id, uint8_t seq) {
  uint8_t d[8];
  std::memset(d, 0, sizeof(d));
  d[0] = seq;
  p.send(id, true, d, 8);
}

// Every buffer's held identifier, as the CHIP sees it: TXREQ set with a
// frame behind it. Co-residency is counted from the chip rather than from
// the driver's own bookkeeping on purpose -- asking the driver whether it
// kept its promise lets a bug in that bookkeeping answer for itself.
uint32_t coresidentNow(Mcp2515Fake& chip) {
  uint32_t worst = 0;
  for (int a = 0; a < 3; a++) {
    if (!chip.txPending(a)) continue;
    uint32_t n = 1;
    for (int b = a + 1; b < 3; b++)
      if (chip.txPending(b) && chip.txFrameId(b) == chip.txFrameId(a) &&
          chip.txFrameExt(b) == chip.txFrameExt(a))
        n++;
    if (n > worst) worst = n;
  }
  return worst;
}

}  // namespace

int main() {
  std::printf("--- spec 2: the same-identifier hold ---\n");

  // --- ONE IDENTIFIER, ENOUGH FRAMES TO WANT EVERY BUFFER -------------
  {
    Rig r;
    const uint32_t N = 60;
    uint32_t worst_coresident = 0;
    uint32_t passes = 0;

    for (uint32_t i = 0; i < N; i++) sendSeq(r.mcp, ONE_ID, (uint8_t)i);

    // Service until the ring and the buffers drain. Co-residency is
    // sampled EVERY pass, not at the end: a violation that resolves
    // before the run finishes is still a violation, and an end-state
    // check could not see it.
    for (uint32_t k = 0; k < 20000; k++) {
      r.mcp.service(r.chip.millis());
      const uint32_t c = coresidentNow(r.chip);
      if (c > worst_coresident) worst_coresident = c;
      passes++;
      r.chip.advance(20);
      if (r.chip.delivered() >= N) break;
    }
    r.mcp.service(r.chip.millis());

    std::printf("  --  %u frames of one id: delivered %u, aged %u, "
                "dropped %u, failed %u over %u passes\n",
                N, r.chip.delivered(), r.mcp.txAged(), r.mcp.txDropped(),
                r.mcp.txFailed(), passes);
    std::printf("  --  worst co-residency of that id across all passes: %u\n",
                worst_coresident);

    check(r.chip.delivered() == N,
          "every frame of the burst reaches the wire -- the hold delays, "
          "it does not discard");
    check(worst_coresident <= 1,
          "spec 2(2): at no pass does more than ONE buffer hold that "
          "identifier");

    // Order, read from the payload sequence on the wire.
    const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
    uint32_t seen = 0;
    bool in_order = true;
    uint8_t expect = 0;
    for (size_t k = 0; k < w.size(); k++) {
      if (!w[k].delivered) continue;
      if (w[k].frame.id != ONE_ID) continue;
      seen++;
      if (w[k].frame.data[0] != expect) in_order = false;
      expect++;
    }
    std::printf("  --  wire sequence: %u frames of 0x%X inspected\n",
                seen, ONE_ID);
    // COUNTED, not just flagged: a loop that only sets a flag on
    // mismatch says "ok" when it inspected nothing at all.
    check(seen == N && in_order,
          "spec 2(1): all of them reach the wire in the order they were "
          "queued, read from the payload rather than the buffer number");
  }

  // --- TWO IDENTIFIERS INTERLEAVED ------------------------------------
  //
  // The hold blocks at the HEAD, so a run of one identifier must not
  // stall a different identifier behind it forever -- and both must stay
  // internally ordered. This is the case where an implementation that
  // "skips past" the blocked head to keep the buffers fed would look
  // better on throughput and be wrong.
  {
    Rig r;
    // 30 each, so 60 total fits the 64-deep ring. The first draft used
    // 40 each and lost 17 to `txDropped` before a single service() ran
    // -- a ring overflow in the TEST, which presented as "the hold
    // starves the other identifier". The numbers to distrust are the
    // ones that blame the thing you are testing for the thing your
    // harness did; `dropped 17` in the counters is what said so.
    const uint32_t N = 30;
    const uint32_t ID_B = 0x18FF50E5;
    for (uint32_t i = 0; i < N; i++) {
      sendSeq(r.mcp, ONE_ID, (uint8_t)i);
      sendSeq(r.mcp, ID_B, (uint8_t)i);
    }
    for (uint32_t k = 0; k < 40000; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
      if (r.chip.delivered() >= 2 * N) break;
    }
    r.mcp.service(r.chip.millis());

    const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
    uint8_t ea = 0, eb = 0;
    bool oa = true, ob = true;
    uint32_t na = 0, nb = 0;
    for (size_t k = 0; k < w.size(); k++) {
      if (!w[k].delivered) continue;
      const mcpfake::CanFrame& f = w[k].frame;
      if (f.id == ONE_ID) {
        na++;
        if (f.data[0] != ea) oa = false;
        ea++;
      } else if (f.id == ID_B) {
        nb++;
        if (f.data[0] != eb) ob = false;
        eb++;
      }
    }
    std::printf("\n  --  interleaved: %u of 0x%X and %u of 0x%X delivered; "
                "aged %u dropped %u failed %u\n",
                na, ONE_ID, nb, ID_B, r.mcp.txAged(), r.mcp.txDropped(),
                r.mcp.txFailed());
    check(na == N && nb == N,
          "both identifiers get through -- the head-of-line hold delays "
          "the blocked identifier, it does not starve the other one");
    check(oa && ob,
          "and each identifier is internally in order");
  }

  // --- SPEC 2(3): AN ABORTED FRAME STILL HOLDS ITS IDENTIFIER ---------
  //
  // THIS IS NOT A MUTATION TEST, AND THE ATTEMPT TO MAKE IT ONE IS THE
  // USEFUL RESULT. In drainTx() the line `tx_holds_[n] = false;` sits
  // in the abort-RESOLVE branch rather than at the abort itself,
  // because p16 s3.6 note 1 says a message ALREADY TRANSMITTING when
  // TXREQ is cleared runs to completion -- so between the abort and
  // the resolve the frame may still be on the wire, and the reasoning
  // is that a successor loaded in that window could overtake it.
  //
  // That mutation was built and run (the clause moved up to the abort,
  // against this case and against a harsher probe with five
  // successors and every buffer free). The mutant passes, byte for
  // byte: `wire order: 0 1 2 3 4 5` either way.
  //
  // The reason is structural, not a gap in the case. The chip model
  // has ONE transmit engine, so a frame already on the wire always
  // finishes before any other buffer can start. A frame that was
  // aborted but is still transmitting therefore cannot be overtaken
  // HERE however many successors are queued -- and that is also true
  // of real silicon with one CAN controller. So the clause is
  // conservative and correct as written, but nothing at this level can
  // distinguish it from the mutation, and no host test should be
  // claimed to defend it. If it is load-bearing at all it is on the
  // bench, which is the witness's job.
  //
  // What this case DOES pin, which is worth having on its own: the
  // abort-then-late ('L') path runs at all, A is counted late rather
  // than failed, and its successor is behind it on the wire.
  //
  // Shape: frame A goes unacknowledged so it is retried and never
  // completes; the driver abandons it at TX_MAX_AGE_MS; the bus then
  // acknowledges, so A goes out LATE. Frame B of the same identifier is
  // queued behind it the whole time. A must still be first.
  {
    Rig r;
    r.chip.setAcknowledged(false);

    sendSeq(r.mcp, ONE_ID, 0);     // A

    // Let A load and start transmitting.
    for (uint32_t k = 0; k < 200; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
    }
    int carrier = -1;
    for (int n = 0; n < 3; n++) if (r.chip.txReq(n)) carrier = n;

    // B IS QUEUED LATE, AND THAT IS NOT A DETAIL. The first draft
    // queued it beside A at t=0. A is only abandoned at
    // TX_MAX_AGE_MS (100 ms), and the ring ages its head on the SAME
    // deadline -- so B went stale in the ring at the very moment the
    // hold would have released, and the case ended with nothing on the
    // wire at all. Queueing B shortly before the abort keeps the ring's
    // age rule out of an experiment that is about the hold.
    const uint32_t B_AT_MS = 95;
    while (r.chip.millis() < B_AT_MS) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
    }
    sendSeq(r.mcp, ONE_ID, 1);     // B, blocked behind A by the hold

    // Run until the driver gives up on A. The abort is OBSERVED --
    // TXREQ going low on the carrying buffer -- rather than assumed
    // from elapsed time.
    //
    // THE BUS IS RE-ACKNOWLEDGED IN THE SAME BREATH, BEFORE TIME MOVES.
    // The first draft advanced the clock first and tested afterwards,
    // which let the chip finish A's attempt while the bus was still
    // silent: p16 s3.6 note 1 then applies to a FAILED attempt, A was
    // discarded, and the run took the 'F' path instead of 'L'. It
    // printed "aborted yes" and proved nothing about ordering. The
    // txLate() guard below is what caught it.
    bool aborted = false;
    for (uint32_t k = 0; k < 400000 && !aborted; k++) {
      r.mcp.service(r.chip.millis());
      if (carrier >= 0 && !r.chip.txReq(carrier)) {
        aborted = true;
        r.chip.setAcknowledged(true);
      }
      r.chip.advance(20);
    }

    for (uint32_t k = 0; k < 400000; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
      if (r.chip.delivered() >= 2) break;
    }
    r.mcp.service(r.chip.millis());

    std::printf("\n  --  abort-then-late: carrier buf %d, aborted %s, "
                "delivered %u, late %u, failed %u, aged %u, dropped %u\n",
                carrier, aborted ? "yes" : "NO", r.chip.delivered(),
                r.mcp.txLate(), r.mcp.txFailed(), r.mcp.txAged(),
                r.mcp.txDropped());

    // THE ANTI-VACUITY GUARDS. Every one of these can fail
    // independently, and without them a run in which A was simply sent
    // normally would print the same "in order" as a run that exercised
    // the abort path.
    check(carrier >= 0, "A reached a hardware buffer at all");
    check(aborted, "the driver DID abandon A -- the abort path ran");
    check(r.mcp.txLate() == 1,
          "and A was then delivered LATE, after the abort: txLate() is 1, "
          "so this run took the resolve path the mutation would break");

    uint32_t na = 0;
    uint8_t order[4] = {0, 0, 0, 0};
    const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
    for (size_t k = 0; k < w.size(); k++) {
      if (!w[k].delivered) continue;
      if (w[k].frame.id != ONE_ID) continue;
      if (na < 4) order[na] = w[k].frame.data[0];
      na++;
    }
    std::printf("  --  wire: %u frame(s) of 0x%X, sequence %u,%u\n",
                na, ONE_ID, order[0], order[1]);
    check(na == 2 && order[0] == 0 && order[1] == 1,
          "spec 2(3): B did not overtake A -- a frame aborted and then "
          "sent late is still ahead of its successor on the wire "
          "(NOTE: passes with the hold released at the abort too -- see "
          "the header; this does not defend that clause)");
  }

  // --- THE HOLD MUST SAY WHETHER IT ENGAGED ---------------------------
  //
  // A bench arm that reports frames in order has proved nothing unless
  // the hold actually bit. On 2026-10-05 the approved spec 12 arm
  // turned out to be exactly that: once the stimulus was modelled with
  // the vehicle wire serialising it, the arm produced no co-residency
  // at all and would have passed identically with the hold removed.
  // txHoldDeferrals() is what makes that visible from the board, so it
  // has to be trustworthy in both directions -- non-zero when the hold
  // bites, and zero when it does not.
  {
    Rig r;
    const uint32_t N = 60;
    for (uint32_t i = 0; i < N; i++) sendSeq(r.mcp, ONE_ID, (uint8_t)i);
    for (uint32_t k = 0; k < 20000; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
      if (r.chip.delivered() >= N) break;
    }
    r.mcp.service(r.chip.millis());
    std::printf("\n  --  contended (one id, %u frames): deferral passes %u, "
                "frames deferred %u, delivered %u\n",
                N, r.mcp.txHoldDeferrals(), r.mcp.txFramesDeferred(),
                r.chip.delivered());
    check(r.mcp.txHoldDeferrals() > 0,
          "the hold reports that it BIT on a single-identifier burst -- "
          "without this, a clean ordering result could never be told from "
          "an arm that never contended");
    check(r.mcp.txFramesDeferred() > 0 &&
          r.mcp.txFramesDeferred() <= N,
          "and the FRAME count is non-zero and cannot exceed the frames "
          "sent -- it is the quotable quantity, the pass count is not");
    check(r.mcp.txFramesDeferred() <= r.mcp.txHoldDeferrals(),
          "frames deferred never exceeds passes blocked, since a frame "
          "is counted once however many passes it waited");
  }

  // THE OTHER DIRECTION, which is the half that makes the check
  // meaningful: a stream that never contends must report ZERO. A
  // counter that is always positive would mark every arm as valid.
  {
    Rig r;
    const uint32_t N = 40;
    for (uint32_t i = 0; i < N; i++) {
      sendSeq(r.mcp, ONE_ID, (uint8_t)i);
      // Fully drained between frames, so no two of this identifier are
      // ever outstanding together and the hold has nothing to block.
      for (uint32_t k = 0; k < 400; k++) {
        r.mcp.service(r.chip.millis());
        r.chip.advance(20);
      }
    }
    r.mcp.service(r.chip.millis());
    std::printf("  --  uncontended (same id, drained between frames): "
                "deferral passes %u, frames deferred %u, delivered %u\n",
                r.mcp.txHoldDeferrals(), r.mcp.txFramesDeferred(),
                r.chip.delivered());
    check(r.chip.delivered() == N,
          "the uncontended case really did send every frame -- otherwise "
          "zero deferrals would just mean nothing happened");
    check(r.mcp.txHoldDeferrals() == 0 && r.mcp.txFramesDeferred() == 0,
          "and it reports ZERO deferrals, so a bench arm that never "
          "contended is distinguishable from one that did");
  }

  // --- HEAD OF LINE: NOTHING BEHIND A HELD FRAME IS LOADED ------------
  //
  // Spec 2: "A frame whose identifier is in any transmit buffer stays at
  // the head of the driver's queue, AND NOTHING BEHIND IT IS LOADED,
  // until that buffer is free." The cases above check the first half
  // (one buffer per identifier, order kept). This checks the second: X1
  // and X2 share an identifier, Y and Z are other identifiers queued
  // after X2. While X1 occupies a buffer, X2 must wait -- and Y and Z,
  // behind it, must not be loaded into the two free buffers. Sampled on
  // the chip every pass, LOADED (written into a buffer, TXREQ or not).
  // (Tester, 2026-10-09; 1b "the hold's head-of-line rule".)
  {
    Rig r;
    const uint32_t I = ONE_ID, J = 0x18FF60E5, K = 0x18FF61E5;
    sendSeq(r.mcp, I, 1);      // X1
    sendSeq(r.mcp, I, 2);      // X2: held behind X1
    sendSeq(r.mcp, J, 3);      // Y: behind X2
    sendSeq(r.mcp, K, 4);      // Z: behind X2

    auto loadedTag = [&](uint32_t id, uint8_t tag) {
      for (int b = 0; b < 3; b++)
        if (r.chip.txLoaded(b) && r.chip.txFrameId(b) == id &&
            r.chip.txFrameByte0(b) == tag)
          return true;
      return false;
    };
    auto onWire = [&](uint32_t id, uint8_t tag) {
      const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
      for (size_t k = 0; k < w.size(); k++)
        if (w[k].delivered && w[k].frame.id == id &&
            w[k].frame.data[0] == tag)
          return true;
      return false;
    };

    uint32_t waiting_passes = 0, violations = 0, passes = 0;
    for (uint32_t k = 0; k < 20000; k++) {
      // sampled BEFORE each service, so the state the sends left is seen
      const bool x2_waiting = !loadedTag(I, 2) && !onWire(I, 2);
      const bool x1_holding = loadedTag(I, 1);
      if (x2_waiting && x1_holding) {
        waiting_passes++;
        if (loadedTag(J, 3) || loadedTag(K, 4) || onWire(J, 3) ||
            onWire(K, 4))
          violations++;
      }
      r.mcp.service(r.chip.millis());
      passes++;
      r.chip.advance(20);
      if (r.chip.delivered() >= 4) break;
    }
    r.mcp.service(r.chip.millis());

    std::vector<uint8_t> order;
    const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
    for (size_t k = 0; k < w.size(); k++)
      if (w[k].delivered) order.push_back(w[k].frame.data[0]);
    std::printf("  --  head of line: X2 waited behind X1 on %u of %u "
                "samples; Y/Z loaded meanwhile on %u; wire order",
                waiting_passes, passes, violations);
    for (size_t k = 0; k < order.size(); k++) std::printf(" %u", order[k]);
    std::printf("\n");

    check(waiting_passes > 0,
          "setup: X2 really did wait behind X1 with Y and Z queued -- "
          "otherwise the rule below was never exercised");
    check(violations == 0,
          "spec 2: nothing behind the held X2 is loaded while it waits "
          "(Y and Z stay out of the free buffers)");
    check(order.size() == 4 && order[0] == 1 && order[1] == 2,
          "all four reach the wire, X1 then X2 first -- Y and Z cannot "
          "overtake the frame they were queued behind");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_sameid_hold: all checks OK\n");
  return 0;
}
