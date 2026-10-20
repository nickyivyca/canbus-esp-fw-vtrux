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
} case_t;

static int64_t g_checks[SL_MAX_RX], g_errs[SL_MAX_ERR];

static void run_case(const case_t *c)
{
    char reason[160] = "";
    const char *cs = c->name;
    int64_t t_last;
    int sent_after_answer;
    setup();
    if (!live_and_answered(cs, &t_last, &sent_after_answer)) { teardown(); return; }

    if (c->tenth_us > 0) sl_silence_tenth_at(t_last, c->tenth_us);
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

static const case_t CASES[] = {
    /* name           retry_us  tenth_us   bus_off  region */
    { "fast",              300,        0,       0,   1 },
    { "before",              0,  300 * MS,      0,   1 },
    { "tie",                 0,  550 * MS,      0,   2 },
    { "slow",                0, 1500 * MS,      0,   3 },
    { "busoff_few",       1000,        0,       5,   3 },
    { "busoff_many",       300,        0,      12,   1 },
};

int main(int argc, char **argv)
{
    const size_t n = sizeof(CASES) / sizeof(CASES[0]);
    if (argc < 2)
    {
        printf("layer\n");
        for (size_t i = 0; i < n; i++) printf("%s\n", CASES[i].name);
        return 0;
    }
    bool found = false;
    if (strcmp(argv[1], "layer") == 0) { layer_case(); found = true; }
    for (size_t i = 0; i < n && !found; i++)
        if (strcmp(argv[1], CASES[i].name) == 0) { run_case(&CASES[i]); found = true; }
    if (!found) { printf("RESULT %s - VOID no such case\n", argv[1]); return 2; }
    printf("SUMMARY %s %d PASS, %d NOT-AS-SPECIFIED, %d VOID\n", argv[1], g_pass, g_bad, g_void);
    return (g_bad || g_void) ? 1 : 0;
}
