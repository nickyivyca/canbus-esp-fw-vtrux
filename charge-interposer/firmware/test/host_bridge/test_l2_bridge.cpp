// Spec 9.1 L2: the REAL bridge loop, on the host, against both fakes.
//
// This compiles `src/main.cpp` itself -- `setup()` and `loop()`, the
// file-static ports, the actual dispatch and diagnostics -- and drives it
// on the virtual clock. Nothing is reimplemented, which is the point: the
// diagnostic cadence, the spec 2.2 counter and spec 8.2's `bridge_ok` are
// properties of that file, and a test that reproduced them would be
// checking its own copy.
//
// BUILT WITH -DINTP_NO_SERIAL=1, so it exercises the TRUCK image's code
// path -- the one that actually goes in the vehicle. Spec 9's scenarios
// read the diagnostic CAN frames rather than the serial text, so nothing
// here needs the prints, and `intp_serial` reading 0 is what identifies a
// truck image on the wire.
//
// ONE PROCESS PER CASE. `main.cpp` keeps its state in file statics with no
// way to reset them, and the answer is NOT to grow a reset hook in flight
// code for the harness's benefit: `run.sh` invokes this binary once per
// case name, so every case starts from genuinely fresh statics. The
// failed-start case could not exist at all otherwise -- it needs a boot
// where begin() fails, which a chained lifetime cannot give.
//
// THE COVERAGE ASSERTION. Every case that bridges asserts the receive
// paths were actually EXERCISED -- frames read out of the MCP2515's
// buffers, and frames handed out by the TWAI driver. The INT pin went
// unmodelled and the entire MCP2515 receive path never ran, with every
// suite green; counting the readings that exercise the term is the only
// thing that would have said so.

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>
#include <map>
#include <vector>

#include "SPI.h"
#include "esp_app_desc.h"
#include "host_clock.h"
#include "l3_replay.h"
#include "mcp2515_fake_chip.h"
#include "machine.h"
#include "twai_fake.h"

// main.cpp's entry points. Declared rather than #included: it has no
// header, and declaring them is what a hosted firmware test does.
void setup();
void loop();

using mcpfake::Mcp2515Fake;
using twaifake::fake;

static int failures = 0;

static void check(bool cond, const char* what) {
  std::printf(cond ? "  ok  %s\n" : "FAIL  %s\n", what);
  if (!cond) failures++;
}

namespace {

const uint32_t DIAG_STATUS = 0x7F4;
const uint32_t DIAG_OBSERVED = 0x7F5;
const uint32_t DIAG_COUNTERS = 0x7F6;
const uint32_t DIAG_BUILD = 0x7F7;

// 0x7F4 B4 bit assignments, from interposer_diag_schema.md section 2.
// Spelled out because an earlier draft guessed bit 1 for bridge_ok --
// which is chg_seen_charging -- and the "bridge_ok cleared" check then
// passed on a bit that is never set here.
const uint8_t FLAG_BRIDGE_OK = 0x40;   // B4 bit 6
const uint8_t FLAG_SERIAL = 0x80;      // B4 bit 7

Mcp2515Fake* g_chip = nullptr;

void runBridge(uint32_t ms, uint32_t step_us = 250) {
  const uint64_t until = g_chip->micros() + (uint64_t)ms * 1000;
  while (g_chip->micros() < until) {
    loop();
    g_chip->advance(step_us);
  }
  loop();
}

const std::vector<twaifake::Frame>& vehSent() { return fake().sent(); }

const twaifake::Frame* lastFrom(uint32_t id) {
  const std::vector<twaifake::Frame>& s = vehSent();
  for (size_t i = s.size(); i > 0; i--)
    if (s[i - 1].id == id) return &s[i - 1];
  return nullptr;
}

uint32_t countFrom(uint32_t id, size_t from) {
  const std::vector<twaifake::Frame>& s = vehSent();
  uint32_t n = 0;
  for (size_t i = from; i < s.size(); i++)
    if (s[i].id == id) n++;
  return n;
}

// A charger status frame, so the core does not trip on staleness.
void chargerStatus() {
  mcpfake::CanFrame f;
  std::memset(&f, 0, sizeof(f));
  f.id = 0x18FF50E5;
  f.ext = true;
  f.len = 8;
  f.data[0] = 12;          // state 12, no faults
  g_chip->deliverFrame(f);
}

// A BMS frame from the vehicle side, so traffic flows both ways and the
// vehicle receive path is exercised too.
void vehicleFrame(uint32_t id) {
  const uint8_t d[8] = {0, 0, 0, 0, 0, 0, 0, 0};
  fake().deliver(id, true, d, 8);
}

// Wire both models in and boot. Returns after setup().
void boot(bool charger_starts = true, bool vehicle_starts = true) {
  fake().hardReset();
  fake().setInstallFails(!vehicle_starts);
  hostclock::attach(g_chip, 10);          // MCP_CS
  hostclock::attachInt(3);                // MCP_INT
  hostspi::attachGlobal(g_chip);
  g_chip->setRefuseModeChange(!charger_starts);
  setup();
}

// Every bridging case ends with this. A run that bridged nothing is a run
// that proved nothing, however many assertions it passed.
void assertPathsExercised() {
  std::printf("  --  coverage: chip rxReads=%u, twai framesHandedOut=%u\n",
              g_chip->rxReads(), fake().framesHandedOut());
  check(g_chip->rxReads() > 0,
        "coverage: the MCP2515 receive path actually ran (this was ZERO "
        "while every suite was green -- the INT pin was unmodelled)");
  check(fake().framesHandedOut() > 0,
        "coverage: the TWAI receive path actually ran");
}

void feed(int cycles, uint32_t ms = 100) {
  for (int i = 0; i < cycles; i++) {
    chargerStatus();
    vehicleFrame(0x18FF4AE5);
    runBridge(ms);
  }
}

// --- the cases ------------------------------------------------------

void caseBoot() {
  boot();
  check(fake().state() == TWAI_STATE_RUNNING, "the vehicle port came up");
  check(fake().invalidStateCalls() == 0,
        "setup() made no wrong-state TWAI call");
  check(!g_chip->busOff(), "the charger port is on the bus");
  feed(12);
  check(countFrom(DIAG_STATUS, 0) > 0, "the board is reporting");
  assertPathsExercised();
}

void caseCadence() {
  boot();
  const size_t mark = vehSent().size();
  feed(30);
  const uint32_t n_status = countFrom(DIAG_STATUS, mark);
  const uint32_t n_obs = countFrom(DIAG_OBSERVED, mark);
  const uint32_t n_cnt = countFrom(DIAG_COUNTERS, mark);
  const uint32_t n_bld = countFrom(DIAG_BUILD, mark);
  std::printf("  --  over ~3 s: 0x7F4=%u 0x7F5=%u 0x7F6=%u 0x7F7=%u\n",
              n_status, n_obs, n_cnt, n_bld);
  check(n_obs >= 2 && n_obs <= 4,
        "0x7F5 goes out about once a second (DIAG_INTERVAL_MS)");
  check(n_obs == n_cnt && n_cnt == n_bld,
        "0x7F5, 0x7F6 and 0x7F7 always go out together");
  check(n_status >= n_obs,
        "0x7F4 goes at least as often -- it also fires on a state change");
  assertPathsExercised();
}

void caseFields() {
  boot();
  feed(15);
  const twaifake::Frame* st = lastFrom(DIAG_STATUS);
  const twaifake::Frame* bd = lastFrom(DIAG_BUILD);
  check(st != nullptr, "a status frame was sent");
  check(bd != nullptr, "a build frame was sent");
  if (!st || !bd) return;

  std::printf("  --  0x7F4 = %02X %02X %02X %02X %02X %02X %02X %02X\n",
              st->data[0], st->data[1], st->data[2], st->data[3],
              st->data[4], st->data[5], st->data[6], st->data[7]);
  check(st->data[0] == 3, "schema version 3 in 0x7F4 byte 0");
  check(st->data[1] == 4, "firmware version 4 in 0x7F4 byte 1");
  check(st->len == 8, "the status frame is 8 bytes");
  check(!st->ext, "diagnostics are STANDARD ids, not extended");

  // LITTLE-endian, like every other multi-byte field in the schema.
  // Decoded big-endian at first, which read 0xE0A55ED1 for 0xD15EA5E0 --
  // a byte reversal reads as a wrong value rather than a wrong decode.
  const uint32_t build = (uint32_t)bd->data[0] |
                         ((uint32_t)bd->data[1] << 8) |
                         ((uint32_t)bd->data[2] << 16) |
                         ((uint32_t)bd->data[3] << 24);
  std::printf("  --  0x7F7 build id = 0x%08X\n", build);
  check(build == HOST_L2_BUILD_ID,
        "0x7F7 carries the build id from the app description");

  // spec 8.3: this is the truck image's code path, and that is how a
  // truck image is identified on the wire.
  check((st->data[4] & FLAG_SERIAL) == 0,
        "intp_serial reads 0 -- a truck-image code path");
  assertPathsExercised();
}

void caseBridgeOk() {
  boot();
  // A THREE-STATE sequence -- set, clear, set again -- on purpose. The
  // first version checked only "a clear one exists" and "a set one
  // exists", and because it read the wrong bit the clear half passed on
  // every frame ever sent. No constant bit satisfies all three.
  auto seen = [](size_t from, bool want) {
    const std::vector<twaifake::Frame>& s = vehSent();
    for (size_t i = from; i < s.size(); i++)
      if (s[i].id == DIAG_STATUS &&
          (((s[i].data[4] & FLAG_BRIDGE_OK) != 0) == want))
        return true;
    return false;
  };

  const size_t a = vehSent().size();
  feed(12);
  const bool set_before = seen(a, true);

  const size_t b = vehSent().size();
  g_chip->injectRxOverrun(false);
  feed(12);
  const bool cleared = seen(b, false);

  const size_t c = vehSent().size();
  feed(25);
  const bool set_again = seen(c, true);

  std::printf("  --  bridge_ok: before=%d cleared=%d again=%d\n",
              set_before ? 1 : 0, cleared ? 1 : 0, set_again ? 1 : 0);
  check(set_before, "bridge_ok is SET on a healthy bridge");
  check(cleared, "an RX overrun clears it (spec 8.2)");
  check(set_again,
        "...and it RECOVERS on the next clean interval -- a flag that "
        "latched would say nothing for the rest of the drive");
  assertPathsExercised();
}

// Spec 2: "Every frame is forwarded to the other port unmodified, in
// order"; the only accepted reordering is vehicle-to-charger across
// identifiers, so charger-to-vehicle has none. Reviewer request (tracker
// "RX rollover order", 2026-10-07): A and B arrive back to back, B while A
// is still unread, so B rolls into RXB1 (p23 s4.2.1); C arrives after the
// board has taken A but before it has taken B, so C lands in the freed
// RXB0 and is NEWER than the B beside it (C12 settles that chip state).
// Spec 2 requires A, B, C on the vehicle side.
//
// C is placed with deliverWhenFreed(): it arrives at the instant RXB0's
// RX0IF is cleared, by whatever means the driver uses -- the window the
// driver's own SPI timing opens, which no time chosen in advance can hit.
// On a wire this needs A, B and C within about three frame times (~330 us
// at 500 kbit for short frames) while one bridge pass runs long, which
// the replay case's pass-time tail shows passes do.
//
// Two variants: three different identifiers, and one identifier three
// times (same-identifier order carries sequence meaning, spec 2).
void caseRxRolloverOrder() {
  boot();
  feed(12);

  struct Variant {
    const char* name;
    uint32_t id[3];
  };
  const Variant variants[] = {
      {"three identifiers", {0x18FF60E5, 0x18FF61E5, 0x18FF62E5}},
      {"one identifier", {0x18FF60E5, 0x18FF60E5, 0x18FF60E5}},
  };
  const uint8_t tag[3] = {0xA0, 0xB0, 0xC0};
  for (int rep = 0; rep < 3; rep++) {
    for (const Variant& v : variants) {
      char msg[200];
      // Precondition: both buffers empty, so A lands in RXB0 and B in RXB1.
      runBridge(5);
      const bool empty = !g_chip->rxFull(0) && !g_chip->rxFull(1);
      const size_t mark = vehSent().size();
      mcpfake::CanFrame f[3];
      for (int k = 0; k < 3; k++) {
        std::memset(&f[k], 0, sizeof(f[k]));
        f[k].id = v.id[k];
        f[k].ext = true;
        f[k].len = 8;
        f[k].data[0] = tag[k];
        f[k].data[1] = (uint8_t)rep;
      }
      g_chip->deliverFrame(f[0]);               // A -> RXB0
      g_chip->deliverFrame(f[1]);               // B -> RXB1 (rollover)
      const bool placed = g_chip->rxFull(0) && g_chip->rxFull(1);
      g_chip->deliverWhenFreed(0, f[2]);        // C when A is taken
      runBridge(20);
      const bool fired = g_chip->firedWhenFreed(0);

      // The vehicle side, in send order, restricted to these three frames.
      std::string order;
      std::vector<uint8_t> got;
      const std::vector<twaifake::Frame>& s = vehSent();
      for (size_t i = mark; i < s.size(); i++) {
        if (!s[i].ext || s[i].len < 2 || s[i].data[1] != (uint8_t)rep)
          continue;
        bool ours = false;
        for (int k = 0; k < 3; k++)
          if (s[i].id == v.id[k] && s[i].data[0] == tag[k]) ours = true;
        if (!ours) continue;
        got.push_back(s[i].data[0]);
        char b[8];
        std::snprintf(b, sizeof(b), "%s%c", order.empty() ? "" : ",",
                      "ABC"[(s[i].data[0] >> 4) - 0xA]);
        order += b;
      }
      std::printf("  --  rep %d, %s: vehicle side %s\n", rep, v.name,
                  order.empty() ? "(none)" : order.c_str());
      std::snprintf(msg, sizeof(msg),
                    "rep %d, %s: setup held (buffers empty, then A in RXB0 "
                    "and B in RXB1, and C arrived when RXB0 was freed)",
                    rep, v.name);
      check(empty && placed && fired, msg);
      std::snprintf(msg, sizeof(msg),
                    "rep %d, %s: all three forwarded once (got %u)", rep,
                    v.name, (unsigned)got.size());
      check(got.size() == 3, msg);
      std::snprintf(msg, sizeof(msg),
                    "rep %d, %s: vehicle side A,B,C per spec 2 (observed "
                    "%s)", rep, v.name, order.empty() ? "none" : order.c_str());
      check(order == "A,B,C", msg);
    }
  }

  // CONTROL: the same three frames, but C arrives only when RXB1 is freed,
  // i.e. after B has been taken. The case above can then be shown to
  // report A,B,C when the window it targets is closed -- so its failure,
  // if it fails, comes from that window and not from the harness's
  // collection or naming of frames.
  {
    runBridge(5);
    const size_t mark = vehSent().size();
    const uint32_t id = 0x18FF60E5;
    mcpfake::CanFrame f[3];
    for (int k = 0; k < 3; k++) {
      std::memset(&f[k], 0, sizeof(f[k]));
      f[k].id = id;
      f[k].ext = true;
      f[k].len = 8;
      f[k].data[0] = tag[k];
      f[k].data[1] = 0x7C;
    }
    g_chip->deliverFrame(f[0]);
    g_chip->deliverFrame(f[1]);
    g_chip->deliverWhenFreed(1, f[2]);
    runBridge(20);
    std::string order;
    const std::vector<twaifake::Frame>& s = vehSent();
    for (size_t i = mark; i < s.size(); i++) {
      if (s[i].id != id || s[i].len < 2 || s[i].data[1] != 0x7C) continue;
      if (!order.empty()) order += ",";
      order += "ABC"[(s[i].data[0] >> 4) - 0xA];
    }
    std::printf("  --  control (C after B is taken): vehicle side %s\n",
                order.empty() ? "(none)" : order.c_str());
    check(g_chip->firedWhenFreed(1) && order == "A,B,C",
          "control: with C arriving after B is taken, the harness reports "
          "A,B,C -- it can pass");
  }
  assertPathsExercised();
}

// The refill interleaving (reviewer, 2026-10-08, asked for ahead of the
// rollover fix): A and B arrive with A unread; A is taken and C refills
// RXB0; B is taken and D rolls into RXB1 while C still sits in RXB0. Now
// the NEWER frame is in RXB1 -- the reverse of rx-rollover-order -- so
// spec 2 requires A, B, C, D, and neither "RXB0 first" nor "RXB1 first"
// as a fixed rule delivers both cases in order.
//
// The setup assertion is strict: C must have been delivered first, with B
// still in RXB1, and D second, with C still in RXB0. If a build takes C
// before B is taken, D never lands beside C and the interleaving did not
// happen; that is reported as a setup failure, never passed.
void caseRxRolloverRefill() {
  boot();
  feed(12);
  const uint8_t tag[4] = {0xA0, 0xB0, 0xC0, 0xD0};
  const struct {
    const char* name;
    uint32_t id[4];
  } variants[] = {
      {"four identifiers", {0x18FF60E5, 0x18FF61E5, 0x18FF62E5, 0x18FF63E5}},
      {"one identifier", {0x18FF60E5, 0x18FF60E5, 0x18FF60E5, 0x18FF60E5}},
  };
  for (int rep = 0; rep < 3; rep++) {
    for (const auto& v : variants) {
      char msg[240];
      runBridge(5);
      const bool empty = !g_chip->rxFull(0) && !g_chip->rxFull(1);
      const size_t mark = vehSent().size();
      mcpfake::CanFrame f[4];
      for (int k = 0; k < 4; k++) {
        std::memset(&f[k], 0, sizeof(f[k]));
        f[k].id = v.id[k];
        f[k].ext = true;
        f[k].len = 8;
        f[k].data[0] = tag[k];
        f[k].data[1] = (uint8_t)(0x40 + rep);
      }
      g_chip->deliverFrame(f[0]);
      g_chip->deliverFrame(f[1]);
      g_chip->deliverWhenFreed(0, f[2]);
      g_chip->deliverWhenFreed(1, f[3]);
      runBridge(20);
      const bool setup =
          empty && g_chip->firedWhenFreed(0) && g_chip->firedWhenFreed(1) &&
          g_chip->firedSeq(0) < g_chip->firedSeq(1) &&
          g_chip->otherFullWhenFired(0) && g_chip->otherFullWhenFired(1);

      std::string order;
      const std::vector<twaifake::Frame>& s = vehSent();
      for (size_t i = mark; i < s.size(); i++) {
        if (!s[i].ext || s[i].len < 2 || s[i].data[1] != (uint8_t)(0x40 + rep))
          continue;
        for (int k = 0; k < 4; k++) {
          if (s[i].id != v.id[k] || s[i].data[0] != tag[k]) continue;
          if (!order.empty()) order += ",";
          order += "ABCD"[k];
        }
      }
      std::printf("  --  rep %d, %s: setup fired C=%d(seq %u, B resident %d) "
                  "D=%d(seq %u, C resident %d); vehicle side %s\n",
                  rep, v.name, g_chip->firedWhenFreed(0) ? 1 : 0,
                  (unsigned)g_chip->firedSeq(0),
                  g_chip->otherFullWhenFired(0) ? 1 : 0,
                  g_chip->firedWhenFreed(1) ? 1 : 0,
                  (unsigned)g_chip->firedSeq(1),
                  g_chip->otherFullWhenFired(1) ? 1 : 0,
                  order.empty() ? "(none)" : order.c_str());
      std::snprintf(msg, sizeof(msg),
                    "rep %d, %s: the interleaving happened (C into RXB0 "
                    "beside B, then D into RXB1 beside C)", rep, v.name);
      check(setup, msg);
      std::snprintf(msg, sizeof(msg),
                    "rep %d, %s: vehicle side A,B,C,D per spec 2 (observed "
                    "%s)", rep, v.name, order.empty() ? "none" : order.c_str());
      check(order == "A,B,C,D", msg);
    }
  }
  assertPathsExercised();
}

// Spec 2.1 / 8.2, B-7d. Needs a boot where the charger controller does
// not start, which is why one process per case is not a convenience.
void caseFailedStart() {
  boot(/*charger_starts=*/false, /*vehicle_starts=*/true);

  check(fake().state() == TWAI_STATE_RUNNING,
        "the vehicle controller still came up");
  feed(25);

  const uint32_t n_status = countFrom(DIAG_STATUS, 0);
  const uint32_t n_other = countFrom(DIAG_OBSERVED, 0) +
                           countFrom(DIAG_COUNTERS, 0) +
                           countFrom(DIAG_BUILD, 0);
  std::printf("  --  failed start: 0x7F4=%u, others=%u\n",
              n_status, n_other);

  check(n_status > 0,
        "a board whose charger port did not start still sends 0x7F4 -- "
        "otherwise it is indistinguishable from a board not fitted");
  check(n_other == 0,
        "...and NOT the other three, which carry observations and "
        "counters that mean nothing on a board bridging nothing");

  const twaifake::Frame* st = lastFrom(DIAG_STATUS);
  check(st != nullptr, "a status frame exists to read");
  if (st) {
    std::printf("  --  0x7F4 state=%u flags=0x%02X\n", st->data[2],
                st->data[4]);
    check((st->data[4] & FLAG_BRIDGE_OK) == 0,
          "bridge_ok is CLEAR on a board that is not bridging");
    check(st->data[2] == 0,
          "...and the state reads PASSTHROUGH, because the core has seen "
          "no frames");
  }
  // Deliberately NO assertPathsExercised() here: nothing is bridged in
  // this case and that is the point of it. Saying so explicitly so the
  // omission reads as a decision rather than an oversight.
}

// Spec 2.2: failed transmissions TOWARD THE CHARGER, counted only while
// the VCU's flow bit is 1, reported in 0x7F6 byte 7.
//
// The gate is the whole point of the field. It reads 0 while driving,
// however many frames bounce off a charger that is not plugged in, so any
// non-zero value means something went wrong DURING A SESSION. A test that
// only checked "failures are counted" would pass on a counter with no gate
// at all, so both halves are checked here and the ungated half comes
// first.
void caseTxFail() {
  boot();

  // The VCU's page-00 master command: d[0] page, d[1] flow, d[2] mode.
  auto vcuCommand = [](uint8_t flow, uint8_t mode) {
    const uint8_t d[8] = {0x00, flow, mode, 0, 0, 0, 0, 0};
    fake().deliver(0x18EFC000, true, d, 8);
  };
  auto txFailByte = []() -> int {
    const twaifake::Frame* f = lastFrom(DIAG_COUNTERS);
    return f ? (int)f->data[7] : -1;
  };

  // --- (a) flow 0: the charger is NOT expected -----------------------
  // Nothing acknowledges on the charger segment, so every forwarded
  // frame ages out at 100 ms and the PORT's own counters climb. The
  // spec 2.2 field must stay at zero regardless.
  g_chip->setAcknowledged(false);
  for (int i = 0; i < 20; i++) {
    vcuCommand(0, interposer::MODE_CHARGER);
    chargerStatus();
    runBridge(100);
  }
  const int ungated = txFailByte();
  std::printf("  --  flow=0, charger silent: 0x7F6 B7 = %d\n", ungated);
  check(ungated == 0,
        "with the VCU's flow bit 0, failed charger sends are NOT counted "
        "-- the field means 'failed while expected' (spec 2.2)");

  // --- (b) flow 1: now it IS expected --------------------------------
  for (int i = 0; i < 25; i++) {
    vcuCommand(1, interposer::MODE_CHARGER);
    chargerStatus();
    runBridge(100);
  }
  const int gated = txFailByte();
  std::printf("  --  flow=1, charger silent: 0x7F6 B7 = %d\n", gated);
  check(gated > 0,
        "...and with flow 1 they ARE counted, so the gate is a gate and "
        "not simply a counter that never increments");

  // --- (c) and it stops again when the charger comes back ------------
  g_chip->setAcknowledged(true);
  for (int i = 0; i < 12; i++) {
    vcuCommand(1, interposer::MODE_CHARGER);
    chargerStatus();
    runBridge(100);
  }
  const int settled = txFailByte();
  for (int i = 0; i < 12; i++) {
    vcuCommand(1, interposer::MODE_CHARGER);
    chargerStatus();
    runBridge(100);
  }
  std::printf("  --  charger acknowledging again: %d -> %d\n",
              settled, txFailByte());
  check(txFailByte() == settled,
        "once the charger acknowledges, the count stops rising -- it is "
        "cumulative, so it must hold rather than fall");

  assertPathsExercised();
}

// Spec 9.1 L3: real captured traffic as stimulus.
//
// 22,626 frames of the truck's own powertrain bus at 2,263 fps, replayed
// through the real bridge loop. The capture window and the bus were both
// chosen by CONTENT -- see make_l3_stimulus.py -- and the file is pinned.
//
// WHAT THIS DELIBERATELY DOES NOT ASSERT: that nothing was lost. Whether
// the charger port keeps up with 2,263 fps is a BUS LOAD question, and
// spec 9.1 says plainly that "an acceptance criterion that depends on bus
// load needs a bench run". The fakes have no shared medium, so a
// zero-loss result here would be a property of the model, not of the
// board, and asserting it would be inventing evidence.
//
// What it asserts instead holds at ANY load:
//   - the accounting CLOSES: every offered frame is either handed to the
//     bridge or counted as lost. A frame vanishing with nothing counting
//     it is a defect whatever the load;
//   - what did cross came through byte-identical and IN ORDER;
//   - the core did not trip on real traffic;
//   - the diagnostics kept their cadence underneath it.
void caseReplay() {
  // The quantised stimulus by default. L3_STIMULUS overrides it, which
  // is how the de-quantised pin (`l3_stimulus_real.txt`) is run against
  // the same case without a second copy of the harness -- the direct
  // test of whether the charger RX overflows are the logger's 1 ms
  // timestamp quantisation or something else. Printed either way, so a
  // run under the override cannot be mistaken for the default one.
  // No repo-relative default since the move to git: l3::load() finds
  // the stimulus through $VTRUX_DATA (charge-interposer/paths.py).
  const char* kPath = NULL;
  if (const char* over = std::getenv("L3_STIMULUS")) {
    kPath = over;
    std::printf("  --  L3_STIMULUS override in effect: %s\n", over);
  }
  l3::Stimulus st = l3::load(kPath);
  // SERIALISED: the capture's 1 ms timestamp quantisation lets
  // several frames share an instant, which no wire can deliver.
  // See l3::serialise() for what that did to the model.
  st = l3::serialise(st);
  if (!st.loaded) {
    std::printf("  --  %s\n", st.why.c_str());
    check(false, "the pinned L3 stimulus loads and its pin matches");
    return;
  }
  std::printf("  --  %s\n", st.source_line.c_str());
  std::printf("  --  %u frames, pin %s\n",
              (unsigned)st.rows.size(), st.pin_actual.c_str());
  check(true, "the pinned L3 stimulus loads and its pin matches");

  boot();

  uint32_t offered_v = 0, offered_c = 0;
  std::vector<l3::Row> offered_veh;      // for the transparency walk
  // THE BOARD'S loop() FREE-RUNS. It does not wait for traffic, and it
  // does not get exactly one pass per burst.
  //
  // The first version of this replay advanced burst-to-burst and called
  // loop() once in between. Frames sharing a timestamp therefore arrived
  // back-to-back with no service at all, the rings backed up, and only
  // 0.9% of the traffic reached the chip. That was a property of the
  // harness: `test_tx_throughput` drives the same port directly at the
  // same rate and delivers 100%.
  //
  // So: a free-running loop with timed injection. Frames are delivered
  // when their capture timestamp arrives, and loop() runs continuously
  // underneath -- which is what the firmware actually does.
  size_t i = 0;
  uint64_t loops = 0;
  // ASK (c): localise the charger-side RX loss (310 offered, 290 read,
  // rx_ovf=20). The chip has exactly two receive buffers, so three
  // charger frames delivered between two service() calls must lose one
  // -- and this inner loop delivers EVERY frame whose timestamp has
  // arrived before calling loop() once. If the capture's charger
  // frames share timestamps, the harness hands the chip a burst that
  // no real wire could produce: frames are serialised on a 500 kbit
  // bus at >=110 us apart, while the bridge services every ~62 us, so
  // on silicon a third frame cannot arrive with both buffers still
  // full. Counted per pass so the claim rests on the distribution
  // rather than on the mechanism sounding right.
  uint32_t chg_per_pass_max = 0;
  uint32_t chg_pass_hist[8] = {0, 0, 0, 0, 0, 0, 0, 0};
  // THE MEAN PASS PERIOD IS THE WRONG STATISTIC for this question and
  // my first answer used it. Two RX buffers overflow only when ONE pass
  // runs longer than about two minimum frame times (~190 us at 500
  // kbit); an average of 60 us says nothing about whether any single
  // pass did. A pass that happens to do a full TX refill plus the 10 ms
  // tick's diagnostics is exactly where that would happen. Collected as
  // a distribution so the tail is visible rather than summarised away.
  std::vector<uint32_t> pass_us;
  pass_us.reserve(200000);
  uint64_t prev_pass_us = g_chip->micros();
  const uint64_t t_start = g_chip->micros();
  const uint64_t span = st.rows.back().t_us;
  while (g_chip->micros() - t_start <= span || i < st.rows.size()) {
    const uint64_t elapsed = g_chip->micros() - t_start;
    uint32_t chg_this_pass = 0;
    while (i < st.rows.size() && st.rows[i].t_us <= elapsed) {
      const l3::Row& r = st.rows[i];
      if (r.charger) {
        chg_this_pass++;
        mcpfake::CanFrame f;
        std::memset(&f, 0, sizeof(f));
        f.id = r.id; f.ext = r.ext; f.len = r.len;
        std::memcpy(f.data, r.data, 8);
        g_chip->deliverFrame(f);
        offered_c++;
      } else {
        fake().deliver(r.id, r.ext, r.data, r.len);
        offered_v++;
        offered_veh.push_back(r);
      }
      i++;
    }
    if (chg_this_pass > chg_per_pass_max) chg_per_pass_max = chg_this_pass;
    chg_pass_hist[chg_this_pass < 8 ? chg_this_pass : 7]++;
    loop();
    loops++;
    {
      const uint64_t now = g_chip->micros();
      pass_us.push_back((uint32_t)(now - prev_pass_us));
      prev_pass_us = now;
    }
    g_chip->advance(50);
    if (g_chip->micros() - t_start > span + 2000000ull) break;  // safety
  }

  // Let the tail drain.
  runBridge(300);

  // --- the accounting ------------------------------------------------
  const uint32_t handed = fake().framesHandedOut();
  const uint32_t missed = fake().rxMissed();
  std::printf("  --  vehicle side: offered %u, handed to the bridge %u, "
              "lost to a full queue %u\n", offered_v, handed, missed);
  // EVERY ROW OFFERED, none skipped. One line, and it guards the
  // next edit that adds a skip to the injection loop: a replay that
  // quietly drops two thirds of its stimulus reports a beautifully
  // clean result, which is how a serialising attempt went wrong on
  // 2026-10-05 before the offered count gave it away.
  {
    uint32_t rows_v = 0, rows_c = 0;
    for (size_t q = 0; q < st.rows.size(); q++)
      (st.rows[q].charger ? rows_c : rows_v)++;
    check(offered_v == rows_v && offered_c == rows_c,
          "the replay offered EVERY stimulus row on both segments -- a "
          "dropping injector can never pass as a quiet bus");
  }

  check(handed + missed == offered_v,
        "vehicle RX accounting closes: every offered frame was either "
        "handed to the bridge or counted lost (spec 9.1 forbids a "
        "zero-loss claim from a replay, but nothing may vanish)");

  // Where did the vehicle traffic actually go? Measured, not assumed.
  {
    const std::vector<mcpfake::WireEvent>& ww = g_chip->wire();
    uint32_t deliv = 0, aborted = 0;
    for (size_t k = 0; k < ww.size(); k++)
      if (ww[k].delivered) deliv++; else aborted++;
    const twaifake::Frame* c6 = lastFrom(DIAG_COUNTERS);
    std::printf("  --  charger wire: %u events, %u delivered, %u not; "
                "0x7F6 rx_ovf=%d txfail=%d\n",
                (unsigned)ww.size(), deliv, aborted,
                c6 ? (int)c6->data[6] : -1, c6 ? (int)c6->data[7] : -1);
    std::printf("  --  txLoads=%u  wall: %.3f s elapsed\n",
                g_chip->txLoads(), g_chip->micros() / 1e6);
  }

  const uint32_t reads = g_chip->rxReads();
  std::printf("  --  charger side: offered %u, read out of the buffers %u\n",
              offered_c, reads);
  check(reads > 0, "the charger receive path ran on real traffic");
  check(reads <= offered_c,
        "...and never read out more than was offered");

  // ASK (c): WHY the charger side loses frames. The chip has two
  // receive buffers, so a pass that hands it three loses one no matter
  // how fast the firmware is -- the loss is decided before service()
  // gets a chance. This is the distribution of how many charger frames
  // this harness delivers between consecutive loop() calls.
  std::printf("  --  charger frames delivered per pass: max %u  "
              "[0]=%u [1]=%u [2]=%u [3]=%u [4]=%u [5]=%u [6]=%u [7+]=%u\n",
              chg_per_pass_max, chg_pass_hist[0], chg_pass_hist[1],
              chg_pass_hist[2], chg_pass_hist[3], chg_pass_hist[4],
              chg_pass_hist[5], chg_pass_hist[6], chg_pass_hist[7]);
  {
    // Frames that could not fit two buffers, summed over the passes
    // that delivered more than two. If this accounts for the overflow
    // count, the loss is the harness's instantaneous delivery and not
    // the firmware: on a 500 kbit wire frames are serialised at least
    // ~110 us apart while the bridge services every ~62 us, so a third
    // frame cannot arrive on silicon with both buffers still full.
    uint32_t excess = 0;
    for (uint32_t n = 3; n < 8; n++)
      excess += chg_pass_hist[n] * (n - 2);
    std::printf("  --  frames beyond the chip's two RX buffers in a "
                "single pass: %u (vs %u overflow(s) counted)\n",
                excess, g_chip->rxOverflowCount());

    // ANSWERED, 2026-10-04, and the two numbers match exactly: 20
    // excess against 20 overflows, with 310 offered and 290 read. The
    // distribution is [1]=104 [2]=73 [3]=20 and the maximum is 3, so
    // every lost frame is the third of three handed over with no
    // service in between.
    //
    // NOT REACHABLE ON SILICON, which is the part that matters. Frames
    // on a 500 kbit wire are serialised: the shortest possible frame is
    // ~47 bits with interframe space, about 94 us, and this run's
    // bridge loop ran 175,484 times in 10.5 s -- one service every
    // ~60 us. So a real charger segment cannot present a second frame
    // before the first has been read out, let alone a third. The
    // harness delivers everything whose capture timestamp has arrived
    // in one instant, and the capture's timestamps are quantised to
    // 1 ms by the logger, so three frames inside one millisecond
    // become three frames inside one microsecond.
    //
    // Same shape as the gs_usb capture ceiling found the same day:
    // offer a burst to something with a finite buffer and no chance to
    // drain, then read the shortfall as a defect in the thing being
    // measured. Left as a reported measurement rather than asserted --
    // fixing it means interleaving loop() into the delivery of
    // same-timestamp frames, which changes the replay's timing model
    // and should be a deliberate decision, not a side effect of
    // chasing this number to zero.
    // THE TAIL OF THE PASS PERIOD, which is what actually decides
    // whether two buffers are enough. Indicative only: the fake's SPI
    // timing is a model, so these are the model's numbers and not the
    // board's. Step 4's rx_ovf from 0x7F6 on silicon is the real
    // answer, and it gets recorded even when it is zero.
    if (!pass_us.empty()) {
      std::vector<uint32_t> s(pass_us);
      std::sort(s.begin(), s.end());
      const size_t n = s.size();
      std::printf("  --  bridge pass period over %u passes (MODEL, not the "
                  "board): mean %.1f us, p50 %u, p99 %u, p99.9 %u, max %u\n",
                  (unsigned)n,
                  (double)(g_chip->micros() - t_start) / (double)n,
                  s[n / 2], s[(size_t)(n * 0.99)], s[(size_t)(n * 0.999)],
                  s[n - 1]);
      // Two minimum frames is the threshold at which a third frame can
      // find both buffers full: ~47 bits each with interframe space,
      // about 94 us at 500 kbit.
      const uint32_t kTwoFrames = 190;
      uint32_t over = 0;
      for (size_t k = 0; k < n; k++)
        if (s[k] > kTwoFrames) over++;
      std::printf("  --  passes longer than two minimum frame times "
                  "(%u us), where two RX buffers could be outrun: %u\n",
                  kTwoFrames, over);
    }

    check(excess == g_chip->rxOverflowCount(),
          "every charger-side RX overflow is accounted for by frames "
          "the harness delivered beyond the chip's two buffers in one "
          "pass -- none is unexplained");
  }

  // --- transparency of what DID cross --------------------------------
  //
  // Every frame on the charger wire must match SOME offered vehicle
  // frame byte for byte. CMD_ID is excluded -- it is the one identifier
  // the core may rewrite (machine.h).
  //
  // CONTENT, and -- since 2026-10-04 -- ORDER. The order check used to
  // be deferred here with the note "worth asserting on a run where the
  // bridge keeps up; see the UNEXPLAINED note below for why this run is
  // not that". The bridge does keep up now: the shortfall that made a
  // one-way walk unsound was a chip-fake defect, not a real one, and
  // with it fixed every offered frame crosses. So the check is written.
  size_t crossed = 0, unmatched = 0, cmd_excluded = 0;
  const std::vector<mcpfake::WireEvent>& w = g_chip->wire();
  for (size_t k = 0; k < w.size(); k++) {
    if (!w[k].delivered) continue;
    const mcpfake::CanFrame& f = w[k].frame;
    // CMD_ID is excluded -- it is the one identifier the core may
    // rewrite (machine.h). COUNTED, not silently skipped: without this
    // the crossed total does not reconcile with the delivered total and
    // the next reader has to re-derive the difference.
    if (f.id == interposer::CMD_ID) { cmd_excluded++; continue; }
    crossed++;
    bool found = false;
    for (size_t j = 0; j < offered_veh.size() && !found; j++) {
      const l3::Row& r = offered_veh[j];
      found = (r.id == f.id && r.len == f.len &&
               std::memcmp(r.data, f.data, r.len) == 0);
    }
    if (!found) unmatched++;
  }
  std::printf("  --  transparency: %u frames crossed, %u of them matched "
              "no offered frame; %u CMD_ID frame(s) excluded (%u + %u = "
              "%u delivered)\n",
              (unsigned)crossed, (unsigned)unmatched, (unsigned)cmd_excluded,
              (unsigned)crossed, (unsigned)cmd_excluded,
              (unsigned)(crossed + cmd_excluded));
  check(crossed > 0, "real traffic did cross the bridge");
  check(unmatched == 0,
        "every frame that crossed is byte-identical to one that was "
        "offered -- nothing rewritten, nothing invented");

  // --- ORDER, PER IDENTIFIER ------------------------------------------
  //
  // Spec 2's same-identifier item is about what the charger sees for a
  // GIVEN id: successive frames of one id must reach the charger in the
  // order the vehicle sent them. Across different ids a global sequence
  // check would fail on correct behaviour, which is why this walks each
  // id separately.
  //
  // WHY reordering across ids happens, stated correctly. An earlier
  // version of this comment said "the three hardware buffers arbitrate
  // by id, so a low-id frame loaded later can and should win the wire
  // first". That is wrong, and wrong in a way that would mislead
  // anyone reasoning about ordering. DS20001801J p15 s3.2, confirmed in
  // the text and on the rendered page: transmit priority inside the
  // chip "is independent from, and not necessarily related to, any
  // prioritization implicit in the message arbitration scheme built
  // into the CAN protocol". Selection among the chip's OWN buffers is
  // TXP first, then highest buffer number on a tie. The identifier
  // never enters into it.
  //
  // Ids arbitrate against OTHER NODES on the wire, once a buffer has
  // already been chosen and is driving SOF. Two different mechanisms
  // at two different boundaries, and conflating them makes the chip
  // look like it is doing something defensible with id order when it
  // is really doing something arbitrary with buffer numbers.
  //
  // So the real cause of cross-id reordering here is the same as the
  // same-id case the finding below records: equal TXP, tie broken on
  // buffer number, load order ignored.
  //
  // The comparison is on PAYLOAD SEQUENCE per id: for each id, the
  // vehicle's frames in offer order against the charger's in wire
  // order. Equal-length and byte-identical, position by position.
  {
    std::map<uint32_t, std::vector<const uint8_t*> > offered_by_id;
    std::map<uint32_t, std::vector<uint8_t> > offered_len;
    for (size_t j = 0; j < offered_veh.size(); j++) {
      const l3::Row& r = offered_veh[j];
      if (r.id == interposer::CMD_ID) continue;
      offered_by_id[r.id].push_back(r.data);
      offered_len[r.id].push_back(r.len);
    }
    std::map<uint32_t, size_t> seen_by_id;
    size_t checked = 0, out_of_order = 0, ids = 0;
    for (size_t k = 0; k < w.size(); k++) {
      if (!w[k].delivered) continue;
      const mcpfake::CanFrame& f = w[k].frame;
      if (f.id == interposer::CMD_ID) continue;
      const size_t pos = seen_by_id[f.id]++;
      std::vector<const uint8_t*>& off = offered_by_id[f.id];
      if (pos >= off.size()) { out_of_order++; continue; }
      checked++;
      if (f.len != offered_len[f.id][pos] ||
          std::memcmp(off[pos], f.data, f.len) != 0) {
        out_of_order++;
        // Print the first few rather than only a count. A bare count
        // cannot distinguish a real reordering from a flaw in this
        // check, and the id plus position is what tells them apart.
        if (out_of_order <= 4) {
          std::printf("  --  OUT OF ORDER: id 0x%X at position %u of %u; "
                      "wire len %u [", (unsigned)f.id, (unsigned)pos,
                      (unsigned)off.size(), f.len);
          for (uint8_t b = 0; b < f.len; b++)
            std::printf("%02X", f.data[b]);
          std::printf("] vs offered len %u [", offered_len[f.id][pos]);
          for (uint8_t b = 0; b < offered_len[f.id][pos]; b++)
            std::printf("%02X", off[pos][b]);
          std::printf("]\n");
          // Where DID this payload come from? If it is the NEXT offered
          // frame of the same id, the two simply swapped -- which is
          // the three-buffer arbitration case. If it appears nowhere
          // nearby, this check is measuring the wrong thing.
          for (size_t q = (pos > 4 ? pos - 4 : 0);
               q < off.size() && q < pos + 5; q++) {
            if (q == pos) continue;
            if (offered_len[f.id][q] == f.len &&
                std::memcmp(off[q], f.data, f.len) == 0)
              std::printf("  --      (that payload is offered position "
                          "%u, i.e. a swap of %+d)\n",
                          (unsigned)q, (int)q - (int)pos);
          }
        }
      }
    }
    for (std::map<uint32_t, std::vector<const uint8_t*> >::const_iterator
             it = offered_by_id.begin(); it != offered_by_id.end(); ++it)
      if (seen_by_id.count(it->first)) ids++;
    std::printf("  --  per-id order: %u ids, %u frames compared in "
                "sequence, %u out of order\n",
                (unsigned)ids, (unsigned)checked, (unsigned)out_of_order);
    // NOT a bare "no mismatches": that is the check that passes when
    // nothing crossed. It must have compared a real population first.
    check(checked > 1000 && ids > 5,
          "the order check actually had something to compare -- over a "
          "thousand frames across more than five ids");
    // NOW ASSERTED. This was a reported FINDING while the fix was a
    // pending spec decision; spec 2 was changed on 2026-10-04 (user) to
    // forbid same-identifier reordering outright, the driver implements
    // the hold, and the note below said to convert this the moment that
    // landed. Leaving it as a print afterwards would be the quiet kind
    // of rot this file keeps finding.
    //
    // Before the hold: 2 of 22,117 out of order, 16 moments with one
    // identifier in two buffers at once. After: 0 and 0, with the
    // charger wire still carrying all 22,316.
    check(out_of_order == 0,
          "spec 2: for every identifier, the charger sees that id's "
          "frames in the order the vehicle sent them");
    check(g_chip->sameIdCoresident() == 0,
          "spec 2: the chip never holds two frames of one identifier at "
          "once -- the precondition for a same-id transposition is "
          "absent, not merely unobserved");

    // The historical note, kept because the reasoning in it was the
    // reason the check could not be written sooner.
    //
    // The run above transposes ONE adjacent pair of 0x3FE frames out of
    // 22,117. The mechanism is understood and is in the driver, not the
    // harness: `loadAndSend()` fills whichever hardware buffer is free,
    // and the chip arbitrates between loaded buffers by TXP, breaking a
    // tie on HIGHEST BUFFER NUMBER (p15 s3.2) rather than on load
    // order. So two frames of one id in flight at once can leave in the
    // order 1-then-0. The driver never writes TXP, so every buffer sits
    // at 00 and the tie-break always decides.
    //
    // This is a spec 2 same-identifier order violation, reproduced on
    // the host for the first time. It is not asserted yet because the
    // fix -- setting TXP in load order -- is a flight behaviour change
    // that belongs in the spec before the code, and that decision is
    // the user's and the review session's. Asserting now would paint
    // the suite red on a known, understood, pending item, and a red
    // suite everyone has learned to ignore is worse than a loud line.
    //
    // WHEN THE TXP CHANGE LANDS, TURN THIS INTO A check(). It is one
    // line, and leaving it as a print afterwards would be the quiet
    // kind of rot this file keeps finding.
    // --- GLOBAL DISPLACEMENT, ACROSS ALL IDS -------------------------
    //
    // Spec 2 claims a different-id transposition is "by exactly one
    // position". That is a claim about a bound, and a bound needs the
    // DISTRIBUTION and the MAXIMUM, not an example. Three buffers can
    // be pending at once with equal TXP, and selection takes the
    // highest buffer number each time, so a pass that loads 0,1,2 and
    // then sends 2,1,0 displaces the first frame by two. Whether that
    // actually happens is measurable here.
    //
    // Displacement = wire position - offer position, in the GLOBAL
    // sequence. Measured by matching each delivered frame to the
    // earliest unconsumed offer of the same id and payload, which is
    // the only unambiguous pairing when payloads repeat.
    {
      std::map<uint32_t, size_t> cursor;
      std::map<int, uint32_t> hist;       // displacement -> count
      int worst = 0;
      size_t paired = 0;
      // Offer position of every frame, per id, in global index terms.
      // BOTH SEQUENCES MUST BE NUMBERED OVER THE SAME POPULATION.
      // Indexing offers by their raw position in `offered_veh` while
      // numbering the wire over non-CMD frames only drifts the two
      // apart by one per CMD_ID frame skipped -- 199 of them here. The
      // first version did exactly that and reported max |d| = 200 with
      // 21,901 of 22,117 frames "displaced by more than one position",
      // which is a constant offset wearing the costume of a finding.
      // `op` advances only for frames the wire sequence will also
      // count.
      std::map<uint32_t, std::vector<size_t> > offer_pos;
      size_t op = 0;
      for (size_t j = 0; j < offered_veh.size(); j++) {
        if (offered_veh[j].id == interposer::CMD_ID) continue;
        offer_pos[offered_veh[j].id].push_back(op++);
      }

      size_t wire_i = 0;
      for (size_t k = 0; k < w.size(); k++) {
        if (!w[k].delivered) continue;
        const mcpfake::CanFrame& f = w[k].frame;
        if (f.id == interposer::CMD_ID) continue;
        const size_t n = cursor[f.id]++;
        std::vector<size_t>& pos = offer_pos[f.id];
        if (n < pos.size()) {
          const int d = (int)wire_i - (int)pos[n];
          hist[d]++;
          if (d > worst) worst = d;
          if (-d > worst) worst = -d;
          paired++;
        }
        wire_i++;
      }
      std::printf("  --  global displacement over %u paired frames, "
                  "max |d| = %d\n", (unsigned)paired, worst);
      // Print the middle of the distribution plus anything beyond 1,
      // since the spec's claim is specifically about 1.
      uint32_t beyond1 = 0;
      for (std::map<int, uint32_t>::const_iterator it = hist.begin();
           it != hist.end(); ++it) {
        if (it->first >= -3 && it->first <= 3)
          std::printf("  --      d=%+d : %u\n", it->first, it->second);
        if (it->first > 1 || it->first < -1) beyond1 += it->second;
      }
      std::printf("  --  frames displaced by MORE than one position: %u "
                  "(spec 2 says transposition is by exactly one)\n",
                  beyond1);
    }

    // --- TWO BUFFERS HOLDING ONE ID AT THE SAME MOMENT ---------------
    //
    // The precondition for a same-id transposition. If it never
    // happens, the 0x3FE pair came from somewhere else and the
    // buffer-number explanation is wrong.
    std::printf("  --  moments with the same id loaded in two or more "
                "buffers at once: %u\n", g_chip->sameIdCoresident());

    if (out_of_order == 0) {
      std::printf("  --  per-id order HOLDS on this run\n");
    } else {
      std::printf("  ***  FINDING, not asserted: %u same-id frame(s) "
                  "reached the charger out of order. Spec 2 cares about "
                  "this. Cause: equal TXP, so the chip breaks the tie on "
                  "buffer number (p15 s3.2), not load order.\n",
                  (unsigned)out_of_order);
    }
  }

  // --- THE SHORTFALL THAT USED TO BE REPORTED HERE, AND WAS NOT REAL ---
  //
  // This block used to print "UNEXPLAINED: 22,316 frames offered toward
  // the charger, 194 loaded into the chip", with a list of things ruled
  // out and "STILL OPEN: the charger TX path as main.cpp drives it --
  // up to 16 sends per loop from dispatch()". That is recorded rather
  // than deleted because the wrong answer was the informative part.
  //
  // There was no shortfall. The chip fake assembled a loaded frame only
  // when exactly 13 bytes had been clocked into LOAD TX BUFFER, while
  // the driver correctly clocks 5 header bytes plus DLC (p65 s12.6 by
  // way of s12.5). Every frame with DLC < 8 therefore left the buffer
  // unloaded while the following RTS still set TXREQ (s12.7), and that
  // buffer was then invisible to both sides: the fake's engine requires
  // TXREQ and a loaded frame, the driver reads only TXREQ. All three
  // wedged until the 100 ms age-out. 26.5% of this capture is DLC < 8.
  // See finishLoadTx() in mcp2515_fake_chip.cpp.
  //
  // The "RULED OUT" list was accurate and useless: every item on it was
  // eliminated with 8-byte payloads, which is the one DLC the broken
  // gate handled correctly. Each elimination was individually sound and
  // the set of them pointed confidently at main.cpp's dispatch() burst
  // pattern -- the last thing standing, rather than the thing shown.
  // That is the trap worth remembering: a list of ruled-out causes
  // carries no warning when every entry shares one blind spot.
  //
  // Now reported as an ordinary measurement. Still not asserted on:
  // spec 9.1 puts a load-dependent criterion on the bench, and that was
  // always the right call here even when the number was alarming.
  {
    const uint32_t loads = g_chip->txLoads();
    std::printf("  --  charger TX: %u offered, %u loaded into the chip "
                "(%.1f%%), %u never started on the wire\n",
                offered_v, loads, 100.0 * loads / (offered_v ? offered_v : 1),
                g_chip->txNeverStarted());
    std::printf("  --  loop() ran %llu times in %.3f s = %.0f/s; "
                "%.2f loads per loop; worst load->SOF %.3f ms\n",
                (unsigned long long)loops, g_chip->micros() / 1e6,
                loops / (g_chip->micros() / 1e6),
                (double)loads / (loops ? loops : 1),
                g_chip->maxLoadToSofNs() / 1e6);
    std::printf("  --  (not asserted: spec 9.1 puts a load-dependent "
                "criterion on the bench)\n");
  }

  // --- the core survived it ------------------------------------------
  const twaifake::Frame* lastst = lastFrom(DIAG_STATUS);
  check(lastst != nullptr, "diagnostics were still going at the end");
  if (lastst) {
    std::printf("  --  final 0x7F4: state=%u trip=%u flags=0x%02X\n",
                lastst->data[2], lastst->data[3], lastst->data[4]);
    check(lastst->data[3] == 0,
          "no trip on 10 s of real truck traffic (0x7F4 B3 trip_reason)");
  }
  check(countFrom(DIAG_STATUS, 0) >= 5,
        "the diagnostics kept their cadence underneath the traffic");
  assertPathsExercised();
}

struct Case {
  const char* name;
  void (*fn)();
};

const Case kCases[] = {
    {"boot", caseBoot},
    {"cadence", caseCadence},
    {"fields", caseFields},
    {"bridge-ok", caseBridgeOk},
    {"tx-fail", caseTxFail},
    {"failed-start", caseFailedStart},
    {"replay", caseReplay},
    {"rx-rollover-order", caseRxRolloverOrder},
    {"rx-rollover-refill", caseRxRolloverRefill},
};

}  // namespace

int main(int argc, char** argv) {
  static Mcp2515Fake chip;
  g_chip = &chip;

  const int n = (int)(sizeof(kCases) / sizeof(kCases[0]));
  if (argc < 2) {
    // No argument: list the cases, so run.sh (or a person) can see what
    // there is without the list being duplicated in the shell script.
    for (int i = 0; i < n; i++) std::printf("%s\n", kCases[i].name);
    return 0;
  }

  const std::string want(argv[1]);
  for (int i = 0; i < n; i++) {
    if (want != kCases[i].name) continue;
    std::printf("--- l2: %s ---\n", kCases[i].name);
    kCases[i].fn();
    if (failures) {
      std::printf("\n%d failure(s) in %s\n", failures, kCases[i].name);
      return 1;
    }
    std::printf("test_l2_bridge[%s]: all checks OK\n", kCases[i].name);
    return 0;
  }
  std::printf("unknown case %s\n", want.c_str());
  return 2;
}
