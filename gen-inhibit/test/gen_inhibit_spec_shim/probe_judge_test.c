/*
 * probe_judge.c on made-up observations: each case's correct observation passes
 * every verdict, and each wrong one fails the verdict it should. No fakes, no
 * product code.
 *
 *   gcc -std=c11 -Wall -Wextra -I. -o probe_judge_test probe_judge_test.c probe_judge.c
 *
 * Prints "ok   <label>" or "FAIL <label>" per row and "probe_judge_test: N ok, M FAIL".
 * Gen-inhibit tester, 2026-11-23.
 */
#include "probe_judge.h"

#include <stdio.h>
#include <string.h>

static int g_ok, g_bad;

static void row(const char *label, bool ok)
{
    printf("%s %s\n", ok ? "ok  " : "FAIL", label);
    if (ok) g_ok++;
    else g_bad++;
}

/* The verdict for `req`, or NULL if the judge gave none. */
static const char *verdict(const pj_obs_t *o, const char *req)
{
    static pj_res_t r[PJ_MAX_RES];
    const int n = pj_judge(o, r);
    for (int i = 0; i < n; i++)
        if (!strcmp(r[i].req, req)) return r[i].v;
    return NULL;
}

static bool all_pass(const pj_obs_t *o)
{
    pj_res_t r[PJ_MAX_RES];
    const int n = pj_judge(o, r);
    for (int i = 0; i < n; i++)
        if (strcmp(r[i].v, "PASS")) return false;
    return n >= 4;
}

static bool fails(const pj_obs_t *o, const char *req)
{
    const char *v = verdict(o, req);
    return v && !strcmp(v, "NOT-AS-SPECIFIED");
}

/* What a device meeting the spec shows for this case. */
static pj_obs_t good(bool respond, pj_kind_t k)
{
    pj_obs_t o;
    memset(&o, 0, sizeof(o));
    o.respond = respond;
    o.kind = k;
    o.behind = (k == PJ_LATE || k == PJ_WAITING) ? 2 : 0;
    o.pre_ok = true;
    o.p0 = (pj_page_t){ .on = 5, .unv = 0, .mx = 0, .skip = 0, .psent = respond ? 5 : 0, .pwithdrawn = 0 };
    o.p1 = o.p0;
    switch (k)
    {
    case PJ_LATE:
    case PJ_BETWEEN:
    case PJ_QUEUED:
        if (!respond) o.p1.skip++;
        o.p2 = o.p1;
        o.p2.on++;                          /* the next command's frame, on time */
        break;
    case PJ_WAITING:
        o.p1.unv++;
        o.p1.mx = 2;
        o.p2 = o.p1;
        break;
    case PJ_EMPTY:
        o.p1.on++;
        o.p2 = o.p1;
        break;
    }
    o.ours_wire = (k == PJ_LATE || k == PJ_BETWEEN || k == PJ_QUEUED) ? 2 : 1;   /* by hand, not pj_expected_ours() */
    if (respond) o.p2.psent = o.p0.psent + o.ours_wire;
    return o;
}

int main(void)
{
    static const char *KN[] = { "late", "between", "waiting", "empty", "queued" };
    char l[160];
    for (int m = 0; m < 2; m++)
        for (int k = 0; k < 5; k++)
        {
            const char *mode = m ? "RESPOND" : "INHIBIT";
            pj_obs_t o = good(m, (pj_kind_t)k);
            snprintf(l, sizeof l, "%s %s: the spec's observation passes every verdict", mode, KN[k]);
            row(l, all_pass(&o));

            o.pre_ok = false;
            snprintf(o.pre_why, sizeof o.pre_why, "made up");
            snprintf(l, sizeof l, "%s %s: a stimulus that did not land is VOID, nothing else", mode, KN[k]);
            {
                pj_res_t r[PJ_MAX_RES];
                const int n = pj_judge(&o, r);
                row(l, n == 1 && !strcmp(r[0].v, "VOID") && !strcmp(r[0].req, "setup"));
            }

            const bool late = k == PJ_LATE || k == PJ_BETWEEN || k == PJ_QUEUED;
            o = good(m, (pj_kind_t)k);
            if (late)
            {
                o.p1.on++; o.p2.on++;
                snprintf(l, sizeof l, "%s %s: booked on time -> counts fails", mode, KN[k]);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p1.unv++; o.p2.unv++;
                snprintf(l, sizeof l, "%s %s: booked on time, with frames waiting -> counts fails", mode, KN[k]);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p2.on = o.p1.on;
                snprintf(l, sizeof l, "%s %s: the next frame not booked on time -> next fails", mode, KN[k]);
                row(l, fails(&o, "next"));
                o = good(m, (pj_kind_t)k);
                o.p2.unv++;
                snprintf(l, sizeof l, "%s %s: the next frame also booked with frames waiting -> next fails", mode,
                         KN[k]);
                row(l, fails(&o, "next"));
                o = good(m, (pj_kind_t)k);
                o.p1.mx = 2; o.p2.mx = 2;
                snprintf(l, sizeof l, "%s %s: the late frame's backlog taken as a max backlog -> backlog fails", mode,
                         KN[k]);
                row(l, fails(&o, "backlog"));
            }
            else if (k == PJ_WAITING)
            {
                o.p1.unv--; o.p1.on++;
                snprintf(l, sizeof l, "%s waiting: booked in ontime -> counts fails", mode);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p1.on++; o.p2.on++;
                snprintf(l, sizeof l, "%s waiting: booked in both counts -> counts fails", mode);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p1.unv--;
                if (!m) o.p1.skip++, o.p2.skip++;
                snprintf(l, sizeof l, "%s waiting: booked late (no 0x051 among them) -> counts fails", mode);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p1.mx = 3;
                snprintf(l, sizeof l, "%s waiting: max backlog 3 with 2 waiting -> backlog fails", mode);
                row(l, fails(&o, "backlog"));
                o = good(m, (pj_kind_t)k);
                o.p1.mx = 1;
                snprintf(l, sizeof l, "%s waiting: max backlog 1 with 2 waiting (the wake counted out twice) -> "
                         "backlog fails", mode);
                row(l, fails(&o, "backlog"));
                o = good(m, (pj_kind_t)k);
                o.p0.mx = 2;
                snprintf(l, sizeof l, "%s waiting: a max backlog already at 2 makes backlog VOID, not PASS", mode);
                {
                    const char *v = verdict(&o, "backlog");
                    row(l, v && !strcmp(v, "VOID"));
                }
            }
            else
            {
                o.p1.on--; o.p1.unv++;
                snprintf(l, sizeof l, "%s empty: booked with frames waiting -> counts fails", mode);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p1.unv++; o.p2.unv++;
                snprintf(l, sizeof l, "%s empty: booked in both counts -> counts fails", mode);
                row(l, fails(&o, "counts"));
                o = good(m, (pj_kind_t)k);
                o.p1.mx = 1;
                snprintf(l, sizeof l, "%s empty: the max backlog moved -> backlog fails", mode);
                row(l, fails(&o, "backlog"));
            }

            /* skips */
            o = good(m, (pj_kind_t)k);
            if (m || !late)
            {
                o.p2.skip++;
                snprintf(l, sizeof l, "%s %s: a skip -> skip fails", mode, KN[k]);
                row(l, fails(&o, "skip"));
                o = good(m, (pj_kind_t)k);
                o.p1.skip++;
                snprintf(l, sizeof l, "%s %s: a skip at the decision, gone by the settled read -> skip fails", mode,
                         KN[k]);
                row(l, fails(&o, "skip"));
            }
            else
            {
                o.p1.skip--; o.p2.skip--;
                snprintf(l, sizeof l, "INHIBIT %s: found late but not a skip -> skip fails", KN[k]);
                row(l, fails(&o, "skip"));
                o = good(m, (pj_kind_t)k);
                o.p2.skip++;
                snprintf(l, sizeof l, "INHIBIT %s: the next frame also a skip -> skip fails", KN[k]);
                row(l, fails(&o, "skip"));
            }

            /* sent */
            o = good(m, (pj_kind_t)k);
            o.ours_wire--;
            snprintf(l, sizeof l, "%s %s: one frame of ours fewer on the wire -> sent fails", mode, KN[k]);
            row(l, fails(&o, "sent"));
            if (m)
            {
                o = good(m, (pj_kind_t)k);
                o.p2.psent--;
                snprintf(l, sizeof l, "RESPOND %s: a probe on the wire not credited sent -> sent fails", KN[k]);
                row(l, fails(&o, "sent"));
                o = good(m, (pj_kind_t)k);
                o.p2.pwithdrawn++;
                snprintf(l, sizeof l, "RESPOND %s: a probe withdrawn -> sent fails", KN[k]);
                row(l, fails(&o, "sent"));
            }
        }
    printf("probe_judge_test: %d ok, %d FAIL\n", g_ok, g_bad);
    return g_bad ? 1 : 0;
}
