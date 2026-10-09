// The MCP2515 model, checked against the datasheet behaviours it claims.
//
// This tests the FAKE, not the driver. It has to come first: a fake wrong
// in the same direction as a defect "confirms" the defect, which is the
// failure the gen-inhibit session paid most for. So every datasheet
// behaviour the model asserts is exercised here before any driver code is
// pointed at it.
//
// Build/run: ./run.sh, or by hand with
//   g++ -std=c++11 -O2 -I . -I ../../src -o /tmp/t test_mcp2515_fake.cpp
//       mcp2515_fake.cpp mcp2515_fake_chip.cpp

#include <cstdio>
#include <cstring>

#include "mcp2515_fake_chip.h"

using namespace mcpfake;

static int failures = 0;

static void check(bool cond, const char* what) {
  std::printf(cond ? "  ok  %s\n" : "FAIL  %s\n", what);
  if (!cond) failures++;
}

// --- a minimal driver-shaped SPI caller, so the test speaks instructions --
namespace {

class Spi {
 public:
  explicit Spi(Mcp2515Fake& c) : c_(c) {}

  uint8_t readReg(uint8_t a) {
    c_.csLow();
    c_.transfer(OP_READ);
    c_.transfer(a);
    const uint8_t v = c_.transfer(0);
    c_.csHigh();
    return v;
  }
  void writeReg(uint8_t a, uint8_t v) {
    c_.csLow();
    c_.transfer(OP_WRITE);
    c_.transfer(a);
    c_.transfer(v);
    c_.csHigh();
  }
  void bitModify(uint8_t a, uint8_t mask, uint8_t v) {
    c_.csLow();
    c_.transfer(OP_BIT_MODIFY);
    c_.transfer(a);
    c_.transfer(mask);
    c_.transfer(v);
    c_.csHigh();
  }
  uint8_t status() {
    c_.csLow();
    c_.transfer(OP_READ_STATUS);
    const uint8_t v = c_.transfer(0);
    c_.csHigh();
    return v;
  }
  void load(uint8_t n, uint32_t id, const uint8_t* d, uint8_t len) {
    c_.csLow();
    c_.transfer((uint8_t)(OP_LOAD_TX | (uint8_t)(n * 2)));
    c_.transfer((uint8_t)(id >> 21));
    c_.transfer((uint8_t)(((id >> 13) & 0xE0) | 0x08 | ((id >> 16) & 0x03)));
    c_.transfer((uint8_t)(id >> 8));
    c_.transfer((uint8_t)id);
    c_.transfer(len);
    for (uint8_t i = 0; i < 8; i++) c_.transfer(i < len ? d[i] : 0);
    c_.csHigh();
  }
  void rts(uint8_t n) { rtsMask((uint8_t)(1u << n)); }

  // One RTS instruction can request several buffers at once (p67 Table
  // 12-1, "1000 0nnn"). The driver never does -- loadAndSend() issues one
  // RTS per buffer -- and that difference turns out to matter, see the
  // ordering cases below.
  void rtsMask(uint8_t mask) {
    c_.csLow();
    c_.transfer((uint8_t)(OP_RTS | (mask & 0x07)));
    c_.csHigh();
  }

 private:
  Mcp2515Fake& c_;
};

const uint8_t kData[8] = {1, 2, 3, 4, 5, 6, 7, 8};

// The frame every case below loads: extended, 8 bytes. Needed explicitly
// now that frame time depends on the frame (F2). These cases used to wait
// `frameTimeUs(CanFrame())` -- a default-constructed STANDARD, ZERO-LENGTH
// frame, 110 us -- while loading an extended 8-byte one that takes 320 us.
// That was only ever right because every frame took the same 183 bits.
// Fixing F2 turned it into eight failures, which is the fix working.
CanFrame ext8Frame() {
  CanFrame f;
  memset(&f, 0, sizeof(f));
  f.ext = true;
  f.len = 8;
  return f;
}

}  // namespace

int main() {
  // --- the register file ------------------------------------------------
  {
    Mcp2515Fake c;
    Spi s(c);
    check((s.readReg(R_CANSTAT) & MODE_MASK) == MODE_CONFIG,
          "RESET leaves Configuration mode (p67 Table 12-1)");
    s.writeReg(R_TEC, 0x55);
    check(s.readReg(R_TEC) == 0, "TEC is read-only (p49 Register 6-1)");

    // p63 Table 11-1: CANINTF is shaded, TEC is not.
    s.writeReg(R_CANINTF, 0x00);
    s.bitModify(R_CANINTF, INTF_TX1IF, INTF_TX1IF);
    check(s.readReg(R_CANINTF) == INTF_TX1IF,
          "BIT MODIFY sets one bit of CANINTF and leaves the rest "
          "(p63 Table 11-1, shaded)");
    s.writeReg(R_CANINTE, 0x0F);
    s.bitModify(R_CANINTE, 0x01, 0x00);
    check(s.readReg(R_CANINTE) == 0x0E, "...and of CANINTE");

    // On an unshaded register the mask is forced to FFh and it becomes a
    // byte write (p66 s12.10 note). Silently -- which is the hazard.
    s.writeReg(R_CANCTRL, MODE_CONFIG | 0x07);
    s.bitModify(R_CANSTAT, 0x01, 0x01);
    check((s.readReg(R_CANSTAT) & MODE_MASK) == MODE_CONFIG,
          "BIT MODIFY on CANSTAT does not corrupt the mode (read-only)");
  }

  // --- CNF writable only in Configuration mode (RECALLED) ----------------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CNF1, 0x11);
    check(s.readReg(R_CNF1) == 0x11, "CNF1 writable in Configuration mode");
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.writeReg(R_CNF1, 0x22);
    check(s.readReg(R_CNF1) == 0x11,
          "CNF1 write ignored outside Configuration mode (RECALLED)");
  }

  // --- the transmit engine ----------------------------------------------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.load(0, 0x18FFD4C0, kData, 8);
    s.rts(0);
    check((s.status() & ST_TX0REQ) != 0, "TXREQ set after RTS");
    check((s.status() & ST_TX0IF) == 0, "TX0IF not yet set");

    c.advance(c.frameTimeUs(ext8Frame()) + 10);
    check((s.status() & ST_TX0REQ) == 0,
          "the chip clears TXREQ on success (p15 s3.3)");
    check((s.status() & ST_TX0IF) != 0,
          "and sets TX0IF whether or not TX0IE is enabled (p17 flowchart)");
    check(c.delivered() == 1, "one frame reached the wire");
  }

  // --- p15 s3.2: equal TXP, the HIGHER buffer goes first -----------------
  //
  // All three requested in ONE instruction, so the tie-break is the only
  // thing that can decide. This is the datasheet rule in isolation.
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.load(0, 0x100, kData, 8);
    s.load(1, 0x200, kData, 8);
    s.load(2, 0x300, kData, 8);
    s.rtsMask(0x07);
    c.advance(4 * c.frameTimeUs(ext8Frame()));
    const std::vector<WireEvent>& w = c.wire();
    check(w.size() == 3, "all three transmitted");
    if (w.size() == 3) {
      check(w[0].buffer == 2 && w[1].buffer == 1 && w[2].buffer == 0,
            "equal TXP sends TXB2, TXB1, TXB0 -- newest-first (p15 s3.2)");
      check(w[0].frame.id == 0x300 && w[2].frame.id == 0x100,
            "...so three frames requested together leave in reverse order");
    }
  }

  // --- what the DRIVER actually does, which is not the same --------------
  //
  // `loadAndSend()` issues one RTS per buffer, so TXB0's request reaches an
  // idle controller and starts transmitting BEFORE TXB1 and TXB2 are
  // requested at all. The tie-break then only orders what is left.
  //
  // This refines the gen-inhibit session's hypothesis rather than
  // contradicting it: newest-first does not apply to the first frame of a
  // drain, only to the ones queued behind whatever is already on the wire.
  // Which is consistent with the measured transposition being rare, always
  // by one position, and dependent on frame length -- the length decides
  // whether TXB0 is still busy when TXB1 is requested.
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.load(0, 0x100, kData, 8);
    s.load(1, 0x200, kData, 8);
    s.load(2, 0x300, kData, 8);
    s.rts(0);
    s.rts(1);
    s.rts(2);
    c.advance(4 * c.frameTimeUs(ext8Frame()));
    const std::vector<WireEvent>& w = c.wire();
    if (w.size() == 3) {
      check(w[0].buffer == 0,
            "with one RTS per buffer, TXB0 starts before the others are "
            "even requested");
      check(w[1].buffer == 2 && w[2].buffer == 1,
            "...and the tie-break orders only what queued behind it");
    }
  }

  // --- p16 s3.6: clearing TXREQ does not stop a frame in flight ----------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.load(0, 0x111, kData, 8);
    s.rts(0);
    c.advance(50);                      // it is on the wire now
    s.bitModify(R_TXB0CTRL, TXB_TXREQ, 0x00);
    check((s.status() & ST_TX0REQ) == 0,
          "TXREQ reads 0 the instant the driver clears it...");
    check(c.delivered() == 0, "...and the frame has not finished yet");
    check((s.readReg(R_TXB0CTRL) & TXB_ABTF) == 0,
          "clearing TXREQ does NOT set ABTF (p16 s3.6 note 2)");

    c.advance(c.frameTimeUs(ext8Frame()));
    check(c.delivered() == 1,
          "the frame already transmitting COMPLETES (p16 s3.6 note 1)");
    check((s.status() & ST_TX0IF) != 0,
          "and TX0IF marks it delivered -- which is how the fix tells "
          "'abandoned' from 'delivered late'");
  }

  // --- the same abort BEFORE transmission starts -------------------------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.load(0, 0x111, kData, 8);
    s.load(1, 0x222, kData, 8);
    s.rts(1);                           // TXB1 takes the wire
    c.advance(50);
    s.rts(0);                           // TXB0 queued behind it
    s.bitModify(R_TXB0CTRL, TXB_TXREQ, 0x00);
    c.advance(4 * c.frameTimeUs(ext8Frame()));
    bool saw0 = false;
    for (size_t i = 0; i < c.wire().size(); i++)
      if (c.wire()[i].buffer == 0) saw0 = c.wire()[i].delivered;
    check(!saw0, "a buffer aborted before it started never reaches the wire");
    check((s.status() & ST_TX0IF) == 0,
          "and TX0IF stays clear for it -- 'abandoned'");
  }

  // --- no acknowledgement: retry forever, never bus-off (DEFERRED) -------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    c.setAcknowledged(false);
    s.load(0, 0x18FFD4C0, kData, 8);
    s.rts(0);
    c.advance(1000000);                 // a full second
    check(c.delivered() == 0, "nothing is delivered on a silent segment");
    check((s.status() & ST_TX0REQ) != 0,
          "TXREQ stays set: the chip is still retrying (p47 s6.2)");
    check(c.tec() >= 128, "TEC reaches the error-passive limit (p47 s6.7)");
    check((s.readReg(R_EFLG) & EFLG_TXEP) != 0, "TXEP is set");
    check(!c.busOff() && c.busOffEntries() == 0,
          "and it does NOT reach bus-off -- DEFERRED, the datasheet hands "
          "the increment rules to ISO 11898. If this is wrong on silicon, "
          "bridge_ok is clear on every drive");
  }

  // --- the abort bound, at and either side of it -------------------------
  {
    // The BOUND, not any particular frame's duration -- that distinction
    // is F2. 183 bits covers the longest frame plus an error frame and
    // interframe space.
    const uint64_t bound = Mcp2515Fake().abortBoundUs();
    std::printf("  --  abort bound at 500 kbit/s = %llu us\n",
                (unsigned long long)bound);
    check(bound >= 360 && bound <= 370,
          "183 bits at 500 kbit/s is ~366 us, the abort-resolution bound");

    for (int k = 0; k < 3; k++) {
      const uint64_t wait = bound - 1 + (uint64_t)k;   // -1, exact, +1
      Mcp2515Fake c;
      Spi s(c);
      s.writeReg(R_CANCTRL, MODE_NORMAL);
      s.load(0, 0x111, kData, 8);
      s.rts(0);
      s.bitModify(R_TXB0CTRL, TXB_TXREQ, 0x00);   // abort at once
      c.advance(wait);
      const bool resolved = (s.status() & ST_TX0IF) != 0 || c.delivered() > 0;
      std::printf("  --  wait %llu us (bound%+d): resolved=%d\n",
                  (unsigned long long)wait, k - 1, resolved ? 1 : 0);
      if (k == 2)
        check(resolved,
              "one frame time after the abort, the outcome is known");
    }
  }

  // --- F1: the clock must advance during SPI, at the DRIVER's 10 MHz ----
  //
  // It did not. `8 * 1000000 / spi_hz` is integer ZERO at 10 MHz, which is
  // port_mcp2515.h's default, so no time passed during SPI and every
  // race inside drainTx() was unreachable while looking covered.
  {
    Mcp2515Fake c(500000, 10000000);
    Spi s(c);
    const uint64_t t0 = c.micros();
    for (int i = 0; i < 100; i++) {
      c.csLow();
      c.transfer(OP_READ_STATUS);
      c.csHigh();
    }
    const uint64_t spent = c.micros() - t0;
    std::printf("  --  100 SPI bytes at 10 MHz took %llu us\n",
                (unsigned long long)spent);
    check(spent == 80,
          "F1: 100 SPI bytes at 10 MHz advance the clock by 80 us");
  }

  // --- F2: frame time depends on DLC and on the identifier type ---------
  {
    Mcp2515Fake c;
    CanFrame ext8;
    memset(&ext8, 0, sizeof(ext8));
    ext8.ext = true;
    ext8.len = 8;
    CanFrame ext0 = ext8;
    ext0.len = 0;
    CanFrame std8 = ext8;
    std8.ext = false;
    std::printf("  --  ext/8 %u bits, ext/0 %u bits, std/8 %u bits, "
                "bound %llu us\n",
                c.frameBits(ext8), c.frameBits(ext0), c.frameBits(std8),
                (unsigned long long)c.abortBoundUs());
    check(c.frameBits(ext8) > c.frameBits(ext0),
          "F2: a longer DLC is a longer frame");
    check(c.frameBits(ext8) > c.frameBits(std8),
          "F2: an extended identifier is a longer frame");
    // 160 was the WORST-CASE length, which is what frameBits() used to
    // return for every frame. Since 2026-10-04 it computes the stuffing
    // from the actual bit pattern, so a specific frame is at or below
    // that bound rather than exactly on it. The bound itself is still
    // the right ceiling and is still what MAX_FRAME_BITS rests on.
    check(c.frameBits(ext8) <= 160,
          "F2: an extended 8-byte frame is at most the 160-bit worst "
          "case...");
    check(c.frameBits(ext8) >= 128,
          "F2: ...and at least the 128 unstuffed bits it must contain, "
          "so the exact count is a real computation and not a constant");
    check(c.abortBoundUs() == 366,
          "F2: ...and 183 bits stays the ABORT BOUND, 366 us, not a "
          "frame duration");
  }

  // --- F3: a completion is stamped when it happened ---------------------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    CanFrame f;
    memset(&f, 0, sizeof(f));
    f.ext = true;
    f.len = 8;
    const uint64_t want = c.frameTimeUs(f);
    s.load(0, 0x18FFD4C0, kData, 8);
    const uint64_t t0 = c.micros();
    s.rts(0);
    c.advance(10000);
    check(c.wire().size() == 1, "F3: one frame on the wire");
    if (c.wire().size() == 1) {
      const uint64_t at = c.wire()[0].t_us - t0;
      std::printf("  --  stamped at +%llu us, frame time %llu us\n",
                  (unsigned long long)at, (unsigned long long)want);
      // Allow the SPI bytes of the RTS instruction, not a 10 us chunk.
      check(at <= want + 2,
            "F3: stamped at the completion instant, not a chunk boundary");
    }
  }

  // --- F4: selection runs again before every attempt --------------------
  //
  // p15 s3.2: "Prior to sending the SOF, the priority of all buffers that
  // are queued for transmission is compared." A failed attempt must put
  // the buffer back in the pool, not retry itself -- which decides which
  // frame meets a charger that comes back.
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    c.setAcknowledged(false);
    s.load(0, 0x100, kData, 8);
    s.rts(0);
    c.advance(2000);                     // TXB0 is retrying
    s.load(2, 0x300, kData, 8);
    s.rts(2);                            // TXB2 joins, higher number
    c.advance(20000);
    c.setAcknowledged(true);
    c.advance(5000);
    bool first_delivered_is_2 = false;
    for (size_t i = 0; i < c.wire().size(); i++)
      if (c.wire()[i].delivered) {
        first_delivered_is_2 = (c.wire()[i].buffer == 2);
        break;
      }
    check(first_delivered_is_2,
          "F4: after a failed attempt the tie-break re-runs, so TXB2 wins "
          "the next one (p15 s3.2)");
  }


  // --- the INT line: level-triggered, and GATED BY CANINTE --------------
  //
  // p24 s7.0: CANINTE enables which CANINTF flags assert the pin. The
  // driver's service() drains `while (digitalRead(int_) == LOW)`, so this
  // is what makes the receive path run at all -- and the pin went
  // unmodelled entirely until the L2 harness needed it, with every suite
  // green.
  //
  // Checked HERE, against the chip, rather than through L2: ignoring the
  // enable mask only changes how long the driver's drain loop spins,
  // which no L2 assertion can see. Per this folder's standing rule, a
  // datasheet behaviour is asserted against the model directly.
  {
    Mcp2515Fake c;
    Spi s2(c);
    s2.writeReg(R_CANCTRL, MODE_NORMAL);
    s2.writeReg(R_CANINTE, 0x00);
    s2.writeReg(R_CANINTF, 0x00);
    check(!c.intAsserted(), "INT is released with nothing pending");

    s2.writeReg(R_CANINTF, INTF_RX0IF);
    check(!c.intAsserted(),
          "a pending flag with CANINTE clear does NOT assert INT "
          "(p24 s7.0: the enable mask gates it)");

    s2.writeReg(R_CANINTE, INTF_RX0IF);
    check(c.intAsserted(), "...and enabling it does assert INT");

    s2.writeReg(R_CANINTF, 0x00);
    check(!c.intAsserted(),
          "INT is LEVEL-triggered: it releases when the flag clears");

    // A flag that is pending but not enabled must not hold the line.
    s2.writeReg(R_CANINTF, INTF_TX0IF);
    check(!c.intAsserted(),
          "a TX flag with only RX enabled leaves INT released -- "
          "otherwise the drain loop would spin to its guard every pass");
  }

  // --- F5: the model does not claim bus-off it cannot reach -------------
  {
    int n = 0;
    const Assumption* a = assumptions(&n);
    bool has_not_modelled = false;
    bool busoff_is_datasheet = false;
    for (int i = 0; i < n; i++) {
      if (a[i].status == NOT_MODELLED) has_not_modelled = true;
      if (a[i].status == DATASHEET && strstr(a[i].what, "BUS-OFF"))
        busoff_is_datasheet = true;
    }
    // The table must still carry its gaps explicitly rather than omitting
    // them. This check used to be labelled "bus-off is declared NOT
    // MODELLED"; bus-off IS modelled as of 2026-10-04, and the label was
    // left describing something that had stopped being true while the
    // check itself still passed on a different entry.
    check(has_not_modelled,
          "F5: the table still states its gaps explicitly (>=1 NOT_MODELLED)");
    check(busoff_is_datasheet,
          "bus-off is now claimed as DATASHEET -- and test_mcp2515_busoff "
          "is what entitles the table to say so");

    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    c.setAcknowledged(false);
    s.load(0, 0x111, kData, 8);
    s.rts(0);
    c.advance(2000000);
    check(!c.busOff() && c.busOffEntries() == 0,
          "F5: ...and the NO-ACK path indeed never reaches bus-off, which stays true now that a bus-error source can");

    // EWARN/TXEP follow the counter rather than latching where they were set.
    c.setAcknowledged(true);
    c.advance(200000);
    check((s.readReg(R_EFLG) & EFLG_TXEP) == 0,
          "F5: TXEP clears once TEC falls back below 128");
  }

  // --- F6: reloading a buffer whose frame is still on the wire ----------
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    s.load(0, 0x111, kData, 8);
    s.rts(0);
    c.advance(50);                           // on the wire
    s.bitModify(R_TXB0CTRL, TXB_TXREQ, 0x00);  // our abort
    const uint8_t other[8] = {9, 9, 9, 9, 9, 9, 9, 9};
    s.load(0, 0x222, other, 8);              // ...and reload at once
    c.advance(5000);
    check(c.reloadsWhileSending() == 1,
          "F6: a reload while the frame is on the wire is COUNTED, not "
          "silently absorbed");
    if (!c.wire().empty())
      check(c.wire()[0].frame.id == 0x111,
            "F6: and under RELOAD_IGNORED the wire carries the frame that "
            "was there at SOF (a named choice -- the outcome is UNKNOWN)");
  }

  // --- receive buffers: occupancy, rollover, READ RX addressing ----------
  //
  // Added 2026-10-08 with the rollover conformance step (C12). Each case
  // asserts a datasheet behaviour on the chip directly, as this file's
  // rule requires. The A/B/C case is the reviewer's receive-order case
  // (tracker "RX rollover order") at chip level: after A is taken, C lands
  // in RXB0 while the OLDER B sits in RXB1.
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_RXB0CTRL, 0x64);          // RXM = 11, BUKT
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    CanFrame f = ext8Frame();
    f.id = 0x18FF60E5;
    // A plain READ of data byte 0 of RXB0 (0x66) / RXB1 (0x76): looks
    // without freeing.
    f.data[0] = 0xA0; c.deliverFrame(f);
    f.data[0] = 0xB0; c.deliverFrame(f);
    check(c.rxFull(0) && c.rxFull(1) && s.readReg(0x66) == 0xA0 &&
          s.readReg(0x76) == 0xB0,
          "A then B, neither read: A in RXB0, B rolled into RXB1 (p23 "
          "s4.2.1), and a plain READ of 0x66 / 0x76 returns them");
    check(c.rxFull(0) && c.rxFull(1),
          "...and READ did not free either buffer (only RXnIF does)");

    // READ RX with n=0, m=1 (0x92) starts at RXB0D0.
    c.csLow();
    c.transfer((uint8_t)(OP_READ_RX | 0x02));
    const uint8_t d0 = c.transfer(0);
    c.csHigh();
    check(d0 == 0xA0, "READ RX 0x92 (n=0, m=1) starts at RXB0D0 (p68 "
                      "Fig 12-3); before 2026-10-08 m was ignored");
    check(!c.rxFull(0) && c.rxFull(1),
          "...and raising CS cleared RX0IF only, freeing RXB0 alone "
          "(p65 s12.4)");
    f.data[0] = 0xC0; c.deliverFrame(f);
    check(s.readReg(0x66) == 0xC0 && s.readReg(0x76) == 0xB0,
          "C arriving now lands in RXB0, NEWER than the B in RXB1 -- the "
          "chip state the receive-order case turns on");

    // Overflow flags (p26 Fig 4-3).
    f.data[0] = 0xD0; c.deliverFrame(f);
    check((s.readReg(R_EFLG) & (EFLG_RX0OVR | EFLG_RX1OVR)) == EFLG_RX1OVR,
          "both full, BUKT set: the frame rolls to a full RXB1 and sets "
          "RX1OVR, not RX0OVR (p26 Fig 4-3)");
    check(s.readReg(0x66) == 0xC0 && s.readReg(0x76) == 0xB0 &&
          c.rxOverflowCount() == 1,
          "...and D is lost: neither buffer overwritten, one counted");

    // BIT MODIFY clearing RX1IF frees RXB1 (p23 s4.1.3).
    s.bitModify(R_CANINTF, INTF_RX1IF, 0x00);
    check(c.rxFull(0) && !c.rxFull(1),
          "BIT MODIFY clearing RX1IF frees RXB1 without a READ RX "
          "(p23 s4.1.3: the flag IS the lockout)");
    f.data[0] = 0xE0; c.deliverFrame(f);
    check(s.readReg(0x76) == 0xE0,
          "...so the next frame rolls into it");

    // BUKT = 0: a frame for a full RXB0 is lost and sets RX0OVR.
    s.writeReg(R_EFLG, 0x00);
    s.writeReg(R_CANINTF, 0x00);
    s.writeReg(R_RXB0CTRL, 0x60);          // BUKT clear
    f.data[0] = 0xF0; c.deliverFrame(f);
    f.data[0] = 0xF1; c.deliverFrame(f);
    check((s.readReg(R_EFLG) & (EFLG_RX0OVR | EFLG_RX1OVR)) == EFLG_RX0OVR &&
          !c.rxFull(1) && s.readReg(0x66) == 0xF0,
          "BUKT clear: the second frame does not roll over, it is lost and "
          "sets RX0OVR (p26 Fig 4-3)");
  }

  // --- the deliverWhenFreed() hook, which the L2 order case relies on ----
  {
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_RXB0CTRL, 0x64);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    CanFrame f = ext8Frame();
    f.id = 0x18FF60E5;
    f.data[0] = 0xA0; c.deliverFrame(f);
    f.data[0] = 0xC0; c.deliverWhenFreed(0, f);
    s.readReg(0x66);
    s.status();
    check(!c.firedWhenFreed(0) && s.readReg(0x66) == 0xA0,
          "hook: a READ or READ STATUS does not free RXB0, so C waits");
    s.bitModify(R_CANINTF, INTF_RX0IF, 0x00);
    check(c.firedWhenFreed(0) && c.rxFull(0) && s.readReg(0x66) == 0xC0,
          "hook: clearing RX0IF by BIT MODIFY delivers C at that instant");
    f.data[0] = 0xC1; c.deliverWhenFreed(0, f);
    c.csLow();
    c.transfer(OP_READ_RX);
    c.transfer(0);
    check(!c.firedWhenFreed(0), "hook: not yet while CS is still low");
    c.csHigh();
    check(c.firedWhenFreed(0) && s.readReg(0x66) == 0xC1,
          "hook: READ RX delivers it when CS is raised");
  }
  {
    // Both buffers armed: the refill interleaving (C into RXB0, then D
    // into RXB1 beside it). The hook records the other buffer's state
    // and the firing order, which is how a case proves its setup.
    Mcp2515Fake c;
    Spi s(c);
    s.writeReg(R_RXB0CTRL, 0x64);
    s.writeReg(R_CANCTRL, MODE_NORMAL);
    CanFrame f = ext8Frame();
    f.id = 0x18FF60E5;
    f.data[0] = 0xA0; c.deliverFrame(f);
    f.data[0] = 0xB0; c.deliverFrame(f);
    f.data[0] = 0xC0; c.deliverWhenFreed(0, f);
    f.data[0] = 0xD0; c.deliverWhenFreed(1, f);
    s.bitModify(R_CANINTF, INTF_RX0IF, 0x00);     // take A
    s.bitModify(R_CANINTF, INTF_RX1IF, 0x00);     // take B
    check(c.firedSeq(0) == 1 && c.firedSeq(1) == 2,
          "hook: firedSeq orders the deliveries (C first, then D)");
    check(c.otherFullWhenFired(0) && c.otherFullWhenFired(1),
          "hook: C arrived with B in RXB1, D arrived with C in RXB0");
    check(s.readReg(0x66) == 0xC0 && s.readReg(0x76) == 0xD0,
          "hook: so RXB0 holds C and the NEWER D sits in RXB1");
    Mcp2515Fake c2;
    Spi s2(c2);
    s2.writeReg(R_RXB0CTRL, 0x64);
    f.data[0] = 0xA0; c2.deliverFrame(f);
    f.data[0] = 0xC0; c2.deliverWhenFreed(0, f);
    s2.bitModify(R_CANINTF, INTF_RX0IF, 0x00);
    check(c2.firedWhenFreed(0) && !c2.otherFullWhenFired(0),
          "hook: otherFullWhenFired is false when the other buffer was "
          "empty -- it is not a constant");
  }

  // --- what this run rests on, printed from the model's own table --------
  std::printf("\nWhat this model rests on:\n");
  int n = 0;
  const Assumption* a = assumptions(&n);
  for (int i = 0; i < n; i++) {
    if (a[i].status == DATASHEET || a[i].status == MEASURED) continue;
    // NOT_MODELLED prints loudest: it is the one that says the model does
    // less than a reader might assume.
    std::printf("  [%-9s] %s\n              (%s)\n",
                statusName(a[i].status), a[i].what, a[i].cite);
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_mcp2515_fake: all checks OK\n");
  return 0;
}
