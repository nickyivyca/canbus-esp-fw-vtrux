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
#include <stdlib.h>
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
static void run(gs_t *s, int64_t us, int64_t step)
{
    for (int64_t t = 0; t < us; t += step)
    {
        gs_tick(s, fb_now());
        fb_advance(step);
    }
    gs_tick(s, fb_now());
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
    run(&s, 50, 10);
    ck_eq(fb_submit_count(), 1, "the diag was handed over");
    ck(fb_state_now() == GS_BUF_AWAITING, "and is awaiting arbitration");

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");

    /* The abort must happen and the inhibit must go out. On an idle-enough bus
     * afterwards the inhibit reaches the wire; the diag comes back later. */
    fb_set_contended(false);
    run(&s, 2000, 10);

    ck(fb_wire_count() >= 1, "something reached the wire");
    ck_eq(fb_wire_id(0), ID_INHIBIT, "THE INHIBIT WENT FIRST");
    const gs_stats_t *st = gs_stats(&s);
    ck(st->aborts >= 1, "an abort was issued");
    ck_eq(st->cls[GS_CLASS_TELEMETRY].requeued, 1, "the diag was requeued");
    ck_eq(st->skipped, 0, "no skip");

    /* and the telemetry is sent eventually, never silently lost */
    run(&s, 3000, 10);
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
    run(&s, 20, 5);          /* idle bus: it starts transmitting at once */
    ck(fb_state_now() == GS_BUF_TRANSMITTING, "the diag is transmitting");

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 1500, 5);

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
    run(&s, 500, 10);
    ck_eq(fb_submit_count(), 0, "nothing was accepted");
    ck(gs_stats(&s)->tx_refused > 0, "the refusal was counted");

    /* The VCM's next command arrives. The stale inhibit is now too late. */
    const gs_deadline_t v = gs_command_received(&s, fb_now());
    ck(v == GS_DEADLINE_SKIPPED, "the deadline reports a SKIP");
    ck_eq(gs_stats(&s)->skipped, 1, "one skip counted");

    /* Now let the driver work again. The purged frame must not appear. */
    fb_reset();                 /* clears the refusal and the wire log */
    run(&s, 3000, 10);
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
        while (fb_now() < at[i]) { fb_advance(1000); gs_tick(&s, fb_now()); }
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        gs_tick(&s, fb_now());
        gs_command_received(&s, fb_now());
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
        gs_tick(&s, fb_now());
        gs_command_received(&s, fb_now());
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
    run(&s, 50, 10);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 300, 10);

    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->cls[GS_CLASS_TELEMETRY].sent, 0,
          "the aborted diag was NOT counted as sent");
    ck_eq(st->cls[GS_CLASS_TELEMETRY].aborted, 1, "it was counted as aborted");
    ck_eq(st->ontime + st->ontime_unverified, 0,
          "and no inhibit completion was credited yet");
}

static long residual(const gs_t *s, gs_class_t c);
static void ck_standdown_identity(const gs_t *s, const char *what);

/*
 * SPEC 7 TRIP 7, THE DECISION WITH FRAMES WAITING (user, 2026-11-23). The
 * inhibit is handed over and completes while the worker is away; the next
 * gs_tick() sees the completion with `waiting` frames in the receive queue.
 */
static void seen_with_waiting(gs_t *s, uint32_t waiting)
{
    fb_reset();
    gs_init(s, fb_hal());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");
    run(s, 20, 5);
    ck(fb_state_now() == GS_BUF_TRANSMITTING,
       "PRECONDITION: the inhibit is transmitting");
    fb_advance(1000);
    ck(fb_state_now() == GS_BUF_EMPTY && fb_wire_count() == 1,
       "PRECONDITION: the inhibit completed on the wire unobserved");
    fb_set_rx_waiting(waiting);
    gs_tick(s, fb_now());    /* the completion is seen here */
}

/* The worker removes one frame from the receive queue, then ticks. */
static void dequeue(gs_t *s, bool vcm)
{
    const uint32_t n = fb_rx_waiting();
    if (n > 0)
    {
        fb_set_rx_waiting(n - 1);
    }
    gs_rx_dequeued(s, vcm, 0);
    gs_tick(s, fb_now());
}

/*
 * Frames waiting at the completion, none of them the VCM's next 0x051: on
 * time, with frames waiting (`ontime_unverified`), decided only after the
 * worker has removed all of them. Counted separately and never summed in.
 */
static void case_ontime_unverified(void)
{
    gs_t s;
    g_case = "trip 7: frames waiting, no command among them: on time, waiting";
    seen_with_waiting(&s, 3);
    ck_eq(fb_rx_reads(), 1, "the scheduler counted the queue at the completion");
    ck(gs_verdict_pending(&s), "the verdict waits for the counted frames");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 0, "not yet credited as sent");
    ck(gs_inhibit_outstanding(&s), "and still outstanding");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the identity closes meanwhile");

    fb_advance(2000);   /* the dequeues take time; the hold must not grow */
    dequeue(&s, false);
    dequeue(&s, false);
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 0, "two of three removed: still waits");
    dequeue(&s, false);

    const gs_stats_t *st = gs_stats(&s);
    ck(!gs_verdict_pending(&s), "decided after the third");
    ck_eq(st->cls[GS_CLASS_INHIBIT].sent, 1, "credited as sent");
    ck_eq(st->ontime, 0, "NOT counted in `ontime` (the queue was not empty)");
    ck_eq(st->ontime_unverified, 1, "counted on time, with frames waiting");
    ck_eq(st->unverified_max_backlog, 3, "with the count recorded");
    ck(st->cls[GS_CLASS_INHIBIT].max_hold_us < 2000,
       "the hold ends when the completion was seen, not after the dequeues");
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_OK,
       "the deadline finds nothing outstanding");
    ck_eq(st->skipped, 0, "no skip");
}

/* The VCM's next 0x051 is one of the counted frames: a late skip. */
static void case_counted_command_is_late(void)
{
    gs_t s;
    g_case = "trip 7: the command is among the counted frames: late skip";
    seen_with_waiting(&s, 2);
    dequeue(&s, false);
    ck(gs_verdict_pending(&s), "one counted frame left, still waits");
    gs_rx_dequeued(&s, true, 0);    /* the VCM's next 0x051, the second one */
    ck(s.credit_hold, "the deadline is told to book it late");
    gs_tick(&s, fb_now());
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 0, "the tick did not credit it");
    const gs_deadline_t v = gs_command_received(&s, fb_now());

    ck(v == GS_DEADLINE_MAYBE_LATE, "the deadline books it as went-out-late");
    ck_eq(s.st.late_on_wire, 1, "late_on_wire counted");
    ck_eq(s.st.skipped_late, 1, "a LATE skip");
    ck_eq(s.st.skipped_withdrawn, 0, "not a withdrawn one");
    ck_eq(s.st.ontime + s.st.ontime_unverified, 0, "NOT credited on time");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 0, "and not credited as sent");
    ck(!s.credit_hold && !gs_verdict_pending(&s), "the deadline ended the wait");

    run(&s, 200, 10);
    ck(!s.held && !s.inhibit_outstanding, "the frame left the scheduler");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the identity closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
    ck_eq(fb_wire_count(), 1, "nothing else went out");
}

/* An empty queue at the completion: on time at once, no wait. */
static void case_empty_queue_is_on_time(void)
{
    gs_t s;
    g_case = "trip 7: an empty queue at the completion: on time";
    seen_with_waiting(&s, 0);
    ck(!gs_verdict_pending(&s), "nothing to wait for");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 1, "credited as sent at once");
    ck_eq(s.st.ontime, 1, "counted in `ontime`");
    ck_eq(s.st.ontime_unverified, 0, "not with frames waiting");
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_OK,
       "the deadline finds nothing outstanding");
}

/*
 * A frame that arrives between the completion read and the count is counted
 * (spec 7 trip 7: "it counts them after it sees the completion, not before").
 * Here it is the VCM's next 0x051, so the frame is a late skip. A count taken
 * before the completion read, as the caller's `rx_backlog` is, reads 0 and
 * books it on time.
 */
static void case_arrival_between_reads_is_counted(void)
{
    gs_t s;
    g_case = "trip 7: a frame arriving between the completion read and the count";
    fb_reset();
    gs_init(&s, fb_hal());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");
    run(&s, 20, 5);
    fb_advance(1000);
    ck(fb_state_now() == GS_BUF_EMPTY && fb_wire_count() == 1,
       "PRECONDITION: the inhibit completed on the wire unobserved");
    fb_arrive_on_free(1);
    gs_tick(&s, fb_now());   /* the caller's count read 0 */
    ck_eq(fb_rx_waiting(), 1, "PRECONDITION: the frame arrived");
    ck(gs_verdict_pending(&s), "the arrival was counted: the verdict waits");
    ck_eq(s.st.ontime, 0, "not credited on time");

    dequeue(&s, true);
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_MAYBE_LATE,
       "the counted frame was the command: a late skip");
    ck_eq(s.st.skipped_late, 1, "booked late");
    ck_eq(s.st.ontime + s.st.ontime_unverified, 0, "and never on time");
}

/*
 * THE LIMIT: the verdict waits for the frames counted, and no more. A VCM
 * 0x051 that arrives after the count, behind them, is not one of them, so the
 * frame is on time, with frames waiting.
 */
static void case_command_after_the_count_is_on_time(void)
{
    gs_t s;
    g_case = "trip 7: a command behind the counted frames does not make it late";
    seen_with_waiting(&s, 1);
    fb_set_rx_waiting(2);           /* the command arrives behind the counted one */
    dequeue(&s, false);
    ck(!gs_verdict_pending(&s), "decided after the one counted frame");
    ck_eq(s.st.ontime_unverified, 1, "on time, with frames waiting");
    dequeue(&s, true);
    ck(!s.credit_hold, "no hold for a command after the count");
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_OK,
       "the deadline finds it on time");
    ck_eq(s.st.skipped, 0, "no skip");
    ck_eq(s.st.unverified_max_backlog, 1, "the count was 1");
}

/*
 * The completion seen after the worker removed this command, with a blocked
 * receive (no hold) and frames waiting behind it: they arrived after the
 * command, so none of them is it. The deadline can not wait for them: on time,
 * with frames waiting, at once.
 */
static void case_deadline_ends_the_wait_on_time(void)
{
    gs_t s;
    g_case = "trip 7: frames behind the dequeued command: on time, waiting";
    fb_reset();
    gs_init(&s, fb_hal());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");
    run(&s, 20, 5);
    fb_advance(1000);
    gs_rx_dequeued(&s, true, 0);    /* the receive blocked: no hold */
    fb_set_rx_waiting(2);
    gs_tick(&s, fb_now());       /* the completion seen after the dequeue */
    ck(gs_verdict_pending(&s), "PRECONDITION: the verdict waits");
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_OK,
       "the deadline credits it");
    ck_eq(s.st.ontime_unverified, 1, "on time, with frames waiting");
    ck_eq(s.st.unverified_max_backlog, 2, "with the count");
    ck_eq(s.st.skipped, 0, "no skip");
    ck(!gs_verdict_pending(&s), "the wait ended");

    /* A departure first seen at the deadline is counted the same way. */
    g_case = "trip 7: a departure first seen at the deadline, frames behind it";
    fb_reset();
    gs_init(&s, fb_hal());
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");
    run(&s, 20, 5);
    fb_advance(1000);
    fb_set_rx_waiting(2);
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_OK,
       "the deadline credits it");
    ck_eq(s.st.ontime_unverified, 1, "on time, with frames waiting");
    ck_eq(s.st.ontime, 0, "not in `ontime`");
}

/*
 * RESPOND'S PROBE TAKES THE INHIBIT'S PATH (spec 11, user 2026-11-23): the
 * same transmit path, deadline and decision with frames waiting. At the
 * deadline a probe is never withdrawn and is never a skip, and a probe found
 * late is in neither on-time count.
 */
static void probe_seen_with_waiting(gs_t *s, uint32_t waiting)
{
    fb_reset();
    gs_init(s, fb_hal());
    gs_rearm(s, fb_hal(), GS_CLASS_PROBE);
    gi_frame_t p = mk(ID_PROBE, GI_TX_PROBE);
    ck(gs_queue_frame(s, GS_CLASS_PROBE, &p, fb_now()), "probe queued");
    run(s, 20, 5);
    ck(fb_state_now() == GS_BUF_TRANSMITTING,
       "PRECONDITION: the probe is transmitting");
    fb_advance(1000);
    ck(fb_state_now() == GS_BUF_EMPTY && fb_wire_count() == 1,
       "PRECONDITION: the probe completed on the wire unobserved");
    fb_set_rx_waiting(waiting);
    gs_tick(s, fb_now());       /* the completion is seen here */
}

static void ck_probe_late(gs_t *s, const char *how)
{
    g_case = how;
    ck(gs_command_received(s, fb_now()) == GS_DEADLINE_OK,
       "a probe is never a skip at the deadline");
    ck(s->held, "the deadline did not credit it");
    gs_tick(s, fb_now());
    const gs_stats_t *st = gs_stats(s);
    ck_eq(st->cls[GS_CLASS_PROBE].sent, 1, "it is still credited as sent");
    ck_eq(st->ontime + st->ontime_unverified, 0, "in neither on-time count");
    ck_eq(st->skipped, 0, "never a skip");
    ck_eq(st->late_on_wire, 0, "not booked late_on_wire");
    ck_eq(st->cls[GS_CLASS_PROBE].withdrawn, 0, "never withdrawn");
    ck_eq(fb_abort_cmds(), 0, "no abort issued");
    ck_eq(residual(s, GS_CLASS_PROBE), 0, "the identity closes");
    ck_eq(fb_wire_count(), 1, "nothing else went out");
}

static void case_probe_counted_command_is_late(void)
{
    gs_t s;
    g_case = "spec 11: probe, the command among the counted frames";
    probe_seen_with_waiting(&s, 2);
    ck(gs_verdict_pending(&s), "the probe's verdict waits");
    ck_eq(s.st.cls[GS_CLASS_PROBE].sent, 0, "not yet credited");
    dequeue(&s, false);
    gs_rx_dequeued(&s, true, 0);    /* the VCM's next 0x051 */
    ck(s.credit_hold, "the deadline is told it was late");
    gs_tick(&s, fb_now());
    ck_probe_late(&s, "spec 11: probe, the command among the counted frames");

    /* Path (ii) for the probe: the command was waiting at the receive. */
    fb_reset();
    gs_init(&s, fb_hal());
    gs_rearm(&s, fb_hal(), GS_CLASS_PROBE);
    gi_frame_t p = mk(ID_PROBE, GI_TX_PROBE);
    ck(gs_queue_frame(&s, GS_CLASS_PROBE, &p, fb_now()), "probe queued");
    run(&s, 20, 5);
    fb_advance(1000);
    ck(gs_timed_in_flight(&s), "the probe is in flight, completion not seen");
    gs_rx_dequeued(&s, true, 1);
    ck(s.credit_hold, "path (ii): held");
    gs_tick(&s, fb_now());
    ck_probe_late(&s, "spec 11: probe, the command waiting at the receive");
}

static void case_probe_not_among_them_is_on_time(void)
{
    gs_t s;
    g_case = "spec 11: probe, frames waiting, no command among them";
    probe_seen_with_waiting(&s, 1);
    ck(gs_verdict_pending(&s), "the probe's verdict waits");
    dequeue(&s, false);
    ck_eq(s.st.cls[GS_CLASS_PROBE].sent, 1, "credited as sent");
    ck_eq(s.st.ontime_unverified, 1, "on time, with frames waiting");
    ck_eq(s.st.ontime, 0, "not in `ontime`");
    ck_eq(s.st.unverified_max_backlog, 1, "with the count");
    ck(gs_command_received(&s, fb_now()) == GS_DEADLINE_OK, "no skip");
    ck_eq(s.st.skipped, 0, "none");

    g_case = "spec 11: probe, an empty queue at the completion";
    probe_seen_with_waiting(&s, 0);
    ck_eq(s.st.cls[GS_CLASS_PROBE].sent, 1, "credited as sent at once");
    ck_eq(s.st.ontime, 1, "on time");
}

/*
 * A stand-down while the verdict waits: counted as a stand-down "whether or
 * not it reached the wire" (spec 5.2 item 5), never as a skip or on time. The
 * wait ends, and a later dequeue changes nothing.
 */
static void case_standdown_ends_the_wait(void)
{
    gs_t s;
    g_case = "trip 7: a stand-down while the verdict waits";
    seen_with_waiting(&s, 2);
    ck(gs_verdict_pending(&s), "PRECONDITION: the verdict waits");
    gs_withdraw_inhibits(&s, fb_now());
    ck(!gs_verdict_pending(&s) && !s.credit_hold, "the wait ended");
    ck_eq(s.st.standdown, 1, "a stand-down");
    dequeue(&s, true);
    run(&s, 200, 10);
    ck(!s.held, "the frame left the scheduler");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].withdrawn, 1, "booked withdrawn");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 0, "not sent");
    ck_eq(s.st.skipped, 0, "not a skip");
    ck_eq(s.st.ontime + s.st.ontime_unverified, 0, "not on time");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the identity closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
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
    run(&s, 50, 10);
    ck(gs_inhibit_outstanding(&s), "the inhibit is outstanding");

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 200, 10);
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
    run(&s, 50, 10);
    ck_eq(fb_submit_count(), 1, "the inhibit was handed over");
    ck(fb_state_now() == GS_BUF_AWAITING, "and is awaiting arbitration");

    const gs_deadline_t v = gs_command_received(&s, fb_now());
    ck(v == GS_DEADLINE_SKIPPED, "the deadline skips it");
    ck_eq(gs_stats(&s)->skipped, 1, "one skip");

    /* Let the bus go quiet and run a long time. It must not reappear. */
    fb_set_contended(false);
    run(&s, 5000, 10);
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
    run(&s, 100, 10);        /* idle bus: transmitting, ~249 us to go */
    ck(fb_state_now() == GS_BUF_TRANSMITTING, "the inhibit is transmitting");

    const gs_deadline_t v = gs_command_received(&s, fb_now());
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
    run(&s, 50, 10);
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
    run(&s, 4000, 10);

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
    run(&s, 50, 10);
    ck(fb_state_now() == GS_BUF_AWAITING, "the diag is awaiting");

    /* The abort will be authorised at AWAITING and land at TS = 1 anyway, and
     * the frame then transmits for 4 ms -- far past the bound. */
    fb_stick_transmitting(4000);
    fb_race_next(3500);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());

    run(&s, 2000, 10);
    const gs_stats_t *st = gs_stats(&s);
    ck(fb_aborts_with_no_effect() >= 1, "the race produced a no-effect abort");
    ck(st->abort_bound_hit >= 1, "THE 1 MS BOUND WAS REACHED AND COUNTED");
    ck_eq(fb_wire_count(), 0, "nothing has reached the wire yet");
    ck(gs_inhibit_outstanding(&s) || s.q[GS_CLASS_INHIBIT].n > 0,
       "and the inhibit is still pending, not dropped");
    /* Spec 5.2 item 5's re-read is for a withdrawn inhibit only. */
    ck_eq(fb_spins(), 0, "A PREEMPTED PAGE IS NEVER RE-READ: one read a pass");
    ck_eq(fb_clock_reads(), 0, "and the HAL's clock is not read");
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
    run(&s, 50, 10);
    ck_eq(fb_submit_count(), 1, "the probe was handed over");

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    fb_set_contended(false);
    run(&s, 3000, 10);

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
    run(&s2, 2000, 10);
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
        while (fb_now() < at[i]) { fb_advance(1000); gs_tick(&s, fb_now()); }
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        gs_tick(&s, fb_now());
        gs_command_received(&s, fb_now());
    }
    ck_eq(gs_stats(&s)->skipped, 3, "three skips");
    ck(!gs_skip_trip(&s),
       "and NO trip: the outer two are exactly 1.000 s apart");

    /* One microsecond closer and it trips, which is what makes the boundary a
     * boundary rather than an accident. */
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    gs_tick(&s, fb_now());
    gs_command_received(&s, fb_now());
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
    run(&s, 50, 10);
    ck(fb_state_now() == GS_BUF_AWAITING, "the diag is awaiting");

    /* The next abort is authorised at AWAITING and lands at TS = 1 anyway. The
     * frame loses arbitration 120 us later. */
    fb_race_next(120);
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    fb_set_contended(false);
    run(&s, 3000, 10);

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
    gs_command_received(&s, fb_now());
    gi_frame_t first = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &first, fb_now());

    if (prev_outstanding)
    {
        /* Contended: it is handed over and sits AWAITING, never completing. */
        fb_set_contended(true);
        run(&s, 50, 10);
    }
    else
    {
        /* Idle: it completes long before the next command. */
        run(&s, 600, 10);
    }

    /* The next command arrives, and its answer is queued. */
    gi_frame_t second = mk(ID_INHIBIT, GI_TX_INHIBIT);
    if (correct_order)
    {
        gs_command_received(&s, fb_now());
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &second, fb_now());
    }
    else
    {
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &second, fb_now());
        gs_command_received(&s, fb_now());
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
    run(&s, 50, 10);
    ck_eq(fb_submit_count(), 1, "the first page was handed over");
    ck(fb_outstanding_now(), "and the controller still holds it");

    /* The mode change. This is the call under test. */
    gs_rearm(&s, fb_hal(), GS_CLASS_INHIBIT);

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
    run(&s, 100, 10);

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
    run(&s, 3000, 10);

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
    run(&s, 50, 10);

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
    run(&s, 3000, 10);
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
    run(&s, 50, 10);
    ck(fb_outstanding_now(), "the controller holds the first page");

    /* Let real time pass before the re-arm, so a hold booked into the new arm
     * would be obvious rather than a rounding artefact. */
    fb_advance(200000);         /* 200 ms */
    gs_rearm(&s, fb_hal(), GS_CLASS_INHIBIT);

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_TELEMETRY];
    ck_eq(c->queued, 0, "the new arm has queued nothing");
    ck_eq(c->carried_in, 1, "and declares the frame it inherited");
    ck_eq(held_in(&s, GS_CLASS_TELEMETRY), 1, "which is still held");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0,
          "so a reading taken right after the re-arm balances");

    /* Now let it complete. */
    fb_set_contended(false);
    run(&s, 3000, 10);

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
    run(&s, 500, 10);
    fb_set_contended(false);
    run(&s, 3000, 10);
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
    (void)gs_command_received(&s, fb_now());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());

    /* The VCM's NEXT command arrives before it went: the deadline withdraws it. */
    const gs_deadline_t v = gs_command_received(&s, fb_now());
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

    (void)gs_command_received(&s, fb_now());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 50, 10);

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(c->handed, 1, "the inhibit was handed to the controller");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "and is held");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes while held");

    /* The VCM's next command: the deadline gives the frame up. */
    const gs_deadline_t v = gs_command_received(&s, fb_now());
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
    run(&s, 3000, 10);

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

/* The stand-down helpers, defined with the stand-down cases below. */
static long json_standdown(const gs_t *s);
static void ck_standdown_identity(const gs_t *s, const char *what);

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
 *
 * A STAND-DOWN SINCE 2026-10-16, NOT A SKIP (spec 5.2 item 5, user). Until then
 * this case asserted one skip here; the frame was given up because the inhibit
 * stopped being live, which is not a cycle a live inhibit missed.
 */
static void case_identity_withdraw_api_balances(void)
{
    g_case = "item 6: gs_withdraw_inhibits' purge books `withdrawn`";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now());
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
    ck_eq(s.st.skipped, 0, "it is NOT a skip");
    ck_eq(json_standdown(&s), 1, "it is a stand-down");
    ck_eq(c->withdrawn, 1, "and the class books the identity's term");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "so the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");

    /* Nothing may come back later: item 5 forbids sending a skipped frame. */
    run(&s, 3000, 10);
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
 * A DEPARTURE OBSERVED BY gs_command_received WHILE AN ABORT IS IN PROGRESS.
 *
 * Found 2026-10-03 by the reviewing session while tracing why mutation U1 survived.
 * gs_tick() guards its completion check with `!s->aborting` (gi_sched.c:435) and its
 * comment calls the guard load-bearing: the driver reports an aborted frame exactly as
 * it reports a real transmission, so "it left the controller" alone credits an ABORTED
 * frame as SENT. gs_command_received() did not have that guard.
 *
 * The consequence is a silently lost telemetry page: booked sent, never counted as
 * aborted, never requeued, never on the wire. Spec 5.2 item 6 requires aborted and
 * requeued to both be counted.
 *
 * NOTHING IN THE SUITE CAUGHT IT because the identity still closes -- queued 1 = sent 1
 * -- with `aborted` and `requeued` simply never incremented. The balance checks are
 * satisfied by a page that has vanished. What shows it is the wire and the per-class
 * terms, which is what this case asserts.
 *
 * ON THE DEVICE the window is real but narrow: gen_inhibit.c runs sched_advance() at
 * :1234 and gs_command_received() at :1257, so an abort command issued in that tick
 * taking effect between the two status reads -- 6-21 us, measured in item 9's table --
 * lands here. It needs a contended bus, which is the condition the scheduler exists for.
 */
static void case_departure_during_abort_is_not_sent(void)
{
    g_case = "abort: a departure seen by gs_command_received is not credited as sent";
    fb_reset();
    fb_set_contended(true);         /* STRESS: saturated bus, spec 12.2 */
    gs_t s;
    gs_init(&s, fb_hal());

    const gs_class_stats_t *telem = &s.st.cls[GS_CLASS_TELEMETRY];

    /* A diag page in the buffer, awaiting arbitration. */
    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    ck(gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now()), "diag queued");
    run(&s, 50, 10);
    ck_eq(fb_submit_count(), 1, "the diag was handed over");
    ck(fb_state_now() == GS_BUF_AWAITING, "and is awaiting arbitration");

    /* An inhibit arrives, so the next tick must preempt: abort the page. */
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");

    /*
     * ONE tick: it begins the abort and issues the command. fake_buf then frees the
     * buffer a few microseconds later, modelling the measured 6-21 us.
     */
    gs_tick(&s, fb_now());
    ck(s.st.aborts >= 1, "the abort was issued");
    fb_advance(10);

    /*
     * AND NOW THE VCM'S NEXT COMMAND, which is the path under test. The buffer is free
     * because the abort took effect, and the page is still held mid-abort.
     */
    (void)gs_command_received(&s, fb_now());

    ck_eq(telem->sent, 0,
          "the aborted diag page was credited as SENT by gs_command_received -- the "
          "driver reports an abort exactly like a transmission, which is what gs_tick's "
          "`!s->aborting` guard exists to prevent");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "the identity still closes");

    /*
     * THE BOOKING BELONGS TO THE NEXT TICK, not to the command path, and that is the
     * point of the guard rather than a gap in it: gs_command_received declines a
     * departure it cannot classify, and gs_tick's step 3 counts it as aborted and puts
     * it back. So these are asserted after a tick has run, not before.
     */
    gs_tick(&s, fb_now());
    ck_eq(telem->aborted, 1, "the abort is counted (spec 5.2 item 6)");
    ck_eq(telem->requeued, 1, "and the page is put back, not dropped");
    ck_eq(telem->sent, 0, "and still not credited as sent");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "the identity closes after the booking");

    /*
     * THE PAGE MUST REACH THE WIRE. This is the assertion the balance checks cannot
     * make: a page booked sent but never transmitted satisfies the identity and is
     * gone.
     */
    fb_set_contended(false);
    run(&s, 5000, 10);
    ck(wire_count_id(ID_DIAG) >= 1,
       "the requeued diag page never reached the wire: it was booked as sent and lost");
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
        (void)gs_command_received(&s, fb_now());
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        if (i % TELEM_EVERY == 0)
        {
            gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
            (void)gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
            pages_offered++;
        }
        run(&s, VCM_PERIOD_US, 250);
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
        const gs_deadline_t v = gs_command_received(&s, fb_now());
        if (v == GS_DEADLINE_OK) { deadlines_ok++; }
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        if (i % TELEM_EVERY == 0)
        {
            gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
            (void)gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
            pages_offered++;
        }
        run(&s, VCM_PERIOD_US, 250);
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
        (void)gs_command_received(&s, fb_now());
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        run(&s, VCM_PERIOD_US, 250);
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
        (void)gs_command_received(&s, fb_now());
        gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
        gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
        run(&s, VCM_PERIOD_US, 250);
    }
    ck_eq(inhib->sent, 6, "six answers out before the interesting part");

    /*
     * Put a telemetry page in the buffer and make it un-abortable for longer than
     * the VCM's period. Item 4 says a frame already transmitting cannot be
     * aborted -- the inhibit waits for it -- so our answer cannot get out.
     */
    gi_frame_t page = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &page, fb_now());
    run(&s, 500, 250);
    fb_stick_transmitting(30000);

    (void)gs_command_received(&s, fb_now());
    gi_frame_t stuck = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &stuck, fb_now());
    run(&s, VCM_PERIOD_US, 250);
    ck_eq(inhib->sent, 6, "it did not get out: the buffer is still transmitting");

    const int wire_inh_before = wire_count_id(ID_INHIBIT);
    const uint32_t skipped_before = s.st.skipped;

    /* The VCM's next command arrives with our answer still queued. */
    const gs_deadline_t v = gs_command_received(&s, fb_now());
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
    run(&s, 80000, 250);
    wire_log_sane();
    ck_eq(wire_count_id(ID_INHIBIT), wire_inh_before,
          "the skipped inhibit never reaches the wire, then or afterwards");

    /* AND THE NEXT COMMAND IS ANSWERED NORMALLY (item 10's skip bullet). A skip
     * that poisoned the following cycle would be far worse than the skip. */
    const uint32_t sent_before_next = inhib->sent;
    (void)gs_command_received(&s, fb_now());
    gi_frame_t next = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &next, fb_now());
    run(&s, VCM_PERIOD_US, 250);
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
    run(&s, 50, 10);
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
    gs_rearm(&s, fb_hal(), GS_CLASS_INHIBIT);

    const gs_class_stats_t *telem = &s.st.cls[GS_CLASS_TELEMETRY];
    ck_eq(telem->carried_in, 1, "the new arm inherited it");
    ck_eq(telem->max_queue_us, 0, "and starts with no wait booked");

    /*
     * An inhibit in the new arm preempts it. The buffer is AWAITING, so item 6's
     * preemption aborts the page and requeues it at the head of its class.
     */
    (void)gs_command_received(&s, fb_now());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 5000, 10);

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
    run(&s, 5000, 10);
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
    run(&s, 20000, 10);
    ck(telem->max_queue_us > 0,
       "a fresh frame's genuine wait IS booked, so the exclusion did not just "
       "turn the counter off");
}

/*
 * A WITHDRAWAL MUST CLEAR `inhibit_outstanding`, and nothing caught it when it did
 * not (reviewing session, 2026-10-02).
 *
 * THE FAILURE IT PINS. After a held inhibit is given up at its deadline the flag has
 * to be cleared, because the frame is gone. Left set, it survives until some later
 * completion happens to clear it -- and gs_command_received() consults it before
 * looking at the buffer. A later deadline then finds the flag true with an EMPTY
 * buffer, which that function reads as "the frame left the buffer and we had not
 * issued an abort, so it went out late": a second, FALSE skip, with late_on_wire++.
 * Three of those are trip 7, so a stale flag can abort a live inhibit over frames
 * that never existed.
 *
 * WHY EVERY OTHER SKIP CASE MISSES IT. In all of them the next command's answer is
 * handed over and completes, which clears the flag before any deadline can misread
 * it. The ingredient that exposes it is A COMMAND THAT PRODUCES NO ANSWER -- which
 * is a real state: the core stops queueing inhibits the moment it stops being live,
 * on any trip, any disable, a stale key.
 */
static void case_withdrawal_clears_inhibit_outstanding(void)
{
    g_case = "a withdrawal clears inhibit_outstanding (no phantom late skip)";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the frame sits AWAITING, so it is held */
    gs_t s;
    gs_init(&s, fb_hal());

    /* One command, one answer, handed to the controller. */
    (void)gs_command_received(&s, fb_now());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());
    run(&s, 50, 10);
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].handed, 1, "the inhibit was handed over");
    ck(gs_inhibit_outstanding(&s), "and is outstanding");

    /* Its deadline: awaiting arbitration, so it is withdrawn and never reaches the
     * wire. */
    const gs_deadline_t v1 = gs_command_received(&s, fb_now());
    ck_eq((long)v1, (long)GS_DEADLINE_SKIPPED, "withdrawn at the deadline");

    /* Let the abort loop finish, so the frame is really gone. */
    run(&s, 3000, 10);
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].withdrawn, 1, "and booked as withdrawn");
    ck_eq(fb_wire_count(), 0, "nothing reached the wire");

    /*
     * THE ASSERTION THE MUTANT BREAKS. The frame is gone, so nothing of ours is
     * outstanding. Checked directly as well as through its consequence below,
     * because the consequence needs two more commands to appear and a direct
     * reading says which of the two is wrong when it does.
     */
    ck(!gs_inhibit_outstanding(&s),
       "a withdrawn inhibit must not leave `inhibit_outstanding` set: the frame is "
       "gone, and the next deadline would read the flag against an empty buffer");

    /*
     * A COMMAND WITH NO ANSWER. The core does this whenever it has stopped being
     * live, which is exactly when a stale flag is most likely to be read.
     */
    const gs_deadline_t v2 = gs_command_received(&s, fb_now());
    ck_eq((long)v2, (long)GS_DEADLINE_OK,
          "a deadline with nothing outstanding and nothing queued is OK");
    run(&s, 1000, 10);

    /* And the command after that -- the one that reads the flag. */
    const gs_deadline_t v3 = gs_command_received(&s, fb_now());
    ck_eq((long)v3, (long)GS_DEADLINE_OK, "and so is the next");

    ck_eq(s.st.skipped, 1,
          "exactly ONE skip happened; a second is a phantom, booked because the "
          "deadline read a stale `inhibit_outstanding` against an empty buffer");
    ck_eq(s.st.skipped_late, 0, "and none of it was a late skip");
    ck_eq(s.st.late_on_wire, 0,
          "late_on_wire must stay 0: no frame of ours went out late, and three "
          "phantom skips in a second would be trip 7 on frames that never existed");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].withdrawn, 1, "still one withdrawal");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "and the balance still closes");
}

/* ------------------------------------------------------------ stand-downs --- */

/*
 * STAND-DOWNS (spec 5.2 items 5 and 6; spec 12.4 row "a stand-down with an answer
 * pending"; user 2026-10-16). An answer still queued, or still in the controller,
 * when the inhibit stops being live is given up on purpose. It is counted as
 * `standdown` and never as a skip, and the identity becomes
 *
 *   withdrawn + dropped == skipped + standdown   (inhibit class)
 *
 * once the item 4 loop has finished.
 *
 * EACH CASE IS PHASE-LOCKED ON A COMMAND: its deadline runs, its answer is queued,
 * and the inhibit stops being live before the answer can leave. That is the timing
 * of bench B20 `liveflag-mmode`, an M-mode release 260 us after a 0x051, which on
 * c65c45e left the diag pages silent until the re-arm.
 *
 * `standdown` IS READ FROM THE STATUS PAGE (gs_json), not from the struct, so this
 * file still builds against c65c45e, where the field does not exist, and fails
 * there on the assertions rather than at compile time. That is also the reading
 * spec 11 puts on the page.
 */
static long json_standdown(const gs_t *s)
{
    static char buf[4096];
    (void)gs_json(s, buf, (int)sizeof(buf));
    const char *p = strstr(buf, "\"standdown\":");
    if (p == NULL)
    {
        return -1;      /* absent: no stand-down count on the page */
    }
    return strtol(p + strlen("\"standdown\":"), NULL, 10);
}

/* One VCM command, phase-locked: its deadline first, then its answer queued. */
static void command_and_answer(gs_t *s)
{
    (void)gs_command_received(s, fb_now());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(s, GS_CLASS_INHIBIT, &inh, fb_now());
}

/*
 * Telemetry after a stand-down: one page every `period_us`. Returns how many of
 * them reached the wire. A page left waiting on a frame nobody will ever report
 * is what the core did on c65c45e; here the scheduler's side of the same promise
 * is checked, that nothing it holds keeps telemetry back.
 */
static int pages_after(gs_t *s, int pages, int64_t period_us)
{
    const int before = wire_count_id(ID_DIAG);
    for (int i = 0; i < pages; i++)
    {
        gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
        gs_queue_frame(s, GS_CLASS_TELEMETRY, &d, fb_now());
        run(s, period_us, 10);
    }
    return wire_count_id(ID_DIAG) - before;
}

static void ck_standdown_identity(const gs_t *s, const char *what)
{
    const gs_class_stats_t *c = &s->st.cls[GS_CLASS_INHIBIT];
    ck_eq((long)(c->withdrawn + c->dropped),
          (long)s->st.skipped + json_standdown(s), what);
}

static void case_standdown_queued_answer(void)
{
    g_case = "stand-down: an answer still QUEUED is a stand-down, not a skip";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    /*
     * A telemetry page already ON THE WIRE, and staying there. Item 4 never aborts
     * a transmitting frame, so the answer has to wait in its class queue -- which
     * is the state this case needs.
     */
    fb_stick_transmitting(5000);
    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 50, 10);
    ck(fb_state_now() == GS_BUF_TRANSMITTING, "the page is transmitting");

    command_and_answer(&s);
    run(&s, 20, 10);
    ck_eq(s.q[GS_CLASS_INHIBIT].n, 1,
          "PRECONDITION: the answer is waiting in its queue");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "and is not in the controller");

    /* The inhibit stops being live. */
    gs_withdraw_inhibits(&s, fb_now());

    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(json_standdown(&s), 1, "one stand-down, on the status page");
    ck_eq(s.st.skipped, 0, "and NO skip");
    ck_eq(s.st.skipped_withdrawn, 0, "of either kind");
    ck_eq(s.st.skip_window, 0, "so nothing in trip 7's window");
    ck_eq(c->withdrawn, 1, "the class books `withdrawn`");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");

    /* The page finishes; telemetry keeps its cadence; the answer never goes. */
    fb_stick_transmitting(0);
    run(&s, 1000, 10);
    ck_eq(wire_count_id(ID_DIAG), 1, "the page that was on the wire finished");
    ck_eq(pages_after(&s, 5, 20000), 5,
          "every page queued after the stand-down reached the wire");
    ck_eq(wire_count_id(ID_INHIBIT), 0,
          "the withdrawn answer never reached the wire");

    /* A later command with no answer, as the core sends none while not live. */
    (void)gs_command_received(&s, fb_now());
    run(&s, 1000, 10);
    ck_eq(s.st.skipped, 0, "a later deadline does not book it as a skip");
    ck_eq(json_standdown(&s), 1, "nor as a second stand-down");
    ck_standdown_identity(&s, "and the identity still holds");
    wire_log_sane();
}

static void case_standdown_held_answer(void)
{
    g_case = "stand-down: an answer IN THE CONTROLLER is a stand-down, not a skip";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the answer sits AWAITING, so it is held */
    gs_t s;
    gs_init(&s, fb_hal());

    command_and_answer(&s);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");
    ck(fb_state_now() == GS_BUF_AWAITING, "awaiting arbitration");

    /* The inhibit stops being live. */
    gs_withdraw_inhibits(&s, fb_now());
    ck_eq(json_standdown(&s), 1, "booked as a stand-down when it is given up");
    ck_eq(s.st.skipped, 0, "not as a skip");

    /*
     * THE VCM'S NEXT COMMAND ARRIVES BEFORE THE LOOP HAS RUN. On c65c45e the frame
     * was still `inhibit_outstanding` here, so this deadline booked it as a skip
     * (the buffer reads AWAITING mid-abort) -- a frame withdrawn on purpose, fed
     * to trip 7.
     */
    const gs_deadline_t v = gs_command_received(&s, fb_now());
    ck_eq((long)v, (long)GS_DEADLINE_OK,
          "the next deadline finds no answer of ours outstanding");
    ck_eq(s.st.skipped, 0, "so it books no skip");
    ck_eq(s.st.late_on_wire, 0, "and no late frame");

    run(&s, 2000, 10);       /* the loop removes it */
    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "the controller has let it go");
    ck_eq(c->withdrawn, 1, "booked `withdrawn` when the loop removed it");
    ck_eq(c->sent, 0, "and never as sent");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");

    fb_set_contended(false);
    ck_eq(pages_after(&s, 5, 20000), 5,
          "every page queued after the stand-down reached the wire");
    ck_eq(wire_count_id(ID_INHIBIT), 0,
          "the withdrawn answer never reached the wire");
    ck_eq(json_standdown(&s), 1, "still one stand-down");
    ck_eq(s.st.skipped, 0, "still no skip");
    wire_log_sane();
}

/*
 * ONE FRAME, ONE BOOKING. A stand-down landing while the deadline's own abort is
 * still running finds a frame that is already a skip. Booking it again as a
 * stand-down would count one frame in both totals and break the identity the
 * other way.
 */
static void case_standdown_after_deadline_books_once(void)
{
    g_case = "stand-down during a deadline's abort: still one skip, no stand-down";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the answer sits AWAITING, so it is held */
    gs_t s;
    gs_init(&s, fb_hal());

    command_and_answer(&s);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");

    const gs_deadline_t v = gs_command_received(&s, fb_now());
    ck_eq((long)v, (long)GS_DEADLINE_SKIPPED, "its deadline skips it");
    ck(s.aborting, "PRECONDITION: the deadline's abort is still running");

    gs_withdraw_inhibits(&s, fb_now());     /* and now the inhibit stops being live */
    ck_eq(s.st.skipped, 1, "one skip");
    ck_eq(json_standdown(&s), 0, "and no stand-down for the same frame");

    run(&s, 2000, 10);
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].withdrawn, 1, "one withdrawal");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
}

/*
 * A RE-ARM WHILE A WITHDRAWAL IS STILL RUNNING (spec 5.2 item 6, user
 * 2026-10-16). The old arm gave an inhibit up and booked it -- as a skip at its
 * deadline, or as a stand-down -- and the item 4 loop is still removing it when
 * gs_rearm() runs. The loop then books `withdrawn` in the NEW arm, so the new
 * arm counts the frame as a stand-down at the re-arm, beside its carried_in, and
 * its own equality holds once the loop finishes. On c6fdab5 it read one over for
 * the rest of the arm.
 *
 * Reachable only by timing: a re-arm within about 0.4 ms of the withdrawal
 * starting. Here gs_rearm() is simply called before the next tick.
 */
static void rearm_mid_withdrawal(bool by_deadline)
{
    fb_reset();
    fb_set_contended(true);     /* STRESS: the answer sits AWAITING, so it is held */
    gs_t s;
    gs_init(&s, fb_hal());

    command_and_answer(&s);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");

    if (by_deadline)
    {
        const gs_deadline_t v = gs_command_received(&s, fb_now());
        ck_eq((long)v, (long)GS_DEADLINE_SKIPPED, "the old arm skips it");
    }
    else
    {
        gs_withdraw_inhibits(&s, fb_now());
        ck_eq(json_standdown(&s), 1, "the old arm stands it down");
    }
    ck(s.aborting, "PRECONDITION: the withdrawal is still running");

    gs_rearm(&s, fb_hal(), GS_CLASS_INHIBIT);
    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(c->carried_in, 1, "the new arm carries the frame in");
    ck_eq(json_standdown(&s), 1, "and counts it as a stand-down at the re-arm");
    ck_eq(s.st.skipped, 0, "not as a skip: the skip, if any, was the old arm's");

    fb_set_contended(false);
    run(&s, 2000, 10);       /* the loop removes it, in the new arm */
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "the controller has let it go");
    ck_eq(c->withdrawn, 1, "booked `withdrawn` in the new arm");
    ck_eq(c->sent, 0, "and never as sent");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the new arm's balance closes");
    ck_standdown_identity(&s,
        "the new arm's withdrawn + dropped == skipped + standdown");
    ck_eq(wire_count_id(ID_INHIBIT), 0, "the withdrawn answer never reached the wire");
}

static void case_rearm_during_deadline_withdrawal(void)
{
    g_case = "re-arm while a deadline's withdrawal runs: a stand-down in the new arm";
    rearm_mid_withdrawal(true);
}

static void case_rearm_during_standdown_withdrawal(void)
{
    g_case = "re-arm while a stand-down's withdrawal runs: a stand-down in the new arm";
    rearm_mid_withdrawal(false);
}

/*
 * AND ONLY THEN. A held inhibit nobody has given up is carried across a re-arm
 * and leaves as `sent`, balanced by carried_in; it is not a stand-down.
 */
static void case_rearm_with_live_answer_is_no_standdown(void)
{
    g_case = "re-arm with an answer still live in the controller: no stand-down";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the answer sits AWAITING, so it is held */
    gs_t s;
    gs_init(&s, fb_hal());

    command_and_answer(&s);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");
    ck(!s.aborting, "PRECONDITION: nothing is withdrawing it");

    gs_rearm(&s, fb_hal(), GS_CLASS_INHIBIT);
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].carried_in, 1, "the new arm carries it in");
    ck_eq(json_standdown(&s), 0, "and books no stand-down for it");

    fb_set_contended(false);
    run(&s, 2000, 10);
    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(c->sent, 1, "it leaves as sent");
    ck_eq(c->withdrawn, 0, "not withdrawn");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
}

/*
 * SPEC 7 TRIP 7: A COMPLETION SEEN ONLY AFTER THE VCM'S NEXT 0x051 WAS DEQUEUED
 * IS A LATE SKIP (user, 2026-11-23; B4 l3s at
 * gen-inhibit-bench_20261123T171226_4b15).
 *
 * The worker was busy: the inhibit was handed over, the next command arrived,
 * and the inhibit completed -- all before the worker came back to its receive,
 * which then returned the waiting command at once. The worker reports the
 * dequeue with the count it read before the receive (gs_rx_dequeued(), path
 * (ii)) before the dequeue's gs_tick(), so neither that tick nor
 * the deadline credits the departure: the deadline finds the inhibit
 * outstanding with the buffer EMPTY and books it late. Before the hold, the
 * tick credited it `ontime` with a backlog of 0, read after the command had
 * left the queue.
 *
 * The control half is the same sequence with the receive having blocked (no
 * hold): the departure is credited on time, as it must be, since the wake is
 * the command's arrival.
 */
static void held_credit_once(bool hold, gs_deadline_t *v, gs_t *s)
{
    fb_reset();
    gs_init(s, fb_hal());

    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(s, GS_CLASS_INHIBIT, &inh, fb_now()), "inhibit queued");
    run(s, 20, 5);
    ck(fb_state_now() == GS_BUF_TRANSMITTING,
       "PRECONDITION: the inhibit is transmitting");

    /* The worker is away: no gs_tick() while the frame finishes. */
    fb_advance(1000);
    ck(fb_state_now() == GS_BUF_EMPTY && fb_wire_count() == 1,
       "PRECONDITION: the inhibit completed on the wire unobserved");

    gs_rx_dequeued(s, true, hold ? 1u : 0u);   /* waiting at the receive, or not */
    gs_tick(s, fb_now());    /* the dequeue's sched_advance() */
    *v = gs_command_received(s, fb_now());
}

static void case_held_credit_is_late(void)
{
    gs_t s;
    gs_deadline_t v;

    g_case = "trip 7: completion seen after a waiting command is late";
    held_credit_once(true, &v, &s);
    ck(v == GS_DEADLINE_MAYBE_LATE, "the deadline books it as went-out-late");
    ck_eq(s.st.late_on_wire, 1, "late_on_wire counted");
    ck_eq(s.st.skipped_late, 1, "a LATE skip");
    ck_eq(s.st.skipped_withdrawn, 0, "not a withdrawn one");
    ck_eq(s.st.ontime + s.st.ontime_unverified, 0, "NOT credited on time");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 0, "and not credited as sent");
    ck(!s.credit_hold, "the deadline ended the hold");

    /* The item 4 loop closes on the empty buffer; the identity balances. */
    run(&s, 200, 10);
    ck(!s.held && !s.inhibit_outstanding, "the frame left the scheduler");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].withdrawn
              + s.st.cls[GS_CLASS_INHIBIT].dropped,
          s.st.skipped + s.st.standdown,
          "withdrawn + dropped == skipped + standdown");
    ck_eq(fb_wire_count(), 1, "nothing else went out");

    /* The next answer, its receive having blocked, is on time again. */
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    ck(gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now()),
       "next inhibit queued");
    run(&s, 1000, 10);
    v = gs_command_received(&s, fb_now());
    ck(v == GS_DEADLINE_OK, "the next deadline finds it on time");
    ck_eq(s.st.ontime, 1, "and credits it on time");
    ck_eq(s.st.skipped, 1, "no second skip");

    g_case = "trip 7 control: the receive blocked, the completion is on time";
    held_credit_once(false, &v, &s);
    ck(v == GS_DEADLINE_OK, "the deadline finds it already gone");
    ck_eq(s.st.ontime, 1, "credited on time");
    ck_eq(s.st.skipped, 0, "no skip");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].sent, 1, "credited as sent");
}

/*
 * The wait is the inhibit's only. A telemetry page that finished while the
 * worker was away is credited as sent at once, with frames waiting or a
 * waiting command, and a deadline with no inhibit outstanding still ends any
 * hold.
 */
static void case_held_credit_spares_telemetry(void)
{
    g_case = "trip 7: the held credit leaves telemetry alone";
    fb_reset();
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    ck(gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now()), "diag queued");
    run(&s, 20, 5);
    fb_advance(1000);
    ck(fb_state_now() == GS_BUF_EMPTY && fb_wire_count() == 1,
       "PRECONDITION: the diag completed on the wire unobserved");

    fb_set_rx_waiting(3);
    gs_rx_dequeued(&s, true, 1);
    ck(!s.credit_hold, "no hold for a telemetry frame");
    gs_tick(&s, fb_now());
    ck_eq(s.st.cls[GS_CLASS_TELEMETRY].sent, 1, "the diag was credited as sent");
    ck(!s.held, "and the controller is free");
    ck(!gs_verdict_pending(&s), "with no wait for the frames in the queue");

    const gs_deadline_t v = gs_command_received(&s, fb_now());
    ck(v == GS_DEADLINE_OK, "no inhibit, no skip");
    ck_eq(s.st.skipped, 0, "nothing skipped");
    ck(!s.credit_hold, "the deadline ended the hold");
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

/* ------------------------- F5: a withdrawn inhibit held past the bound --- */

/*
 * SPEC 5.2 ITEM 5, "A withdrawn frame held past the bound" (user, 2026-11-26,
 * tracker F5): "If an inhibit frame is withdrawn and the 1 ms limit is reached
 * while it is still in the controller, the scheduler must abort it again on
 * every later pass until the buffer is free. Telemetry waits behind it."
 *
 * The defect, from the adversarial review on 4c3afb4d: the withdrawal opened
 * one abort, the next pass ended it at the bound with the frame still held,
 * and nothing opened another. Step 2 aborts lower classes only, and a
 * stand-down withdraws once, on the falling edge of the live flag. With no
 * node acknowledging, the controller retries the frame, and a read every
 * 200 ms that never lands between retries left it there; it went out, stale,
 * when a node came back.
 *
 * Each pass below is placed on purpose, on a retry or in the gap between two
 * (fb_set_unacked()), so the gap's length decides nothing asserted here.
 *
 * THE RE-READ ON EACH PASS (user, 2026-11-27, tracker F5 option A): "On each
 * such pass, the scheduler must re-read the controller about every 2 us, for
 * up to 1 ms, and issue the abort at the first read with TS = 0." The C3's TS
 * between resends (VALIDATION.md 5.2): never 0 in error active (the model's
 * gap 0), about 16 us of 0 before each resend in error passive (gap 16). So
 * the passes that must leave the frame in the controller run in error active,
 * and the error-passive pass removes it.
 */

/* What the dense re-read is: 1 ms of reads, GS_REREAD_GAP_US (2 us) apart. */
#define REREAD_SPAN_US  1000
#define REREAD_GAP_US   2
#define REREAD_READS    (REREAD_SPAN_US / REREAD_GAP_US)

/* Step the model, with no pass, until the buffer reads `want`. */
static void until_state(gs_buf_t want)
{
    for (int i = 0; i < 2000 && fb_state_now() != want; i++)
    {
        fb_advance(1);
    }
    ck(fb_state_now() == want, "PRECONDITION: the buffer reads the state picked");
}

/* An inhibit with a marker byte, so the wire shows which one went out. */
static void queue_marked_inhibit(gs_t *s, uint8_t marker)
{
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    inh.data[0] = marker;
    gs_queue_frame(s, GS_CLASS_INHIBIT, &inh, fb_now());
}

/*
 * The held inhibit is given up while the controller is retrying it, and the
 * pass 1 ms later finds it retrying again: the bound is reached with the frame
 * still held. The caller runs it in error active (gap 0), so THAT SAME PASS
 * re-reads for the whole 1 ms and issues nothing (reviewer, 2026-11-27: 709b69ff
 * re-read only from the pass after it). A pass between the withdrawal and the
 * bound reads once.
 */
static void bound_reached_on_a_retry(gs_t *s)
{
    const int spins = fb_spins();
    fb_advance(500);
    until_state(GS_BUF_TRANSMITTING);
    gs_tick(s, fb_now());
    ck_eq(fb_spins() - spins, 0, "NO RE-READ BEFORE THE BOUND");

    fb_advance(500);
    until_state(GS_BUF_TRANSMITTING);
    const int64_t t0 = fb_now();
    const int r0 = fb_abort_reads();
    const int cmds = fb_abort_cmds();
    gs_tick(s, t0);
    ck_eq(held_in(s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the bound was reached with the inhibit still held");
    ck_eq(fb_now() - t0, REREAD_SPAN_US,
          "THE PASS THAT REACHED THE BOUND RE-READ FOR 1 MS");
    ck_eq(fb_abort_reads() - r0, 1 + REREAD_READS, "its reads were 2 us apart");
    ck_eq(fb_abort_cmds() - cmds, 0, "in error active it issued nothing");
}

/*
 * `n` passes 200 ms apart, the worker's receive timeout on a quiet bus, each
 * starting on a retry. In error active (gap 0) each one re-reads for the whole
 * 1 ms and issues nothing. Returns how many times the passes read the buffer.
 */
static int passes_on_retries(gs_t *s, int n)
{
    const int reads = fb_abort_reads();
    const int cmds = fb_abort_cmds();
    for (int i = 0; i < n; i++)
    {
        fb_advance(200000);
        until_state(GS_BUF_TRANSMITTING);
        const int64_t t0 = fb_now();
        const int r0 = fb_abort_reads();
        gs_tick(s, t0);
        ck_eq(fb_now() - t0, REREAD_SPAN_US,
              "THE PASS RE-READ FOR 1 MS, AND NO LONGER");
        ck_eq(fb_abort_reads() - r0, 1 + REREAD_READS,
              "ITS READS WERE 2 US APART");
    }
    ck_eq(fb_abort_cmds() - cmds, 0,
          "IN ERROR ACTIVE NO ABORT WAS ISSUED: TS never read 0");
    return fb_abort_reads() - reads;
}

/*
 * One pass starts on a retry in error passive (gap 16, the C3's): its re-read
 * meets the next gap inside the 1 ms, issues the abort at the first read in it,
 * and ends at that read (F7: the next pass reads the result). Returns the
 * abort commands it issued.
 */
static int pass_error_passive(gs_t *s)
{
    fb_set_unacked(true, 16);
    fb_advance(200000);
    until_state(GS_BUF_TRANSMITTING);
    const int64_t t0 = fb_now();
    const int cmds = fb_abort_cmds();
    gs_tick(s, t0);
    const int issued = fb_abort_cmds() - cmds;
    ck(fb_now() - t0 < REREAD_SPAN_US, "PRECONDITION: a gap came inside the 1 ms");
    ck(fb_gap_began_at() > t0, "PRECONDITION: the re-read met a gap");
    ck(fb_last_cmd_at() >= fb_gap_began_at()
       && fb_last_cmd_at() - fb_gap_began_at() < REREAD_GAP_US,
       "THE ABORT WAS ISSUED AT THE FIRST READ IN THE GAP");
    ck_eq(fb_now(), fb_last_cmd_at(), "AND THE PASS ENDED AT THAT READ (F7)");
    ck_eq(held_in(s, GS_CLASS_INHIBIT), 1,
          "the frame is booked on the next pass, not this one");
    return issued;
}

/* One pass lands between two retries. Returns the abort commands it issued. */
static int pass_in_a_gap(gs_t *s)
{
    fb_advance(200000);
    until_state(GS_BUF_AWAITING);
    const int cmds = fb_abort_cmds();
    const int spins = fb_spins();
    gs_tick(s, fb_now());
    ck_eq(fb_spins() - spins, 0, "its first read issued the abort: no re-read");
    return fb_abort_cmds() - cmds;
}

static void case_standdown_held_past_bound(void)
{
    g_case = "F5: a stand-down held past the bound is aborted on every pass";
    fb_reset();
    fb_set_unacked(true, 0);    /* error active: TS never reads 0 */
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now());
    queue_marked_inhibit(&s, 1);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");

    /* The trip lands while the controller is retrying it. */
    until_state(GS_BUF_TRANSMITTING);
    gs_withdraw_inhibits(&s, fb_now());
    ck_eq(json_standdown(&s), 1, "booked as a stand-down");

    bound_reached_on_a_retry(&s);
    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->abort_bound_hit, 1, "the 1 ms bound was reached and counted");

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());

    ck_eq(passes_on_retries(&s, 5), 5 * (1 + REREAD_READS),
          "THE LOOP READ THE BUFFER AGAIN ON EVERY LATER PASS");
    ck_eq(fb_submit_count(), 1, "TELEMETRY WAITS BEHIND IT");
    ck_eq(st->abort_bound_hit, 1, "the bound is counted once, not per pass");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "the inhibit is still held");
    ck_eq(st->cls[GS_CLASS_INHIBIT].sent, 0, "and not booked as sent");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the inhibit balance closes meanwhile");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "so does telemetry's");

    /* The controller is error passive now. */
    ck_eq(pass_error_passive(&s), 1,
          "THE ERROR-PASSIVE PASS ISSUED THE ABORT IN A GAP");
    fb_advance(200000);
    gs_tick(&s, fb_now());
    const gs_class_stats_t *c = &st->cls[GS_CLASS_INHIBIT];
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "THE NEXT PASS FOUND THE BUFFER FREE");
    ck_eq(c->withdrawn, 1, "and booked it `withdrawn`");

    /* The nodes come back. */
    fb_set_unacked(false, 0);
    run(&s, 2000, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "the controller has let it go");
    ck_eq(c->withdrawn, 1, "booked `withdrawn` once");
    ck_eq(c->sent, 0, "never as sent");
    ck_eq(st->skipped, 0, "and no skip");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
    ck_eq(wire_count_id(ID_INHIBIT), 0,
          "THE WITHDRAWN INHIBIT NEVER REACHED THE WIRE");
    ck_eq(wire_count_id(ID_DIAG), 1, "the page queued behind it went out");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "telemetry's balance closes");

    /*
     * A LATER WITHDRAWAL STARTS A NEW LOOP, with its own bound. Without
     * begin_abort() clearing the past-bound state, this loop would never
     * reach its bound and the count would stay at 1.
     */
    fb_set_unacked(true, 0);
    (void)gs_command_received(&s, fb_now());
    queue_marked_inhibit(&s, 2);
    run(&s, 20, 10);
    until_state(GS_BUF_TRANSMITTING);
    gs_withdraw_inhibits(&s, fb_now());
    bound_reached_on_a_retry(&s);
    ck_eq(st->abort_bound_hit, 2, "a second withdrawal reaches its own bound");
    fb_set_unacked(true, 16);
    ck_eq(pass_in_a_gap(&s), 1, "and is aborted again in a gap");
    fb_set_unacked(false, 0);
    run(&s, 2000, 10);
    ck_eq(wire_count_id(ID_INHIBIT), 0, "neither inhibit reached the wire");
    ck_standdown_identity(&s, "the identity holds after both");
    wire_log_sane();
}

/*
 * The deadline route: the VCM's next 0x051 withdraws the held answer as a
 * skip, and the bound is reached with it still held. Then another command
 * arrives while it is still there -- as in an error storm, where the frame is
 * retried for errors and not for want of an acknowledgement. That deadline
 * must not book the same frame again: one frame, one skip.
 */
static void case_deadline_skip_held_past_bound(void)
{
    g_case = "F5: a deadline skip held past the bound is aborted, booked once";
    fb_reset();
    fb_set_unacked(true, 0);    /* error active: TS never reads 0 */
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now());
    queue_marked_inhibit(&s, 1);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");

    until_state(GS_BUF_TRANSMITTING);
    const gs_deadline_t v1 = gs_command_received(&s, fb_now());
    ck_eq((long)v1, (long)GS_DEADLINE_MAYBE_LATE,
          "transmitting at its deadline: a skip that may go out late");
    ck_eq(s.st.skipped, 1, "one skip");
    queue_marked_inhibit(&s, 2);    /* the answer to the new command */

    bound_reached_on_a_retry(&s);
    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->abort_bound_hit, 1, "the 1 ms bound was reached and counted");

    ck_eq(passes_on_retries(&s, 3), 3 * (1 + REREAD_READS),
          "THE LOOP READ THE BUFFER AGAIN ON EVERY LATER PASS");
    ck_eq(fb_submit_count(), 1, "nothing was handed over behind it");

    /*
     * The next command, with the old answer still held. Its deadline purges
     * the queued answer 2 (never handed over: a skip of its own), and must not
     * book the held answer 1 again.
     */
    const gs_deadline_t v2 = gs_command_received(&s, fb_now());
    ck_eq((long)v2, (long)GS_DEADLINE_SKIPPED, "the queued answer was purged");
    ck_eq(s.st.skipped, 2, "ONE SKIP PER FRAME: the held one is not booked again");
    ck_eq(s.st.late_on_wire, 1, "and not counted late again");
    queue_marked_inhibit(&s, 3);    /* the answer to this command */

    ck_eq(pass_error_passive(&s), 1,
          "THE ERROR-PASSIVE PASS ISSUED THE ABORT IN A GAP");
    fb_set_unacked(false, 0);
    run(&s, 2000, 10);

    const gs_class_stats_t *c = &st->cls[GS_CLASS_INHIBIT];
    ck_eq(c->withdrawn, 2, "answers 1 and 2 booked `withdrawn`");
    ck_eq(c->sent, 1, "answer 3 sent");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
    ck_eq(wire_count_id(ID_INHIBIT), 1, "ONE inhibit reached the wire");
    ck(fb_wire_count() >= 1 && fb_wire_data(0)[0] == 3,
       "AND IT WAS THE ANSWER TO THE CURRENT COMMAND, not the stale one");
    wire_log_sane();
}

/*
 * THE FIRST PASS PAST THE BOUND RE-READS (spec 5.2 item 5; reviewer,
 * 2026-11-27, from the tester's emulation). On a quiet bus the pass after the
 * withdrawal comes at the worker's 200 ms receive timeout, so it is the one
 * that finds the bound passed. On 709b69ff that pass read once and only set the
 * past-bound state; the dense re-read began 200 ms later. Error passive (gap
 * 16) here, as in the tester's emulation: that first pass must issue the abort
 * in a gap, and the next pass books the frame withdrawn.
 */
static void case_first_pass_past_bound_rereads(void)
{
    g_case = "F5: the pass that finds the bound passed re-reads on that pass";
    fb_reset();
    fb_set_unacked(true, 16);   /* error passive, the C3's 16 us gap */
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now());
    queue_marked_inhibit(&s, 1);
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");

    until_state(GS_BUF_TRANSMITTING);
    const int spins = fb_spins();
    gs_withdraw_inhibits(&s, fb_now());
    gs_tick(&s, fb_now());
    ck_eq(fb_spins() - spins, 0, "THE WITHDRAWAL'S OWN PASS DOES NOT RE-READ");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "PRECONDITION: still held");
    const gs_stats_t *st = gs_stats(&s);
    ck_eq(st->abort_bound_hit, 0, "PRECONDITION: the bound is not reached yet");

    /* The next pass, 200 ms on, starts on a resend. */
    fb_advance(200000);
    until_state(GS_BUF_TRANSMITTING);
    const int64_t t0 = fb_now();
    const int cmds = fb_abort_cmds();
    gs_tick(&s, t0);
    ck_eq(st->abort_bound_hit, 1, "this pass reached the bound, counted once");
    ck(fb_spins() - spins > 0, "THE PASS THAT REACHED THE BOUND RE-READ");
    ck_eq(fb_abort_cmds() - cmds, 1, "AND ISSUED THE ABORT IN A GAP");
    ck(fb_now() - t0 < REREAD_SPAN_US, "inside its 1 ms");
    ck(fb_last_cmd_at() >= fb_gap_began_at()
       && fb_last_cmd_at() - fb_gap_began_at() < REREAD_GAP_US,
       "at the first read in the gap");
    ck_eq(fb_now(), fb_last_cmd_at(), "and the pass ended at that read (F7)");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "booked on the next pass, not this one");

    fb_advance(200000);
    gs_tick(&s, fb_now());
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "THE NEXT PASS FOUND THE BUFFER FREE");
    ck_eq(st->cls[GS_CLASS_INHIBIT].withdrawn, 1, "booked `withdrawn`");
    ck_eq(st->abort_bound_hit, 1, "the bound still counted once");

    fb_set_unacked(false, 0);
    run(&s, 2000, 10);
    ck_eq(wire_count_id(ID_INHIBIT), 0, "THE WITHDRAWN INHIBIT NEVER REACHED THE WIRE");
    ck_eq(st->cls[GS_CLASS_INHIBIT].sent, 0, "never booked as sent");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
    wire_log_sane();
}

/*
 * THE DRIVER'S COUNT LAGS THE BUFFER (reviewer, 2026-11-27). On the device the
 * abort reads the controller's registers, and outstanding() reads the driver's
 * tx_pending, which falls only when the transmit interrupt has run. A pass in
 * between sees the buffer EMPTY with the frame still counted. For a withdrawn
 * inhibit, gs_tick() step 3 keeps the loop open there, so the departure stays
 * step 3's: booked `withdrawn` on the next pass, never `sent` by step 1.
 */
static void case_withdrawn_inhibit_count_lag(void)
{
    g_case = "F5: a withdrawn inhibit freed before the driver's count falls";
    fb_reset();
    fb_set_contended(true);     /* STRESS: the answer waits AWAITING */
    fb_set_count_lag(50);
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now());
    queue_marked_inhibit(&s, 1);
    gs_tick(&s, fb_now());
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1,
          "PRECONDITION: the answer is in the controller");
    ck(fb_state_now() == GS_BUF_AWAITING, "PRECONDITION: and awaiting");

    gs_withdraw_inhibits(&s, fb_now());
    ck_eq(fb_abort_cmds(), 1, "the withdrawal issued the abort");
    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    const int subs = fb_submit_count();

    /* The buffer has freed; the driver still counts the frame. */
    fb_advance(10);
    ck(fb_state_now() == GS_BUF_EMPTY, "PRECONDITION: the buffer reads EMPTY");
    ck(fb_counted_now(), "PRECONDITION: the driver still counts it");
    gs_tick(&s, fb_now());
    const gs_class_stats_t *c = &s.st.cls[GS_CLASS_INHIBIT];
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "THE LOOP STAYED OPEN: still held");
    ck_eq(c->sent, 0, "not booked as sent");
    ck_eq(c->withdrawn, 0, "not booked yet");
    ck_eq(fb_submit_count(), subs, "nothing handed over behind it");

    /* The count has fallen. */
    fb_advance(50);
    ck(!fb_counted_now(), "PRECONDITION: the count has fallen");
    gs_tick(&s, fb_now());
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "THE NEXT PASS BOOKED IT");
    ck_eq(c->withdrawn, 1, "AS `withdrawn`");
    ck_eq(c->sent, 0, "NEVER AS SENT");

    run(&s, 5000, 10);
    ck_eq(wire_count_id(ID_INHIBIT), 0, "THE WITHDRAWN INHIBIT NEVER REACHED THE WIRE");
    ck_eq(wire_count_id(ID_DIAG), 1, "the page went out after it");
    ck_eq(c->sent, 0, "and is still not booked as sent");
    ck_eq(residual(&s, GS_CLASS_INHIBIT), 0, "the balance closes");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "so does telemetry's");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
    wire_log_sane();
}

/*
 * THE RE-READ IS BOUNDED AT 1 MS A PASS (spec 5.2 item 5, user 2026-11-27): it
 * does not wait for a window that comes later. The resends here are 1.5 ms
 * apart, longer than the span (the C3's are 276 us apart), so where the pass
 * starts decides whether a 16 us window falls inside its 1 ms.
 */
static void case_reread_bounded_by_the_span(void)
{
    g_case = "F5: the re-read stops at 1 ms, and meets a window inside it";
    fb_reset();
    fb_set_unacked(true, 0);
    gs_t s;
    gs_init(&s, fb_hal());

    (void)gs_command_received(&s, fb_now());
    queue_marked_inhibit(&s, 1);
    run(&s, 20, 10);
    until_state(GS_BUF_TRANSMITTING);
    gs_withdraw_inhibits(&s, fb_now());
    bound_reached_on_a_retry(&s);

    /* Error passive, with each resend 1500 us long and 16 us of TS = 0 after. */
    fb_stick_transmitting(1500);
    fb_set_unacked(true, 16);
    fb_advance(200000);
    until_state(GS_BUF_AWAITING);
    until_state(GS_BUF_TRANSMITTING);   /* a resend has just begun */

    const int64_t t0 = fb_now();
    const int cmds = fb_abort_cmds();
    gs_tick(&s, t0);
    ck_eq(fb_now() - t0, REREAD_SPAN_US, "THE PASS STOPPED AT 1 MS");
    ck_eq(fb_abort_cmds() - cmds, 0, "no window inside it, so no abort");
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 1, "the frame is still held");
    ck(fb_state_now() == GS_BUF_TRANSMITTING, "and still resending");

    /* The next pass starts 600 us into a resend: the window comes 900 us in. */
    fb_advance(200000);
    until_state(GS_BUF_AWAITING);
    until_state(GS_BUF_TRANSMITTING);
    fb_advance(600);
    const int64_t t1 = fb_now();
    gs_tick(&s, t1);
    ck_eq(fb_abort_cmds() - cmds, 1, "THE WINDOW INSIDE THE 1 MS GOT THE ABORT");
    ck_eq(fb_last_cmd_at() - t1, 900, "at the first read in it");
    fb_advance(200000);
    gs_tick(&s, fb_now());
    ck_eq(held_in(&s, GS_CLASS_INHIBIT), 0, "the next pass found the buffer free");
    ck_eq(s.st.cls[GS_CLASS_INHIBIT].withdrawn, 1, "booked `withdrawn`");

    fb_stick_transmitting(0);
    fb_set_unacked(false, 0);
    run(&s, 2000, 10);
    ck_eq(wire_count_id(ID_INHIBIT), 0, "THE WITHDRAWN INHIBIT NEVER REACHED THE WIRE");
    ck_standdown_identity(&s, "withdrawn + dropped == skipped + standdown");
    wire_log_sane();
}

/*
 * F7 (spec 5.2 item 6, "never lost silently"; user 2026-11-26). A page is
 * being aborted for an inhibit, the first command had no effect (the race),
 * and the pass that reaches the 1 ms bound finds the page AWAITING and issues
 * the command again. That command removes it 7 us later. On a0c19825 the
 * bound ended the loop on that read with the page still held, and the next
 * pass's step 1 saw the buffer free and booked the page `sent`: never on the
 * wire, never requeued, and the balance still closed.
 */
static void case_page_aborted_at_the_bound_is_requeued(void)
{
    g_case = "F7: a page aborted on the bound's read is requeued, not sent";
    fb_reset();
    fb_set_unacked(true, 20);   /* keeps the page retrying, so it stays held */
    gs_t s;
    gs_init(&s, fb_hal());

    gi_frame_t d = mk(ID_DIAG, GI_TX_DIAG);
    gs_queue_frame(&s, GS_CLASS_TELEMETRY, &d, fb_now());
    run(&s, 20, 10);
    ck_eq(held_in(&s, GS_CLASS_TELEMETRY), 1,
          "PRECONDITION: the page is in the controller");

    (void)gs_command_received(&s, fb_now());
    gi_frame_t inh = mk(ID_INHIBIT, GI_TX_INHIBIT);
    gs_queue_frame(&s, GS_CLASS_INHIBIT, &inh, fb_now());

    /* Preemption opens the loop in a gap; its command lands at TS = 1. */
    until_state(GS_BUF_AWAITING);
    fb_race_next(300);
    gs_tick(&s, fb_now());
    ck_eq(fb_aborts_with_no_effect(), 1,
          "PRECONDITION: the first command had no effect");
    const int64_t opened = fb_now();

    /* A pass on a retry before the bound: the loop waits. */
    fb_advance(500);
    until_state(GS_BUF_TRANSMITTING);
    gs_tick(&s, fb_now());

    /* The pass that reaches the bound lands in a gap and issues the command. */
    fb_advance(opened + 1000 - fb_now());
    until_state(GS_BUF_AWAITING);
    const int cmds = fb_abort_cmds();
    gs_tick(&s, fb_now());
    ck_eq(fb_abort_cmds() - cmds, 1,
          "PRECONDITION: the bound's pass issued the command");
    ck(fb_now() - opened >= 1000, "PRECONDITION: at the bound");

    /* The command frees the buffer; the next pass sees it. */
    fb_advance(50);
    ck(fb_state_now() == GS_BUF_EMPTY, "PRECONDITION: the abort removed the page");
    gs_tick(&s, fb_now());

    const gs_class_stats_t *t = &s.st.cls[GS_CLASS_TELEMETRY];
    ck_eq(t->sent, 0, "THE ABORTED PAGE IS NOT BOOKED SENT");
    ck_eq(t->aborted, 1, "it is booked aborted");
    ck_eq(t->requeued, 1, "and requeued");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "the balance closes");

    fb_set_unacked(false, 0);
    run(&s, 3000, 10);
    ck_eq(wire_count_id(ID_INHIBIT), 1, "the inhibit went out");
    ck(fb_wire_count() >= 1 && fb_wire_id(0) == ID_INHIBIT, "first");
    ck_eq(wire_count_id(ID_DIAG), 1, "THE PAGE REACHED THE WIRE, once");
    ck_eq(t->sent, 1, "and is booked sent once");
    ck_eq(residual(&s, GS_CLASS_TELEMETRY), 0, "the balance still closes");
    wire_log_sane();
}

int main(void)
{
    printf("scheduler cases (spec 5.2 item 10, emulation half)\n");
    case_preempt_awaiting_telemetry();
    case_departure_during_abort_is_not_sent();
    case_never_aborts_while_transmitting();
    case_deadline_purges_queued_inhibit();
    case_three_skips_sliding_window();
    case_two_skips_no_trip();
    case_abort_is_not_a_completion();
    case_ontime_unverified();
    case_counted_command_is_late();
    case_empty_queue_is_on_time();
    case_arrival_between_reads_is_counted();
    case_command_after_the_count_is_on_time();
    case_deadline_ends_the_wait_on_time();
    case_standdown_ends_the_wait();
    case_probe_counted_command_is_late();
    case_probe_not_among_them_is_on_time();
    case_telemetry_overflow();
    case_nothing_behind_an_inhibit();
    case_inhibit_queue_full_is_a_skip();
    case_withdrawn_held_inhibit_never_returns();
    case_transmitting_at_deadline_is_maybe_late();
    case_held_credit_is_late();
    case_held_credit_spares_telemetry();
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
    case_withdrawal_clears_inhibit_outstanding();
    case_identity_carried_requeue_sets_no_wait();
    case_standdown_queued_answer();
    case_standdown_held_answer();
    case_standdown_after_deadline_books_once();
    case_rearm_during_deadline_withdrawal();
    case_rearm_during_standdown_withdrawal();
    case_rearm_with_live_answer_is_no_standdown();
    case_standdown_held_past_bound();
    case_deadline_skip_held_past_bound();
    case_first_pass_past_bound_rereads();
    case_withdrawn_inhibit_count_lag();
    case_reread_bounded_by_the_span();
    case_page_aborted_at_the_bound_is_requeued();

    if (g_fail == 0)
    {
        printf("all cases passed\n");
        return 0;
    }
    printf("%d assertion(s) FAILED\n", g_fail);
    return 1;
}
