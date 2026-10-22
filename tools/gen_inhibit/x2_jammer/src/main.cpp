// Vtrux X2 JAMMER -- TEST EQUIPMENT, NOT FOR VEHICLE.
//
// Gen-inhibit tester, 2026-10-08. Runs on an Autosport Labs ESP32-CAN-X2 and
// puts short dominant pulses on CAN1 by driving the MCP2562 TXD (GPIO7) from
// the RMT peripheral. The TWAI controller is never started and the MCP2515
// (CAN2) is reset into configuration mode at boot, so this image never sends a
// CAN frame and never ACKs one. It only corrupts frames, on command.
//
// Safety design (README.md has the reasoning):
//  - Boots silent: GPIO7 is driven HIGH (recessive) before anything else.
//    Before that, through ROM and bootloader, GPIO7 is an input and the
//    MCP2562's internal TXD pull-up holds the bus recessive.
//  - Jams only on an explicit command, and every command carries a duration,
//    capped at MS_MAX. Nothing extends a deadline: there is no keepalive.
//  - Pulses only, never a held dominant line: W is 12..50 us and the period is
//    at least 4 W. The RMT channel output is inverted, so its idle and
//    end-of-transmission level (internal 0) is recessive on the pin.
//  - Two independent stops at the deadline:
//      (a) a one-shot esp_timer disables the RMT channel;
//      (b) the RMT only ever runs finite chunks (<= CHUNK_US), each sized not
//          to cross the deadline, and the jam task re-arms a chunk only while
//          now < deadline. If the task hangs, pulses stop within one chunk.
//
// Commands, one per line on the USB-Serial-JTAG console (every reply line
// starts with "JAMMER "):
//   TRAIN <w_us> <p_us> <ms>        periodic pulses for ms milliseconds
//   PULSES <w_us> <n> <interval_ms> n single pulses, interval_ms apart
//   STOP                            stop now
//   STATUS / ID                     state line / banner
#include <Arduino.h>
#include <SPI.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/rmt_encoder.h"
#include "driver/rmt_tx.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#ifndef JAMMER_SRC_DIGEST
#define JAMMER_SRC_DIGEST "unknown"
#endif

static const gpio_num_t TX_PIN = GPIO_NUM_7;  // CAN1 (TWAI) TXD on the X2
static const int MCP_CS = 10, MCP_SCK = 12, MCP_MISO = 13, MCP_MOSI = 11;

static const uint32_t W_MIN_US = 12;       // > 5 bit times at 500 kbit/s
static const uint32_t W_MAX_US = 50;       // far below any TXD dominant timeout
static const uint32_t P_MAX_US = 20000;    // RMT duration field is 15 bits
static const uint32_t MS_MAX = 30000;      // hard cap on any command
static const uint32_t N_MAX = 1000;
static const uint32_t INTERVAL_MAX_MS = 10000;
static const uint32_t CHUNK_US = 200000;   // layer (b) granularity
static const uint32_t LOOPS_MAX = 1000;    // S3 RMT: <= 1023 per batch

enum Kind { K_TRAIN, K_PULSES };
struct Cmd {
    Kind kind;
    uint32_t w_us, p_us, ms, n, interval_ms;
};

enum { NOTE_DONE = 1, NOTE_STOP = 2 };
enum { WHY_NONE = 0, WHY_DEADLINE_TIMER, WHY_STOP_CMD };

static QueueHandle_t g_cmdq;
static TaskHandle_t g_task;
static SemaphoreHandle_t g_chan_mux;
static rmt_channel_handle_t g_ch = NULL;
static bool g_enabled = false;  // g_ch enabled; guarded by g_chan_mux
static rmt_encoder_handle_t g_enc = NULL;
static esp_timer_handle_t g_deadline_timer;
static volatile bool g_stop = false;
static volatile bool g_busy = false;
static volatile int g_stop_why = WHY_NONE;
static volatile int64_t g_deadline_us = 0;
static rmt_symbol_word_t g_sym;  // must outlive each transmission
static uint8_t g_canstat = 0, g_canctrl = 0;

static void drive_recessive() {
    gpio_set_level(TX_PIN, 1);              // level first, so enabling is high
    gpio_set_direction(TX_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(TX_PIN, 1);
}

// Shared by the deadline timer and the STOP command. Disables the channel;
// the RMT then sits at its idle level, which the inverted output makes
// recessive.
static void hw_stop(int why) {
    g_stop = true;
    if (g_stop_why == WHY_NONE) g_stop_why = why;
    if (xSemaphoreTake(g_chan_mux, pdMS_TO_TICKS(20)) == pdTRUE) {
        if (g_ch && g_enabled) {
            rmt_disable(g_ch);
            g_enabled = false;
        }
        xSemaphoreGive(g_chan_mux);
    }
    if (g_task) xTaskNotify(g_task, NOTE_STOP, eSetBits);
}

static void on_deadline(void *) { hw_stop(WHY_DEADLINE_TIMER); }

static bool IRAM_ATTR on_done(rmt_channel_handle_t, const rmt_tx_done_event_data_t *,
                              void *) {
    BaseType_t woken = pdFALSE;
    xTaskNotifyFromISR(g_task, NOTE_DONE, eSetBits, &woken);
    return woken == pdTRUE;
}

static bool open_channel() {
    rmt_tx_channel_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.gpio_num = TX_PIN;
    cfg.clk_src = RMT_CLK_SRC_DEFAULT;
    cfg.resolution_hz = 1000000;  // 1 us ticks
    cfg.mem_block_symbols = 48;
    cfg.trans_queue_depth = 4;
    cfg.flags.invert_out = 1;     // internal 0 (idle, eot) -> pin HIGH
    xSemaphoreTake(g_chan_mux, portMAX_DELAY);
    bool ok = rmt_new_tx_channel(&cfg, &g_ch) == ESP_OK;
    if (ok) {
        rmt_tx_event_callbacks_t cbs;
        memset(&cbs, 0, sizeof(cbs));
        cbs.on_trans_done = on_done;
        ok = rmt_tx_register_event_callbacks(g_ch, &cbs, NULL) == ESP_OK &&
             rmt_enable(g_ch) == ESP_OK;
        g_enabled = ok;
        if (!ok) {
            rmt_del_channel(g_ch);
            g_ch = NULL;
        }
    } else {
        g_ch = NULL;
    }
    xSemaphoreGive(g_chan_mux);
    return ok;
}

static void close_channel() {
    xSemaphoreTake(g_chan_mux, portMAX_DELAY);
    if (g_ch) {
        if (g_enabled) rmt_disable(g_ch);
        g_enabled = false;
        rmt_del_channel(g_ch);
        g_ch = NULL;
    }
    xSemaphoreGive(g_chan_mux);
    drive_recessive();
}

static esp_err_t transmit(int loops) {
    rmt_transmit_config_t tc;
    memset(&tc, 0, sizeof(tc));
    tc.loop_count = loops;
    tc.flags.eot_level = 0;  // inverted -> recessive
    esp_err_t e = ESP_FAIL;
    xSemaphoreTake(g_chan_mux, portMAX_DELAY);
    if (g_ch && g_enabled && !g_stop) {
        rmt_encoder_reset(g_enc);
        e = rmt_transmit(g_ch, g_enc, &g_sym, sizeof(g_sym), &tc);
    }
    xSemaphoreGive(g_chan_mux);
    return e;
}

static uint32_t wait_note(uint32_t ms) {
    uint32_t note = 0;
    xTaskNotifyWait(0, 0xFFFFFFFF, &note, pdMS_TO_TICKS(ms));
    return note;
}

static void jam_task(void *) {
    Cmd c;
    for (;;) {
        xQueueReceive(g_cmdq, &c, portMAX_DELAY);
        xTaskNotifyStateClear(NULL);
        ulTaskNotifyValueClear(NULL, 0xFFFFFFFF);
        g_stop = false;
        g_stop_why = WHY_NONE;
        uint32_t ms = c.kind == K_TRAIN ? c.ms : c.n * c.interval_ms + 100;
        int64_t t0 = esp_timer_get_time();
        g_deadline_us = t0 + (int64_t)ms * 1000;
        if (!open_channel()) {
            Serial.println("JAMMER ERR rmt_open_failed");
            drive_recessive();
            g_busy = false;
            continue;
        }
        esp_timer_start_once(g_deadline_timer, (uint64_t)ms * 1000);
        uint32_t chunks = 0, pulses = 0;
        const char *end = "complete";
        if (c.kind == K_TRAIN) {
            Serial.printf("JAMMER ACK TRAIN w_us=%u p_us=%u ms=%u t0_us=%lld\n",
                          c.w_us, c.p_us, ms, (long long)t0);
            g_sym.duration0 = c.w_us;
            g_sym.level0 = 1;  // inverted -> dominant
            g_sym.duration1 = c.p_us - c.w_us;
            g_sym.level1 = 0;
            for (;;) {
                if (g_stop) { end = "stopped"; break; }
                int64_t left = g_deadline_us - esp_timer_get_time();
                uint32_t loops = (uint32_t)(left / c.p_us);
                if (loops > CHUNK_US / c.p_us) loops = CHUNK_US / c.p_us;
                if (loops > LOOPS_MAX) loops = LOOPS_MAX;
                if (loops < 1) { end = "deadline_layer_b"; break; }
                if (transmit((int)loops) != ESP_OK) { end = "tx_refused"; break; }
                chunks++;
                pulses += loops;
                uint32_t note = wait_note(CHUNK_US / 1000 * 2 + 50);
                if (note & NOTE_STOP) { end = "stopped"; break; }
                if (!(note & NOTE_DONE)) { end = "chunk_timeout"; break; }
            }
        } else {
            Serial.printf("JAMMER ACK PULSES w_us=%u n=%u interval_ms=%u "
                          "deadline_ms=%u t0_us=%lld\n",
                          c.w_us, c.n, c.interval_ms, ms, (long long)t0);
            g_sym.duration0 = c.w_us;
            g_sym.level0 = 1;
            g_sym.duration1 = 10;
            g_sym.level1 = 0;
            int64_t next = t0 + 1000;
            for (uint32_t k = 0; k < c.n; k++) {
                int64_t wait_us = next - esp_timer_get_time();
                if (wait_us > 1000 && (wait_note((uint32_t)(wait_us / 1000)) & NOTE_STOP)) {
                    end = "stopped";
                    break;
                }
                while (esp_timer_get_time() < next && !g_stop) {
                }
                if (g_stop) { end = "stopped"; break; }
                int64_t t = esp_timer_get_time();
                if (transmit(0) != ESP_OK) { end = "tx_refused"; break; }
                uint32_t note = wait_note(50);
                if (note & NOTE_STOP) { end = "stopped"; break; }
                pulses++;
                Serial.printf("JAMMER PULSE k=%u t_us=%lld rel_us=%lld\n", k,
                              (long long)t, (long long)(t - t0));
                next += (int64_t)c.interval_ms * 1000;
            }
        }
        esp_timer_stop(g_deadline_timer);
        close_channel();
        int64_t t1 = esp_timer_get_time();
        const char *why = g_stop_why == WHY_DEADLINE_TIMER ? "deadline_timer"
                          : g_stop_why == WHY_STOP_CMD   ? "stop_cmd"
                                                         : "none";
        Serial.printf("JAMMER DONE %s end=%s stop_by=%s pulses=%u chunks=%u "
                      "elapsed_us=%lld over_deadline_us=%lld\n",
                      c.kind == K_TRAIN ? "TRAIN" : "PULSES", end, why, pulses,
                      chunks, (long long)(t1 - t0), (long long)(t1 - g_deadline_us));
        g_busy = false;
    }
}

static uint8_t mcp_read(uint8_t reg) {
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
    digitalWrite(MCP_CS, LOW);
    SPI.transfer(0x03);
    SPI.transfer(reg);
    uint8_t v = SPI.transfer(0x00);
    digitalWrite(MCP_CS, HIGH);
    SPI.endTransaction();
    return v;
}

// SPI RESET puts the MCP2515 in configuration mode (CANSTAT 0x80), where it
// neither transmits nor ACKs. Its reset is not tied to the S3's, so without
// this it could still be in whatever mode the previous image left it.
static void mcp_reset() {
    pinMode(MCP_CS, OUTPUT);
    digitalWrite(MCP_CS, HIGH);
    SPI.begin(MCP_SCK, MCP_MISO, MCP_MOSI, MCP_CS);
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
    digitalWrite(MCP_CS, LOW);
    SPI.transfer(0xC0);
    digitalWrite(MCP_CS, HIGH);
    SPI.endTransaction();
    delay(10);
    g_canstat = mcp_read(0x0E);
    g_canctrl = mcp_read(0x0F);
}

static void banner() {
    Serial.println("JAMMER vtrux_jammer_x2_NOT_FOR_VEHICLE -- TEST EQUIPMENT, "
                   "NEVER ON THE TRUCK");
    Serial.printf("JAMMER src_digest=%s tx=GPIO7(CAN1/TWAI) reset_reason=%d "
                  "uptime_us=%lld\n",
                  JAMMER_SRC_DIGEST, (int)esp_reset_reason(),
                  (long long)esp_timer_get_time());
    Serial.printf("JAMMER mcp2515 canstat=0x%02X canctrl=0x%02X config_mode=%s\n",
                  g_canstat, g_canctrl, (g_canstat & 0xE0) == 0x80 ? "yes" : "NO");
    Serial.printf("JAMMER limits w_us=%u..%u p_us>=4w..%u ms<=%u n<=%u "
                  "interval_ms=1..%u\n",
                  W_MIN_US, W_MAX_US, P_MAX_US, MS_MAX, N_MAX, INTERVAL_MAX_MS);
}

static void handle(char *line) {
    char word[12] = {0};
    unsigned a = 0, b = 0, d = 0;
    char extra = 0;
    int got = sscanf(line, "%11s %u %u %u %c", word, &a, &b, &d, &extra);
    if (got < 1) return;
    if (!strcmp(word, "STOP")) {
        if (g_busy) hw_stop(WHY_STOP_CMD);
        Serial.printf("JAMMER ACK STOP busy=%d\n", (int)g_busy);
        return;
    }
    if (!strcmp(word, "STATUS")) {
        Serial.printf("JAMMER STATUS %s uptime_us=%lld\n", g_busy ? "jamming" : "idle",
                      (long long)esp_timer_get_time());
        return;
    }
    if (!strcmp(word, "ID")) {
        banner();
        return;
    }
    bool train = !strcmp(word, "TRAIN"), pulses = !strcmp(word, "PULSES");
    if (!train && !pulses) {
        Serial.printf("JAMMER ERR unknown_command\n");
        return;
    }
    if (got != 4) {  // missing duration, or trailing junk
        Serial.printf("JAMMER ERR need_exactly_3_args got=%d\n", got - 1);
        return;
    }
    if (g_busy) {
        Serial.println("JAMMER ERR busy");
        return;
    }
    Cmd c;
    memset(&c, 0, sizeof(c));
    c.w_us = a;
    if (a < W_MIN_US || a > W_MAX_US) {
        Serial.printf("JAMMER ERR w_us_out_of_range %u\n", a);
        return;
    }
    if (train) {
        c.kind = K_TRAIN;
        c.p_us = b;
        c.ms = d;
        if (b < 4 * a || b > P_MAX_US) {
            Serial.printf("JAMMER ERR p_us_out_of_range %u\n", b);
            return;
        }
        if (d < 1) {
            Serial.println("JAMMER ERR ms_zero");
            return;
        }
        if (d > MS_MAX) {
            Serial.printf("JAMMER CAPPED ms %u -> %u\n", d, MS_MAX);
            c.ms = MS_MAX;
        }
    } else {
        c.kind = K_PULSES;
        c.n = b;
        c.interval_ms = d;
        if (b < 1 || b > N_MAX || d < 1 || d > INTERVAL_MAX_MS ||
            (uint64_t)b * d + 100 > MS_MAX) {
            Serial.printf("JAMMER ERR pulses_out_of_range n=%u interval_ms=%u\n", b, d);
            return;
        }
    }
    g_busy = true;
    if (xQueueSend(g_cmdq, &c, 0) != pdTRUE) {
        g_busy = false;
        Serial.println("JAMMER ERR queue");
    }
}

void setup() {
    drive_recessive();  // first, before anything that can take time
    Serial.begin(115200);
    mcp_reset();
    g_chan_mux = xSemaphoreCreateMutex();
    g_cmdq = xQueueCreate(1, sizeof(Cmd));
    rmt_copy_encoder_config_t ecfg;
    memset(&ecfg, 0, sizeof(ecfg));
    rmt_new_copy_encoder(&ecfg, &g_enc);
    esp_timer_create_args_t targs;
    memset(&targs, 0, sizeof(targs));
    targs.callback = on_deadline;
    targs.name = "jam_deadline";
    esp_timer_create(&targs, &g_deadline_timer);
    xTaskCreatePinnedToCore(jam_task, "jam", 4096, NULL, 10, &g_task, 1);
    delay(300);
    banner();
}

void loop() {
    static char buf[64];
    static size_t n = 0;
    while (Serial.available()) {
        int ch = Serial.read();
        if (ch == '\r') continue;
        if (ch == '\n') {
            buf[n] = 0;
            handle(buf);
            n = 0;
        } else if (n < sizeof(buf) - 1) {
            buf[n++] = (char)ch;
        }
    }
    delay(2);
}
