// Stage 0 board self-test -- no dongles, no sims, no truck.
//
// Wire the board to itself:  CAN1H <-> CAN2H,  CAN1L <-> CAN2L
// (the vendor's own ping_pong wiring). Termination works out by accident:
// 120 ohm on each port in parallel is the 60 ohm a CAN segment wants.
//
// This exists because the two port drivers are the ONLY completely unexercised
// code in the project. machine.cpp is verified byte-for-byte against the Python
// reference over eight cases; port_twai.cpp and port_mcp2515.cpp have never
// seen a transceiver. In particular the MCP2515 driver is hand-written -- bit
// timing for the 16 MHz crystal, extended-ID encode/decode, IRQ-driven RX on
// GPIO3, and a non-blocking TX path that replaced coryjfowler's blocking one.
// Every one of those is a place to be wrong.
//
// NOT the bridge firmware. Do not flash main.cpp with these jumpers fitted: the
// bridge would forward CAN1 -> CAN2 straight back onto its own wire and storm.
//
//     pio run -d projects/vtrux/tools/interposer/firmware -e selftest -t upload
//     pio device monitor -b 115200

#include <Arduino.h>
#include <SPI.h>

#include "port_mcp2515.h"
#include "port_twai.h"

using namespace interposer;

static const int CAN1_TX_GPIO = 7;
static const int CAN1_RX_GPIO = 6;
static const uint8_t MCP_CS = 10;
static const uint8_t MCP_SCK = 12;
static const uint8_t MCP_MISO = 13;
static const uint8_t MCP_MOSI = 11;
static const uint8_t MCP_INT = 3;

static const uint32_t BITRATE = 500000UL;

static SPIClass mcp_spi(HSPI);
static TwaiPort can1(CAN1_TX_GPIO, CAN1_RX_GPIO);
static Mcp2515Port can2(mcp_spi, MCP_CS, MCP_INT);

static uint16_t checks_run = 0;
static uint16_t checks_failed = 0;

static void check(bool ok, const char* what) {
  checks_run++;
  if (!ok) checks_failed++;
  Serial.printf("  [%s] %s\n", ok ? "PASS" : "FAIL", what);
}

// Drain both ports for `ms`, collecting into `got` whatever arrives on `dst`.
// Returns how many frames arrived.
static uint32_t drain(CanPort& dst, CanPort& other, uint32_t ms,
                      RxFrame* got, uint32_t cap) {
  const uint32_t end = millis() + ms;
  uint32_t n = 0;
  while ((int32_t)(millis() - end) < 0) {
    const uint32_t t = millis();
    dst.service(t);
    other.service(t);
    RxFrame f;
    while (dst.receive(f)) {
      if (n < cap) got[n] = f;
      n++;
    }
  }
  return n;
}

// --- phase 1: does anything cross at all, and does it cross intact? --------

struct Probe {
  uint32_t id;
  bool ext;
  uint8_t len;
  const char* what;
};

static const Probe PROBES[] = {
    {0x410, false, 8, "std 0x410 (BMS status)"},
    {0x430, false, 6, "std 0x430 (cell v/t), DLC 6"},
    {0x7FF, false, 8, "std 0x7FF (max standard id)"},
    {0x18EFC000, true, 8, "ext 0x18EFC000 (the command frame)"},
    {0x18FFD4C0, true, 8, "ext 0x18FFD4C0 (charger status)"},
    {0x1FFFFFFF, true, 8, "ext 0x1FFFFFFF (max extended id)"},
    {0x000, false, 0, "std 0x000, DLC 0"},
};

static void phaseIdentity(CanPort& src, CanPort& dst, const char* dir) {
  Serial.printf("\n-- identity, %s --\n", dir);
  for (uint8_t p = 0; p < sizeof(PROBES) / sizeof(PROBES[0]); p++) {
    const Probe& pr = PROBES[p];
    uint8_t payload[8];
    for (uint8_t i = 0; i < pr.len; i++) payload[i] = (uint8_t)(0xA0 + i + p);

    src.send(pr.id, pr.ext, payload, pr.len);
    RxFrame got[4];
    const uint32_t n = drain(dst, src, 60, got, 4);

    bool ok = (n == 1);
    if (ok) ok = (got[0].id == pr.id);
    if (ok) ok = (got[0].ext == pr.ext);
    if (ok) ok = (got[0].len == pr.len);
    if (ok) {
      for (uint8_t i = 0; i < pr.len; i++) {
        if (got[0].data[i] != payload[i]) ok = false;
      }
    }
    if (!ok && n >= 1) {
      Serial.printf("       sent id=%lX ext=%d dlc=%u -> got id=%lX ext=%d "
                    "dlc=%u (n=%lu)\n",
                    (unsigned long)pr.id, pr.ext ? 1 : 0, pr.len,
                    (unsigned long)got[0].id, got[0].ext ? 1 : 0, got[0].len,
                    (unsigned long)n);
    } else if (!ok) {
      Serial.printf("       nothing arrived\n");
    }
    check(ok, pr.what);
  }
}

static void phaseDlc(CanPort& src, CanPort& dst, const char* dir) {
  Serial.printf("\n-- DLC 0..8 round trip, %s --\n", dir);
  bool all = true;
  for (uint8_t len = 0; len <= 8; len++) {
    uint8_t payload[8];
    for (uint8_t i = 0; i < len; i++) payload[i] = (uint8_t)(len * 16 + i);
    src.send(0x123, false, payload, len);
    RxFrame got[4];
    const uint32_t n = drain(dst, src, 40, got, 4);
    bool ok = (n == 1 && got[0].len == len);
    for (uint8_t i = 0; ok && i < len; i++) {
      if (got[0].data[i] != payload[i]) ok = false;
    }
    if (!ok) {
      Serial.printf("       DLC %u: n=%lu len=%u\n", len, (unsigned long)n,
                    n ? got[0].len : 0);
      all = false;
    }
  }
  check(all, "all DLCs 0..8 round-trip intact");
}

// --- phase 2: how fast can each direction actually go? --------------------
//
// The numbers to beat, measured on the truck's powertrain bus:
//   charging          864 frames/s
//   generator running 2250 frames/s
// The vehicle segment is TWAI and the charger segment is the MCP2515, so what
// matters most is MCP2515 *transmit*: in the bridge it has to re-emit whatever
// the vehicle side forwards.

static void phaseThroughput(CanPort& src, CanPort& dst, const char* dir,
                            uint32_t fps, uint32_t seconds) {
  const uint32_t want = fps * seconds;
  const uint32_t period_us = 1000000UL / fps;
  uint8_t payload[8] = {0, 1, 2, 3, 4, 5, 6, 7};

  uint32_t sent = 0, rejected = 0, recv = 0;
  uint32_t send_us_total = 0, send_us_max = 0;

  const uint32_t t0 = micros();
  uint32_t next = t0;
  RxFrame f;

  while (sent < want) {
    const uint32_t now = micros();
    if ((int32_t)(now - next) >= 0) {
      payload[0] = (uint8_t)sent;
      payload[1] = (uint8_t)(sent >> 8);
      const uint32_t a = micros();
      const bool ok = src.send(0x18EFC000, true, payload, 8);
      const uint32_t cost = micros() - a;
      send_us_total += cost;
      if (cost > send_us_max) send_us_max = cost;
      if (!ok) rejected++;
      sent++;
      next += period_us;
    }
    const uint32_t t = millis();
    src.service(t);
    dst.service(t);
    while (dst.receive(f)) recv++;
  }

  // Stop the clock BEFORE draining. The drain below is a fixed 300 ms of
  // quiet, and counting it as transmit time divided every rate by 2.3/2.0 and
  // reported a flat 87 % of target on runs that in fact hit target exactly.
  const uint32_t elapsed_ms = (micros() - t0) / 1000;

  // Let the tail drain.
  const uint32_t end = millis() + 300;
  while ((int32_t)(millis() - end) < 0) {
    const uint32_t t = millis();
    src.service(t);
    dst.service(t);
    while (dst.receive(f)) recv++;
  }
  const uint32_t actual_fps = elapsed_ms ? (sent * 1000UL) / elapsed_ms : 0;
  const long lost = (long)sent - (long)rejected - (long)recv;

  Serial.printf(
      "  %-14s target %4lu fps -> actual %4lu | sent %5lu rejected %4lu "
      "recv %5lu lost %4ld | send() avg %lu us max %lu us\n",
      dir, (unsigned long)fps, (unsigned long)actual_fps,
      (unsigned long)sent, (unsigned long)rejected, (unsigned long)recv,
      lost, (unsigned long)(sent ? send_us_total / sent : 0),
      (unsigned long)send_us_max);
}

static void reportPort(CanPort& p) {
  Serial.printf("  %-8s rx=%lu tx=%lu rxDrop=%lu txDrop=%lu err=%lu busOff=%d\n",
                p.name(), (unsigned long)p.rxCount(), (unsigned long)p.txCount(),
                (unsigned long)p.rxDropped(), (unsigned long)p.txDropped(),
                (unsigned long)p.busErrors(), p.busOff() ? 1 : 0);
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n\n=== ESP32-CAN-X2 interposer self-test ===");
  Serial.println("Requires CAN1H<->CAN2H and CAN1L<->CAN2L jumpers.");
  Serial.printf("bitrate %lu\n\n", (unsigned long)BITRATE);

  mcp_spi.begin(MCP_SCK, MCP_MISO, MCP_MOSI, MCP_CS);
  check(can1.begin(BITRATE), "CAN1 (TWAI) came up");
  check(can2.begin(BITRATE), "CAN2 (MCP2515) came up");

  if (checks_failed) {
    Serial.println("\nA port failed to initialise; the rest is meaningless.");
    return;
  }

  phaseIdentity(can1, can2, "CAN1 -> CAN2");
  phaseIdentity(can2, can1, "CAN2 -> CAN1");
  phaseDlc(can1, can2, "CAN1 -> CAN2");
  phaseDlc(can2, can1, "CAN2 -> CAN1");

  Serial.println("\n-- throughput --");
  Serial.println("  (864 fps = charging regime, 2250 fps = generator running)");
  const uint32_t rates[] = {200, 864, 1500, 2250, 3000};
  for (uint8_t i = 0; i < 5; i++) {
    phaseThroughput(can1, can2, "TWAI->MCP", rates[i], 2);
  }
  for (uint8_t i = 0; i < 5; i++) {
    phaseThroughput(can2, can1, "MCP->TWAI", rates[i], 2);
  }

  Serial.println("\n-- port counters --");
  reportPort(can1);
  reportPort(can2);
  Serial.printf("  MCP rings: rx=%u tx=%u (capacity 64 each)\n",
                can2.rxRingUsed(), can2.txRingUsed());
  Serial.printf("  TWAI rx queue peak: %lu (capacity 64)\n",
                (unsigned long)can1.rxQueuePeak());

  Serial.printf("\n=== %u checks, %u failed ===\n", checks_run, checks_failed);
  Serial.println(
      "Throughput rows are informational, not pass/fail -- record them, they "
      "are the numbers the bridge design was sized against.");
}

void loop() {
  delay(1000);
}
