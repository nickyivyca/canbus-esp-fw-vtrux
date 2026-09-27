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
 * R5. TRANSMITTING at the deadline is GS_DEADLINE_MAYBE_LATE, not a skip. The
 * device cannot tell a late completion from a successful abort, so the ambiguous
 * case is reported as the immediate trip and the skip window does NOT move --
 * mixing it into the skips would both understate the severity and pollute the
 * 3-in-1 s count.
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
    ck_eq(st->skipped, 0, "and it is NOT counted as a skip");
    ck_eq(st->skip_window, 0, "so the 3-in-1s window did not move");
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

    if (g_fail == 0)
    {
        printf("all cases passed\n");
        return 0;
    }
    printf("%d assertion(s) FAILED\n", g_fail);
    return 1;
}
