/*
 * The transmit scheduler's own cases. Spec 5.2 item 10's catalogue, emulation
 * half -- run here in the mock buffer first, then on the bench with the same
 * pass criteria. A case that exists only in emulation does not count toward
 * acceptance, which is why every pass criterion below is one a witness can also
 * read: which frames reached the wire, in what order, and how late.
 *
 * TWO OF THESE EXIST BECAUSE THE REVIEW FOUND DEFECTS THEY NOW PIN:
 *
 *   case_deadline_purges_queued_inhibit  -- D1. gs_command_received() looked only
 *     at `inhibit_outstanding`, set at HANDOVER, so an inhibit still in its class
 *     queue at the deadline was invisible: it stayed queued and went out AFTER
 *     the VCM's next command with no skip counted and no trip. Remove the purge
 *     and this case fails on the wire order, not on a counter.
 *
 *   case_three_skips_sliding_window      -- D2. The window was fixed, not
 *     sliding: it started at the first skip and reset a second later, so three
 *     skips inside 0.15 s that straddled the reset never tripped. This case puts
 *     them exactly there.
 *
 * Every case asserts on fb_wire_count()/fb_wire_id() where it can, because the
 * device cannot tell an aborted frame from a transmitted one and neither can the
 * scheduler's counters. The model can, and that is what it is for.
 */

#include <stdio.h>
#include <string.h>

#include "fake_buf.h"
#include "gi_sched.h"

static int g_fail;
static const char *g_case;

static void ck(bool ok, const char *what)
{
    if (!ok)
    {
        printf("  FAIL %-46s %s\n", what, g_case);
        g_fail++;
    }
}

static void ck_eq(long got, long want, const char *what)
{
    if (got != want)
    {
        printf("  FAIL %-46s got %ld want %ld  (%s)\n", what, got, want, g_case);
        g_fail++;
    }
}

/* ------------------------------------------------------------- fixtures --- */

#define ID_INHIBIT  0x051
#define ID_PROBE    0x7F0
#define ID_DIAG     0x7F1

static gi_frame_t mk(uint32_t id, uint8_t kind)
{
    gi_frame_t f;
    memset(&f, 0, sizeof(f));
    f.id = id;
    f.dlc = (id == ID_INHIBIT) ? 6 : 8;
    f.kind = kind;
    return f;
}

/* Run the scheduler and the buffer forward together, in `step` slices, which is
 * how the worker actually drives it: gs_tick() on every loop iteration. */
static void run(gs_t *s, int64_t us, int64_t step, uint32_t backlog)
{
    for (int64_t t = 0; t < us; t += step)
    {
        gs_tick(s, fb_now(), backlog);
        fb_advance(step);
    }
    gs_tick(s, fb_now(), backlog);
}

/* ------------------------------------------------------------- the cases -- */

/*
 * Item 10: telemetry waiting in the buffer when a VCM command arrives -- aborted,
 * the inhibit on time, the telemetry requeued and later sent.
 */
static void case_preempt_awaiting_telemetry(void)
{
    g_case = "preempt: telemetry awaiting, inhibit wins";
    fb_reset();
    fb_set_contended(true);     /* STRESS: saturated-bus figure, spec 12.2 */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    ck(gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now()), "diag queued");
    run(&s, 50, 10, 0);
    ck_eq(fb_submit_count(), 1, "the diag was handed over");
    ck(fb_state_now() == GS_BUF_AWAITING, "and is awaiting arbitration");

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");

    /* The abort must happen and the inhibit must go out. On an idle-enough bus
     * afterwards the inhibit reaches the wire; the diag comes back later. */
    fb_set_contended(false);
    run(&s, 2000, 10, 0);

    ck(fb_wire_count() >= 1, "something reached the wire");
    ck_eq(fb_wire_id(0), ID_INHIBIT, "THE INHIBIT WENT FIRST");
    const gs_stats_t *st = gs_stats(&s);
    ck(st->aborts >= 1, "an abort was issued");
    ck_eq(st->cls[GS_CLASS_TELEMETRY].requeued, 1, "the diag was requeued");
    ck_eq(st->skipped, 0, "no skip");

    /* and the telemetry is sent eventually, never silently lost */
    run(&s, 3000, 10, 0);
    bool diag_on_wire = false;
    for (int i = 0; i < fb_wire_count(); i++)
    {
        if (fb_wire_id(i) == ID_DIAG) { diag_on_wire = true; }
    }
    ck(diag_on_wire, "the requeued diag reached the wire later");
}

/*
 * Item 4's central behaviour: THE SCHEDULER NEVER ISSUES AN ABORT WHILE THE
 * BUFFER IS TRANSMITTING. It waits for TS to fall and aborts then.
 *
 * MY FIRST VERSION OF THIS CASE WAS WRONG and the failure taught me something
 * about the design. It armed fb_abort_no_effect_next(), expected the no-effect
 * episode to fire, and asserted the loop issued a second command. None of that
 * happened -- because the scheduler correctly never issues the doomed command in
 * the first place, so the hardware's no-effect behaviour is UNREACHABLE from
 * here. The model keeps that behaviour because item 9 requires it to (the
 * hardware does it, and the E4 runner can provoke it), but the scheduler's job is
 * to avoid it, and what a case should assert is the avoidance.
 *
 * On an idle bus a transmitting frame therefore COMPLETES and the inhibit follows
 * it -- which is exactly item 4's stated guarantee: "telemetry never delays an
 * inhibit frame by more than one telemetry frame already on the wire".
 */
static void case_never_aborts_while_transmitting(void)
{
    g_case = "item 4: no abort command while TRANSMITTING";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 20, 5, 0);          /* idle bus: it starts transmitting at once */
    ck(fb_state_now() == GS_BUF_TRANSMITTING, "the diag is transmitting");

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 1500, 5, 0);

    ck_eq(fb_abort_cmds_while_transmitting(), 0,
          "NO abort command was issued while transmitting");
    ck_eq(fb_aborts_with_no_effect(), 0, "so no abort could have no effect");

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->abort_bound_hit, 0, "the 1 ms bound was not reached");
    ck_eq(fb_wire_count(), 2, "both frames reached the wire");
    ck_eq(fb_wire_id(0), ID_DIAG, "the diag finished first -- it was on the wire");
    ck_eq(fb_wire_id(1), ID_INHIBIT, "and the inhibit followed it");
    ck_eq(st->skipped, 0, "nothing was skipped");
    /* Item 4's guarantee, as a number: the inhibit waited at most one frame. */
    ck(st->cls[GS_CLASS_INHIBIT].max_queue_us <= 300,
       "the inhibit's queue wait was within one frame time");
}

/*
 * D1. The inhibit is still QUEUED at the deadline, never handed over, because the
 * driver refuses every submit. It must be purged and counted as a skip, and it
 * must NEVER reach the wire afterwards.
 */
static void case_deadline_purges_queued_inhibit(void)
{
    g_case = "D1: a queued inhibit is purged at the deadline";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    fb_refuse_next(100);        /* the driver will not take anything */
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 500, 10, 0);
    ck_eq(fb_submit_count(), 0, "nothing was accepted");
    ck(gs_stats(&s)->tx_refused > 0, "the refusal was counted");

    /* The VCM's next command arrives. The stale inhibit is now too late. */
    const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
    ck(v == GS_DEADLINE_SKIPPED, "the deadline reports a SKIP");
    ck_eq(gs_stats(&s)->skipped, 1, "one skip counted");

    /* Now let the driver work again. The purged frame must not appear. */
    fb_reset();                 /* clears the refusal and the wire log */
    run(&s, 3000, 10, 0);
    for (int i = 0; i < fb_wire_count(); i++)
    {
        ck(fb_wire_id(i) != ID_INHIBIT,
           "THE PURGED INHIBIT NEVER REACHED THE WIRE");
    }
}

/*
 * D2. FOUR skips at 0, 0.95, 1.05 and 1.10 s. The newest THREE span 0.15 s, so a
 * sliding window trips; the old fixed window reset at 1.0 s and never saw three.
 *
 * MY FIRST VERSION USED THREE SKIPS at 0, 0.95 and 1.05 -- which span 1.05 s and
 * are therefore genuinely NOT three within a second. The case failed, and it was
 * right to: the test was wrong, not the window. Worth recording, because a test
 * that asserts a trip the spec does not require would have been "fixed" by
 * loosening the window.
 */
static void case_three_skips_sliding_window(void)
{
    g_case = "D2: 3 skips in 0.15 s across the old window reset";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    /* A skip needs an inhibit that cannot get out. Refusing every submit is the
     * cleanest way to produce one on demand. */
    fb_refuse_next(1000);

    const int64_t at[4] = { 0, 950000, 1050000, 1100000 };
    for (int i = 0; i < 4; i++)
    {
        while (fb_now() < at[i]) { fb_advance(1000); gs_tick(&s, fb_now(), 0); }
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        gs_tick(&s, fb_now(), 0);
        gs_command_received(&s, fb_now(), 0);
    }
    ck_eq(gs_stats(&s)->skipped, 4, "four skips counted");
    ck(gs_skip_trip(&s), "TRIP 7 FIRES: the newest three span 0.15 s");
    ck_eq(gs_stats(&s)->skip_window, 3,
          "and the reported window count is the three inside the last second");
}

/* Two skips in a second must NOT trip -- item 10 names this explicitly. */
static void case_two_skips_no_trip(void)
{
    g_case = "2 skips in 1 s: no trip";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());
    fb_refuse_next(1000);

    for (int i = 0; i < 2; i++)
    {
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        gs_tick(&s, fb_now(), 0);
        gs_command_received(&s, fb_now(), 0);
        fb_advance(300000);
    }
    ck_eq(gs_stats(&s)->skipped, 2, "two skips");
    ck(!gs_skip_trip(&s), "and no trip");
}

/*
 * Item 5's reporting, which is the finding the whole measurement produced: an
 * aborted frame is reported as a completion, so the scheduler must credit from
 * its OWN record. Here the telemetry frame is aborted and must NOT be counted as
 * sent, and the inhibit must not inherit its completion.
 */
static void case_abort_is_not_a_completion(void)
{
    g_case = "item 5: an abort is not credited as a send";
    fb_reset();
    fb_set_contended(true);     /* STRESS */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 50, 10, 0);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 300, 10, 0);

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->cls[GS_CLASS_TELEMETRY].sent, 0,
          "the aborted diag was NOT counted as sent");
    ck_eq(st->cls[GS_CLASS_TELEMETRY].aborted, 1, "it was counted as aborted");
    ck_eq(st->ontime + st->ontime_unverified, 0,
          "and no inhibit completion was credited yet");
}

/* An on-time claim with a backlog is counted separately and never summed in. */
static void case_ontime_unverified(void)
{
    g_case = "on-time with a backlog is unverified";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 400, 10, 3);        /* three frames waiting in the RX queue */

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(fb_wire_count(), 1, "the inhibit reached the wire");
    ck_eq(st->ontime, 0, "it was NOT counted as verified on time");
    ck_eq(st->ontime_unverified, 1, "it was counted as unverified");
    ck_eq(st->unverified_max_backlog, 3, "with the backlog recorded");
}

/* Telemetry queue overflow: drops counted (item 6). */
static void case_telemetry_overflow(void)
{
    g_case = "telemetry overflow: drops counted";
    fb_reset();
    fb_set_contended(true);     /* STRESS: nothing gets out, so the queue fills */
    gs_t s;
    gs_init(&s, fb_hal());

    int accepted = 0;
    for (int i = 0; i < GS_Q_TELEMETRY + 4; i++)
    {
        gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
        if (gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now())) { accepted++; }
    }
    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->cls[GS_CLASS_TELEMETRY].queued, GS_Q_TELEMETRY + 4,
          "every attempt counted as queued");
    ck_eq(st->cls[GS_CLASS_TELEMETRY].dropped,
          (GS_Q_TELEMETRY + 4) - accepted, "the rest counted as dropped");
    ck(st->cls[GS_CLASS_TELEMETRY].dropped > 0, "and there were drops");
}

/* Nothing is handed to the driver while an inhibit is outstanding (item 7). */
static void case_nothing_behind_an_inhibit(void)
{
    g_case = "item 7: nothing goes out behind an inhibit";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the inhibit sits awaiting */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 50, 10, 0);
    ck(gs_inhibit_outstanding(&s), "the inhibit is outstanding");

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 200, 10, 0);
    ck_eq(fb_submit_count(), 1, "ONLY the inhibit was handed over");
}

/*
 * D3. An inhibit that cannot even be QUEUED is a missed inhibit and must feed
 * trip 7, not just the `dropped` counter. Added because the mutation harness
 * reported this guard as uncovered -- the mutant passed the whole suite, which is
 * a gap in the suite rather than a defect in the code, and the harness says so in
 * those words.
 *
 * With the deadline purge in place this should be unreachable in normal operation,
 * which is exactly why it needs a case: nothing else would ever exercise it, and
 * an unreachable-but-wrong counter is how a trip quietly stops working.
 */
static void case_inhibit_queue_full_is_a_skip(void)
{
    g_case = "D3: an inhibit that cannot be queued is a skip";
    fb_reset();
    fb_set_contended(true);     /* STRESS: nothing leaves, so the queue fills */
    gs_t s;
    gs_init(&s, fb_hal());

    int accepted = 0;
    for (int i = 0; i < GS_Q_INHIBIT + 2; i++)
    {
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        if (gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now())) { accepted++; }
        /* No tick between queues: the point is a full QUEUE, not a full buffer. */
    }
    const gs_stats_t *st = gs_stats(&s);
    const uint32_t refused = (GS_Q_INHIBIT + 2) - (uint32_t)accepted;
    ck(refused > 0, "some inhibits could not be queued");
    ck_eq(st->cls[GS_CLASS_INHIBIT].dropped, refused, "each counted as dropped");
    ck_eq(st->skipped, refused, "AND each counted as a SKIP, feeding trip 7");
}


/*
 * R8. A HELD inhibit withdrawn at its deadline must NEVER be requeued, and must
 * never reach the wire. The mutant that requeues it (post-abort path,
 * `c != GS_CLASS_INHIBIT` -> `1`) sends the frame LATE, which is the failure the
 * whole skip rule exists to prevent -- and the suite was green through it,
 * because D1's case covers a QUEUED inhibit and nothing covered a held one.
 */
static void case_withdrawn_held_inhibit_never_returns(void)
{
    g_case = "R8: a withdrawn HELD inhibit never comes back";
    fb_reset();
    fb_set_contended(true);     /* STRESS: it sits AWAITING, never gets out */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 50, 10, 0);
    ck_eq(fb_submit_count(), 1, "the inhibit was handed over");
    ck(fb_state_now() == GS_BUF_AWAITING, "and is awaiting arbitration");

    const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
    ck(v == GS_DEADLINE_SKIPPED, "the deadline skips it");
    ck_eq(gs_stats(&s)->skipped, 1, "one skip");

    /* Let the bus go quiet and run a long time. It must not reappear. */
    fb_set_contended(false);
    run(&s, 5000, 10, 0);
    for (int i = 0; i < fb_wire_count(); i++)
    {
        ck(fb_wire_id(i) != ID_INHIBIT,
           "THE WITHDRAWN INHIBIT NEVER REACHED THE WIRE");
    }
    ck_eq(fb_submit_count(), 1, "and was never handed over a second time");
}

/*
 * R5. TRANSMITTING at the deadline is GS_DEADLINE_MAYBE_LATE, which IS a skip and
 * DOES move the 3-in-1 s window. The device cannot tell a late completion from a
 * successful abort, so the verdict records that ambiguity -- but MAYBE_LATE is a
 * skip KIND, not an immediate trip. Only TX_FAILED and a driver refusal trip at
 * once. The reasoning is in the note on the assertions below, and every deadline
 * path in gs_command_received() calls note_skip() accordingly.
 *
 * THIS COMMENT SAID THE OPPOSITE UNTIL 2026-09-27, contradicting the very
 * assertions beneath it, which had already been updated for the amended trip 7.
 * It cost a session a wrong reading of spec 5.2 item 10's catalogue: item 9 says
 * "a skip, not an immediate trip", this header said "the immediate trip", and the
 * two were reported to the user as a contradiction in the spec when the only
 * stale thing was this paragraph. A header comment that disagrees with its own
 * assertions is worse than none -- it is read first and trusted most.
 */
static void case_transmitting_at_deadline_is_maybe_late(void)
{
    g_case = "R5: transmitting at the deadline is MAYBE_LATE";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 100, 10, 0);        /* idle bus: transmitting, ~249 us to go */
    ck(fb_state_now() == GS_BUF_TRANSMITTING, "the inhibit is transmitting");

    const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
    ck(v == GS_DEADLINE_MAYBE_LATE, "the verdict is MAYBE_LATE");
    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->late_on_wire, 1, "late_on_wire counted");
    /*
     * UPDATED for spec trip 7 as amended 2026-09-27. This case used to assert
     * that a late frame was NOT a skip and did not move the window, because the
     * spec then said it tripped at once. The user changed that: the inverter has
     * already acted on the VCM's command with that counter and ignores ours, so
     * the effect is one real command acted on -- the same as a skip -- and only
     * TX_FAILED and a driver refusal still trip immediately. `skipped` is now the
     * TOTAL and `late_on_wire` a subset of it; they are never summed.
     */
    ck_eq(st->skipped, 1, "it IS counted as a skip (trip 7, amended)");
    ck_eq(st->skip_window, 1, "and it moves the 3-in-1s window");
}

/*
 * R4. An aborted telemetry page goes back at the HEAD of its class, so it is not
 * starved behind pages queued during its own preemption. Three distinct diag IDs
 * make the order readable.
 */
static void case_requeue_at_head(void)
{
    g_case = "R4: an aborted page requeues at the HEAD";
    fb_reset();
    fb_set_contended(true);     /* STRESS */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t a = mk(0x7F1, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &a, fb_now());
    run(&s, 50, 10, 0);
    ck_eq(fb_submit_count(), 1, "0x7F1 was handed over");

    /* Two more pages queue up behind it while it is in the buffer. */
    gi_frame_t b = mk(0x7F2, GI_TX_DIAG);
    gi_frame_t c = mk(0x7F3, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &b, fb_now());
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &c, fb_now());

    /* An inhibit preempts 0x7F1. */
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    fb_set_contended(false);
    run(&s, 4000, 10, 0);

    ck(fb_wire_count() >= 3, "several frames reached the wire");
    ck_eq(fb_wire_id(0), ID_INHIBIT, "the inhibit went first");
    ck_eq(fb_wire_id(1), 0x7F1, "THE PREEMPTED PAGE WENT NEXT, not 0x7F2");
}

/*
 * R1. Item 4's 1 ms bound is reached. The only way there: the buffer stays
 * TRANSMITTING longer than the bound, as an error-retransmit storm would, because
 * the loop correctly refuses to issue a command while transmitting and just waits.
 * The race mode gets us into that state from an abort that was authorised while
 * AWAITING.
 *
 * Spec item 4 on reaching the bound: the inhibit "stays queued at the head of its
 * class and the scheduler keeps trying until the deadline in item 5".
 */
static void case_abort_bound_is_reached(void)
{
    g_case = "R1: the 1 ms abort bound is reached and counted";
    fb_reset();
    fb_set_contended(true);     /* STRESS */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 50, 10, 0);
    ck(fb_state_now() == GS_BUF_AWAITING, "the diag is awaiting");

    /* The abort will be authorised at AWAITING and land at TS = 1 anyway, and
     * the frame then transmits for 4 ms -- far past the bound. */
    fb_stick_transmitting(4000);
    fb_race_next(3500);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());

    run(&s, 2000, 10, 0);
    const gs_stats_t *st = gs_stats(&s);
    ck(fb_aborts_with_no_effect() >= 1, "the race produced a no-effect abort");
    ck(st->abort_bound_hit >= 1, "THE 1 MS BOUND WAS REACHED AND COUNTED");
    ck_eq(fb_wire_count(), 0, "nothing has reached the wire yet");
    ck(gs_inhibit_outstanding(&s) || s.q[GS_CLASS_INHIBIT].n > 0,
       "and the inhibit is still pending, not dropped");
}

/*
 * R9. The probe class sits between inhibit and telemetry, and a probe in the
 * buffer must be preempted by an inhibit just as telemetry is. No case had a probe
 * in the buffer at all, so the whole middle class was untested.
 */
static void case_probe_is_preempted_by_inhibit(void)
{
    g_case = "R9: a probe in the buffer is preempted";
    fb_reset();
    fb_set_contended(true);     /* STRESS */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t p = mk(ID_PROBE, GI_TX_PROBE);
    gs_queue_frame(&s, GS_CLASS_PROBE, &p, fb_now());
    run(&s, 50, 10, 0);
    ck_eq(fb_submit_count(), 1, "the probe was handed over");

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    fb_set_contended(false);
    run(&s, 3000, 10, 0);

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->cls[GS_CLASS_PROBE].aborted, 1, "the probe was aborted");
    ck(fb_wire_count() >= 1, "something reached the wire");
    ck_eq(fb_wire_id(0), ID_INHIBIT, "and the INHIBIT was first");

    /* And priority order with nothing held: inhibit before probe. */
    fb_reset();
    gs_t s2;
    gs_init(&s2, fb_hal());
    gi_frame_t p2 = mk(ID_PROBE, GI_TX_PROBE);
    gi_frame_t i2 = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s2, GS_CLASS_PROBE, &p2, fb_now());
    gs_queue_frame(&s2, GS_CLASS_INHIBIT, &i2, fb_now());
    run(&s2, 2000, 10, 0);
    ck_eq(fb_submit_id(0), ID_INHIBIT, "queued together, the inhibit goes first");
}

/*
 * R11, THE BOUNDARY, AND THE ANSWER IS STATED RATHER THAN LEFT TO THE OPERATOR.
 * "3 skips within 1 s" is read as STRICTLY LESS THAN one second apart, so three
 * skips exactly 1.000 s apart end to end do NOT trip. Chosen that way because the
 * trip is a safety response and the spec's own numbers elsewhere (the 4.69 ms
 * guard band, the 0.3 s crank debounce) are all read as thresholds to be exceeded,
 * not met.
 */
static void case_skip_window_boundary_exactly_one_second(void)
{
    g_case = "R11: exactly 1.000 s apart does NOT trip";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());
    fb_refuse_next(1000);

    const int64_t at[3] = { 0, 500000, 1000000 };
    for (int i = 0; i < 3; i++)
    {
        while (fb_now() < at[i]) { fb_advance(1000); gs_tick(&s, fb_now(), 0); }
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        gs_tick(&s, fb_now(), 0);
        gs_command_received(&s, fb_now(), 0);
    }
    ck_eq(gs_stats(&s)->skipped, 3, "three skips");
    ck(!gs_skip_trip(&s),
       "and NO trip: the outer two are exactly 1.000 s apart");

    /* One microsecond closer and it trips, which is what makes the boundary a
     * boundary rather than an accident. */
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    gs_tick(&s, fb_now(), 0);
    gs_command_received(&s, fb_now(), 0);
    ck(gs_skip_trip(&s), "a fourth skip at the same instant DOES trip");
}


/*
 * THE RACE, and the reason item 4 is a loop rather than a single abort.
 *
 * The atomic HAL op reads the buffer state and issues the command as one step, so
 * the scheduler never *decides* to abort a transmitting frame. But the controller
 * is independent hardware and does not stop for a critical section: it can enter
 * arbitration between the read and the write taking effect, and the command then
 * lands at TS = 1, where the measurement says it has no effect (7 of 8 contended
 * trials). The frame keeps contending, loses, TS falls to 0, and the loop's next
 * pass aborts it for real.
 *
 * I ASSERTED THIS OUTCOME WAS UNREACHABLE FROM THE SCHEDULER AND WAS WRONG. The
 * reviewing session pointed out the race, 2026-09-27. It is why
 * fb_abort_cmds_while_transmitting() == 0 is a property of the non-racing path
 * only, and not an invariant.
 */
static void case_race_between_read_and_abort(void)
{
    g_case = "the read/abort race: the loop still recovers";
    fb_reset();
    fb_set_contended(true);     /* STRESS: saturated-bus figure, spec 12.2 */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 50, 10, 0);
    ck(fb_state_now() == GS_BUF_AWAITING, "the diag is awaiting");

    /* The next abort is authorised at AWAITING and lands at TS = 1 anyway. The
     * frame loses arbitration 120 us later. */
    fb_race_next(120);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    fb_set_contended(false);
    run(&s, 3000, 10, 0);

    ck_eq(fb_aborts_with_no_effect(), 1, "one abort really had no effect");
    ck(fb_abort_cmds_while_transmitting() >= 1,
       "and it was issued while transmitting -- via the race, not by choice");
    ck(fb_abort_cmds() >= 2, "the LOOP issued a further command");

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->abort_bound_hit, 0, "the 1 ms bound was not reached");
    ck(fb_wire_count() >= 1, "something reached the wire");
    ck_eq(fb_wire_id(0), ID_INHIBIT, "THE INHIBIT WAS STILL FIRST");
    ck_eq(st->skipped, 0, "and nothing was skipped");
}


/*
 * THE INTEGRATION ORDER, all four combinations. Spec item 5: the deadline is
 * evaluated when the VCM's next 0x051 is dequeued, BEFORE the answer to that
 * command is queued.
 *
 * Each ordering is run twice, with the previous inhibit COMPLETE and with it still
 * OUTSTANDING, because that is the variable the weak version of this check turned
 * on: testing `inhibit_outstanding` caught the swap only when the previous frame
 * was still in flight, which is the rare case. The common case -- previous frame
 * long finished -- went entirely undetected, and the reviewing session found it.
 */
static void order_once(bool correct_order, bool prev_outstanding,
                       uint32_t *violations)
{
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    /* One command and answer, so there IS a previous inhibit. */
    gs_command_received(&s, fb_now(), 0);
    gi_frame_t first = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &first, fb_now());

    if (prev_outstanding)
    {
        /* Contended: it is handed over and sits AWAITING, never completing. */
        fb_set_contended(true);
        run(&s, 50, 10, 0);
    }
    else
    {
        /* Idle: it completes long before the next command. */
        run(&s, 600, 10, 0);
    }

    /* The next command arrives, and its answer is queued. */
    gi_frame_t second = mk(ID_INHIBIT, GI_TX_INHIBIT);
    if (correct_order)
    {
        gs_command_received(&s, fb_now(), 0);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &second, fb_now());
    }
    else
    {
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &second, fb_now());
        gs_command_received(&s, fb_now(), 0);
    }
    *violations = gs_stats(&s)->order_violations;
}

/*
 * D8. A RE-ARM MUST NOT FORGET WHAT THE CONTROLLER IS HOLDING.
 *
 * gs_init() memsets the whole struct, `held`, `held_f` and `aborting` included,
 * and a mode change can land while a diag page is still in the buffer. A
 * scheduler that has forgotten it will hand over the next frame at once: on the
 * device twai_transmit() queues it BEHIND the old one in the driver's FIFO, which
 * is the priority inversion item 3 exists to prevent, and the old frame's
 * departure gets credited to the new one.
 *
 * THE MODEL SHOWS THIS AS A REFUSAL, NOT AS TWO FRAMES IN THE BUFFER.
 * fb_submit() refuses while occupied -- "the scheduler must never do this: item
 * 3" -- so the fault surfaces as a handover that should never have been
 * attempted. Same fault, different symptom, and the assertion is on the attempt.
 *
 * The reviewing session's round 13 left this as its one survivor: gs_rearm()
 * swapped for gs_init() passed every suite, in both this file and E1.
 */
static void case_rearm_keeps_what_the_hardware_holds(void)
{
    g_case = "D8: a re-arm does not forget a frame in the controller";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the page sits AWAITING, not gone */
    gs_t s;
    gs_init(&s, fb_hal());

    /* The first diag page is handed over and is still in the buffer. */
    gi_frame_t page_a = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page_a, fb_now());
    run(&s, 50, 10, 0);
    ck_eq(fb_submit_count(), 1, "the first page was handed over");
    ck(fb_outstanding_now(), "and the controller still holds it");

    /* The mode change. This is the call under test. */
    gs_rearm(&s, fb_hal());

    /*
     * THE SECOND FRAME IS THE SAME CLASS, DELIBERATELY. The first version of this
     * case queued an INHIBIT here and asserted that nothing more was handed over
     * -- which fails on correct code, because an inhibit behind an AWAITING
     * telemetry frame is precisely what item 6 preemption is for: abort the page,
     * hand the inhibit over. Two submits is the right answer there, so that
     * scenario cannot ask D8's question at all.
     *
     * With a same-class successor there is no preemption to reach for, and the
     * only thing deciding whether the scheduler waits is whether it still knows
     * the controller is holding something.
     */
    gi_frame_t page_b = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page_b, fb_now());
    run(&s, 100, 10, 0);

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->cls[GS_CLASS_TELEMETRY].refused, 0,
          "the second page was NOT offered to a driver still holding the first");
    ck_eq(fb_submit_count(), 1, "still only the first page has been handed over");

    /*
     * Let the first page lose arbitration and leave, then the second go. Both
     * must be on the wire, and every frame on the wire must be one the scheduler
     * counted as sent -- which is the form "the departure is not credited to the
     * wrong frame" takes here, because gs_init() clears `held` and so credits the
     * first page's departure to nobody at all.
     */
    fb_set_contended(false);
    run(&s, 3000, 10, 0);

    ck_eq(fb_wire_count(), 2, "both pages reached the wire");
    ck_eq(st->cls[GS_CLASS_TELEMETRY].sent, 2,
          "and both were counted as sent; a forgotten held frame is a frame on "
          "the wire that nothing credited");
}


/* ------------------------------------------- spec 5.2 item 6's balance --- */

/*
 * `held` FOR ONE CLASS, read straight off the struct as the status page does.
 * Spec 5.2 item 6 (2026-09-29) makes this a term of the per-class balance:
 *
 *     queued + carried_in == sent + dropped + withdrawn + depth + held
 *
 * At most one class can read 1, because the controller holds at most one frame.
 */
static long held_in(const gs_t *s, gs_class_t c)
{
    return (s->held && s->held_class == c) ? 1 : 0;
}

/* The residual the two new terms exist to remove. 0 means the class balances. */
static long residual(const gs_t *s, gs_class_t c)
{
    const gs_class_stats_t *k = &s->st.cls[c];
    return (long)k->queued + (long)k->carried_in
         - ((long)k->sent + (long)k->dropped + (long)k->withdrawn
            + (long)s->q[c].n + held_in(s, c));
}

/*
 * Spec 12.4: "with a frame in flight at the reading". Four bench arms read -1
 * here and the frame was in the controller, not lost -- which is the one gap
 * "telemetry is never lost silently" cannot be allowed to leave open.
 */
static void case_identity_frame_in_flight(void)
{
    g_case = "item 6: the balance closes with a frame in the controller";
    fb_reset();
    /* STRESS: contention keeps the frame AWAITING, so it is still held at the
     * reading. That is exactly the state the bench arms were read in. */
    fb_set_contended(true);
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
    run(&s, 50, 10, 0);

    ck(fb_outstanding_now(), "the controller is holding the page");
    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_TELEMETRY];
    ck_eq(c->queued, 1, "queued 1");
    ck_eq(c->sent, 0, "not sent yet");
    ck_eq(c->dropped, 0, "not dropped");
    ck_eq(s.q[GS_CLASS_TELEMETRY].n, 0, "not in the class queue either");
    ck_eq(c->carried_in, 0, "nothing was carried into this arm");
    ck_eq(held_in(&s, GS_CLASS_TELEMETRY), 1, "held reads 1");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0,
          "the balance closes WITH the held term");
    ck_eq(c->withdrawn, 0, "and nothing was withdrawn: this is not an inhibit");

    /* And it still closes once the frame really goes. */
    fb_set_contended(false);
    run(&s, 3000, 10, 0);
    ck_eq(fb_wire_count(), 1, "the page reached the wire");
    ck_eq(c->sent, 1, "and was counted as sent");
    ck_eq(held_in(&s, GS_CLASS_TELEMETRY), 0, "nothing is held now");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "the balance still closes");
}

/*
 * Spec 12.4: "across a re-arm with a frame held". Three bench arms read +1 here
 * -- one more frame left than this arm ever queued -- and all three also carried
 * an impossible max_hold_us of 142-310 s on a 90 s arm, which is the same frame
 * seen from the other side.
 */
static void case_identity_carried_across_rearm(void)
{
    g_case = "item 6: carried_in closes the balance across a re-arm";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the page is still AWAITING at the re-arm */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t page_a = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page_a, fb_now());
    run(&s, 50, 10, 0);
    ck(fb_outstanding_now(), "the controller holds the first page");

    /* Let real time pass before the re-arm, so a hold booked into the new arm
     * would be obvious rather than a rounding artefact. */
    fb_advance(200000);         /* 200 ms */
    gs_rearm(&s, fb_hal());

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_TELEMETRY];
    ck_eq(c->queued, 0, "the new arm has queued nothing");
    ck_eq(c->carried_in, 1, "and declares the frame it inherited");
    ck_eq(held_in(&s, GS_CLASS_TELEMETRY), 1, "which is still held");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0,
          "so a reading taken right after the re-arm balances");

    /* Now let it complete. */
    fb_set_contended(false);
    run(&s, 3000, 10, 0);

    ck_eq(c->sent, 1, "the inherited frame was counted as sent");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0,
          "queued 0 + carried_in 1 == sent 1, so the arm still balances");
    /*
     * THE POINT OF (A). Its hold began 200 ms+ before this arm existed, and the
     * arm cannot know how long it really was, so it books nothing -- not the
     * elapsed figure, and not a value clamped to the re-arm instant either,
     * because that would be a measurement the device never made.
     */
    ck_eq(c->max_hold_us, 0,
          "the carried frame's hold is NOT booked into the new arm");

    /*
     * AND THE EXCLUSION IS NOT A BLANKET SUPPRESSION. A frame this arm really
     * did hand over must still have its hold measured, or the fix would have
     * silenced the counter instead of correcting it -- a failure that looks
     * exactly like a healthy 0.
     */
    fb_set_contended(true);
    gi_frame_t page_b = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page_b, fb_now());
    run(&s, 500, 10, 0);
    fb_set_contended(false);
    run(&s, 3000, 10, 0);
    ck_eq(c->sent, 2, "the second page went too");
    ck(c->max_hold_us > 0,
       "and ITS hold WAS measured; the exclusion is per-frame, not per-arm");
}

/*
 * A WITHDRAWN INHIBIT, which item 6 balances through `withdrawn` (user,
 * 2026-09-29). This case first shipped asserting a residual of exactly 1, because
 * the identity as originally written had no term for a frame popped at its
 * deadline and counted only as a skip -- so the inhibit class read one over on any
 * arm with a skip. That went unnoticed because NO RECORDED BENCH READING HAS A
 * NON-ZERO `skipped` (checked: 31 readings across every run file), so the missing
 * term was never exercised on hardware.
 *
 * It now asserts the balance, and also the invariant that ties the per-class term
 * to trip 7's counter:
 *
 *     cls[INHIBIT].withdrawn + cls[INHIBIT].dropped == skipped
 *
 * That holds once any in-progress abort has completed. While one is still running
 * the frame has had note_skip() called but not yet been booked as withdrawn, and
 * in exactly that state it is still counted in `held` -- which is what keeps the
 * identity closed in the meantime. This case withdraws a frame that was never
 * handed over, so there is no abort to wait for.
 */
static void case_identity_withdrawn_inhibit_balances(void)
{
    g_case = "item 6: a withdrawn inhibit balances through `withdrawn`";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    /* One command, one answer, queued but not yet handed over. */
    (void)gs_command_received(&s, fb_now(), 0);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());

    /* The VCM's NEXT command arrives before it went: the deadline withdraws it. */
    const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
    ck_eq((long)v, (long)GS_DEADLINE_SKIPPED, "it was withdrawn, never on the wire");

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(c->queued, 1, "it was queued");
    ck_eq(c->sent, 0, "never sent");
    ck_eq(c->dropped, 0, "not dropped -- a withdrawal is not a drop");
    ck_eq(s.q[GS_CLASS_INHIBIT].n, 0, "and it is no longer in the queue");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "nothing is held");
    ck_eq(c->carried_in, 0, "nothing was carried in");
    ck_eq(s.st.skipped, 1, "it is counted as a skip");
    ck_eq(s.st.skipped_withdrawn, 1, "and as a withdrawn one");
    ck_eq(c->withdrawn, 1, "and the class books the identity's term");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "so the balance closes");
    ck_eq((long)(c->withdrawn + c->dropped), (long)s.st.skipped,
          "withdrawn + dropped == skipped");
    ck_eq(fb_wire_count(), 0, "nothing reached the wire, which is the point");
}

/*
 * THE OTHER withdrawn SITE: the frame the CONTROLLER was holding.
 *
 * The case above withdraws a frame that never left its class queue, so it books
 * `withdrawn` at the purge. A held frame is given up in a different place -- the
 * abort-completion branch of gs_tick() step 3 -- and dropping that increment left
 * the entire suite green, which is how this case came to exist.
 *
 * It also pins the window where `withdrawn + dropped == skipped` is NOT yet true:
 * note_skip() runs at the deadline, but the frame is not booked as withdrawn until
 * the abort completes, and in between it is still counted in `held`. That is what
 * keeps the identity closed meanwhile, so both states are asserted rather than
 * only the settled one.
 */
static void case_identity_withdrawn_held_inhibit_balances(void)
{
    g_case = "item 6: a WITHDRAWN HELD inhibit balances too";
    fb_reset();
    /* STRESS: contention keeps the frame awaiting arbitration, so the deadline
     * finds it in the buffer and the abort can actually remove it. */
    fb_set_contended(true);
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now(), 0);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 50, 10, 0);

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(c->handed, 1, "the inhibit was handed to the controller");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "and is held");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes while held");

    /* The VCM's next command: the deadline gives the frame up. */
    const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
    ck_eq((long)v, (long)GS_DEADLINE_SKIPPED,
          "awaiting arbitration at the deadline: withdrawn, never on the wire");
    ck_eq(s.st.skipped, 1, "counted as a skip immediately");

    /*
     * MID-WITHDRAWAL. The skip is counted and `withdrawn` is not yet, so the
     * invariant below does not hold here -- and the identity still does, because
     * the frame is counted in `held`. Asserting this state is the point: a later
     * change that booked `withdrawn` at the deadline instead of at the abort would
     * balance at the end and be wrong in between.
     */
    if (held_in(&s, GS_CLASS_INHIBIT) == 1)
    {
        ck_eq(residual(&s, GS_CLASS_INHIBIT), 0,
              "still balances mid-withdrawal, through `held`");
    }

    /* Let the abort loop finish. */
    run(&s, 3000, 10, 0);

    ck_eq(fb_wire_count(), 0, "the frame never reached the wire");
    ck_eq(c->sent, 0, "and was never credited as sent");
    ck_eq(c->dropped, 0, "a withdrawal is not a drop");
    ck_eq(s.q[GS_CLASS_INHIBIT].n, 0, "nothing is queued");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "nothing is held");
    ck_eq(c->withdrawn, 1, "the abort branch booked the term");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "so the balance closes");
    ck_eq((long)(c->withdrawn + c->dropped), (long)s.st.skipped,
          "withdrawn + dropped == skipped, once the abort has completed");
}

/*
 * gs_withdraw_inhibits()'s OWN purge, the third and last site that books
 * `withdrawn`. The core calls it whenever it stops being live -- any section 7
 * abort, any disable, any mode change out of INHIBIT -- so it is the path that
 * runs precisely when someone goes looking at the counters to find out what
 * happened. Deleting its increment left every other case green.
 *
 * The frame is withdrawn straight out of the class queue with no gs_tick() in
 * between, so it is never handed over and there is no abort to wait for: the
 * purge is the whole of it.
 */
static void case_identity_withdraw_api_balances(void)
{
    g_case = "item 6: gs_withdraw_inhibits' purge books `withdrawn`";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now(), 0);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(c->queued, 1, "queued");
    ck_eq(s.q[GS_CLASS_INHIBIT].n, 1, "and sitting in its class queue");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes while queued");

    /* The core stops being live. */
    gs_withdraw_inhibits(&s, fb_now());

    ck_eq(s.q[GS_CLASS_INHIBIT].n, 0, "the queue was purged");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "nothing was handed over");
    ck_eq(c->sent, 0, "nothing was sent");
    ck_eq(c->dropped, 0, "and a withdrawal is not a drop");
    ck_eq(s.st.skipped, 1, "it is counted as a skip");
    ck_eq(s.st.skipped_withdrawn, 1, "a withdrawn one");
    ck_eq(c->withdrawn, 1, "and the class books the identity's term");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "so the balance closes");
    ck_eq((long)(c->withdrawn + c->dropped), (long)s.st.skipped,
          "withdrawn + dropped == skipped");

    /* Nothing may come back later: item 5 forbids sending a skipped frame. */
    run(&s, 3000, 10, 0);
    ck_eq(fb_wire_count(), 0, "and it never reaches the wire afterwards");
    ck_eq(c->sent, 0, "nor is it ever credited as sent");
}

/* ------------------------------------ item 10 case 3: sustained saturation --- */

/*
 * Count what actually reached the wire, by class -- the check the device cannot
 * make, because the driver reports an aborted frame and a real transmission
 * identically (169 of 169 measured).
 *
 * BOUNDED BY THE MODEL: fake_buf logs the wire into a fixed 64-entry array and
 * stops recording silently when it is full. So every use of this must first
 * establish the log is not saturated -- otherwise "the count did not increase"
 * passes however the scheduler behaved, which is the failure-looks-like-success
 * shape this project's notes warn about. wire_log_sane() is that guard.
 */
static int wire_count_id(uint32_t id)
{
    int n = 0;
    for (int i = 0; i < fb_wire_count(); i++)
    {
        if (fb_wire_id(i) == id) { n++; }
    }
    return n;
}

static void wire_log_sane(void)
{
    ck(fb_wire_count() < 64,
       "the model's 64-frame wire log is FULL, so the wire counts in this phase "
       "prove nothing -- shorten the phase");
}

/*
 * ITEM 10 CASE 3, EMULATION HALF: "sustained bus saturation: no inhibit late,
 * telemetry delayed and counted, never silently lost."
 *
 * The bench half passes (runs/e4_case3_withdrawn.json, 92.5 % carried). Item 10
 * requires both runners with the same pass criteria, and until this case existed
 * saturation appeared in this suite only as fb_set_contended(true) used as a
 * stress knob inside cases about something else -- never as the subject.
 *
 * WHAT ONLY THIS RUNNER CAN DO. The third criterion is "never silently LOST", and
 * on the bench nothing can be made to lose on purpose: you saturate the wire and
 * observe that it did not. `skipped` is 0 in all 31 readings this project has ever
 * recorded, so the loss paths are unexercised on hardware. Here a loss is staged
 * and then required to be counted, which is what the criterion says -- losses are
 * permitted, silence is not.
 *
 * WHAT ONLY THE BENCH CAN DO, so this is not over-read: the arbitration wait here
 * is one calibrated constant (FB_ARB_WAIT_US 400 us, from a flood two witnesses
 * measured at ~3978 frames/s), not a bus. The agreement is worth having -- 400 +
 * 249 us puts a frame's hold at ~649 us against the bench arm's measured
 * max_hold_us of 730 us -- but this suite's README records a model erring in the
 * defect's own direction and proving nothing, which is why both halves are
 * required.
 *
 * STRESS throughout, per spec 12.2.
 */
static void case_saturation_sustained(void)
{
    g_case = "item 10 case 3: sustained saturation (emulation half)";
    fb_reset();
    fb_set_contended(true);         /* STRESS: saturated bus, spec 12.2 */
    gs_t s;
    gs_init(&s, fb_hal());

    const int64_t VCM_PERIOD_US = 10000;        /* the truck's 100 Hz */
    const gs_class_stats_t *inhib = &s.st.cls[GS_CLASS_INHIBIT];
    const gs_class_stats_t *telem = &s.st.cls[GS_CLASS_TELEMETRY];

    /* --- phase 0: short enough that the wire log is still evidence --------- */
    const int N_SHORT = 40;
    const int TELEM_EVERY = 12;
    int pages_offered = 0;
    for (int i = 0; i < N_SHORT; i++)
    {
        (void)gs_command_received(&s, fb_now(), 0);
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        if (i % TELEM_EVERY == 0)
        {
            gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
            (void)gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
            pages_offered++;
        }
        run(&s, VCM_PERIOD_US, 250, 0);
    }

    wire_log_sane();
    ck_eq(wire_count_id(ID_INHIBIT), (int)inhib->sent,
          "every inhibit counted as sent really reached the wire");
    ck_eq(wire_count_id(ID_DIAG), (int)telem->sent,
          "and every telemetry frame counted as sent did too");
    ck_eq(inhib->sent, N_SHORT, "all of them, under saturation");

    /* --- phase 1: sustain it -----------------------------------------------
     * No absolute wire assertions past here: the log saturates at 64 and would
     * make them meaningless rather than false. The counters and the identity have
     * no such ceiling.
     */
    const int N_MORE = 200;
    int deadlines_ok = 0;
    for (int i = 0; i < N_MORE; i++)
    {
        const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
        if (v == GS_DEADLINE_OK) { deadlines_ok++; }
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        if (i % TELEM_EVERY == 0)
        {
            gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
            (void)gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
            pages_offered++;
        }
        run(&s, VCM_PERIOD_US, 250, 0);
    }

    /* 1. NO INHIBIT LATE. */
    ck_eq(deadlines_ok, N_MORE, "every deadline found the inhibit already out");
    ck_eq(s.st.late_on_wire, 0, "no inhibit went out late");
    ck_eq(s.st.skipped, 0, "and none was skipped");
    ck_eq(inhib->withdrawn, 0, "so nothing was withdrawn");
    ck_eq(s.st.order_violations, 0, "the deadline ran before each answer");
    ck_eq(inhib->queued, N_SHORT + N_MORE, "one answer queued per command");
    ck_eq(inhib->sent, N_SHORT + N_MORE, "and every one was sent");
    ck_eq(inhib->dropped, 0, "no inhibit was dropped");
    ck_eq(s.st.ontime, N_SHORT + N_MORE,
          "all verified on time (the receive queue was empty throughout)");
    ck_eq(s.st.ontime_unverified, 0, "none unverified");

    /* 2. TELEMETRY DELAYED, and measurably -- it yields to the inhibit. */
    ck(telem->max_queue_us > 0, "telemetry waited");
    ck(telem->max_queue_us > inhib->max_queue_us,
       "and waited LONGER than the inhibit, which is what the classes are for; an "
       "equal wait would mean the priority never bit");

    /* 3. NEVER SILENTLY LOST. */
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "the telemetry balance closes");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "and so does the inhibit's");
    ck_eq((long)telem->queued, (long)pages_offered,
          "`queued` counts every page offered");

    /* --- phase 2: a staged loss, which only this runner can force ---------- */
    /*
     * A run that loses nothing cannot test a criterion about losses. GS_Q_TELEMETRY
     * is 6, so ten pages in one window overflows it while the buffer is busy.
     */
    const uint32_t dropped_before = telem->dropped;
    const uint32_t sent_before = telem->sent;
    for (int i = 0; i < 10; i++)
    {
        gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
        (void)gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
        pages_offered++;
    }
    ck(telem->dropped > dropped_before,
       "the overflow really dropped pages -- without that this phase tests "
       "nothing");

    for (int i = 0; i < 40; i++)
    {
        (void)gs_command_received(&s, fb_now(), 0);
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        run(&s, VCM_PERIOD_US, 250, 0);
    }

    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0,
          "after a drop episode every page offered is still accounted for: sent, "
          "dropped, queued or held -- none vanished");
    ck_eq((long)telem->queued, (long)pages_offered,
          "and the drops are counted in `queued` too");
    ck(telem->sent > sent_before, "pages kept flowing after the overflow");
    ck_eq(s.st.late_on_wire, 0, "and no inhibit went late through any of it");
    ck_eq(s.st.skipped, 0, "nor was one skipped");
}

/*
 * THE SKIP PATH, UNDER SATURATION -- the state combination that exists nowhere
 * else. `skipped` is 0 in all 31 recorded bench readings, so nothing on hardware
 * has ever driven this, and case 3's "no inhibit late" clause is only meaningful
 * if a frame that cannot get out is withdrawn rather than sent late.
 *
 * Its own case, and its own fb_reset(), so the 64-frame wire log is still evidence
 * when it matters: the whole point is that the skipped frame NEVER appears on the
 * wire, and on a saturated log that assertion would pass regardless.
 */
static void case_saturation_skip_is_counted(void)
{
    g_case = "item 10 case 3: a skip under saturation is counted, never sent";
    fb_reset();
    fb_set_contended(true);         /* STRESS: saturated bus, spec 12.2 */
    gs_t s;
    gs_init(&s, fb_hal());

    const int64_t VCM_PERIOD_US = 10000;
    const gs_class_stats_t *inhib = &s.st.cls[GS_CLASS_INHIBIT];

    /* A few normal cycles first, so this is a skip DURING traffic. */
    for (int i = 0; i < 6; i++)
    {
        (void)gs_command_received(&s, fb_now(), 0);
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        run(&s, VCM_PERIOD_US, 250, 0);
    }
    ck_eq(inhib->sent, 6, "six answers out before the interesting part");

    /*
     * Put a telemetry page in the buffer and make it un-abortable for longer than
     * the VCM's period. Item 4 says a frame already transmitting cannot be
     * aborted -- the inhibit waits for it -- so our answer cannot get out.
     */
    gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
    run(&s, 500, 250, 0);
    fb_stick_transmitting(30000);

    (void)gs_command_received(&s, fb_now(), 0);
    gi_frame_t stuck = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &stuck, fb_now());
    run(&s, VCM_PERIOD_US, 250, 0);
    ck_eq(inhib->sent, 6, "it did not get out: the buffer is still transmitting");

    const int wire_inh_before = wire_count_id(ID_INHIBIT);
    const uint32_t skipped_before = s.st.skipped;

    /* The VCM's next command arrives with our answer still queued. */
    const gs_deadline_t v = gs_command_received(&s, fb_now(), 0);
    ck_eq((long)v, (long)GS_DEADLINE_SKIPPED,
          "never handed over, so it is a clean skip and not a maybe-late");
    ck(s.st.skipped > skipped_before, "the skip is counted");
    ck_eq(s.st.skipped_withdrawn, s.st.skipped, "as a withdrawn one");
    ck_eq(inhib->sent, 6, "and it is NOT credited as sent");
    ck_eq(inhib->withdrawn, 1, "the class books the identity's term");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "so the balance closes");
    ck_eq((long)(inhib->withdrawn + inhib->dropped), (long)s.st.skipped,
          "withdrawn + dropped == skipped (nothing is mid-abort: it was never "
          "handed over)");

    /*
     * RELEASE THE KNOB BEFORE ASKING FOR NORMAL BEHAVIOUR. fb_stick_transmitting()
     * sets a sticky global that fb_air() returns for EVERY later frame, not just
     * the one in the buffer -- so leaving it at 30 ms made the next inhibit take
     * 30 ms of air time and it was still transmitting, uncredited, when the 10 ms
     * window closed. That read as "the command after a skip was not answered",
     * which is a serious-looking failure with a harness cause.
     */
    fb_stick_transmitting(0);

    /* Item 5: a skipped frame is never sent later. Let everything drain. */
    run(&s, 80000, 250, 0);
    wire_log_sane();
    ck_eq(wire_count_id(ID_INHIBIT), wire_inh_before,
          "the skipped inhibit never reaches the wire, then or afterwards");

    /* AND THE NEXT COMMAND IS ANSWERED NORMALLY (item 10's skip bullet). A skip
     * that poisoned the following cycle would be far worse than the skip. */
    const uint32_t sent_before_next = inhib->sent;
    (void)gs_command_received(&s, fb_now(), 0);
    gi_frame_t next = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &next, fb_now());
    run(&s, VCM_PERIOD_US, 250, 0);
    ck_eq(inhib->sent, sent_before_next + 1,
          "the command after a skip is answered normally");
    ck_eq(s.st.order_violations, 0, "and no ordering violation anywhere in the run");
}

/*
 * THE REQUEUE HALF OF THE CARRIED-IN EXCLUSION (spec 5.2 item 6's "the longest
 * hold and the longest wait both exclude a carried-in frame").
 *
 * case_identity_carried_across_rearm covers the HOLD. This covers the WAIT, and it
 * exists because three separate mutations survived the whole suite: dropping the
 * `!sl->carried` guard on max_queue_us, dropping q_push's reset of that flag, and
 * requeueing with the flag cleared. max_queue_us was asserted nowhere with a
 * carried frame in play.
 *
 * THE PATH. A telemetry page is in the controller when the mode changes, so the new
 * arm inherits it. An inhibit then arrives, preempts it, and the abort requeues it
 * -- with `t_queued` taken from `held_since`, a timestamp from the PREVIOUS arm,
 * because there is nowhere else to get one and the frame really has been waiting
 * that long. Its next handover must book nothing: the new arm cannot know how long
 * it waited, and clamping it to the re-arm instant would be a figure the device
 * never measured.
 */
static void case_identity_carried_requeue_sets_no_wait(void)
{
    g_case = "item 6: a carried-in frame's REQUEUED wait is not booked either";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the page stays AWAITING, so it is held */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t page_a = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page_a, fb_now());
    run(&s, 50, 10, 0);
    ck(fb_outstanding_now(), "the controller holds the page");

    /*
     * RE-ARM AT ONCE, and the window is short for a reason worth recording: the
     * model cannot hold a frame AWAITING for long. Contention costs 400 us of
     * arbitration and then the frame starts transmitting, so advancing virtual
     * time to make the carried frame "old" instead lets it COMPLETE -- the first
     * version of this case advanced 500 ms and the page was sent, not carried,
     * so the preemption it was written to drive never happened.
     *
     * The figure a mutation books is therefore a few hundred microseconds rather
     * than half a second. That is still unambiguous against an expected 0, which
     * is what makes the assertion work; it is the SIGN of the bug that matters,
     * not its size.
     */
    gs_rearm(&s, fb_hal());

    const gs_class_stats_t *telem = &s.st.cls[GS_CLASS_TELEMETRY];
    ck_eq(telem->carried_in, 1, "the new arm inherited it");
    ck_eq(telem->max_queue_us, 0, "and starts with no wait booked");

    /*
     * An inhibit in the new arm preempts it. The buffer is AWAITING, so item 6's
     * preemption aborts the page and requeues it at the head of its class.
     */
    (void)gs_command_received(&s, fb_now(), 0);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 5000, 10, 0);

    ck_eq(telem->aborted, 1, "the page was aborted");
    ck_eq(telem->requeued, 1, "and requeued rather than dropped");

    /*
     * THE FLAG TRAVELLED WITH THE FRAME. Asserted on the slot directly, because
     * the behavioural consequence below cannot distinguish "the flag was lost" from
     * "the frame happened not to wait".
     */
    int carried_slots = 0;
    int carried_slot_ix = -1;
    for (int i = 0; i < GS_Q_TELEMETRY; i++)
    {
        if (s.q[GS_CLASS_TELEMETRY].q[i].carried)
        {
            carried_slots++;
            carried_slot_ix = i;
        }
    }
    ck_eq(carried_slots, 1, "exactly one queued slot is marked carried");
    ck(carried_slot_ix >= 0, "and its index was found");

    /* Let the inhibit go, then the requeued page. */
    fb_set_contended(false);
    run(&s, 5000, 10, 0);
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 1, "the inhibit went first");
    ck_eq(telem->sent, 1, "then the carried page");

    /*
     * THE POINT. Its `t_queued` is 500 ms+ in the past and in another arm, so the
     * handover must have booked nothing. Without the guard, or with the flag lost
     * at the requeue, this reads about half a second.
     */
    ck_eq(telem->max_queue_us, 0,
          "the requeued carried frame's wait is NOT booked into the new arm");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "and the balance still closes");

    /*
     * AND THE SLOT IS CLEAN FOR THE NEXT FRAME. q_pop() does not clear the slot it
     * leaves, so if q_push() did not reset the flag, the next frame to land in that
     * index would inherit `carried` and have its genuine wait silently suppressed.
     * Filling every slot is what makes this deterministic -- which index the next
     * push lands in is not something a test should have to predict.
     */
    fb_set_contended(true);
    gi_frame_t fresh[GS_Q_TELEMETRY];
    for (int i = 0; i < GS_Q_TELEMETRY; i++)
    {
        fresh[i] = mk(ID_DIAG, GI_TX_DIAG);
        ck(gs_queue_frame(&s, GS_CLASS_TELEMETRY, &fresh[i], fb_now()),
           "the fresh page was accepted");
    }
    int still_carried = 0;
    for (int i = 0; i < GS_Q_TELEMETRY; i++)
    {
        if (s.q[GS_CLASS_TELEMETRY].q[i].carried) { still_carried++; }
    }
    ck_eq(still_carried, 0,
          "no slot still reads `carried` once fresh frames have been pushed into "
          "every one; a stale flag would suppress a real wait figure");

    /* And a fresh frame that really waits DOES book it -- the exclusion is
     * per-frame, not a blanket silencing of the counter. */
    run(&s, 20000, 10, 0);
    ck(telem->max_queue_us > 0,
       "a fresh frame's genuine wait IS booked, so the exclusion did not just "
       "turn the counter off");
}

static void case_integration_order(void)
{
    uint32_t v;

    g_case = "order: correct, previous complete";
    order_once(true, false, &v);
    ck_eq(v, 0, "no violation");

    g_case = "order: correct, previous still outstanding";
    order_once(true, true, &v);
    ck_eq(v, 0, "no violation");

    /*
     * THE CASE THE WEAK CHECK MISSED. Previous frame complete, calls swapped: the
     * old `inhibit_outstanding` test saw nothing outstanding and counted nothing,
     * while the deadline purge quietly withdrew the new inhibit on every command.
     */
    g_case = "order: SWAPPED, previous complete (the missed case)";
    order_once(false, false, &v);
    ck_eq(v, 1, "ONE violation counted");

    g_case = "order: SWAPPED, previous still outstanding";
    order_once(false, true, &v);
    ck_eq(v, 1, "ONE violation counted");
}

int main(void)
{
    printf("scheduler cases (spec 5.2 item 10, emulation half)\n");
    case_preempt_awaiting_telemetry();
    case_never_aborts_while_transmitting();
    case_deadline_purges_queued_inhibit();
    case_three_skips_sliding_window();
    case_two_skips_no_trip();
    case_abort_is_not_a_completion();
    case_ontime_unverified();
    case_telemetry_overflow();
    case_nothing_behind_an_inhibit();
    case_inhibit_queue_full_is_a_skip();
    case_withdrawn_held_inhibit_never_returns();
    case_transmitting_at_deadline_is_maybe_late();
    case_requeue_at_head();
    case_abort_bound_is_reached();
    case_probe_is_preempted_by_inhibit();
    case_skip_window_boundary_exactly_one_second();
    case_race_between_read_and_abort();
    case_integration_order();
    case_rearm_keeps_what_the_hardware_holds();
    case_identity_frame_in_flight();
    case_identity_carried_across_rearm();
    case_identity_withdrawn_inhibit_balances();
    case_identity_withdrawn_held_inhibit_balances();
    case_identity_withdraw_api_balances();
    case_saturation_sustained();
    case_saturation_skip_is_counted();
    case_identity_carried_requeue_sets_no_wait();

    if (g_fail == 0)
    {
        printf("all cases passed\n");
        return 0;
    }
    printf("%d assertion(s) FAILED\n", g_fail);
    return 1;
}
