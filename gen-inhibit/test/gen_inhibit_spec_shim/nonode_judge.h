/*
 * nonode_judge -- the spec 12.4 "a bus with no other node, emulated" verdicts,
 * as pure functions of what a case observed, so judge_test.c can show each one
 * fails on data that breaks its requirement. Gen-inhibit tester, 2026-10-20.
 *
 * Times are virtual microseconds. Every verdict is PASS, NOT-AS-SPECIFIED or
 * VOID (the case did not set up what it meant to: a harness problem, never a
 * pass).
 */
#ifndef NONODE_JUDGE_H
#define NONODE_JUDGE_H

#include <stdbool.h>
#include <stdint.h>

#define NN_MS          1000LL
#define NN_FRESH_US    (500 * NN_MS)     /* spec 7: 0x051's freshness window */
#define NN_TRIP2_MAX   (700 * NN_MS)     /* 12.4 row: trip 2 0.5-0.7 s after the last 0x051 */
#define NN_EDGE_US     (20 * NN_MS)      /* no check point this close to the 0.5 s edge */
#define NN_TRIP6_N     10                /* spec 7: 10 error frames ... */
#define NN_TRIP6_WIN   (10000 * NN_MS)   /* ... within 10 s (inclusive) */
#define NN_SLACK_US    50                /* a check point may share the last unlatched poll's instant */

typedef struct
{
    const char *v;          /* "PASS", "NOT-AS-SPECIFIED" or "VOID" */
    char detail[480];
} nn_verdict_t;

typedef struct
{
    int64_t        t_last;          /* the last 0x051 the device received */
    const int64_t *checks;          /* every receive return after t_last, ascending */
    int            n_checks;
    int            frames_in_silence;
    const int64_t *errs;            /* every injected error frame's time, ascending */
    int            n_errs;
    int            region;          /* where the case meant the 10th error: 0 any,
                                       1 at or before the check before the stale one,
                                       2 after it and at or before the stale check
                                       (the tie), 3 after the stale check or none */
    int64_t        t_unlatched;     /* the last poll that read not latched */
    int64_t        t_latch;         /* the first poll that read latched; < 0: none */
    const char    *reason;          /* abort_reason at t_latch */
} nn_obs_t;

typedef struct
{
    bool    usable;                 /* false: VOID, with why in nn_expect's verdict */
    int     trip;                   /* 6 or 2 */
    int64_t c_exp;                  /* the check point it must latch at */
    int64_t c_stale;                /* the first check that finds 0x051 stale */
    int64_t c_six;                  /* the first check at which trip 6's threshold is met; -1 none */
    int64_t tenth;                  /* the 10th error's time; -1 none */
} nn_expect_t;

/* Spec 7 for the injected timing: trip 6 if its threshold is met at a check
 * strictly earlier than the one that finds 0x051 stale, else trip 2 there. */
nn_expect_t  nn_expect(const nn_obs_t *o, nn_verdict_t *why_void);

nn_verdict_t nn_r1_no_051(int late051);
nn_verdict_t nn_r2_at_check(const nn_obs_t *o);
nn_verdict_t nn_r3_trip(const nn_obs_t *o, const nn_expect_t *e);
nn_verdict_t nn_layer_reaches(long err_n_max, int n_errs);
nn_verdict_t nn_layer_waits(int64_t first_err, int64_t first_wait);
nn_verdict_t nn_r4_not_blocked(int returns, int64_t page_gap_max, int stuck);
nn_verdict_t nn_r5_back(bool latched, bool stuck_completed, int diag_more, int sent051_more);
nn_verdict_t nn_r6_key_on(bool latched);
nn_verdict_t nn_r7_stays_off(int recovery_calls, bool latched, int dropped, int installs);
nn_verdict_t nn_r7_rearm(int frames_received, bool latched);

/* The answer-stuck case (reviewer, 2026-10-20; spec 5.2 items 5 and 6): the
 * device's answer to the last VCM command is the frame stuck in the controller
 * when the latch lands, so the trip withdraws it as a stand-down. */
nn_verdict_t nn_a1_answer_never_sent(bool answer_on_wire, int wire051_after_return);
nn_verdict_t nn_a2_standdown(long standdown_delta, long skipped_delta);
nn_verdict_t nn_a3_balance(int reads, int bad_reads, const char *first_bad);
nn_verdict_t nn_a4_equality(int reads_checked, int bad_reads, const char *first_bad);
nn_verdict_t nn_a5_not_held(int diag_handed_after, bool any_done_in_silence, bool first_done_on_return);

/* The withdrawn inhibit frame stuck in the controller (spec 12.4 row, added
 * 2026-11-26, user; spec 5.2 item 5, "A withdrawn frame held past the bound"):
 * the trip lands while the inhibit frame is in the controller, unacknowledged
 * and retried at the bench's pattern, transmitting during each retry.
 *   W1  the scheduler aborts it again on later passes until the buffer is
 *       free: an abort of the device's removed it before traffic returned;
 *       VOID if no trip withdrew it (no latch), or the latch came with the
 *       frame no longer held (the stimulus did not land);
 *   W2  it never completes, traffic back included;
 *   W3  no 0x051 reaches the wire when traffic returns;
 *   W4  the queued telemetry then goes out: every telemetry frame waiting
 *       when traffic returned is sent, and that many diag pages reach the
 *       wire; VOID if none was waiting, or a frame completed in the silence. */
nn_verdict_t nn_w1_freed(int64_t t_latch, bool held_at_latch, int64_t removed_at, int64_t t_return, int aborts,
                         int aborts_in_attempt, int passes, int status_reads, int reads_ts_clear);
nn_verdict_t nn_w2_never_completes(int64_t on_wire_at);
nn_verdict_t nn_w3_no_051_on_return(int wire051_after_return);
nn_verdict_t nn_w4_telemetry_out(bool any_done_in_silence, long waiting_at_return, long sent_after_return,
                                 int diag_wire_after_return);

#endif
