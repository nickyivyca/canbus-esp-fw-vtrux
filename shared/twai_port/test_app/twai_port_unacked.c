/*
 * twai_port_unacked -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T13).
 * The no-acknowledge modes M1-M3; see twai_port_unacked.h, and
 * test_app/README.md for the parameters and the answer.
 *
 * WHAT IS BEING MEASURED (gen-inhibit tracker F5). With no node to
 * acknowledge, each attempt ends in an ACK error and an error frame, and the
 * controller starts the next attempt by itself: about 6 us of idle in the
 * error-active state, about 22 us in the error-passive state (CAN bit timing
 * at 500 kbit/s; the 8-bit suspend adds the difference). gen-inhibit's
 * emulated model assumes TS reads 0 in that idle. If TS stays 1 across the
 * attempts, an abort issued only while TS = 0 never removes the frame. So M1
 * records what TS does, and M2 and M3 record what the abort does in each
 * place.
 *
 * HOW THE TRACE IS TAKEN. A tight loop reads the status register and the TEC
 * and records a change of either, timed by the CPU cycle counter from the
 * send. The loop runs at the caller's priority with interrupts on unless
 * irq=off: then the driver's ISR runs in the middle of it, and every gap
 * between two samples longer than TXP_GAP_US plus the sampling period is
 * counted and flags the next change, so a reader knows where an edge may have
 * been missed or timed late. Nothing here reads the interrupt register.
 */
#include "twai_port_unacked.h"
#include "twai_port_probe_ll.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

txp_event_t txp_events[TXP_EVENTS];

/* ----------------------------------------------------------- arguments -- */

int txp_phase_from(const char *s)
{
    if (strcmp(s, "m1") == 0) { return TXP_M1; }
    if (strcmp(s, "m2") == 0) { return TXP_M2; }
    if (strcmp(s, "m3") == 0) { return TXP_M3; }
    return 0;
}

#define MB(m) (1u << (m))
#define M_ALL (MB(TXP_M1) | MB(TXP_M2) | MB(TXP_M3))
#define M_23  (MB(TXP_M2) | MB(TXP_M3))

static bool whole(const char *val, long lo, long hi, int *out)
{
    char *end = NULL;
    const long v = strtol(val, &end, 10);
    if (end == val || *end != '\0' || v < lo || v > hi)
    {
        return false;
    }
    *out = (int)v;
    return true;
}

static bool refuse(char *err, size_t errn, const char *key, const char *why)
{
    snprintf(err, errn, "query argument '%s' %s -- refused rather than run "
             "with a different configuration than was asked for", key, why);
    return false;
}

bool txp_parse(const char *phase, txp_get_fn get, void *ctx,
               txp_params_t *p, char *err, size_t errn)
{
    char val[40];
    int g;

    memset(p, 0, sizeof(*p));
    p->mode = txp_phase_from(phase);
    if (p->mode == 0)
    {
        snprintf(err, errn, "phase must be m1|m2|m3");
        return false;
    }
    p->reps = 1;
    p->settle_ms = 20;
    p->fresh = true;
    p->irq_off = false;
    p->sample_us = 1;
    p->window_us = 10000;
    p->trail_us = 500;
    p->wait_us = -1;
    p->wait_att = -1;
    p->bound_us = (p->mode == TXP_M1) ? -1 : 2000;
    p->delay_us = (p->mode == TXP_M3) ? 100 : -1;

    /* The older phases' arguments mean nothing here; given, they would be
     * silently ignored, so they are refused. */
    static const char *const foreign[] = { "gate", "retry", "d0", "d1", "step" };
    for (unsigned i = 0; i < sizeof(foreign) / sizeof(foreign[0]); i++)
    {
        if (get(ctx, foreign[i], val, sizeof(val)) != 0)
        {
            return refuse(err, errn, foreign[i], "is not used by m1, m2 or m3");
        }
    }

    g = get(ctx, "label", val, sizeof(val));
    if (g < 0 || (g > 0 && strlen(val) >= sizeof(p->label)))
    {
        return refuse(err, errn, "label", "is too long");
    }
    /* It is echoed into the JSON answer verbatim. */
    for (const char *c = val; g > 0 && *c != '\0'; c++)
    {
        if (*c == '"' || *c == '\\' || (unsigned char)*c < 0x20)
        {
            return refuse(err, errn, "label", "holds a quote, a backslash or "
                          "a control character");
        }
    }
    if (g > 0)
    {
        memcpy(p->label, val, strlen(val) + 1);
    }

    g = get(ctx, "fresh", val, sizeof(val));
    if (g < 0 || (g > 0 && strcmp(val, "0") != 0 && strcmp(val, "1") != 0))
    {
        return refuse(err, errn, "fresh", "must be 0|1");
    }
    if (g > 0)
    {
        p->fresh = (val[0] == '1');
    }

    g = get(ctx, "irq", val, sizeof(val));
    if (g < 0 || (g > 0 && strcmp(val, "on") != 0 && strcmp(val, "off") != 0))
    {
        return refuse(err, errn, "irq", "must be on|off");
    }
    if (g > 0)
    {
        p->irq_off = (strcmp(val, "off") == 0);
    }

    /* Static, so the table is not built on the caller's stack (the answer's
     * buffers, below, say why the stack matters here). */
#define P_OFF(f) offsetof(txp_params_t, f)
    static const struct { const char *key; size_t off; long lo, hi; unsigned modes; } nums[] = {
        { "reps",      P_OFF(reps),      1,   TXP_MAX_REPS, M_ALL },
        { "settle",    P_OFF(settle_ms), 1,   1000,         M_ALL },
        { "sample_us", P_OFF(sample_us), 0,   100,          M_ALL },
        { "window_us", P_OFF(window_us), 100, 50000,        M_ALL },
        { "trail_us",  P_OFF(trail_us),  0,   5000,         M_ALL },
        { "wait_us",   P_OFF(wait_us),   0,   50000,        M_23 },
        { "wait_att",  P_OFF(wait_att),  1,   255,          M_23 },
        { "bound_us",  P_OFF(bound_us),  10,  20000,        M_23 },
        { "delay_us",  P_OFF(delay_us),  0,   5000,         MB(TXP_M3) },
        { "hold_ms",   P_OFF(hold_ms),   0,   2000,         M_ALL },
    };
#undef P_OFF
    for (unsigned i = 0; i < sizeof(nums) / sizeof(nums[0]); i++)
    {
        int *const dst = (int *)(void *)((char *)p + nums[i].off);
        g = get(ctx, nums[i].key, val, sizeof(val));
        if (g == 0)
        {
            continue;
        }
        if (!(nums[i].modes & MB(p->mode)))
        {
            return refuse(err, errn, nums[i].key, "is not used by this phase");
        }
        if (g < 0 || !whole(val, nums[i].lo, nums[i].hi, dst))
        {
            return refuse(err, errn, nums[i].key,
                          "is too long, not a whole number, or out of range");
        }
    }

    if (p->mode != TXP_M1)
    {
        if (p->wait_us >= 0 && p->wait_att >= 0)
        {
            return refuse(err, errn, "wait_us", "and wait_att were both given; "
                          "give one");
        }
        if (p->wait_us < 0 && p->wait_att < 0)
        {
            p->wait_att = 1;
        }
        if (p->wait_us >= p->window_us)
        {
            return refuse(err, errn, "wait_us", "is not below window_us, so the "
                          "wait could never be met");
        }
    }
    return true;
}

/* ------------------------------------------------------------- sampling -- */

typedef struct {
    txp_run_t   *run;
    txp_trial_t *r;
    uint32_t t0;        /* the cycle count just after the send */
    uint32_t last;      /* the cycle count of the last sample */
    uint32_t period;    /* sample_us in cycles */
    uint32_t gap;       /* a longer gap is counted */
    uint32_t prev_st;   /* for the TS rise: before the first sample, st_pre */
    uint32_t last_st, last_tec;
    bool     have;
    int32_t  last_rise; /* the last TS rise, cycles from the send */
} smp_t;

static void feed(smp_t *s, uint32_t now, uint32_t st, uint32_t tec)
{
    txp_trial_t *r = s->r;
    uint8_t gap = 0;

    if (s->have)
    {
        const uint32_t d = now - s->last;
        if (d > r->max_gap_cyc) { r->max_gap_cyc = d; }
        if (d > s->gap)         { r->gaps++; gap = 1; }
    }
    s->last = now;
    r->samples++;

    const uint32_t t = now - s->t0;
    if ((st & TXP_ST_TS) && !(s->prev_st & TXP_ST_TS))
    {
        r->attempts++;
        s->last_rise = (int32_t)t;
    }
    s->prev_st = st;

    if (tec > 255) { tec = 255; }
    if (!s->have || st != s->last_st || tec != s->last_tec)
    {
        if (s->run->events_used < TXP_EVENTS)
        {
            txp_event_t *e = &txp_events[s->run->events_used++];
            e->t = t;
            e->st = (uint16_t)st;
            e->tec = (uint8_t)tec;
            e->gap = gap;
            r->ev_n++;
        }
        else
        {
            r->ev_lost++;
        }
    }
    s->last_st = st;
    s->last_tec = tec;
    s->have = true;
    r->end_cyc = t;

    if (r->clear_t < 0 && (st & TXP_ST_TBS))
    {
        r->clear_t = (int32_t)t;
        r->cleared = (st & TXP_ST_TCS) ? TXP_CLR_TBS_TCS : TXP_CLR_TBS;
    }
}

/* Wait for the sampling period, then the cycle count. */
static uint32_t pace(const smp_t *s)
{
    uint32_t now = txp_env_cycles();
    if (s->period != 0 && s->have)
    {
        while ((uint32_t)(now - s->last) < s->period)
        {
            now = txp_env_cycles();
        }
    }
    return now;
}

static uint32_t sample(smp_t *s)
{
    const uint32_t now = pace(s);
    const uint32_t st = txp_ll_status();
    feed(s, now, st, txp_ll_tec());
    return st;
}

/* -------------------------------------------------------------- a trial -- */

static void snapshot(const txp_params_t *p, txp_trial_t *r)
{
    /* Driver calls take locks and may not run with interrupts masked, and
     * with irq=off the ISR has not run yet, so there is nothing to split. */
    if (p->irq_off)
    {
        return;
    }
    const uint32_t c = txp_env_cycles();
    r->alerts_before = txp_env_alerts();
    r->have_mid = txp_env_driver(&r->drv_mid);
    r->snap_cyc = txp_env_cycles() - c;
}

static void record_abort(smp_t *s, uint32_t now, const txp_abort_t *a)
{
    txp_trial_t *r = s->r;
    const uint32_t tec = txp_ll_tec();
    feed(s, now, a->st_before, tec);
    r->abort_t = (int32_t)(now - s->t0);
    r->ab_issued = 1;
    r->ab_st_before = a->st_before;
    r->ab_st_after = a->st_after;
    r->ab_tec = tec;
    r->ab_attempt = r->attempts;
    feed(s, txp_env_cycles(), a->st_after, tec);
}

/*
 * The wait of m2 and m3. wait_att = N: until attempt N's TS rise. wait_us:
 * until that time from the send, and for m3 then until the next TS rise.
 * False if the buffer freed first or the wait ran past window_us.
 */
static bool wait_for(smp_t *s, const txp_params_t *p, uint32_t cpm)
{
    txp_trial_t *r = s->r;
    const uint32_t window = (uint32_t)p->window_us * cpm;

    if (p->wait_att > 0)
    {
        while (r->attempts < (uint32_t)p->wait_att)
        {
            sample(s);
            if (r->clear_t >= 0)     { r->freed_before = 1; return false; }
            if (r->end_cyc >= window) { r->wait_timeout = 1; return false; }
        }
    }
    else
    {
        const uint32_t wait = (uint32_t)p->wait_us * cpm;
        while (!s->have || r->end_cyc < wait)
        {
            sample(s);
            if (r->clear_t >= 0)     { r->freed_before = 1; return false; }
            if (r->end_cyc >= window) { r->wait_timeout = 1; return false; }
        }
        if (p->mode == TXP_M3)
        {
            const uint32_t att0 = r->attempts;
            while (r->attempts == att0)
            {
                sample(s);
                if (r->clear_t >= 0)     { r->freed_before = 1; return false; }
                if (r->end_cyc >= window) { r->wait_timeout = 1; return false; }
            }
        }
    }
    r->met_t = (int32_t)r->end_cyc;
    r->anchor_t = s->last_rise;
    return true;
}

/*
 * The hold (gen-inhibit reviewer, 2026-11-27, on the tester's question).
 * Without it the cleanup removes whatever frame the abort left behind, so a
 * bench check "an acknowledging node comes back: nothing may go out" could
 * never fail. During the hold the driver stays installed and running, with
 * interrupts on; nothing is aborted, cleared or recorded, so resends do not
 * use up the change records. Its end is read: the status first, as close to
 * the end as it can be, then the TEC, the alerts and the driver's view.
 */
static void hold(const txp_params_t *p, txp_trial_t *r)
{
    const uint32_t c = txp_env_cycles();
    txp_env_hold(p->hold_ms);
    r->hold_cyc = txp_env_cycles() - c;
    r->held = 1;
    r->hold_st = txp_ll_status();
    r->hold_tec = txp_ll_tec();
    r->alerts_hold = txp_env_alerts();
    r->have_hold = txp_env_driver(&r->drv_hold);
    if (r->cleared == TXP_CLR_NONE && (r->hold_st & TXP_ST_TBS))
    {
        r->cleared = TXP_CLR_HOLD;
    }
}

static void cleanup(txp_trial_t *r, uint32_t cpm)
{
    txp_abort_t a;
    const uint32_t c0 = txp_env_cycles();
    const uint32_t bound = (uint32_t)TXP_CLEANUP_US * cpm;

    r->cleanup = 1;
    txp_env_clear_tx();
    for (;;)
    {
        const uint32_t now = txp_env_cycles();
        txp_ll_abort(TXP_ABORT_IF_TS0, &a);
        if (a.issued)
        {
            r->cleanup_aborts++;
        }
        if ((a.st_before | a.st_after) & TXP_ST_TBS)
        {
            r->cleanup_cyc = (int32_t)(now - c0);
            r->cleared = TXP_CLR_CLEANUP;
            break;
        }
        if ((uint32_t)(now - c0) >= bound)
        {
            break;
        }
    }
    r->alerts_cleanup = txp_env_alerts();
}

static void trial(const txp_params_t *p, txp_run_t *run, uint16_t idx)
{
    txp_trial_t *r = &run->trials[run->n++];
    const uint32_t cpm = run->cpu_hz / 1000000u;
    const uint32_t c_start = txp_env_cycles();
    const int64_t u_start = txp_env_timer_us();
    smp_t s;

    memset(r, 0, sizeof(*r));
    r->trial = idx;
    r->clear_t = -1;
    r->met_t = -1;
    r->anchor_t = -1;
    r->abort_t = -1;
    r->cleanup_cyc = -1;
    r->ev_first = run->events_used;

    if (p->fresh)
    {
        r->reinstalled = 1;
        if (!txp_env_reinstall())
        {
            r->skip = TXP_SKIP_REINSTALL;
            goto done;
        }
    }
    txp_env_flush_rx();
    (void)txp_env_alerts();
    r->have_pre = txp_env_driver(&r->drv_pre);
    r->st_pre = txp_ll_status();
    r->tec_pre = txp_ll_tec();
    if (!(r->st_pre & TXP_ST_TBS) || !r->have_pre || r->drv_pre.msgs_to_tx != 0)
    {
        r->skip = TXP_SKIP_BUSY;
        goto done;
    }

    memset(&s, 0, sizeof(s));
    s.run = run;
    s.r = r;
    s.period = (uint32_t)p->sample_us * cpm;
    s.gap = (uint32_t)(TXP_GAP_US + p->sample_us) * cpm;
    s.prev_st = r->st_pre;
    s.last_rise = -1;

    const uint32_t c0 = txp_env_cycles();
    if (!txp_env_send(idx))
    {
        r->skip = TXP_SKIP_TX_REFUSED;
        goto done;
    }
    s.t0 = txp_env_cycles();
    r->tx_call_cyc = s.t0 - c0;

    if (p->irq_off)
    {
        txp_env_irq_off();
    }

    if (p->mode == TXP_M1)
    {
        const uint32_t window = (uint32_t)p->window_us * cpm;
        do
        {
            sample(&s);
        } while (r->end_cyc < window);
    }
    else if (wait_for(&s, p, cpm))
    {
        txp_abort_t a;
        snapshot(p, r);
        if (p->mode == TXP_M2)
        {
            /* The first read with TS = 0 issues it: the read is inside
             * txp_ll_abort()'s critical section, with the command. */
            const uint32_t window = (uint32_t)p->window_us * cpm;
            for (;;)
            {
                const uint32_t now = pace(&s);
                txp_ll_abort(TXP_ABORT_IF_TS0, &a);
                if (a.issued)
                {
                    record_abort(&s, now, &a);
                    break;
                }
                feed(&s, now, a.st_before, txp_ll_tec());
                if (r->clear_t >= 0)     { r->freed_before = 1; break; }
                if (r->end_cyc >= window) { r->wait_timeout = 1; break; }
            }
        }
        else
        {
            const uint32_t target = (uint32_t)r->anchor_t
                                    + (uint32_t)p->delay_us * cpm;
            while (r->end_cyc < target && r->clear_t < 0)
            {
                sample(&s);
            }
            if (r->clear_t >= 0)
            {
                r->freed_before = 1;
            }
            else
            {
                const uint32_t now = txp_env_cycles();
                txp_ll_abort(TXP_ABORT_ALWAYS, &a);
                record_abort(&s, now, &a);
            }
        }
        if (r->ab_issued)
        {
            const uint32_t bound = (uint32_t)r->abort_t
                                   + (uint32_t)p->bound_us * cpm;
            while (r->clear_t < 0 && r->end_cyc < bound)
            {
                sample(&s);
            }
        }
    }

    /* The trail: what follows the buffer coming free. */
    if (r->clear_t >= 0)
    {
        const uint32_t trail = (uint32_t)r->clear_t + (uint32_t)p->trail_us * cpm;
        while (r->end_cyc < trail)
        {
            sample(&s);
        }
    }

    if (p->irq_off)
    {
        txp_env_irq_on();
    }

    r->alerts_after = txp_env_alerts();
    r->have_after = txp_env_driver(&r->drv_after);

    /* Whether or not the trace saw the buffer free: a frame the abort removed
     * must not go out when a node acknowledges again either. */
    if (p->hold_ms > 0)
    {
        hold(p, r);
    }

    if (r->cleared == TXP_CLR_NONE)
    {
        cleanup(r, cpm);
    }

done:
    r->st_end = txp_ll_status();
    r->have_end = txp_env_driver(&r->drv_end);
    r->clk_cyc = txp_env_cycles() - c_start;
    r->clk_us = txp_env_timer_us() - u_start;
}

bool txp_run(const txp_params_t *p, txp_run_t *run)
{
    if (!txp_ll_supported())
    {
        return false;
    }
    memset(run, 0, sizeof(*run));
    run->cpu_hz = txp_env_cpu_hz();
    for (int i = 0; i < p->reps; i++)
    {
        trial(p, run, (uint16_t)i);
        txp_env_settle(p->settle_ms);
    }
    run->cpu_hz_end = txp_env_cpu_hz();
    return true;
}

/* -------------------------------------------------------------- answer -- */

static long long to_ns(const txp_run_t *run, long long cyc)
{
    if (cyc < 0 || run->cpu_hz == 0)
    {
        return -1;
    }
    return (long long)(((unsigned long long)cyc * 1000000000ull) / run->cpu_hz);
}

static const char *skip_name(uint8_t s)
{
    switch (s)
    {
    case TXP_SKIP_NONE:       return "";
    case TXP_SKIP_REINSTALL:  return "driver reinstall failed";
    case TXP_SKIP_BUSY:       return "buffer or driver busy before the send";
    case TXP_SKIP_TX_REFUSED: return "twai_transmit refused";
    default:                  return "?";
    }
}

static const char *cleared_name(uint8_t c)
{
    switch (c)
    {
    case TXP_CLR_TBS_TCS: return "tbs_tcs";
    case TXP_CLR_TBS:     return "tbs";
    case TXP_CLR_CLEANUP: return "cleanup";
    case TXP_CLR_HOLD:    return "hold";
    default:              return "never";
    }
}

static int drv_json(char *b, size_t n, bool have, const txp_drv_t *d)
{
    if (!have)
    {
        return snprintf(b, n, "null");
    }
    return snprintf(b, n,
        "{\"state\":%lu,\"mtx\":%lu,\"tec\":%lu,\"rec\":%lu,"
        "\"tx_failed\":%lu,\"bus_error\":%lu,\"arb_lost\":%lu}",
        (unsigned long)d->state, (unsigned long)d->msgs_to_tx,
        (unsigned long)d->tec, (unsigned long)d->rec,
        (unsigned long)d->tx_failed, (unsigned long)d->bus_error,
        (unsigned long)d->arb_lost);
}

static bool put(txp_emit_fn emit, void *ctx, const char *b, int n, size_t cap)
{
    if (n < 0 || (size_t)n >= cap)
    {
        return false;
    }
    return emit(ctx, b, (size_t)n);
}

/*
 * THE ANSWER'S BUFFERS ARE STATIC, NOT ON THE STACK (gen-inhibit
 * VERIFICATION.md 12.4, "The probe's no-acknowledge modes on the bench",
 * 2026-11-28: every m1-m3 call panicked the WiCAN). The URI handler runs on
 * the httpd task, whose stack gen-inhibit sets to 5120 bytes. The handler and
 * twai_port_txabort_run() already hold about 2.6 KB of it, and httpd and the
 * socket send about 1 KB more. With these buffers on the stack,
 * txp_write_json() took 3520 bytes on top, so every answer overflowed the
 * stack. One request runs at a time (one httpd task; the T19 app's one
 * command loop), the rule s_txp_run and txp_events already rest on.
 * test/test_stack.py holds every frame in this file to 512 bytes on the host.
 */
#define ANS_B 1400
#define ANS_D 160
static struct {
    char b[ANS_B];
    char d[5][ANS_D];   /* the driver views: pre, mid, after, end, hold */
} s_ans;

static bool trial_json(const txp_run_t *run,
                       const txp_trial_t *r, bool first,
                       txp_emit_fn emit, void *ctx)
{
    char *const d0 = s_ans.d[0];
    char *const d1 = s_ans.d[1];
    char *const d2 = s_ans.d[2];
    char *const d3 = s_ans.d[3];
    char *const b = s_ans.b;
    int n;

    if (drv_json(d0, ANS_D, r->have_pre, &r->drv_pre) >= ANS_D
        || drv_json(d1, ANS_D, r->have_mid, &r->drv_mid) >= ANS_D
        || drv_json(d2, ANS_D, r->have_after, &r->drv_after) >= ANS_D
        || drv_json(d3, ANS_D, r->have_end, &r->drv_end) >= ANS_D)
    {
        return false;
    }
    const long long free_after = (r->ab_issued && r->clear_t >= 0)
        ? to_ns(run, (long long)r->clear_t - r->abort_t) : -1;
    char before[16];
    if (r->have_mid)
    {
        snprintf(before, sizeof(before), "%lu", (unsigned long)r->alerts_before);
    }
    else
    {
        snprintf(before, sizeof(before), "null");
    }

    n = snprintf(b, ANS_B,
        "%s{\"t\":%u,\"skip\":\"%s\",\"reinstalled\":%u,"
        "\"st_pre\":%lu,\"tec_pre\":%lu,\"drv_pre\":%s,\"tx_call_ns\":%lld,"
        "\"samples\":%lu,\"max_gap_ns\":%lld,\"gaps\":%lu,\"ev_lost\":%lu,"
        "\"attempts\":%lu,\"end_ns\":%lld,"
        "\"cleared\":\"%s\",\"clear_ns\":%lld,"
        "\"wait_timeout\":%u,\"freed_before\":%u,\"met_ns\":%lld,"
        "\"anchor_ns\":%lld,\"abort_ns\":%lld,\"ab_issued\":%u,"
        "\"ab_st_before\":%lu,\"ab_st_after\":%lu,\"ab_tec\":%lu,"
        "\"ab_attempt\":%lu,\"free_after_abort_ns\":%lld,"
        "\"alerts_before\":%s,\"drv_mid\":%s,\"snap_ns\":%lld,"
        "\"alerts_after\":%lu,\"drv_after\":%s,"
        "\"cleanup\":%u,\"cleanup_ns\":%lld,\"cleanup_aborts\":%u,"
        "\"alerts_cleanup\":%lu,"
        "\"st_end\":%lu,\"drv_end\":%s,\"free_end\":%u,"
        "\"clk_cyc\":%lu,\"clk_us\":%lld,",
        first ? "" : ",", (unsigned)r->trial, skip_name(r->skip),
        (unsigned)r->reinstalled,
        (unsigned long)r->st_pre, (unsigned long)r->tec_pre, d0,
        to_ns(run, r->tx_call_cyc),
        (unsigned long)r->samples, to_ns(run, r->max_gap_cyc),
        (unsigned long)r->gaps, (unsigned long)r->ev_lost,
        (unsigned long)r->attempts, to_ns(run, r->end_cyc),
        cleared_name(r->cleared), to_ns(run, r->clear_t),
        (unsigned)r->wait_timeout, (unsigned)r->freed_before,
        to_ns(run, r->met_t), to_ns(run, r->anchor_t), to_ns(run, r->abort_t),
        (unsigned)r->ab_issued,
        (unsigned long)r->ab_st_before, (unsigned long)r->ab_st_after,
        (unsigned long)r->ab_tec, (unsigned long)r->ab_attempt, free_after,
        before, d1, r->have_mid ? to_ns(run, r->snap_cyc) : -1,
        (unsigned long)r->alerts_after, d2,
        (unsigned)r->cleanup, to_ns(run, r->cleanup_cyc),
        (unsigned)r->cleanup_aborts, (unsigned long)r->alerts_cleanup,
        (unsigned long)r->st_end, d3,
        (r->st_end & TXP_ST_TBS) ? 1u : 0u,
        (unsigned long)r->clk_cyc, (long long)r->clk_us);
    if (!put(emit, ctx, b, n, ANS_B))
    {
        return false;
    }

    /* The hold, in a chunk of its own; -1 and null with no hold. */
    if (r->held)
    {
        char *const dh = s_ans.d[4];
        if (drv_json(dh, ANS_D, r->have_hold, &r->drv_hold) >= ANS_D)
        {
            return false;
        }
        n = snprintf(b, ANS_B,
            "\"hold_ns\":%lld,\"hold_st\":%lu,\"hold_tec\":%lu,"
            "\"hold_tbs\":%u,\"hold_tcs\":%u,\"alerts_hold\":%lu,"
            "\"drv_hold\":%s,\"events\":[",
            to_ns(run, r->hold_cyc), (unsigned long)r->hold_st,
            (unsigned long)r->hold_tec,
            (r->hold_st & TXP_ST_TBS) ? 1u : 0u,
            (r->hold_st & TXP_ST_TCS) ? 1u : 0u,
            (unsigned long)r->alerts_hold, dh);
    }
    else
    {
        n = snprintf(b, ANS_B,
            "\"hold_ns\":-1,\"hold_st\":-1,\"hold_tec\":-1,"
            "\"hold_tbs\":-1,\"hold_tcs\":-1,\"alerts_hold\":null,"
            "\"drv_hold\":null,\"events\":[");
    }
    if (!put(emit, ctx, b, n, ANS_B))
    {
        return false;
    }

    /* The changes, [t_ns, st, tec, gap], a few to a chunk. */
    n = 0;
    for (uint32_t i = 0; i < r->ev_n; i++)
    {
        const txp_event_t *e = &txp_events[r->ev_first + i];
        const int m = snprintf(b + n, ANS_B - (size_t)n,
                               "%s[%lld,%u,%u,%u]", i == 0 ? "" : ",",
                               to_ns(run, e->t), (unsigned)e->st,
                               (unsigned)e->tec, (unsigned)e->gap);
        if (m < 0 || (size_t)m >= ANS_B - (size_t)n)
        {
            return false;
        }
        n += m;
        if ((size_t)n > ANS_B - 64)
        {
            if (!emit(ctx, b, (size_t)n))
            {
                return false;
            }
            n = 0;
        }
    }
    if (n > 0 && !emit(ctx, b, (size_t)n))
    {
        return false;
    }
    return emit(ctx, "]}", 2);
}

static const char *phase_name(int m)
{
    return m == TXP_M1 ? "m1" : m == TXP_M2 ? "m2" : "m3";
}

bool txp_write_json(const txp_params_t *p, const txp_run_t *run,
                    const txp_head_t *h, txp_emit_fn emit, void *ctx)
{
    char *const b = s_ans.b;
    const int n = snprintf(b, ANS_B,
        "{\"probe\":\"gi_txabort\",\"git\":\"%s\",\"phase\":\"%s\","
        "\"label\":\"%s\",\"id\":\"0x%03X\",\"dlc\":%d,\"tag\":\"AB7E trial\","
        "\"single_shot\":false,"
        "\"reps\":%d,\"settle_ms\":%d,\"fresh\":%d,\"irq\":\"%s\","
        "\"sample_us\":%d,\"window_us\":%d,\"trail_us\":%d,"
        "\"wait_us\":%d,\"wait_att\":%d,\"bound_us\":%d,\"delay_us\":%d,"
        "\"hold_ms\":%d,"
        "\"timing\":\"cpu cycle counter\",\"cpu_hz\":%lu,\"cpu_hz_end\":%lu,"
        "\"gap_us\":%d,"
        "\"st_bits\":{\"tbs\":%u,\"tcs\":%u,\"rs\":%u,\"ts\":%u,\"es\":%u,"
        "\"bs\":%u},"
        "\"events_max\":%d,\"events_used\":%lu,"
        "\"n\":%d,\"run_us\":%lld,\"uptime_us\":%lld,"
        "\"trials\":[",
        h->git, phase_name(p->mode), p->label, h->id, h->dlc,
        p->reps, p->settle_ms, p->fresh ? 1 : 0, p->irq_off ? "off" : "on",
        p->sample_us, p->window_us, p->trail_us,
        p->wait_us, p->wait_att, p->bound_us, p->delay_us, p->hold_ms,
        (unsigned long)run->cpu_hz, (unsigned long)run->cpu_hz_end,
        TXP_GAP_US,
        TXP_ST_TBS, TXP_ST_TCS, TXP_ST_RS, TXP_ST_TS, TXP_ST_ES, TXP_ST_BS,
        TXP_EVENTS, (unsigned long)run->events_used,
        run->n, h->run_us, h->uptime_us);
    if (!put(emit, ctx, b, n, ANS_B))
    {
        return false;
    }
    for (int i = 0; i < run->n; i++)
    {
        if (!trial_json(run, &run->trials[i], i == 0, emit, ctx))
        {
            return false;
        }
    }
    /* The calling task's least free stack so far, the last chunk's send not
     * included: the margin the bench can read with no console. */
    const int m = snprintf(b, ANS_B, "],\"stack_free_min\":%lu}",
                           (unsigned long)txp_env_stack_free());
    return put(emit, ctx, b, m, ANS_B);
}
