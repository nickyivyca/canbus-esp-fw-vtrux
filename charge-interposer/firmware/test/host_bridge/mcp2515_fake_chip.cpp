#include "mcp2515_fake_chip.h"

namespace mcpfake {

void Mcp2515Fake::reset(bool power_on) {
  memset(regs_, 0, sizeof(regs_));
  // p63 Table 11-2 POR/Reset column; p67 Table 12-1: RESET "sets
  // Configuration mode".
  for (uint8_t a = 0x0E; a < 0x80; a = (uint8_t)(a + 0x10))
    regs_[a] = MODE_CONFIG;
  for (uint8_t a = 0x0F; a < 0x80; a = (uint8_t)(a + 0x10))
    regs_[a] = MODE_CONFIG | 0x07;
  for (int n = 0; n < 3; n++) tx_[n] = TxBuf();
  rx_reads_ = 0;
  tx_loads_ = 0;
  tx_never_started_ = 0;
  max_load_to_sof_ns_ = 0;
  tec_ = 0;
  bus_off_recover_ns_ = 0;
  if (power_on) {
    wire_.clear();
    bus_errors_on_ = false;
    bus_off_entries_ = 0;
    reloads_while_sending_ = 0;
    same_id_coresident_ = 0;
    rx_overflow_count_ = 0;
    t_ns_ = 0;
  }
}

uint32_t Mcp2515Fake::delivered() const {
  uint32_t n = 0;
  for (size_t i = 0; i < wire_.size(); i++)
    if (wire_[i].delivered) n++;
  return n;
}

// CAN 2.0B on the wire. Standard framing is 44 bits, extended 64, plus 8
// per data byte. Stuffing applies from SOF through the CRC -- the last 10
// bits (ACK slot, ACK delimiter, EOF) are fixed-form -- and worst case
// inserts one bit per four, which is the bound rather than the typical
// case. Plus 3 bits of interframe space.
//
// Worst case on purpose: this number decides how long an abort window
// stays open, and understating a frame's time on the wire would make the
// window look safer than it is. F2 -- it used to return 183 for every
// frame, which made DLC irrelevant and so made the measured,
// length-dependent transposition impossible to reproduce.
// The EXACT number of bits this frame occupies, stuffing computed from
// the actual bit pattern rather than assumed.
//
// It used to charge worst-case stuffing -- one bit per four of the
// stuffable span -- on the stated grounds that "overstating a frame's
// time on the wire is the safe direction". That is true for the ABORT
// WINDOW, which is why `MAX_FRAME_BITS` still uses the worst case. It is
// the UNSAFE direction for THROUGHPUT: it shrinks the modelled capacity
// of the wire, and over a real capture it overstated the total by 10.8%,
// which made a feasible traffic pattern look like congestion.
//
// Nothing here needs to be assumed: the id, DLC and data are all in
// hand. Measured against the L3 capture, this removes an ASSUMED row
// from the status table instead of tuning it
// (`notes/artifacts/interposer-firmware/l3_feasibility.py`).
//
// Layout per CAN 2.0B. The stuffable span runs from SOF through the CRC
// sequence; the CRC delimiter, ACK slot, ACK delimiter and EOF are
// fixed-form and never stuffed. Three bits of interframe space follow.
uint32_t Mcp2515Fake::frameBits(const CanFrame& f) const {
  return canFrameBits(f);
}

uint32_t canFrameBits(const CanFrame& f) {
  uint8_t bits[256];
  uint32_t n = 0;
  bits[n++] = 0;                                   // SOF, dominant
  const uint32_t len = f.rtr ? 0 : (f.len > 8 ? 8 : f.len);

  if (!f.ext) {
    for (int i = 10; i >= 0; i--) bits[n++] = (uint8_t)((f.id >> i) & 1);
    bits[n++] = (uint8_t)(f.rtr ? 1 : 0);          // RTR
    bits[n++] = 0;                                 // IDE dominant
    bits[n++] = 0;                                 // r0
  } else {
    for (int i = 28; i >= 18; i--) bits[n++] = (uint8_t)((f.id >> i) & 1);
    bits[n++] = 1;                                 // SRR recessive
    bits[n++] = 1;                                 // IDE recessive
    for (int i = 17; i >= 0; i--) bits[n++] = (uint8_t)((f.id >> i) & 1);
    bits[n++] = (uint8_t)(f.rtr ? 1 : 0);          // RTR
    bits[n++] = 0;                                 // r1
    bits[n++] = 0;                                 // r0
  }
  for (int i = 3; i >= 0; i--) bits[n++] = (uint8_t)((len >> i) & 1);
  for (uint32_t b = 0; b < len; b++)
    for (int i = 7; i >= 0; i--)
      bits[n++] = (uint8_t)((f.data[b] >> i) & 1);

  // CRC-15, polynomial 0x4599, over everything so far.
  uint16_t crc = 0;
  for (uint32_t i = 0; i < n; i++) {
    const uint8_t inv = (uint8_t)(bits[i] ^ ((crc >> 14) & 1));
    crc = (uint16_t)((crc << 1) & 0x7FFF);
    if (inv) crc ^= 0x4599;
  }
  for (int i = 14; i >= 0; i--) bits[n++] = (uint8_t)((crc >> i) & 1);

  // Stuffing: after five consecutive identical bits, one of opposite
  // polarity is inserted, and the inserted bit restarts the count.
  uint32_t stuffed = 0;
  uint32_t run = 1;
  for (uint32_t i = 1; i < n; i++) {
    if (bits[i] == bits[i - 1]) {
      if (++run == 5) { stuffed++; run = 1; }
    } else {
      run = 1;
    }
  }
  // CRC delimiter, ACK slot, ACK delimiter, EOF(7), IFS(3).
  return n + stuffed + 13;
}

uint8_t Mcp2515Fake::readReg(uint8_t a) const {
  if ((a & 0x0F) == 0x0E) return regs_[R_CANSTAT];
  if ((a & 0x0F) == 0x0F) return regs_[R_CANCTRL];
  return regs_[a];
}

void Mcp2515Fake::setMode(uint8_t m) {
  // A chip that never reports the requested mode: the driver polls
  // CANSTAT and gives up, so begin() returns false. Models "the charger
  // controller did not start" without needing a second kind of fake.
  if (refuse_mode_) return;
  regs_[R_CANSTAT] = (uint8_t)((regs_[R_CANSTAT] & ~MODE_MASK) | m);
}

// EWARN, TXEP and TXBO follow the counters rather than being latched where
// they happen to be set (F5). Thresholds from p47 s6.7 and p48 Fig 6-1.
void Mcp2515Fake::refreshErrorFlags() {
  uint8_t e = (uint8_t)(regs_[R_EFLG] & ~(EFLG_EWARN | EFLG_TXEP | EFLG_RXEP));
  const uint8_t tec = regs_[R_TEC];
  const uint8_t rec = regs_[R_REC];
  if (tec >= 96 || rec >= 96) e |= EFLG_EWARN;
  if (tec >= 128) e |= EFLG_TXEP;
  if (rec >= 128) e |= EFLG_RXEP;
  regs_[R_EFLG] = e;
}

// The TEC register is 8 bits, but bus-off is entered when TEC EXCEEDS 255
// (p47 s6.7), which 8 bits cannot represent. The true count is kept in
// tec_ and the register carries the saturated value the MCU can read.
void Mcp2515Fake::tecSet(uint16_t v) {
  tec_ = v;
  regs_[R_TEC] = (uint8_t)(v > 255 ? 255 : v);
}

// A frame arrives from the segment.
//
// p27 s4.2.1 and the RXB0CTRL.BUKT bit: RXB0 takes it, and if RXB0 is
// already full and BUKT is set it rolls into RXB1. With both full the
// frame is lost -- which is the REAL mechanism behind rxDropped(), as
// opposed to injectRxOverrun() poking the flag directly.
void Mcp2515Fake::deliverFrame(const CanFrame& f) {
  // p47 s6.6: in bus-off "messages can neither be received or
  // transmitted", so a frame on the wire does not reach the buffers at
  // all and is not an overrun either -- nothing was overrun, the
  // controller simply was not listening.
  if (regs_[R_EFLG] & EFLG_TXBO) return;

  // Acceptance filtering is not modelled: every frame meets RXB0's
  // criteria, as with RXM = 11 (p27 Register 4-1).
  if (!rxFull(0)) {
    storeRx(0, f);
    regs_[R_CANINTF] |= INTF_RX0IF;
    return;
  }
  // p26 Figure 4-3: RX0IF = 1 -> "BUKT = 1?" -- No sets RX0OVR; Yes joins
  // the RXB1 path, where "RX1IF = 0?" -- No sets RX1OVR. So a frame that
  // rolled over into a full RXB1 sets RX1OVR. (Until 2026-10-08 this set
  // RX0OVR and the status table called the flag UNKNOWN; the flowchart
  // settles it, read from the rendered page.) The driver sums both bits,
  // so rxDropped() is unchanged either way.
  if (!(regs_[R_RXB0CTRL] & RXB0_BUKT)) {
    rx_overflow_count_++;
    regs_[R_EFLG] |= EFLG_RX0OVR;
    return;
  }
  if (!rxFull(1)) {
    storeRx(1, f);
    regs_[R_CANINTF] |= INTF_RX1IF;
    return;
  }
  rx_overflow_count_++;
  regs_[R_EFLG] |= EFLG_RX1OVR;
}

// The MAB's content written into RXBn's registers, SIDH..D7 (p23 note:
// "the entire receive buffer is overwritten with the MAB contents").
// RXB0 is 0x61-0x6D and RXB1 0x71-0x7D (p68 Figure 12-3's address table).
// The encoding is the one READ RX staged before 2026-10-08, unchanged.
void Mcp2515Fake::storeRx(int n, const CanFrame& f) {
  uint8_t* r = &regs_[n ? 0x71 : 0x61];
  memset(r, 0, 13);
  if (f.ext) {
    r[0] = (uint8_t)(f.id >> 21);
    r[1] = (uint8_t)(((f.id >> 13) & 0xE0) | 0x08 | ((f.id >> 16) & 0x03));
    r[2] = (uint8_t)(f.id >> 8);
    r[3] = (uint8_t)f.id;
    r[4] = (uint8_t)((f.len & 0x0F) | (f.rtr ? 0x40 : 0x00));
  } else {
    r[0] = (uint8_t)(f.id >> 3);
    r[1] = (uint8_t)(((f.id & 0x07) << 5) | (f.rtr ? 0x10 : 0));
    r[4] = (uint8_t)(f.len & 0x0F);
  }
  for (uint8_t i = 0; i < f.len && i < 8; i++) r[5 + i] = f.data[i];
}

// Called after anything that can change CANINTF. A buffer whose RXnIF
// went 1 -> 0 is free from this instant (p23 s4.1.3), which is when a
// deliverWhenFreed() frame for it arrives.
void Mcp2515Fake::rxFlagsChanged(uint8_t before) {
  const uint8_t after = regs_[R_CANINTF];
  for (int n = 0; n < 2; n++) {
    const uint8_t bit = n ? INTF_RX1IF : INTF_RX0IF;
    if (!(before & bit) || (after & bit) || !pend_armed_[n]) continue;
    pend_armed_[n] = false;
    pend_fired_[n] = true;
    pend_other_full_[n] = rxFull(1 - n);
    pend_seq_[n] = ++fire_seq_;
    deliverFrame(pend_[n]);
  }
}

void Mcp2515Fake::enterBusOff() {
  if (regs_[R_EFLG] & EFLG_TXBO) return;    // already there; not an entry
  regs_[R_EFLG] |= EFLG_TXBO;
  bus_off_entries_++;
  // p47 note box: the MCP2515 "will recover back to error-active without
  // any intervention by the MCU if the bus remains idle for 128 x 11 bit
  // times". No driver call is involved, unlike the TWAI side.
  bus_off_recover_ns_ = t_ns_ + busOffRecoveryNs();
  // p47 s6.6: during bus-off "messages can neither be received or
  // transmitted", so whatever was mid-air is not going to complete.
  for (int n = 0; n < 3; n++) tx_[n].sending = false;
}

void Mcp2515Fake::recoverFromBusOff() {
  if (!(regs_[R_EFLG] & EFLG_TXBO)) return;
  // p48 Figure 6-1: the edge out of Bus-Off goes to ERROR-ACTIVE, not to
  // error-passive, so both counters clear rather than TEC merely dropping
  // under the bus-off limit.
  regs_[R_EFLG] = (uint8_t)(regs_[R_EFLG] & ~EFLG_TXBO);
  tecSet(0);
  regs_[R_REC] = 0;
  refreshErrorFlags();
}

void Mcp2515Fake::writeReg(uint8_t a, uint8_t v) {
  if ((a & 0x0F) == 0x0F) {
    regs_[R_CANCTRL] = v;
    setMode((uint8_t)(v & MODE_MASK));
    return;
  }
  if ((a & 0x0F) == 0x0E) return;    // CANSTAT is read-only
  if (a == R_TEC || a == R_REC) return;

  if (a == R_CNF1 || a == R_CNF2 || a == R_CNF3) {
    if ((regs_[R_CANSTAT] & MODE_MASK) != MODE_CONFIG) return;  // RECALLED
  }

  if (a >= R_TXB0CTRL && a <= R_TXB0CTRL + 0x20 &&
      ((a - R_TXB0CTRL) % 0x10) == 0) {
    const uint8_t n = (uint8_t)((a - R_TXB0CTRL) / 0x10);
    const uint8_t was = regs_[a];
    regs_[a] = v;
    if (!(was & TXB_TXREQ) && (v & TXB_TXREQ)) {
      // UNKNOWN which reading holds; modelled as the p17 flowchart, which
      // clears the three result bits at the top of every attempt.
      regs_[a] = (uint8_t)(regs_[a] & ~(TXB_ABTF | TXB_MLOA | TXB_TXERR));
      tx_[n].abort_requested = false;
      startIfIdle();
    } else if ((was & TXB_TXREQ) && !(v & TXB_TXREQ)) {
      // p16 s3.6 note 1: a message already transmitting CONTINUES; only one
      // that has not started is aborted. Note 2: this does NOT set ABTF.
      tx_[n].abort_requested = true;
      if (!tx_[n].sending) {
        if (tx_[n].loaded && !tx_[n].started) tx_never_started_++;
        tx_[n].loaded = false;
        wire_.push_back(WireEvent{micros(), n, tx_[n].frame, false, true});
      }
    }
    return;
  }
  const uint8_t intf_before = regs_[R_CANINTF];
  regs_[a] = v;
  if (a == R_CANINTF) rxFlagsChanged(intf_before);
}

uint8_t Mcp2515Fake::statusByte() const {
  uint8_t s = 0;
  const uint8_t f = regs_[R_CANINTF];
  if (f & INTF_RX0IF) s |= ST_RX0IF;
  if (f & INTF_RX1IF) s |= ST_RX1IF;
  if (f & INTF_TX0IF) s |= ST_TX0IF;
  if (f & INTF_TX1IF) s |= ST_TX1IF;
  if (f & INTF_TX2IF) s |= ST_TX2IF;
  if (regs_[R_TXB0CTRL] & TXB_TXREQ) s |= ST_TX0REQ;
  if (regs_[R_TXB0CTRL + 0x10] & TXB_TXREQ) s |= ST_TX1REQ;
  if (regs_[R_TXB0CTRL + 0x20] & TXB_TXREQ) s |= ST_TX2REQ;
  return s;
}

// p15 s3.2: "Prior to sending the SOF, the priority of all buffers that
// are queued for transmission is compared" -- so selection runs afresh
// before EVERY attempt, including a retry (F4). And on equal TXP "the
// buffer with the highest buffer number will be sent first". The driver
// never writes TXP, so every buffer sits at 00 and the tie-break decides.
// Foreign frames up to `upto_ns`, each occupying the wire for its own
// duration. Called as the clock advances, before the board is given a
// chance to start, so the board sees the wire as it would in life.
void Mcp2515Fake::stepForeign(uint64_t upto_ns) {
  if (!foreign_fps_) return;
  const uint64_t period_ns =
      (uint64_t)foreign_burst_ * 1000000000ull / foreign_fps_;
  while (foreign_next_ns_ <= upto_ns) {
    if (!foreign_left_) {
      foreign_left_ = foreign_burst_;
      foreign_next_ns_ += period_ns;
      continue;
    }
    CanFrame f;
    f.id = 0x010;            // low id: wins arbitration against our traffic
    f.ext = false;
    f.rtr = false;
    f.len = foreign_dlc_;
    for (uint8_t i = 0; i < 8; i++) f.data[i] = 0x5A;
    const uint64_t dur = frameTimeNs(f);
    const uint64_t at =
        foreign_busy_ns_ > upto_ns ? foreign_busy_ns_ : upto_ns;
    foreign_busy_ns_ = at + dur;
    foreign_busy_total_ += dur;
    foreign_count_++;
    foreign_left_--;
    if (!foreign_left_) break;
  }
}

void Mcp2515Fake::startIfIdle() {
  // The wire is held by another node: we cannot start, and our loaded
  // frames stay pending with TXREQ set. This is what losing
  // arbitration looks like from the board.
  if (t_ns_ < foreign_busy_ns_) return;
  // p47 s6.6: in bus-off "messages can neither be received or
  // transmitted". Without this the engine would keep sending while the
  // chip is supposedly off the bus, and every bus-off test would be
  // measuring nothing.
  if (regs_[R_EFLG] & EFLG_TXBO) return;
  for (int n = 0; n < 3; n++)
    if (tx_[n].sending) return;

  int best = -1;
  uint8_t best_p = 0;
  for (int n = 0; n < 3; n++) {
    const uint8_t c = regs_[(uint8_t)(R_TXB0CTRL + n * 0x10)];
    if (!(c & TXB_TXREQ) || !tx_[n].loaded) continue;
    const uint8_t p = (uint8_t)(c & TXB_TXP_MASK);
    if (best < 0 || p > best_p || (p == best_p && n > best)) {
      best = n;
      best_p = p;
    }
  }
  if (best < 0) return;
  tx_[best].sending = true;
  if (!tx_[best].started) {
    tx_[best].started = true;
    const uint64_t wait = t_ns_ - tx_[best].load_ns;
    if (wait > max_load_to_sof_ns_) max_load_to_sof_ns_ = wait;
  }
  // Snapshot at SOF: what goes on the wire is what was in the buffer when
  // transmission started, so a later reload cannot rewrite history (F6).
  tx_[best].on_wire = tx_[best].frame;
  tx_[best].done_ns = t_ns_ + frameTimeNs(tx_[best].on_wire);
}

void Mcp2515Fake::completeTx(int n) {
  TxBuf& b = tx_[n];
  b.sending = false;
  const uint8_t ctrl = (uint8_t)(R_TXB0CTRL + n * 0x10);

  if (bus_errors_on_) {
    // A form, bit or stuff error (p47 s6.3-6.5): "an error frame is
    // generated. The message is repeated."
    //
    // The contrast with the no-ACK path below is the reason this source
    // exists. ISO 11898's exception for an error-passive transmitter
    // counting ACKNOWLEDGE errors does NOT apply here, so nothing pins
    // TEC and the chip walks error-active -> error-passive -> bus-off
    // exactly as p48 Figure 6-1 draws it.
    regs_[ctrl] |= TXB_TXERR;
    tecSet((uint16_t)(tec_ + 8));          // the usual ISO increment
    refreshErrorFlags();
    if (tec_ > BUS_OFF_TEC_LIMIT) {
      enterBusOff();
      // Bus-off abandons it; it is not retried from here.
      regs_[ctrl] = (uint8_t)(regs_[ctrl] & ~TXB_TXREQ);
      b.loaded = false;
      b.abort_requested = false;
      wire_.push_back(WireEvent{micros(), (uint8_t)n, b.on_wire, false, false});
      return;
    }
    if (b.abort_requested) {
      regs_[ctrl] = (uint8_t)(regs_[ctrl] & ~TXB_TXREQ);
      b.loaded = false;
      b.abort_requested = false;
      wire_.push_back(WireEvent{micros(), (uint8_t)n, b.on_wire, false, false});
    }
    return;   // TXREQ stays set otherwise: the message is repeated
  }

  if (acked_) {
    // p15 s3.3 / p17 flowchart / p18 Register 3-1.
    regs_[ctrl] = (uint8_t)(regs_[ctrl] & ~TXB_TXREQ);
    regs_[R_CANINTF] |= (uint8_t)(INTF_TX0IF << n);
    b.loaded = false;
    b.abort_requested = false;
    // DEFERRED, like the increments: the datasheet hands decrements to the
    // CAN specification too (p47 s6.7). Modelled as the usual rule.
    if (tec_ > 0) tecSet((uint16_t)(tec_ - 1));
    refreshErrorFlags();
    wire_.push_back(WireEvent{micros(), (uint8_t)n, b.on_wire, true, false});
    return;
  }

  // No acknowledgement (p47 s6.2): error frame, the message is repeated,
  // TXREQ stays set.
  regs_[ctrl] |= TXB_TXERR;
  tecSet((uint16_t)(tec_ + 8));
  // DEFERRED (#7): the datasheet gives the thresholds and defers the
  // increment rules to ISO 11898, which has an exception stopping an
  // error-passive transmitter counting acknowledgement errors. Modelled
  // that way, so THE NO-ACK PATH never reaches bus-off. Bus-off itself IS
  // modelled as of 2026-10-04, by the separate form/bit/stuff error
  // source (setBusErrors) where no such exception applies -- see
  // enterBusOff() and the status table.
  if (tec_ >= 128) tecSet(128);
  refreshErrorFlags();

  if (b.abort_requested) {
    // p16 s3.6 note 1: the abort takes effect once the attempt fails.
    regs_[ctrl] = (uint8_t)(regs_[ctrl] & ~TXB_TXREQ);
    b.loaded = false;
    b.abort_requested = false;
    wire_.push_back(WireEvent{micros(), (uint8_t)n, b.on_wire, false, false});
  }
  // Otherwise the buffer stays pending with TXREQ set, and selection runs
  // again in startIfIdle() -- it does not simply retry itself (F4).
}

void Mcp2515Fake::advanceNs(uint64_t ns) {
  const uint64_t target = t_ns_ + ns;
  stepForeign(target);
  for (;;) {
    int next = -1;
    uint64_t when = 0;
    for (int n = 0; n < 3; n++) {
      if (!tx_[n].sending) continue;
      if (next < 0 || tx_[n].done_ns < when) {
        next = n;
        when = tx_[n].done_ns;
      }
    }

    // The bus-off recovery is an event on the same timeline as a frame
    // completion, so it is stepped to exactly like one. Running the
    // frame loop first and only then checking the clock would recover
    // late by up to a whole advance() -- the F3 mistake, in a new place.
    const bool recovering = (regs_[R_EFLG] & EFLG_TXBO) != 0;
    if (recovering && bus_off_recover_ns_ <= target &&
        (next < 0 || bus_off_recover_ns_ <= when)) {
      t_ns_ = bus_off_recover_ns_;
      recoverFromBusOff();
      startIfIdle();
      continue;
    }

    if (next < 0 || when > target) break;
    t_ns_ = when;          // exactly when it finished, not a chunk boundary
    completeTx(next);
    startIfIdle();
  }
  t_ns_ = target;
  startIfIdle();
}

// --------------------------------------------------------------------------
// SPI, one byte at a time, with the clock running while it happens.
// --------------------------------------------------------------------------
void Mcp2515Fake::csLow() {
  cs_ = true;
  op_ = 0;
  phase_ = 0;
}

void Mcp2515Fake::csHigh() {
  // The LOAD TX BUFFER instruction terminates HERE, at CS high, with
  // whatever was clocked -- see finishLoadTx() for why that matters.
  if (cs_ && op_ == OP_LOAD_TX) finishLoadTx();
  if (cs_ && op_ == OP_READ_RX) {
    // p67 Table 12-1 note / p65 s12.4: RXnIF is cleared when CS is
    // raised, and with it the buffer is free (p23 s4.1.3).
    const uint8_t which = rx_which_;
    const uint8_t before = regs_[R_CANINTF];
    if (rxFull(which)) rx_reads_++;   // only count a read that had one
    regs_[R_CANINTF] &= (uint8_t)~(which ? INTF_RX1IF : INTF_RX0IF);
    cs_ = false;                      // the instruction is over
    op_ = 0;
    rxFlagsChanged(before);
  }
  cs_ = false;
  op_ = 0;
  phase_ = 0;
}

uint8_t Mcp2515Fake::transfer(uint8_t out) {
  if (!cs_) return 0xFF;           // RECALLED: CS high aborts an instruction
  // 8 bits at the SPI clock, in NANOSECONDS. In microseconds this was 0 at
  // the driver's 10 MHz default and the clock never moved during SPI (F1).
  advanceNs((uint64_t)8 * 1000000000ull / spi_hz_);

  if (phase_ == 0) {
    op_ = out;
    phase_ = 1;
    if ((out & 0xF8) == OP_LOAD_TX) {
      load_buf_ = (uint8_t)((out & 0x07) >> 1);
      load_i_ = 0;
      op_ = OP_LOAD_TX;
    } else if ((out & 0xF8) == OP_RTS) {
      op_ = OP_RTS;
      // One instruction: every requested buffer becomes pending together,
      // and only then does the engine choose. Applying the bits one at a
      // time made a single RTS behave like three and hid the p15 s3.2
      // tie-break entirely.
      for (int n = 0; n < 3; n++) {
        if (!(out & (1 << n))) continue;
        const uint8_t c = (uint8_t)(R_TXB0CTRL + n * 0x10);
        if (regs_[c] & TXB_TXREQ) continue;
        regs_[c] = (uint8_t)((regs_[c] | TXB_TXREQ) &
                             ~(TXB_ABTF | TXB_MLOA | TXB_TXERR));
        tx_[n].abort_requested = false;
      }
      startIfIdle();
      return 0;
    } else if ((out & 0xF9) == OP_READ_RX) {
      op_ = OP_READ_RX;
      // p68 Figure 12-3: "1001 0nm0". n (bit 2) picks the buffer, m
      // (bit 1) starts at Dn0 instead of SIDH: 0x61, 0x66, 0x71, 0x76.
      // Then sequential, "the same as the READ instruction" (p65 s12.4).
      // Until 2026-10-08 m was ignored and a read always began at SIDH.
      rx_which_ = (uint8_t)((out >> 2) & 1);
      rx_bytes_ = 0;
      addr_ = (uint8_t)((rx_which_ ? 0x71 : 0x61) + ((out & 0x02) ? 5 : 0));
    } else if (out == OP_RESET) {
      reset(false);
    }
    return 0;
  }

  switch (op_) {
    case OP_READ_RX: {
      const uint8_t v = readReg(addr_++);
      // deliverDuringRead(): after the first data byte, with CS still low.
      if (++rx_bytes_ == 1 && mid_armed_ && rx_which_ == mid_buf_ &&
          rxFull(mid_buf_)) {
        mid_armed_ = false;
        mid_fired_ = true;
        mid_other_before_ = rxFull(1 - mid_buf_);
        deliverFrame(mid_);
        mid_other_after_ = rxFull(1 - mid_buf_);
      }
      return v;
    }
    case OP_READ_STATUS:
      return statusByte();
    case OP_RX_STATUS:
      return 0;
    case OP_READ:
      if (phase_ == 1) { addr_ = out; phase_ = 2; return 0; }
      return readReg(addr_++);
    case OP_WRITE:
      if (phase_ == 1) { addr_ = out; phase_ = 2; return 0; }
      writeReg(addr_++, out);
      return 0;
    case OP_BIT_MODIFY:
      if (phase_ == 1) { addr_ = out; phase_ = 2; return 0; }
      if (phase_ == 2) { mask_ = out; phase_ = 3; return 0; }
      // p66 s12.10 / p67 Table 12-1 note: on a register that is not
      // bit-modifiable the mask is forced to FFh and this becomes a byte
      // write. Silently.
      if (!isBitModifiable(addr_)) mask_ = 0xFF;
      writeReg(addr_, (uint8_t)((readReg(addr_) & ~mask_) | (out & mask_)));
      return 0;
    case OP_LOAD_TX: {
      // Bytes are only ACCUMULATED here. The instruction ends when CS
      // rises, not after a fixed count -- see finishLoadTx().
      if (load_i_ < 13) load_[load_i_++] = out;
      return 0;
    }
    default:
      return 0;
  }
}

// LOAD TX BUFFER ends at CS high, with however many bytes were clocked.
//
// THIS USED TO REQUIRE EXACTLY 13 BYTES, and that was wrong in a way
// that destroyed a week of throughput measurement.
//
// p65 s12.6 by way of s12.5. s12.6 LOAD TX BUFFER says only that the
// instruction "sets the Address Pointer to one of six addresses" and
// "eliminates the eight-bit address required by a normal WRITE"; it is
// s12.5 WRITE that supplies the behaviour this depends on -- "It is
// possible to write to sequential registers by continuing to clock in
// data bytes as long as CS is held low", and, decisively for a partial
// load, "If the CS line is brought high before eight bits are loaded,
// the write will be aborted for that data byte and previous bytes in
// the command will have been written."
//
// So the host clocks as many bytes as it wants and raises CS; what it
// did clock IS written. These are ordinary registers, not a FIFO, and
// the chip transmits DLC data bytes whatever was written.
// `loadAndSend()` therefore clocks 5 header bytes plus DLC -- correct,
// and what every MCP2515 library does.
//
// The other half of the wedge is s12.7: RTS "will set the TXREQ bit
// (TXBnCTRL[3]) for the respective buffer(s)" unconditionally. It does
// not consult whether a frame was loaded, so a short load followed by
// an RTS is exactly how a buffer reached TXREQ-with-no-frame.
//
// Under the old gate a frame with DLC < 8 clocked fewer than 13 bytes,
// so `loaded` was never set. The RTS that followed still set TXREQ, so
// the buffer entered a state that NEITHER side could see was wrong:
// startIfIdle() requires TXREQ && loaded and skipped it forever, while
// the driver read TXREQ in the status byte and called the buffer busy.
// All three buffers wedged, and nothing moved until the driver's 100 ms
// age-out tore them down -- then the cycle repeated. It presented as a
// 97.6% frame loss "in the driver" and survived elimination of the
// chip, the bridge loop, main.cpp, the harness, bursts, extended ids,
// timestamp quantisation and the stuffing model, because every one of
// those experiments used 8-byte payloads and 8-byte payloads are the
// one case the gate got right. 26.5% of the real capture is DLC < 8.
void Mcp2515Fake::finishLoadTx() {
  // Fewer than five bytes means SIDH..DLC were not all written, so the
  // buffer does not yet describe a frame. No caller does this; a real
  // chip would simply have updated the registers that were clocked.
  if (load_i_ < 5) return;

  TxBuf& b = tx_[load_buf_];
  CanFrame f;
  memset(&f, 0, sizeof(f));
  f.ext = (load_[1] & 0x08) != 0;
  if (f.ext) {
    f.id = ((uint32_t)load_[0] << 21) |
           ((uint32_t)(load_[1] & 0xE0) << 13) |
           ((uint32_t)(load_[1] & 0x03) << 16) |
           ((uint32_t)load_[2] << 8) | load_[3];
  } else {
    f.id = ((uint32_t)load_[0] << 3) | (uint32_t)(load_[1] >> 5);
  }
  f.len = (uint8_t)(load_[4] & 0x0F);
  if (f.len > 8) f.len = 8;
  // A data byte the host did not clock keeps whatever the buffer already
  // held: TXBnDm are registers that persist across loads. This matters
  // only for a host that writes a DLC larger than the bytes it sends,
  // which the driver never does -- modelled because the alternative is
  // to invent a zero that the silicon would not produce.
  for (uint8_t i = 0; i < f.len; i++)
    f.data[i] = (uint8_t)(5 + i < load_i_ ? load_[5 + i] : b.frame.data[i]);

  {
        if (b.sending) {
          // UNKNOWN (F6). The driver does this on every age-out of a
          // transmitting frame: it clears TXREQ, sees TXREQ=0 in the same
          // pass, and reloads. The datasheet says only to write a buffer
          // when TXREQ is clear and does not cover a buffer whose aborted
          // frame is still on the wire. Silicon could in principle put a
          // hybrid frame out. Counted and reported; the outcome is a named
          // choice, not a silent one.
          reloads_while_sending_++;
          if (reload_mode_ == RELOAD_CORRUPTS) b.on_wire = f;
        }
        // Counted BEFORE this buffer is marked loaded, so it cannot
        // match itself. See sameIdCoresident() for why it matters.
        for (int n = 0; n < 3; n++) {
          if (n == (int)load_buf_) continue;
          if (tx_[n].loaded && tx_[n].frame.id == f.id &&
              tx_[n].frame.ext == f.ext) {
            same_id_coresident_++;
            break;
          }
        }

        b.frame = f;
        b.loaded = true;
        b.started = false;
        b.load_ns = t_ns_;
        tx_loads_++;
  }
}

}  // namespace mcpfake
