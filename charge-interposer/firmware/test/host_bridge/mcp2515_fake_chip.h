// The MCP2515 model: register file, SPI instruction decode, transmit engine.
//
// Citations are in mcp2515_fake.h and in the assumptions table; this file
// implements what they say. Every behaviour here that is not DATASHEET is
// named in that table, so a test can print what its result rests on.
//
// TIME IS KEPT IN NANOSECONDS. It was microseconds until 2026-10-04, and
// an SPI byte at the driver's default 10 MHz is 0.8 us, so
// `8 * 1000000 / spi_hz` came out as integer ZERO and the clock did not
// advance during SPI at all. Everything time-dependent rested on that: the
// race inside drainTx() between readStatus() and the readReg() after it
// was claimed reachable and was not. Found by the generator-inhibit review
// session (F1).
//
// Nothing advances time by itself -- not the chip, not the SPI layer -- so
// a test can place an event exactly on a boundary and on either side of
// it. `micros()` and `millis()` both come from ONE counter.

#pragma once

#include <stdint.h>
#include <string.h>

#include <vector>

#include "mcp2515_fake.h"

namespace mcpfake {

// The driver's abort bound: the longest a frame already on the wire can
// still occupy it when TXREQ is cleared. Longest stuffed extended 8-byte
// frame (160 bits, see frameBits) plus an error frame and interframe space
// (~23). This is a BOUND, not a frame duration -- using it as the duration
// was F2.
static const uint32_t MAX_FRAME_BITS = 183;

// p47 s6.7 / p48 Figure 6-1: "The bus-off recovery sequence consists of
// 128 occurrences of 11 consecutive recessive bits."
static const uint32_t BUS_OFF_RECOVERY_BITS = 128 * 11;

// p47 s6.7: "It goes to bus-off if the TEC exceeds the bus-off limit of
// 255." EXCEEDS, so the transition is at 256 -- which is why the counter
// cannot live in the 8-bit TEC register alone.
static const uint16_t BUS_OFF_TEC_LIMIT = 255;

// RXB0CTRL.BUKT -- "rollover enable": a frame arriving for a full RXB0
// goes to RXB1 instead. The driver sets it (writeReg(REG_RXB0CTRL, 0x64)),
// which is what makes two receive buffers better than one.
static const uint8_t RXB0_BUKT = 0x04;

// What the model does when the driver reloads a buffer whose aborted frame
// is still on the wire. The datasheet does not cover it (p15 says only to
// write a buffer when TXREQ is clear, and our abort clears it early), so
// the behaviour is a named CHOICE rather than a silent one -- F6.
enum ReloadWhileSending {
  RELOAD_IGNORED,     // the in-flight frame completes; the load is lost
  RELOAD_CORRUPTS,    // the in-flight frame completes with the new payload
};

class Mcp2515Fake {
 public:
  explicit Mcp2515Fake(uint32_t bitrate = 500000, uint32_t spi_hz = 10000000)
      : bitrate_(bitrate), spi_hz_(spi_hz) {
    reset(true);
  }

  // ---- the clock ---------------------------------------------------------
  uint64_t nanos() const { return t_ns_; }
  uint64_t micros() const { return t_ns_ / 1000; }
  uint32_t millis() const { return (uint32_t)(t_ns_ / 1000000); }

  void advance(uint64_t us) { advanceNs(us * 1000); }
  // Steps exactly to each transmit completion rather than in fixed chunks,
  // so an event is stamped when it happened and the next frame starts
  // then too. Fixed chunks left up to 9 us of idle before each start and
  // the error accumulated across a run -- F3.
  void advanceNs(uint64_t ns);

  // ---- the segment -------------------------------------------------------
  void setAcknowledged(bool yes) { acked_ = yes; }

  // Every transmit attempt hits a FORM / BIT / STUFF error (p47 s6.3-6.5)
  // rather than merely going unacknowledged.
  //
  // This is a different error source from `setAcknowledged(false)` and the
  // difference is the whole point. ISO 11898 has an exception that stops an
  // error-passive transmitter counting ACKNOWLEDGE errors, which is why the
  // no-ACK path pins TEC at 128 and can never reach bus-off (the DEFERRED
  // item). No such exception applies to these, so TEC climbs by 8 per
  // attempt without limit and bus-off IS reachable -- p47 s6.7, "it goes to
  // bus-off if the TEC exceeds the bus-off limit of 255".
  void setBusErrors(bool yes) { bus_errors_on_ = yes; }

  // The bus-off recovery sequence: 128 occurrences of 11 consecutive
  // recessive bits (p47 s6.7, p48 Fig 6-1). The MCP2515 does this
  // "without any intervention by the MCU if the bus remains idle"
  // (p47 note box) -- unlike the TWAI driver, which the port must restart.
  uint64_t busOffRecoveryNs() const {
    return (uint64_t)BUS_OFF_RECOVERY_BITS * 1000000000ull / bitrate_;
  }
  // The internal transmit error counter. Wider than the TEC register
  // because bus-off is entered when TEC EXCEEDS 255, which an 8-bit
  // register cannot represent; the register carries min(tec, 255).
  uint16_t tecInternal() const { return tec_; }

  // A frame arrives from the segment and lands in a receive buffer.
  //
  // RXB0 first; if it is full and BUKT is set in RXB0CTRL the frame rolls
  // over into RXB1 (p27 s4.2.1, and the driver sets BUKT deliberately);
  // if both are full the frame is LOST and an overrun flag is set. That
  // last path is the real mechanism behind rxDropped(), as opposed to the
  // injected one below.
  //
  // A buffer is FULL exactly while its RXnIF flag is set: p23 s4.1.3,
  // "This bit must be cleared by the MCU in order to allow a new message
  // to be received into the buffer", and p26 Figure 4-3 decides on
  // "RXnIF = 0?" alone. So clearing the flag frees the buffer however it
  // is cleared -- READ RX BUFFER at CS high (p65 s12.4), BIT MODIFY or
  // WRITE of CANINTF. Until 2026-10-08 the model kept a separate "full"
  // flag that only READ RX cleared, so a driver clearing RXnIF by BIT
  // MODIFY would have seen a buffer that never took another frame.
  // The frame is stored INTO the RXBn registers (0x61-0x6D, 0x71-0x7D):
  // the p23 note says the whole buffer is overwritten with the MAB, and a
  // READ of those addresses must return it -- the conformance sequence
  // observes a buffer that way without freeing it.
  void deliverFrame(const CanFrame& f);
  bool rxFull(int n) const {
    return (regs_[R_CANINTF] & (n & 1 ? INTF_RX1IF : INTF_RX0IF)) != 0;
  }

  // Deliver `f` at the instant RXBn is next FREED (its RXnIF cleared by
  // any means), and not before. This is how a test places a frame in the
  // window between the driver taking one buffer and taking the other --
  // a window the driver's own SPI timing decides, so it cannot be hit
  // by choosing a time in advance. One pending frame per buffer; arming
  // again replaces it. firedWhenFreed() says whether it was delivered,
  // so a test whose window never opened fails instead of passing on an
  // ordering it never set up.
  void deliverWhenFreed(int n, const CanFrame& f) {
    pend_[n & 1] = f;
    pend_armed_[n & 1] = true;
    pend_fired_[n & 1] = false;
  }
  bool firedWhenFreed(int n) const { return pend_fired_[n & 1]; }

  // A controller-level receive overrun: a frame reached the wire
  // and the host did not read it in time. Distinct from our own
  // ring overflowing, and the chip records it in EFLG rather than
  // dropping it quietly. p63 Table 11-2: RX0OVR bit 6, RX1OVR bit 7.
  void injectRxOverrun(bool rxb1) {
    regs_[R_EFLG] |= (uint8_t)(rxb1 ? EFLG_RX1OVR : EFLG_RX0OVR);
  }
  void setReloadWhileSending(ReloadWhileSending m) { reload_mode_ = m; }

  // Bits on the wire for THIS frame, per CAN 2.0B: 44 bits of standard
  // framing or 64 of extended, plus 8 per data byte, plus a worst-case
  // stuffing estimate over the stuffable span, plus 3 bits of interframe
  // space. Worst-case stuffing because the alternative is to model the bit
  // pattern, and overstating a frame's time on the wire is the safe
  // direction for reasoning about an abort window.
  // Delegates to canFrameBits() below. The free function exists so the
  // replay harness can serialise a stimulus with the SAME arithmetic
  // the chip uses, rather than a second copy that could drift.
  uint32_t frameBits(const CanFrame& f) const;
  uint64_t frameTimeNs(const CanFrame& f) const {
    return (uint64_t)frameBits(f) * 1000000000ull / bitrate_;
  }
  uint64_t frameTimeUs(const CanFrame& f) const {
    return frameTimeNs(f) / 1000;
  }
  // The abort bound, which is not any particular frame's duration.
  uint64_t abortBoundUs() const {
    return (uint64_t)MAX_FRAME_BITS * 1000000ull / bitrate_;
  }

  // ---- SPI ---------------------------------------------------------------
  void csLow();
  uint8_t transfer(uint8_t out);
  void csHigh();

  // ---- inspection, for tests --------------------------------------------
  const std::vector<WireEvent>& wire() const { return wire_; }
  uint8_t reg(uint8_t a) const { return regs_[a]; }
  uint8_t tec() const { return regs_[R_TEC]; }
  bool busOff() const { return (regs_[R_EFLG] & EFLG_TXBO) != 0; }
  uint32_t busOffEntries() const { return bus_off_entries_; }

  // How many frames the driver has actually READ OUT of the receive
  // buffers. This is the coverage measure: a run where it is zero has not
  // exercised the receive path at all, however green it looks. The INT
  // pin going unmodelled made every run look like this and nothing said
  // so, so an L2 case now asserts it is non-zero.
  uint32_t rxReads() const { return rx_reads_; }

  // Completed LOAD TX BUFFER instructions: how many frames the DRIVER
  // handed to the chip. Distinguishes "the chip could not send them"
  // from "the driver never offered them", which is the difference
  // between a wire-capacity result and a queueing one.
  uint32_t txLoads() const { return tx_loads_; }

  // Localising a throughput stall: separates "the engine never started
  // this frame" from "it started it late". Those point at different
  // culprits -- scheduling versus latency -- and a single throughput
  // number cannot tell them apart.
  uint32_t txNeverStarted() const { return tx_never_started_; }
  uint64_t maxLoadToSofNs() const { return max_load_to_sof_ns_; }

  // Refuse every mode change, so Mcp2515Port::begin()'s setMode() times
  // out and the charger port fails to start. That is the spec 2.1 / 8.2
  // B-7d case: vehicle up, charger down.
  void setRefuseModeChange(bool yes) { refuse_mode_ = yes; }

  // ---- FOREIGN TRAFFIC ON THIS SEGMENT -----------------------------
  //
  // Another node transmitting here, which the model previously could
  // not express at all. It matters because ARBITRATION is the real
  // mechanism by which the board's charger-side buffers back up: the
  // charger transmits on that segment, and every time the board loses
  // arbitration its loaded frames stay pending while more arrive.
  //
  // Modelled as occupancy, not as bitwise arbitration: while a foreign
  // frame holds the wire the board cannot start one. That is the
  // board-visible consequence of losing arbitration to a
  // higher-priority identifier, which is the case of interest; a
  // foreign frame of LOWER priority would lose to the board and is not
  // modelled, so this is a worst case for the board, deliberately.
  //
  // `fps` is the mean rate of foreign frames, `burst` how many arrive
  // back to back, `dlc` their length. Rate 0 disables it.
  void setForeignTraffic(uint32_t fps, uint32_t burst = 1, uint8_t dlc = 8) {
    foreign_fps_ = fps;
    foreign_burst_ = burst ? burst : 1;
    foreign_dlc_ = dlc;
    foreign_left_ = 0;
    foreign_next_ns_ = t_ns_;
    foreign_busy_ns_ = 0;
    foreign_count_ = 0;
  }
  // Foreign frames actually placed on the wire -- so a test can show
  // the filler ran, rather than assuming it did.
  uint32_t foreignFrames() const { return foreign_count_; }
  uint64_t foreignBusyNs() const { return foreign_busy_total_; }

  // The INT pin, active low and LEVEL-triggered: asserted while any
  // ENABLED interrupt flag is pending (p24 s7.0 -- CANINTE gates CANINTF).
  // The driver's service() drains `while (digitalRead(int_) == LOW)`, so
  // without this the receive path never runs at all.
  bool intAsserted() const {
    return (regs_[R_CANINTF] & regs_[R_CANINTE]) != 0;
  }
  uint32_t reloadsWhileSending() const { return reloads_while_sending_; }
  uint32_t delivered() const;

  // ---- the transmit engine, for the L3 stall trace ----------------------
  //
  // The driver's own trace can only say what the DRIVER believed about
  // each buffer. These say what the CHIP is doing, so the two can be put
  // side by side: a buffer the driver sees as busy is either genuinely on
  // the wire, or queued behind another, or pending with the engine idle --
  // and only the last of those is a defect. Without this the three are
  // indistinguishable, which is how "all three buffers busy" was first
  // misread as congestion.
  int sendingBuf() const {
    for (int n = 0; n < 3; n++)
      if (tx_[n].sending) return n;
    return -1;
  }
  // Pending means TXREQ set and a frame in the buffer, i.e. eligible for
  // selection. Taken from the register, as startIfIdle() does.
  bool txPending(int n) const { return txReq(n) && tx_[n].loaded; }
  // The two halves separately, because the interesting case is when they
  // DISAGREE: TXREQ set with no frame behind it is invisible to the
  // driver -- which reads TXREQ and calls the buffer busy -- and
  // invisible to the engine, which requires both and skips it.
  bool txReq(int n) const {
    return (regs_[(uint8_t)(R_TXB0CTRL + n * 0x10)] & TXB_TXREQ) != 0;
  }
  bool txLoaded(int n) const { return tx_[n].loaded; }
  // What a buffer is holding, for the spec 2 co-residency check. Read
  // from the CHIP's copy rather than from the driver's bookkeeping on
  // purpose: asking the driver whether it kept its own promise lets a
  // bug in that bookkeeping answer for itself.
  uint32_t txFrameId(int n) const { return tx_[n].frame.id; }
  bool txFrameExt(int n) const { return tx_[n].frame.ext; }
  // Every transmit ATTEMPT that reached the wire, delivered or not.
  // delivered() counts only the acknowledged ones.
  uint32_t wireEvents() const { return (uint32_t)wire_.size(); }

  // How many times a frame was loaded into a buffer while ANOTHER
  // buffer already held a pending frame of the SAME id.
  //
  // This is the precondition for a same-identifier transposition: with
  // equal TXP the chip picks the highest buffer number (p15 s3.2), so
  // two frames of one id resident at once can leave in load order or
  // against it depending purely on which buffers they landed in. If
  // this counter is zero, any observed same-id reordering came from
  // somewhere else and the buffer-number explanation is wrong --
  // which is why it is counted rather than assumed.
  uint32_t sameIdCoresident() const { return same_id_coresident_; }

  // Frames the chip DROPPED on receive because both RXB0 and RXB1 were
  // full. Distinct from the EFLG overflow BIT, which is sticky and so
  // cannot say how many were lost -- a count is what an accounting
  // needs. Incremented in deliverFrame().
  uint32_t rxOverflowCount() const { return rx_overflow_count_; }

  void reset(bool power_on);

 private:
  struct TxBuf {
    CanFrame frame;            // what the driver loaded
    CanFrame on_wire;          // snapshot taken at SOF (F6)
    bool loaded = false;
    bool sending = false;
    uint64_t done_ns = 0;
    bool abort_requested = false;
    bool started = false;
    uint64_t load_ns = 0;
  };

  uint8_t readReg(uint8_t a) const;
  void writeReg(uint8_t a, uint8_t v);
  uint8_t statusByte() const;
  void completeTx(int n);
  void startIfIdle();
  // Ends a LOAD TX BUFFER instruction at CS high, with however many
  // bytes were clocked. Not a fixed 13 -- see the definition.
  void finishLoadTx();
  void setMode(uint8_t m);
  void refreshErrorFlags();
  void tecSet(uint16_t v);      // keeps regs_[R_TEC] = min(v, 255)
  void enterBusOff();
  void recoverFromBusOff();

  uint32_t foreign_fps_ = 0;
  uint32_t foreign_burst_ = 1;
  uint8_t foreign_dlc_ = 8;
  uint32_t foreign_left_ = 0;
  uint64_t foreign_next_ns_ = 0;
  uint64_t foreign_busy_ns_ = 0;      // wire occupied until this time
  uint64_t foreign_busy_total_ = 0;
  uint32_t foreign_count_ = 0;
  void stepForeign(uint64_t upto_ns);

  uint32_t bitrate_;
  uint32_t spi_hz_;
  uint64_t t_ns_ = 0;
  bool acked_ = true;
  bool cs_ = false;
  ReloadWhileSending reload_mode_ = RELOAD_IGNORED;

  uint8_t regs_[0x80];
  TxBuf tx_[3];
  std::vector<WireEvent> wire_;
  uint32_t bus_off_entries_ = 0;
  uint32_t reloads_while_sending_ = 0;
  uint32_t same_id_coresident_ = 0;
  uint32_t rx_overflow_count_ = 0;
  bool bus_errors_on_ = false;
  uint16_t tec_ = 0;
  uint32_t rx_reads_ = 0;
  uint32_t tx_loads_ = 0;
  uint32_t tx_never_started_ = 0;
  uint64_t max_load_to_sof_ns_ = 0;
  bool refuse_mode_ = false;
  uint64_t bus_off_recover_ns_ = 0;

  uint8_t op_ = 0;
  int phase_ = 0;
  uint8_t addr_ = 0;
  uint8_t mask_ = 0;
  uint8_t load_buf_ = 0;
  uint8_t load_i_ = 0;
  uint8_t load_[13];

  // RXBn lives in regs_ (see deliverFrame); these are only the
  // deliverWhenFreed() hook and the helpers around CANINTF changes.
  void storeRx(int n, const CanFrame& f);
  void rxFlagsChanged(uint8_t before);
  CanFrame pend_[2];
  bool pend_armed_[2] = {false, false};
  bool pend_fired_[2] = {false, false};
  uint8_t rx_which_ = 0;      // the buffer the current READ RX names
};

// Bits a frame occupies on the wire, exact stuffing from the bit
// pattern. Free so that anything needing wire TIME -- the chip model
// and the replay harness both do -- uses one implementation.
uint32_t canFrameBits(const CanFrame& f);

}  // namespace mcpfake
