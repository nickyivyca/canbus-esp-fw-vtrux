/*
 * judge_test -- nonode_judge.c on made-up observations: each verdict shown to
 * PASS on data that meets its requirement and to fail (NOT-AS-SPECIFIED or
 * VOID) on data that breaks it, so none of them is a check that cannot fail.
 * Needs no fakes and no product code:
 *   gcc -std=c11 -I. -o /tmp/jt judge_test.c nonode_judge.c && /tmp/jt
 * Prints "ok ..." or "FAIL ..." per check; exit 0 only with no FAIL.
 * Gen-inhibit tester, 2026-10-20.
 */
#include "nonode_judge.h"

#include <stdio.h>
#include <string.h>

#define MS NN_MS

static int g_fail, g_ok;

static void expect_v(const char *name, nn_verdict_t v, const char *want)
{
    if (strcmp(v.v, want) == 0) { g_ok++; printf("ok   %s\n", name); }
    else { g_fail++; printf("FAIL %s: got %s, want %s (%s)\n", name, v.v, want, v.detail); }
}

static void expect_b(const char *name, int cond, const char *detail)
{
    if (cond) { g_ok++; printf("ok   %s\n", name); }
    else { g_fail++; printf("FAIL %s: %s\n", name, detail); }
}

/* Check points every 200 ms after the last 0x051 at 0: 200, 400, 600, 800, 1000. */
static const int64_t CHK[] = { 200 * MS + 30, 400 * MS + 60, 600 * MS + 90, 800 * MS + 120, 1000 * MS + 150 };
static int64_t E[64];

static nn_obs_t obs(const int64_t *errs, int n, int region)
{
    nn_obs_t o;
    memset(&o, 0, sizeof(o));
    o.t_last = 0;
    o.checks = CHK;
    o.n_checks = 5;
    o.errs = errs;
    o.n_errs = n;
    o.region = region;
    o.t_latch = -1;
    return o;
}

/* n errors, 1 ms apart, the 10th at `tenth` */
static int errs_tenth(int64_t tenth, int n)
{
    for (int k = 0; k < n; k++) E[k] = tenth + (int64_t)(k - 9) * MS;
    return n;
}

static nn_expect_t expect_of(nn_obs_t *o, nn_verdict_t *why)
{
    return nn_expect(o, why);
}

int main(void)
{
    nn_verdict_t why;
    nn_obs_t o;
    nn_expect_t e;

    /* nn_expect: which trip, at which check */
    o = obs(E, errs_tenth(300 * MS, 12), 1);
    e = expect_of(&o, &why);
    expect_b("expect: 10th error before the +400 check -> trip 6 there",
             e.usable && e.trip == 6 && e.c_exp == CHK[1], why.detail);
    o = obs(E, errs_tenth(550 * MS, 12), 2);
    e = expect_of(&o, &why);
    expect_b("expect: threshold first met at the stale check (a tie) -> trip 2 there",
             e.usable && e.trip == 2 && e.c_exp == CHK[2], why.detail);
    o = obs(E, errs_tenth(CHK[1], 10), 1);
    e = expect_of(&o, &why);
    expect_b("expect: the 10th error at the check's own instant counts at that check",
             e.usable && e.trip == 6 && e.c_exp == CHK[1], why.detail);
    o = obs(E, errs_tenth(300 * MS, 10), 1);
    e = expect_of(&o, &why);
    expect_b("expect: exactly 10 errors meet the threshold", e.usable && e.trip == 6, why.detail);
    o = obs(E, errs_tenth(300 * MS, 9), 3);
    e = expect_of(&o, &why);
    expect_b("expect: 9 errors do not -> trip 2 at the stale check", e.usable && e.trip == 2 && e.c_exp == CHK[2],
             why.detail);
    o = obs(E, errs_tenth(1500 * MS, 12), 3);
    e = expect_of(&o, &why);
    expect_b("expect: a slow burst -> trip 2 at the stale check", e.usable && e.trip == 2 && e.c_exp == CHK[2],
             why.detail);
    for (int k = 0; k < 9; k++) E[k] = -11000 * MS + k * MS;     /* old history, more than 10 s back */
    E[9] = 300 * MS;
    o = obs(E, 10, 0);
    e = expect_of(&o, &why);
    expect_b("expect: errors older than 10 s before the check do not count", e.usable && e.trip == 2, why.detail);
    {
        static const int64_t edge[] = { 200 * MS, 400 * MS, 505 * MS, 705 * MS };
        o = obs(E, errs_tenth(300 * MS, 12), 0);
        o.checks = edge;
        o.n_checks = 4;
        e = expect_of(&o, &why);
        expect_b("expect: a check within 20 ms of the 0.5 s edge is VOID", !e.usable && !strcmp(why.v, "VOID"),
                 why.detail);
    }
    o = obs(E, errs_tenth(300 * MS, 12), 1);
    o.frames_in_silence = 1;
    e = expect_of(&o, &why);
    expect_b("expect: a frame received in the silence is VOID", !e.usable, why.detail);
    o = obs(E, errs_tenth(300 * MS, 12), 2);
    e = expect_of(&o, &why);
    expect_b("expect: the 10th error outside the case's region is VOID", !e.usable, why.detail);
    o = obs(E, errs_tenth(300 * MS, 12), 1);
    o.n_checks = 2;
    e = expect_of(&o, &why);
    expect_b("expect: no stale check is VOID", !e.usable, why.detail);
    {
        static const int64_t early[] = { -100 * MS, 200 * MS, 400 * MS, 600 * MS + 90, 800 * MS };
        o = obs(E, errs_tenth(300 * MS, 12), 1);
        o.checks = early;
        o.n_checks = 5;
        e = expect_of(&o, &why);
        expect_b("expect: a check point before the last 0x051 is VOID", !e.usable && !strcmp(why.v, "VOID"),
                 why.detail);
    }

    /* R2 and R3 */
    o = obs(E, errs_tenth(300 * MS, 12), 1);
    e = expect_of(&o, &why);
    o.t_unlatched = CHK[1] - 70;
    o.t_latch = CHK[1] + 30;
    o.reason = "error-frame rate exceeded";
    expect_v("R2: a latch bracketing a check point", nn_r2_at_check(&o), "PASS");
    expect_v("R3: trip 6 at the expected check", nn_r3_trip(&o, &e), "PASS");
    o.reason = "bus lost -- no 0x051";
    expect_v("R3: the wrong trip at the right time", nn_r3_trip(&o, &e), "NOT-AS-SPECIFIED");
    o.reason = "error-frame rate exceeded";
    o.t_unlatched = CHK[1] + 10 * MS;
    o.t_latch = CHK[1] + 10 * MS + 100;
    expect_v("R2: a latch between two check points", nn_r2_at_check(&o), "NOT-AS-SPECIFIED");
    expect_v("R3: the right trip at the wrong time", nn_r3_trip(&o, &e), "NOT-AS-SPECIFIED");
    o.t_unlatched = CHK[1] + 30;
    o.t_latch = CHK[1] + 130;
    expect_v("R2: a check point 30 us before the last unlatched poll still counts", nn_r2_at_check(&o), "PASS");
    o.t_unlatched = CHK[1] + 2 * MS;
    o.t_latch = CHK[1] + 2 * MS + 100;
    expect_v("R2: a check point 2 ms before the last unlatched poll does not", nn_r2_at_check(&o), "NOT-AS-SPECIFIED");
    o.t_latch = -1;
    expect_v("R2: no latch", nn_r2_at_check(&o), "NOT-AS-SPECIFIED");
    expect_v("R3: no latch", nn_r3_trip(&o, &e), "NOT-AS-SPECIFIED");

    o = obs(E, errs_tenth(1500 * MS, 12), 3);
    e = expect_of(&o, &why);
    o.t_unlatched = CHK[2] - 70;
    o.t_latch = CHK[2] + 30;
    o.reason = "bus lost -- no 0x051 (latched; CAN link down)";
    expect_v("R3: trip 2 at the stale check, 0.6 s after", nn_r3_trip(&o, &e), "PASS");
    o.reason = "error-frame rate exceeded";
    expect_v("R3: trip 6 where trip 2 was due", nn_r3_trip(&o, &e), "NOT-AS-SPECIFIED");
    {
        static const int64_t late[] = { 250 * MS, 750 * MS, 950 * MS };
        o = obs(E, errs_tenth(1500 * MS, 12), 3);
        o.checks = late;
        o.n_checks = 3;
        e = expect_of(&o, &why);
        o.t_unlatched = late[1] - 70;
        o.t_latch = late[1] + 30;
        o.reason = "bus lost -- no 0x051";
        expect_v("R3: trip 2 at a stale check 0.75 s after is past the 0.7 s limit", nn_r3_trip(&o, &e),
                 "NOT-AS-SPECIFIED");
    }

    /* the rest */
    expect_v("R1: none after the answer", nn_r1_no_051(0), "PASS");
    expect_v("R1: one after the answer", nn_r1_no_051(1), "NOT-AS-SPECIFIED");
    expect_v("layer: err_n reached 10 of 12", nn_layer_reaches(10, 12), "PASS");
    expect_v("layer: err_n reached all 5 of 5", nn_layer_reaches(5, 5), "PASS");
    expect_v("layer: err_n 4 of 5 never reached the product", nn_layer_reaches(4, 5), "VOID");
    expect_v("layer: err_n 0 with errors injected", nn_layer_reaches(0, 12), "VOID");
    expect_v("layer: the first error after the first wait", nn_layer_waits(100, 50), "PASS");
    expect_v("layer: an error before any frame waited", nn_layer_waits(40, 50), "VOID");
    expect_v("layer: no frame waited", nn_layer_waits(40, -1), "VOID");
    expect_v("R4: receive kept returning, the page answered", nn_r4_not_blocked(9, 100, 1), "PASS");
    expect_v("R4: receive stopped returning", nn_r4_not_blocked(2, 100, 1), "NOT-AS-SPECIFIED");
    expect_v("R4: the page stalled", nn_r4_not_blocked(9, 5000, 1), "NOT-AS-SPECIFIED");
    expect_v("R4: nothing stuck", nn_r4_not_blocked(9, 100, 0), "VOID");
    expect_v("R5: latched, completed, pages back, no 0x051", nn_r5_back(true, true, 8, 0), "PASS");
    expect_v("R5: not latched", nn_r5_back(false, true, 8, 0), "NOT-AS-SPECIFIED");
    expect_v("R5: the stuck frame never completed", nn_r5_back(true, false, 8, 0), "NOT-AS-SPECIFIED");
    expect_v("R5: only the stuck page completed", nn_r5_back(true, true, 1, 0), "NOT-AS-SPECIFIED");
    expect_v("R5: an inhibit frame while latched", nn_r5_back(true, true, 8, 1), "NOT-AS-SPECIFIED");
    expect_v("R6: cleared", nn_r6_key_on(false), "PASS");
    expect_v("R6: still latched", nn_r6_key_on(true), "NOT-AS-SPECIFIED");
    expect_v("R7: no recovery, latched, dropped, no install", nn_r7_stays_off(0, true, 10, 0), "PASS");
    expect_v("R7: a recovery call", nn_r7_stays_off(1, true, 10, 0), "NOT-AS-SPECIFIED");
    expect_v("R7: not latched", nn_r7_stays_off(0, false, 10, 0), "NOT-AS-SPECIFIED");
    expect_v("R7: frames not dropped", nn_r7_stays_off(0, true, 0, 0), "NOT-AS-SPECIFIED");
    expect_v("R7: a reinstall with no re-arm", nn_r7_stays_off(0, true, 10, 1), "NOT-AS-SPECIFIED");
    expect_v("R7: back on the bus after a re-arm", nn_r7_rearm(5, false), "PASS");
    expect_v("R7: deaf after a re-arm", nn_r7_rearm(0, false), "NOT-AS-SPECIFIED");
    expect_v("R7: still latched after a re-arm", nn_r7_rearm(5, true), "NOT-AS-SPECIFIED");

    /* the answer-stuck case */
    expect_v("A1: the answer never sent, no 0x051 after the return", nn_a1_answer_never_sent(false, 0), "PASS");
    expect_v("A1: the answer reached the wire", nn_a1_answer_never_sent(true, 0), "NOT-AS-SPECIFIED");
    expect_v("A1: an 0x051 on the wire after the return", nn_a1_answer_never_sent(false, 1), "NOT-AS-SPECIFIED");
    expect_v("A2: one stand-down, no skip", nn_a2_standdown(1, 0), "PASS");
    expect_v("A2: counted as a skip", nn_a2_standdown(0, 1), "NOT-AS-SPECIFIED");
    expect_v("A2: as both", nn_a2_standdown(1, 1), "NOT-AS-SPECIFIED");
    expect_v("A2: as neither", nn_a2_standdown(0, 0), "NOT-AS-SPECIFIED");
    expect_v("A2: twice", nn_a2_standdown(2, 0), "NOT-AS-SPECIFIED");
    expect_v("A3: every read balances", nn_a3_balance(100, 0, ""), "PASS");
    expect_v("A3: one read off", nn_a3_balance(100, 1, "x"), "NOT-AS-SPECIFIED");
    expect_v("A3: no read", nn_a3_balance(0, 0, ""), "VOID");
    expect_v("A4: the equality holds", nn_a4_equality(100, 0, ""), "PASS");
    expect_v("A4: one read off", nn_a4_equality(100, 1, "x"), "NOT-AS-SPECIFIED");
    expect_v("A4: no read outside a withdrawal", nn_a4_equality(0, 0, ""), "VOID");
    expect_v("A5: a diag frame after the latch, completed on the return", nn_a5_not_held(2, false, true), "PASS");
    expect_v("A5: no diag frame after the latch", nn_a5_not_held(0, false, false), "NOT-AS-SPECIFIED");
    expect_v("A5: the later diag frame never completed", nn_a5_not_held(2, false, false), "NOT-AS-SPECIFIED");
    expect_v("A5: a frame completed in the silence", nn_a5_not_held(2, true, true), "VOID");

    printf("\n%d ok, %d FAIL\n", g_ok, g_fail);
    return g_fail ? 1 : 0;
}
