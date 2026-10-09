// The board-side completion-order witness (INTP_ORDER_WITNESS).
//
// WHY THE BOARD HAS TO HAVE A VIEW AT ALL. Spec 12 entry 2 reads the
// order frames arrive at the charger from a sequence number in the
// payload. That is the right end-to-end check, but it can only say what
// ARRIVED. A dongle that drops or reorders on capture is
// indistinguishable, from the charger side alone, from the driver doing
// it -- and this project has already spent a day on exactly that
// confusion, where four separate "firmware failures" turned out to be
// undrained receive buffers in the measuring equipment. The witness is
// the board's own record of the order it completed frames in, so the
// two can be compared and the question "who reordered it" has an
// answer.
//
// This file checks the witness itself. A witness that is wrong is worse
// than no witness, because it would be used to exonerate the firmware.
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
const uint32_t ONE_ID = 0x18EFC000;

struct Rig {
  Mcp2515Fake chip;
  SPIClass spi;
  Mcp2515Port mcp;

  Rig() : spi(0), mcp(spi, CS_PIN, INT_PIN) {
    hostclock::attach(&chip, CS_PIN);
    hostclock::attachInt(INT_PIN);
    spi.attach(&chip);
    mcp.begin(500000);
    // AFTER begin(). The witness storage is static and shared between
    // cases in one process, so each case starts it from zero rather
    // than inheriting the previous one.
    Mcp2515Port::witnessReset();
  }
  ~Rig() { hostclock::detach(); }
};

void sendSeq(Mcp2515Port& p, uint32_t id, uint8_t seq) {
  uint8_t d[8];
  std::memset(d, 0, sizeof(d));
  d[0] = seq;
  p.send(id, true, d, 8);
}

// MIXED IDENTIFIERS UNDER LOAD. Everything above sends ONE identifier,
// and that is the configuration in which this witness cannot fail: the
// same-identifier hold stops a buffer being refilled while another
// carries that id, which happens to close the window the bug below
// lives in. Caught by the review session, not by this file.
//
// Returns true if the witness accounted for every delivered frame.
bool mixedLoad(uint32_t svc_us, uint32_t want, uint32_t nids) {
  Rig r;
  const uint32_t BURST = 8;
  const uint64_t burst_period_us = 2000;    // 8 frames / 2 ms = 4000 fps
  uint64_t next_burst = r.chip.micros();
  uint64_t next_service = r.chip.micros();
  uint32_t offered = 0;
  uint8_t d[8];
  while (offered < want) {
    if (r.chip.micros() >= next_burst) {
      for (uint32_t k = 0; k < BURST && offered < want; k++) {
        const uint8_t len = (uint8_t)(1 + (offered % 8));   // DLC 1..8
        for (uint8_t i = 0; i < 8; i++) d[i] = (uint8_t)(offered + i);
        r.mcp.send(0x18EF0000 + (offered % nids), true, d, len);
        offered++;
      }
      next_burst += burst_period_us;
    }
    if (r.chip.micros() >= next_service) {
      r.mcp.service(r.chip.millis());
      next_service += svc_us;
    }
    r.chip.advance(20);
  }
  for (uint32_t k = 0; k < 2000000; k++) {
    r.mcp.service(r.chip.millis());
    r.chip.advance(20);
    if (r.chip.delivered() >= offered) break;
  }
  r.mcp.service(r.chip.millis());

  const uint32_t comp = Mcp2515Port::witnessCompletions();
  const uint32_t deliv = r.chip.delivered();
  std::printf("  --  svc %3u us, %u ids: loads %u, witness completions %u, "
              "chip delivered %u, missing %d, multi %u\n",
              svc_us, nids, Mcp2515Port::witnessLoads(), comp, deliv,
              (int)deliv - (int)comp, Mcp2515Port::witnessMultiPasses());
  return comp == deliv;
}

}  // namespace

int main() {
  std::printf("--- board-side completion-order witness ---\n");

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

    std::printf("  --  loads %u, completions %u, stored %u, passes %u, "
                "multi-completion passes %u, full %s\n",
                Mcp2515Port::witnessLoads(),
                Mcp2515Port::witnessCompletions(),
                Mcp2515Port::witnessCount(), Mcp2515Port::witnessPasses(),
                Mcp2515Port::witnessMultiPasses(),
                Mcp2515Port::witnessFull() ? "YES (truncated)" : "no");

    check(Mcp2515Port::witnessLoads() == N,
          "the witness saw every load: one load sequence number per frame "
          "handed to the chip");
    check(Mcp2515Port::witnessCompletions() == r.chip.delivered(),
          "and one completion per frame the chip actually delivered -- the "
          "witness total agrees with the wire, not with the driver");
    check(!Mcp2515Port::witnessFull(),
          "nothing was lost to truncation in this run, so the order below "
          "is the whole order and not a prefix");

    // THE ORDER ITSELF, compared against the payload sequence the CHIP
    // recorded rather than against what this test queued. The test
    // expectation is what a broken witness would most plausibly have
    // been built to match, so it is the wrong thing to check against.
    const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
    std::vector<uint8_t> wire_seq;
    for (size_t k = 0; k < w.size(); k++)
      if (w[k].delivered && w[k].frame.id == ONE_ID)
        wire_seq.push_back(w[k].frame.data[0]);

    const Mcp2515Port::Completion* c = Mcp2515Port::witness();
    uint32_t compared = 0, agree = 0;
    for (uint32_t i = 0; i < Mcp2515Port::witnessCount() &&
                         i < (uint32_t)wire_seq.size(); i++) {
      if (c[i].outcome == 'F') continue;   // never reached the wire
      compared++;
      // load_seq counts loads from zero in order, and this burst sends
      // one frame per sequence number, so the two must line up.
      if (c[i].load_seq == (uint32_t)wire_seq[i]) agree++;
    }
    std::printf("  --  witness vs wire: %u compared, %u agree\n",
                compared, agree);
    // COUNTED, not flagged: `agree == compared` alone is true when
    // nothing was compared at all.
    check(compared == N && agree == compared,
          "the completion order the board recorded IS the order on the "
          "wire, load sequence number against payload sequence number");

    // Ambiguity is reported, never resolved. With the hold in force only
    // one buffer may carry this identifier, so no pass should complete
    // two of them.
    check(Mcp2515Port::witnessMultiPasses() == 0,
          "no pass completed more than one frame of this identifier, so "
          "no two entries here are mutually unordered");

    bool ids_ok = true, ext_ok = true;
    for (uint32_t i = 0; i < Mcp2515Port::witnessCount(); i++) {
      if (c[i].id != ONE_ID) ids_ok = false;
      if (!c[i].ext) ext_ok = false;
    }
    check(Mcp2515Port::witnessCount() > 0 && ids_ok && ext_ok,
          "every entry carries the right (id, ext), kept by the witness at "
          "load time rather than read back from the hold bookkeeping it "
          "exists to check");
  }

  // --- IT MUST STOP, NOT WRAP ----------------------------------------
  //
  // A ring would silently discard the START of the run, which for an
  // ordering question is the part that matters, and the result would
  // read as a complete short witness rather than a truncated long one.
  // Driven past WITNESS_N on purpose.
  {
    Rig r;
    const uint32_t N = Mcp2515Port::WITNESS_N + 50;
    for (uint32_t i = 0; i < N; i++) {
      sendSeq(r.mcp, ONE_ID, (uint8_t)i);
      // Serviced as we go. The ring is 64 deep and this is far more
      // than 64 frames; queueing them all first would overflow the RING
      // and measure that instead, which is how an earlier hold test
      // came to look like starvation.
      for (uint32_t k = 0; k < 40; k++) {
        r.mcp.service(r.chip.millis());
        r.chip.advance(20);
      }
    }
    for (uint32_t k = 0; k < 200000; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
      if (r.chip.delivered() >= N) break;
    }
    r.mcp.service(r.chip.millis());

    std::printf("\n  --  overrun: WITNESS_N %u, loads %u, completions %u, "
                "stored %u, dropped %u\n",
                Mcp2515Port::WITNESS_N, Mcp2515Port::witnessLoads(),
                Mcp2515Port::witnessCompletions(),
                Mcp2515Port::witnessCount(), r.mcp.txDropped());

    check(Mcp2515Port::witnessCount() == Mcp2515Port::WITNESS_N,
          "storage stops exactly at WITNESS_N");
    check(Mcp2515Port::witnessCompletions() > Mcp2515Port::WITNESS_N,
          "more completions happened than were stored -- the overrun is "
          "real, so this case is not vacuous");
    check(Mcp2515Port::witnessFull(),
          "and witnessFull() says so, so a truncated witness can never be "
          "read as a complete one");

    // The KEPT entries must be the FIRST ones. A wrap would leave the
    // LAST WITNESS_N instead, and every count above is identical in
    // both cases -- this is the only check that separates them.
    const Mcp2515Port::Completion* c = Mcp2515Port::witness();
    check(Mcp2515Port::witnessCount() > 0 && c[0].load_seq == 0,
          "and what it kept is the BEGINNING of the run: the first stored "
          "entry is load 0, which a ring would have overwritten");
  }

  // --- RECONCILIATION UNDER MIXED-IDENTIFIER LOAD --------------------
  //
  // THE CHECK THE REST OF THIS FILE COULD NOT MAKE. A witness that
  // silently drops completions is worse than no witness, because it
  // would be used to exonerate the firmware -- and every case above
  // sends a single identifier, where the hold prevents a buffer being
  // refilled before its completion is resolved and the gap cannot
  // appear. Mixed ids at a short service period open it.
  //
  // `witnessCompletions() == delivered` is the reconciliation every
  // bench witness dump must print, for exactly this reason.
  {
    std::printf("\n  --  mixed-identifier load, 20000 frames, DLC 1-8, "
                "bursts of 8 every 2 ms\n");
    bool ok20 = mixedLoad(20, 20000, 7);
    bool ok60 = mixedLoad(60, 20000, 7);
    bool ok200 = mixedLoad(200, 20000, 7);
    check(ok20,
          "at a 20 us service period the witness accounts for every frame "
          "the chip delivered");
    check(ok60,
          "and at 60 us, the bridge's own modelled pass period -- the one "
          "the bench will actually run at");
    check(ok200,
          "and at 200 us");
  }

  // --- THE WHOLE-RUN ORDER COUNTERS -----------------------------------
  //
  // The stored entries are the first WITNESS_N completions -- about 2%
  // of a truck-rate replay. A clean stored window therefore says
  // nothing about the rest of the run, and on 2026-10-05 a clean
  // window WAS reported as a clean run. These counters see every
  // completion and cost no storage; this checks they actually count.
  {
    Rig r;
    const uint32_t N = 400;
    // One identifier, so the hold serialises it and the completion
    // order must equal the load order exactly.
    //
    // OFFERED AS THE RUN PROGRESSES, not queued up front. The ring is
    // 64 deep and the hold limits a single identifier to roughly one
    // frame per 300 us; queueing 400 at once overflows the RING and
    // measures that instead. The first draft did exactly that and
    // completed 63 of 400 -- a ring-full -- which is the third time
    // this harness mistake has appeared in this file.
    for (uint32_t i = 0; i < N; i++) {
      sendSeq(r.mcp, ONE_ID, (uint8_t)i);
      for (uint32_t k = 0; k < 16; k++) {      // ~320 us of service
        r.mcp.service(r.chip.millis());
        r.chip.advance(20);
      }
    }
    for (uint32_t k = 0; k < 200000; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
      if (r.chip.delivered() >= N) break;
    }
    r.mcp.service(r.chip.millis());
    std::printf("\n  --  single id: completions %u, inversions %u, "
                "maxDisp %u, perId %u, idsTracked %u, overflow %u\n",
                Mcp2515Port::witnessCompletions(),
                Mcp2515Port::witnessInversions(),
                Mcp2515Port::witnessMaxDisplacement(),
                Mcp2515Port::witnessIdInversions(),
                Mcp2515Port::witnessIdsTracked(),
                Mcp2515Port::witnessIdOverflow());
    check(Mcp2515Port::witnessCompletions() == N,
          "the counters saw every completion, including the ones past "
          "WITNESS_N that were never stored");
    check(Mcp2515Port::witnessInversions() == 0 &&
          Mcp2515Port::witnessIdInversions() == 0,
          "and a single identifier under the hold has no inversions at all");
    check(Mcp2515Port::witnessIdsTracked() == 1 &&
          Mcp2515Port::witnessIdOverflow() == 0,
          "one identifier tracked, nothing overflowed the per-id table");
  }

  // THE COUNTER MUST BE ABLE TO REPORT A NON-ZERO, or every zero above
  // is worthless. There is no way to make the real driver reorder a
  // single identifier -- the hold forbids it -- so this drives the
  // counter through a case it MUST count: many identifiers, where the
  // chip's own tie-break (TXP, then highest buffer number, p15 s3.2)
  // reorders across identifiers freely.
  {
    Rig r;
    const uint32_t N = 600, NIDS = 12;
    uint8_t d[8];
    // OFFERED IN BURSTS OF THREE DISTINCT IDENTIFIERS, which is what
    // the cross-id transposition needs: three frames resident in three
    // buffers at once, so the chip's tie-break (TXP, then HIGHEST
    // buffer number, p15 s3.2) can hand them to the wire in an order
    // that is not the load order.
    //
    // The first draft serviced 30 passes after EVERY send, so only one
    // frame was ever in flight, nothing could co-reside and the
    // counter reported 0 -- which failed the anti-vacuity check below
    // and looked briefly like a broken counter. The stimulus was the
    // problem, and the check caught it, which is the point of having
    // it.
    for (uint32_t i = 0; i < N; i += 3) {
      for (uint32_t b = 0; b < 3 && i + b < N; b++) {
        std::memset(d, 0, sizeof(d));
        d[0] = (uint8_t)(i + b);
        r.mcp.send(0x18EF0000 + ((i + b) % NIDS), true, d, 8);
      }
      for (uint32_t k = 0; k < 40; k++) {      // ~800 us for three
        r.mcp.service(r.chip.millis());
        r.chip.advance(20);
      }
    }
    for (uint32_t k = 0; k < 200000; k++) {
      r.mcp.service(r.chip.millis());
      r.chip.advance(20);
      if (r.chip.delivered() >= N) break;
    }
    r.mcp.service(r.chip.millis());
    const uint32_t inv = Mcp2515Port::witnessInversions();
    std::printf("\n  --  %u ids: completions %u, inversions %u, maxDisp %u, "
                "perId %u, idsTracked %u, overflow %u\n",
                NIDS, Mcp2515Port::witnessCompletions(), inv,
                Mcp2515Port::witnessMaxDisplacement(),
                Mcp2515Port::witnessIdInversions(),
                Mcp2515Port::witnessIdsTracked(),
                Mcp2515Port::witnessIdOverflow());
    check(Mcp2515Port::witnessCompletions() == r.chip.delivered(),
          "mixed-id run still reconciles against the wire");
    check(Mcp2515Port::witnessIdInversions() == 0,
          "spec 2 holds per identifier even with twelve of them "
          "interleaved");
    check(Mcp2515Port::witnessIdsTracked() == NIDS &&
          Mcp2515Port::witnessIdOverflow() == 0,
          "all twelve identifiers tracked, none lost to the table");
    // THE ANTI-VACUITY CHECK FOR THE COUNTER ITSELF. If this is 0 the
    // stimulus never produced a cross-id transposition, and every
    // "inversions == 0" above would be evidence of nothing.
    std::printf("  --  cross-id inversions observed: %u (maxDisp %u)\n",
                inv, Mcp2515Port::witnessMaxDisplacement());
    check(inv > 0 && Mcp2515Port::witnessMaxDisplacement() > 0,
          "the inversion counter DOES report non-zero when frames are "
          "genuinely transposed -- without this, every zero it reports "
          "would be unfalsifiable");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_order_witness: all checks OK\n");
  return 0;
}
