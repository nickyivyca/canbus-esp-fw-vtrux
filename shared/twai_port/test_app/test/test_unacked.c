/*
 * Host tests of the no-acknowledge modes, m1-m3 (twai_port_unacked.c;
 * SPEC.md T13). The implementor's self-checks, not verification: the modes
 * are measured on the bench by the tester.
 *
 * The register layer (twai_port_probe_ll.h) and the target calls
 * (txp_env_*()) are given here by a SCRIPT, not a model of the controller:
 * each case sets a timeline of attempts (TS = 1 for `att` cycles, then
 * TS = 0 for the gap, the TEC up 8 at each attempt's end until 128), and
 * what an abort does: remove the frame `free_delay` cycles later if it was
 * issued with TS = 0, or nothing. The clock moves only when the code reads
 * it (txp_env_cycles()), by STEP cycles a read.
 */
#include "../twai_port_unacked.h"
#include "../twai_port_probe_ll.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HZ      160000000u
#define CPM     160u            /* cycles per microsecond */
#define STEP    16u             /* cycles per clock read: 0.1 us */

static int g_fail;
#define CHECK(c) do { if (!(c)) { printf("  FAIL %s:%d: %s\n", __FILE__, \
                                         __LINE__, #c); g_fail++; } } while (0)

/* ------------------------------------------------------------ the script -- */

enum { RULE_TS0_REMOVES = 0, RULE_NONE };

static struct {
    /* clock */
    uint32_t cyc;
    uint32_t stall_at, stall_len;   /* one jump, as an ISR would make */
    bool     stalled;
    /* timeline, cycles from the send */
    bool     sent;
    uint32_t send_cyc;
    uint32_t first;                 /* to the first attempt's TS rise */
    uint32_t att;                   /* TS = 1 per attempt */
    uint32_t gap_active, gap_passive;
    bool     ts_stuck;              /* TS stays 1 from the first rise */
    int64_t  complete_at;           /* -1, or TBS = 1 and TCS = 1 from then */
    /* the abort */
    int      rule;
    uint32_t free_delay;
    int64_t  removed_at;            /* -1, or TBS = 1 from then */
    bool     removed_tcs0;          /* a removed frame reads TCS = 0 */
    int      aborts, aborts_ts1, aborts_always;
    uint32_t abort_cyc[8];
    uint32_t abort_st[8];
    /* the target */
    bool     supported;
    bool     reinstall_ok, send_ok, busy;
    int      reinstalls, sends, settles, settle_ms, clear_tx, flushes;
    int      irq_offs, irq_ons, irq_depth;
    int      calls_with_irq_off;
    int      alert_calls;
    uint32_t alert_seq[16];
    int      driver_calls;
    /* the hold: an acknowledging node comes back at its start */
    bool     ack_back;
    int      holds, hold_ms;
    int      aborts_at_hold, clear_tx_at_hold, alert_calls_at_hold;
    int      settles_at_hold, holds_with_irq_off;
    bool     sent_in_hold;          /* the held frame went out in the hold */
} S;

static uint32_t att_start[1200];
static uint32_t att_end[1200];
static uint32_t tec_after[1200];
#define NATT ((int)(sizeof(att_start) / sizeof(att_start[0])))

static int g_cur;
static void script(uint32_t first, uint32_t att, uint32_t ga, uint32_t gp)
{
    memset(&S, 0, sizeof(S));
    S.first = first;
    S.att = att;
    S.gap_active = ga;
    S.gap_passive = gp;
    S.complete_at = -1;
    S.removed_at = -1;
    S.free_delay = 2 * CPM;
    S.supported = true;
    S.reinstall_ok = true;
    S.send_ok = true;
    S.cyc = 1000;
    g_cur = -1;
    uint32_t t = first;
    uint32_t tec = 0;
    for (int k = 0; k < NATT; k++)
    {
        att_start[k] = t;
        att_end[k] = t + att;
        tec = (tec + 8 > 128) ? 128 : tec + 8;
        tec_after[k] = tec;
        t = att_end[k] + (tec >= 128 ? gp : ga);
    }
}

/* The default bus: 2 us to the first rise, 200 us attempts, 6 us idle
 * error-active, 22 us error-passive. */
static void script_default(void)
{
    script(2 * CPM, 200 * CPM, 6 * CPM, 22 * CPM);
}

/* The last attempt started at or before t; the time mostly moves forward. */
static int find(uint32_t t)
{
    if (g_cur >= 0 && t < att_start[g_cur])
    {
        g_cur = -1;
    }
    while (g_cur + 1 < NATT && att_start[g_cur + 1] <= t)
    {
        g_cur++;
    }
    return g_cur;
}

static uint32_t now_rel(void)
{
    return S.cyc - S.send_cyc;
}

static uint32_t status_at(uint32_t t)
{
    if (!S.sent)
    {
        return TXP_ST_TBS | TXP_ST_TCS;
    }
    if (S.removed_at >= 0 && (int64_t)t >= S.removed_at)
    {
        return TXP_ST_TBS | (S.removed_tcs0 ? 0 : TXP_ST_TCS);
    }
    if (S.complete_at >= 0 && (int64_t)t >= S.complete_at)
    {
        return TXP_ST_TBS | TXP_ST_TCS;
    }
    if (t < S.first)
    {
        return 0;
    }
    if (S.ts_stuck)
    {
        return TXP_ST_TS;
    }
    const int k = find(t);
    if (k < 0)
    {
        return 0;
    }
    if (t < att_end[k])
    {
        const uint32_t tec = (k > 0) ? tec_after[k - 1] : 0;
        return TXP_ST_TS | (tec >= 96 ? TXP_ST_ES : 0);
    }
    return tec_after[k] >= 96 ? TXP_ST_ES : 0;
}

static uint32_t tec_at(uint32_t t)
{
    if (!S.sent || t < S.first || S.ts_stuck)
    {
        return 0;
    }
    const int k = find(t);
    if (k < 0)
    {
        return 0;
    }
    if (t < att_end[k])
    {
        return (k > 0) ? tec_after[k - 1] : 0;
    }
    return tec_after[k];
}

bool txp_ll_supported(void) { return S.supported; }
uint32_t txp_ll_status(void) { return status_at(now_rel()); }
uint32_t txp_ll_tec(void) { return tec_at(now_rel()); }

void txp_ll_abort(txp_abort_when_t when, txp_abort_t *out)
{
    const uint32_t t = now_rel();
    out->st_before = status_at(t);
    out->issued = (when == TXP_ABORT_ALWAYS)
                  || (out->st_before & (TXP_ST_TBS | TXP_ST_TS)) == 0;
    if (out->issued)
    {
        if (S.aborts < 8)
        {
            S.abort_cyc[S.aborts] = t;
            S.abort_st[S.aborts] = out->st_before;
        }
        S.aborts++;
        if (out->st_before & TXP_ST_TS) { S.aborts_ts1++; }
        if (when == TXP_ABORT_ALWAYS)   { S.aborts_always++; }
        if (S.rule == RULE_TS0_REMOVES && !(out->st_before & TXP_ST_TS)
            && S.removed_at < 0)
        {
            S.removed_at = (int64_t)t + S.free_delay;
        }
    }
    out->st_after = status_at(t);
}

uint32_t txp_env_cycles(void)
{
    S.cyc += STEP;
    if (S.stall_len != 0 && !S.stalled && S.sent && now_rel() >= S.stall_at)
    {
        S.stalled = true;
        S.cyc += S.stall_len;
    }
    return S.cyc;
}
uint32_t txp_env_cpu_hz(void) { return HZ; }
int64_t txp_env_timer_us(void) { return S.cyc / CPM; }
void txp_env_irq_off(void) { S.irq_offs++; S.irq_depth++; }
void txp_env_irq_on(void) { S.irq_ons++; S.irq_depth--; }

static void no_irq_off(void)
{
    if (S.irq_depth != 0) { S.calls_with_irq_off++; }
}

bool txp_env_reinstall(void)
{
    no_irq_off();
    S.reinstalls++;
    return S.reinstall_ok;
}

bool txp_env_send(uint16_t trial)
{
    (void)trial;
    no_irq_off();
    S.sends++;
    if (!S.send_ok)
    {
        return false;
    }
    /* A new trial's frame: the timeline starts again. */
    S.sent = true;
    S.send_cyc = S.cyc;
    S.removed_at = -1;
    S.stalled = false;
    return true;
}

uint32_t txp_env_alerts(void)
{
    no_irq_off();
    const uint32_t a = (S.alert_calls < 16) ? S.alert_seq[S.alert_calls] : 0;
    S.alert_calls++;
    return a;
}

bool txp_env_driver(txp_drv_t *out)
{
    no_irq_off();
    S.driver_calls++;
    memset(out, 0, sizeof(*out));
    out->msgs_to_tx = (S.busy || (S.sent && !(txp_ll_status() & TXP_ST_TBS)))
                      ? 1 : 0;
    out->tec = txp_ll_tec();
    out->bus_error = (uint32_t)S.driver_calls;
    return true;
}

void txp_env_clear_tx(void) { no_irq_off(); S.clear_tx++; }
void txp_env_flush_rx(void) { no_irq_off(); S.flushes++; }
void txp_env_settle(int ms)
{
    no_irq_off();
    S.settles++;
    S.settle_ms = ms;
    S.sent = false;      /* between trials the last frame is gone */
}

uint32_t txp_env_stack_free(void) { return 1234; }

/* The hold: the clock jumps by ms. With ack_back, a frame still in the
 * buffer at the hold's start goes out 100 us into it. */
void txp_env_hold(int ms)
{
    if (S.irq_depth != 0) { S.holds_with_irq_off++; }
    no_irq_off();
    S.holds++;
    S.hold_ms = ms;
    S.aborts_at_hold = S.aborts;
    S.clear_tx_at_hold = S.clear_tx;
    S.alert_calls_at_hold = S.alert_calls;
    S.settles_at_hold = S.settles;
    if (S.ack_back && S.sent && !(status_at(now_rel()) & TXP_ST_TBS))
    {
        S.complete_at = (int64_t)now_rel() + 100 * CPM;
        S.sent_in_hold = true;
    }
    S.cyc += (uint32_t)ms * 1000u * CPM;
}

/* ------------------------------------------------------------ arguments -- */

typedef struct { const char *k[12]; const char *v[12]; int trunc; } args_t;

static int args_get(void *ctx, const char *key, char *val, size_t vn)
{
    const args_t *a = ctx;
    for (int i = 0; i < 12 && a->k[i] != NULL; i++)
    {
        if (strcmp(a->k[i], key) == 0)
        {
            if (a->trunc && strcmp(a->k[a->trunc - 1], key) == 0)
            {
                return -1;
            }
            if (strlen(a->v[i]) >= vn)
            {
                return -1;
            }
            strcpy(val, a->v[i]);
            return 1;
        }
    }
    return 0;
}

static bool parse(const char *phase, args_t a, txp_params_t *p, char *err)
{
    return txp_parse(phase, args_get, &a, p, err, 200);
}

static void test_parse(void)
{
    txp_params_t p;
    char err[200];

    printf("parse\n");
    CHECK(parse("m1", (args_t){ {NULL} }, &p, err));
    CHECK(p.mode == TXP_M1 && p.reps == 1 && p.settle_ms == 20 && p.fresh
          && !p.irq_off && p.sample_us == 1 && p.window_us == 10000
          && p.trail_us == 500 && p.wait_us == -1 && p.wait_att == -1
          && p.bound_us == -1 && p.delay_us == -1 && p.label[0] == '\0'
          && p.hold_ms == 0);
    CHECK(parse("m2", (args_t){ {NULL} }, &p, err));
    CHECK(p.mode == TXP_M2 && p.wait_att == 1 && p.wait_us == -1
          && p.bound_us == 2000 && p.delay_us == -1);
    CHECK(parse("m3", (args_t){ {NULL} }, &p, err));
    CHECK(p.mode == TXP_M3 && p.wait_att == 1 && p.delay_us == 100);

    CHECK(parse("m3", (args_t){ {"label", "reps", "fresh", "irq",
                                 "sample_us", "window_us", "trail_us",
                                 "wait_us", "bound_us", "delay_us", "settle"},
                                {"run a", "8", "0", "off", "0", "50000", "0",
                                 "49999", "10", "5000", "1000"} }, &p, err));
    CHECK(strcmp(p.label, "run a") == 0);
    CHECK(p.reps == 8 && !p.fresh && p.irq_off && p.sample_us == 0
          && p.window_us == 50000 && p.trail_us == 0 && p.wait_us == 49999
          && p.wait_att == -1 && p.bound_us == 10 && p.delay_us == 5000
          && p.settle_ms == 1000);

    CHECK(!parse("m4", (args_t){ {NULL} }, &p, err));
    CHECK(!parse("abort", (args_t){ {NULL} }, &p, err));
    const char *foreign[] = { "gate", "retry", "d0", "d1", "step" };
    for (int i = 0; i < 5; i++)
    {
        CHECK(!parse("m1", (args_t){ {foreign[i]}, {"1"} }, &p, err));
        CHECK(strstr(err, foreign[i]) != NULL);
    }
    CHECK(!parse("m1", (args_t){ {"wait_att"}, {"2"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"wait_us"}, {"2"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"bound_us"}, {"20"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"delay_us"}, {"20"} }, &p, err));
    CHECK(!parse("m2", (args_t){ {"delay_us"}, {"20"} }, &p, err));
    CHECK(strstr(err, "delay_us") != NULL);
    CHECK(!parse("m2", (args_t){ {"wait_us", "wait_att"}, {"10", "2"} }, &p, err));
    CHECK(!parse("m2", (args_t){ {"wait_us"}, {"10000"} }, &p, err));
    CHECK(parse("m2", (args_t){ {"wait_us"}, {"9999"} }, &p, err));

    struct { const char *k, *bad, *good; } ranges[] = {
        { "reps", "9", "8" }, { "reps", "0", "1" },
        { "settle", "0", "1" }, { "settle", "1001", "1000" },
        { "sample_us", "101", "100" }, { "sample_us", "-1", "0" },
        { "window_us", "99", "100" }, { "window_us", "50001", "50000" },
        { "trail_us", "5001", "5000" },
        { "wait_att", "0", "1" }, { "wait_att", "256", "255" },
        { "bound_us", "9", "10" }, { "bound_us", "20001", "20000" },
        { "reps", "2x", "2" }, { "reps", "", "2" },
        { "hold_ms", "2001", "2000" }, { "hold_ms", "-1", "0" },
    };
    for (unsigned i = 0; i < sizeof(ranges) / sizeof(ranges[0]); i++)
    {
        CHECK(!parse("m2", (args_t){ {ranges[i].k}, {ranges[i].bad} }, &p, err));
        CHECK(parse("m2", (args_t){ {ranges[i].k}, {ranges[i].good} }, &p, err));
    }
    CHECK(!parse("m3", (args_t){ {"delay_us"}, {"5001"} }, &p, err));
    /* hold_ms: every phase */
    CHECK(parse("m1", (args_t){ {"hold_ms"}, {"1500"} }, &p, err) && p.hold_ms == 1500);
    CHECK(parse("m3", (args_t){ {"hold_ms"}, {"7"} }, &p, err) && p.hold_ms == 7);
    CHECK(!parse("m1", (args_t){ {"hold_ms"}, {"2001"} }, &p, err));
    CHECK(strstr(err, "hold_ms") != NULL);
    CHECK(!parse("m1", (args_t){ {"fresh"}, {"2"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"irq"}, {"no"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"label"},
                                 {"0123456789012345678901234567890123"} }, &p, err));
    CHECK(parse("m1", (args_t){ {"label"},
                                {"0123456789012345678901234567890"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"label"}, {"a\"b"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"label"}, {"a\\b"} }, &p, err));
    CHECK(!parse("m1", (args_t){ {"label"}, {"a\nb"} }, &p, err));
    /* present but unusable (truncated by the server): refused, never the
     * default */
    CHECK(!parse("m1", (args_t){ {"window_us"}, {"200"}, 1 }, &p, err));
    CHECK(!parse("m1", (args_t){ {"label"}, {"a"}, 1 }, &p, err));
}

/* ----------------------------------------------------------------- runs -- */

static txp_run_t R;

static txp_params_t params(int mode)
{
    txp_params_t p;
    char err[200];
    const char *ph = mode == TXP_M1 ? "m1" : mode == TXP_M2 ? "m2" : "m3";
    args_t none = { {NULL} };
    if (!txp_parse(ph, args_get, &none, &p, err, sizeof(err)))
    {
        printf("  params: %s\n", err);
        abort();
    }
    return p;
}

static const txp_event_t *ev(const txp_trial_t *r, uint32_t i)
{
    return &txp_events[r->ev_first + i];
}

/* Within one sample of the scripted time. */
static bool near(uint32_t got, uint32_t want, uint32_t slack)
{
    return got >= want && got <= want + slack;
}

static void test_m1_trace(void)
{
    printf("m1: the TS trace\n");
    script_default();
    txp_params_t p = params(TXP_M1);
    p.window_us = 2000;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(R.n == 1 && r->skip == TXP_SKIP_NONE);
    CHECK(R.cpu_hz == HZ && R.cpu_hz_end == HZ);
    CHECK(S.reinstalls == 1 && S.sends == 1);

    /* attempts: rises at 2, 208, 414, ... us; in 2000 us that is 10 */
    uint32_t want = 0;
    for (int k = 0; k < NATT && att_start[k] < 2000 * CPM; k++) { want++; }
    CHECK(r->attempts == want);
    CHECK(r->end_cyc >= 2000 * CPM && r->end_cyc < 2002 * CPM);

    /* the first sample is recorded: TBS = 0, before the first rise */
    CHECK(r->ev_n > 0 && ev(r, 0)->st == 0 && ev(r, 0)->t < S.first);
    /* every change is there, within one sample, and nothing else */
    uint32_t k = 0, i = 1;
    int ts_rises = 0, ts_falls = 0, tec_steps = 0;
    for (; i < r->ev_n; i++)
    {
        const txp_event_t *e = ev(r, i), *pe = ev(r, i - 1);
        CHECK(e->st != pe->st || e->tec != pe->tec);
        if ((e->st & TXP_ST_TS) && !(pe->st & TXP_ST_TS))
        {
            CHECK(near(e->t, att_start[k], 2 * CPM));
            ts_rises++;
        }
        if (!(e->st & TXP_ST_TS) && (pe->st & TXP_ST_TS))
        {
            CHECK(near(e->t, att_end[k], 2 * CPM));
            CHECK(e->tec == tec_after[k]);
            ts_falls++;
            tec_steps++;
            k++;
        }
    }
    CHECK(ts_rises == (int)want && ts_falls >= (int)want - 1);
    CHECK(tec_steps == ts_falls);
    CHECK(r->samples > 1900 && r->samples < 2100);   /* about every 1 us */
    CHECK(r->gaps == 0 && r->max_gap_cyc < 2 * CPM);
    CHECK(r->ev_lost == 0);

    /* not freed in the window: the cleanup aborts in an idle, never at TS = 1 */
    CHECK(r->cleanup && r->cleanup_aborts >= 1 && S.aborts_ts1 == 0
          && S.aborts_always == 0);
    CHECK(r->cleared == TXP_CLR_CLEANUP && r->cleanup_cyc >= 0);
    CHECK(S.clear_tx == 1);
    CHECK(r->clear_t < 0 && r->abort_t < 0 && !r->ab_issued);
    CHECK(r->have_after && r->have_end && (r->st_end & TXP_ST_TBS));
    CHECK(r->clk_cyc > 0 && r->clk_us > 0);
    CHECK(S.settles == 1 && S.settle_ms == 20);
    /* no hold by default */
    CHECK(S.holds == 0 && !r->held && r->hold_cyc == 0);
}

static void test_m1_tec_change_alone(void)
{
    printf("m1: TS stuck at 1\n");
    script_default();
    S.ts_stuck = true;
    txp_params_t p = params(TXP_M1);
    p.window_us = 1000;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->attempts == 1);
    /* TS stuck: the first sample, then the rise, and nothing else */
    CHECK(r->ev_n == 2);
    /* and the cleanup can never abort: TS never reads 0 */
    CHECK(r->cleanup && r->cleanup_aborts == 0 && S.aborts == 0);
    CHECK(r->cleared == TXP_CLR_NONE && r->cleanup_cyc < 0);
    CHECK(!(r->st_end & TXP_ST_TBS));
}

static void test_m1_tec_only(void)
{
    printf("m1: TEC-only changes\n");
    /* attempts with no idle at all: TS stays 1 across them, the TEC steps */
    script(2 * CPM, 200 * CPM, 0, 0);
    txp_params_t p = params(TXP_M1);
    p.window_us = 1000;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->attempts == 1);
    /* first sample, rise, then a TEC step at each of 4 attempt ends */
    CHECK(r->ev_n == 6);
    for (uint32_t i = 2; i < r->ev_n; i++)
    {
        CHECK(ev(r, i)->st == ev(r, i - 1)->st);
        CHECK(ev(r, i)->tec == ev(r, i - 1)->tec + 8);
    }
}

static void test_m1_completes(void)
{
    printf("m1: a frame that completes inside the window\n");
    script_default();
    S.complete_at = 300 * CPM;
    txp_params_t p = params(TXP_M1);
    p.window_us = 1000;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->cleared == TXP_CLR_TBS_TCS && near((uint32_t)r->clear_t, 300 * CPM, 2 * CPM));
    CHECK(!r->cleanup && S.aborts == 0);
    CHECK(r->attempts == 2);
}

static void test_m1_gap(void)
{
    printf("m1: a stall in the sampling is counted\n");
    script_default();
    S.stall_at = 201 * CPM;   /* to 231 us: over the first idle, 202-208 us */
    S.stall_len = 30 * CPM;
    txp_params_t p = params(TXP_M1);
    p.window_us = 500;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->gaps == 1 && r->max_gap_cyc >= 30 * CPM);
    /* the idle was missed: the change after it is flagged */
    int flagged = 0;
    for (uint32_t i = 0; i < r->ev_n; i++) { flagged += ev(r, i)->gap; }
    CHECK(flagged == 1);
}

static void test_m1_trail_after_complete(void)
{
    printf("m1/m2: the trail after the buffer frees\n");
    script_default();
    S.complete_at = 9990 * CPM;
    txp_params_t p = params(TXP_M1);
    p.trail_us = 300;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->clear_t >= 0 && r->end_cyc >= (uint32_t)r->clear_t + 300 * CPM);
}

static void test_pool(void)
{
    printf("m1: the change records run out and are counted\n");
    script(2 * CPM, 5 * CPM, 3 * CPM, 3 * CPM);   /* fast toggling */
    txp_params_t p = params(TXP_M1);
    p.reps = 8;
    p.window_us = 2000;
    CHECK(txp_run(&p, &R));
    CHECK(R.n == 8 && R.events_used == TXP_EVENTS);
    uint32_t lost = 0, n = 0;
    for (int i = 0; i < R.n; i++) { lost += R.trials[i].ev_lost; n += R.trials[i].ev_n; }
    CHECK(lost > 0 && n == TXP_EVENTS);
    CHECK(R.trials[7].ev_n == 0 && R.trials[7].ev_lost > 0);
    CHECK(S.reinstalls == 8 && S.settles == 8);
}

static void test_m2_wait_att(void)
{
    printf("m2: the abort in the idle after attempt 3\n");
    script_default();
    S.alert_seq[0] = 0;      /* the clear before the send */
    S.alert_seq[1] = 0x11;   /* before the abort */
    S.alert_seq[2] = 0x22;   /* after it */
    txp_params_t p = params(TXP_M2);
    p.wait_att = 3;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && S.aborts == 1 && S.aborts_ts1 == 0);
    CHECK(r->ab_attempt == 3);
    CHECK(near(S.abort_cyc[0], att_end[2], 2 * CPM));
    CHECK(near((uint32_t)r->abort_t, att_end[2], 2 * CPM));
    CHECK(near((uint32_t)r->met_t, att_start[2], 2 * CPM));
    CHECK((r->ab_st_before & (TXP_ST_TS | TXP_ST_TBS)) == 0);
    CHECK(r->ab_tec == 24);
    /* freed 2 us after the abort, and nothing more needed */
    CHECK(r->cleared == TXP_CLR_TBS_TCS);
    CHECK(near((uint32_t)(r->clear_t - r->abort_t), 2 * CPM, 2 * CPM));
    CHECK(!r->cleanup && !r->wait_timeout && !r->freed_before);
    CHECK(r->have_mid && r->alerts_before == 0x11 && r->alerts_after == 0x22);
    CHECK(r->snap_cyc > 0);
    CHECK(r->end_cyc >= (uint32_t)r->clear_t + 500 * CPM);
}

static void test_m2_error_passive(void)
{
    printf("m2: the abort in an error-passive idle\n");
    script_default();
    txp_params_t p = params(TXP_M2);
    p.wait_att = 20;
    p.window_us = 20000;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && r->ab_attempt == 20 && r->ab_tec == 128);
    CHECK(near((uint32_t)r->abort_t, att_end[19], 2 * CPM));
    CHECK(r->cleared == TXP_CLR_TBS_TCS);
}

static void test_m2_wait_us(void)
{
    printf("m2: the abort on the first TS = 0 read after wait_us\n");
    script_default();
    txp_params_t p = params(TXP_M2);
    p.wait_att = -1;
    p.wait_us = 500;          /* inside attempt 3 (414-614 us) */
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && r->ab_attempt == 3);
    CHECK(near((uint32_t)r->abort_t, att_end[2], 2 * CPM));
    CHECK(near((uint32_t)r->met_t, 500 * CPM, 2 * CPM));

    printf("m2: wait_us before the first rise aborts at once\n");
    script_default();
    S.first = 50 * CPM;
    for (int k = 0; k < NATT; k++) { att_start[k] += 48 * CPM; att_end[k] += 48 * CPM; }
    p.wait_us = 10;
    CHECK(txp_run(&p, &R));
    r = &R.trials[0];
    CHECK(r->ab_issued && r->ab_attempt == 0);
    CHECK(near((uint32_t)r->abort_t, 10 * CPM, 2 * CPM));
}

static void test_m2_never(void)
{
    printf("m2: TS never reads 0: no abort, the wait runs out\n");
    script_default();
    S.ts_stuck = true;
    txp_params_t p = params(TXP_M2);
    p.window_us = 1000;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(!r->ab_issued && r->wait_timeout && S.aborts == 0);
    CHECK(r->abort_t < 0 && r->end_cyc >= 1000 * CPM);
    CHECK(r->cleanup && r->cleared == TXP_CLR_NONE);
}

static void test_m2_freed_before(void)
{
    printf("m2: a frame that frees before the abort\n");
    script_default();
    S.complete_at = 100 * CPM;
    txp_params_t p = params(TXP_M2);
    p.wait_att = 2;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->freed_before && !r->ab_issued && S.aborts == 0 && !r->cleanup);
    CHECK(r->cleared == TXP_CLR_TBS_TCS);

    printf("m2: a frame that frees after the wait, before TS = 0\n");
    script_default();
    S.complete_at = 100 * CPM;   /* inside attempt 1, 2-202 us */
    p.wait_att = 1;
    CHECK(txp_run(&p, &R));
    r = &R.trials[0];
    CHECK(r->met_t >= 0 && r->freed_before && !r->wait_timeout);
    CHECK(!r->ab_issued && S.aborts == 0 && !r->cleanup);
    CHECK(near((uint32_t)r->clear_t, 100 * CPM, 2 * CPM));
}

static void test_m2_no_effect(void)
{
    printf("m2: an abort with no effect: bound, then the cleanup\n");
    script_default();
    S.rule = RULE_NONE;
    txp_params_t p = params(TXP_M2);
    p.bound_us = 300;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && r->clear_t < 0);
    CHECK(r->end_cyc >= (uint32_t)r->abort_t + 300 * CPM
          && r->end_cyc < (uint32_t)r->abort_t + 302 * CPM);
    CHECK(r->cleanup && r->cleared == TXP_CLR_NONE && r->cleanup_aborts > 1);
}

static void test_m3(void)
{
    printf("m3: the abort 20 us after attempt 2's rise\n");
    script_default();
    txp_params_t p = params(TXP_M3);
    p.wait_att = 2;
    p.delay_us = 20;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && S.aborts_always == 1 && S.aborts_ts1 >= 1);
    CHECK(near((uint32_t)r->anchor_t, att_start[1], 2 * CPM));
    CHECK(near((uint32_t)r->abort_t, att_start[1] + 20 * CPM, 3 * CPM));
    CHECK(r->ab_st_before & TXP_ST_TS);
    CHECK(r->ab_attempt == 2);
    /* the script's abort at TS = 1 does nothing: the bound, then the cleanup
     * frees it in an idle */
    CHECK(r->clear_t < 0 && r->cleanup && r->cleared == TXP_CLR_CLEANUP);
    CHECK(S.aborts_ts1 == 1);

    printf("m3: delay 0 aborts on the rise\n");
    script_default();
    p.wait_att = 1;
    p.delay_us = 0;
    CHECK(txp_run(&p, &R));
    r = &R.trials[0];
    CHECK(near((uint32_t)r->abort_t, att_start[0], 3 * CPM + r->snap_cyc));

    printf("m3: wait_us: the first rise at or after it\n");
    script_default();
    p.wait_att = -1;
    p.wait_us = 300;          /* inside attempt 2 (208-408 us) */
    p.delay_us = 100;
    CHECK(txp_run(&p, &R));
    r = &R.trials[0];
    CHECK(near((uint32_t)r->anchor_t, att_start[2], 2 * CPM));
    CHECK(near((uint32_t)r->abort_t, att_start[2] + 100 * CPM, 3 * CPM));
    CHECK(r->ab_attempt == 3);
}

static void test_m3_idle_abort_removes(void)
{
    printf("m3: a delay past the attempt lands in the idle and removes it\n");
    script_default();
    txp_params_t p = params(TXP_M3);
    p.wait_att = 1;
    p.delay_us = 203;         /* attempt 1 is 2-202 us, its idle 202-208 */
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && !(r->ab_st_before & TXP_ST_TS));
    CHECK(r->cleared == TXP_CLR_TBS_TCS && !r->cleanup);
}

static void test_fresh_and_skips(void)
{
    txp_params_t p;

    printf("fresh=0: no reinstall\n");
    script_default();
    p = params(TXP_M1);
    p.window_us = 200;
    p.reps = 3;
    p.fresh = false;
    CHECK(txp_run(&p, &R));
    CHECK(S.reinstalls == 0 && S.sends == 3 && R.trials[0].reinstalled == 0);

    printf("a failed reinstall skips the trial, nothing sent\n");
    script_default();
    S.reinstall_ok = false;
    p.fresh = true;
    CHECK(txp_run(&p, &R));
    CHECK(S.sends == 0 && R.n == 3 && R.trials[0].skip == TXP_SKIP_REINSTALL);

    printf("a busy driver skips the trial\n");
    script_default();
    S.busy = true;
    CHECK(txp_run(&p, &R));
    CHECK(S.sends == 0 && R.trials[0].skip == TXP_SKIP_BUSY);

    printf("a refused send skips the trial\n");
    script_default();
    S.send_ok = false;
    CHECK(txp_run(&p, &R));
    CHECK(S.sends == 3 && R.trials[0].skip == TXP_SKIP_TX_REFUSED
          && R.trials[0].samples == 0 && S.aborts == 0);
}

static void test_irq_off(void)
{
    printf("irq=off: masked around the sampling only\n");
    script_default();
    txp_params_t p = params(TXP_M2);
    p.irq_off = true;
    p.reps = 2;
    CHECK(txp_run(&p, &R));
    CHECK(S.irq_offs == 2 && S.irq_ons == 2 && S.irq_depth == 0);
    CHECK(S.calls_with_irq_off == 0);
    CHECK(R.trials[0].ab_issued && !R.trials[0].have_mid);

    printf("irq=on: never masked\n");
    script_default();
    p.irq_off = false;
    CHECK(txp_run(&p, &R));
    CHECK(S.irq_offs == 0 && S.irq_ons == 0 && R.trials[0].have_mid);
}

static void test_unsupported(void)
{
    printf("an unsupported chip: refused, nothing touched\n");
    script_default();
    S.supported = false;
    txp_params_t p = params(TXP_M1);
    CHECK(!txp_run(&p, &R));
    CHECK(S.reinstalls == 0 && S.sends == 0 && S.driver_calls == 0
          && S.alert_calls == 0 && S.aborts == 0);
}

/* -------------------------------------------------- the answer's sink -- */

static char J[200000];
static size_t JN;
static int g_emit_fail_at;

static bool emit(void *ctx, const char *s, size_t n)
{
    (void)ctx;
    if (g_emit_fail_at > 0 && --g_emit_fail_at == 0)
    {
        return false;
    }
    if (JN + n >= sizeof(J))
    {
        return false;
    }
    memcpy(J + JN, s, n);
    JN += n;
    J[JN] = '\0';
    return true;
}

/* ----------------------------------------------------------------- hold -- */

#define TX_SUCCESS 0x2u   /* any marker: the script returns what it is given */

static void test_hold_m1_positive(void)
{
    printf("hold, m1: the frame was never aborted, so it goes out when the "
           "node returns\n");
    /* the same trace without a hold, to compare */
    script_default();
    txp_params_t p = params(TXP_M1);
    p.window_us = 1000;
    CHECK(txp_run(&p, &R));
    const uint32_t samples0 = R.trials[0].samples, ev0 = R.trials[0].ev_n;
    const uint32_t used0 = R.events_used;

    script_default();
    S.ack_back = true;
    S.alert_seq[1] = 0x40;          /* after the trace */
    S.alert_seq[2] = TX_SUCCESS;    /* at the hold's end */
    p.hold_ms = 100;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(S.holds == 1 && S.hold_ms == 100 && r->held);
    /* after the trace and its reads, before the cleanup and the settle */
    CHECK(S.alert_calls_at_hold == 2 && S.settles_at_hold == 0);
    CHECK(S.aborts_at_hold == 0 && S.clear_tx_at_hold == 0);
    CHECK(S.holds_with_irq_off == 0);
    /* nothing recorded during it */
    CHECK(r->samples == samples0 && r->ev_n == ev0 && R.events_used == used0);
    CHECK(r->end_cyc < 1002 * CPM);
    /* its length, and its end */
    CHECK(r->hold_cyc >= 100000 * CPM && r->hold_cyc < 100001 * CPM);
    CHECK(S.sent_in_hold);
    CHECK((r->hold_st & (TXP_ST_TBS | TXP_ST_TCS)) == (TXP_ST_TBS | TXP_ST_TCS));
    CHECK(r->hold_tec == 128);      /* the script's TEC, error-passive by then */
    CHECK(r->alerts_after == 0x40 && r->alerts_hold == TX_SUCCESS);
    CHECK(r->have_hold && r->drv_hold.msgs_to_tx == 0);
    /* freed in the hold: no cleanup, nothing aborted */
    CHECK(r->cleared == TXP_CLR_HOLD && r->clear_t < 0);
    CHECK(!r->cleanup && S.aborts == 0 && S.clear_tx == 0);
}

static void test_hold_m1_no_node(void)
{
    printf("hold, m1: no node returns: still held at its end, then the "
           "cleanup\n");
    script_default();
    txp_params_t p = params(TXP_M1);
    p.window_us = 1000;
    p.hold_ms = 20;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->held && !S.sent_in_hold);
    CHECK(!(r->hold_st & TXP_ST_TBS));
    CHECK(r->hold_tec == 128);
    CHECK(r->have_hold && r->drv_hold.msgs_to_tx == 1);
    /* the cleanup ran after the hold, not before */
    CHECK(S.aborts_at_hold == 0 && S.clear_tx_at_hold == 0);
    CHECK(r->cleanup && S.clear_tx == 1 && r->cleanup_aborts >= 1);
    CHECK(r->cleared == TXP_CLR_CLEANUP);
}

static void test_hold_m2(void)
{
    printf("hold, m2: the abort removed the frame: nothing goes out\n");
    script_default();
    S.ack_back = true;
    S.removed_tcs0 = true;
    txp_params_t p = params(TXP_M2);
    p.hold_ms = 50;
    CHECK(txp_run(&p, &R));
    const txp_trial_t *r = &R.trials[0];
    CHECK(r->ab_issued && r->clear_t >= 0);
    /* the hold runs although the trace saw the buffer free */
    CHECK(S.holds == 1 && r->held && !S.sent_in_hold);
    CHECK((r->hold_st & (TXP_ST_TBS | TXP_ST_TCS)) == TXP_ST_TBS);
    CHECK(r->cleared == TXP_CLR_TBS && !r->cleanup);
    const txp_head_t h = { "g", 1, 2, 0x7FE, 8 };
    JN = 0;
    g_emit_fail_at = 0;
    CHECK(txp_write_json(&p, &R, &h, emit, NULL));
    CHECK(strstr(J, "\"hold_tbs\":1,\"hold_tcs\":0,") != NULL);

    printf("hold, m2: the abort left the frame: it goes out in the hold\n");
    script_default();
    S.rule = RULE_NONE;
    S.ack_back = true;
    p.bound_us = 100;
    CHECK(txp_run(&p, &R));
    r = &R.trials[0];
    CHECK(r->ab_issued && r->clear_t < 0);
    CHECK(S.sent_in_hold && S.aborts_at_hold == 1 && S.aborts == 1);
    CHECK((r->hold_st & (TXP_ST_TBS | TXP_ST_TCS)) == (TXP_ST_TBS | TXP_ST_TCS));
    CHECK(r->cleared == TXP_CLR_HOLD && !r->cleanup);
}

static void test_hold_irq_and_skip(void)
{
    printf("hold: interrupts on, after irq=off's trace\n");
    script_default();
    txp_params_t p = params(TXP_M2);
    p.irq_off = true;
    p.reps = 2;
    p.hold_ms = 5;
    CHECK(txp_run(&p, &R));
    CHECK(S.holds == 2 && S.holds_with_irq_off == 0 && S.calls_with_irq_off == 0);

    printf("hold: a skipped trial has none\n");
    script_default();
    S.send_ok = false;
    p.reps = 1;
    CHECK(txp_run(&p, &R));
    CHECK(S.holds == 0 && !R.trials[0].held);
}

/* ----------------------------------------------------------------- JSON -- */

static void test_json(void)
{
    printf("the answer\n");
    script_default();
    txp_params_t p = params(TXP_M2);
    strcpy(p.label, "bench-a");
    p.wait_att = 2;
    CHECK(txp_run(&p, &R));
    const txp_head_t h = { "abc123", 42, 99, 0x7FE, 8 };
    JN = 0;
    g_emit_fail_at = 0;
    CHECK(txp_write_json(&p, &R, &h, emit, NULL));
    const char *head = "{\"probe\":\"gi_txabort\",\"git\":\"abc123\",\"phase\":\"m2\","
                       "\"label\":\"bench-a\",\"id\":\"0x7FE\",\"dlc\":8,";
    CHECK(strncmp(J, head, strlen(head)) == 0);
    CHECK(strstr(J, "\"single_shot\":false") != NULL);
    CHECK(strstr(J, "\"wait_us\":-1,\"wait_att\":2,\"bound_us\":2000,\"delay_us\":-1,"
                    "\"hold_ms\":0,") != NULL);
    CHECK(strstr(J, "\"hold_ns\":-1,\"hold_st\":-1,\"hold_tec\":-1,\"hold_tbs\":-1,"
                    "\"hold_tcs\":-1,\"alerts_hold\":null,\"drv_hold\":null,"
                    "\"events\":[") != NULL);
    CHECK(strstr(J, "\"cpu_hz\":160000000") != NULL);
    CHECK(strstr(J, "\"st_bits\":{\"tbs\":4,\"tcs\":8,\"rs\":16,\"ts\":32,\"es\":64,\"bs\":128}") != NULL);
    CHECK(strstr(J, "\"n\":1,\"run_us\":42,\"uptime_us\":99,\"trials\":[{\"t\":0,") != NULL);
    CHECK(strstr(J, "\"ab_issued\":1") != NULL);
    CHECK(strstr(J, "\"cleared\":\"tbs_tcs\"") != NULL);
    CHECK(strstr(J, "\"alerts_before\":0,\"drv_mid\":{\"state\":") != NULL);
    CHECK(strstr(J, "\"ab_attempt\":2,") != NULL);

    /* times in ns: abort_ns is abort_t cycles at 160 MHz */
    char want[64];
    snprintf(want, sizeof(want), "\"abort_ns\":%lld,",
             (long long)R.trials[0].abort_t * 1000 / 160);
    CHECK(strstr(J, want) != NULL);
    snprintf(want, sizeof(want), "\"free_after_abort_ns\":%lld,",
             (long long)(R.trials[0].clear_t - R.trials[0].abort_t) * 1000 / 160);
    CHECK(strstr(J, want) != NULL);
    /* the first change: [t_ns, st, tec, gap], st 0 before the first rise */
    snprintf(want, sizeof(want), "\"events\":[[%lld,0,0,0],",
             (long long)txp_events[0].t * 1000 / 160);
    CHECK(strstr(J, want) != NULL);
    /* every event is in the answer */
    int brackets = 0;
    for (const char *c = strstr(J, "\"events\":["); *c; c++) { brackets += (*c == '['); }
    CHECK(brackets == (int)R.trials[0].ev_n + 1);
    /* the end: the trials closed, then the stack margin */
    const char *end = "]}],\"stack_free_min\":1234}";
    CHECK(JN >= strlen(end) && strcmp(J + JN - strlen(end), end) == 0);

    printf("the answer, with no driver reads and skips\n");
    script_default();
    S.reinstall_ok = false;
    p = params(TXP_M1);
    CHECK(txp_run(&p, &R));
    JN = 0;
    CHECK(txp_write_json(&p, &R, &h, emit, NULL));
    CHECK(strstr(J, "\"skip\":\"driver reinstall failed\"") != NULL);
    CHECK(strstr(J, "\"cleared\":\"never\",\"clear_ns\":-1") != NULL);
    CHECK(strstr(J, "\"alerts_before\":null,\"drv_mid\":null,\"snap_ns\":-1") != NULL);

    printf("the answer, with a hold\n");
    script_default();
    S.ack_back = true;
    S.alert_seq[2] = TX_SUCCESS;
    p = params(TXP_M1);
    p.window_us = 500;
    p.hold_ms = 10;
    CHECK(txp_run(&p, &R));
    JN = 0;
    CHECK(txp_write_json(&p, &R, &h, emit, NULL));
    CHECK(strstr(J, "\"delay_us\":-1,\"hold_ms\":10,") != NULL);
    CHECK(strstr(J, "\"cleared\":\"hold\",\"clear_ns\":-1") != NULL);
    CHECK(strstr(J, "\"cleanup\":0,") != NULL);
    snprintf(want, sizeof(want), "\"hold_ns\":%lld,\"hold_st\":12,\"hold_tec\":128,",
             (long long)R.trials[0].hold_cyc * 1000 / 160);
    CHECK(strstr(J, want) != NULL);
    CHECK(strstr(J, "\"hold_tbs\":1,\"hold_tcs\":1,\"alerts_hold\":2,"
                    "\"drv_hold\":{\"state\":0,\"mtx\":0,") != NULL);
    CHECK(JN >= strlen(end) && strcmp(J + JN - strlen(end), end) == 0);

    printf("the answer stops when a chunk fails\n");
    script_default();
    p = params(TXP_M1);
    p.window_us = 2000;
    CHECK(txp_run(&p, &R));
    for (int at = 1; at <= 6; at++)
    {
        JN = 0;
        g_emit_fail_at = at;
        CHECK(!txp_write_json(&p, &R, &h, emit, NULL));
    }
    g_emit_fail_at = 0;
}

int main(void)
{
    test_parse();
    test_m1_trace();
    test_m1_tec_change_alone();
    test_m1_tec_only();
    test_m1_completes();
    test_m1_gap();
    test_m1_trail_after_complete();
    test_pool();
    test_m2_wait_att();
    test_m2_error_passive();
    test_m2_wait_us();
    test_m2_never();
    test_m2_freed_before();
    test_m2_no_effect();
    test_m3();
    test_m3_idle_abort_removes();
    test_fresh_and_skips();
    test_irq_off();
    test_unsupported();
    test_hold_m1_positive();
    test_hold_m1_no_node();
    test_hold_m2();
    test_hold_irq_and_skip();
    test_json();
    printf("%s: %d failure(s)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
