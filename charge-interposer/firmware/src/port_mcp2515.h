// MCP2515 CAN port -- CAN2 on the ESP32-CAN-X2, the charger segment.
//
// Written rather than taken from a library, for one reason. coryjfowler's
// MCP_CAN::sendMsg() -- the obvious choice, and what the vendor examples use --
// busy-waits for the frame to complete ON THE WIRE, polling TXREQ up to
// TIMEOUTVALUE (2500 us, mcp_can_dfs.h:47), and then returns
// CAN_SENDMSGTIMEOUT and drops the frame. That means ~300-350 us of blocking
// per send at 500 kbps, all three TX buffers wasted (strictly one frame in
// flight), and silent frame loss on arbitration contention. For a transparent
// bridge that is a correctness bug, not a performance one.
//
// This driver instead loads a free TX buffer, sets TXREQ, and returns. A
// software ring absorbs bursts, and a single READ STATUS byte per service()
// reports both RX flags and all three TXREQ states, so keeping the buffers fed
// costs one SPI transaction.
//
// Board wiring (Autosport Labs ESP32-CAN-X2, variant `aslcanx2`):
//   CS 10, SCK 12, MISO 13, MOSI 11, IRQ 3, 16 MHz crystal.
// Note the vendor's own examples poll and never use IRQ; the pin is documented
// on the wiki but absent from pins_arduino.h, so it is passed in explicitly.

#pragma once

#include <SPI.h>

#include "can_port.h"

namespace interposer {

class Mcp2515Port : public CanPort {
 public:
  Mcp2515Port(SPIClass& spi, uint8_t cs_pin, uint8_t int_pin,
              uint32_t spi_hz = 10000000UL);

  bool begin(uint32_t bitrate) override;
  bool receive(RxFrame& out) override;
  bool send(uint32_t id, bool ext, const uint8_t* data, uint8_t len) override;
  void service(uint32_t t_ms) override;

  uint32_t rxCount() const override { return rx_count_; }
  uint32_t txCount() const override { return tx_count_; }
  uint32_t rxDropped() const override;
  uint32_t txDropped() const override { return tx_dropped_; }
  uint32_t busErrors() const override { return bus_errors_; }
  // spec 2.2: transmissions the controller could not complete, plus
  // frames aged out of the queue. Counted ONCE each, here and nowhere
  // else -- main.cpp must not add its own tally on top.
  uint32_t txFailed() const { return tx_failed_; }
  // Frames that WERE delivered after we had given up on them. Not a
  // failure -- the charger acknowledged -- but worth seeing on the
  // bench, so the serial LOSS line carries it. No CAN field.
  uint32_t txLate() const { return tx_late_; }
  // Buffers found idle, never aborted, with TXnIF clear. DS20001801J
  // p15 s3.3 says that cannot happen -- the chip clears TXREQ only on
  // success, and success sets TXnIF. A non-zero value here means a
  // DATASHEET item this driver relies on is wrong, which matters more
  // than the frame does. Serial only; it is counted into txFailed()
  // as well, because without evidence of delivery the conservative
  // reading for spec 2.2 is that the frame did not arrive.
  uint32_t txUnexplained() const { return tx_unexplained_; }
  uint32_t txAged() const { return tx_aged_; }
  // HOW OFTEN THE SPEC 2 HOLD ACTUALLY BIT: the number of times the
  // refill loop stopped because the head of the ring carried an
  // identifier a hardware buffer was already holding.
  //
  // NOT behind a diagnostic gate, and deliberately so. It is four
  // bytes and one increment, and it is the only way a bench arm can
  // show it EXERCISED the hold rather than merely passing. A run
  // that reports frames in order with zero deferrals did not test
  // the hold at all, whatever its ordering result -- and on
  // 2026-10-05 the approved spec 12 arm turned out to be exactly
  // that once the stimulus was modelled physically. Proving the
  // mechanism engaged needs no second firmware image if the board
  // counts it.
  uint32_t txHoldDeferrals() const { return tx_hold_defer_; }
  // FRAMES that were deferred at least once, as opposed to the PASSES
  // above. The pass counter is the pass/fail indicator -- zero means
  // the hold never bit -- but it counts every pass the refill loop
  // re-tested a blocked head, about six per frame at truck rate, so
  // it is not a quantity anyone should divide by. This is.
  uint32_t txFramesDeferred() const { return tx_frames_defer_; }
  bool busOff() const override { return bus_off_; }
  uint32_t busOffEvents() const override { return bus_off_events_; }
  const char* name() const override { return "MCP2515"; }

  // Bench diagnostics: how close the rings came to overflowing.
  uint16_t rxRingUsed() const { return rx_.used(); }
  uint16_t txRingUsed() const { return tx_.used(); }
  uint8_t lastError() const { return last_eflg_; }
  // The controller-overrun half of rxDropped(), for the serial line.
  uint32_t rxOverruns() const { return rx_overruns_; }

  // Bench diagnostics only -- the bridge never calls these.
  //
  // Internal loopback (CANCTRL REQOP=010) routes a transmitted frame straight
  // into the receive buffers inside the controller. TXCAN stays recessive and
  // no ACK is needed, so a successful round trip isolates the SPI driver and
  // the ID encode/decode from the wire, the transceiver, and every other node.
  // Call before begin().
  void setLoopback(bool on) { loopback_ = on; }
  // Listen-only (CANCTRL REQOP=011): receives without ACKing. See the matching
  // comment on TwaiPort::setListenOnly().
  void setListenOnly(bool on) { listen_only_ = on; }
  void end();
  uint8_t reg(uint8_t addr) { return readReg(addr); }

 private:
  // instructions
  static const uint8_t CMD_RESET = 0xC0;
  static const uint8_t CMD_READ = 0x03;
  static const uint8_t CMD_WRITE = 0x02;
  static const uint8_t CMD_RTS = 0x80;
  static const uint8_t CMD_READ_STATUS = 0xA0;
  static const uint8_t CMD_BIT_MODIFY = 0x05;
  static const uint8_t CMD_READ_RX = 0x90;   // | (n ? 0x04 : 0x00)
  static const uint8_t CMD_LOAD_TX = 0x40;   // | (n * 2)

  // registers
  static const uint8_t REG_CANSTAT = 0x0E;
  static const uint8_t REG_CANCTRL = 0x0F;
  static const uint8_t REG_TEC = 0x1C;
  static const uint8_t REG_REC = 0x1D;
  static const uint8_t REG_CNF3 = 0x28;
  static const uint8_t REG_CNF2 = 0x29;
  static const uint8_t REG_CNF1 = 0x2A;
  static const uint8_t REG_CANINTE = 0x2B;
  static const uint8_t REG_CANINTF = 0x2C;
  static const uint8_t REG_EFLG = 0x2D;
  static const uint8_t REG_TXB0CTRL = 0x30;  // +0x10 per buffer
  // TXBnCTRL: ABTF aborted, MLOA lost arbitration, TXERR bus error,
  // TXREQ pending.
  static const uint8_t TXB_ABTF = 0x40;
  static const uint8_t TXB_MLOA = 0x20;
  static const uint8_t TXB_TXERR = 0x10;
  static const uint8_t TXB_TXREQ = 0x08;
  static const uint8_t REG_RXB0CTRL = 0x60;
  static const uint8_t REG_RXB1CTRL = 0x70;

  void select();
  void deselect();
  uint8_t readReg(uint8_t addr);
  void writeReg(uint8_t addr, uint8_t val);
  void modifyReg(uint8_t addr, uint8_t mask, uint8_t val);
  uint8_t readStatus();
  bool setMode(uint8_t mode);
  bool setBitrate(uint32_t bitrate);
  void readRxBuffer(uint8_t n, uint32_t t_ms);
  bool loadAndSend(uint8_t buf_n, const RxFrame& f);
  void drainTx();

  SPIClass& spi_;
  SPISettings settings_;
  uint8_t cs_;
  uint8_t int_;

  FrameRing<64> rx_;
  FrameRing<64> tx_;

  uint32_t rx_count_;
  uint32_t tx_count_;
  uint32_t tx_dropped_;
  uint32_t tx_failed_;
  uint32_t tx_aged_;
  uint32_t tx_late_;
  uint32_t tx_unexplained_;
  uint32_t now_ms_;            // last service() tick, for ageing
  uint32_t tx_busy_since_[3];  // when each hardware buffer was loaded
  bool tx_busy_[3];
  // Spec 2.2: a buffer whose TXREQ we cleared, whose outcome is not
  // yet known. The frame may still have been on the wire when we
  // cleared it (p16 s3.6 note 1), so it is neither delivered nor
  // failed until TXnIF says which. Microseconds, because the bound is
  // 366 us and millis() cannot represent it.
  bool tx_aborting_[3];
  uint32_t tx_abort_us_[3];

  // Spec 2, the same-identifier hold (user, 2026-10-04). Which
  // identifier each hardware buffer is currently holding, so the
  // refill loop can refuse to put a second frame of that identifier
  // into the chip at the same time.
  //
  // WHY THE DRIVER HAS TO DO THIS. The chip chooses among its own
  // buffers by TXP and then by HIGHEST BUFFER NUMBER, "independent
  // from ... the message arbitration scheme" (DS20001801J p15 s3.2).
  // The driver leaves every TXP at 00, so the tie-break decides, and
  // two frames of one identifier resident at once can leave in either
  // order. The host L2 replay caught exactly that: one adjacent swap
  // of 0x3FE pages in 10 s of truck traffic. Order for a given
  // identifier carries meaning for the paged 0x18EFC000, so it cannot
  // rest on which buffer a frame happened to land in.
  //
  // A buffer counts as holding its identifier until its outcome is
  // KNOWN, which includes tx_aborting_: a frame we gave up on may
  // still have been on the wire and may yet be delivered late, so
  // releasing the identifier at the abort would allow its successor
  // to overtake it.
  bool tx_holds_[3];
  uint32_t tx_hold_defer_;
  uint32_t tx_frames_defer_;
  bool tx_head_deferred_;     // the current ring head has been blocked
  uint32_t tx_held_id_[3];
  bool tx_held_ext_[3];

  // True if any buffer is holding this identifier.
  bool idHeld(uint32_t id, bool ext) const {
    for (uint8_t n = 0; n < 3; n++)
      if (tx_holds_[n] && tx_held_id_[n] == id && tx_held_ext_[n] == ext)
        return true;
    return false;
  }
  // Frames lost at the CONTROLLER, as against our software ring.
  // Both are 'frames lost' under can_port.h's definition of
  // rxDropped(), so that returns the sum; kept apart because on the
  // bench they mean different things -- the ring overflowing is the
  // bridge falling behind, the chip overrunning is the host not
  // servicing SPI fast enough.
  uint32_t rx_overruns_;
  uint32_t bus_errors_;
  uint8_t last_eflg_;

  // Spec 2.2: how long a frame toward the charger may wait before it is
  // abandoned. The VCU's command page is 20 Hz, so anything held longer
  // than this has already been superseded by a newer command and delivering
  // it would describe a charge that has moved on. 100 ms = two command
  // periods: long enough to ride out ordinary arbitration and a busy
  // moment, short enough that a returning charger never sees stale state.
  static const uint32_t TX_MAX_AGE_MS = 100;

  // How long after clearing TXREQ the outcome is certain.
  //
  // A frame already transmitting when the abort is requested runs to
  // completion (DS20001801J p16 s3.6 note 1), so TXREQ reading 0 says
  // nothing about whether it reached the wire. The worst case is the
  // longest stuffed extended 8-byte frame (~160 bits) plus an error
  // frame and interframe space (~23): 183 bits, which at 500 kbit/s
  // is 366 us. After that TXnIF is decisive -- set means delivered,
  // clear means abandoned.
  //
  // 400 rather than 366 to leave margin for the SPI round trip that
  // reads it; the age-out it sits behind is 100 ms, so the rounding
  // costs nothing.
  static const uint32_t TX_ABORT_RESOLVE_US = 400;
  bool bus_off_;
  uint32_t bus_off_events_;
  bool ready_;
  bool loopback_;
  bool listen_only_;

#ifdef INTP_TX_TRACE
  // ---- L3 STALL INSTRUMENTATION -- NOT FLIGHT CODE -------------------
  //
  // Compiled only when INTP_TX_TRACE is defined. No PlatformIO
  // environment defines it, so all five firmware images contain none of
  // this; `build_check.py` still sees the same binaries.
  //
  // WHY IT HAS TO EXIST. The charger port delivers 540 of 22,314 frames
  // of realistic traffic (2.4%), and the totals cannot say why: aged,
  // dropped and failed read the same whether the three hardware buffers
  // are churning flat out and simply outpaced, or sitting idle behind a
  // ring that is overflowing. Those two have opposite fixes. Only a
  // record of what each buffer was doing ON EACH PASS separates them,
  // and only a per-pass one -- totals over a 10 s window average the
  // stall away, which is how four earlier experiments all came back
  // "inconclusive".
  //
  // STORAGE IS STATIC, not per-instance, for two reasons: 65,536 passes
  // x 16 bytes would be a 1 MB stack frame in a test that constructs
  // the port as a local, and the probe has exactly one port. A second
  // Mcp2515Port in an INTP_TX_TRACE build would interleave into the
  // same buffer -- acceptable for a single-port diagnostic, and the
  // reason this is not offered as a general facility. Call
  // traceReset() before the window of interest.
 public:
  struct TxPass {
    uint32_t us;        // micros() at the top of the pass
    uint32_t ms;        // now_ms_, the ageing clock
    // Per hardware buffer, what this pass found and did:
    //   '.'  idle and unused -- available to load
    //   'B'  TXREQ set, within its age budget: transmitting or arbitrating
    //   'X'  TXREQ set, over age: TXREQ cleared this pass, abort started
    //   'W'  abort placed, still inside the 400 us resolve wait
    //   'L'  abort resolved: it went out late (TXnIF set)
    //   'F'  abort resolved: it never reached the wire (TXnIF clear)
    //   'C'  completed normally this pass (chip cleared TXREQ, TXnIF set)
    //   'U'  idle, never aborted, TXnIF clear -- the unexplained case
    char state[3];
    uint16_t ring;      // tx_ occupancy at the top of the pass
    uint8_t loaded;     // frames handed to the chip in the refill loop
    uint8_t aged;       // frames discarded as stale in the refill loop
  };
  static const uint32_t TRACE_N = 65536;
  static void traceReset() { trace_count_ = 0; trace_passes_ = 0; }
  static const TxPass* trace() { return trace_; }
  static uint32_t traceCount() { return trace_count_; }
  // Every pass, including any past TRACE_N that were not stored, so a
  // reader can tell a truncated trace from a complete one.
  static uint32_t tracePasses() { return trace_passes_; }

 private:
  static TxPass trace_[TRACE_N];
  static uint32_t trace_count_;
  static uint32_t trace_passes_;
#endif

#ifdef INTP_ORDER_WITNESS
  // ---- BOARD-SIDE COMPLETION-ORDER WITNESS -- NOT FLIGHT CODE -------
  //
  // Compiled only when INTP_ORDER_WITNESS is defined. No PlatformIO
  // environment defines it, so all five firmware images contain none of
  // this; `build_check.py` asserts that the truck image is unchanged by
  // it. Default footprint is WITNESS_N * 16 bytes = 8 KB of .bss.
  //
  // WHAT IT IS FOR. The spec 12 entry 2 arm checks the ORDER frames
  // reach the charger, read from a sequence number in the payload. That
  // says what arrived; it cannot say what the board sent, so a dongle
  // that drops or reorders on capture is indistinguishable from the
  // driver doing it. This records the board's own view of the order it
  // completed frames in, so the two can be compared and a capture
  // artefact cannot be mistaken for a firmware defect -- the exact
  // confusion that cost a day on the L3 shortfall.
  //
  // IT STOPS WHEN FULL AND NEVER WRAPS. A ring would silently discard
  // the beginning of the run, which for an ORDER question is the part
  // that matters; `witnessFull()` and the gap between witnessCount()
  // and witnessCompletions() say exactly how much was not stored.
  //
  // IT KEEPS ITS OWN COPY OF (id, ext), taken at load time, rather than
  // reading `tx_held_id_`. Those belong to the same-identifier hold,
  // and using the hold's bookkeeping to witness whether the hold worked
  // would be circular: a bug in that bookkeeping would answer for
  // itself.
  //
  // MULTI-COMPLETION PASSES ARE COUNTED, NOT RESOLVED. When two buffers
  // are found complete in the same drainTx pass the driver cannot know
  // which reached the wire first -- it sees both only after the fact.
  // Such passes are counted in witnessMultiPasses() and the entries
  // carry the same pass number, so a reader can see the ambiguity
  // instead of being handed an order that was really the loop's
  // iteration order.
  //
  // Storage is static for the same reasons as the trace above: one port
  // per board, and 8 KB has no business on a stack.
 public:
  struct Completion {
    uint32_t load_seq;   // which load this was, counted from witnessReset()
    uint32_t id;
    uint32_t pass;       // drainTx pass number; equal pass = unordered
    uint8_t buf;         // 0..2
    uint8_t outcome;     // 'C' completed, 'L' late after abort,
                         // 'U' unexplained, 'F' never reached the wire
    uint8_t ext;
    uint8_t pad;
  };
#ifndef INTP_WITNESS_N
#define INTP_WITNESS_N 512
#endif
  static const uint32_t WITNESS_N = INTP_WITNESS_N;
  static void witnessReset();
  static const Completion* witness() { return wit_; }

  // ---- WHOLE-RUN STATISTICS, costing no storage ------------------
  //
  // The stored window is WITNESS_N entries and a truck-rate replay is
  // 22,316 completions, so the entries cover about 2% of a run. The
  // RECONCILE total proves the COUNTS for the whole run and says
  // nothing about the ORDER outside the window -- a distinction worth
  // stating plainly, because a clean stored window reads like a clean
  // run and on 2026-10-05 was reported as one.
  //
  // These are updated on EVERY completion, including the ones past
  // WITNESS_N, so order is covered end to end.
  //
  // An inversion is a completion whose load sequence number is below
  // the highest already completed: the frame was loaded earlier and
  // came out later. Displacement is how far below.
  static uint32_t witnessInversions() { return wit_inv_; }
  static uint32_t witnessMaxDisplacement() { return wit_max_disp_; }
  // Inversions WITHIN one identifier, which spec 2's hold forbids
  // outright -- the cross-identifier ones it permits.
  static uint32_t witnessIdInversions() { return wit_id_inv_; }
  // Identifiers that did not fit the fixed per-id table. Non-zero
  // means witnessIdInversions() is a LOWER bound, and it is reported
  // rather than letting the count quietly understate.
  static uint32_t witnessIdOverflow() { return wit_id_over_; }
  static uint32_t witnessIdsTracked() { return wit_id_used_; }

  // ---- WHERE A DIVERGENCE WOULD BE, not just that there is one ----
  //
  // An inversion needs at least two frames pending and not yet
  // started: one buffer alone cannot be overtaken. So the number of
  // OTHER buffers already holding a requested frame at the moment of
  // each load bounds how much reordering is even possible.
  //
  // If silicon sits mostly at 0-1 where the model has many 2s, the
  // two disagree about TIMING -- load-to-SOF, or the service cadence
  // -- and not about the p15 s3.2 tie-break. That is a different fix,
  // and the histogram is what distinguishes them.
  //
  // busyAtLoad(k) = loads made with exactly k other buffers requested.
  static uint32_t witnessBusyAtLoad(uint8_t k) {
    return k < 3 ? wit_busy_hist_[k] : 0;
  }
  // Loads made while another buffer was loaded in an EARLIER pass --
  // as close as the driver can get to "two pending, neither started",
  // since it cannot see SOF.
  static uint32_t witnessLoadsWithOlderPending() { return wit_older_; }
  // Stored entries.
  static uint32_t witnessCount() { return wit_count_; }
  // Every completion seen, including those past WITNESS_N that were not
  // stored -- so a truncated witness is distinguishable from a complete
  // one rather than just looking short.
  static uint32_t witnessCompletions() { return wit_total_; }
  static bool witnessFull() { return wit_total_ > wit_count_; }
  static uint32_t witnessLoads() { return wit_loads_; }
  static uint32_t witnessPasses() { return wit_pass_; }
  // Passes in which more than one buffer completed: the entries from
  // those passes are mutually unordered and must not be read as a
  // sequence.
  static uint32_t witnessMultiPasses() { return wit_multi_; }

 private:
  static Completion wit_[WITNESS_N];
  static uint32_t wit_count_;
  static uint32_t wit_total_;
  static uint32_t wit_loads_;
  static uint32_t wit_pass_;
  static uint32_t wit_multi_;
  static uint32_t wit_load_seq_[3];
  static uint32_t wit_id_[3];
  static uint8_t wit_ext_[3];

  // Whole-run order statistics.
  static uint32_t wit_hi_seq_;      // highest load_seq completed so far
  static bool wit_have_hi_;
  static uint32_t wit_inv_;
  static uint32_t wit_max_disp_;
  // Last completed load_seq per identifier, in a fixed open-addressed
  // table. Fixed because this is flight-adjacent code and must not
  // allocate; the capture has 72 identifiers, so 256 slots leaves
  // room, and an identifier that does not fit is COUNTED rather than
  // dropped silently.
  static const uint32_t WIT_IDS = 256;
  static uint32_t wit_id_key_[WIT_IDS];   // (id<<1)|ext, +1 so 0 = empty
  static uint32_t wit_id_last_[WIT_IDS];
  static uint32_t wit_id_inv_;
  static uint32_t wit_id_over_;
  static uint32_t wit_id_used_;
  static void witnessNote(uint32_t seq, uint32_t id, uint8_t ext);
  static uint32_t wit_busy_hist_[3];
  static uint32_t wit_older_;
  static uint32_t wit_load_pass_[3];
#endif
};

}  // namespace interposer
