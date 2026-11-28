// s3probe: the T19 measurement image (shared/twai_port SPEC.md T19; the
// charge-interposer reviewer accepted the variant, 2026-11-27). A test image,
// never released: it runs no bridge and owns neither bus, and its serial
// commands drive shared/twai_port's T19 app (test_app/README.md, "The T19
// app on the S3: serial commands") on board port 0, the S3's TWAI. The
// charger port, the MCP2515, is never set up.
//
// The top CMakeLists.txt sets TWAI_PORT_TXABORT_PROBE and TWAI_PORT_S3_PROBE
// for this variant only. shared/restart_guard is in every variant; the T12
// commands read its record (G4) through its own calls. Only this variant's
// switch makes the S3 stop (twai_port T12) act; elsewhere it answers "not
// supported" until T19's measurement passes.
//
// Every command runs here, on the Arduino loop task, so the TWAI driver is
// installed, and its interrupt runs, on that task's core; each answer names
// the core.
#include <Arduino.h>

#include <stdio.h>

#include "board_ports.h"
#include "restart_guard.h"
#include "twai_port_t19.h"

namespace {

// The answers can be long (switchloop's), and the host reads them all, so a
// ring big enough for most lines and a write that waits for the host.
constexpr size_t S3P_CDC_TX_RING_BYTES = 8192;
constexpr uint32_t S3P_CDC_TX_TIMEOUT_MS = 2000;
constexpr size_t S3P_LINE_MAX = 255;

char s_line[S3P_LINE_MAX + 1];
size_t s_len = 0;
bool s_too_long = false;

bool emit(void *ctx, const char *s, size_t n) {
  (void)ctx;
  return Serial.write(reinterpret_cast<const uint8_t *>(s), n) == n;
}

void flush() { Serial.flush(); }

// G4's record as one JSON value (restart_guard.h), or null.
bool restartRecord(char *buf, size_t n) {
  if (!restart_guard_record_valid()) {
    return snprintf(buf, n, "null") < static_cast<int>(n);
  }
  restart_guard_record_t r;
  restart_guard_record_get(&r);
  const int k = snprintf(
      buf, n,
      "{\"entries\":%lu,\"twai_clk\":%lu,\"twai_rst\":%lu,\"in_reset\":%lu,"
      "\"st_entry\":%lu,\"tec\":%lu,\"rec\":%lu,\"did_twai\":%lu,"
      "\"st_reset\":%lu,\"wait_us\":%lu,\"timed_out\":%lu,\"did_stdby\":%lu,"
      "\"aborts\":%lu}",
      (unsigned long)r.entries, (unsigned long)r.twai_clk,
      (unsigned long)r.twai_rst, (unsigned long)r.in_reset,
      (unsigned long)r.st_entry, (unsigned long)r.tec, (unsigned long)r.rec,
      (unsigned long)r.did_twai, (unsigned long)r.st_reset,
      (unsigned long)r.wait_us, (unsigned long)r.timed_out,
      (unsigned long)r.did_stdby, (unsigned long)r.aborts);
  return k > 0 && k < static_cast<int>(n);
}

}  // namespace

void setup() {
  Serial.setTxBufferSize(S3P_CDC_TX_RING_BYTES);
  Serial.begin(115200);
  Serial.setTxTimeoutMs(S3P_CDC_TX_TIMEOUT_MS);

  // A power-on leaves no valid record; the first restart then reads 1.
  if (!restart_guard_record_valid()) {
    restart_guard_record_init();
  }

  twai_port_t19_cfg_t c = {};
  c.board_port = interposer::BOARD_PORT_VEHICLE;
  c.emit = emit;
  c.emit_ctx = nullptr;
  c.flush = flush;
  c.restart_record = restartRecord;
  const esp_err_t e = twai_port_t19_init(&c);
  Serial.printf("{\"s3probe\":\"MEASUREMENT BUILD, never for the truck\","
                "\"init\":%d}\n", static_cast<int>(e));
}

void loop() {
  // One line at a time; CR and LF both end it. A line over the limit is
  // read to its end and refused whole, never run cut short.
  while (Serial.available() > 0) {
    const int ch = Serial.read();
    if (ch < 0) break;
    if (ch == '\r' || ch == '\n') {
      if (s_len > 0 || s_too_long) {
        s_line[s_len] = '\0';
        twai_port_t19_line(s_line, s_too_long);
      }
      s_len = 0;
      s_too_long = false;
    } else if (s_len < S3P_LINE_MAX) {
      s_line[s_len++] = static_cast<char>(ch);
    } else {
      s_too_long = true;
    }
  }
  delay(1);
}
