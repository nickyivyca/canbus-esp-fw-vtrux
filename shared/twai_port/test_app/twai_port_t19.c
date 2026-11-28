/*
 * twai_port_t19 -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T19). See
 * include/twai_port_t19.h; the commands and answers are in
 * test_app/README.md.
 *
 * Every answer is one JSON line:
 *
 *   {"cmd":"<the line>","core":N,"t0_us":T,"status":S, ... ,"t1_us":T}
 *
 * with the core the command ran on and esp_timer at its start and end
 * (interposer tester, 2026-11-27). A refusal has "status" 400, 500 or 501
 * and "error"; the probe's refusals keep the URI's codes and bodies.
 *
 * The bus is can_port's port 0 through the stock CAN calls (can.h), as the
 * probe drives it, so the probe's phases run here unchanged. The port is
 * twai_port with the kept-driver switch (T14), installed in normal mode.
 */
#include "twai_port_t19.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_cpu.h"
#include "esp_app_desc.h"
#include "esp_idf_version.h"
#include "esp_system.h"
#include "esp_rom_sys.h"
#include "driver/twai.h"

#include "can.h"
#include "can_port.h"
#include "twai_port.h"
#include "../twai_port_ll.h"
#include "twai_port_txabort_run.h"
#include "twai_port_t19_ll.h"
#include "twai_port_t19_parse.h"

static twai_port_t19_cfg_t s_cfg;
static bool s_out_ok;
static int s_install_core = -1;

/* ------------------------------------------------------------ the output -- */

static void out(const char *s, size_t n)
{
    if (s_out_ok)
    {
        s_out_ok = s_cfg.emit(s_cfg.emit_ctx, s, n);
    }
}

static void outf(const char *fmt, ...)
{
    char b[400];
    va_list ap;
    va_start(ap, fmt);
    const int n = vsnprintf(b, sizeof(b), fmt, ap);
    va_end(ap);
    if (n < 0 || n >= (int)sizeof(b))
    {
        s_out_ok = false;   /* a piece that did not fit: the line is cut */
        return;
    }
    out(b, (size_t)n);
}

static void out_str(const char *s)
{
    char b[2 * T19_LINE_MAX + 8];
    if (t19_json_str(b, sizeof(b), s))
    {
        out(b, strlen(b));
    }
    else
    {
        out("null", 4);
    }
}

static int refuse(char *err, size_t errn, int status, const char *msg)
{
    snprintf(err, errn, "%s", msg);
    return status;
}

/* ------------------------------------------------------- the bus, port 0 -- */

/* The T17 hook: the frames a switch to listen-only hands back, in order. */
#define T19_HOOK_MAX 64
static struct { uint32_t id, tag; } s_hook[T19_HOOK_MAX];
static uint32_t s_hook_n, s_hook_lost;

static void on_discard(void *ctx, const twai_message_t *frame, uint32_t tag)
{
    (void)ctx;
    if (s_hook_n < T19_HOOK_MAX)
    {
        s_hook[s_hook_n].id = frame->identifier;
        s_hook[s_hook_n].tag = tag;
        s_hook_n++;
    }
    else
    {
        s_hook_lost++;
    }
}

/* Up in normal mode, as the probe brings it up; from listen-only, by the
 * kept-driver switch. */
static int bus_normal(char *err, size_t errn)
{
    if (!can_is_enabled())
    {
        can_set_silent(0);
        can_set_bitrate(CAN_500K);
        can_enable();
    }
    if (!can_is_enabled())
    {
        return refuse(err, errn, 500, "CAN bus would not come up");
    }
    if (can_is_silent())
    {
        can_set_silent(0);
    }
    if (can_is_silent() || twai_port_mode() != CAN_PORT_MODE_NORMAL)
    {
        return refuse(err, errn, 500, "the port would not go to normal mode");
    }
    return 200;
}

/* The driver's interrupt runs on the core that installed it: this task's. */
static void note_install_core(void)
{
    s_install_core = twai_port_is_installed() ? (s_install_core >= 0
                                                 ? s_install_core
                                                 : esp_cpu_get_core_id())
                                              : -1;
}

static bool drv(twai_status_info_t *s)
{
    if (!twai_port_is_installed())
    {
        return false;
    }
    return twai_get_status_info(s) == ESP_OK;
}

static const char *mode_name(can_port_mode_t m)
{
    switch (m)
    {
    case CAN_PORT_MODE_NORMAL:      return "normal";
    case CAN_PORT_MODE_LISTEN_ONLY: return "listen_only";
    default:                        return "no_ack";
    }
}

static const char *tx_outcome(uint32_t a)
{
    const bool ok = (a & TWAI_ALERT_TX_SUCCESS) != 0;
    const bool bad = (a & TWAI_ALERT_TX_FAILED) != 0;
    return ok ? (bad ? "both" : "success") : (bad ? "failed" : "none");
}

static void tag_frame(twai_message_t *m, uint32_t id, uint8_t lo, uint32_t n)
{
    memset(m, 0, sizeof(*m));
    m->identifier = id;
    m->data_length_code = 8;
    m->data[0] = (uint8_t)(n & 0xFF);
    m->data[1] = (uint8_t)(n >> 8);
    m->data[2] = 0xAB;
    m->data[3] = lo;
}

/* Busy until `us` have passed: the measurements must not block. */
static void spin_us(int64_t us)
{
    const int64_t end = esp_timer_get_time() + us;
    while (esp_timer_get_time() < end)
    {
    }
}

/* ------------------------------------------------------------------ info -- */

static int cmd_info(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { NULL };
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "info takes no arguments");
    }
    outf(",\"status\":200,\"build\":");
    out_str(esp_app_get_description()->version);
    outf(",\"idf\":");
    out_str(esp_get_idf_version());
    outf(",\"port_core\":%d,\"installed\":%s,\"mode\":\"%s\",\"reset_reason\":%d",
         s_install_core, twai_port_is_installed() ? "true" : "false",
         mode_name(twai_port_mode()), (int)esp_reset_reason());
    if (!t19_ll_clock_on())
    {
        outf(",\"clock\":false");
        return 200;
    }
    t19_regs_t r;
    t19_ll_regs(&r);
    outf(",\"clock\":true,\"btr0\":%lu,\"btr1\":%lu,\"brp\":%lu,\"sjw\":%lu,"
         "\"tseg1\":%lu,\"tseg2\":%lu,\"sam\":%lu,\"tec\":%lu,\"rec\":%lu",
         (unsigned long)r.btr0, (unsigned long)r.btr1,
         (unsigned long)(r.btr0 & 0x1FFFu), (unsigned long)((r.btr0 >> 14) & 0x3u),
         (unsigned long)(r.btr1 & 0xFu), (unsigned long)((r.btr1 >> 4) & 0x7u),
         (unsigned long)((r.btr1 >> 7) & 0x1u),
         (unsigned long)r.tec, (unsigned long)r.rec);
    return 200;
}

/* ------------------------------------------------------------------ regs -- */

static int cmd_regs(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { NULL };
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "regs takes no arguments");
    }
    outf(",\"status\":200");
    if (!t19_ll_clock_on())
    {
        outf(",\"clock\":false");
    }
    else
    {
        t19_regs_t r;
        t19_ll_regs(&r);
        const uint32_t s = r.status;
        outf(",\"clock\":true,\"st\":%lu,\"tbs\":%d,\"tcs\":%d,\"rs\":%d,\"ts\":%d,"
             "\"es\":%d,\"bs\":%d,\"mode_reg\":%lu,\"rm\":%d,\"lom\":%d,\"stm\":%d,"
             "\"tec\":%lu,\"rec\":%lu",
             (unsigned long)s, (s >> 2) & 1, (s >> 3) & 1, (s >> 4) & 1,
             (s >> 5) & 1, (s >> 6) & 1, (s >> 7) & 1, (unsigned long)r.mode,
             (r.mode & T19_MODE_RM) ? 1 : 0, (r.mode & T19_MODE_LOM) ? 1 : 0,
             (r.mode & T19_MODE_STM) ? 1 : 0,
             (unsigned long)r.tec, (unsigned long)r.rec);
    }
    twai_status_info_t d;
    if (drv(&d))
    {
        outf(",\"drv\":{\"state\":%d,\"mtx\":%lu,\"mrx\":%lu}", (int)d.state,
             (unsigned long)d.msgs_to_tx, (unsigned long)d.msgs_to_rx);
    }
    else
    {
        outf(",\"drv\":null");
    }
    return 200;
}

/* --------------------------------------------------------------- txabort -- */

static bool s_answer_open;

static bool probe_emit(void *ctx, const char *s, size_t n)
{
    (void)ctx;
    if (!s_answer_open)
    {
        s_answer_open = true;
        outf(",\"status\":200,\"answer\":");
    }
    out(s, n);
    return s_out_ok;
}

static bool owns_nothing(void)
{
    return false;   /* the s3probe image runs no project traffic */
}

/* The T13 probe, every phase, with the URI's arguments (test_app/README.md). */
static int cmd_txabort(t19_line_t *l, char *err, size_t errn)
{
    s_answer_open = false;
    const txab_io_t io = {
        .have_query = l->n > 0,
        .get = t19_get,
        .get_ctx = l,
        .emit = probe_emit,
        .emit_ctx = NULL,
        .owns_bus = owns_nothing,
        .owned_body = NULL,
    };
    const int st = twai_port_txabort_run(&io, err, errn);
    if (st == TXAB_EMIT_FAILED)
    {
        return refuse(err, errn, 0, "the answer was cut off");
    }
    return st;
}

/* ---------------------------------------------------------------- switch -- */

static void out_report(const twai_port_switch_report_t *r)
{
    outf("{\"seq\":%lu,\"to\":\"%s\",\"result\":%d,\"status_at_reset\":%lu,"
         "\"buf_at_reset\":%d,\"held\":%lu,\"head_aborted\":%s,"
         "\"abort_wait_us\":%lu,\"abort_timed_out\":%s,\"driver_tx_stuck\":%s,"
         "\"rec_written\":%lu,\"rec_read_back\":%lu,\"tec_read_back\":%lu}",
         (unsigned long)r->seq, mode_name(r->to_mode), (int)r->result,
         (unsigned long)r->status_at_reset, (int)r->buf_at_reset,
         (unsigned long)r->held, r->head_aborted ? "true" : "false",
         (unsigned long)r->abort_wait_us, r->abort_timed_out ? "true" : "false",
         r->driver_tx_stuck ? "true" : "false", (unsigned long)r->rec_written,
         (unsigned long)r->rec_read_back, (unsigned long)r->tec_read_back);
}

/* One kept-driver switch through the stock call, timed. */
static int64_t do_switch(bool to_lom)
{
    s_hook_n = 0;
    s_hook_lost = 0;
    const int64_t t0 = esp_timer_get_time();
    can_set_silent(to_lom ? 1 : 0);
    return esp_timer_get_time() - t0;
}

static int cmd_switch(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { "to", "n", NULL };
    char v[16];
    long n = 0;
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "switch takes to=lom|normal and n=0..32");
    }
    const int g = t19_get((void *)l, "to", v, sizeof(v));
    const bool to_lom = (g > 0 && strcmp(v, "lom") == 0);
    if (g <= 0 || (!to_lom && strcmp(v, "normal") != 0))
    {
        return refuse(err, errn, 400, "to must be lom or normal");
    }
    const int gn = t19_get((void *)l, "n", v, sizeof(v));
    if (gn < 0 || (gn > 0 && !t19_int(v, 0, 32, &n)))
    {
        return refuse(err, errn, 400, "n must be 0..32");
    }
    if (!can_is_enabled() || (!to_lom && !can_is_silent()))
    {
        const int b = bus_normal(err, errn);   /* from listen-only: stays */
        if (b != 200)
        {
            return b;
        }
    }

    int queued = 0, refused = 0;
    for (long i = 0; i < n; i++)
    {
        twai_message_t m;
        tag_frame(&m, 0x7FD, 0x7D, (uint32_t)i);
        if (twai_port_send_tagged(&m, 0, 0x7100u + (uint32_t)i) == ESP_OK)
        {
            queued++;
        }
        else
        {
            refused++;
        }
    }

    const int64_t sw_us = do_switch(to_lom);
    twai_port_switch_report_t r;
    const bool have = twai_port_last_switch(&r) == ESP_OK;
    const uint32_t st_after = t19_ll_clock_on() ? t19_ll_status() : 0;
    twai_status_info_t d;
    const bool have_d = drv(&d);
    can_port_status_t ps;
    const bool have_ps = twai_port_status(&ps) == ESP_OK;

    outf(",\"status\":200,\"queued\":%d,\"queue_refused\":%d,\"switch_us\":%lld,"
         "\"mode\":\"%s\",\"report\":", queued, refused, (long long)sw_us,
         mode_name(twai_port_mode()));
    if (have)
    {
        out_report(&r);
    }
    else
    {
        out("null", 4);
    }
    outf(",\"hook\":[");
    for (uint32_t i = 0; i < s_hook_n; i++)
    {
        outf("%s[%lu,%lu]", i ? "," : "", (unsigned long)s_hook[i].id,
             (unsigned long)s_hook[i].tag);
    }
    outf("],\"hook_lost\":%lu,\"st_after\":%lu,\"mtx_after\":%ld,"
         "\"tx_discarded_switch\":%ld,\"tx_failed\":%ld",
         (unsigned long)s_hook_lost, (unsigned long)st_after,
         have_d ? (long)d.msgs_to_tx : -1L,
         have_ps ? (long)ps.tx_discarded_switch : -1L,
         have_ps ? (long)ps.tx_failed : -1L);
    return 200;
}

/* ------------------------------------------------------------ switchloop -- */

#define T19_LOOP_MAX 200
static struct {
    int64_t t_us, sw_us;
    twai_port_switch_report_t r;
} s_loop[T19_LOOP_MAX];
static uint32_t s_rx_win[T19_LOOP_MAX + 1];

static uint32_t rx_window(int64_t until)
{
    uint32_t got = 0;
    twai_message_t m;
    while (esp_timer_get_time() < until)
    {
        if (twai_port_receive(&m, 1) == ESP_OK)
        {
            got++;
        }
    }
    return got;
}

static int cmd_switchloop(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { "k", "period_ms", NULL };
    char v[16];
    long k = 10, period = 100;
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "switchloop takes k=1..200 and period_ms=1..10000");
    }
    int g = t19_get((void *)l, "k", v, sizeof(v));
    if (g < 0 || (g > 0 && !t19_int(v, 1, T19_LOOP_MAX, &k)))
    {
        return refuse(err, errn, 400, "k must be 1..200");
    }
    g = t19_get((void *)l, "period_ms", v, sizeof(v));
    if (g < 0 || (g > 0 && !t19_int(v, 1, 10000, &period)))
    {
        return refuse(err, errn, 400, "period_ms must be 1..10000");
    }
    const int b = bus_normal(err, errn);
    if (b != 200)
    {
        return b;
    }
    can_port_status_t p0;
    const bool have_p0 = twai_port_status(&p0) == ESP_OK;

    const int64_t t_start = esp_timer_get_time();
    for (long i = 0; i < k; i++)
    {
        s_rx_win[i] = rx_window(t_start + (int64_t)(i + 1) * period * 1000);
        s_loop[i].t_us = esp_timer_get_time() - t_start;
        s_loop[i].sw_us = do_switch((i % 2) == 0);
        if (twai_port_last_switch(&s_loop[i].r) != ESP_OK)
        {
            memset(&s_loop[i].r, 0, sizeof(s_loop[i].r));
        }
    }
    s_rx_win[k] = rx_window(t_start + (int64_t)(k + 1) * period * 1000);
    can_port_status_t p1;
    const bool have_p1 = twai_port_status(&p1) == ESP_OK;

    outf(",\"status\":200,\"k\":%ld,\"period_ms\":%ld,\"switches\":[", k, period);
    for (long i = 0; i < k; i++)
    {
        const twai_port_switch_report_t *r = &s_loop[i].r;
        outf("%s[%lld,%d,%lld,%d,%lu,%lu,%lu,%d,%d]", i ? "," : "",
             (long long)s_loop[i].t_us, (i % 2) == 0 ? 1 : 0,
             (long long)s_loop[i].sw_us, (int)r->result, (unsigned long)r->held,
             (unsigned long)r->rec_read_back, (unsigned long)r->status_at_reset,
             r->head_aborted ? 1 : 0, r->driver_tx_stuck ? 1 : 0);
    }
    outf("],\"rx_windows\":[");
    for (long i = 0; i <= k; i++)
    {
        outf("%s%lu", i ? "," : "", (unsigned long)s_rx_win[i]);
    }
    out("]", 1);
    if (have_p0 && have_p1)
    {
        outf(",\"rx_frames\":%lu,\"rx_lost\":%lu,\"rx_lost_queue_full\":%lu,"
             "\"rx_lost_overrun\":%lu,\"rx_lost_restart\":%lu,\"bus_errors\":%lu,"
             "\"tx_discarded_switch\":%lu",
             (unsigned long)(p1.rx_frames - p0.rx_frames),
             (unsigned long)(p1.rx_lost - p0.rx_lost),
             (unsigned long)(p1.rx_lost_queue_full - p0.rx_lost_queue_full),
             (unsigned long)(p1.rx_lost_overrun - p0.rx_lost_overrun),
             (unsigned long)(p1.rx_lost_restart - p0.rx_lost_restart),
             (unsigned long)(p1.bus_errors - p0.bus_errors),
             (unsigned long)(p1.tx_discarded_switch - p0.tx_discarded_switch));
    }
    if (can_is_silent())
    {
        can_set_silent(0);  /* an odd k ends in listen-only: back to normal */
    }
    return 200;
}

/* ------------------------------------------------------------ resetprobe -- */

static int cmd_resetprobe(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { "at", "att", "delay_us", "leave",
                                        "wait_us", "abort", "window_us", NULL };
    char v[16];
    long att = 1, delay_us = 0, wait_us = 1000, abort_after = 0, window_us = 10000;
    bool at_ts1 = false, leave_lom = false;
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "resetprobe takes at, att, delay_us, leave, "
                                      "wait_us, abort and window_us");
    }
    int g = t19_get((void *)l, "at", v, sizeof(v));
    if (g < 0 || (g > 0 && strcmp(v, "ts0") != 0 && strcmp(v, "ts1") != 0))
    {
        return refuse(err, errn, 400, "at must be ts0 or ts1");
    }
    at_ts1 = g > 0 && strcmp(v, "ts1") == 0;
    g = t19_get((void *)l, "leave", v, sizeof(v));
    if (g < 0 || (g > 0 && strcmp(v, "normal") != 0 && strcmp(v, "lom") != 0))
    {
        return refuse(err, errn, 400, "leave must be normal or lom");
    }
    leave_lom = g > 0 && strcmp(v, "lom") == 0;
    const struct { const char *key; long *dst; long lo, hi; } nums[] = {
        { "att", &att, 1, 255 },          { "delay_us", &delay_us, 0, 5000 },
        { "wait_us", &wait_us, 0, 100000 }, { "abort", &abort_after, 0, 1 },
        { "window_us", &window_us, 100, 50000 },
    };
    for (unsigned i = 0; i < sizeof(nums) / sizeof(nums[0]); i++)
    {
        g = t19_get((void *)l, nums[i].key, v, sizeof(v));
        if (g < 0 || (g > 0 && !t19_int(v, nums[i].lo, nums[i].hi, nums[i].dst)))
        {
            return refuse(err, errn, 400, "an argument is out of range");
        }
    }
    if (abort_after && !leave_lom)
    {
        return refuse(err, errn, 400, "abort=1 needs leave=lom");
    }
    if (delay_us > 0 && !at_ts1)
    {
        return refuse(err, errn, 400, "delay_us needs at=ts1");
    }
    int b = bus_normal(err, errn);
    if (b != 200)
    {
        return b;
    }
    if (twai_reconfigure_alerts(TWAI_ALERT_ALL, NULL) != ESP_OK)
    {
        return refuse(err, errn, 500, "alerts would not enable");
    }
    uint32_t a = 0;
    (void)twai_read_alerts(&a, 0);
    can_flush_rx();
    twai_status_info_t d;
    if ((t19_ll_status() & T19_ST_TBS) == 0 || !drv(&d) || d.msgs_to_tx != 0)
    {
        return refuse(err, errn, 400, "the buffer or the driver holds a frame");
    }

    /* The frame, then the attempts counted by TS rises. */
    twai_message_t m;
    tag_frame(&m, 0x7FC, 0x7C, 0);
    if (twai_transmit(&m, 0) != ESP_OK)
    {
        return refuse(err, errn, 500, "twai_transmit refused the frame");
    }
    const int64_t t_send = esp_timer_get_time();
    uint32_t attempts = 0, prev = t19_ll_status();
    bool entered = false, freed_before = false, timed_out = false;
    uint32_t st_b = 0, st_a = 0;
    int64_t t_enter = -1;
    if (prev & T19_ST_TS)
    {
        attempts = 1;
    }
    for (;;)
    {
        const uint32_t st = t19_ll_status();
        if ((st & T19_ST_TS) && !(prev & T19_ST_TS))
        {
            attempts++;
        }
        prev = st;
        if (st & T19_ST_TBS)
        {
            freed_before = true;
            break;
        }
        if (attempts >= (uint32_t)att)
        {
            if (at_ts1 && (st & T19_ST_TS))
            {
                spin_us(delay_us);
                entered = t19_ll_enter_reset(false, &st_b, &st_a);
            }
            else if (!at_ts1)
            {
                entered = t19_ll_enter_reset(true, &st_b, &st_a);
            }
            if (entered)
            {
                t_enter = esp_timer_get_time() - t_send;
                break;
            }
        }
        if (esp_timer_get_time() - t_send > window_us)
        {
            timed_out = true;
            break;
        }
    }

    uint32_t st_reset = 0, st_wait = 0, ab_b = 0, ab_a = 0, a_wait = 0, a_two = 0;
    long mtx_reset = -1, mtx_wait = -1;
    int two_err = -1;
    if (entered)
    {
        st_reset = t19_ll_status();
        mtx_reset = drv(&d) ? (long)d.msgs_to_tx : -1L;
        t19_ll_leave_reset(leave_lom);
        if (abort_after)
        {
            t19_ll_abort(&ab_b, &ab_a);
        }
        spin_us(wait_us);
        st_wait = t19_ll_status();
        mtx_wait = drv(&d) ? (long)d.msgs_to_tx : -1L;
        (void)twai_read_alerts(&a_wait, 0);

        tag_frame(&m, 0x7FC, 0x7B, 1);
        const esp_err_t e2 = twai_transmit(&m, pdMS_TO_TICKS(10));
        two_err = (e2 == ESP_OK) ? 0 : (int)e2;
        const int64_t t2 = esp_timer_get_time();
        while (esp_timer_get_time() - t2 < 10000)
        {
            uint32_t x = 0;
            (void)twai_read_alerts(&x, 0);
            a_two |= x;
            if (a_two & (TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED))
            {
                break;
            }
        }
    }

    outf(",\"status\":200,\"at\":\"%s\",\"att\":%ld,\"delay_us\":%ld,\"leave\":\"%s\","
         "\"wait_us\":%ld,\"abort\":%ld,\"attempts\":%lu,\"entered\":%s,"
         "\"freed_before\":%s,\"wait_timeout\":%s,\"enter_us\":%lld,"
         "\"st_before\":%lu,\"st_after_enter\":%lu,\"st_reset\":%lu,\"mtx_reset\":%ld",
         at_ts1 ? "ts1" : "ts0", att, delay_us, leave_lom ? "lom" : "normal",
         wait_us, abort_after, (unsigned long)attempts, entered ? "true" : "false",
         freed_before ? "true" : "false", timed_out ? "true" : "false",
         (long long)t_enter, (unsigned long)st_b, (unsigned long)st_a,
         (unsigned long)st_reset, mtx_reset);
    outf(",\"ab_st_before\":%lu,\"ab_st_after\":%lu,\"st_wait\":%lu,\"mtx_wait\":%ld,"
         "\"tx_alerts\":\"%s\",\"alerts_wait\":%lu,\"second_tx_err\":%d,"
         "\"second\":\"%s\",\"alerts_second\":%lu",
         (unsigned long)ab_b, (unsigned long)ab_a, (unsigned long)st_wait, mtx_wait,
         tx_outcome(a_wait), (unsigned long)a_wait, two_err, tx_outcome(a_two),
         (unsigned long)a_two);

    /* Nothing of this trial is left for the next command: the port down. */
    can_disable();
    outf(",\"cleanup\":\"%s\"", can_is_enabled() ? "bus still up" : "bus down");
    return 200;
}

/* --------------------------------------------------------------- restart -- */

static volatile int s_core_period_us;

static void core_sender(void *arg)
{
    (void)arg;
    uint32_t n = 0;
    twai_message_t m;
    for (;;)
    {
        tag_frame(&m, 0x7FB, 0x7A, n++);
        (void)twai_transmit(&m, 0);
        esp_rom_delay_us((uint32_t)s_core_period_us);
    }
}

static int cmd_restart(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { "how", "load", "n", "period_us", NULL };
    char how[16], load[16], v[16];
    long n = 4, period = 1000;
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "restart takes how, load, n and period_us");
    }
    if (t19_get((void *)l, "how", how, sizeof(how)) <= 0
        || (strcmp(how, "restart") != 0 && strcmp(how, "panic") != 0))
    {
        return refuse(err, errn, 400, "how must be restart or panic");
    }
    int g = t19_get((void *)l, "load", load, sizeof(load));
    if (g < 0)
    {
        return refuse(err, errn, 400, "load is unusable");
    }
    if (g == 0)
    {
        strcpy(load, "idle");
    }
    const bool l_idle = strcmp(load, "idle") == 0, l_noack = strcmp(load, "noack") == 0;
    const bool l_queued = strcmp(load, "queued") == 0, l_flood = strcmp(load, "flood") == 0;
    const bool l_core = strcmp(load, "core") == 0;
    if (!(l_idle || l_noack || l_queued || l_flood || l_core))
    {
        return refuse(err, errn, 400, "load must be idle, noack, queued, flood or core");
    }
    g = t19_get((void *)l, "n", v, sizeof(v));
    if (g < 0 || (g > 0 && (!l_queued || !t19_int(v, 1, 32, &n))))
    {
        return refuse(err, errn, 400, "n is 1..32, with load=queued only");
    }
    g = t19_get((void *)l, "period_us", v, sizeof(v));
    if (g < 0 || (g > 0 && (!l_core || !t19_int(v, 50, 100000, &period))))
    {
        return refuse(err, errn, 400, "period_us is 50..100000, with load=core only");
    }
    const int b = bus_normal(err, errn);
    if (b != 200)
    {
        return b;
    }
    note_install_core();

    twai_message_t m;
    if (l_noack)
    {
        tag_frame(&m, 0x7FA, 0x79, 0);
        (void)twai_transmit(&m, 0);
    }
    else if (l_queued)
    {
        for (long i = 0; i < n; i++)
        {
            tag_frame(&m, 0x7FA, 0x79, (uint32_t)i);
            (void)twai_transmit(&m, 0);
        }
    }
    else if (l_core)
    {
        s_core_period_us = (int)period;
        const int other = (esp_cpu_get_core_id() == 0) ? 1 : 0;
        if (xTaskCreatePinnedToCore(core_sender, "t19_core", 3072, NULL, 5, NULL,
                                    other) != pdPASS)
        {
            return refuse(err, errn, 500, "the sender task would not start");
        }
        vTaskDelay(pdMS_TO_TICKS(50));  /* let it run before the restart */
    }

    outf(",\"status\":200,\"how\":\"%s\",\"load\":\"%s\",\"port_core\":%d,"
         "\"restart_at_us\":%lld", how, load, s_install_core,
         (long long)esp_timer_get_time());
    outf(",\"t1_us\":%lld}\n", (long long)esp_timer_get_time());
    if (s_cfg.flush != NULL)
    {
        s_cfg.flush();
    }
    if (strcmp(how, "panic") == 0)
    {
        abort();
    }
    esp_restart();
    return 0;   /* not reached */
}

/* -------------------------------------------------------------------- g4 -- */

static int cmd_g4(const t19_line_t *l, char *err, size_t errn)
{
    static const char *const keys[] = { "clear", NULL };
    char v[8];
    long clear = 0;
    if (t19_unknown(l, keys) != NULL)
    {
        return refuse(err, errn, 400, "g4 takes clear=0|1");
    }
    const int g = t19_get((void *)l, "clear", v, sizeof(v));
    if (g < 0 || (g > 0 && !t19_int(v, 0, 1, &clear)))
    {
        return refuse(err, errn, 400, "clear must be 0 or 1");
    }
    char rec[700];
    const bool have = s_cfg.restart_record != NULL
                      && s_cfg.restart_record(rec, sizeof(rec));
    outf(",\"status\":200,\"reset_reason\":%d,\"g4\":", (int)esp_reset_reason());
    if (have)
    {
        out(rec, strlen(rec));
    }
    else
    {
        out("null", 4);
    }
    twai_port_s3_stall_record_t s;
    const bool valid = twai_port_ll_s3_stall_record(&s);
    outf(",\"stall\":{\"valid\":%s,\"core\":%lu,\"other\":%lu,\"options0\":%lu,"
         "\"sw_cpu_stall\":%lu,\"stalled_at_entry\":%lu,\"stall_called\":%lu,"
         "\"entered_reset\":%lu}", valid ? "true" : "false",
         (unsigned long)s.core, (unsigned long)s.other, (unsigned long)s.options0,
         (unsigned long)s.sw_cpu_stall, (unsigned long)s.stalled_at_entry,
         (unsigned long)s.stall_called, (unsigned long)s.entered_reset);
    if (clear)
    {
        twai_port_ll_s3_stall_record_clear();
    }
    return 200;
}

/* ------------------------------------------------------------ the driver -- */

esp_err_t twai_port_t19_init(const twai_port_t19_cfg_t *cfg)
{
    if (cfg == NULL || cfg->emit == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = *cfg;
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.board_port = cfg->board_port;
    c.mode = CAN_PORT_MODE_NORMAL;
    c.filter.acceptance_code = 0;
    c.filter.acceptance_mask = 0xFFFFFFFFu;
    c.filter.single_filter = true;
    c.single_shot = false;
    c.rx_queue_len = 64;
    c.tx_queue_len = 32;
    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    c.kept_driver_switch = true;
    c.switch_discard = on_discard;
    esp_err_t e = twai_port_setup(&c);
    if (e == ESP_OK)
    {
        e = can_port_register(0, &twai_port_ops, NULL);
    }
    if (e == ESP_OK)
    {
        can_init(CAN_500K);
        can_set_bitrate(CAN_500K);
    }
    return e;
}

void twai_port_t19_line(const char *line, bool too_long)
{
    static t19_line_t l;
    char err[TXAB_ERR_MAX];
    int st;

    s_out_ok = true;
    note_install_core();
    out("{\"cmd\":", 7);
    out_str(line);
    outf(",\"core\":%d,\"t0_us\":%lld", esp_cpu_get_core_id(),
         (long long)esp_timer_get_time());

    err[0] = '\0';
    if (too_long)
    {
        st = refuse(err, sizeof(err), 400,
                    "the line is too long; refused rather than cut");
    }
    else if (!t19_parse(line, &l, err, sizeof(err)))
    {
        st = 400;
    }
    else if (strcmp(l.cmd, "info") == 0)        { st = cmd_info(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "regs") == 0)        { st = cmd_regs(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "txabort") == 0)     { st = cmd_txabort(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "switch") == 0)      { st = cmd_switch(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "switchloop") == 0)  { st = cmd_switchloop(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "resetprobe") == 0)  { st = cmd_resetprobe(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "restart") == 0)     { st = cmd_restart(&l, err, sizeof(err)); }
    else if (strcmp(l.cmd, "g4") == 0)          { st = cmd_g4(&l, err, sizeof(err)); }
    else
    {
        st = refuse(err, sizeof(err), 400, "unknown command; the commands are info, "
                    "regs, txabort, switch, switchloop, resetprobe, restart and g4");
    }

    if (st != 200)
    {
        outf(",\"status\":%d,\"error\":", st);
        out_str(err);
    }
    note_install_core();
    outf(",\"t1_us\":%lld}\n", (long long)esp_timer_get_time());
}
