// The real `Mcp2515Port`, driven against the chip model.
//
// This is the test the transmit-failure fix exists for. Written to FAIL
// against the driver as it stands, so that when it passes we know the fix
// did it, and not the test.
//
// The defect (review sessions, 2026-10-04; gen-inhibit B6):
//
//   (a) the age-out abort increments tx_failed_ the instant it clears
//       TXREQ, but DS20001801J p16 s3.6 note 1 says a message already
//       transmitting completes -- so a DELIVERED frame can be counted as
//       a failure;
//   (b) the completion branch counts ABTF|TXERR on a buffer that has gone
//       idle, but with no ABAT and no one-shot the chip never sets ABTF
//       (p16 s3.6 note 2) and clears TXREQ only on success (p15 s3.3) --
//       so every buffer that branch inspects was delivered, and it can
//       never count a real failure.
//
// Spec 2.2's counter means "frames that did not reach the wire" (user,
// 2026-10-04), so (a) overcounts and (b) is dead.

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
const uint8_t kData[8] = {0xDE, 0xAD, 0xBE, 0xEF, 1, 2, 3, 4};

// One rig: chip model, the Arduino/SPI shims pointed at it, and the real
// driver on top.
struct Rig {
  Mcp2515Fake chip;
  SPIClass spi;
  Mcp2515Port port;

  Rig() : spi(0), port(spi, CS_PIN, INT_PIN) {
    hostclock::attach(&chip, CS_PIN);
    spi.attach(&chip);
    port.begin(500000);
  }
  ~Rig() { hostclock::detach(); }

  // Run the driver's service loop until the clock has advanced `us`, the
  // way the bridge loop does -- repeatedly, not once.
  //
  // Targets the CLOCK, not a count of grants. service() issues SPI, and
  // since F1 was fixed SPI costs time: at 10 MHz a service pass of ~30
  // bytes is ~24 us, so granting 50 us per iteration for 1,980 iterations
  // reached 111 ms of virtual time rather than 99 ms -- straight past the
  // 100 ms age-out the test was trying to stop short of. Counting grants
  // only worked while the SPI clock was broken.
  void run(uint64_t us, uint64_t step = 50) {
    const uint64_t until = chip.micros() + us;
    while (chip.micros() < until) {
      const uint64_t left = until - chip.micros();
      chip.advance(left < step ? left : step);
      if (chip.micros() >= until) break;
      port.service(chip.millis());
    }
  }
};

}  // namespace

int main() {
  // --- the rig itself works ---------------------------------------------
  {
    Rig r;
    r.chip.setAcknowledged(true);
    check(r.port.send(0x18FFD4C0, true, kData, 8), "send() queues a frame");
    r.run(5000);
    check(r.chip.delivered() == 1, "and the chip delivers it");
    check(r.port.txFailed() == 0, "with no failure counted");
    check(r.port.txAged() == 0, "and nothing aged out");
  }

  // --- (a) a frame delivered after the abort is counted as a failure -----
  //
  // The segment acknowledges, but slowly enough that the age-out fires
  // while a frame is on the wire. Modelled by letting the frame start and
  // then holding the driver's clock past 100 ms before it completes: the
  // driver aborts, the chip finishes the frame anyway, and the wire shows
  // a delivery that the driver has already called a failure.
  {
    Rig r;
    r.chip.setAcknowledged(false);        // nothing acknowledges yet
    r.port.send(0x18FFD4C0, true, kData, 8);
    r.run(99000);                          // just under the age-out
    check(r.port.txFailed() == 0, "(a) nothing counted before the age-out");

    // The charger comes back the instant before the driver gives up, so
    // the attempt in flight will succeed.
    r.chip.setAcknowledged(true);
    r.run(3000);

    const uint32_t delivered = r.chip.delivered();
    const uint32_t failed = r.port.txFailed();
    std::printf("  --  wire delivered=%u, driver txFailed=%u, txAged=%u\n",
                delivered, failed, r.port.txAged());
    check(!(delivered >= 1 && failed >= 1),
          "(a) a frame the wire delivered is NOT also counted as failed");
  }

  // --- (b) the completion branch can never see a real failure -----------
  //
  // A healthy segment, many frames, no aborts. If ABTF/TXERR could ever
  // mark a delivered buffer as failed, it would show here.
  {
    Rig r;
    r.chip.setAcknowledged(true);
    for (int i = 0; i < 20; i++) {
      r.port.send(0x18EFC000u + i, true, kData, 8);
      r.run(1000);
    }
    r.run(5000);
    check(r.chip.delivered() == 20, "(b) all 20 delivered");
    check(r.port.txFailed() == 0,
          "(b) and none counted failed on a healthy segment");
  }

  // --- the real failure IS counted: nothing ever acknowledges -----------
  {
    Rig r;
    r.chip.setAcknowledged(false);
    r.port.send(0x18FFD4C0, true, kData, 8);
    r.run(150000);                         // past the 100 ms age-out
    check(r.chip.delivered() == 0, "nothing reached the wire");
    check(r.port.txFailed() >= 1,
          "a frame that never reached the wire IS counted failed");
  }

  // --- three buffers aborting in one pass, the last right on the bound --
  //
  // The margin between the 366 us bound and the 400 us wait is only worth
  // its worst case, and the worst case is three aborts in a single
  // drainTx() pass: each BIT MODIFY costs SPI time, so the third buffer's
  // TXREQ goes down tens of microseconds after the first's. Stamping all
  // three from one `micros()` taken before the loop would start the third
  // one's clock early and resolve it before its frame could have finished.
  {
    Rig r;
    r.chip.setAcknowledged(false);
    for (int i = 0; i < 3; i++) {
      r.port.send(0x18EFC000u + i, true, kData, 8);
      r.run(200);
    }
    // Let all three hardware buffers fill and age out together.
    r.run(130000);
    std::printf("  --  three-abort pass: txFailed=%u txLate=%u "
                "unexplained=%u delivered=%u\n",
                r.port.txFailed(), r.port.txLate(),
                r.port.txUnexplained(), r.chip.delivered());
    check(r.chip.delivered() == 0, "three aborts: nothing was delivered");
    check(r.port.txLate() == 0,
          "...so none of the three is counted as a late delivery");
    check(r.port.txFailed() >= 3,
          "...and all three are counted as failures");
    check(r.port.txUnexplained() == 0,
          "...with no buffer reaching the state p15 s3.3 forbids");
  }

  // --- the control: recovery BEFORE the age-out counts nothing ----------
  //
  // Written first as "the segment comes back mid-abort", which it does
  // not: the charger recovers at 99 ms, the in-flight frame completes in
  // 320 us, and the 100 ms age-out never fires. So nothing is aborted and
  // the assertions about aborted buffers were about an empty set.
  //
  // Kept, reframed, because the real claim is worth making: a charger that
  // comes back before we give up produces three ordinary deliveries and
  // NO entries in any of the three counters. The mid-abort case is the
  // (a) block above, which is where an abort and a delivery actually
  // overlap.
  {
    Rig r;
    r.chip.setAcknowledged(false);
    for (int i = 0; i < 3; i++) {
      r.port.send(0x18EFC000u + i, true, kData, 8);
      r.run(200);
    }
    r.run(99000);
    r.chip.setAcknowledged(true);     // back before the 100 ms age-out
    r.run(5000);
    std::printf("  --  recovery before the age-out: delivered=%u "
                "txFailed=%u txLate=%u unexplained=%u\n",
                r.chip.delivered(), r.port.txFailed(), r.port.txLate(),
                r.port.txUnexplained());
    check(r.chip.delivered() == 3, "all three delivered once it recovered");
    check(r.port.txFailed() == 0 && r.port.txLate() == 0,
          "...and nothing is counted: no abort ever happened");
    check(r.port.txUnexplained() == 0,
          "...with no buffer reaching the state p15 s3.3 forbids");
  }

  // --- and the ring paths still count -----------------------------------
  {
    Rig r;
    r.chip.setAcknowledged(false);
    // Fill the hardware buffers, then keep queueing so the ring ages.
    for (int i = 0; i < 8; i++) {
      r.port.send(0x18EFC000u + i, true, kData, 8);
      r.run(500);
    }
    r.run(250000);
    std::printf("  --  txFailed=%u txAged=%u txDropped=%u delivered=%u\n",
                r.port.txFailed(), r.port.txAged(), r.port.txDropped(),
                r.chip.delivered());
    check(r.chip.delivered() == 0, "silent segment: nothing delivered");
    check(r.port.txAged() + r.port.txFailed() + r.port.txDropped() >= 8,
          "every queued frame is accounted for as aged, failed or dropped");
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_tx_failed: all checks OK\n");
  return 0;
}
