// How much does Mcp2515Port actually get out, at the truck's frame rate?
//
// WHY THIS EXISTS. An L3 replay appeared to show the charger port
// delivering 2.4% of realistic traffic, and this file was written to
// localise that: it drives `Mcp2515Port` DIRECTLY, with no main.cpp, no
// core and no bridge loop, so there are fewer things to blame.
//
// THE SHORTFALL WAS NOT REAL, and the way it was finally caught is the
// reason to keep reading. It was a defect in the CHIP FAKE: LOAD TX
// BUFFER only marked a buffer loaded when exactly 13 bytes were clocked,
// while the driver correctly clocks 5 header bytes plus DLC. Every frame
// with DLC < 8 left the buffer unloaded with TXREQ set, which the fake's
// transmit engine skipped forever and the driver read as busy. See
// `finishLoadTx()` in mcp2515_fake_chip.cpp for the full account.
//
// EVERY SYNTHETIC CASE IN THIS FILE PASSED THROUGHOUT, at 100%, because
// they all use 8-byte payloads -- the one DLC the broken gate handled
// correctly. That is the lesson worth carrying: the probe built to
// localise the fault was blind to it by construction, and its clean
// result was read as "the synthetic path is fine, so the difference must
// be in the capture", which sent four more experiments after properties
// of the capture (bursts, extended ids, timestamp quantisation, bit
// stuffing) that had nothing to do with it. What broke the deadlock was
// `test_tx_stall_trace`, which stopped asking whether the numbers were
// bad and recorded what each hardware buffer was DOING pass by pass.
//
// The `burst` parameter and EXPERIMENT 3 below are kept, even though the
// hypotheses they were written to test are now closed. They are cheap,
// and they cover rate and id-width shapes nothing else exercises.
//
// IT IS A MODEL, NOT THE BENCH. Spec 9.1: "an acceptance criterion that
// depends on bus load needs a bench run". Nothing here is an acceptance
// criterion. The bench measured the real thing: 100% delivery at 2250,
// 3000 and 4000 fps (firmware/README.md, Stage 1) -- with the driver as
// it stood on 2026-09-11, before this year's age-out and TXnIF changes.

#include <cstdio>
#include <cstring>
#include <vector>

#include "l3_replay.h"
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
    hostclock::attachInt(INT_PIN);
    spi.attach(&chip);
    mcp.begin(500000);
  }
  ~Rig() { hostclock::detach(); }
};

// Offer `fps` standard 8-byte frames for `ms`, servicing the port the way
// the bridge loop does, and report what got out.
// `burst` frames are offered back-to-back, then nothing until the next
// burst, keeping the MEAN rate at `fps`. burst=1 is the steady case.
//
// This exists because "the driver keeps up at 2250 fps" was measured
// with steady input, and real truck traffic is bursty -- frames share a
// timestamp. Stage 1 on silicon was synthetic constant-rate too, so
// bursty traffic at this rate had never been shown to cross the charger
// port anywhere. Testing the steady case and concluding about the bursty
// one was an over-claim, and worth having closed.
//
// It was NOT the explanation for the shortfall, though: burst=4, 8 and
// 16 all passed at 100% while the capture sat at 2.4%, and that gap was
// read as evidence about burst SHAPE when it was really evidence about
// payload LENGTH -- every case here sends 8 bytes. A parameter that is
// held constant across every arm of an experiment cannot be ruled out
// by that experiment, and it is the easiest thing to forget is there.
void run(uint32_t fps, uint32_t ms, uint32_t service_every_us,
         uint32_t burst = 1, uint32_t id = 0x200,
         bool ext = false) {
  Rig r;
  const uint64_t period_us = (uint64_t)burst * 1000000ull / fps;
  const uint64_t end_us = r.chip.micros() + (uint64_t)ms * 1000;
  uint64_t next_frame = r.chip.micros();
  uint64_t next_service = r.chip.micros();
  uint32_t offered = 0;
  const uint8_t payload[8] = {1, 2, 3, 4, 5, 6, 7, 8};

  while (r.chip.micros() < end_us) {
    if (r.chip.micros() >= next_frame) {
      for (uint32_t k = 0; k < burst; k++) {
        r.mcp.send(id, ext, payload, 8);
        offered++;
      }
      next_frame += period_us;
    }
    if (r.chip.micros() >= next_service) {
      r.mcp.service(r.chip.millis());
      next_service += service_every_us;
    }
    r.chip.advance(20);
  }
  r.mcp.service(r.chip.millis());

  const uint32_t delivered = r.chip.delivered();
  std::printf("  --  %4u fps burst=%-2u svc %4u us %s: offered %5u, "
              "delivered %5u (%5.1f%%)   aged %5u dropped %5u failed %5u\n",
              fps, burst, service_every_us,
              ext ? "EXT" : "std", offered, delivered,
              100.0 * delivered / (offered ? offered : 1),
              r.mcp.txAged(), r.mcp.txDropped(), r.mcp.txFailed());
}

// Measure the CEILING a single identifier can sustain, as a function of
// how often the port is serviced. Offers far above any plausible
// ceiling, so what comes out is the limit rather than the offer.
//
// `nids` is the control: spec 2's hold binds only when consecutive
// frames share an identifier, so the same sweep over three identifiers
// says how much of the ceiling is the hold and how much is the wire.
// Returns frames delivered per second.
uint32_t ceilingFps(uint32_t svc_us, uint32_t nids, uint32_t ms) {
  Rig r;
  const uint32_t OFFER_FPS = 6000;
  const uint64_t period_us = 1000000ull / OFFER_FPS;
  const uint64_t end_us = r.chip.micros() + (uint64_t)ms * 1000;
  uint64_t next_frame = r.chip.micros();
  uint64_t next_service = r.chip.micros();
  uint32_t offered = 0;
  const uint8_t payload[8] = {1, 2, 3, 4, 5, 6, 7, 8};
  while (r.chip.micros() < end_us) {
    if (r.chip.micros() >= next_frame) {
      r.mcp.send(0x200 + (offered % nids), false, payload, 8);
      offered++;
      next_frame += period_us;
    }
    if (r.chip.micros() >= next_service) {
      r.mcp.service(r.chip.millis());
      next_service += svc_us;
    }
    r.chip.advance(20);
  }
  r.mcp.service(r.chip.millis());
  return (uint32_t)((uint64_t)r.chip.delivered() * 1000 / ms);
}

}  // namespace

int main() {
  std::printf("--- Mcp2515Port throughput, driven directly ---\n");
  std::printf("    (the L3 shortfall was a CHIP FAKE defect, now fixed --\n");
  std::printf("     see finishLoadTx(); these stay as rate coverage)\n\n");

  // The rate the truck actually runs at, and the rates Stage 1 measured
  // 100% delivery on silicon.
  run(2250, 1000, 200);
  run(3000, 1000, 200);
  run(4000, 1000, 200);

  std::printf("\n    the same rate, serviced more and less often:\n");
  run(2250, 1000, 50);
  run(2250, 1000, 1000);

  // THE REAL QUESTION. Real truck traffic shares timestamps -- frames
  // arrive in bursts, not one per period. Neither this probe nor Stage 1
  // on silicon had ever offered it that way, so "the driver keeps up at
  // 2250 fps" was a claim about steady input only. Same MEAN rate here,
  // delivered in bursts.
  std::printf("\n    THE REAL QUESTION: the same mean rate, in BURSTS\n");
  run(2250, 1000, 200, 4);
  run(2250, 1000, 200, 8);
  run(2250, 1000, 200, 16);

  // EXPERIMENT 3: every synthetic case above uses a STANDARD id, and the
  // capture is mostly 29-bit. This is the inverse of swapping the
  // capture's ids out: same steady timing, extended id.
  std::printf("\n    EXPERIMENT 3: steady rate with a 29-bit EXTENDED id\n");
  run(2250, 1000, 200, 1, 0x18FFD4C0, true);
  run(2250, 1000, 200, 4, 0x18FFD4C0, true);

  std::printf("\n    well below the ring's fill rate, as a control:\n");
  run(200, 1000, 200);

  // --- THE SAME STIMULUS, THROUGH A BARE PORT -------------------------
  //
  // Synthetic bursts pass. So feed the port the ACTUAL capture, at its
  // actual timestamps, with no main.cpp, no core and no bridge loop --
  // only send() and service(). If this reproduces the L3 shortfall the
  // cause is in the port under real traffic; if it does not, the cause
  // is above the port, in main.cpp or in the replay harness.
  {
    l3::Stimulus st = l3::load("l3_stimulus.txt");
    // SERIALISED: the capture's 1 ms timestamp quantisation lets
    // several frames share an instant, which no wire can deliver.
    // See l3::serialise() for what that did to the model.
    st = l3::serialise(st);
    // EVERY ROW OFFERED, none skipped -- see test_l2_bridge for why
    // this one line is here.
    uint32_t rows_v_ = 0;
    for (size_t q_ = 0; q_ < st.rows.size(); q_++)
      if (!st.rows[q_].charger) rows_v_++;
    if (!st.loaded) {
      std::printf("\n  --  (stimulus not loadable here: %s)\n",
                  st.why.c_str());
    } else {
      Rig r;
      size_t i = 0;
      uint32_t offered = 0;
      uint32_t max_dump = 0, passes = 0;
      const uint64_t t0 = r.chip.micros();
      const uint64_t span = st.rows.back().t_us;
      uint64_t next_service = t0;
      // `|| i < rows.size()`: the clock advances in 20 us steps, so
      // elapsed time can step PAST the last row's timestamp without
      // ever equalling it, and the loop would end one frame short.
      // Serialising the stimulus pushed that last timestamp later and
      // made it visible; the offered == rows check below is what
      // caught it.
      while (r.chip.micros() - t0 <= span || i < st.rows.size()) {
        const uint64_t el = r.chip.micros() - t0;
        uint32_t dumped = 0;
        while (i < st.rows.size() && st.rows[i].t_us <= el) {
          const l3::Row& w = st.rows[i++];
          if (!w.charger) {           // vehicle -> charger, as the bridge does
            r.mcp.send(w.id, w.ext, w.data, w.len);
            offered++;
            dumped++;
          }
        }
        if (dumped > max_dump) max_dump = dumped;
        passes++;
        if (r.chip.micros() >= next_service) {
          r.mcp.service(r.chip.millis());
          next_service += 200;
        }
        r.chip.advance(20);
      }
      r.mcp.service(r.chip.millis());
      std::printf("\n    THE CAPTURE ITSELF, port only (no main.cpp):\n");
      std::printf("  --  harness check: %u passes, worst single pass sent "
                  "%u frames into a 64-deep ring\n", passes, max_dump);
      std::printf("  --  offered %5u, delivered %5u (%5.1f%%)   "
                  "aged %5u dropped %5u failed %5u\n",
                  offered, r.chip.delivered(),
                  100.0 * r.chip.delivered() / (offered ? offered : 1),
                  r.mcp.txAged(), r.mcp.txDropped(), r.mcp.txFailed());
      // THE ACCOUNTING MUST CLOSE, and it is assertable at any load --
      // which is what makes it a check rather than a measurement.
      //
      // The only frames legitimately unaccounted are those still resident
      // when the window ends: at most the 64-deep ring plus the chip's
      // three transmit buffers. That is a hard structural bound, not a
      // tolerance chosen to make the numbers fit.
      const uint32_t booked = r.chip.delivered() + r.mcp.txAged() +
                              r.mcp.txDropped() + r.mcp.txFailed();
      const uint32_t resident_max = 64 + 3;
      std::printf("  --  accounting: delivered+aged+dropped+failed = %u "
                  "vs offered %u (gap %d, bound %u)\n",
                  booked, offered, (int)offered - (int)booked, resident_max);
      check(booked <= offered,
            "nothing is booked that was never offered");
      check(offered == rows_v_,
            "the capture replay offered EVERY vehicle row -- a dropping "
            "injector would otherwise report a clean result on a stimulus "
            "it had mostly thrown away");
      check(offered - booked <= resident_max,
            "charger TX accounting CLOSES: every offered frame is "
            "delivered, aged, dropped or failed, bar at most a ring-full "
            "plus three buffers still resident at the end");

      // THE PARAGRAPH THAT USED TO BE HERE WAS WRONG, and it is worth
      // saying so rather than quietly deleting it. It read the dominant
      // txDropped term as the 64-deep ring being overrun by demand that
      // exceeded the wire, called it "congestion collapse rather than
      // graceful degradation", and concluded it was "reachable on the
      // truck". None of that was happening. The ring was full because
      // the three hardware buffers were deadlocked in the fake, so the
      // refill loop could not empty it -- the opposite of too much
      // demand, and with the opposite fix. It was written from totals
      // that were consistent with the story, and it would have sent a
      // bench session hunting a ring policy for a fault that was not in
      // the firmware at all.
      //
      // With the fake fixed, the capture crosses whole: 22,310 of
      // 22,314, nothing aged, dropped or failed.
      //
      // Still NOT asserted as a pass/fail throughput number: spec 9.1
      // puts a load-dependent criterion on the bench. The accounting
      // above is the part that holds at any load.
    }
  }

  // The only assertion: a rate FAR below anything the hardware struggles
  // with must come out essentially whole. If even this leaks, the port
  // is dropping frames for a reason that has nothing to do with load,
  // and that is worth failing on.
  {
    Rig r;
    const uint8_t payload[8] = {9, 8, 7, 6, 5, 4, 3, 2};
    for (int i = 0; i < 50; i++) {
      r.mcp.send(0x201, false, payload, 8);
      for (int k = 0; k < 40; k++) {     // 40 x 200 us = 8 ms per frame
        r.mcp.service(r.chip.millis());
        r.chip.advance(200);
      }
    }
    r.mcp.service(r.chip.millis());
    std::printf("\n  --  125 fps control: offered 50, delivered %u, "
                "aged %u dropped %u failed %u\n",
                r.chip.delivered(), r.mcp.txAged(), r.mcp.txDropped(),
                r.mcp.txFailed());
    check(r.chip.delivered() == 50,
          "at 125 fps, with 8 ms of service per frame, every frame gets "
          "out -- a leak here would be load-independent and real");
  }

  // EVERY DLC, ONE FRAME EACH, WITH TIME TO SPARE.
  //
  // The regression guard for the defect this file's header describes.
  // Nothing else in the suite sent a frame shorter than 8 bytes through
  // the port, so a chip model that mishandled short frames was invisible
  // -- and 26.5% of real charger traffic is DLC < 8.
  //
  // Load-independent and therefore assertable here: one frame at a time,
  // 8 ms of service each, nothing queued behind it. If a DLC fails this
  // it would fail at any rate.
  {
    Rig r;
    const uint8_t payload[8] = {0xA0, 0xA1, 0xA2, 0xA3,
                                0xA4, 0xA5, 0xA6, 0xA7};
    for (uint8_t dlc = 0; dlc <= 8; dlc++) {
      r.mcp.send((uint32_t)(0x300 + dlc), false, payload, dlc);
      for (int k = 0; k < 40; k++) {
        r.mcp.service(r.chip.millis());
        r.chip.advance(200);
      }
    }
    r.mcp.service(r.chip.millis());
    std::printf("\n  --  every DLC 0..8, one frame each: delivered %u of 9, "
                "aged %u dropped %u failed %u\n",
                r.chip.delivered(), r.mcp.txAged(), r.mcp.txDropped(),
                r.mcp.txFailed());
    check(r.chip.delivered() == 9,
          "a frame of EVERY DLC from 0 to 8 reaches the wire -- the guard "
          "for a transmit path that only works for full 8-byte payloads");

    // And the payload must survive, not merely the frame. A model that
    // got the length right while truncating or padding the data would
    // pass the count above and still be wrong.
    // COUNT THE MATCHES, do not merely fail on a mismatch. A loop that
    // only sets a flag when it finds something wrong says "ok" when it
    // finds nothing at all -- and nothing at all is precisely what the
    // defect this guards against produced. Under the 13-byte gate this
    // printed "ok" with 0 frames delivered.
    uint32_t seen = 0;
    bool bytes_ok = true, lens_ok = true;
    const std::vector<mcpfake::WireEvent>& w = r.chip.wire();
    for (size_t k = 0; k < w.size(); k++) {
      const mcpfake::CanFrame& f = w[k].frame;
      if (f.id < 0x300 || f.id > 0x308) continue;
      seen++;
      if (f.len != (uint8_t)(f.id - 0x300)) lens_ok = false;
      for (uint8_t i = 0; i < f.len; i++)
        if (f.data[i] != (uint8_t)(0xA0 + i)) bytes_ok = false;
    }
    check(seen == 9 && lens_ok,
          "all NINE frames reach the wire, each with the DLC it was sent "
          "with and not the DLC of whatever used the buffer before");
    check(seen == 9 && bytes_ok, "and each with its own payload bytes intact");
  }

  // EXPERIMENT 4: WHAT THE SPEC 2 HOLD COSTS, and what sets the limit.
  //
  // The hold lets at most one hardware buffer carry a given identifier,
  // so a single-identifier stream can advance only one frame per
  // service pass, and only once the previous frame is OBSERVED
  // complete. Three buffers stop helping, and the ceiling stops being a
  // property of the wire and becomes a property of the SERVICE PERIOD.
  //
  // THE PREDICTION THIS REPLACED WAS WRONG, and the failure is the
  // interesting part. Two data points (svc 200 us -> 2500 fps, svc 1000
  // us -> 1000 fps) fit `1 / (svc * ceil(frame_time / svc))` exactly,
  // which is a tidy law built on two points. Swept properly it does not
  // hold: the curve is NOT monotonic in the service period -- 250 us is
  // worse than 300 us -- because what matters is how the period ALIGNS
  // with the frame's time on the wire, not how short it is. A model
  // fitted to the two rates that happened to be on hand would have been
  // quoted as a formula and been wrong in the middle of its own range.
  // Hence a measured sweep rather than a closed form.
  std::printf("\n    EXPERIMENT 4: the single-id ceiling vs service period\n");
  std::printf("    (offering 6000 fps, so this is the LIMIT, not the offer)\n");
  {
    const uint32_t svcs[] = {25, 50, 100, 150, 200, 250, 300, 500, 1000};
    const uint32_t ns = (uint32_t)(sizeof(svcs) / sizeof(svcs[0]));
    uint32_t best_one = 0;
    // PAIRWISE, at the same service period. The first draft of this
    // compared the WORST three-id ceiling against the BEST single-id
    // one -- across different service periods -- and failed, because
    // three ids at svc 1000 us (2998) is slower than one id at svc 25
    // us (3799). That comparison was never the claim: it is only a
    // controlled experiment when the service period is held fixed.
    uint32_t compared = 0, three_wins = 0;
    for (uint32_t i = 0; i < ns; i++) {
      const uint32_t one = ceilingFps(svcs[i], 1, 1000);
      const uint32_t three = ceilingFps(svcs[i], 3, 1000);
      std::printf("  --  svc %4u us: one id %4u fps, three ids %4u fps\n",
                  svcs[i], one, three);
      if (one > best_one) best_one = one;
      compared++;
      if (three > one) three_wins++;
    }
    std::printf("  --  best single-id ceiling at ANY service period: %u fps\n",
                best_one);

    // A model property at 4000 fps, the rate an EARLIER plan for the
    // same-identifier bench arm named. Spec 12 entry 2 now offers 2,000 fps
    // (corrected by the tester, 2026-10-07: this line attributed 4000 to the
    // spec), so this says nothing about whether that arm can be met.
    const uint32_t OLD_PLAN_SAMEID_FPS = 4000;
    check(best_one < OLD_PLAN_SAMEID_FPS,
          "RECORDED, NOT A DEFECT: with the spec 2 hold, no service "
          "period reaches 4000 fps for a SINGLE identifier (a superseded "
          "plan's rate; spec 12 entry 2 offers 2,000 fps)");
    // COUNTED, not flagged: `three_wins == compared` alone would pass
    // if the sweep ran zero periods.
    std::printf("  --  three ids beat one id at %u of %u service periods\n",
                three_wins, compared);
    check(compared == ns && three_wins == compared,
          "and the hold is what does it: at EVERY service period, three "
          "identifiers sustain a higher rate than one, so the ceiling is "
          "the hold and not the modelled wire");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_tx_throughput: all checks OK\n");
  return 0;
}
