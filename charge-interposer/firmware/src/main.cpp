// Vtrux charge interposer -- ESP32-CAN-X2 (ESP32-S3-WROOM-1-N8R8).
//
//     vehicle segment ==[ CAN1 / TWAI ]== CORE ==[ CAN2 / MCP2515 ]== charger
//
// All of the decision-making is in machine.cpp, which is a verified port of the
// Python reference in the parent folder and knows nothing about this file. This
// file only moves frames.
//
// Pins come from the Arduino variant `aslcanx2` (espressif/arduino-esp32) with
// one exception: the MCP2515 IRQ on GPIO3 is documented on the Autosport Labs
// wiki but absent from pins_arduino.h, so it is spelled out here.
//
// NO HARDWARE MODS ASSUMED. Both MCP2562 transceivers are hardwired always-on
// -- pin 8 (STBY) has no GPIO on this board -- so the firmware cannot take
// itself off the bus. What it can do is not make things worse: the core
// originates nothing toward the charger (spec 5.3), the only frames added are
// the diagnostics on the vehicle side (spec 8), and every dropped frame is
// counted rather than hidden. See
// notes/plans/can-bus-fault-injection-test.md for the test that would tell us
// what a fault here actually costs.

#include <Arduino.h>
#include <SPI.h>
#include <esp_app_desc.h>

#include "bridge_health.h"
#include "machine.h"
#include "port_mcp2515.h"
#include "port_twai.h"

// --------------------------------------------------------------------------
// Spec 8.3: builds loaded to the truck have serial output disabled.
//
// Every print below runs inside the bridge loop. A USB host attached but not
// reading can block the CDC write and the bridge would stall with it. These
// compile the calls OUT under -DINTP_NO_SERIAL ([env:truck]) rather than
// guarding them at run time, so in the truck build there is no call left to
// block on. The spec 8.2 diagnostic frames are unaffected -- 0x7F4-0x7F7 are
// how the truck build is observed, and `intp_serial` reads 0 there, which is
// what identifies it as a truck image on the wire.
#ifdef INTP_NO_SERIAL
#define INTP_SERIAL_BEGIN(baud) ((void)0)
#define INTP_PRINTF(...)        ((void)0)
#define INTP_PRINT(x)           ((void)0)
#define INTP_PRINTLN(x)         ((void)0)
#define INTP_SERIAL_READY       false
#else
#define INTP_SERIAL_BEGIN(baud) Serial.begin(baud)
#define INTP_PRINTF(...)        Serial.printf(__VA_ARGS__)
#define INTP_PRINT(x)           Serial.print(x)
#define INTP_PRINTLN(x)         Serial.println(x)
#define INTP_SERIAL_READY       ((bool)Serial)
#endif


using namespace interposer;

// --- board wiring ---------------------------------------------------------
static const int CAN1_TX_GPIO = 7;   // variant: CAN1_TX
static const int CAN1_RX_GPIO = 6;   // variant: CAN1_RX
static const uint8_t MCP_CS = 10;    // variant: CS
static const uint8_t MCP_SCK = 12;   // variant: SCK
static const uint8_t MCP_MISO = 13;  // variant: MISO
static const uint8_t MCP_MOSI = 11;  // variant: MOSI
static const uint8_t MCP_INT = 3;    // wiki only; not in pins_arduino.h

static const uint32_t BITRATE = 500000UL;
static const uint32_t TICK_INTERVAL_MS = 10;
static const uint32_t STATUS_INTERVAL_MS = 5000;

static SPIClass mcp_spi(HSPI);
static TwaiPort vehicle(CAN1_TX_GPIO, CAN1_RX_GPIO);
static Mcp2515Port charger(mcp_spi, MCP_CS, MCP_INT);

static Config cfg;
static InterposerCore* core = nullptr;

static uint32_t last_tick_ms = 0;
static uint32_t last_status_ms = 0;
static uint32_t last_diag_ms = 0;
static uint8_t last_diag_state = 255;
static uint32_t emit_overflow = 0;
static uint32_t bridge_drops = 0;
static uint32_t diag_tx_fail = 0;
static bool healthy = false;
// B-7d: `healthy` is both controllers. This one is the vehicle side alone,
// because a board that bridges nothing can still say so on the vehicle bus.
static bool vehicle_up = false;

// Spec 2.2: failed transmissions TOWARD THE CHARGER, counted only while the
// VCU's flow bit is 1. The ports' own counters are monotonic and know
// nothing about the flow bit, so the delta is taken every loop iteration and
// added only when the charger is expected. Sampling at the 1 Hz diagnostic
// cadence instead would misattribute up to a second of failures at each flow
// transition.
static uint32_t txf_charger_seen = 0;      // port total at the last sample
static uint32_t txf_while_expected = 0;    // what goes on the wire

// Spec 8.2 (B-3, widened 2026-10-04): `bridge_ok` is health since the
// previous status frame. The rule lives in bridge_health.h, which is pure
// and host-testable; this file only counts.
static BridgeHealth bridge_health;

// Bridged frames that failed to reach the VEHICLE. Counted in dispatch(),
// mirrors excluded -- see the comment there. NOT vehicle.txDropped(), which
// also counts the board's own diagnostic sends.
static uint32_t veh_bridge_fail = 0;

// --- diagnostics (spec 8.2 / 8.3) -----------------------------------------
// Build id (0x7F7 B0-B3): the first four bytes of this application's ELF
// SHA-256, read from the ESP-IDF app descriptor.
//
// This replaced FNV-1a of a GIT_SHA string, copied from the WiCAN inhibitor.
// That was never a fingerprint here: platformio.ini does not pass -DGIT_SHA,
// so every build ever flashed reported fnv1a("unknown") = 2608177081. A
// constant that looks like a hash is worse than no field, because it reads
// as provenance. There is also no git checkout on the bench machine, so
// there is no SHA to pass even if the flag were added.
//
// Measured 2026-09-20: two clean builds of identical source produced
// different ELF hashes, so this identifies the BUILD, not the source. Same
// value proves the same binary; a different value does not prove the code
// changed. Check it against the artifact before flashing --
// `esptool --chip esp32s3 image-info firmware.bin` prints `ELF file SHA256`
// and the first four bytes are what the board reports. (Ignore that
// command's Project name / App version / Compile time: those come from the
// prebuilt Arduino library, not from this project.)
static uint32_t buildId() {
  const esp_app_desc_t* d = esp_app_get_description();
  if (d == NULL) return 0;
  return ((uint32_t)d->app_elf_sha256[0] << 24) |
         ((uint32_t)d->app_elf_sha256[1] << 16) |
         ((uint32_t)d->app_elf_sha256[2] << 8) |
         (uint32_t)d->app_elf_sha256[3];
}
static const uint32_t DIAG_INTERVAL_MS = 1000;
static const uint32_t DIAG_IDS[4] = {DIAG_STATUS_ID, DIAG_OBSERVED_ID,
                                     DIAG_COUNTERS_ID, DIAG_BUILD_ID};

static uint8_t sat8(uint32_t v) { return v > 255u ? 255u : (uint8_t)v; }

// Spec 2.2. Every failed transmission toward the charger is counted once:
// the three charger-port tallies are disjoint (queue full, controller
// reported failure, aged out past 100 ms) and none of them is the vehicle
// side. The gate is the VCU's own command, so the counter reads 0 while
// driving however many frames bounce off a charger that is not there, and
// any non-zero value is a problem during a session.
static void accrueChargerTxFailures() {
  const uint32_t now =
      charger.txDropped() + charger.txFailed() + charger.txAged();
  const uint32_t delta = now - txf_charger_seen;
  txf_charger_seen = now;
  if (delta && core->vcuFlow()) txf_while_expected += delta;
}

// Send the four status frames on the VEHICLE port (never toward the charger),
// best-effort, and print the matching serial line. `status_only` sends just
// 0x7F4 -- the immediate frame on a state change.
static void sendDiag(uint32_t t_ms, bool status_only) {
  uint8_t fr[4][8];
  const uint32_t ovf = vehicle.rxDropped() + charger.rxDropped() + emit_overflow;
  const uint32_t boe = vehicle.busOffEvents() + charger.busOffEvents();

  // Spec 2.2: each failed transmission counted ONCE, and only toward the
  // charger while the flow bit is 1 -- accrueChargerTxFailures() does the
  // counting in the loop.
  //
  // The counting used to add `bridge_drops` and `diag_tx_fail` on top of the
  // ports' own `txDropped()`, but dispatch() increments bridge_drops for
  // exactly the sends that already incremented the port counter -- so every
  // failure was counted twice, and `intp_tx_fail` read its 255 ceiling before
  // every bench run. The ports are the single place a failure is counted; the
  // two local tallies are kept for the serial line only, where seeing them
  // separately is useful, and are no longer summed in.
  const uint32_t txf = txf_while_expected;

  // Spec 8.2: current health, not "since boot". A single overflow in hour
  // one must not leave the flag clear for the rest of the run.
  const bool bridge_ok = bridge_health.sample(
      healthy, ovf, boe, veh_bridge_fail,
      vehicle.busOff() || charger.busOff());

  core->diagFrames(t_ms, sat8(ovf), sat8(txf), t_ms, bridge_ok,
                   INTP_SERIAL_READY, buildId(), fr);
  const uint8_t n = status_only ? 1 : 4;
  for (uint8_t i = 0; i < n; i++) {
    if (!vehicle.send(DIAG_IDS[i], false, fr[i], 8)) diag_tx_fail++;
  }
  if (status_only) return;
#ifdef INTP_NO_SERIAL
  (void)fr;   // spec 8.3: no serial on the truck build, so nothing to format
#else
  // spec 8.3: the same content, one line, so a bench run needs no CAN tap
  const uint8_t* s = fr[0];
  const uint8_t* o = fr[1];
  const uint8_t* k = fr[2];
  INTP_PRINTF(
      "diag st=%s trip=%u flags=0x%02x ilim=", stateName(s[2]), s[3], s[4]);
  if (s[5] == 255) {
    INTP_PRINT("-");
  } else {
    INTP_PRINTF("%u.%u", s[5] / 10, s[5] % 10);
  }
  INTP_PRINTF(" vmax=%u chgmax=%u soc=%u.%u evap=%u chg=%u mod=%lu synth=%u "
                "ovf=%u txf=%u silent=%u fw=%u schema=%u\n",
                (unsigned)(o[0] | (o[1] << 8)), (unsigned)(o[2] | (o[3] << 8)),
                o[4] / 2, (o[4] % 2) ? 5 : 0, (o[7] >> 7) & 1u, o[5],
                (unsigned long)(k[0] | ((uint32_t)k[1] << 8) |
                                ((uint32_t)k[2] << 16) | ((uint32_t)k[3] << 24)),
                (unsigned)(k[4] | (k[5] << 8)), k[6], k[7],
                (unsigned)((o[7] >> 6) & 1u), s[1], s[0]);
#endif
}

// --------------------------------------------------------------------------

static void dispatch(const EmitList& out) {
  for (uint8_t i = 0; i < out.n; i++) {
    const Emit& e = out.items[i];
    const bool ok = (e.port == TO_CHARGER)
                        ? charger.send(e.id, e.ext, e.data, e.len)
                        : vehicle.send(e.id, e.ext, e.data, e.len);
    if (!ok) {
      bridge_drops++;
      // Spec 8.2: a BRIDGED frame that failed to reach the vehicle clears
      // bridge_ok for this interval. The spec 8.1 mirrors come through here
      // toward the vehicle too and are NOT bridged traffic, so they are
      // excluded -- they carry source address 0xE1, which is unused on this
      // truck, and that is the premise the mirror scheme already rests on.
      const bool mirror = e.ext && (e.id & 0xFFu) == DIAG_SA;
      if (e.port == TO_VEHICLE && !mirror) veh_bridge_fail++;
    }
  }
  if (out.dropped) emit_overflow += out.dropped;
}

static void reportEvents() {
  for (uint8_t i = 0; i < core->eventCount(); i++) {
    const Event& e = core->event(i);
    INTP_PRINTF("[%lu] %-12s %s", (unsigned long)e.t_ms,
                  stateName(e.state), eventName(e.code));
    if (e.code == EV_TRIP) {
      // "TRIP: reason", not "TRIP (reason)". machine.py logs
      // "TRIP: %s" % reason, and run_scenario.py matches the board's serial
      // output against the very same ev(r"TRIP: ...") patterns it uses for the
      // simulated interposer. The bracketed form parsed as a failure on every
      // trip scenario run against hardware even though the trip, the release
      // burst and the state change were all correct -- fault-during-override
      // reported FAIL on 2026-09-11 with synth=20 and final state SAFE.
      INTP_PRINTF(": %s", tripName((uint8_t)e.a));
    } else if (e.a || e.b || e.c) {
      INTP_PRINTF(" a=%ld b=%ld c=%ld", (long)e.a, (long)e.b, (long)e.c);
    }
    INTP_PRINTLN("");
  }
  core->clearEvents();
}

#ifdef INTP_ORDER_WITNESS
// Dump the completion-order witness.
//
// THE FIRST LINE IS THE RECONCILIATION, deliberately. `completions`
// against the frames the core actually forwarded to the charger is the
// one number that says whether the rest of the dump can be trusted: a
// witness that quietly misses completions is worse than none, because
// it would be used to exonerate the firmware. That exact gap existed
// and was found by review on 2026-10-04 -- the refill loop reloaded
// buffers the completion loop had not yet resolved -- so this line is
// read FIRST and the entries only after it balances.
static void witnessDump() {
  const uint32_t comp = Mcp2515Port::witnessCompletions();
  const uint32_t fwd = (uint32_t)core->fwdV2C();
  INTP_PRINTLN("");
  INTP_PRINTLN("*** WITNESS BUILD (env esp32-can-x2-witness) -- "
               "completion-order dump ***");
  INTP_PRINTF("  RECONCILE: completions=%lu forwarded(v2c)=%lu %s\n",
              (unsigned long)comp, (unsigned long)fwd,
              comp == fwd ? "BALANCED"
                          : "*** MISMATCH -- entries below are NOT a "
                            "complete record ***");
  // WHOLE-RUN ORDER, which the stored entries below do NOT cover: they
  // are the first WITNESS_N completions, about 2% of a truck-rate
  // replay. These counters see every completion.
  INTP_PRINTF("  ORDER (whole run): inversions=%lu maxDisplacement=%lu "
              "perIdInversions=%lu idsTracked=%lu idTableOverflow=%lu%s\n",
              (unsigned long)Mcp2515Port::witnessInversions(),
              (unsigned long)Mcp2515Port::witnessMaxDisplacement(),
              (unsigned long)Mcp2515Port::witnessIdInversions(),
              (unsigned long)Mcp2515Port::witnessIdsTracked(),
              (unsigned long)Mcp2515Port::witnessIdOverflow(),
              Mcp2515Port::witnessIdOverflow()
                  ? "  *** per-id count is a LOWER BOUND: identifiers did "
                    "not fit the table ***"
                  : "");
  INTP_PRINTF("  BUSY-AT-LOAD: 0 others=%lu  1 other=%lu  2 others=%lu  "
              "loadsWithOlderPending=%lu\n",
              (unsigned long)Mcp2515Port::witnessBusyAtLoad(0),
              (unsigned long)Mcp2515Port::witnessBusyAtLoad(1),
              (unsigned long)Mcp2515Port::witnessBusyAtLoad(2),
              (unsigned long)Mcp2515Port::witnessLoadsWithOlderPending());
  INTP_PRINTF("  loads=%lu stored=%lu of %lu passes=%lu multi-pass=%lu "
              "truncated=%s\n",
              (unsigned long)Mcp2515Port::witnessLoads(),
              (unsigned long)Mcp2515Port::witnessCount(),
              (unsigned long)Mcp2515Port::WITNESS_N,
              (unsigned long)Mcp2515Port::witnessPasses(),
              (unsigned long)Mcp2515Port::witnessMultiPasses(),
              Mcp2515Port::witnessFull() ? "YES" : "no");
  if (Mcp2515Port::witnessMultiPasses()) {
    INTP_PRINTF("  NOTE: %lu pass(es) completed more than one buffer. "
                "Entries sharing a pass number are mutually UNORDERED -- "
                "the board cannot say which reached the wire first.\n",
                (unsigned long)Mcp2515Port::witnessMultiPasses());
  }
  INTP_PRINTLN("  seq,buf,id,ext,pass,outcome");
  const Mcp2515Port::Completion* c = Mcp2515Port::witness();
  for (uint32_t i = 0; i < Mcp2515Port::witnessCount(); i++) {
    INTP_PRINTF("  %lu,%u,%lX,%u,%lu,%c\n",
                (unsigned long)c[i].load_seq, c[i].buf,
                (unsigned long)c[i].id, c[i].ext,
                (unsigned long)c[i].pass, (char)c[i].outcome);
  }
  INTP_PRINTLN("*** end witness dump ***");
}
#endif

static void status(uint32_t t_ms) {
#ifdef INTP_NO_SERIAL
  // spec 8.3: the whole body is serial output, so on the truck build
  // there is nothing left to do with the timestamp. Same idiom as
  // sendDiag's `(void)fr`.
  (void)t_ms;
#endif
  INTP_PRINTF(
      "t=%lus state=%-11s vmax=%ldmV chgmax=%ldcA ibat=%ldcA soc=%u.%u%% "
      "chg=%u | v2c=%lu c2v=%lu mod=%lu synth=%lu\n",
      (unsigned long)(t_ms / 1000), stateName(core->state()),
      (long)core->vmax(), (long)core->chgMax(), (long)core->ibat(),
      core->socHalf() / 2, (core->socHalf() % 2) ? 5 : 0,
      core->chargerState(), (unsigned long)core->fwdV2C(),
      (unsigned long)core->fwdC2V(), (unsigned long)core->modified(),
      (unsigned long)core->synth());

  // Anything non-zero on this line means the bridge is not transparent, which
  // matters more than any of the numbers above.
  //
  // BUS ERRORS ARE IN THIS CONDITION, added 2026-10-05, and their
  // absence was a real gap. The line carries `err=` for both ports,
  // but a port accumulating bus errors with nothing DROPPED did not
  // satisfy any clause here, so the line never printed and the error
  // count could not be read at all. That is exactly the state a
  // lightly loaded bench sits in, and it silently defeated an error
  // sweep: two of four load points reported "veh counters
  // unreadable" because the board, having dropped nothing, said
  // nothing. A counter that is only visible when some OTHER counter
  // is non-zero is not observable.
  if (vehicle.rxDropped() || vehicle.txDropped() || charger.rxDropped() ||
      charger.txDropped() || bridge_drops || emit_overflow ||
      vehicle.busOff() || charger.busOff() ||
      vehicle.busErrors() || charger.busErrors()) {
    INTP_PRINTF(
        "  LOSS veh(rx=%lu tx=%lu err=%lu off=%d peak=%lu) "
        "chg(rx=%lu tx=%lu err=%lu off=%d ring=%u/%u) bridge=%lu emit=%lu\n",
        (unsigned long)vehicle.rxDropped(), (unsigned long)vehicle.txDropped(),
        (unsigned long)vehicle.busErrors(), vehicle.busOff() ? 1 : 0,
        (unsigned long)vehicle.rxQueuePeak(),
        (unsigned long)charger.rxDropped(), (unsigned long)charger.txDropped(),
        (unsigned long)charger.busErrors(), charger.busOff() ? 1 : 0,
        charger.rxRingUsed(), charger.txRingUsed(),
        (unsigned long)bridge_drops, (unsigned long)emit_overflow);
    INTP_PRINTF("       bridged frames that missed the vehicle: %lu "
                "(clears bridge_ok for that interval)\n",
                (unsigned long)veh_bridge_fail);
    if (vehicle.txDiscardedBusOff()) {
      // Frames the TWAI driver had queued when the vehicle port went
      // bus-off. `twai_initiate_recovery()` resets the TX queue (esp32s3
      // driver/twai.h), so they never reached the wire -- and they are in
      // no other figure on this line: send() accepted them, so they are
      // not in veh tx=, and they never failed an attempt either.
      //
      // Reported separately rather than folded into veh_bridge_fail
      // because the PORT cannot tell a bridged frame from a diagnostic
      // send -- that distinction is made in dispatch(), above it. Adding
      // them to veh_bridge_fail would overstate the bridged loss by
      // however many diagnostic frames happened to be queued.
      // spec 8.2 is unaffected: bridge_ok already clears for the interval
      // containing the bus-off entry.
      INTP_PRINTF("       vehicle tx discarded by bus-off recovery: %lu "
                  "(bridged and diagnostic, not separable here)\n",
                  (unsigned long)vehicle.txDiscardedBusOff());
    }
    // Spec 2.2's charger counter is one byte and answers one question --
    // did frames meant for the charger fail to reach it during a session.
    // The parts are only separable here, and they mean different things:
    // `abandoned` never reached the wire, `aged` was superseded in the ring
    // before it was ever loaded, and `late` DID reach the wire after we had
    // given up on it, which is not a failure at all (the charger
    // acknowledged) and is deliberately not in the CAN field.
    INTP_PRINTF("       charger tx: abandoned=%lu aged=%lu queue-full=%lu "
                "delivered-late=%lu\n",
                (unsigned long)charger.txFailed(),
                (unsigned long)charger.txAged(),
                (unsigned long)charger.txDropped(),
                (unsigned long)charger.txLate());
    if (charger.txUnexplained()) {
      // p15 s3.3 says this state cannot occur. If it has, a datasheet
      // behaviour the driver is built on does not hold on this silicon.
      INTP_PRINTF("       *** charger tx UNEXPLAINED=%lu -- a buffer went "
                  "idle with TXnIF clear and no abort. DS20001801J p15 "
                  "s3.3 says that cannot happen.\n",
                  (unsigned long)charger.txUnexplained());
    }
  }
}

void setup() {
  INTP_SERIAL_BEGIN(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  delay(200);

  INTP_PRINTLN("");
  INTP_PRINTLN("Vtrux charge interposer");
#ifdef INTP_ORDER_WITNESS
  // A board left in this state must not be mistaken for the bench
  // bridge. Said at boot and again on every dump header, because a
  // bench session is usually joined part-way through its log.
  INTP_PRINTLN("*** WITNESS BUILD (env esp32-can-x2-witness) -- "
               "completion-order instrumentation compiled in, NOT a "
               "flight image ***");
#endif
  INTP_PRINTF("bitrate %lu, tick %lu ms\n", (unsigned long)BITRATE,
                (unsigned long)TICK_INTERVAL_MS);

  mcp_spi.begin(MCP_SCK, MCP_MISO, MCP_MOSI, MCP_CS);

  const bool v_ok = vehicle.begin(BITRATE);
  INTP_PRINTF("CAN1 (vehicle, TWAI)   : %s\n", v_ok ? "up" : "FAILED");
  const bool c_ok = charger.begin(BITRATE);
  INTP_PRINTF("CAN2 (charger, MCP2515): %s\n", c_ok ? "up" : "FAILED");

  healthy = v_ok && c_ok;
  vehicle_up = v_ok;
  if (!healthy) {
    // Both segments are severed either way -- the board is physically in the
    // bus. Say so loudly rather than pretending to bridge; a half-open bridge
    // is worse than an obvious failure.
    INTP_PRINTLN("BRIDGE DOWN -- one or both segments are not bridged.");
    if (v_ok) {
      // Spec 2.1 / 8.2 (B-7d): the vehicle controller came up, so say so on
      // the vehicle bus. A board that failed to start and a board that is
      // not fitted look identical in a truck log otherwise, and the two call
      // for different repairs.
      INTP_PRINTLN("vehicle side is up: 0x7F4 will report the bridge failed.");
    }
  }

  configDefaults(cfg);
  static InterposerCore instance(cfg, millis());
  core = &instance;

  last_tick_ms = millis();
  last_status_ms = last_tick_ms;
}

void loop() {
  const uint32_t t_ms = millis();

#ifdef INTP_ORDER_WITNESS
  // BENCH ONLY, and compiled out of every flight image with the rest
  // of the witness. 'w' dumps, 'W' resets the window. Reading one byte
  // per pass rather than draining the buffer keeps the cost per loop
  // bounded -- the bridge loop is the one place in this firmware that
  // must not acquire an unbounded piece of work (spec 8.3 made the
  // same argument about a blocked CDC write).
  if (Serial.available() > 0) {
    const int ch = Serial.read();
    if (ch == 'w') witnessDump();
    if (ch == 'W') {
      Mcp2515Port::witnessReset();
      INTP_PRINTLN("*** WITNESS BUILD -- window reset ***");
    }
  }
#endif

  vehicle.service(t_ms);
  charger.service(t_ms);

  if (!healthy) {
    digitalWrite(LED_BUILTIN, (t_ms / 100) % 2);  // fast blink = down
    // Spec 2.1 / 8.2 (B-7d): bridge nothing, but keep saying so. The core
    // has seen no frames, so this reports PASSTHROUGH with `bridge_ok`
    // clear -- sendDiag forces it clear while `healthy` is false. Only 0x7F4
    // goes out; the other three carry observations and counters that mean
    // nothing on a board that is not bridging.
    if (vehicle_up && (uint32_t)(t_ms - last_diag_ms) >= DIAG_INTERVAL_MS) {
      last_diag_ms = t_ms;
      sendDiag(t_ms, true);
    }
    return;
  }

  accrueChargerTxFailures();   // spec 2.2, before this pass can fail anything

  EmitList out;
  RxFrame f;

  // Vehicle -> core. Bounded per iteration so a burst on one side cannot
  // starve the other; whatever is left stays in the driver queue.
  for (uint8_t i = 0; i < 16 && vehicle.receive(f); i++) {
    out.clear();
    core->onVehicleFrame(f.id, f.ext, f.data, f.len, t_ms, out);
    dispatch(out);
  }

  for (uint8_t i = 0; i < 16 && charger.receive(f); i++) {
    out.clear();
    core->onChargerFrame(f.id, f.ext, f.data, f.len, t_ms, out);
    dispatch(out);
  }

  if ((uint32_t)(t_ms - last_tick_ms) >= TICK_INTERVAL_MS) {
    last_tick_ms = t_ms;
    out.clear();
    core->tick(t_ms, out);
    dispatch(out);
  }

  if (core->eventCount()) reportEvents();

  // Diagnostics: one STATUS frame the moment the state changes, and all
  // four frames plus the serial line every second (spec 8.2 / 8.3).
  if (core->state() != last_diag_state) {
    last_diag_state = core->state();
    sendDiag(t_ms, true);
  }
  if ((uint32_t)(t_ms - last_diag_ms) >= DIAG_INTERVAL_MS) {
    last_diag_ms = t_ms;
    sendDiag(t_ms, false);
  }

  if ((uint32_t)(t_ms - last_status_ms) >= STATUS_INTERVAL_MS) {
    last_status_ms = t_ms;
    status(t_ms);
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }
}
