#include "port_mcp2515.h"

#include <Arduino.h>

namespace interposer {

#ifdef INTP_TX_TRACE
// Storage for the stall trace declared in the header. Static, so it does
// not sit on the stack of a test that holds the port as a local. Absent
// from every firmware image -- see the header for why this exists.
Mcp2515Port::TxPass Mcp2515Port::trace_[Mcp2515Port::TRACE_N];
uint32_t Mcp2515Port::trace_count_ = 0;
uint32_t Mcp2515Port::trace_passes_ = 0;
#endif

#ifdef INTP_ORDER_WITNESS
// Storage for the completion-order witness declared in the header.
// Absent from every firmware image; see the header for the design and
// for why it stops rather than wraps.
Mcp2515Port::Completion Mcp2515Port::wit_[Mcp2515Port::WITNESS_N];
uint32_t Mcp2515Port::wit_count_ = 0;
uint32_t Mcp2515Port::wit_total_ = 0;
uint32_t Mcp2515Port::wit_loads_ = 0;
uint32_t Mcp2515Port::wit_pass_ = 0;
uint32_t Mcp2515Port::wit_multi_ = 0;
uint32_t Mcp2515Port::wit_load_seq_[3] = {0, 0, 0};
uint32_t Mcp2515Port::wit_id_[3] = {0, 0, 0};
uint8_t Mcp2515Port::wit_ext_[3] = {0, 0, 0};
uint32_t Mcp2515Port::wit_hi_seq_ = 0;
bool Mcp2515Port::wit_have_hi_ = false;
uint32_t Mcp2515Port::wit_inv_ = 0;
uint32_t Mcp2515Port::wit_max_disp_ = 0;
uint32_t Mcp2515Port::wit_id_key_[Mcp2515Port::WIT_IDS];
uint32_t Mcp2515Port::wit_id_last_[Mcp2515Port::WIT_IDS];
uint32_t Mcp2515Port::wit_id_inv_ = 0;
uint32_t Mcp2515Port::wit_id_over_ = 0;
uint32_t Mcp2515Port::wit_id_used_ = 0;
uint32_t Mcp2515Port::wit_busy_hist_[3] = {0, 0, 0};
uint32_t Mcp2515Port::wit_older_ = 0;
uint32_t Mcp2515Port::wit_load_pass_[3] = {0, 0, 0};

// Order statistics for ONE completion. Called for EVERY completion,
// including those past WITNESS_N, so order is covered for the whole
// run even though only the first WITNESS_N entries are stored. The
// stored window is about 2% of a truck-rate replay, and a clean
// window was reported as a clean run on 2026-10-05.
void Mcp2515Port::witnessNote(uint32_t seq, uint32_t id, uint8_t ext) {
  if (wit_have_hi_ && seq < wit_hi_seq_) {
    wit_inv_++;
    const uint32_t d = wit_hi_seq_ - seq;
    if (d > wit_max_disp_) wit_max_disp_ = d;
  }
  if (!wit_have_hi_ || seq > wit_hi_seq_) {
    wit_hi_seq_ = seq;
    wit_have_hi_ = true;
  }

  // Per identifier. Open addressing with linear probing; the key is
  // stored +1 so that zero means empty without a separate flag.
  const uint32_t key = ((id << 1) | (ext ? 1u : 0u)) + 1u;
  const uint32_t h = (key * 2654435761u) % WIT_IDS;
  for (uint32_t probe = 0; probe < WIT_IDS; probe++) {
    const uint32_t i = (h + probe) % WIT_IDS;
    if (wit_id_key_[i] == 0) {
      wit_id_key_[i] = key;
      wit_id_last_[i] = seq;
      wit_id_used_++;
      return;
    }
    if (wit_id_key_[i] == key) {
      // Spec 2: within one identifier the hold forbids this outright.
      if (seq < wit_id_last_[i]) wit_id_inv_++;
      else wit_id_last_[i] = seq;
      return;
    }
  }
  // Table full. COUNTED, never ignored: witnessIdInversions() becomes
  // a lower bound and the dump says so, rather than reporting a clean
  // zero it cannot justify.
  wit_id_over_++;
}

void Mcp2515Port::witnessReset() {
  wit_count_ = 0;
  wit_total_ = 0;
  wit_loads_ = 0;
  wit_pass_ = 0;
  wit_multi_ = 0;
  wit_hi_seq_ = 0;
  wit_have_hi_ = false;
  wit_inv_ = 0;
  wit_max_disp_ = 0;
  wit_id_inv_ = 0;
  wit_id_over_ = 0;
  wit_id_used_ = 0;
  wit_busy_hist_[0] = wit_busy_hist_[1] = wit_busy_hist_[2] = 0;
  wit_older_ = 0;
  wit_load_pass_[0] = wit_load_pass_[1] = wit_load_pass_[2] = 0;
  for (uint32_t i = 0; i < WIT_IDS; i++) {
    wit_id_key_[i] = 0;
    wit_id_last_[i] = 0;
  }
}
#endif

namespace {
// CANCTRL REQOP
const uint8_t MODE_NORMAL = 0x00;
const uint8_t MODE_CONFIG = 0x80;
const uint8_t MODE_LOOPBACK = 0x40;
const uint8_t MODE_LISTEN_ONLY = 0x60;
const uint8_t MODE_MASK = 0xE0;

// READ STATUS bit assignments -- DS20001801J p70, Figure 12-8.
//
// The three TXnIF bits were unused until 2026-10-04. They are the chip's
// own record of a successful transmission (p15 s3.3), and they arrive in
// the byte readStatus() already fetches, so spec 2.2 can tell a frame that
// reached the wire from one that did not without a single extra SPI
// transaction.
const uint8_t ST_RX0IF = 0x01;    // bit 0
const uint8_t ST_RX1IF = 0x02;    // bit 1
const uint8_t ST_TX0REQ = 0x04;   // bit 2
const uint8_t ST_TX0IF = 0x08;    // bit 3
const uint8_t ST_TX1REQ = 0x10;   // bit 4
const uint8_t ST_TX1IF = 0x20;    // bit 5
const uint8_t ST_TX2REQ = 0x40;   // bit 6
const uint8_t ST_TX2IF = 0x80;    // bit 7

// CANINTF -- p63, Table 11-2. TX0IF is bit 2, so TXnIF is INTF_TX0IF << n.
// NOTE these are NOT the READ STATUS positions above; the same flags sit
// at different offsets in the two.
const uint8_t INTF_TX0IF = 0x04;

// EFLG
const uint8_t EFLG_RX1OVR = 0x80;
const uint8_t EFLG_RX0OVR = 0x40;
const uint8_t EFLG_TXBO = 0x20;
}  // namespace

Mcp2515Port::Mcp2515Port(SPIClass& spi, uint8_t cs_pin, uint8_t int_pin,
                         uint32_t spi_hz)
    : spi_(spi),
      settings_(spi_hz, MSBFIRST, SPI_MODE0),
      cs_(cs_pin),
      int_(int_pin),
      rx_count_(0),
      tx_count_(0),
      tx_dropped_(0),
      tx_failed_(0),
      tx_aged_(0),
      tx_late_(0),
      tx_unexplained_(0),
      now_ms_(0),
      tx_busy_since_{0, 0, 0},
      tx_busy_{false, false, false},
      tx_aborting_{false, false, false},
      tx_abort_us_{0, 0, 0},
      tx_holds_{false, false, false},
      tx_hold_defer_(0),
      tx_frames_defer_(0),
      tx_head_deferred_(false),
      tx_held_id_{0, 0, 0},
      tx_held_ext_{false, false, false},
      rx_overruns_(0),
      bus_errors_(0),
      last_eflg_(0),
      bus_off_(false),
      bus_off_events_(0),
      ready_(false),
      loopback_(false),
      listen_only_(false) {}

void Mcp2515Port::select() {
  spi_.beginTransaction(settings_);
  digitalWrite(cs_, LOW);
}

void Mcp2515Port::deselect() {
  digitalWrite(cs_, HIGH);
  spi_.endTransaction();
}

uint8_t Mcp2515Port::readReg(uint8_t addr) {
  select();
  spi_.transfer(CMD_READ);
  spi_.transfer(addr);
  const uint8_t v = spi_.transfer(0x00);
  deselect();
  return v;
}

void Mcp2515Port::writeReg(uint8_t addr, uint8_t val) {
  select();
  spi_.transfer(CMD_WRITE);
  spi_.transfer(addr);
  spi_.transfer(val);
  deselect();
}

void Mcp2515Port::modifyReg(uint8_t addr, uint8_t mask, uint8_t val) {
  select();
  spi_.transfer(CMD_BIT_MODIFY);
  spi_.transfer(addr);
  spi_.transfer(mask);
  spi_.transfer(val);
  deselect();
}

uint8_t Mcp2515Port::readStatus() {
  select();
  spi_.transfer(CMD_READ_STATUS);
  const uint8_t v = spi_.transfer(0x00);
  deselect();
  return v;
}

bool Mcp2515Port::setMode(uint8_t mode) {
  modifyReg(REG_CANCTRL, MODE_MASK, mode);
  // Mode changes are not instant; the datasheet has us confirm via CANSTAT.
  // Bounded and short -- this runs only at init, never in the bridge path.
  for (uint8_t i = 0; i < 20; i++) {
    if ((readReg(REG_CANSTAT) & MODE_MASK) == mode) return true;
    delayMicroseconds(200);
  }
  return false;
}

// Bit timing for the board's 16 MHz crystal.
//
//   TQ       = 2 * (BRP + 1) / Fosc
//   bit time = SYNC(1) + PROP + PS1 + PS2 in TQ
//
// 500 kbps: BRP=0 -> TQ = 125 ns, 16 TQ per bit = 2 us. PROP=1, PS1=7, PS2=7.
// Sample point 56.25 %, which is what every MCP2515 table in circulation uses
// for this crystal and what the charger segment will be running.
bool Mcp2515Port::setBitrate(uint32_t bitrate) {
  uint8_t cnf1, cnf2, cnf3;
  switch (bitrate) {
    case 1000000UL: cnf1 = 0x00; cnf2 = 0xD0; cnf3 = 0x82; break;
    case 500000UL:  cnf1 = 0x00; cnf2 = 0xF0; cnf3 = 0x86; break;
    case 250000UL:  cnf1 = 0x41; cnf2 = 0xF1; cnf3 = 0x85; break;
    case 125000UL:  cnf1 = 0x03; cnf2 = 0xF0; cnf3 = 0x86; break;
    default: return false;
  }
  writeReg(REG_CNF1, cnf1);
  writeReg(REG_CNF2, cnf2);
  writeReg(REG_CNF3, cnf3);
  return true;
}

bool Mcp2515Port::begin(uint32_t bitrate) {
  pinMode(cs_, OUTPUT);
  digitalWrite(cs_, HIGH);
  pinMode(int_, INPUT_PULLUP);  // MCP2515 INT is open-drain, active low

  select();
  spi_.transfer(CMD_RESET);
  deselect();
  delay(10);

  if (!setMode(MODE_CONFIG)) return false;
  if (!setBitrate(bitrate)) return false;

  // Accept everything. RXM = 11 turns masks and filters off; BUKT lets RXB0
  // roll over into RXB1, which is the difference between two receive buffers
  // and one. A bridge filters in software or not at all -- the MCP2515's two
  // masks and six filters cannot express "everything except what I originate".
  writeReg(REG_RXB0CTRL, 0x64);  // RXM=11, BUKT
  writeReg(REG_RXB1CTRL, 0x60);  // RXM=11

  // Interrupt on receive only. TX completion is picked up from READ STATUS in
  // service(), which costs one byte and covers all three buffers at once.
  writeReg(REG_CANINTE, ST_RX0IF | ST_RX1IF);
  writeReg(REG_CANINTF, 0x00);

  uint8_t want = MODE_NORMAL;
  if (loopback_) want = MODE_LOOPBACK;
  else if (listen_only_) want = MODE_LISTEN_ONLY;
  if (!setMode(want)) return false;
  ready_ = true;
  return true;
}

// Pull one frame out of RXBn and into the ring. The READ RX BUFFER instruction
// auto-clears the matching CANINTF flag on CS release, so no follow-up write.
void Mcp2515Port::readRxBuffer(uint8_t n, uint32_t t_ms) {
  uint8_t b[13];
  select();
  spi_.transfer((uint8_t)(CMD_READ_RX | (n ? 0x04 : 0x00)));
  for (uint8_t i = 0; i < 13; i++) b[i] = spi_.transfer(0x00);
  deselect();

  const uint8_t sidh = b[0];
  const uint8_t sidl = b[1];
  const bool ext = (sidl & 0x08) != 0;
  uint32_t id;
  if (ext) {
    id = ((uint32_t)sidh << 21) | ((uint32_t)(sidl & 0xE0) << 13) |
         ((uint32_t)(sidl & 0x03) << 16) | ((uint32_t)b[2] << 8) | b[3];
  } else {
    id = ((uint32_t)sidh << 3) | (uint32_t)(sidl >> 5);
  }
  uint8_t len = b[4] & 0x0F;
  if (len > 8) len = 8;

  // Remote frames carry no data and must not be forwarded as if they did.
  // Nothing on this bus uses them; drop rather than guess.
  const bool rtr = ext ? ((b[4] & 0x40) != 0) : ((sidl & 0x10) != 0);
  if (rtr) return;

  rx_.push(id, ext, &b[5], len, t_ms);
  rx_count_++;
}

bool Mcp2515Port::loadAndSend(uint8_t buf_n, const RxFrame& f) {
  uint8_t h[5];
  if (f.ext) {
    h[0] = (uint8_t)(f.id >> 21);
    h[1] = (uint8_t)(((f.id >> 13) & 0xE0) | 0x08 | ((f.id >> 16) & 0x03));
    h[2] = (uint8_t)(f.id >> 8);
    h[3] = (uint8_t)(f.id);
  } else {
    h[0] = (uint8_t)(f.id >> 3);
    h[1] = (uint8_t)((f.id << 5) & 0xE0);
    h[2] = 0;
    h[3] = 0;
  }
  h[4] = f.len;

  // Spec 2.2: TXnIF is the delivery signal, so it must start clear.
  // CANINTF is bit-modifiable (DS20001801J p63 Table 11-1, shaded), so
  // this touches one bit and leaves the RX and error flags alone -- on
  // a register that was NOT bit-modifiable the chip would force the
  // mask to FFh and this would wipe them (p66 s12.10).
  modifyReg(REG_CANINTF, (uint8_t)(INTF_TX0IF << buf_n), 0x00);

  select();
  spi_.transfer((uint8_t)(CMD_LOAD_TX | (uint8_t)(buf_n * 2)));
  for (uint8_t i = 0; i < 5; i++) spi_.transfer(h[i]);
  for (uint8_t i = 0; i < f.len; i++) spi_.transfer(f.data[i]);
  deselect();

  // Request to send. Returns immediately -- the wire time is the controller's
  // problem, not ours. This is the whole reason for the custom driver.
  select();
  spi_.transfer((uint8_t)(CMD_RTS | (uint8_t)(1u << buf_n)));
  deselect();

  tx_count_++;
#ifdef INTP_ORDER_WITNESS
  // The witness's own record of what went into this buffer, taken here
  // rather than read back from tx_held_id_ -- see the header.
  wit_load_seq_[buf_n] = wit_loads_++;
  wit_id_[buf_n] = f.id;
  wit_ext_[buf_n] = (uint8_t)(f.ext ? 1 : 0);
#endif
  return true;
}

void Mcp2515Port::drainTx() {
  const uint8_t st = readStatus();
  const uint8_t busy[3] = {ST_TX0REQ, ST_TX1REQ, ST_TX2REQ};
#ifdef INTP_TX_TRACE
  // See port_mcp2515.h: compiled out of every firmware image.
  TxPass pass;
  pass.us = micros();
  pass.ms = now_ms_;
  pass.ring = tx_.used();
  pass.loaded = 0;
  pass.aged = 0;
  pass.state[0] = pass.state[1] = pass.state[2] = '.';
#define TXTRACE(n, c) do { pass.state[(n)] = (c); } while (0)
#else
#define TXTRACE(n, c) do { } while (0)
#endif
#ifdef INTP_ORDER_WITNESS
  wit_pass_++;
  uint8_t wit_in_pass = 0;
  // 'F' never reached the wire, so it is recorded but is NOT a
  // completion and does not make a pass ambiguous.
#define WITNESS(n, oc)                                               \
  do {                                                               \
    if ((oc) != 'F') {                                               \
      if (++wit_in_pass == 2) wit_multi_++;                          \
      wit_total_++;                                                  \
      witnessNote(wit_load_seq_[(n)], wit_id_[(n)], wit_ext_[(n)]);  \
    }                                                                \
    if (wit_count_ < WITNESS_N) {                                    \
      Completion& w = wit_[wit_count_++];                            \
      w.load_seq = wit_load_seq_[(n)];                               \
      w.id = wit_id_[(n)];                                           \
      w.pass = wit_pass_;                                            \
      w.buf = (n);                                                   \
      w.outcome = (oc);                                              \
      w.ext = wit_ext_[(n)];                                         \
      w.pad = 0;                                                     \
    }                                                                \
  } while (0)
#else
#define WITNESS(n, oc) do { } while (0)
#endif

  // Spec 2.2, part one: a transmission that has been pending longer than
  // TX_MAX_AGE_MS is abandoned.
  //
  // Retries are deliberately left ON (no one-shot mode): on a healthy
  // segment a frame that merely loses arbitration should be retried, and
  // one-shot would discard it. What must not happen is a frame retrying
  // forever because nothing is acknowledging -- that is what pinned all
  // three hardware buffers and filled the ring behind them. Aborting on age
  // keeps normal retries and bounds staleness.
  // TXnIF is the delivery signal (DS20001801J p15 s3.3: the chip sets it
  // on successful transmission). loadAndSend() clears it before each load,
  // so for an idle buffer: TXnIF set means delivered, TXnIF clear means
  // abandoned. It arrives in the READ STATUS byte already read above
  // (p70 Fig 12-8, bits 3/5/7), so none of this costs an extra transaction.
  const uint8_t txif[3] = {ST_TX0IF, ST_TX1IF, ST_TX2IF};

  for (uint8_t n = 0; n < 3; n++) {
    const uint8_t ctrl_reg = (uint8_t)(REG_TXB0CTRL + n * 0x10);

    if (tx_aborting_[n]) {
      // We cleared TXREQ on this buffer and do not yet know what became of
      // the frame. A message already transmitting at that moment runs to
      // completion (p16 s3.6 note 1), so the answer is only certain once
      // the longest possible frame could have finished. Until then the
      // buffer stays unloaded -- which also keeps us from writing a buffer
      // whose frame is still on the wire, a case p15 does not cover.
      if ((uint32_t)(micros() - tx_abort_us_[n]) < TX_ABORT_RESOLVE_US) {
        TXTRACE(n, 'W');
        continue;
      }
      // A FRESH status read. `st` was taken at the top of the pass,
      // before the wait condition was even tested, so deciding on it
      // would answer with a byte read earlier than the moment the
      // answer became valid. One extra transaction, only at the
      // instant an abort resolves.
      if (readStatus() & txif[n]) {
        // It went out after we gave up on it. The charger acknowledged, so
        // this is NOT a failure under spec 2.2 -- the counter means frames
        // that did not reach the wire.
        tx_late_++;
        TXTRACE(n, 'L');
        WITNESS(n, 'L');
      } else {
        tx_failed_++;
        TXTRACE(n, 'F');
        WITNESS(n, 'F');
      }
      modifyReg(REG_CANINTF, (uint8_t)(INTF_TX0IF << n), 0x00);
      tx_aborting_[n] = false;
      tx_busy_[n] = false;
      // Spec 2: the outcome is now KNOWN -- late ('L') or failed ('F')
      // -- so the identifier is released. Not released at the abort
      // itself: between clearing TXREQ and this point the frame may
      // still be on the wire, and a successor loaded in that window
      // could overtake it.
      tx_holds_[n] = false;
      continue;
    }

    if (st & busy[n]) {
      TXTRACE(n, 'B');
      // Spec 2.2, part one: a transmission pending longer than
      // TX_MAX_AGE_MS is abandoned. Clearing TXREQ does NOT by itself mean
      // the frame failed, so nothing is counted here -- the resolve step
      // above decides, once it can.
      if (tx_busy_[n] &&
          (uint32_t)(now_ms_ - tx_busy_since_[n]) > TX_MAX_AGE_MS) {
        modifyReg(ctrl_reg, TXB_TXREQ, 0x00);
        tx_aborting_[n] = true;
        TXTRACE(n, 'X');
        // Stamped AFTER the clear, not from a micros() taken before
        // the loop: TXREQ goes down inside the modifyReg above, which
        // runs after any earlier buffer's BIT MODIFY in this same
        // pass. Using the pass-start time would start the clock early
        // and eat the margin between the 366 us bound and the 400 us
        // wait.
        tx_abort_us_[n] = micros();
      }
      continue;
    }

    if (tx_busy_[n]) {
      // Idle without an abort: the chip cleared TXREQ itself, which it does
      // only on success (p15 s3.3). TXnIF confirms it.
      //
      // This replaced a branch that counted ABTF|TXERR here. That branch
      // could never count a real failure: with no ABAT and no one-shot the
      // chip never sets ABTF (p16 s3.6 note 2), and it clears TXREQ only on
      // success -- so every buffer it inspected had been delivered.
      if (!(st & txif[n])) {
        // This should be unreachable. p15 s3.3: the chip clears TXREQ
        // only on successful transmission, and that sets TXnIF. So an
        // idle buffer we never aborted, with TXnIF clear, means one of
        // the datasheet behaviours this driver is built on does not
        // hold on real silicon -- which is worth far more than the one
        // frame. Counted separately and reported on the serial LOSS
        // line, AND counted as a failure, because with no evidence of
        // delivery the conservative reading for spec 2.2 is that it
        // did not arrive.
        tx_unexplained_++;
        tx_failed_++;
        TXTRACE(n, 'U');
        WITNESS(n, 'U');
      } else {
        TXTRACE(n, 'C');
        WITNESS(n, 'C');
      }
      modifyReg(REG_CANINTF, (uint8_t)(INTF_TX0IF << n), 0x00);
      tx_busy_[n] = false;
      // Spec 2: delivered ('C') or unexplained ('U'). Either way the
      // buffer is free and its identifier is released.
      tx_holds_[n] = false;
    }
  }

  // Spec 2.2, part two: never hand the charger a stale frame from the ring.
  for (uint8_t n = 0; n < 3; n++) {
    if (tx_.empty()) break;
    if (tx_aborting_[n]) continue;   // outcome not yet known

    // ONLY REFILL A BUFFER THE LOOP ABOVE HAS ALREADY RESOLVED.
    //
    // `tx_busy_[n]` is set at load and cleared by that loop when it
    // books the outcome ('C', 'U', 'L' or 'F'). Still set here means
    // the loop judged this buffer from `st`, read at the TOP of the
    // pass, and found it busy. The `readStatus()` below is a fresh
    // read: if the frame completed in between, the buffer now looks
    // idle and would be loaded over, and its completion would never be
    // booked at all.
    //
    // Found by the review session 2026-10-04, in the completion-order
    // witness: at the bridge's ~60 us pass period, 1,501 of 20,000
    // delivered frames were never resolved, with nothing flagging it.
    // The driver's own counters could not show it -- a normal
    // completion increments nothing -- and the single-identifier tests
    // could not either, because the same-identifier hold already stops
    // a buffer being refilled while that id is outstanding, which
    // closes this window by accident. It takes mixed identifiers and a
    // short service period to open.
    //
    // THIS IS NOT GATED BEHIND THE WITNESS. An instrument that is only
    // accurate about a driver nobody ships is not an instrument. The
    // cost is that a buffer which completes just after the pass began
    // waits one more pass (~60 us) before being refilled, while the
    // other two keep the wire busy.
    if (tx_busy_[n]) continue;
    const uint8_t st_now = readStatus();
    if (st_now & busy[n]) continue;

    // Spec 2, part three: age out whatever is stale at the HEAD first.
    // Unchanged in effect from the old loop, but it now inspects the
    // head in place rather than popping, because the hold below has to
    // decide about that exact frame and leave it where it is if the
    // answer is no.
    const RxFrame* head;
    for (;;) {
      head = tx_.peek();
      if (head == 0) break;
      if ((uint32_t)(now_ms_ - head->t_ms) <= TX_MAX_AGE_MS) break;
      RxFrame stale;
      tx_.pop(stale);
      tx_aged_++;    // superseded while it waited; drop it, do not send it
      // It never loaded, so it is not a deferred FRAME -- and the flag
      // must not be inherited by whatever becomes the new head, which
      // would book a deferral against a frame that was never blocked.
      tx_head_deferred_ = false;
#ifdef INTP_TX_TRACE
      pass.aged++;
#endif
    }
    if (head == 0) break;

    // THE SAME-IDENTIFIER HOLD (spec 2, user 2026-10-04).
    //
    // If this identifier is already in a hardware buffer, the frame
    // STAYS AT THE HEAD and nothing behind it is loaded either. The
    // blocking is deliberate and head-of-line: skipping past it to
    // find a loadable frame would deliver a later frame first, which
    // is reordering across identifiers introduced by us rather than by
    // the chip -- and worse, it would be a reordering the spec's
    // different-identifier exception does not cover, because it would
    // not be bounded by anything.
    //
    // `break`, not `continue`: with the head blocked there is nothing
    // any remaining buffer may legally be given this pass.
    //
    // The 2.2 age-out above still runs while it waits, so a frame held
    // here long enough is discarded rather than delivered stale.
    if (idHeld(head->id, head->ext)) {
      tx_hold_defer_++;      // PASSES blocked; see txHoldDeferrals()
      tx_head_deferred_ = true;   // this FRAME was held at least once
      break;
    }

    RxFrame f;
    tx_.pop(f);
    if (tx_head_deferred_) {
      tx_frames_defer_++;         // counted when it finally loads
      tx_head_deferred_ = false;
    }
#ifdef INTP_ORDER_WITNESS
    // How many OTHER buffers hold a requested frame at this moment.
    // Read from the status byte taken just above -- the CHIP's view,
    // not the driver's tx_busy_ bookkeeping, since the point is to
    // compare against what silicon does.
    {
      uint8_t others = 0;
      uint8_t older = 0;
      for (uint8_t m = 0; m < 3; m++) {
        if (m == n) continue;
        if (!(st_now & busy[m])) continue;
        others++;
        // Loaded in an EARLIER pass, so it has had a pass in which it
        // could have started. The nearest the driver can get to
        // "pending and not yet started", which is what an inversion
        // actually needs; it cannot observe SOF.
        if (tx_busy_[m] && wit_load_pass_[m] < wit_pass_) older++;
      }
      if (others < 3) wit_busy_hist_[others]++;
      if (older) wit_older_++;
      wit_load_pass_[n] = wit_pass_;
    }
#endif
    loadAndSend(n, f);
    tx_busy_[n] = true;
    tx_busy_since_[n] = now_ms_;
    tx_holds_[n] = true;
    tx_held_id_[n] = f.id;
    tx_held_ext_[n] = f.ext;
#ifdef INTP_TX_TRACE
    pass.loaded++;
#endif
  }
#ifdef INTP_TX_TRACE
  trace_passes_++;
  if (trace_count_ < TRACE_N) trace_[trace_count_++] = pass;
#endif
#undef TXTRACE
#undef WITNESS
}

bool Mcp2515Port::send(uint32_t id, bool ext, const uint8_t* data,
                       uint8_t len) {
  // Spec 2.2: stamp every queued frame so it can be aged out. This used to
  // store 0, which made a queued frame immortal: with nothing acknowledging
  // on the charger segment the three hardware buffers never complete, the
  // ring fills within ~30 ms, and a charger coming back on the bus was met
  // with a burst of up to 66 frames describing a charge that had already
  // moved on.
  if (!ready_) {
    tx_dropped_++;   // counted here, so the caller does not count it again
    return false;
  }
  if (!tx_.push(id, ext, data, len, now_ms_)) {
    tx_dropped_++;
    return false;
  }
  return true;
}

bool Mcp2515Port::receive(RxFrame& out) { return rx_.pop(out); }

void Mcp2515Port::service(uint32_t t_ms) {
  if (!ready_) return;
  now_ms_ = t_ms;

  // The IRQ line is level-triggered and stays asserted while any enabled flag
  // is set, so draining until it releases costs nothing when idle and cannot
  // miss a frame that arrived during the drain.
  uint8_t guard = 0;
  while (digitalRead(int_) == LOW && guard++ < 8) {
    const uint8_t st = readStatus();
    if (st & ST_RX0IF) {
      readRxBuffer(0, t_ms);
    } else if (st & ST_RX1IF) {
      readRxBuffer(1, t_ms);
    } else {
      break;  // IRQ asserted by something we did not enable
    }
  }

  drainTx();

  const uint8_t eflg = readReg(REG_EFLG);

  // Controller-level overrun: a frame reached the wire and we did not read
  // it in time. Handled OUTSIDE the change detection below, and counted
  // into rx_dropped_, for two reasons -- both of which were defects until
  // 2026-10-04.
  //
  // 1. `can_port.h` defines rxDropped() as "controller overrun, frames
  //    lost". These were going into bus_errors_, which means something
  //    else entirely: a malformed frame or an unhealthy bus, not a frame
  //    we were too slow for. main.cpp was written to the contract, so
  //    spec 8.2's bridge_ok -- fed from the ports' rxDropped() -- stayed
  //    SET when a charger frame was lost at the chip and never reached
  //    the VCU, which is exactly the transparency failure the flag is
  //    supposed to report.
  //
  // 2. Inside the `eflg != last_eflg_` guard, only the FIRST overrun was
  //    ever seen. The guard stored eflg with the OVR bits still in it and
  //    then cleared those bits on the chip, so the next overrun with
  //    nothing else changing compared equal, and was neither counted nor
  //    cleared -- and so was every one after it, for the life of the run.
  //
  // The chip gives one flag per buffer, not a count, so this is at best
  // one per service() pass: a bound on how many were lost, not a tally.
  if (eflg & (EFLG_RX0OVR | EFLG_RX1OVR)) {
    if (eflg & EFLG_RX0OVR) rx_overruns_++;
    if (eflg & EFLG_RX1OVR) rx_overruns_++;
    modifyReg(REG_EFLG, (uint8_t)(EFLG_RX0OVR | EFLG_RX1OVR), 0x00);
  }

  // Everything else in EFLG is edge-detected. The OVR bits are masked out
  // of the stored value because they have just been cleared on the chip --
  // keeping them would make the next overrun compare equal, which is
  // defect 2 above.
  const uint8_t eflg_stable = (uint8_t)(eflg & ~(EFLG_RX0OVR | EFLG_RX1OVR));
  if (eflg_stable != last_eflg_) {
    last_eflg_ = eflg_stable;
    bus_errors_++;
  }
  const bool off = (eflg & EFLG_TXBO) != 0;
  if (off && !bus_off_) bus_off_events_++;
  bus_off_ = off;
}

// can_port.h: "controller overrun, frames lost". BOTH kinds of loss
// count -- our ring overflowing and the chip's receive buffers
// overrunning. Returning only the ring's count was what kept a frame
// lost at the chip out of spec 8.2's bridge_ok.
uint32_t Mcp2515Port::rxDropped() const {
  return rx_.dropped() + rx_overruns_;
}

void Mcp2515Port::end() {
  setMode(MODE_CONFIG);
  rx_.clear();
  tx_.clear();
  ready_ = false;
}

}  // namespace interposer
