// Stage 0a -- controller and physical-layer diagnostic.
//
// Written 2026-09-10 after the first Stage 0 run failed every cross-port check
// with TWAI err=95812 and the MCP2515 TX ring pinned full. Both of those are
// what you see when nothing ACKs a transmission -- and "the jumper is not
// connected" and "the controller never reached normal mode" produce IDENTICAL
// symptoms. This separates them, then narrows the physical case further.
//
// Phase 1, internal loopback -- needs no wire at all:
//   MCP2515  CANCTRL REQOP=010, TX routed to RX inside the chip, TXCAN idle
//   TWAI     TWAI_MODE_NO_ACK + self-reception request
//
//   loopback PASS, cross-port FAIL  -> drivers are fine, the problem is physical
//   loopback FAIL                   -> the driver is wrong, ignore the wiring
//
// Phase 2, physical layer. Normal mode cannot test one direction at a time on a
// two-node bus: each node needs the other to ACK before either reports success,
// so a single break makes BOTH directions fail identically. These break that
// symmetry -- raw GPIO for the bit level, and a NO_ACK transmitter paired with
// a listen-only receiver for the frame level.
//
//     pio run -d projects/vtrux/tools/interposer/firmware -e diag -t upload

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

struct Probe {
  uint32_t id;
  bool ext;
  uint8_t len;
  const char* what;
};

static const Probe PROBES[] = {
    {0x410, false, 8, "std 0x410"},
    {0x7FF, false, 8, "std 0x7FF (max std id)"},
    {0x18EFC000, true, 8, "ext 0x18EFC000 (command frame)"},
    {0x1FFFFFFF, true, 8, "ext 0x1FFFFFFF (max ext id)"},
    {0x000, false, 0, "std 0x000, DLC 0"},
};

// --- phase 1: internal loopback ------------------------------------------

// Send on a port and receive on the SAME port. No wire involved.
static void phaseSelfLoop(CanPort& p, const char* label) {
  Serial.printf("\n-- internal loopback, %s --\n", label);
  for (uint8_t i = 0; i < sizeof(PROBES) / sizeof(PROBES[0]); i++) {
    const Probe& pr = PROBES[i];
    uint8_t payload[8];
    for (uint8_t k = 0; k < pr.len; k++) payload[k] = (uint8_t)(0x50 + k + i);

    const bool queued = p.send(pr.id, pr.ext, payload, pr.len);

    RxFrame f;
    bool got = false;
    const uint32_t end = millis() + 80;
    while ((int32_t)(millis() - end) < 0 && !got) {
      p.service(millis());
      if (p.receive(f)) got = true;
    }

    bool ok = queued && got;
    if (ok) ok = (f.id == pr.id && f.ext == pr.ext && f.len == pr.len);
    for (uint8_t k = 0; ok && k < pr.len; k++) {
      if (f.data[k] != payload[k]) ok = false;
    }
    if (!ok) {
      Serial.printf("       queued=%d got=%d", queued ? 1 : 0, got ? 1 : 0);
      if (got) {
        Serial.printf(" id=%lX ext=%d dlc=%u", (unsigned long)f.id,
                      f.ext ? 1 : 0, f.len);
      }
      Serial.println();
    }
    check(ok, pr.what);
  }
}

static void dumpMcpRegs(const char* when) {
  // Addresses duplicated from port_mcp2515.h, which keeps them private.
  Serial.printf("  MCP2515 regs (%s):\n", when);
  Serial.printf("    CANSTAT=%02X CANCTRL=%02X  (REQOP=%u, OPMOD=%u)\n",
                can2.reg(0x0E), can2.reg(0x0F), can2.reg(0x0F) >> 5,
                can2.reg(0x0E) >> 5);
  Serial.printf("    CNF1=%02X CNF2=%02X CNF3=%02X  (want 00 F0 86 @500k/16MHz)\n",
                can2.reg(0x2A), can2.reg(0x29), can2.reg(0x28));
  Serial.printf("    TEC=%u REC=%u EFLG=%02X CANINTF=%02X\n", can2.reg(0x1C),
                can2.reg(0x1D), can2.reg(0x2D), can2.reg(0x2C));
  Serial.printf("    TXB0CTRL=%02X RXB0CTRL=%02X RXB1CTRL=%02X\n",
                can2.reg(0x30), can2.reg(0x60), can2.reg(0x70));
}

// --- phase 2: the physical layer -----------------------------------------

// Transceiver 1, by hand. With the TWAI driver uninstalled, GPIO7 is just an
// output into the MCP2562 TXD pin and GPIO6 just an input from its RXD pin. A
// CAN transceiver keeps its receiver live and sees the bus it is itself
// driving, so pulling TXD low must show up on RXD. If it does not, that
// transceiver is dead, unpowered, or parked in standby -- and no amount of
// rewiring fixes it.
static void phaseTransceiverEcho() {
  Serial.println("\n-- transceiver 1 (CAN1) local echo, raw GPIO --");
  pinMode(CAN1_TX_GPIO, OUTPUT);
  pinMode(CAN1_RX_GPIO, INPUT);

  digitalWrite(CAN1_TX_GPIO, HIGH);  // recessive
  delayMicroseconds(200);
  const int rec = digitalRead(CAN1_RX_GPIO);

  digitalWrite(CAN1_TX_GPIO, LOW);  // dominant
  delayMicroseconds(200);
  const int dom = digitalRead(CAN1_RX_GPIO);

  digitalWrite(CAN1_TX_GPIO, HIGH);  // leave the bus recessive
  delayMicroseconds(200);

  Serial.printf("     TXD recessive -> RXD %d (want 1)\n", rec);
  Serial.printf("     TXD dominant  -> RXD %d (want 0)\n", dom);
  check(rec == HIGH && dom == LOW, "CAN1 transceiver drives and echoes the bus");
}

// Does anything the MCP2515 transmits physically reach the CAN1 receiver? The
// MCP2515 is in normal mode with nothing to ACK it, so it retries the same
// frame forever -- which is exactly the continuous carrier this test wants.
// CAN1 TXD is held recessive so it contributes nothing of its own.
static void phaseBusActivityFromCan2() {
  Serial.println("\n-- bus activity from CAN2, sampled on CAN1 RXD --");
  digitalWrite(CAN1_TX_GPIO, HIGH);
  pinMode(CAN1_RX_GPIO, INPUT);

  uint8_t payload[8] = {0xDE, 0xAD, 0xBE, 0xEF, 0, 0, 0, 0};
  for (uint8_t i = 0; i < 8; i++) can2.send(0x123, false, payload, 8);
  can2.service(millis());

  uint32_t samples = 0, lows = 0, transitions = 0;
  int prev = digitalRead(CAN1_RX_GPIO);
  const uint32_t end = millis() + 50;
  while ((int32_t)(millis() - end) < 0) {
    const int v = digitalRead(CAN1_RX_GPIO);
    if (v != prev) {
      transitions++;
      prev = v;
    }
    if (v == LOW) lows++;
    samples++;
  }
  Serial.printf("     %lu samples over 50 ms: %lu low, %lu transitions\n",
                (unsigned long)samples, (unsigned long)lows,
                (unsigned long)transitions);
  check(transitions > 0,
        "CAN2 transmissions reach CAN1 (wire + both transceivers)");
}

// Frame level, one direction only: CAN1 transmits in NO_ACK (so a missing ACK
// is not an error and it neither retries nor emits an error frame) while CAN2
// listens without ACKing. If the bits get through, real frames are received.
static void phaseOneWay() {
  Serial.println("\n-- one-way frames: CAN1 (NO_ACK) -> CAN2 (listen-only) --");
  uint8_t payload[8] = {1, 2, 3, 4, 5, 6, 7, 8};
  uint32_t got = 0;
  for (uint8_t i = 0; i < 10; i++) {
    can1.send(0x321, false, payload, 8);
    const uint32_t end = millis() + 20;
    RxFrame f;
    while ((int32_t)(millis() - end) < 0) {
      can1.service(millis());
      can2.service(millis());
      while (can2.receive(f)) got++;
    }
  }
  Serial.printf("     sent 10, CAN2 received %lu\n", (unsigned long)got);
  check(got > 0, "CAN1 -> CAN2 carries frames");
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n\n=== ESP32-CAN-X2 diagnostic (Stage 0a) ===");
  Serial.printf("bitrate %lu\n", (unsigned long)BITRATE);

  mcp_spi.begin(MCP_SCK, MCP_MISO, MCP_MOSI, MCP_CS);

  // --- phase 1 ---
  Serial.println("\n### phase 1: drivers, internal loopback, no wire ###");
  can1.setSelfTest(true);
  can2.setLoopback(true);
  check(can1.begin(BITRATE), "CAN1 (TWAI) began in NO_ACK self-test mode");
  check(can2.begin(BITRATE), "CAN2 (MCP2515) began in loopback mode");
  dumpMcpRegs("after begin, expect OPMOD=2 for loopback");
  phaseSelfLoop(can1, "TWAI (self-reception)");
  phaseSelfLoop(can2, "MCP2515 (internal loopback)");
  const uint16_t phase1_failed = checks_failed;

  // --- phase 2 needs different modes, so tear both ports down first ---
  Serial.println("\n### phase 2: physical layer ###");
  can1.end();
  can2.end();

  can2.setLoopback(false);
  can2.setListenOnly(false);
  check(can2.begin(BITRATE), "CAN2 re-began in normal mode");

  phaseTransceiverEcho();
  phaseBusActivityFromCan2();

  can2.end();
  can2.setListenOnly(true);
  check(can2.begin(BITRATE), "CAN2 re-began in listen-only mode");
  can1.setSelfTest(true);
  can1.setListenOnly(false);
  check(can1.begin(BITRATE), "CAN1 re-began in NO_ACK mode");
  phaseOneWay();

  dumpMcpRegs("final");
  Serial.printf("  TWAI TEC=%lu REC=%lu busOff=%d\n",
                (unsigned long)can1.txErrCounter(),
                (unsigned long)can1.rxErrCounter(), can1.busOff() ? 1 : 0);

  Serial.printf("\n=== %u checks, %u failed ===\n", checks_run, checks_failed);
  if (phase1_failed) {
    Serial.println("A DRIVER is at fault. Fix that before touching wiring.");
  } else if (checks_failed) {
    Serial.println("Drivers are sound; the fault is PHYSICAL. Read phase 2:");
    Serial.println("  echo FAIL            -> CAN1 transceiver dead or in standby");
    Serial.println("  echo PASS, rest FAIL -> break between CAN2 and CAN1");
  } else {
    Serial.println("Drivers and both directions of the wire are good.");
  }
}

void loop() {
  delay(1000);
}
