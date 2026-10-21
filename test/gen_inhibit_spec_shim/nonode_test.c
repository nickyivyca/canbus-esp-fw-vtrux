/*
 * Spec 12.4, "a bus with no other node, emulated" (added 2026-10-20, user),
 * judged on the mock-HAL shim: main/gen_inhibit.c, gen_inhibit_core.c,
 * gi_sched.c and can.c as shipped, against the implementor's fakes, with the
 * tester's fault behaviour on top (spec_layer.h). Gen-inhibit tester,
 * 2026-10-20.
 *
 * The row, as each case judges it (nonode_judge.c):
 *   R1  no 0x051 from the device after its answer to the last VCM command;
 *   R2  the trips are evaluated once per received frame or per 200 ms receive
 *       timeout: the latch lands at a check point (the tick right after a
 *       twai_receive() return), never between two;
 *   R3  the latch and its reason follow spec 7 for the injected timing: trip 6
 *       if its threshold (10 error frames within 10 s) is met at a check
 *       strictly earlier than the one that finds 0x051 stale (a tie is trip 2;
 *       reviewer, 2026-10-20), otherwise trip 2, 0.5-0.7 s after the last 0x051;
 *   R4  a stuck diag frame does not block the worker (receive keeps
 *       returning), the status page (it answers) or the latch;
 *   R5  when traffic returns without a key-on the device stays latched and the
 *       stuck frame completes, and its diag pages resume;
 *   R6  a key-on then clears the latch (7.1);
 *   R7  bus-off: the device starts no recovery and stays off the bus until a
 *       re-arm (or reboot), whatever traffic and key cycles come (spec 7).
 * And on the layer (reviewer, 2026-10-20): the device's err_n moves with the
 * layer's per-retry count, so trip 6 can be reached at all; the layer injects
 * no error before a frame waits.
 *
 * Expected values come from the spec and from what the layer injected and
 * observed, never from the product source. One line per verdict:
 *   RESULT <case> <req> PASS|NOT-AS-SPECIFIED|VOID <detail>
 * One case per process (`nonode_test <case>`; no argument lists the cases),
 * because the firmware's statics outlive ft_reset() and trip 6's error history
 * is one of them (only a key-on clears it, spec 7). Exit 0 only when every
 * line is PASS.
 */
#include "fake_twai.h"
#include "gen_inhibit.h"
#include "driver/twai.h"
#include "esp_log.h"
#include "can.h"
#include "spec_layer.h"
#include "nonode_judge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MS       1000LL
#define STEP_US  100            /* the test's polling step */
#define WATCH_US (2000 * MS)    /* how long the silence is watched */

static int g_pass, g_bad, g_void;

static void report(const char *cs, const char *req, nn_verdict_t v)
{
    printf("RESULT %s %s %s %s\n", cs, req, v.v, v.detail);
    if (!strcmp(v.v, "PASS")) g_pass++;
    else if (!strcmp(v.v, "VOID")) g_void++;
    else g_bad++;
    fflush(stdout);
}

/* ------------------------------------------------------- the status page -- */

static const char *page(void)
{
    static char buf[8192];
    buf[0] = 0;
    gen_inhibit_get_stats_json(buf, sizeof(buf));
    return buf;
}

static bool latched(const char *p)  { return strstr(p, "\"abort_latched\":true") != NULL; }
static bool is_live(const char *p)  { return strstr(p, "\"inhibit_live\":true") != NULL; }

static long page_num(const char *p, const char *key)
{
    char k[64];
    snprintf(k, sizeof(k), "\"%s\":", key);
    const char *q = strstr(p, k);
    return q ? strtol(q + strlen(k), NULL, 10) : -1;
}

static void page_str(const char *p, const char *key, char *out, int n)
{
    char k[64];
    snprintf(k, sizeof(k), "\"%s\":\"", key);
    const char *q = strstr(p, k);
    out[0] = 0;
    if (!q) { snprintf(out, (size_t)n, "<no %s>", key); return; }
    q += strlen(k);
    int i = 0;
    while (q[i] && q[i] != '"' && i < n - 1) { out[i] = q[i]; i++; }
    out[i] = 0;
}

/* ----------------------------------------------------------- the traffic -- */
/* The interlock set, encoded per epri-pt-bus.dbc (run_spec_shim.py checks each
 * payload against the DBC with cantools): key on (0x592 B0 bit 4), contactors
 * closed (0x440 bcm_mainc_stat 11), SoC 50 %, VCM fault flag not 0xCA, shift
 * position set, and a VCM command at its engine-off null with zero torque. */
static const uint8_t KEY_ON[8]  = { 0x10 };
static const uint8_t KEY_OFF[8] = { 0x00 };
static const uint8_t CONT[8]    = { 11 << 2 };
static const uint8_t SOC[8]     = { 0x4E, 0x20 };
static const uint8_t FLT[8]     = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
static const uint8_t SHF[8]     = { 0, 0, 0, 0, 0, 0, 2 << 4 };
static const uint8_t VCM[6]     = { 0x08, 0x00, 0x80, 0xFF, 0x7F, 0x00 };

static uint8_t g_ctr;

static void feed(uint32_t id, const uint8_t *d, uint8_t dlc, int64_t us)
{
    ft_deliver(id, d, dlc);
    ft_run(us);
    if (ft_rx_pending() > 0 && ft_in_receive()) ft_run(0);
}

static void round_of(const uint8_t *key, int64_t us_each)
{
    uint8_t cmd[6];
    feed(0x592, key, 8, us_each);
    feed(0x440, CONT, 8, us_each);
    feed(0x411, SOC, 8, us_each);
    feed(0x617, FLT, 8, us_each);
    feed(0x639, SHF, 8, us_each);
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(g_ctr++ & 0x0F);
    feed(0x051, cmd, 6, us_each);
}

static void traffic(const uint8_t *key, int64_t us)
{
    const int64_t end = ft_now() + us;
    while (ft_now() < end) round_of(key, 1500);
}

static int diag_on_wire(void)
{
    return ft_wire_count_id(0x7F1) + ft_wire_count_id(0x7F2) + ft_wire_count_id(0x7F3) + ft_wire_count_id(0x7F8);
}

static void setup(void)
{
    ft_reset();
    sl_reset(NULL);
    ml_clear();
    gen_inhibit_init(CAN_500K, 0);
    ft_start_worker();
    ft_run(1000);
}

static void teardown(void)
{
    static const uint8_t PAD[8] = { 0 };
    gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
    ft_deliver(0x7FF, PAD, 8);
    ft_run(1000);
    ft_run(100000);
    ft_stop_worker();
}

/* Live on the interlock set, then the last VCM command, answered on a bus that
 * still acknowledges. Returns false (and reports VOID) if that did not happen. */
static bool live_and_answered(const char *cs, int64_t *t_last, int *sent_after_answer)
{
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    traffic(KEY_ON, 1500 * MS);
    if (getenv("NN_PAGE")) printf("PAGE %s\n", page());
    if (!is_live(page()))
    {
        nn_verdict_t v = { "VOID", "" };
        snprintf(v.detail, sizeof(v.detail), "never went live on the interlock set");
        report(cs, "setup", v);
        return false;
    }
    const int sent_before = ft_sent_count_id(0x051), wire_before = ft_wire_count_id(0x051);
    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(g_ctr++ & 0x0F);
    ft_deliver(0x051, cmd, 6);
    for (int i = 0; i < 20 && ft_wire_count_id(0x051) == wire_before; i++) ft_run(100);
    *t_last = ft_last_rx_time();
    *sent_after_answer = ft_sent_count();
    if (ft_wire_count_id(0x051) != wire_before + 1 || ft_sent_count_id(0x051) != sent_before + 1)
    {
        nn_verdict_t v = { "VOID", "" };
        snprintf(v.detail, sizeof(v.detail), "the last command's answer did not complete (%d sent, %d on the wire)",
                 ft_sent_count_id(0x051) - sent_before, ft_wire_count_id(0x051) - wire_before);
        report(cs, "setup", v);
        return false;
    }
    return true;
}

/* ------------------------------------------------------------ the cases -- */

typedef struct
{
    const char *name;
    int64_t retry_us;        /* > 0: a fixed retry period */
    int64_t tenth_us;        /* > 0: the 10th error this long after the last 0x051 */
    int     bus_off_after;   /* > 0: bus-off once this many errors were injected */
    int     region;          /* nn_obs_t.region */
    int64_t passive_us;      /* > 0: retry_us until TEC 128, then this (the bench's pattern) */
} case_t;

static int64_t g_checks[SL_MAX_RX], g_errs[SL_MAX_ERR];

/* Whether the errors injected so far follow the bench's pattern: after the first,
 * 15 gaps of active_us (TEC 8 to 120), then passive_us; at least 30 errors. */
static nn_verdict_t bench_pattern(int64_t active_us, int64_t passive_us)
{
    nn_verdict_t v;
    const int n = sl_err_count() < SL_MAX_ERR ? sl_err_count() : SL_MAX_ERR;
    int bad = 0, first_bad = -1;
    for (int k = 1; k < n; k++)
        if (sl_err_time(k) - sl_err_time(k - 1) != (k < 16 ? active_us : passive_us))
        {
            bad++;
            if (first_bad < 0) first_bad = k;
        }
    snprintf(v.detail, sizeof(v.detail), "%d errors; gaps %lld us to TEC 128 then %lld us: %d off (first at %d); TEC %u",
             sl_err_count(), (long long)active_us, (long long)passive_us, bad, first_bad, sl_tec());
    v.v = (n >= 30 && bad == 0 && sl_tec() == 128) ? "PASS" : "NOT-AS-SPECIFIED";
    return v;
}

static void run_case(const case_t *c)
{
    char reason[160] = "";
    const char *cs = c->name;
    int64_t t_last;
    int sent_after_answer;
    setup();
    if (!live_and_answered(cs, &t_last, &sent_after_answer)) { teardown(); return; }

    if (c->tenth_us > 0) sl_silence_tenth_at(t_last, c->tenth_us);
    else if (c->passive_us > 0) sl_silence_measured(c->retry_us, c->passive_us);
    else sl_silence(c->retry_us);
    const int rx_at_silence = sl_rx_count();

    int64_t t_latch = -1, t_unlatched = ft_now(), gap_max = 0, t_prev = ft_now();
    long err_n_max = -1;
    bool bus_off_done = false;
    while (ft_now() < t_last + WATCH_US)
    {
        ft_run(STEP_US);
        if (c->bus_off_after > 0 && !bus_off_done && sl_err_count() >= c->bus_off_after)
        {
            sl_bus_off();
            bus_off_done = true;
        }
        const char *p = page();
        if (p[0])
        {
            if (ft_now() - t_prev > gap_max) gap_max = ft_now() - t_prev;
            t_prev = ft_now();
        }
        const long e = page_num(p, "err_n");
        if (e > err_n_max) err_n_max = e;
        if (t_latch < 0 && latched(p))
        {
            t_latch = ft_now();
            page_str(p, "abort_reason", reason, sizeof(reason));
        }
        else if (t_latch < 0)
        {
            t_unlatched = ft_now();
        }
    }

    int late051 = 0;
    for (int i = sent_after_answer; i < ft_sent_count(); i++)
        if (ft_sent(i)->id == 0x051) late051++;
    report(cs, "R1", nn_r1_no_051(late051));

    nn_obs_t o = { 0 };
    o.t_last = t_last;
    o.checks = g_checks;
    for (int i = 0; i < sl_rx_count(); i++)
        if (sl_rx(i)->t > t_last) g_checks[o.n_checks++] = sl_rx(i)->t;
    for (int i = rx_at_silence; i < sl_rx_count(); i++) o.frames_in_silence += sl_rx(i)->ok;
    o.errs = g_errs;
    for (int k = 0; k < sl_err_count() && k < SL_MAX_ERR; k++) g_errs[o.n_errs++] = sl_err_time(k);
    o.region = c->region;
    o.t_unlatched = t_unlatched;
    o.t_latch = t_latch;
    o.reason = reason;

    nn_verdict_t why;
    const nn_expect_t e = nn_expect(&o, &why);
    if (!e.usable) report(cs, "R2/R3", why);
    else
    {
        report(cs, "R2", nn_r2_at_check(&o));
        report(cs, "R3", nn_r3_trip(&o, &e));
    }
    if (sl_err_count() > 0)
    {
        report(cs, "layer", nn_layer_reaches(err_n_max, sl_err_count()));
        report(cs, "layer", nn_layer_waits(sl_err_time(0),
                                           ft_sent_count() > sent_after_answer ? ft_sent(sent_after_answer)->t_queued : -1));
    }
    report(cs, "R4", nn_r4_not_blocked(sl_rx_count() - rx_at_silence, gap_max, ft_sent_count() - ft_wire_count()));
    if (c->passive_us > 0) report(cs, "layer", bench_pattern(c->retry_us, c->passive_us));

    if (c->bus_off_after > 0)
    {
        const int dropped0 = ft_rx_dropped_off(), installs0 = ft_install_count();
        traffic(KEY_ON, 500 * MS);
        traffic(KEY_OFF, 300 * MS);
        traffic(KEY_ON, 500 * MS);
        report(cs, "R7", nn_r7_stays_off(sl_recovery_calls(), latched(page()), ft_rx_dropped_off() - dropped0,
                                         ft_install_count() - installs0));
        /* spec 7: a re-arm goes live only once the bus has been free of a qualifying
         * burst for 10 s, so the re-arm comes more than 10 s after the last error */
        gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
        while (ft_now() < sl_err_time(sl_err_count() - 1) + NN_TRIP6_WIN + 500 * MS) ft_run(100 * MS);
        gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
        sl_nodes_back();
        const int rx0 = sl_rx_count();
        traffic(KEY_ON, 1000 * MS);
        int frames = 0;
        for (int i = rx0; i < sl_rx_count(); i++) frames += sl_rx(i)->ok;
        report(cs, "R7", nn_r7_rearm(frames, latched(page())));
        teardown();
        return;
    }

    const ft_frame_t stuck = *ft_sent(sent_after_answer);   /* the first frame queued in the silence */
    const int diag0 = diag_on_wire(), sent051 = ft_sent_count_id(0x051);
    sl_nodes_back();
    traffic(KEY_ON, 1000 * MS);
    bool completed = false;
    for (int i = 0; i < ft_wire_count(); i++)
        if (ft_wire(i)->t_queued == stuck.t_queued && ft_wire(i)->id == stuck.id) completed = true;
    report(cs, "R5", nn_r5_back(latched(page()), completed, diag_on_wire() - diag0,
                                ft_sent_count_id(0x051) - sent051));

    traffic(KEY_OFF, 300 * MS);
    traffic(KEY_ON, 300 * MS);
    report(cs, "R6", nn_r6_key_on(latched(page())));
    teardown();
}

/*
 * THE LAYER ON ITS OWN, on a live device: no error before a frame waits; one
 * per period after; bus_error_count and TEC as modelled; alerts latched and
 * cleared on read, delivered only where enabled; bus-off; the recovery count.
 * The device is a bystander here, so nothing about it is judged.
 */
static void layer_case(void)
{
    const char *cs = "layer";
    int64_t t_last;
    int sent_after_answer;
    nn_verdict_t v;
    setup();
    if (!live_and_answered(cs, &t_last, &sent_after_answer)) { teardown(); return; }
    twai_reconfigure_alerts(sl_alerts_enabled() | SL_ALERT_BUS_ERROR | SL_ALERT_ERR_PASS | SL_ALERT_BUS_OFF, NULL);
    uint32_t a = 0;
    while (twai_read_alerts(&a, 0) == ESP_OK) {}            /* clear what is pending */
    sl_silence(1000);
    const int64_t t0 = ft_now();
    while (ft_now() < t0 + 600 * MS && ft_sent_count() == sent_after_answer) ft_run(STEP_US);
    const int64_t t_wait = ft_sent_count() > sent_after_answer ? ft_sent(sent_after_answer)->t_queued : -1;
    const int before_wait = sl_err_count();
    ft_run(20 * MS);
    twai_status_info_t s;
    twai_get_status_info(&s);
    const int n = sl_err_count();
    bool spaced = n >= 10;
    for (int k = 1; k < n && k < SL_MAX_ERR; k++) spaced = spaced && sl_err_time(k) - sl_err_time(k - 1) == 1000;

    snprintf(v.detail, sizeof(v.detail), "a frame waiting from %lld us; %d error(s) before it, %d after 20 ms, "
             "1 ms apart %d; bus_error_count %u; TEC %u", (long long)t_wait, before_wait, n, spaced,
             s.bus_error_count, s.tx_error_counter);
    v.v = (t_wait >= 0 && before_wait == 0 && n >= 15 && n <= 25 && spaced && s.bus_error_count == (uint32_t)n &&
           s.tx_error_counter == 128) ? "PASS" : "NOT-AS-SPECIFIED";
    report(cs, "errors", v);

    a = 0;
    const esp_err_t r1 = twai_read_alerts(&a, 0);
    uint32_t a2 = 0;
    const esp_err_t r2 = twai_read_alerts(&a2, 0);
    snprintf(v.detail, sizeof(v.detail), "first read %d 0x%X, second read %d 0x%X", r1, a, r2, a2 & (SL_ALERT_BUS_ERROR |
             SL_ALERT_ERR_PASS));
    v.v = (r1 == ESP_OK && (a & SL_ALERT_BUS_ERROR) && (a & SL_ALERT_ERR_PASS) &&
           !(a2 & (SL_ALERT_BUS_ERROR | SL_ALERT_ERR_PASS))) ? "PASS" : "NOT-AS-SPECIFIED";
    report(cs, "alerts", v);

    sl_bus_off();
    const int n_off = sl_err_count();
    ft_run(50 * MS);
    twai_get_status_info(&s);
    a = 0;
    twai_read_alerts(&a, 0);
    snprintf(v.detail, sizeof(v.detail), "state %d, errors +%d after bus-off, TEC %u, BUS_OFF alert %d",
             (int)s.state, sl_err_count() - n_off, s.tx_error_counter, !!(a & SL_ALERT_BUS_OFF));
    v.v = (s.state == TWAI_STATE_BUS_OFF && sl_err_count() == n_off && s.tx_error_counter == 256 &&
           (a & SL_ALERT_BUS_OFF)) ? "PASS" : "NOT-AS-SPECIFIED";
    report(cs, "busoff", v);

    const int rc0 = sl_recovery_calls();
    twai_initiate_recovery();
    snprintf(v.detail, sizeof(v.detail), "one call counted as %d", sl_recovery_calls() - rc0);
    v.v = sl_recovery_calls() - rc0 == 1 ? "PASS" : "NOT-AS-SPECIFIED";
    report(cs, "recovery", v);
    teardown();
}

/* The layer's replay of the bench's pattern, on its own: from the frame's wait,
 * 16 errors 246 us apart (TEC to 128), then 262 us apart, each a bus error. */
static void layer_bench_case(void)
{
    const char *cs = "layer_bench";
    int64_t t_last;
    int sent_after_answer;
    nn_verdict_t v;
    setup();
    if (!live_and_answered(cs, &t_last, &sent_after_answer)) { teardown(); return; }
    sl_silence_measured(246, 262);
    const int64_t t0 = ft_now();
    while (ft_now() < t0 + 600 * MS && ft_sent_count() == sent_after_answer) ft_run(STEP_US);
    const int64_t t_wait = ft_sent_count() > sent_after_answer ? ft_sent(sent_after_answer)->t_queued : -1;
    const int before_wait = sl_err_count();
    ft_run(20 * MS);
    twai_status_info_t s;
    twai_get_status_info(&s);
    v = bench_pattern(246, 262);
    const int n = sl_err_count();
    const bool count_ok = n >= 70 && n <= 85;   /* 16 x 246 us, then ~16 ms / 262 us */
    snprintf(v.detail + strlen(v.detail), sizeof(v.detail) - strlen(v.detail),
             "; waiting from %lld us, %d before; bus_error_count %u, REC %u", (long long)t_wait, before_wait,
             s.bus_error_count, s.rx_error_counter);
    if (!(t_wait >= 0 && before_wait == 0 && count_ok && s.bus_error_count == (uint32_t)n &&
          s.tx_error_counter == 128 && s.rx_error_counter == 0 && s.state == TWAI_STATE_RUNNING))
        v.v = "NOT-AS-SPECIFIED";
    report(cs, "pattern", v);
    teardown();
}

/* ------------------------------------------------- the answer-stuck case -- */

/* A number inside the page's "sched" block, before its "cls" block. */
static long sched_num(const char *p, const char *key)
{
    const char *s = strstr(p, "\"sched\":{");
    if (!s) return -1;
    const char *end = strstr(s, "\"cls\":");
    char k[64];
    snprintf(k, sizeof(k), "\"%s\":", key);
    const char *q = strstr(s, k);
    if (!q || (end && q > end)) return -1;
    return strtol(q + strlen(k), NULL, 10);
}

/* A number in one class of "sched"."cls". */
static long cls_num(const char *p, const char *cls, const char *key)
{
    const char *s = strstr(p, "\"cls\":");
    if (!s) return -1;
    char c[64], k[64];
    snprintf(c, sizeof(c), "\"%s\":{", cls);
    s = strstr(s, c);
    if (!s) return -1;
    const char *end = strchr(s + strlen(c), '}');
    snprintf(k, sizeof(k), "\"%s\":", key);
    const char *q = strstr(s, k);
    if (!q || (end && q > end)) return -1;
    return strtol(q + strlen(k), NULL, 10);
}

/* Spec 5.2 item 6 on one page: (balance ok, equality ok or not checkable). */
static void identities(const char *p, int *bal_bad, char *bal_first, int *eq_checked, int *eq_bad, char *eq_first)
{
    static const char *CLS[] = { "inhibit", "probe", "telemetry" };
    for (int i = 0; i < 3; i++)
    {
        const char *c = CLS[i];
        const long l = cls_num(p, c, "queued") + cls_num(p, c, "carried_in");
        const long r = cls_num(p, c, "sent") + cls_num(p, c, "dropped") + cls_num(p, c, "withdrawn") +
                       cls_num(p, c, "depth") + cls_num(p, c, "held");
        if (l != r || cls_num(p, c, "queued") < 0)
        {
            if (!(*bal_bad)++)
                snprintf(bal_first, 160, "%s: queued+carried_in %ld, sent+dropped+withdrawn+depth+held %ld at %lld us",
                         c, l, r, (long long)ft_now());
        }
    }
    if (cls_num(p, "inhibit", "held") == 0)     /* not mid-withdrawal (5.2 item 6) */
    {
        (*eq_checked)++;
        const long l = cls_num(p, "inhibit", "withdrawn") + cls_num(p, "inhibit", "dropped");
        const long r = sched_num(p, "skipped") + sched_num(p, "standdown");
        if (l != r || l < 0)
            if (!(*eq_bad)++)
                snprintf(eq_first, 160, "withdrawn+dropped %ld, skipped+standdown %ld at %lld us", l, r,
                         (long long)ft_now());
    }
}

/*
 * The nodes go after the last VCM command but before the device's answer
 * completes, so the answer is the stuck frame when the latch lands (reviewer,
 * 2026-10-20). Spec 5.2 item 5: a section 7 trip that lands while an answer is
 * still in the controller withdraws it as a stand-down, never a skip, never
 * sent; item 6: the counts balance on every read, and the stand-down ends the
 * wait for that answer, so later diag pages are not held behind it.
 *
 * THE ABORT IS THE LAYER'S MODEL (spec_layer.h): the answer is held in the
 * controller as a frame between retries, awaiting arbitration (TS clear), where
 * the controller's abort removes it (ft_stall_id); every other frame retries as
 * in the other cases. Retries every 150 ms put the 10th error at about +1.5 s,
 * so the expected latch is trip 2 at the stale check (0.5-0.7 s).
 */
static void answer_case(void)
{
    const char *cs = "answer_stuck";
    char reason[160] = "", bal_first[160] = "", eq_first[160] = "";
    setup();
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    traffic(KEY_ON, 1500 * MS);
    if (!is_live(page()))
    {
        nn_verdict_t v = { "VOID", "never went live on the interlock set" };
        report(cs, "setup", v);
        teardown();
        return;
    }
    const char *p0 = page();
    const long sd0 = sched_num(p0, "standdown"), sk0 = sched_num(p0, "skipped");
    const int wire_at_silence = ft_wire_count();

    ft_stall_id(0x051, 1);                      /* the answer: never on the wire, abortable */
    sl_silence(150 * MS);
    const int sent051 = ft_sent_count_id(0x051);
    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(g_ctr++ & 0x0F);
    ft_deliver(0x051, cmd, 6);
    for (int i = 0; i < 20 && ft_sent_count_id(0x051) == sent051; i++) ft_run(100);
    const int64_t t_last = ft_last_rx_time();
    if (ft_sent_count_id(0x051) != sent051 + 1)
    {
        nn_verdict_t v = { "VOID", "" };
        snprintf(v.detail, sizeof(v.detail), "the last command was answered %d times, not once",
                 ft_sent_count_id(0x051) - sent051);
        report(cs, "setup", v);
        teardown();
        return;
    }
    int answer = -1;
    for (int i = ft_sent_count() - 1; i >= 0 && answer < 0; i--)
        if (ft_sent(i)->id == 0x051) answer = i;
    const int64_t answer_t = ft_sent(answer)->t_queued;
    const int sent_after_answer = answer + 1;
    const int rx_at_silence = sl_rx_count();

    int64_t t_latch = -1, t_unlatched = ft_now(), gap_max = 0, t_prev = ft_now();
    long err_n_max = -1;
    int reads = 0, bal_bad = 0, eq_checked = 0, eq_bad = 0;
    while (ft_now() < t_last + WATCH_US)
    {
        ft_run(STEP_US);
        const char *p = page();
        if (p[0])
        {
            if (ft_now() - t_prev > gap_max) gap_max = ft_now() - t_prev;
            t_prev = ft_now();
        }
        reads++;
        identities(p, &bal_bad, bal_first, &eq_checked, &eq_bad, eq_first);
        const long e = page_num(p, "err_n");
        if (e > err_n_max) err_n_max = e;
        if (t_latch < 0 && latched(p))
        {
            t_latch = ft_now();
            page_str(p, "abort_reason", reason, sizeof(reason));
        }
        else if (t_latch < 0)
        {
            t_unlatched = ft_now();
        }
    }
    const char *pe = page();
    const long sd1 = sched_num(pe, "standdown"), sk1 = sched_num(pe, "skipped");

    int late051 = 0;
    for (int i = sent_after_answer; i < ft_sent_count(); i++)
        if (ft_sent(i)->id == 0x051) late051++;
    report(cs, "R1", nn_r1_no_051(late051));

    nn_obs_t o = { 0 };
    o.t_last = t_last;
    o.checks = g_checks;
    for (int i = 0; i < sl_rx_count(); i++)
        if (sl_rx(i)->t > t_last) g_checks[o.n_checks++] = sl_rx(i)->t;
    for (int i = rx_at_silence; i < sl_rx_count(); i++) o.frames_in_silence += sl_rx(i)->ok;
    o.errs = g_errs;
    for (int k = 0; k < sl_err_count() && k < SL_MAX_ERR; k++) g_errs[o.n_errs++] = sl_err_time(k);
    o.region = 3;
    o.t_unlatched = t_unlatched;
    o.t_latch = t_latch;
    o.reason = reason;
    nn_verdict_t why;
    const nn_expect_t e = nn_expect(&o, &why);
    if (!e.usable) report(cs, "R2/R3", why);
    else
    {
        report(cs, "R2", nn_r2_at_check(&o));
        report(cs, "R3", nn_r3_trip(&o, &e));
    }
    report(cs, "layer", nn_layer_reaches(err_n_max, sl_err_count()));
    report(cs, "A2", nn_a2_standdown(sd1 - sd0, sk1 - sk0));
    report(cs, "A3", nn_a3_balance(reads, bal_bad, bal_first));
    report(cs, "A4", nn_a4_equality(eq_checked, eq_bad, eq_first));
    report(cs, "R4", nn_r4_not_blocked(sl_rx_count() - rx_at_silence, gap_max, ft_sent_count() - ft_wire_count()));

    /* the diag frames handed to the driver after the latch, and whether any frame completed in the silence */
    int first_diag = -1, diag_after = 0;
    for (int i = sent_after_answer; i < ft_sent_count(); i++)
    {
        const uint32_t id = ft_sent(i)->id;
        if ((id == 0x7F1 || id == 0x7F2 || id == 0x7F3 || id == 0x7F8) && t_latch >= 0 &&
            ft_sent(i)->t_queued >= t_unlatched - NN_SLACK_US)
        {
            diag_after++;
            if (first_diag < 0) first_diag = i;
        }
    }
    const bool done_in_silence = ft_wire_count() != wire_at_silence;
    const ft_frame_t stuck = first_diag >= 0 ? *ft_sent(first_diag) : *ft_sent(answer);
    const int diag0 = diag_on_wire(), q051 = ft_sent_count_id(0x051);

    sl_nodes_back();
    const int64_t t_return = ft_now();
    traffic(KEY_ON, 1000 * MS);
    bool stuck_done = false, answer_on_wire = false;
    int wire051_after = 0;
    for (int i = 0; i < ft_wire_count(); i++)
    {
        const ft_frame_t *w = ft_wire(i);
        if (first_diag >= 0 && w->t_queued == stuck.t_queued && w->id == stuck.id) stuck_done = true;
        if (w->id == 0x051 && w->t_queued == answer_t) answer_on_wire = true;
        if (w->id == 0x051 && w->t_queued >= t_return) wire051_after++;
    }
    report(cs, "A5", nn_a5_not_held(diag_after, done_in_silence, stuck_done));
    report(cs, "R5", nn_r5_back(latched(page()), stuck_done, diag_on_wire() - diag0, ft_sent_count_id(0x051) - q051));
    traffic(KEY_OFF, 300 * MS);
    traffic(KEY_ON, 300 * MS);
    report(cs, "R6", nn_r6_key_on(latched(page())));
    for (int i = 0; i < ft_wire_count(); i++)
        if (ft_wire(i)->id == 0x051 && ft_wire(i)->t_queued == answer_t) answer_on_wire = true;
    report(cs, "A1", nn_a1_answer_never_sent(answer_on_wire, wire051_after));
    teardown();
}

static const case_t CASES[] = {
    /* name           retry_us  tenth_us   bus_off  region */
    { "fast",              300,        0,       0,   1 },
    { "before",              0,  300 * MS,      0,   1 },
    { "tie",                 0,  550 * MS,      0,   2 },
    { "slow",                0, 1500 * MS,      0,   3 },
    { "busoff_few",       1000,        0,       5,   3 },
    { "busoff_many",       300,        0,      12,   1 },
    /* the bench's measured pattern (nonode_bench.py 2026-10-20 17:04): arm 1, and arms 2 and 3 */
    { "bench_262",         246,        0,       0,   1, 262 },
    { "bench_274",         258,        0,       0,   1, 274 },
};

int main(int argc, char **argv)
{
    const size_t n = sizeof(CASES) / sizeof(CASES[0]);
    if (argc < 2)
    {
        printf("layer\n");
        printf("layer_bench\n");
        for (size_t i = 0; i < n; i++) printf("%s\n", CASES[i].name);
        printf("answer_stuck\n");
        return 0;
    }
    bool found = false;
    if (strcmp(argv[1], "layer") == 0) { layer_case(); found = true; }
    if (strcmp(argv[1], "layer_bench") == 0) { layer_bench_case(); found = true; }
    if (strcmp(argv[1], "answer_stuck") == 0) { answer_case(); found = true; }
    for (size_t i = 0; i < n && !found; i++)
        if (strcmp(argv[1], CASES[i].name) == 0) { run_case(&CASES[i]); found = true; }
    if (!found) { printf("RESULT %s - VOID no such case\n", argv[1]); return 2; }
    printf("SUMMARY %s %d PASS, %d NOT-AS-SPECIFIED, %d VOID\n", argv[1], g_pass, g_bad, g_void);
    return (g_bad || g_void) ? 1 : 0;
}
