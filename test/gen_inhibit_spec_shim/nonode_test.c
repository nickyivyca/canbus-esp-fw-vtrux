/*
 * Spec 12.4, "a bus with no other node, emulated" (added 2026-10-20, user),
 * judged on the mock-HAL shim: main/gen_inhibit.c, gen_inhibit_core.c,
 * gi_sched.c and can.c as shipped, against the implementor's fakes, with the
 * tester's fault behaviour on top (spec_layer.h). Gen-inhibit tester,
 * 2026-10-20.
 *
 * The row, as each case judges it:
 *   R1  no 0x051 from the device after its answer to the last VCM command;
 *   R2  the trips are evaluated once per received frame or per 200 ms receive
 *       timeout: the latch lands at a check point (the tick right after a
 *       twai_receive() return), never between two;
 *   R3  the latch and its reason follow spec 7 for the injected timing: trip 6
 *       if its threshold (10 error frames within 10 s) is met at an EARLIER
 *       check than the one that finds 0x051 stale (strictly earlier: a tie is
 *       trip 2; reviewer, 2026-10-20), otherwise trip 2, 0.5-0.7 s after the
 *       last 0x051;
 *   R4  a stuck diag frame does not block the worker (receive keeps
 *       returning), the status page (it answers) or the latch;
 *   R5  when traffic returns without a key-on the device stays latched and the
 *       stuck frame completes, and its diag pages resume;
 *   R6  a key-on then clears the latch (7.1);
 *   R7  bus-off: the device starts no recovery and stays off the bus until a
 *       re-arm (or reboot), whatever traffic and key cycles come (spec 7).
 * And one check on the layer itself (reviewer, 2026-10-20): the device's err_n
 * moves with the layer's per-retry count, so trip 6 can be reached at all.
 *
 * Expected values come from the spec and from what the layer injected and
 * observed, never from the product source. Every line is
 *   RESULT <case> <req> PASS|NOT-AS-SPECIFIED|VOID <detail>
 * VOID: the case did not set up what it meant to (a harness problem, never a
 * pass). Exit 0 only when every line is PASS.
 */
#include "fake_twai.h"
#include "gen_inhibit.h"
#include "driver/twai.h"
#include "esp_log.h"
#include "can.h"
#include "spec_layer.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MS 1000LL
#define FRESH_US      (500 * MS)    /* spec 7: 0x051's freshness window */
#define TRIP2_MAX_US  (700 * MS)    /* spec 12.4 row: 0.5-0.7 s after the last 0x051 */
#define EDGE_US       (20 * MS)     /* keep check points this far from the 0.5 s edge */
#define TRIP6_N       10            /* spec 7: 10 error frames within 10 s */
#define TRIP6_WIN_US  (10000 * MS)
#define STEP_US       100           /* the test's polling step, us */
#define SLACK_US      50            /* a check point may share the instant of the last poll before it */

static int g_pass, g_bad, g_void;

static void result(const char *cs, const char *req, const char *v, const char *fmt, ...)
    __attribute__((format(printf, 4, 5)));

#include <stdarg.h>
static void result(const char *cs, const char *req, const char *v, const char *fmt, ...)
{
    va_list ap;
    printf("RESULT %s %s %s ", cs, req, v);
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
    if (!strcmp(v, "PASS")) g_pass++;
    else if (!strcmp(v, "VOID")) g_void++;
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
/* The interlock set, encoded per epri-pt-bus.dbc (run.sh checks each payload
 * against the DBC with cantools): key on (0x592 B0 bit 4), contactors closed
 * (0x440 bcm_mainc_stat 11), SoC 50 %, VCM fault flag not 0xCA, shift position
 * set, and a VCM command at its engine-off null with zero torque. */
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

/* One round of the interlock set, the VCM command last; `key` is 0x592. */
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

/* ------------------------------------------------------------ one case -- */

typedef struct
{
    const char *name;
    int64_t retry_us;        /* > 0: a fixed retry period */
    int64_t tenth_us;        /* > 0: the 10th error this long after the last 0x051 */
    int     bus_off_after;   /* > 0: bus-off once this many errors were injected */
    int     expect_region;   /* where the 10th error must land, by check points:
                                0 any, 1 before the stale check's predecessor,
                                2 between the stale check's predecessor and it
                                (the tie), 3 after the stale check */
} case_t;

static int first_check_after(int64_t t)
{
    for (int i = 0; i < sl_rx_count(); i++)
        if (sl_rx(i)->t > t) return i;
    return -1;
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

static void run_case(const case_t *c)
{
    char why[512], reason[160];
    const char *cs = c->name;
    setup();

    /* live, on the interlock set */
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    traffic(KEY_ON, 1500 * MS);
    if (!is_live(page()))
    {
        result(cs, "setup", "VOID", "never went live on the interlock set: %.200s", page());
        teardown();
        return;
    }

    /* the last VCM command, answered on a bus that still acknowledges */
    const int sent_before = ft_sent_count_id(0x051);
    const int wire_before = ft_wire_count_id(0x051);
    uint8_t cmd[6];
    memcpy(cmd, VCM, sizeof(cmd));
    cmd[5] = (uint8_t)(g_ctr++ & 0x0F);
    ft_deliver(0x051, cmd, 6);
    for (int i = 0; i < 20 && ft_wire_count_id(0x051) == wire_before; i++) ft_run(100);
    const int64_t t_last = ft_last_rx_time();
    if (ft_wire_count_id(0x051) != wire_before + 1 || ft_sent_count_id(0x051) != sent_before + 1)
    {
        result(cs, "setup", "VOID", "the last command's answer did not complete (%d sent, %d on the wire)",
               ft_sent_count_id(0x051) - sent_before, ft_wire_count_id(0x051) - wire_before);
        teardown();
        return;
    }
    const int sent_after_answer = ft_sent_count(); /* every frame queued so far */

    /* no other node from here */
    if (c->tenth_us > 0) sl_silence_tenth_at(t_last, c->tenth_us);
    else sl_silence(c->retry_us);
    const int rx_at_silence = sl_rx_count();

    int64_t t_latch = -1, t_unlatched = ft_now(), t_page_max_gap = 0, t_prev_page = ft_now();
    long err_n_max = -1;
    bool bus_off_done = false;
    const int64_t end = t_last + 2000 * MS;
    while (ft_now() < end)
    {
        ft_run(STEP_US);
        if (c->bus_off_after > 0 && !bus_off_done && sl_err_count() >= c->bus_off_after)
        {
            sl_bus_off();
            bus_off_done = true;
        }
        const char *p = page();             /* the status page answers, or the loop stalls */
        if (p[0])
        {
            if (ft_now() - t_prev_page > t_page_max_gap) t_page_max_gap = ft_now() - t_prev_page;
            t_prev_page = ft_now();
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
            t_unlatched = ft_now();     /* the latch, when it comes, is after this poll */
        }
    }

    /* R1: nothing 0x051 queued after the answer */
    int late051 = 0;
    for (int i = sent_after_answer; i < ft_sent_count(); i++)
        if (ft_sent(i)->id == 0x051) late051++;
    result(cs, "R1", late051 == 0 ? "PASS" : "NOT-AS-SPECIFIED",
           "%d 0x051 frame(s) queued after the answer to the last VCM command", late051);

    /* the check points after the last 0x051 */
    const int i0 = first_check_after(t_last);
    int i_stale = -1;
    for (int i = (i0 < 0 ? sl_rx_count() : i0); i < sl_rx_count(); i++)
        if (sl_rx(i)->t - t_last > FRESH_US) { i_stale = i; break; }
    bool edge = false;
    for (int i = (i0 < 0 ? sl_rx_count() : i0); i < sl_rx_count(); i++)
    {
        const int64_t a = sl_rx(i)->t - t_last - FRESH_US;
        if (a > -EDGE_US && a < EDGE_US) edge = true;
    }
    int frames_in_silence = 0;
    for (int i = rx_at_silence; i < sl_rx_count(); i++) frames_in_silence += sl_rx(i)->ok;
    if (i0 < 0 || i_stale < 0 || edge || frames_in_silence)
    {
        result(cs, "R2/R3", "VOID", "check points unusable: first %d, stale %d, a check within %lld ms of the "
               "0.5 s edge %d, frames received in the silence %d", i0, i_stale,
               (long long)(EDGE_US / MS), edge, frames_in_silence);
        teardown();
        return;
    }
    const int64_t c_stale = sl_rx(i_stale)->t;

    /* the first check at which >= 10 errors fall within 10 s of it */
    int i6 = -1;
    for (int i = i0; i < sl_rx_count() && i6 < 0; i++)
    {
        int n = 0;
        for (int k = 0; k < sl_err_count() && k < SL_MAX_ERR; k++)
        {
            const int64_t te = sl_err_time(k);
            if (te <= sl_rx(i)->t && te >= sl_rx(i)->t - TRIP6_WIN_US) n++;
        }
        if (n >= TRIP6_N) i6 = i;
    }
    const int64_t tenth = sl_err_count() >= TRIP6_N ? sl_err_time(TRIP6_N - 1) : -1;

    /* did the case place the 10th error where it meant to? */
    if (c->expect_region)
    {
        const int64_t c_pre = i_stale > i0 ? sl_rx(i_stale - 1)->t : t_last;
        int region = tenth < 0 ? 3 : tenth <= c_pre ? 1 : tenth <= c_stale ? 2 : 3;
        if (region != c->expect_region)
        {
            result(cs, "R3", "VOID", "the 10th error at +%lld ms is in region %d, not %d (checks +%lld, +%lld ms)",
                   (long long)((tenth - t_last) / MS), region, c->expect_region,
                   (long long)((c_pre - t_last) / MS), (long long)((c_stale - t_last) / MS));
            teardown();
            return;
        }
    }

    const bool expect6 = i6 >= 0 && sl_rx(i6)->t < c_stale;
    const int64_t c_exp = expect6 ? sl_rx(i6)->t : c_stale;

    /* R2: the latch lands at a check point */
    if (getenv("NN_DEBUG"))
    {
        printf("DEBUG t_last %lld t_latch %lld c_exp %lld\n", (long long)t_last, (long long)t_latch, (long long)c_exp);
        for (int i = i0; i < sl_rx_count() && i < i0 + 6; i++)
            printf("DEBUG rx %d t %lld ok %d id %03X\n", i, (long long)sl_rx(i)->t, sl_rx(i)->ok, (unsigned)sl_rx(i)->id);
    }
    int i_at = -1;
    for (int i = i0; t_latch >= 0 && i < sl_rx_count(); i++)
        if (sl_rx(i)->t <= t_latch && sl_rx(i)->t >= t_unlatched - SLACK_US) { i_at = i; }
    if (t_latch < 0)
        result(cs, "R2", "NOT-AS-SPECIFIED", "no latch within 2 s of the last 0x051");
    else
        result(cs, "R2", i_at >= 0 ? "PASS" : "NOT-AS-SPECIFIED",
               "latched between +%lld.%03lld and +%lld.%03lld ms; %s",
               (long long)((t_unlatched - t_last) / MS), (long long)((t_unlatched - t_last) % MS),
               (long long)((t_latch - t_last) / MS), (long long)((t_latch - t_last) % MS),
               i_at >= 0 ? "a receive return (a check point) in that span" :
               "no receive return (frame or timeout) in that span");

    /* R3: which trip, and when */
    if (t_latch >= 0)
    {
        const bool got6 = strncmp(reason, "error-frame rate", 16) == 0;
        const bool got2 = strncmp(reason, "bus lost", 8) == 0;
        const bool when_ok = c_exp <= t_latch && c_exp >= t_unlatched - SLACK_US;
        bool ok = when_ok && (expect6 ? got6 : got2);
        if (!expect6) ok = ok && c_exp - t_last >= FRESH_US && c_exp - t_last <= TRIP2_MAX_US;
        snprintf(why, sizeof(why), "expected trip %d at the check +%lld ms (10th error %s%lld ms, stale check "
                 "+%lld ms, retry %lld us, %d errors); got \"%s\" by +%lld ms",
                 expect6 ? 6 : 2, (long long)((c_exp - t_last) / MS), tenth < 0 ? "none " : "+",
                 (long long)(tenth < 0 ? 0 : (tenth - t_last) / MS), (long long)((c_stale - t_last) / MS),
                 (long long)sl_retry_us(), sl_err_count(), reason, (long long)((t_latch - t_last) / MS));
        result(cs, "R3", ok ? "PASS" : "NOT-AS-SPECIFIED", "%s", why);
    }

    /* the layer reaches the product: err_n moved with the injected errors */
    if (!c->bus_off_after || sl_err_count() > 0)
    {
        const long want = sl_err_count() < 16 ? sl_err_count() : 16;
        result(cs, "layer", (err_n_max >= (want < TRIP6_N ? want : TRIP6_N) && err_n_max > 0) ? "PASS" : "VOID",
               "err_n reached %ld with %d errors injected", err_n_max, sl_err_count());
    }

    /* R4: worker and status page never blocked */
    int returns = sl_rx_count() - rx_at_silence;
    const bool stuck = ft_wire_count() < ft_sent_count();
    result(cs, "R4", (returns >= 8 && t_page_max_gap <= 2 * STEP_US && stuck) ? "PASS" :
           (!stuck ? "VOID" : "NOT-AS-SPECIFIED"),
           "%d receive returns in 2 s of silence, page gap max %lld us, %d frame(s) stuck",
           returns, (long long)t_page_max_gap, ft_sent_count() - ft_wire_count());

    if (c->bus_off_after > 0)
    {
        /* R7: bus-off */
        const int dropped0 = ft_rx_dropped_off(), installs0 = ft_install_count();
        traffic(KEY_ON, 500 * MS);
        traffic(KEY_OFF, 300 * MS);
        traffic(KEY_ON, 500 * MS);
        const char *p = page();
        result(cs, "R7", (sl_recovery_calls() == 0 && latched(p) && ft_rx_dropped_off() > dropped0 &&
                          ft_install_count() == installs0) ? "PASS" : "NOT-AS-SPECIFIED",
               "recovery calls %d; after traffic and a key cycle: latched %d, frames dropped %d, installs %d",
               sl_recovery_calls(), latched(p), ft_rx_dropped_off() - dropped0,
               ft_install_count() - installs0);
        /* spec 7: a re-arm goes live only once the bus has been free of a qualifying
         * burst for 10 s, so the re-arm comes more than 10 s after the last error */
        gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
        while (ft_now() < sl_err_time(sl_err_count() - 1) + TRIP6_WIN_US + 500 * MS) ft_run(100 * MS);
        gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
        sl_nodes_back();
        traffic(KEY_ON, 1000 * MS);
        p = page();
        int rx_frames = 0;
        for (int i = sl_rx_count() - 50; i < sl_rx_count(); i++) if (i >= 0) rx_frames += sl_rx(i)->ok;
        result(cs, "R7", (rx_frames > 0 && !latched(p)) ? "PASS" :
               "NOT-AS-SPECIFIED", "after a re-arm: installs +%d, frames received %d, latched %d",
               ft_install_count() - installs0, rx_frames, latched(p));
        teardown();
        return;
    }

    /* R5: nodes back, no key-on */
    const int head = sent_after_answer;     /* the first frame queued in the silence */
    const ft_frame_t stuck_frame = *ft_sent(head);
    const int diag_before = ft_wire_count_id(0x7F1) + ft_wire_count_id(0x7F2) +
                            ft_wire_count_id(0x7F3) + ft_wire_count_id(0x7F8);
    const int sent051 = ft_sent_count_id(0x051);
    sl_nodes_back();
    traffic(KEY_ON, 1000 * MS);
    bool completed = false;
    for (int i = 0; i < ft_wire_count(); i++)
        if (ft_wire(i)->t_queued == stuck_frame.t_queued && ft_wire(i)->id == stuck_frame.id) completed = true;
    const int diag_after = ft_wire_count_id(0x7F1) + ft_wire_count_id(0x7F2) +
                           ft_wire_count_id(0x7F3) + ft_wire_count_id(0x7F8);
    const char *p = page();
    result(cs, "R5", (latched(p) && completed && diag_after > diag_before + 1 &&
                      ft_sent_count_id(0x051) == sent051) ? "PASS" : "NOT-AS-SPECIFIED",
           "after 1 s of traffic, key on throughout: latched %d, stuck frame 0x%03X completed %d, "
           "diag pages on the wire +%d, 0x051 queued +%d",
           latched(p), (unsigned)stuck_frame.id, completed, diag_after - diag_before,
           ft_sent_count_id(0x051) - sent051);

    /* R6: a key-on clears the latch */
    traffic(KEY_OFF, 300 * MS);
    traffic(KEY_ON, 300 * MS);
    p = page();
    result(cs, "R6", !latched(p) ? "PASS" : "NOT-AS-SPECIFIED", "after key off 0.3 s then on: latched %d",
           latched(p));
    teardown();
}

static const case_t CASES[] = {
    /* name        retry_us  tenth_us   bus_off  region */
    { "fast",          300,        0,       0,   1 },
    { "before",          0,  300 * MS,      0,   1 },
    { "tie",             0,  550 * MS,      0,   2 },
    { "slow",            0, 1500 * MS,      0,   3 },
    { "busoff_few",   1000,        0,       5,   3 },
    { "busoff_many",   300,        0,      12,   1 },
};

/*
 * ONE CASE PER PROCESS (`nonode_test <case>`; no argument lists them). The
 * firmware's statics outlive ft_reset(), and trip 6's error history is one of
 * them: only a key-on clears it (spec 7), so a case run after another could
 * inherit its errors. A fresh process is a fresh device.
 */
int main(int argc, char **argv)
{
    const size_t n = sizeof(CASES) / sizeof(CASES[0]);
    if (argc < 2)
    {
        for (size_t i = 0; i < n; i++) printf("%s\n", CASES[i].name);
        return 0;
    }
    for (size_t i = 0; i < n; i++)
    {
        if (strcmp(argv[1], CASES[i].name) == 0)
        {
            run_case(&CASES[i]);
            printf("SUMMARY %s %d PASS, %d NOT-AS-SPECIFIED, %d VOID\n", CASES[i].name, g_pass, g_bad, g_void);
            return (g_bad || g_void) ? 1 : 0;
        }
    }
    printf("RESULT %s - VOID no such case\n", argv[1]);
    return 2;
}
