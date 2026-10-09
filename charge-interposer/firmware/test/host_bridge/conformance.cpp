#include "conformance.h"

#include <cstdio>
#include <cstring>

namespace mcpconf {

using namespace mcpfake;

// --------------------------------------------------------------------------
// Primitives. Every one of these is a raw SPI instruction from p67 Table
// 12-1, so the bench target inherits them unchanged.
// --------------------------------------------------------------------------

uint8_t readReg(Target& t, uint8_t addr) {
  t.csLow();
  t.transfer(OP_READ);
  t.transfer(addr);
  const uint8_t v = t.transfer(0x00);
  t.csHigh();
  return v;
}

void writeReg(Target& t, uint8_t addr, uint8_t value) {
  t.csLow();
  t.transfer(OP_WRITE);
  t.transfer(addr);
  t.transfer(value);
  t.csHigh();
}

void bitModify(Target& t, uint8_t addr, uint8_t mask, uint8_t value) {
  t.csLow();
  t.transfer(OP_BIT_MODIFY);
  t.transfer(addr);
  t.transfer(mask);
  t.transfer(value);
  t.csHigh();
}

uint8_t readStatus(Target& t) {
  t.csLow();
  t.transfer(OP_READ_STATUS);
  const uint8_t v = t.transfer(0x00);
  t.csHigh();
  return v;
}

void reset(Target& t) {
  t.csLow();
  t.transfer(OP_RESET);
  t.csHigh();
  t.advanceUs(100);
}

void loadBuffer(Target& t, int n, uint32_t id, uint8_t tag, uint8_t dlc) {
  // LOAD TX BUFFER, p67 Table 12-1: 0100 0abc, abc selecting the buffer
  // and the start address. abc = n*2 starts at TXBnSIDH.
  t.csLow();
  t.transfer((uint8_t)(OP_LOAD_TX | (n << 1)));
  t.transfer((uint8_t)(id >> 3));            // SIDH
  t.transfer((uint8_t)((id & 0x07) << 5));   // SIDL, standard frame
  t.transfer(0x00);                          // EID8
  t.transfer(0x00);                          // EID0
  t.transfer((uint8_t)(dlc & 0x0F));         // DLC
  for (uint8_t i = 0; i < 8; i++)
    t.transfer(i == 0 ? tag : (uint8_t)(0xA0 + i));
  t.csHigh();
}

void rtsTogether(Target& t, uint8_t mask) {
  t.csLow();
  t.transfer((uint8_t)(OP_RTS | (mask & 0x07)));
  t.csHigh();
}

void rtsSeparately(Target& t, uint8_t mask) {
  for (int n = 0; n < 3; n++) {
    if (!(mask & (1 << n))) continue;
    t.csLow();
    t.transfer((uint8_t)(OP_RTS | (1 << n)));
    t.csHigh();
  }
}

// --------------------------------------------------------------------------
// Recorder
// --------------------------------------------------------------------------

void Recorder::hex8(const char* step, const char* key, uint8_t v,
                    Status status, const char* cite) {
  char buf[8];
  std::snprintf(buf, sizeof(buf), "0x%02X", v);
  note(step, key, buf, status, cite);
}

void Recorder::dec(const char* step, const char* key, uint32_t v,
                   Status status, const char* cite) {
  char buf[16];
  std::snprintf(buf, sizeof(buf), "%u", v);
  note(step, key, buf, status, cite);
}

void Recorder::print(const char* subject) const {
  std::printf("# MCP2515 conformance transcript\n");
  std::printf("# subject: %s\n", subject);
  std::printf("# Diff this against the other subject's transcript. A\n");
  std::printf("# DATASHEET line that differs means the model or the\n");
  std::printf("# reading is wrong; an UNKNOWN/DEFERRED line that differs\n");
  std::printf("# is the bench answering what the document would not.\n");
  std::printf("#\n");
  std::printf("# %-28s %-26s %-12s %-10s %s\n",
              "STEP", "KEY", "VALUE", "STATUS", "RESTS ON");
  for (size_t i = 0; i < obs_.size(); i++) {
    const Observation& o = obs_[i];
    std::printf("  %-28s %-26s %-12s %-10s %s\n",
                o.step.c_str(), o.key.c_str(), o.value.c_str(),
                statusName(o.status), o.cite.c_str());
  }
}

// --------------------------------------------------------------------------
// The sequence
// --------------------------------------------------------------------------

namespace {

const char* P63 = "p63 Table 11-2";
const char* P70 = "p70 Fig 12-8 + Stage 1 anchor";
const char* P47 = "p47 s6.7 -> ISO 11898";

// Enough wall/virtual time for a standard 8-byte frame at 500 kbit (~270 us
// including worst-case stuffing) with room to spare, but short enough that
// a step measuring ORDER cannot accidentally let a second frame finish
// before we look.
const uint64_t ONE_FRAME_US = 1200;

std::string tagList(const std::vector<uint8_t>& tags) {
  if (tags.empty()) return "(none)";
  std::string s;
  char buf[8];
  for (size_t i = 0; i < tags.size(); i++) {
    std::snprintf(buf, sizeof(buf), "%u", tags[i]);
    if (i) s += ",";
    s += buf;
  }
  return s;
}

void intoNormalMode(Target& t) {
  writeReg(t, R_CANCTRL, MODE_NORMAL);
  t.advanceUs(100);
}

// --- C1 -------------------------------------------------------------------
void stepResetDefaults(Target& t, Recorder& r) {
  reset(t);
  const char* S = "C1-reset-defaults";
  r.hex8(S, "CANSTAT.mode", (uint8_t)(readReg(t, R_CANSTAT) & MODE_MASK),
         DATASHEET, "p60 s10.0: RESET enters Configuration mode");
  r.hex8(S, "TXB0CTRL", readReg(t, R_TXB0CTRL), DATASHEET, P63);
  r.hex8(S, "TXB1CTRL", readReg(t, (uint8_t)(R_TXB0CTRL + 0x10)), DATASHEET,
         P63);
  r.hex8(S, "TXB2CTRL", readReg(t, (uint8_t)(R_TXB0CTRL + 0x20)), DATASHEET,
         P63);
  r.hex8(S, "CANINTF", readReg(t, R_CANINTF), DATASHEET, P63);
  r.hex8(S, "EFLG", readReg(t, R_EFLG), DATASHEET, P63);
  r.hex8(S, "TEC", readReg(t, R_TEC), DATASHEET, P63);
  r.hex8(S, "REC", readReg(t, R_REC), DATASHEET, P63);
}

// --- C2: BIT MODIFY, both directions --------------------------------------
//
// The forced-mask case is the one that matters to us: on a register that is
// NOT bit-modifiable the chip silently widens the mask to FFh, so a driver
// that bit-modifies TEC or CANSTAT wipes the whole register and nothing
// reports it.
void stepBitModify(Target& t, Recorder& r) {
  const char* S = "C2-bit-modify";

  writeReg(t, R_CANINTF, 0x00);
  bitModify(t, R_CANINTF, INTF_TX0IF, INTF_TX0IF);
  r.hex8(S, "CANINTF.after-set-TX0IF", readReg(t, R_CANINTF), DATASHEET,
         "p63 Table 11-1: CANINTF is shaded = bit-modifiable");

  writeReg(t, R_CANINTF, 0xFF);
  bitModify(t, R_CANINTF, INTF_TX0IF, 0x00);
  r.hex8(S, "CANINTF.after-clear-one", readReg(t, R_CANINTF), DATASHEET,
         "only the masked bit changes");

  // A register that is not shaded in Table 11-1 and IS writable. Writing
  // 0x22 under a one-bit mask must land as a WHOLE BYTE write if the chip
  // forces the mask to FFh; if the mask were honoured the result would be
  // 0x00, since bit 0 of 0x22 is clear.
  //
  // This was TEC at first, which proved nothing: TEC is ALSO read-only, so
  // the write was dropped before the mask mattered and the step returned
  // 0x00 -- the same value a chip that honoured the mask would give. Two
  // properties, one observation, and the reading was ambiguous between
  // them. TXB0SIDH is non-bit-modifiable and plainly writable, so only the
  // mask can explain the result.
  const uint8_t TXB0SIDH = (uint8_t)(R_TXB0CTRL + 1);
  writeReg(t, TXB0SIDH, 0x00);
  bitModify(t, TXB0SIDH, 0x01, 0x22);
  r.hex8(S, "TXB0SIDH.after-masked-write", readReg(t, TXB0SIDH), DATASHEET,
         "p66 s12.10: mask forced to FFh on non-modifiable registers");

  // And the read-only counters really are read-only -- worth its own line,
  // because a chip that let TEC be written would make every no-ACK reading
  // in C9 meaningless.
  writeReg(t, R_TEC, 0x55);
  r.hex8(S, "TEC.after-direct-write", readReg(t, R_TEC), DATASHEET,
         "p47 s6.7: TEC is maintained by the chip, not host-writable");
}

// --- C3: the READ STATUS bit map ------------------------------------------
//
// The anchor for the whole figure's orientation. If the real chip disagrees
// here, every TXnIF read in the driver is reading the wrong bit.
void stepReadStatusMap(Target& t, Recorder& r) {
  const char* S = "C3-read-status-map";
  reset(t);
  intoNormalMode(t);
  t.setAcknowledged(true);
  t.clearWire();

  r.hex8(S, "status.idle", readStatus(t), DATASHEET, P70);

  loadBuffer(t, 0, 0x201, 0, 8);
  rtsTogether(t, 0x01);
  // Look while it is still on the wire: TXREQ must be set, TX0IF clear.
  r.hex8(S, "status.TXB0-requested", readStatus(t), DATASHEET, P70);

  t.advanceUs(ONE_FRAME_US);
  r.hex8(S, "status.after-complete", readStatus(t), DATASHEET, P70);
  r.hex8(S, "CANINTF.after-complete", readReg(t, R_CANINTF), DATASHEET, P63);
}

// --- C4: are ABTF / MLOA / TXERR cleared when TXREQ is set? ---------------
//
// UNKNOWN in the table: p15 s3.3 says "when TXREQ is set", the p17
// flowchart clears them at the top of every attempt. Decides whether TXERR
// can read 1 on a frame that eventually went out.
void stepTxFlagsOnRequest(Target& t, Recorder& r) {
  const char* S = "C4-txflags-on-request";
  reset(t);
  intoNormalMode(t);
  t.clearWire();

  // Dirty the flags by hand, then request and look.
  writeReg(t, R_TXB0CTRL, (uint8_t)(TXB_ABTF | TXB_MLOA | TXB_TXERR));
  r.hex8(S, "TXB0CTRL.dirtied", readReg(t, R_TXB0CTRL), ASSUMED,
         "set directly; the chip normally sets these itself");

  t.setAcknowledged(true);
  loadBuffer(t, 0, 0x202, 0, 8);
  rtsTogether(t, 0x01);
  r.hex8(S, "TXB0CTRL.after-RTS", readReg(t, R_TXB0CTRL), UNKNOWN,
         "p15 s3.3 vs p17 flowchart -- the bench decides");
  t.advanceUs(ONE_FRAME_US);
  r.hex8(S, "TXB0CTRL.after-complete", readReg(t, R_TXB0CTRL), DATASHEET,
         "p18 Register 3-1: TXREQ clears on success");
}

// --- C5 / C6: transmit ordering -------------------------------------------
//
// The question the whole TX-ordering investigation turns on, and the reason
// the wire observable is a tag rather than a buffer number.
void stepRtsTogether(Target& t, Recorder& r) {
  const char* S = "C5-rts-one-instruction";
  reset(t);
  intoNormalMode(t);
  t.setAcknowledged(true);
  t.clearWire();

  // Equal priority: TXP left at 00 in all three, exactly as the driver
  // leaves it.
  loadBuffer(t, 0, 0x210, 0, 8);
  loadBuffer(t, 1, 0x211, 1, 8);
  loadBuffer(t, 2, 0x212, 2, 8);
  rtsTogether(t, 0x07);
  t.advanceUs(ONE_FRAME_US * 4);

  r.note(S, "wire.tag-order", tagList(t.wireTags()), DATASHEET,
         "p15 s3.2: equal TXP -> highest buffer number first");
}

void stepRtsSeparately(Target& t, Recorder& r) {
  const char* S = "C6-rts-per-buffer";
  reset(t);
  intoNormalMode(t);
  t.setAcknowledged(true);
  t.clearWire();

  loadBuffer(t, 0, 0x210, 0, 8);
  loadBuffer(t, 1, 0x211, 1, 8);
  loadBuffer(t, 2, 0x212, 2, 8);
  // The driver's actual pattern. TXB0's request reaches an idle controller
  // and starts transmitting before TXB1 and TXB2 are requested at all, so
  // the tie-break only orders what queued behind it.
  rtsSeparately(t, 0x07);
  t.advanceUs(ONE_FRAME_US * 4);

  // NOT a datasheet claim, and the status matters more here than anywhere
  // else in the sequence.
  //
  // On the fake this order is determinate, because the model's SPI byte
  // time and frame time are fixed. ON SILICON IT IS NOT: whether TXB1 is
  // still waiting when TXB2 is requested depends on how long the three
  // RTS instructions take against how long the frame already on the wire
  // lasts. So the bench does not reproduce an ORDER here, it measures a
  // RATE -- how often the swap happens at a given offered load and frame
  // length.
  //
  // Marked DATASHEET until 2026-10-04, which would have made a different
  // order on the real chip read as "the model or the datasheet reading is
  // wrong" when it is neither. Raised by the review session.
  r.note(S, "wire.tag-order", tagList(t.wireTags()), UNKNOWN,
         "fake: determinate. Silicon: timing-dependent, measure a RATE");
}

// --- C7: TXP actually wins over buffer number -----------------------------
void stepTxPriority(Target& t, Recorder& r) {
  const char* S = "C7-txp-priority";
  reset(t);
  intoNormalMode(t);
  t.setAcknowledged(true);
  t.clearWire();

  loadBuffer(t, 0, 0x220, 0, 8);
  loadBuffer(t, 1, 0x221, 1, 8);
  loadBuffer(t, 2, 0x222, 2, 8);
  // Descending priority with ASCENDING buffer number: if TXP governs, the
  // order is 0,1,2 -- the exact reverse of the equal-TXP tie-break, so this
  // step cannot pass by accident if C5 passes.
  bitModify(t, R_TXB0CTRL, TXB_TXP_MASK, 0x03);
  bitModify(t, (uint8_t)(R_TXB0CTRL + 0x10), TXB_TXP_MASK, 0x02);
  bitModify(t, (uint8_t)(R_TXB0CTRL + 0x20), TXB_TXP_MASK, 0x01);
  rtsTogether(t, 0x07);
  t.advanceUs(ONE_FRAME_US * 4);

  r.note(S, "wire.tag-order", tagList(t.wireTags()), DATASHEET,
         "p15 s3.2: TXP decides before buffer number");
}

// --- C8: same identifier, varying DLC -------------------------------------
//
// Spec 2, now recorded as unconfirmed. Uniform DLC never reproduced the
// swap on the bench, so the step varies length deliberately.
void stepSameIdVaryingDlc(Target& t, Recorder& r) {
  const char* S = "C8-same-id-varying-dlc";
  reset(t);
  intoNormalMode(t);
  t.setAcknowledged(true);
  t.clearWire();

  loadBuffer(t, 0, 0x200, 0, 2);
  loadBuffer(t, 1, 0x200, 1, 8);
  loadBuffer(t, 2, 0x200, 2, 5);
  rtsTogether(t, 0x07);
  t.advanceUs(ONE_FRAME_US * 4);

  r.note(S, "wire.tag-order", tagList(t.wireTags()), UNKNOWN,
         "spec 2 unconfirmed 2026-10-04; the bench arm settles it");
}

// --- C9: a segment with nothing acknowledging -----------------------------
//
// The DEFERRED item, and the most valuable one on the bench. If a no-ACK
// transmitter DOES reach bus-off, busOffEvents() climbs all through every
// drive and spec 8.2's bridge_ok reads clear on every status frame of every
// journey -- a flag that is always false says nothing.
void stepNoAck(Target& t, Recorder& r) {
  const char* S = "C9-no-ack";
  if (!t.canWithholdAck()) {
    r.note(S, "skipped", "subject cannot withhold ACK", NOT_MODELLED,
           "bench: needs a segment with no other node");
    return;
  }
  reset(t);
  intoNormalMode(t);
  t.clearWire();
  t.setAcknowledged(false);

  loadBuffer(t, 0, 0x230, 0, 8);
  rtsTogether(t, 0x01);

  // Sample the trajectory rather than only the endpoint: WHERE it stops is
  // the answer, and an endpoint alone cannot distinguish "saturated at the
  // error-passive limit" from "cycled through bus-off and reset".
  //
  // The checkpoints are RELATIVE to the start of this step. They were
  // absolute at first, and since the earlier steps have already advanced
  // the clock well past the first few, every sample landed at the same
  // instant -- five identical TEC readings that still printed as a
  // trajectory.
  const uint64_t t0 = t.nowUs();
  const uint32_t checkpoints_us[] = {2000, 10000, 50000, 200000, 1000000};
  for (int i = 0; i < 5; i++) {
    const uint64_t target = t0 + checkpoints_us[i];
    while (t.nowUs() < target) {
      const uint64_t left = target - t.nowUs();
      t.advanceUs(left > 1000 ? 1000 : left);
    }
    char key[48];
    std::snprintf(key, sizeof(key), "TEC.at-%ums",
                  (unsigned)(checkpoints_us[i] / 1000));
    r.dec(S, key, readReg(t, R_TEC), DEFERRED, P47);
  }
  r.hex8(S, "EFLG.final", readReg(t, R_EFLG), DEFERRED, P47);
  r.hex8(S, "EFLG.TXBO-set", (uint8_t)((readReg(t, R_EFLG) & EFLG_TXBO) ? 1 : 0),
         DEFERRED, "p47 s6.7: bus-off reachability is the open question");
  r.hex8(S, "TXB0CTRL.final", readReg(t, R_TXB0CTRL), DEFERRED,
         "TXREQ should still be set: the message is retried forever");
  r.note(S, "wire.delivered", tagList(t.wireTags()), DATASHEET,
         "nothing is acknowledged, so nothing is delivered");

  t.setAcknowledged(true);
}

// --- C10: abort ------------------------------------------------------------
void stepAbort(Target& t, Recorder& r) {
  const char* S = "C10-abort";
  reset(t);
  intoNormalMode(t);
  t.setAcknowledged(true);
  t.clearWire();

  // Abort BEFORE the frame can start: load two, request both, then clear
  // TXREQ on the one still waiting.
  loadBuffer(t, 0, 0x240, 0, 8);
  loadBuffer(t, 1, 0x241, 1, 8);
  rtsTogether(t, 0x03);
  // TXB1 wins the tie-break and is on the wire; TXB0 is waiting behind it.
  bitModify(t, R_TXB0CTRL, TXB_TXREQ, 0x00);
  t.advanceUs(ONE_FRAME_US * 3);

  r.note(S, "wire.after-abort-of-waiting", tagList(t.wireTags()), DATASHEET,
         "p16 s3.6: clearing TXREQ on a buffer not yet sending drops it");
  r.hex8(S, "TXB0CTRL.after-abort", readReg(t, R_TXB0CTRL), DATASHEET,
         "p18 Register 3-1");
}

// --- C11: receive overrun flags -------------------------------------------
void stepRxOverrun(Target& t, Recorder& r) {
  const char* S = "C11-rx-overrun";
  reset(t);
  intoNormalMode(t);

  r.hex8(S, "EFLG.before", readReg(t, R_EFLG), DATASHEET, P63);
  // The model exposes overrun injection directly; on the bench this step is
  // driven by flooding the segment faster than service() drains it, so the
  // OBSERVABLE (EFLG bits and whether they are bit-modifiable away) is what
  // the two subjects share.
  bitModify(t, R_EFLG, (uint8_t)(EFLG_RX0OVR | EFLG_RX1OVR), 0x00);
  r.hex8(S, "EFLG.after-clear", readReg(t, R_EFLG), DATASHEET,
         "p63 Table 11-1: EFLG is bit-modifiable");
}

// --- C12: receive rollover and buffer order -------------------------------
//
// Reviewer request (tracker "RX rollover order", sent 2026-10-07): frames A
// and B arrive back to back, B while A is still unread; C arrives after A
// has been taken but before B has. This step settles, on each subject,
// WHERE each frame sits at every point -- which is all the chip decides.
// Whether a driver then delivers A, B, C or A, C, B is the driver's doing,
// and is judged through main.cpp in test_l2_bridge's rx-rollover-order
// case, not here.
//
// Observed by plain READ of RXBnD0 (0x66 / 0x76), which does not free a
// buffer, so looking does not change what is looked at. Every step is
// sequenced by the script; nothing is raced.
uint8_t rxFlags(Target& t) {
  return (uint8_t)(readReg(t, R_CANINTF) & (INTF_RX0IF | INTF_RX1IF));
}
uint8_t ovrFlags(Target& t) {
  return (uint8_t)(readReg(t, R_EFLG) & (EFLG_RX0OVR | EFLG_RX1OVR));
}

void stepRxRollover(Target& t, Recorder& r) {
  const char* S = "C12-rx-rollover";
  const char* P23 = "p23 s4.1.3 + s4.2.1";
  const char* P26 = "p26 Fig 4-3 (rendered)";
  const char* P68 = "p65 s12.4, p68 Fig 12-3";
  if (!t.canInject()) {
    r.note(S, "not-run", "no sender on the segment", UNKNOWN,
           "Target::canInject");
    return;
  }
  const uint32_t ID = 0x18FF60E5;
  reset(t);
  writeReg(t, R_RXB0CTRL, 0x64);     // RXM = 11 (any frame), BUKT
  writeReg(t, (uint8_t)(R_RXB0CTRL + 0x10), 0x60);   // RXB1CTRL, RXM = 11
  intoNormalMode(t);

  t.injectFrame(ID, true, 0xA0, 8);
  t.injectFrame(ID, true, 0xB0, 8);
  r.hex8(S, "AB.rx-flags", rxFlags(t), DATASHEET, P23);
  r.hex8(S, "AB.RXB0D0", readReg(t, 0x66), DATASHEET, P23);
  r.hex8(S, "AB.RXB1D0", readReg(t, 0x76), DATASHEET,
         "p23 s4.2.1: rolls over into RXB1");
  r.hex8(S, "AB.overflow", ovrFlags(t), DATASHEET, P26);

  // Take A as the driver would, with READ RX BUFFER on RXB0.
  t.csLow();
  t.transfer(OP_READ_RX);
  uint8_t got[13];
  for (int i = 0; i < 13; i++) got[i] = t.transfer(0);
  t.csHigh();
  r.hex8(S, "takeA.D0-read", got[5], DATASHEET, P68);
  r.hex8(S, "takeA.rx-flags", rxFlags(t), DATASHEET,
         "p65 s12.4: RX0IF cleared at CS high");

  t.injectFrame(ID, true, 0xC0, 8);
  r.hex8(S, "C.rx-flags", rxFlags(t), DATASHEET, P23);
  r.hex8(S, "C.RXB0D0", readReg(t, 0x66), DATASHEET,
         "p23 s4.1.3 / p26: RXB0 free, so it takes C");
  r.hex8(S, "C.RXB1D0", readReg(t, 0x76), DATASHEET,
         "B is still in RXB1, OLDER than C in RXB0");

  // Both full: D rolls to a full RXB1 and is lost.
  t.injectFrame(ID, true, 0xD0, 8);
  r.hex8(S, "D.overflow", ovrFlags(t), DATASHEET, P26);
  r.hex8(S, "D.RXB0D0", readReg(t, 0x66), DATASHEET, P26);
  r.hex8(S, "D.RXB1D0", readReg(t, 0x76), DATASHEET, P26);

  // Clearing RX1IF by BIT MODIFY frees RXB1 just as READ RX would.
  bitModify(t, R_CANINTF, INTF_RX1IF, 0x00);
  t.injectFrame(ID, true, 0xE0, 8);
  r.hex8(S, "E.RXB1D0", readReg(t, 0x76), DATASHEET,
         "p23 s4.1.3: the flag is the lockout");
  // READ RX with n=1, m=1 (0x96) starts at RXB1D0 and frees RXB1 only.
  t.csLow();
  t.transfer((uint8_t)(OP_READ_RX | 0x06));
  const uint8_t e0 = t.transfer(0);
  t.csHigh();
  r.hex8(S, "E.read96-first-byte", e0, DATASHEET, P68);
  r.hex8(S, "E.read96-rx-flags", rxFlags(t), DATASHEET, P68);

  // BUKT clear: no rollover; the second frame is lost with RX0OVR.
  writeReg(t, R_EFLG, 0x00);
  writeReg(t, R_CANINTF, 0x00);
  writeReg(t, R_RXB0CTRL, 0x60);
  t.injectFrame(ID, true, 0xF0, 8);
  t.injectFrame(ID, true, 0xF1, 8);
  r.hex8(S, "noBUKT.overflow", ovrFlags(t), DATASHEET, P26);
  r.hex8(S, "noBUKT.rx-flags", rxFlags(t), DATASHEET, P26);
  r.hex8(S, "noBUKT.RXB0D0", readReg(t, 0x66), DATASHEET, P26);
}

}  // namespace

void run(Target& t, Recorder& rec) {
  stepResetDefaults(t, rec);
  stepBitModify(t, rec);
  stepReadStatusMap(t, rec);
  stepTxFlagsOnRequest(t, rec);
  stepRtsTogether(t, rec);
  stepRtsSeparately(t, rec);
  stepTxPriority(t, rec);
  stepSameIdVaryingDlc(t, rec);
  stepNoAck(t, rec);
  stepAbort(t, rec);
  stepRxOverrun(t, rec);
  stepRxRollover(t, rec);
}

}  // namespace mcpconf
