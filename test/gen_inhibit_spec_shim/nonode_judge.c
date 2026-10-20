/* nonode_judge.c -- see nonode_judge.h. Gen-inhibit tester, 2026-10-20. */
#include "nonode_judge.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static nn_verdict_t verdict(const char *v, const char *fmt, ...)
{
    nn_verdict_t r;
    va_list ap;
    r.v = v;
    va_start(ap, fmt);
    vsnprintf(r.detail, sizeof(r.detail), fmt, ap);
    va_end(ap);
    return r;
}

#define PASS "PASS"
#define NAS  "NOT-AS-SPECIFIED"
#define VOID "VOID"

static long long ms(int64_t us) { return (long long)(us / NN_MS); }

nn_expect_t nn_expect(const nn_obs_t *o, nn_verdict_t *why)
{
    nn_expect_t e = { false, 0, -1, -1, -1, -1 };
    int i_stale = -1, i_six = -1;
    bool edge = false;
    for (int i = 0; i < o->n_checks; i++)
    {
        if (o->checks[i] <= o->t_last)
        {
            *why = verdict(VOID, "a check point at %lld us is not after the last 0x051 at %lld us: the case "
                           "passed the wrong returns", (long long)o->checks[i], (long long)o->t_last);
            return e;
        }
    }
    for (int i = 0; i < o->n_checks; i++)
    {
        const int64_t age = o->checks[i] - o->t_last;
        if (i_stale < 0 && age > NN_FRESH_US) i_stale = i;
        if (age - NN_FRESH_US > -NN_EDGE_US && age - NN_FRESH_US < NN_EDGE_US) edge = true;
    }
    if (o->n_checks == 0 || i_stale < 0 || edge || o->frames_in_silence)
    {
        *why = verdict(VOID, "check points unusable: %d after the last 0x051, stale check %s, one within %lld ms "
                       "of the 0.5 s edge %d, frames received in the silence %d", o->n_checks,
                       i_stale < 0 ? "none" : "found", ms(NN_EDGE_US), edge, o->frames_in_silence);
        return e;
    }
    for (int i = 0; i < o->n_checks && i_six < 0; i++)
    {
        int n = 0;
        for (int k = 0; k < o->n_errs; k++)
            if (o->errs[k] <= o->checks[i] && o->errs[k] >= o->checks[i] - NN_TRIP6_WIN) n++;
        if (n >= NN_TRIP6_N) i_six = i;
    }
    e.c_stale = o->checks[i_stale];
    e.c_six = i_six >= 0 ? o->checks[i_six] : -1;
    e.tenth = o->n_errs >= NN_TRIP6_N ? o->errs[NN_TRIP6_N - 1] : -1;
    if (o->region)
    {
        const int64_t c_pre = i_stale > 0 ? o->checks[i_stale - 1] : o->t_last;
        const int region = e.tenth < 0 ? 3 : e.tenth <= c_pre ? 1 : e.tenth <= e.c_stale ? 2 : 3;
        if (region != o->region)
        {
            *why = verdict(VOID, "the 10th error at +%lld ms is in region %d, not the case's %d (checks +%lld, "
                           "+%lld ms)", e.tenth < 0 ? -1LL : ms(e.tenth - o->t_last), region, o->region,
                           ms(c_pre - o->t_last), ms(e.c_stale - o->t_last));
            return e;
        }
    }
    e.trip = (e.c_six >= 0 && e.c_six < e.c_stale) ? 6 : 2;     /* strictly earlier (reviewer, 2026-10-20) */
    e.c_exp = e.trip == 6 ? e.c_six : e.c_stale;
    e.usable = true;
    return e;
}

nn_verdict_t nn_r1_no_051(int late051)
{
    return verdict(late051 == 0 ? PASS : NAS,
                   "%d 0x051 frame(s) queued after the answer to the last VCM command", late051);
}

nn_verdict_t nn_r2_at_check(const nn_obs_t *o)
{
    if (o->t_latch < 0) return verdict(NAS, "no latch after the last 0x051");
    int found = 0;
    for (int i = 0; i < o->n_checks; i++)
        if (o->checks[i] <= o->t_latch && o->checks[i] >= o->t_unlatched - NN_SLACK_US) found++;
    return verdict(found ? PASS : NAS, "latched between +%lld.%03lld and +%lld.%03lld ms; %s",
                   ms(o->t_unlatched - o->t_last), (long long)((o->t_unlatched - o->t_last) % NN_MS),
                   ms(o->t_latch - o->t_last), (long long)((o->t_latch - o->t_last) % NN_MS),
                   found ? "a receive return (a check point) in that span"
                         : "no receive return (frame or timeout) in that span");
}

nn_verdict_t nn_r3_trip(const nn_obs_t *o, const nn_expect_t *e)
{
    if (o->t_latch < 0) return verdict(NAS, "expected trip %d at +%lld ms; no latch", e->trip, ms(e->c_exp - o->t_last));
    const char *r = o->reason ? o->reason : "";
    const bool got6 = strncmp(r, "error-frame rate", 16) == 0;
    const bool got2 = strncmp(r, "bus lost", 8) == 0;
    const bool when = e->c_exp <= o->t_latch && e->c_exp >= o->t_unlatched - NN_SLACK_US;
    bool ok = when && (e->trip == 6 ? got6 : got2);
    if (e->trip == 2)
        ok = ok && e->c_exp - o->t_last >= NN_FRESH_US && e->c_exp - o->t_last <= NN_TRIP2_MAX;
    return verdict(ok ? PASS : NAS, "expected trip %d at the check +%lld ms (10th error %s%lld ms, trip 6 threshold "
                   "first met %s%lld ms, stale check +%lld ms, %d errors); got \"%s\", latched between +%lld and +%lld ms",
                   e->trip, ms(e->c_exp - o->t_last), e->tenth < 0 ? "none " : "+",
                   e->tenth < 0 ? 0LL : ms(e->tenth - o->t_last), e->c_six < 0 ? "never " : "+",
                   e->c_six < 0 ? 0LL : ms(e->c_six - o->t_last), ms(e->c_stale - o->t_last), o->n_errs, r,
                   ms(o->t_unlatched - o->t_last), ms(o->t_latch - o->t_last));
}

nn_verdict_t nn_layer_reaches(long err_n_max, int n_errs)
{
    /* err_n counts at most 16 (spec 11); trip 6 needs 10 of them */
    const long want = n_errs < NN_TRIP6_N ? n_errs : NN_TRIP6_N;
    return verdict(n_errs > 0 && err_n_max >= want ? PASS : VOID,
                   "the page's err_n reached %ld with %d error frames injected", err_n_max, n_errs);
}

nn_verdict_t nn_layer_waits(int64_t first_err, int64_t first_wait)
{
    return verdict(first_wait >= 0 && first_err >= first_wait ? PASS : VOID,
                   "the first error frame at %lld us, the first frame waiting from %lld us", (long long)first_err,
                   (long long)first_wait);
}

nn_verdict_t nn_r4_not_blocked(int returns, int64_t page_gap_max, int stuck)
{
    if (stuck < 1) return verdict(VOID, "no frame was stuck, so nothing could block");
    return verdict(returns >= 8 && page_gap_max <= 1000 ? PASS : NAS,
                   "%d receive returns in 2 s with %d frame(s) stuck; the status page answered every poll, "
                   "the longest gap %lld us", returns, stuck, (long long)page_gap_max);
}

nn_verdict_t nn_r5_back(bool latched, bool stuck_completed, int diag_more, int sent051_more)
{
    return verdict(latched && stuck_completed && diag_more >= 2 && sent051_more == 0 ? PASS : NAS,
                   "after 1 s of traffic, key on throughout: latched %d, the stuck frame completed %d, diag pages "
                   "on the wire +%d, 0x051 queued +%d", latched, stuck_completed, diag_more, sent051_more);
}

nn_verdict_t nn_r6_key_on(bool latched)
{
    return verdict(!latched ? PASS : NAS, "after key off 0.3 s then on: latched %d", latched);
}

nn_verdict_t nn_r7_stays_off(int recovery_calls, bool latched, int dropped, int installs)
{
    return verdict(recovery_calls == 0 && latched && dropped > 0 && installs == 0 ? PASS : NAS,
                   "recovery calls %d; after traffic and a key cycle: latched %d, frames dropped %d, installs %d",
                   recovery_calls, latched, dropped, installs);
}

nn_verdict_t nn_r7_rearm(int frames_received, bool latched)
{
    return verdict(frames_received > 0 && !latched ? PASS : NAS,
                   "after a re-arm more than 10 s after the last error: frames received %d, latched %d",
                   frames_received, latched);
}
