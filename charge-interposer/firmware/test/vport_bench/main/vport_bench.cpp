// vport_bench: what the X2's vehicle port can put on the vehicle segment by
// itself (spec 9, user 2026-10-12 / 2026-10-13).
//
// A minimal standalone ESP-IDF image -- no Arduino, no PlatformIO, no
// product code -- that drives the S3's built-in TWAI controller, the
// vehicle port, and nothing else. It NEVER goes on the truck. The host
// (SeaDrive bench_vport.py) commands it over the USB Serial/JTAG console
// and judges the wire through its own adapters; the image only reports
// what it asked the controller to do and what the controller says.
//
// Commands, one per line; every reply is one line starting with '@' and
// ends with the port's current `mode=<normal|listen> bitrate=<...>`:
//
//   HELLO?                 @HELLO vport_bench version=.. elf_sha256=..
//   MODE normal|listen     reinstall the driver in that mode
//   BITRATE 125k|250k|500k|1M   reinstall the driver at that bit rate
//   SEND <hexid> [n]       n one-shot frames (default 1); extended when the
//                          id is above 0x7FF; 8 bytes, a 32-bit counter LE
//                          in bytes 0-3 and the id in bytes 4-7
//   FLOOD <hexid>          back-to-back frames on that id until STOP
//   STOP                   end a FLOOD; reports how many were queued
//   STALL <n>              a task queues n frames (n <= TX_QUEUE) and then
//                          blocks forever; the controller keeps running, so
//                          the wire shows whether anything beyond those n
//                          goes out
//   DOMINANT <us> [reps]   the MCP2562's permanent-dominant timeout, timed by
//                          the board itself (spec 9 at 731e9c15): the driver
//                          stopped, TWAI TX detached in the GPIO matrix, TXD
//                          driven low for at most <us> (100..5000), RXD
//                          polled for the transceiver releasing the bus
//                          while TXD is still low; one @DOM line per pulse
//                          (reps 1..50) and an @DOMINANT summary with min /
//                          median / max. A pulse shorter than the timeout
//                          must report by=txd (the can-fail)
//   STATUS                 twai_get_status_info, plus this image's counters,
//                          plus rec_reg= / tec_reg=: the controller's error
//                          counter registers read directly (twai_ll_get_rec /
//                          twai_ll_get_tec, masked to the 8-bit counter; the
//                          raw 32-bit words beside them as rec_raw= /
//                          tec_raw=, so the reserved bits are shown to read
//                          0, not assumed). The API's rec= / tec= are 0 in
//                          listen-only by construction (IDF v5.4.1 twai.c,
//                          twai_get_status_info_v2), not a register reading
//   RESET                  stop, uninstall, reinstall in the current mode
//   AUTORECOVER on|off     bus-off -> recovery -> start again, by itself
//                          (default on, as the product does, B-7c, so the
//                          wrong-bit-rate bursts can repeat)
//
// POWER-UP STATE: LISTEN-ONLY at 500 kbit/s. The image puts nothing on the
// bus until the host asks for normal mode, so flashing it onto a bench that
// is running cannot disturb the segment before a measurement starts. In
// listen-only the controller sends no frame, ACK or error flag, and the
// driver refuses twai_transmit (ESP_ERR_NOT_SUPPORTED); SEND / FLOOD /
// STALL then report that refusal rather than pretending.
//
// The X2's vehicle port: TWAI TX GPIO7, RX GPIO6, MCP2562 transceiver whose
// STBY pin has no GPIO (bring-up notes, "The board"). The MCP2515 / SPI is
// never started, so the charger segment sees nothing from this image.
//
// Tester, 2026-10-13. firmware/test/vport_bench/README.md has the build.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/twai.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_app_desc.h"
#include "esp_idf_version.h"
#include "esp_rom_gpio.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hal/twai_ll.h"
#include "soc/gpio_sig_map.h"
#include "soc/twai_struct.h"

namespace {

const gpio_num_t PIN_TX = GPIO_NUM_7;
const gpio_num_t PIN_RX = GPIO_NUM_6;
const uint32_t TX_QUEUE = 64;
const uint32_t RX_QUEUE = 64;

struct Rate {
  const char* name;
  twai_timing_config_t t;
};
const Rate RATES[] = {
    {"125k", TWAI_TIMING_CONFIG_125KBITS()},
    {"250k", TWAI_TIMING_CONFIG_250KBITS()},
    {"500k", TWAI_TIMING_CONFIG_500KBITS()},
    {"1M", TWAI_TIMING_CONFIG_1MBITS()},
};
const int N_RATES = sizeof(RATES) / sizeof(RATES[0]);

// Port state. `drv` serialises every driver install / uninstall / start /
// stop against the background tasks.
SemaphoreHandle_t drv;
bool listen_only = true;
int rate_idx = 2;  // 500k
bool installed = false;
bool autorecover = true;

// Counters this image keeps (the controller's own are in STATUS).
volatile uint32_t rx_frames = 0;
volatile uint32_t recoveries = 0;
volatile uint32_t tx_counter = 0;  // the counter carried in byte 0-3

// FLOOD
volatile bool flood_run = false;
volatile bool flood_done = true;
volatile uint32_t flood_id = 0;
volatile uint32_t flood_queued = 0;
volatile uint32_t flood_refused = 0;
volatile esp_err_t flood_last_err = ESP_OK;

char line_buf[256];

const char* modeName() { return listen_only ? "listen" : "normal"; }

// Every reply ends with the port's mode and bit rate.
void reply(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void reply(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vprintf(fmt, ap);
  va_end(ap);
  printf(" mode=%s bitrate=%s\n", modeName(), RATES[rate_idx].name);
  fflush(stdout);
}

const char* readLine() {
  for (;;) {
    if (!fgets(line_buf, sizeof(line_buf), stdin)) {
      vTaskDelay(1);
      continue;
    }
    size_t n = strlen(line_buf);
    while (n && (line_buf[n - 1] == '\n' || line_buf[n - 1] == '\r'))
      line_buf[--n] = 0;
    if (n) return line_buf;
  }
}

// Caller holds `drv`.
esp_err_t installLocked() {
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
      PIN_TX, PIN_RX, listen_only ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
  g.tx_queue_len = TX_QUEUE;
  g.rx_queue_len = RX_QUEUE;
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  esp_err_t e = twai_driver_install(&g, &RATES[rate_idx].t, &f);
  if (e != ESP_OK) return e;
  e = twai_start();
  if (e != ESP_OK) {
    twai_driver_uninstall();
    return e;
  }
  installed = true;
  return ESP_OK;
}

// Caller holds `drv`.
void uninstallLocked() {
  if (!installed) return;
  twai_stop();  // fails harmlessly when bus-off; uninstall still works
  twai_driver_uninstall();
  installed = false;
}

void stopFlood() {
  flood_run = false;
  while (!flood_done) vTaskDelay(1);
}

esp_err_t reinstall() {
  stopFlood();
  xSemaphoreTake(drv, portMAX_DELAY);
  uninstallLocked();
  esp_err_t e = installLocked();
  xSemaphoreGive(drv);
  return e;
}

void fillFrame(twai_message_t* m, uint32_t id) {
  memset(m, 0, sizeof(*m));
  m->identifier = id;
  m->extd = id > 0x7FF ? 1 : 0;
  m->data_length_code = 8;
  const uint32_t c = tx_counter;
  tx_counter = c + 1;
  for (int i = 0; i < 4; i++) m->data[i] = (uint8_t)(c >> (8 * i));
  for (int i = 0; i < 4; i++) m->data[4 + i] = (uint8_t)(id >> (8 * i));
}

// Drains the RX queue so it never fills, and counts what arrived. It
// holds `drv` while it reads, with no wait, so a reinstall can never
// delete the queue under a blocked receive; 1 ms between drains is under
// a tenth of the 64-frame queue at 500 kbit/s.
void rxTask(void*) {
  twai_message_t m;
  for (;;) {
    xSemaphoreTake(drv, portMAX_DELAY);
    if (installed) {
      for (int i = 0; i < (int)RX_QUEUE; i++) {
        if (twai_receive(&m, 0) != ESP_OK) break;
        rx_frames = rx_frames + 1;
      }
    }
    xSemaphoreGive(drv);
    vTaskDelay(1);
  }
}

// Bus-off -> recovery -> start, as the product does (B-7c), when enabled.
void monitorTask(void*) {
  for (;;) {
    vTaskDelay(5);
    if (!autorecover || !installed) continue;
    xSemaphoreTake(drv, portMAX_DELAY);
    twai_status_info_t s;
    if (installed && twai_get_status_info(&s) == ESP_OK) {
      if (s.state == TWAI_STATE_BUS_OFF) {
        twai_initiate_recovery();
      } else if (s.state == TWAI_STATE_STOPPED) {
        if (twai_start() == ESP_OK) recoveries = recoveries + 1;
      }
    }
    xSemaphoreGive(drv);
  }
}

void floodTask(void*) {
  twai_message_t m;
  while (flood_run) {
    fillFrame(&m, flood_id);
    const esp_err_t e = twai_transmit(&m, 10);
    if (e == ESP_OK) {
      flood_queued = flood_queued + 1;
    } else {
      flood_refused = flood_refused + 1;
      flood_last_err = e;
      if (e == ESP_ERR_NOT_SUPPORTED || e == ESP_ERR_INVALID_STATE)
        vTaskDelay(10);  // listen-only / bus-off: do not spin
    }
  }
  flood_done = true;
  vTaskDelete(nullptr);
}

struct StallArgs {
  uint32_t id;
  uint32_t n;
  volatile uint32_t queued;
  volatile uint32_t refused;
  volatile esp_err_t last_err;
  volatile bool ready;
};
StallArgs stall;

// Queues n frames, says so, then blocks forever. The controller keeps
// running and the TX queue keeps draining onto the wire; nothing in this
// task ever queues another frame.
void stallTask(void*) {
  twai_message_t m;
  for (uint32_t i = 0; i < stall.n; i++) {
    fillFrame(&m, stall.id);
    const esp_err_t e = twai_transmit(&m, 0);
    if (e == ESP_OK) stall.queued = stall.queued + 1;
    else {
      stall.refused = stall.refused + 1;
      stall.last_err = e;
    }
  }
  stall.ready = true;
  vTaskSuspend(nullptr);  // never resumed
}

void hello() {
  const esp_app_desc_t* d = esp_app_get_description();
  char sha[65];
  for (int i = 0; i < 32; i++) sprintf(sha + 2 * i, "%02x",
                                       d->app_elf_sha256[i]);
  reply("@HELLO vport_bench version=%s elf_sha256=%s idf=%s tx_gpio=%d "
        "rx_gpio=%d",
        d->version, sha, esp_get_idf_version(), (int)PIN_TX, (int)PIN_RX);
}

const char* stateName(twai_state_t s) {
  switch (s) {
    case TWAI_STATE_STOPPED: return "stopped";
    case TWAI_STATE_RUNNING: return "running";
    case TWAI_STATE_BUS_OFF: return "bus_off";
    case TWAI_STATE_RECOVERING: return "recovering";
    default: return "unknown";
  }
}

bool parseHex(const char* s, uint32_t* out) {
  char* end = nullptr;
  const unsigned long v = strtoul(s, &end, 16);
  if (end == s || (*end && *end != ' ') || v > 0x1FFFFFFFUL) return false;
  *out = (uint32_t)v;
  return true;
}

void cmdSend(const char* args) {
  uint32_t id = 0;
  if (!parseHex(args, &id)) {
    reply("@ERR SEND needs <hexid> [n]");
    return;
  }
  const char* sp = strchr(args, ' ');
  long n = sp ? strtol(sp + 1, nullptr, 10) : 1;
  if (n < 1 || n > 100000) {
    reply("@ERR SEND n must be 1..100000");
    return;
  }
  uint32_t queued = 0, refused = 0;
  esp_err_t last = ESP_OK;
  twai_message_t m;
  for (long i = 0; i < n; i++) {
    fillFrame(&m, id);
    const esp_err_t e = twai_transmit(&m, 100);
    if (e == ESP_OK) queued++;
    else {
      refused++;
      last = e;
      if (e == ESP_ERR_NOT_SUPPORTED || e == ESP_ERR_INVALID_STATE) break;
    }
  }
  reply("@SENT id=%lX ext=%d n=%ld queued=%lu refused=%lu last_err=%s",
        (unsigned long)id, id > 0x7FF ? 1 : 0, n, (unsigned long)queued,
        (unsigned long)refused, esp_err_to_name(last));
}

void cmdFlood(const char* args) {
  uint32_t id = 0;
  if (!parseHex(args, &id)) {
    reply("@ERR FLOOD needs <hexid>");
    return;
  }
  stopFlood();
  flood_id = id;
  flood_queued = 0;
  flood_refused = 0;
  flood_last_err = ESP_OK;
  flood_done = false;
  flood_run = true;
  xTaskCreate(floodTask, "flood", 4096, nullptr, 5, nullptr);
  reply("@FLOODING id=%lX ext=%d", (unsigned long)id, id > 0x7FF ? 1 : 0);
}

void cmdStall(const char* args) {
  const long n = strtol(args, nullptr, 10);
  if (n < 1 || n > (long)TX_QUEUE) {
    reply("@ERR STALL n must be 1..%lu (the TX queue)",
          (unsigned long)TX_QUEUE);
    return;
  }
  stopFlood();
  memset((void*)&stall, 0, sizeof(stall));
  stall.id = 0x7F0;
  stall.n = (uint32_t)n;
  xTaskCreate(stallTask, "stall", 4096, nullptr, 5, nullptr);
  while (!stall.ready) vTaskDelay(1);
  reply("@STALLED id=%lX n=%lu queued=%lu refused=%lu last_err=%s "
        "task=blocked",
        (unsigned long)stall.id, (unsigned long)stall.n,
        (unsigned long)stall.queued, (unsigned long)stall.refused,
        esp_err_to_name(stall.last_err));
}

// One TXD-low pulse, timed by the board itself (spec 9 at 731e9c15, user
// 2026-10-13: "from its transmit line held low to its receive line going
// recessive"). TXD is held low for at most `pulse_us`; RXD is polled in a
// critical section (no preemption, so the loop's own jitter is well under
// the microsecond the timer resolves) for, in order: RXD going low (the
// transceiver drove the bus), then RXD going high again. If RXD goes high
// while TXD is still low, the transceiver released the bus by itself
// (by=transceiver, rxd_high_us = the timeout). If TXD is released first,
// RXD is followed for up to 1 ms more (by=txd: the capture tracks the line
// rather than reporting a constant -- the can-fail run uses a pulse shorter
// than the timeout). Otherwise by=none.
struct DomResult {
  int64_t rxd_low_us;      // -1: RXD never went low
  int64_t rxd_high_us;     // -1: never high again inside the window
  int64_t released_us;     // when this image let TXD go
  const char* by;
};

DomResult dominantPulse(int64_t pulse_us) {
  static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
  DomResult r = {-1, -1, -1, "none"};
  portENTER_CRITICAL(&mux);
  gpio_set_level(PIN_TX, 0);  // TXD low = dominant
  const int64_t t0 = esp_timer_get_time();
  bool txd_low = true;
  for (;;) {
    const int64_t t = esp_timer_get_time() - t0;
    const int rxd = gpio_get_level(PIN_RX);
    if (r.rxd_low_us < 0) {
      if (rxd == 0) r.rxd_low_us = t;
    } else if (rxd == 1) {
      r.rxd_high_us = t;
      r.by = txd_low ? "transceiver" : "txd";
      break;
    }
    if (txd_low && t >= pulse_us) {
      gpio_set_level(PIN_TX, 1);  // recessive
      txd_low = false;
      r.released_us = t;
    }
    if (!txd_low && t >= r.released_us + 1000) break;
  }
  if (txd_low) {
    gpio_set_level(PIN_TX, 1);
    r.released_us = esp_timer_get_time() - t0;
  }
  portEXIT_CRITICAL(&mux);
  return r;
}

void cmdDominant(const char* args) {
  char* end = nullptr;
  const long pulse_us = strtol(args, &end, 10);
  long reps = end && *end ? strtol(end, nullptr, 10) : 1;
  if (pulse_us < 100 || pulse_us > 5000 || reps < 1 || reps > 50) {
    reply("@ERR DOMINANT <pulse_us 100..5000> [reps 1..50]");
    return;
  }
  stopFlood();
  xSemaphoreTake(drv, portMAX_DELAY);
  const bool was = installed;
  uninstallLocked();
  gpio_reset_pin(PIN_RX);
  gpio_set_direction(PIN_RX, GPIO_MODE_INPUT);
  gpio_reset_pin(PIN_TX);
  gpio_set_direction(PIN_TX, GPIO_MODE_OUTPUT);
  esp_rom_gpio_connect_out_signal(PIN_TX, SIG_GPIO_OUT_IDX, false, false);
  gpio_set_level(PIN_TX, 1);  // recessive before the first pulse
  vTaskDelay(pdMS_TO_TICKS(20));
  int64_t by_xcvr[50];
  int n_xcvr = 0, n_txd = 0, n_none = 0, n_never_low = 0;
  for (long i = 0; i < reps; i++) {
    const DomResult r = dominantPulse(pulse_us);
    if (r.rxd_low_us < 0) n_never_low++;
    if (strcmp(r.by, "transceiver") == 0) by_xcvr[n_xcvr++] = r.rxd_high_us;
    else if (strcmp(r.by, "txd") == 0) n_txd++;
    else n_none++;
    reply("@DOM i=%ld pulse_us=%ld rxd_low_us=%lld rxd_high_us=%lld "
          "released_us=%lld by=%s", i, pulse_us, (long long)r.rxd_low_us,
          (long long)r.rxd_high_us, (long long)r.released_us, r.by);
    vTaskDelay(pdMS_TO_TICKS(100));  // the other nodes recover between
  }
  // installLocked() routes TWAI_TX/RX back onto the pins through the driver
  esp_err_t e = was ? installLocked() : ESP_OK;
  xSemaphoreGive(drv);
  for (int a = 1; a < n_xcvr; a++)        // insertion sort, n <= 50
    for (int b = a; b > 0 && by_xcvr[b - 1] > by_xcvr[b]; b--) {
      const int64_t t = by_xcvr[b];
      by_xcvr[b] = by_xcvr[b - 1];
      by_xcvr[b - 1] = t;
    }
  if (n_xcvr) {
    reply("@DOMINANT pulse_us=%ld reps=%ld transceiver=%d min_us=%lld "
          "median_us=%lld max_us=%lld followed_txd=%d none=%d "
          "rxd_never_low=%d driver=%s", pulse_us, reps, n_xcvr,
          (long long)by_xcvr[0], (long long)by_xcvr[n_xcvr / 2],
          (long long)by_xcvr[n_xcvr - 1], n_txd, n_none, n_never_low,
          esp_err_to_name(e));
  } else {
    reply("@DOMINANT pulse_us=%ld reps=%ld transceiver=0 followed_txd=%d "
          "none=%d rxd_never_low=%d driver=%s", pulse_us, reps, n_txd, n_none,
          n_never_low, esp_err_to_name(e));
  }
}

void cmdStatus() {
  twai_status_info_t s = {};
  uint32_t rec_raw = 0, tec_raw = 0;
  xSemaphoreTake(drv, portMAX_DELAY);
  const esp_err_t e = installed ? twai_get_status_info(&s)
                                : ESP_ERR_INVALID_STATE;
  if (installed) {  // the peripheral is clocked only while installed
    rec_raw = twai_ll_get_rec(&TWAI);  // the whole word; bits 8-31 reserved
    tec_raw = twai_ll_get_tec(&TWAI);
  }
  xSemaphoreGive(drv);
  if (e != ESP_OK) {
    reply("@STATUS installed=0 err=%s rx_frames=%lu recoveries=%lu",
          esp_err_to_name(e), (unsigned long)rx_frames,
          (unsigned long)recoveries);
    return;
  }
  reply("@STATUS installed=1 state=%s tec=%lu rec=%lu tec_reg=%lu "
        "rec_reg=%lu tec_raw=0x%08lx rec_raw=0x%08lx tx_failed=%lu "
        "bus_err=%lu arb_lost=%lu msgs_to_tx=%lu msgs_to_rx=%lu "
        "rx_missed=%lu rx_overrun=%lu rx_frames=%lu recoveries=%lu "
        "autorecover=%d flood=%d flood_queued=%lu flood_refused=%lu",
        stateName(s.state), (unsigned long)s.tx_error_counter,
        (unsigned long)s.rx_error_counter, (unsigned long)(tec_raw & 0xFF),
        (unsigned long)(rec_raw & 0xFF), (unsigned long)tec_raw,
        (unsigned long)rec_raw, (unsigned long)s.tx_failed_count,
        (unsigned long)s.bus_error_count, (unsigned long)s.arb_lost_count,
        (unsigned long)s.msgs_to_tx, (unsigned long)s.msgs_to_rx,
        (unsigned long)s.rx_missed_count, (unsigned long)s.rx_overrun_count,
        (unsigned long)rx_frames, (unsigned long)recoveries,
        autorecover ? 1 : 0, flood_run ? 1 : 0,
        (unsigned long)flood_queued, (unsigned long)flood_refused);
}

}  // namespace

extern "C" void app_main(void) {
  usb_serial_jtag_driver_config_t cfg =
      USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
  usb_serial_jtag_driver_install(&cfg);
  usb_serial_jtag_vfs_use_driver();
  setvbuf(stdin, nullptr, _IONBF, 0);

  drv = xSemaphoreCreateMutex();
  xSemaphoreTake(drv, portMAX_DELAY);
  const esp_err_t e0 = installLocked();  // listen-only, 500k
  xSemaphoreGive(drv);
  xTaskCreate(rxTask, "rx", 4096, nullptr, 6, nullptr);
  xTaskCreate(monitorTask, "mon", 4096, nullptr, 7, nullptr);
  hello();
  reply("@READY twai=%s", esp_err_to_name(e0));

  for (;;) {
    const char* cmd = readLine();
    if (strcmp(cmd, "HELLO?") == 0) {
      hello();
    } else if (strncmp(cmd, "MODE ", 5) == 0) {
      const char* a = cmd + 5;
      if (strcmp(a, "normal") != 0 && strcmp(a, "listen") != 0) {
        reply("@ERR MODE normal|listen");
        continue;
      }
      listen_only = strcmp(a, "listen") == 0;
      const esp_err_t e = reinstall();
      reply("@MODE driver=%s", esp_err_to_name(e));
    } else if (strncmp(cmd, "BITRATE ", 8) == 0) {
      int k = -1;
      for (int i = 0; i < N_RATES; i++)
        if (strcmp(cmd + 8, RATES[i].name) == 0) k = i;
      if (k < 0) {
        reply("@ERR BITRATE 125k|250k|500k|1M");
        continue;
      }
      rate_idx = k;
      const esp_err_t e = reinstall();
      reply("@BITRATE driver=%s", esp_err_to_name(e));
    } else if (strncmp(cmd, "SEND ", 5) == 0) {
      cmdSend(cmd + 5);
    } else if (strncmp(cmd, "FLOOD ", 6) == 0) {
      cmdFlood(cmd + 6);
    } else if (strcmp(cmd, "STOP") == 0) {
      stopFlood();
      reply("@STOPPED id=%lX queued=%lu refused=%lu last_err=%s",
            (unsigned long)flood_id, (unsigned long)flood_queued,
            (unsigned long)flood_refused, esp_err_to_name(flood_last_err));
    } else if (strncmp(cmd, "STALL ", 6) == 0) {
      cmdStall(cmd + 6);
    } else if (strncmp(cmd, "DOMINANT ", 9) == 0) {
      cmdDominant(cmd + 9);
    } else if (strcmp(cmd, "STATUS") == 0) {
      cmdStatus();
    } else if (strcmp(cmd, "RESET") == 0) {
      const esp_err_t e = reinstall();
      reply("@RESET driver=%s", esp_err_to_name(e));
    } else if (strncmp(cmd, "AUTORECOVER ", 12) == 0) {
      const char* a = cmd + 12;
      if (strcmp(a, "on") != 0 && strcmp(a, "off") != 0) {
        reply("@ERR AUTORECOVER on|off");
        continue;
      }
      autorecover = strcmp(a, "on") == 0;
      reply("@AUTORECOVER %s", a);
    } else {
      reply("@ERR unknown command: %s", cmd);
    }
  }
}
