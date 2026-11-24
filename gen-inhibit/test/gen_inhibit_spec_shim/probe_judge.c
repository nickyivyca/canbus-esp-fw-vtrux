/*
 * probe_test.c's verdicts (probe_judge.h says what correct looks like and why).
 * Gen-inhibit tester, 2026-11-23.
 */
#include "probe_judge.h"

#include <stdarg.h>
#include <stdio.h>

static const char *PASS = "PASS", *NAS = "NOT-AS-SPECIFIED", *VOID_ = "VOID";

static int put(pj_res_t *out, int n, const char *req, bool ok, const char *fmt, ...)
{
    va_list ap;
    out[n].req = req;
    out[n].v = ok ? PASS : NAS;
    va_start(ap, fmt);
    vsnprintf(out[n].detail, sizeof(out[n].detail), fmt, ap);
    va_end(ap);
    return n + 1;
}

int pj_expected_ours(pj_kind_t k)
{
    return PJ_IS_LATE(k) ? 2 : 1;
}

int pj_judge(const pj_obs_t *o, pj_res_t *out)
{
    int n = 0;
    const bool late = PJ_IS_LATE(o->kind);
    if (!o->pre_ok)
    {
        out[0].req = "setup";
        out[0].v = VOID_;
        snprintf(out[0].detail, sizeof(out[0].detail), "%s", o->pre_why);
        return 1;
    }
    const long don = o->p1.on - o->p0.on, dunv = o->p1.unv - o->p0.unv;

    /* the decision: the counts after it, before the next frame of ours is seen */
    if (late)
        n = put(out, n, "counts", don == 0 && dunv == 0,
                "found late: ontime %+ld, with frames waiting %+ld (want +0, +0: in neither on-time count)", don, dunv);
    else if (o->kind == PJ_WAITING)
        n = put(out, n, "counts", don == 0 && dunv == 1,
                "frames waiting, no 0x051 among them: ontime %+ld, with frames waiting %+ld (want +0, +1)", don, dunv);
    else
        n = put(out, n, "counts", don == 1 && dunv == 0,
                "nothing waiting: ontime %+ld, with frames waiting %+ld (want +1, +0)", don, dunv);

    /* the largest number of frames waiting for an on-time-with-frames-waiting booking */
    if (o->kind == PJ_WAITING && o->p0.mx >= o->behind)
    {
        out[n].req = "backlog";
        out[n].v = VOID_;
        snprintf(out[n].detail, sizeof(out[n].detail),
                 "max backlog %ld before the case, not below the %d frames it puts behind", o->p0.mx, o->behind);
        n++;
    }
    else if (o->kind == PJ_WAITING)
        n = put(out, n, "backlog", o->p1.mx == o->behind, "max backlog %ld -> %ld (want %d, the frames waiting)",
                o->p0.mx, o->p1.mx, o->behind);
    else
        n = put(out, n, "backlog", o->p1.mx == o->p0.mx,
                "max backlog %ld -> %ld (want unchanged: no booking with frames waiting)", o->p0.mx, o->p1.mx);

    /* skips: a probe never; an inhibit frame found late is one */
    if (o->respond)
        n = put(out, n, "skip", o->p1.skip == o->p0.skip && o->p2.skip == o->p0.skip,
                "skipped %ld -> %ld -> %ld (a probe is never a skip)", o->p0.skip, o->p1.skip, o->p2.skip);
    else if (late)
        n = put(out, n, "skip", o->p1.skip == o->p0.skip + 1 && o->p2.skip == o->p1.skip,
                "skipped %ld -> %ld at the decision -> %ld settled (want +1, then no more)", o->p0.skip, o->p1.skip,
                o->p2.skip);
    else
        n = put(out, n, "skip", o->p1.skip == o->p0.skip && o->p2.skip == o->p0.skip,
                "skipped %ld -> %ld -> %ld (want unchanged)", o->p0.skip, o->p1.skip, o->p2.skip);

    /* the next command's frame, seen with nothing waiting, is booked on time */
    if (late)
        n = put(out, n, "next", o->p2.on - o->p1.on == 1 && o->p2.unv == o->p1.unv,
                "the next command's frame, nothing waiting: ontime %+ld, with frames waiting %+ld (want +1, +0)",
                o->p2.on - o->p1.on, o->p2.unv - o->p1.unv);

    /* every frame of ours went out; in RESPOND every probe is credited sent, the
     * late one too, and none is withdrawn */
    const int want = pj_expected_ours(o->kind);
    if (o->respond)
    {
        const long ds = o->p2.psent - o->p0.psent, dw = o->p2.pwithdrawn - o->p0.pwithdrawn;
        n = put(out, n, "sent", o->ours_wire == want && ds == want && dw == 0,
                "probes on the wire %d, cls.probe.sent %+ld, withdrawn %+ld (want %d, +%d, +0)", o->ours_wire, ds, dw,
                want, want);
    }
    else
        n = put(out, n, "sent", o->ours_wire == want, "inhibit frames on the wire %d (want %d%s)", o->ours_wire, want,
                late ? ": the frame found late went out too" : "");
    return n;
}
