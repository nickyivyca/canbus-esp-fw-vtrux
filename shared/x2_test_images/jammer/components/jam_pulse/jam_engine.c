/*
 * jam_engine.c -- the jammer's pulse engine (jam_pulse.h). TEST EQUIPMENT,
 * NOT FOR VEHICLE (x2_test_images SPEC T8, T13).
 *
 * Ported from the PlatformIO jammer (gen-inhibit/tools/x2_jammer/src/
 * main.cpp, gen-inhibit tester, 2026-10-08) onto ESP-IDF v5.4.2's RMT and
 * esp_timer drivers, the same drivers it used under arduino-esp32. Changes
 * from it:
 *  - replies go through the caller's callback, without "JAMMER ";
 *  - a STOP that comes between a command's acceptance and its start is
 *    kept: the stop flag is cleared when the command is accepted, not when
 *    the task picks it up;
 *  - PULSES re-arms a pulse only while it ends before the deadline, as
 *    TRAIN's chunks do (layer (b) for PULSES; before, only the pulse count
 *    bounded it);
 *  - the two test-only builds (jam_pulse.h): JAM_TEST_STOP_A_ONLY moves
 *    layer (b) to the deadline plus JAM_BACKSTOP_US, JAM_TEST_STOP_B_ONLY
 *    never creates or starts the deadline timer.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/rmt_encoder.h"
#include "driver/rmt_tx.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "jam_internal.h"
#include "jam_pulse.h"

#if defined(JAM_TEST_STOP_A_ONLY)
#define LAYER_B_EXTRA_US ((int64_t)JAM_BACKSTOP_US)
#define LAYER_B_END      "backstop_layer_b"
#define STOPS_TEXT       "a_only(b at deadline+2000ms)"
#else
#define LAYER_B_EXTRA_US ((int64_t)0)
#define LAYER_B_END      "deadline_layer_b"
#endif
#if defined(JAM_TEST_STOP_B_ONLY)
#define HAVE_LAYER_A 0
#define STOPS_TEXT   "b_only"
#else
#define HAVE_LAYER_A 1
#endif
#ifndef STOPS_TEXT
#define STOPS_TEXT "a+b"
#endif

#define PULSE_GAP_US 10u        /* PULSES: the recessive tail of a pulse */

enum { NOTE_DONE = 1, NOTE_STOP = 2 };
enum { WHY_NONE = 0, WHY_DEADLINE_TIMER, WHY_STOP_CMD };

static jam_config_t s_cfg;
static QueueHandle_t s_cmdq;
static TaskHandle_t s_task;
static SemaphoreHandle_t s_chan_mux;
static rmt_channel_handle_t s_ch;
static bool s_enabled;          /* s_ch enabled; guarded by s_chan_mux */
static rmt_encoder_handle_t s_enc;
#if HAVE_LAYER_A
static esp_timer_handle_t s_deadline_timer;
#endif
static volatile bool s_stop;
static volatile bool s_busy;
static volatile int s_stop_why = WHY_NONE;
static volatile int64_t s_deadline_us;
static rmt_symbol_word_t s_sym;  /* must outlive each transmission */

static void reply(const char *fmt, ...)
{
    char b[JAM_REPLY_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    if (s_cfg.reply) s_cfg.reply(b, s_cfg.ctx);
}

void jam_drive_recessive(gpio_num_t gpio)
{
    gpio_set_level(gpio, 1);    /* level first, so enabling is high */
    gpio_set_direction(gpio, GPIO_MODE_OUTPUT);
    gpio_set_level(gpio, 1);
}

/* Shared by the deadline timer and STOP. Disables the channel; the RMT then
 * sits at its idle level, which the inverted output makes recessive. */
static void hw_stop(int why)
{
    s_stop = true;
    if (s_stop_why == WHY_NONE) s_stop_why = why;
    if (xSemaphoreTake(s_chan_mux, pdMS_TO_TICKS(20)) == pdTRUE) {
        if (s_ch && s_enabled) {
            rmt_disable(s_ch);
            s_enabled = false;
        }
        xSemaphoreGive(s_chan_mux);
    }
    if (s_task) xTaskNotify(s_task, NOTE_STOP, eSetBits);
}

#if HAVE_LAYER_A
static void on_deadline(void *arg)
{
    (void)arg;
    hw_stop(WHY_DEADLINE_TIMER);
}
#endif

static bool IRAM_ATTR on_done(rmt_channel_handle_t ch,
                              const rmt_tx_done_event_data_t *ev, void *ctx)
{
    (void)ch;
    (void)ev;
    (void)ctx;
    BaseType_t woken = pdFALSE;
    xTaskNotifyFromISR(s_task, NOTE_DONE, eSetBits, &woken);
    return woken == pdTRUE;
}

static bool open_channel(void)
{
    rmt_tx_channel_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.gpio_num = s_cfg.tx_gpio;
    cfg.clk_src = RMT_CLK_SRC_DEFAULT;
    cfg.resolution_hz = 1000000;    /* 1 us ticks */
    cfg.mem_block_symbols = 48;
    cfg.trans_queue_depth = 4;
    cfg.flags.invert_out = 1;       /* internal 0 (idle, eot) -> pin high */
    xSemaphoreTake(s_chan_mux, portMAX_DELAY);
    bool ok = rmt_new_tx_channel(&cfg, &s_ch) == ESP_OK;
    if (ok) {
        rmt_tx_event_callbacks_t cbs;
        memset(&cbs, 0, sizeof cbs);
        cbs.on_trans_done = on_done;
        ok = rmt_tx_register_event_callbacks(s_ch, &cbs, NULL) == ESP_OK
             && rmt_enable(s_ch) == ESP_OK;
        s_enabled = ok;
        if (!ok) {
            rmt_del_channel(s_ch);
            s_ch = NULL;
        }
    } else {
        s_ch = NULL;
    }
    xSemaphoreGive(s_chan_mux);
    return ok;
}

static void close_channel(void)
{
    xSemaphoreTake(s_chan_mux, portMAX_DELAY);
    if (s_ch) {
        if (s_enabled) rmt_disable(s_ch);
        s_enabled = false;
        rmt_del_channel(s_ch);
        s_ch = NULL;
    }
    xSemaphoreGive(s_chan_mux);
    jam_drive_recessive(s_cfg.tx_gpio);
}

static esp_err_t transmit(int loops)
{
    rmt_transmit_config_t tc;
    memset(&tc, 0, sizeof tc);
    tc.loop_count = loops;
    tc.flags.eot_level = 0;     /* inverted -> recessive */
    esp_err_t e = ESP_FAIL;
    xSemaphoreTake(s_chan_mux, portMAX_DELAY);
    if (s_ch && s_enabled && !s_stop) {
        rmt_encoder_reset(s_enc);
        e = rmt_transmit(s_ch, s_enc, &s_sym, sizeof s_sym, &tc);
    }
    xSemaphoreGive(s_chan_mux);
    return e;
}

static uint32_t wait_note(uint32_t ms)
{
    uint32_t note = 0;
    xTaskNotifyWait(0, 0xFFFFFFFF, &note, pdMS_TO_TICKS(ms));
    return note;
}

bool jam_engine_take(jam_cmd_t *c, TickType_t wait)
{
    return xQueueReceive(s_cmdq, c, wait) == pdTRUE;
}

void jam_engine_run(const jam_cmd_t *c)
{
    xTaskNotifyStateClear(NULL);
    ulTaskNotifyValueClear(NULL, 0xFFFFFFFF);
    uint32_t ms = jam_cmd_ms(c);
    int64_t t0 = esp_timer_get_time();
    s_deadline_us = t0 + (int64_t)ms * 1000;
    int64_t limit = s_deadline_us + LAYER_B_EXTRA_US;  /* layer (b) */
    if (!open_channel()) {
        reply("ERR rmt_open_failed");
        jam_drive_recessive(s_cfg.tx_gpio);
        s_busy = false;
        return;
    }
#if HAVE_LAYER_A
    esp_timer_start_once(s_deadline_timer, (uint64_t)ms * 1000);
#endif
    uint32_t chunks = 0, pulses = 0;
    const char *end = "complete";
    if (c->kind == JAM_K_TRAIN) {
        reply("ACK TRAIN w_us=%lu p_us=%lu ms=%lu t0_us=%lld",
              (unsigned long)c->w_us, (unsigned long)c->p_us,
              (unsigned long)ms, (long long)t0);
        s_sym.duration0 = c->w_us;
        s_sym.level0 = 1;       /* inverted -> dominant */
        s_sym.duration1 = c->p_us - c->w_us;
        s_sym.level1 = 0;
        for (;;) {
            if (s_stop) { end = "stopped"; break; }
            int64_t left = limit - esp_timer_get_time();
            int64_t loops = left > 0 ? left / c->p_us : 0;
            if (loops > JAM_CHUNK_US / c->p_us) loops = JAM_CHUNK_US / c->p_us;
            if (loops > JAM_LOOPS_MAX) loops = JAM_LOOPS_MAX;
            if (loops < 1) { end = LAYER_B_END; break; }
            if (transmit((int)loops) != ESP_OK) { end = "tx_refused"; break; }
            chunks++;
            pulses += (uint32_t)loops;
            uint32_t note = wait_note(JAM_CHUNK_US / 1000 * 2 + 50);
            if (note & NOTE_STOP) { end = "stopped"; break; }
            if (!(note & NOTE_DONE)) { end = "chunk_timeout"; break; }
        }
    } else {
        reply("ACK PULSES w_us=%lu n=%lu interval_ms=%lu deadline_ms=%lu "
              "t0_us=%lld",
              (unsigned long)c->w_us, (unsigned long)c->n,
              (unsigned long)c->interval_ms, (unsigned long)ms,
              (long long)t0);
        s_sym.duration0 = c->w_us;
        s_sym.level0 = 1;
        s_sym.duration1 = PULSE_GAP_US;
        s_sym.level1 = 0;
        int64_t next = t0 + 1000;
        for (uint32_t k = 0; k < c->n; k++) {
            int64_t wait_us = next - esp_timer_get_time();
            if (wait_us > 1000
                    && (wait_note((uint32_t)(wait_us / 1000)) & NOTE_STOP)) {
                end = "stopped";
                break;
            }
            while (esp_timer_get_time() < next && !s_stop) {
            }
            if (s_stop) { end = "stopped"; break; }
            int64_t t = esp_timer_get_time();
            if (t + c->w_us + PULSE_GAP_US > limit) { end = LAYER_B_END; break; }
            if (transmit(0) != ESP_OK) { end = "tx_refused"; break; }
            uint32_t note = wait_note(50);
            if (note & NOTE_STOP) { end = "stopped"; break; }
            pulses++;
            reply("PULSE k=%lu t_us=%lld rel_us=%lld", (unsigned long)k,
                  (long long)t, (long long)(t - t0));
            next += (int64_t)c->interval_ms * 1000;
        }
    }
#if HAVE_LAYER_A
    esp_timer_stop(s_deadline_timer);
#endif
    close_channel();
    int64_t t1 = esp_timer_get_time();
    const char *why = s_stop_why == WHY_DEADLINE_TIMER ? "deadline_timer"
                      : s_stop_why == WHY_STOP_CMD     ? "stop_cmd"
                                                       : "none";
    reply("DONE %s end=%s stop_by=%s pulses=%lu chunks=%lu elapsed_us=%lld "
          "over_deadline_us=%lld",
          c->kind == JAM_K_TRAIN ? "TRAIN" : "PULSES", end, why,
          (unsigned long)pulses, (unsigned long)chunks, (long long)(t1 - t0),
          (long long)(t1 - s_deadline_us));
    s_busy = false;
}

static void jam_task(void *arg)
{
    (void)arg;
    jam_cmd_t c;
    for (;;) {
        if (jam_engine_take(&c, portMAX_DELAY)) jam_engine_run(&c);
    }
}

esp_err_t jam_init(const jam_config_t *cfg)
{
    if (!cfg || !cfg->reply) return ESP_ERR_INVALID_ARG;
    s_cfg = *cfg;
    jam_drive_recessive(s_cfg.tx_gpio);
    s_chan_mux = xSemaphoreCreateMutex();
    s_cmdq = xQueueCreate(1, sizeof(jam_cmd_t));
    if (!s_chan_mux || !s_cmdq) return ESP_ERR_NO_MEM;
    rmt_copy_encoder_config_t ecfg;
    memset(&ecfg, 0, sizeof ecfg);
    esp_err_t err = rmt_new_copy_encoder(&ecfg, &s_enc);
    if (err != ESP_OK) return err;
#if HAVE_LAYER_A
    esp_timer_create_args_t targs;
    memset(&targs, 0, sizeof targs);
    targs.callback = on_deadline;
    targs.name = "jam_deadline";
    err = esp_timer_create(&targs, &s_deadline_timer);
    if (err != ESP_OK) return err;
#endif
    if (xTaskCreatePinnedToCore(jam_task, "jam", 4096, NULL,
                                (UBaseType_t)s_cfg.priority, &s_task,
                                s_cfg.core) != pdPASS)
        return ESP_ERR_NO_MEM;
    return ESP_OK;
}

jam_line_t jam_line(const char *line)
{
    jam_cmd_t c;
    char note[JAM_REPLY_MAX];
    switch (jam_parse(line, s_busy, &c, note, sizeof note)) {
    case JP_EMPTY:
        return JAM_LINE_DONE;
    case JP_NOT_MINE:
        return JAM_LINE_NOT_MINE;
    case JP_STOP: {
        bool busy = s_busy;
        if (busy) hw_stop(WHY_STOP_CMD);
        reply("ACK STOP busy=%d", (int)busy);
        return JAM_LINE_DONE;
    }
    case JP_STATUS:
        reply("STATUS %s uptime_us=%lld", s_busy ? "jamming" : "idle",
              (long long)esp_timer_get_time());
        return JAM_LINE_DONE;
    case JP_REFUSED:
        reply("%s", note);
        return JAM_LINE_DONE;
    case JP_CMD:
        break;
    }
    if (note[0]) reply("%s", note);
    s_stop = false;
    s_stop_why = WHY_NONE;
    s_busy = true;
    if (xQueueSend(s_cmdq, &c, 0) != pdTRUE) {
        s_busy = false;
        reply("ERR queue");
    }
    return JAM_LINE_DONE;
}

bool jam_busy(void)
{
    return s_busy;
}

void jam_limits_text(char *buf, size_t n)
{
    snprintf(buf, n, "limits w_us=%u..%u p_us>=4w..%u ms<=%u n<=%u "
             "interval_ms=1..%u",
             JAM_W_MIN_US, JAM_W_MAX_US, JAM_P_MAX_US, JAM_MS_MAX, JAM_N_MAX,
             JAM_INTERVAL_MAX_MS);
}

const char *jam_stops_text(void)
{
    return STOPS_TEXT;
}
