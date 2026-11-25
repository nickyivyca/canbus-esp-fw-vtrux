/*
 * The verdicts of probe_test.c as pure functions of what a case observed: spec 7
 * trip 7, "What the device can see of lateness", and spec 11's probe on the same
 * path (both changed 2026-11-23, user; f1961dcf and ab74c3da). No product code, no
 * fakes: probe_judge_test.c shows each verdict passing and failing on made-up
 * observations. Gen-inhibit tester, 2026-11-23.
 *
 * What correct looks like, from the spec only:
 *   - when the worker sees our frame complete it counts the receive queue, after
 *     the completion, so a frame arriving between the two reads is counted;
 *   - queue empty: on time (`ontime` +1);
 *   - frames waiting, the VCM's next 0x051 among them: late. An inhibit frame is a
 *     skip (`skipped` +1); a probe is never a skip, never withdrawn, and is in
 *     neither on-time count (spec 11);
 *   - frames waiting, no 0x051 among them: on time, with frames waiting
 *     (`ontime_unverified` +1), and `unverified_max_backlog` is the largest number
 *     of frames waiting for one of these;
 *   - in RESPOND the counts are booked for probe completions (spec 11), and a
 *     probe found late is still sent (cls.probe.sent counts every probe that went
 *     out).
 */
#ifndef PROBE_JUDGE_H
#define PROBE_JUDGE_H

#include <stdbool.h>

typedef enum
{
    PJ_LATE,     /* the next 0x051 among the frames waiting behind the wake */
    PJ_BETWEEN,  /* the next 0x051 arrives between the completion read and the count */
    PJ_WAITING,  /* frames waiting, no 0x051 among them */
    PJ_EMPTY,    /* nothing waiting */
    PJ_QUEUED    /* the next 0x051 already waiting when the worker takes N, so our
                  * frame's completion is seen only after it was dequeued */
} pj_kind_t;

#define PJ_IS_LATE(k) ((k) == PJ_LATE || (k) == PJ_BETWEEN || (k) == PJ_QUEUED)

/* One page reading: sched's on-time counts and skips, and cls.probe. */
typedef struct
{
    long on, unv, mx, skip;
    long psent, pwithdrawn;
} pj_page_t;

typedef struct
{
    bool      respond;          /* RESPOND (our frame is the probe) or INHIBIT */
    pj_kind_t kind;
    int       behind;           /* frames delivered behind the wake (PJ_LATE, PJ_WAITING) */
    bool      pre_ok;           /* the case's stimulus landed where it meant */
    char      pre_why[160];
    pj_page_t p0;               /* before the command */
    pj_page_t p1;               /* after the decision, before the next frame is seen */
    pj_page_t p2;               /* settled, every frame of ours seen */
    int       ours_wire;        /* our frames (0x7F0 or 0x051) completed on the wire */
} pj_obs_t;

typedef struct
{
    const char *req;
    const char *v;              /* PASS, NOT-AS-SPECIFIED or VOID */
    char        detail[220];
} pj_res_t;

#define PJ_MAX_RES 8

/* Fills out[] with one verdict per requirement the case judges; returns how many.
 * With pre_ok false it gives one VOID ("setup") and nothing else. */
int pj_judge(const pj_obs_t *o, pj_res_t *out);

/* Our frames the case expects on the wire: 2 when the next 0x051 came (it is
 * answered too), else 1. */
int pj_expected_ours(pj_kind_t k);

#endif /* PROBE_JUDGE_H */
