/*
 * Host test of the T19 app's switch commands (../twai_port_t19.c; SPEC.md
 * T14, T17, T19; the implementor's). A self-check, not verification: the
 * tester measures on the bench.
 *
 * The app runs whole, with shared/can_port's real dispatch (can_port.c), over
 * a fake port that gives only what the switch commands reach:
 *
 *   - THE STOCK CALLS AS can_stock.c HAS THEM: can_set_silent() is ignored
 *     while the bus is on, and only records the mode for the next
 *     can_enable(). At tag 2026.11.27.6 the app switched through it, so no
 *     switch ever ran on the S3 (VERIFICATION.md, T19 run
 *     bench_t19_1d4221c9). Every switch case here fails on that app.
 *   - twai_port_ops.set_mode as the kept-driver switch (T14): a change
 *     between normal and listen-only with the driver installed makes one
 *     report (seq + 1, the new mode, REC 128 into listen-only and 0 out of
 *     it), and on the way into listen-only hands each queued frame to the
 *     switch_discard hook once, in order (T17). With `s_refuse` set it
 *     refuses as a port that is not running does, and makes no report.
 *
 * The clock moves 1 us at each read, by each esp_rom_delay_us(), and 1 ms at
 * each empty receive, so switchloop's windows end.
 *
 *   make -C shared/twai_port/test_app/test run
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "can.h"
#include "can_port.h"
#include "twai_port.h"
#include "../twai_port_ll.h"
#include "twai_port_t19.h"
#include "twai_port_t19_ll.h"
#include "twai_port_txabort_run.h"
#include "esp_app_desc.h"
#include "esp_idf_version.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_cpu.h"
#include "esp_rom_sys.h"
#include "freertos/task.h"

static int s_fail;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); s_fail++; } } while (0)

/* ------------------------------------------------------------- the fake -- */

static twai_port_config_t s_cfg;
static bool s_on_bus;           /* can_stock.c's ON_BUS */
static uint8_t s_stock_silent;  /* can_stock.c's can_cfg.silent */
static bool s_installed;
static can_port_mode_t s_mode = CAN_PORT_MODE_NORMAL;
static twai_port_switch_report_t s_rep;
static uint32_t s_seq;
static uint32_t s_discarded;
static int s_set_mode_calls;
static bool s_refuse;
static int64_t s_now;

#define QMAX 64
static struct { twai_message_t m; uint32_t tag; } s_q[QMAX];
static unsigned s_qn;

void twai_port_config_defaults(twai_port_config_t *cfg) { memset(cfg, 0, sizeof(*cfg)); }
esp_err_t twai_port_setup(const twai_port_config_t *cfg) { s_cfg = *cfg; return ESP_OK; }
bool twai_port_is_installed(void) { return s_installed; }
can_port_mode_t twai_port_mode(void) { return s_mode; }

esp_err_t twai_port_last_switch(twai_port_switch_report_t *out)
{
    *out = s_rep;
    return ESP_OK;
}

static esp_err_t fake_set_mode(void *ctx, can_port_mode_t mode)
{
    (void)ctx;
    s_set_mode_calls++;
    if (mode == s_mode)
    {
        return ESP_OK;
    }
    if (!s_installed)
    {
        s_mode = mode;
        return ESP_OK;
    }
    if (s_refuse)
    {
        return ESP_ERR_INVALID_STATE;
    }
    const bool to_lom = mode == CAN_PORT_MODE_LISTEN_ONLY;
    memset(&s_rep, 0, sizeof(s_rep));
    s_rep.seq = ++s_seq;
    s_rep.to_mode = mode;
    s_rep.result = ESP_OK;
    s_rep.rec_written = to_lom ? 128u : 0u;
    s_rep.rec_read_back = s_rep.rec_written;
    if (to_lom)
    {
        s_rep.held = s_qn;
        for (unsigned i = 0; i < s_qn; i++)
        {
            if (s_cfg.switch_discard != NULL)
            {
                s_cfg.switch_discard(NULL, &s_q[i].m, s_q[i].tag);
            }
        }
        s_discarded += s_qn;
        s_qn = 0;
    }
    s_mode = mode;
    return ESP_OK;
}

esp_err_t twai_port_send_tagged(const twai_message_t *m, TickType_t wait, uint32_t tag)
{
    (void)wait;
    if (s_qn >= QMAX || s_mode != CAN_PORT_MODE_NORMAL)
    {
        return ESP_FAIL;
    }
    s_q[s_qn].m = *m;
    s_q[s_qn].tag = tag;
    s_qn++;
    return ESP_OK;
}

esp_err_t twai_port_receive(twai_message_t *out, TickType_t wait)
{
    (void)out;
    s_now += (int64_t)wait * 1000;
    return ESP_ERR_TIMEOUT;
}

esp_err_t twai_port_status(can_port_status_t *out)
{
    memset(out, 0, sizeof(*out));
    out->tx_discarded_switch = s_discarded;
    return ESP_OK;
}

static esp_err_t op_err(void *ctx) { (void)ctx; return ESP_OK; }
static bool op_is_up(void *ctx) { (void)ctx; return s_installed; }
static esp_err_t op_configure(void *ctx, const can_port_settings_t *s) { (void)ctx; (void)s; return ESP_OK; }
static esp_err_t op_receive(void *ctx, twai_message_t *m, TickType_t w) { (void)ctx; return twai_port_receive(m, w); }
static esp_err_t op_send(void *ctx, const twai_message_t *m, TickType_t w) { (void)ctx; return twai_port_send_tagged(m, w, 0); }
static void op_service(void *ctx) { (void)ctx; }
static esp_err_t op_status(void *ctx, can_port_status_t *o) { (void)ctx; return twai_port_status(o); }
static void op_flush(void *ctx) { (void)ctx; }

const can_port_ops_t twai_port_ops = {
    .name = "fake twai_port", .up = op_err, .down = op_err, .is_up = op_is_up,
    .set_mode = fake_set_mode, .configure = op_configure, .receive = op_receive,
    .send = op_send, .service = op_service, .status = op_status, .flush_rx = op_flush,
};

/* The stock calls, as can_stock.c has them for what the app reaches. */
void can_init(uint8_t bitrate) { (void)bitrate; }
void can_set_bitrate(uint8_t rate) { (void)rate; }
bool can_is_enabled(void) { return s_on_bus; }
uint8_t can_is_silent(void) { return s_stock_silent; }
void can_flush_rx(void) {}
void can_set_silent(uint8_t flag)
{
    if (s_on_bus)
    {
        return;     /* can_stock.c: ignored while the bus is on */
    }
    s_stock_silent = flag;
}
void can_enable(void)
{
    if (s_on_bus)
    {
        return;
    }
    s_mode = s_stock_silent ? CAN_PORT_MODE_LISTEN_ONLY : CAN_PORT_MODE_NORMAL;
    s_installed = true;
    s_on_bus = true;
}
void can_disable(void)
{
    s_on_bus = false;
    s_installed = false;
}

/* The driver and the chip: idle. */
esp_err_t twai_get_status_info(twai_status_info_t *s)
{
    memset(s, 0, sizeof(*s));
    s->msgs_to_tx = s_qn;
    return ESP_OK;
}
esp_err_t twai_read_alerts(uint32_t *a, TickType_t w) { (void)w; *a = 0; return ESP_ERR_TIMEOUT; }
esp_err_t twai_reconfigure_alerts(uint32_t a, uint32_t *c) { (void)a; if (c) *c = 0; return ESP_OK; }
esp_err_t twai_transmit(const twai_message_t *m, TickType_t w) { (void)m; (void)w; return ESP_ERR_TIMEOUT; }
bool t19_ll_clock_on(void) { return false; }
void t19_ll_regs(t19_regs_t *out) { memset(out, 0, sizeof(*out)); }
uint32_t t19_ll_status(void) { return 0; }
bool t19_ll_enter_reset(bool if_ts0, uint32_t *b, uint32_t *a) { (void)if_ts0; *b = 0; *a = 0; return false; }
void t19_ll_leave_reset(bool listen_only) { (void)listen_only; }
void t19_ll_abort(uint32_t *b, uint32_t *a) { *b = 0; *a = 0; }
/* The probe's run (txabort), which no case here reaches. */
int twai_port_txabort_run(const txab_io_t *io, char *err, size_t errn)
{
    (void)io;
    snprintf(err, errn, "not in this test");
    return 501;
}
bool twai_port_ll_s3_stall_record(twai_port_s3_stall_record_t *out) { memset(out, 0, sizeof(*out)); return false; }
void twai_port_ll_s3_stall_record_clear(void) {}

static const esp_app_desc_t s_desc = { .version = "host" };
const esp_app_desc_t *esp_app_get_description(void) { return &s_desc; }
const char *esp_get_idf_version(void) { return "v5.4.2"; }
esp_reset_reason_t esp_reset_reason(void) { return 1; }
void esp_restart(void) { abort(); }
int esp_cpu_get_core_id(void) { return 1; }
int64_t esp_timer_get_time(void) { return ++s_now; }
void esp_rom_delay_us(uint32_t us) { s_now += us; }
void vTaskDelay(TickType_t t) { s_now += (int64_t)t * 1000; }
BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char *name, uint32_t stack,
                                   void *arg, unsigned prio, TaskHandle_t *out, BaseType_t core)
{
    (void)fn; (void)name; (void)stack; (void)arg; (void)prio; (void)out; (void)core;
    return 0;
}
TickType_t xTaskGetTickCount(void) { return (TickType_t)(s_now / 1000); }

/* ------------------------------------------------------------ the output -- */

static char s_out[16384];
static size_t s_out_n;

static bool emit(void *ctx, const char *s, size_t n)
{
    (void)ctx;
    if (s_out_n + n >= sizeof(s_out))
    {
        return false;
    }
    memcpy(s_out + s_out_n, s, n);
    s_out_n += n;
    s_out[s_out_n] = '\0';
    return true;
}

static void flush(void) {}

/* One command; its answer, one line ending in a newline. */
static const char *ask(const char *line)
{
    s_out_n = 0;
    s_out[0] = '\0';
    twai_port_t19_line(line, false);
    const size_t n = strlen(s_out);
    CHECK(n > 0 && s_out[n - 1] == '\n' && strchr(s_out, '\n') == s_out + n - 1);
    return s_out;
}

static bool has(const char *a, const char *s)
{
    if (strstr(a, s) != NULL)
    {
        return true;
    }
    printf("  missing %s in %s", s, a);
    return false;
}

/* The `switches` entries of a switchloop answer: [t, to_lom, sw, result,
 * held, rec_read_back, status_at_reset, head_aborted, driver_tx_stuck]. */
static int loop_entries(const char *a, long e[][9], int max)
{
    const char *p = strstr(a, "\"switches\":[");
    if (p == NULL)
    {
        return -1;
    }
    p += strlen("\"switches\":[");
    int n = 0;
    while (*p == '[' && n < max)
    {
        int used = 0;
        if (sscanf(p, "[%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld]%n", &e[n][0], &e[n][1],
                   &e[n][2], &e[n][3], &e[n][4], &e[n][5], &e[n][6], &e[n][7],
                   &e[n][8], &used) != 9)
        {
            return -1;
        }
        p += used;
        n++;
        if (*p == ',')
        {
            p++;
        }
    }
    return n;
}

/* ------------------------------------------------------------- the cases -- */

/* T14: `switch to=lom` reaches the kept-driver switch: the port is in
 * listen-only, by one set-mode call, and the answer says so. At 2026.11.27.6
 * the mode stayed normal and the report was seq 0. */
static void case_switch_to_lom(void)
{
    const int calls = s_set_mode_calls;
    const char *a = ask("switch to=lom");
    CHECK(s_mode == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(s_set_mode_calls == calls + 1);
    CHECK(has(a, "\"status\":200,"));
    CHECK(has(a, "\"set_mode\":0,\"mode\":\"listen_only\""));
    CHECK(has(a, "\"report\":{\"seq\":1,\"to\":\"listen_only\",\"result\":0,"));
    CHECK(has(a, "\"rec_written\":128,\"rec_read_back\":128,"));
}

/* Back to normal by the same call, from listen-only. */
static void case_switch_to_normal(void)
{
    const char *a = ask("switch to=normal");
    CHECK(s_mode == CAN_PORT_MODE_NORMAL);
    CHECK(has(a, "\"set_mode\":0,\"mode\":\"normal\""));
    CHECK(has(a, "\"report\":{\"seq\":2,\"to\":\"normal\",\"result\":0,"));
    CHECK(has(a, "\"rec_written\":0,"));
}

/* T17: frames queued before a switch to listen-only reach the hook once each,
 * in order, with their tags, and are counted. */
static void case_switch_held(void)
{
    const char *a = ask("switch to=lom n=3");
    CHECK(s_mode == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(has(a, "\"queued\":3,\"queue_refused\":0,"));
    CHECK(has(a, "\"held\":3,"));
    CHECK(has(a, "\"hook\":[[2045,28928],[2045,28929],[2045,28930]],\"hook_lost\":0,"));
    CHECK(has(a, "\"mtx_after\":0,\"tx_discarded_switch\":3,"));
    ask("switch to=normal");
    CHECK(s_mode == CAN_PORT_MODE_NORMAL);
}

/* switchloop: k switches, listen-only and normal by turns, each one a real
 * switch (a new report, REC by direction); an odd k is put back to normal. */
static void case_switchloop(void)
{
    const int calls = s_set_mode_calls;
    const uint32_t seq0 = s_seq;
    const char *a = ask("switchloop k=3 period_ms=2");
    CHECK(has(a, "\"status\":200,\"k\":3,"));
    long e[8][9];
    CHECK(loop_entries(a, e, 8) == 3);
    for (int i = 0; i < 3; i++)
    {
        const bool lom = (i % 2) == 0;
        CHECK(e[i][1] == (lom ? 1 : 0));
        CHECK(e[i][3] == 0);
        CHECK(e[i][5] == (lom ? 128 : 0));
    }
    CHECK(s_seq == seq0 + 4);               /* 3, and the one back */
    CHECK(s_set_mode_calls == calls + 4);
    CHECK(s_mode == CAN_PORT_MODE_NORMAL);
}

/* switchloop from listen-only starts in normal: its first switch is a real
 * one into listen-only. */
static void case_switchloop_from_lom(void)
{
    ask("switch to=lom");
    CHECK(s_mode == CAN_PORT_MODE_LISTEN_ONLY);
    const char *a = ask("switchloop k=2 period_ms=1");
    CHECK(has(a, "\"status\":200,\"k\":2,"));
    long e[8][9];
    CHECK(loop_entries(a, e, 8) == 2);
    CHECK(e[0][1] == 1 && e[0][3] == 0 && e[0][5] == 128);
    CHECK(e[1][1] == 0 && e[1][3] == 0 && e[1][5] == 0);
    CHECK(s_mode == CAN_PORT_MODE_NORMAL);
}

/* A set-mode call that runs no switch is not shown as the last report: its
 * entry is zeros with the call's return as the result. */
static void case_switchloop_refused(void)
{
    s_refuse = true;
    /* `switch` answers what the call returned, and the mode it left. */
    char want[64];
    snprintf(want, sizeof(want), "\"set_mode\":%d,\"mode\":\"normal\"",
             (int)ESP_ERR_INVALID_STATE);
    CHECK(has(ask("switch to=lom"), want));
    const char *a = ask("switchloop k=2 period_ms=1");
    s_refuse = false;
    long e[8][9];
    CHECK(loop_entries(a, e, 8) == 2);
    /* The refused switch to listen-only; then "to normal" in normal, which
     * is no switch and no error. */
    CHECK(e[0][3] == (long)ESP_ERR_INVALID_STATE);
    CHECK(e[1][3] == 0);
    for (int i = 0; i < 2; i++)
    {
        CHECK(e[i][4] == 0 && e[i][5] == 0);
    }
    CHECK(s_mode == CAN_PORT_MODE_NORMAL);
}

int main(void)
{
    twai_port_t19_cfg_t c = { .board_port = 0, .emit = emit, .flush = flush };
    CHECK(twai_port_t19_init(&c) == ESP_OK);
    CHECK(s_cfg.kept_driver_switch && s_cfg.mode == CAN_PORT_MODE_NORMAL);
    can_enable();   /* up in normal, as the bench leaves it after `info` */

    case_switch_to_lom();
    case_switch_to_normal();
    case_switch_held();
    case_switchloop();
    case_switchloop_from_lom();
    case_switchloop_refused();

    printf("test_t19: %s\n", s_fail ? "FAIL" : "all OK");
    return s_fail ? 1 : 0;
}
