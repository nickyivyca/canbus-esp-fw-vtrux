/*
 * gi_sched -- the one transmit owner. See gi_sched.h for the contract and for
 * the hardware measurements every decision here rests on.
 *
 * NO PLATFORM CALLS. No printf, no allocation, no clock of its own, no spinning:
 * time arrives as an argument and the controller is reached only through
 * gs_hal_t. That is what lets the host suite compile this file unchanged and
 * drive it from a virtual clock (spec 5.2 item 8).
 */

#include <string.h>
#include <stdio.h>

#include "gi_sched.h"

/*
 * Item 4's bound, from the user: 1 ms. The measurement put the loop's worst case
 * at 36 us over 78 trials, so 1 ms is ~28x the observed worst and is a backstop
 * rather than an operating parameter. Reaching it is counted, and the spec says
 * the inhibit then stays at the head of its class and the scheduler keeps
 * trying -- so the bound ends the LOOP, not the attempt.
 */
#define GS_ABORT_BOUND_US   1000

/* Trip 7: 3 skips within 1 s (spec 7 trip 7, amended 2026-09-27). */
#define GS_SKIP_WINDOW_US   1000000
#define GS_SKIP_TRIP_N      3

/*
 * Forward declarations. note_skip() is used by gs_queue_frame(), which sits above
 * it because the queue helpers belong together -- every path that loses an
 * inhibit frame must go through note_skip() so the counters and trip 7's window
 * cannot disagree about what happened.
 */
static void     note_skip(gs_t *s, int64_t now, bool late);
static void     begin_abort(gs_t *s, int64_t now);
static uint32_t skips_in_window(const gs_t *s, int64_t now);

/* ------------------------------------------------------------- queueing --- */

static uint8_t cap_for(gs_class_t c)
{
    switch (c)
    {
    case GS_CLASS_INHIBIT:   return GS_Q_INHIBIT;
    case GS_CLASS_PROBE:     return GS_Q_PROBE;
    case GS_CLASS_TELEMETRY: return GS_Q_TELEMETRY;
    default:                 return 0;
    }
}

static bool q_push(gs_queue_t *q, const gi_frame_t *f, int64_t now, uint32_t seq)
{
    if (q->n >= q->cap)
    {
        return false;
    }
    const int i = (q->head + q->n) % q->cap;
    q->q[i].f = *f;
    q->q[i].t_queued = now;
    q->q[i].seq = seq;
    /*
     * EXPLICITLY FALSE, because q_pop() does not clear the slot it leaves behind.
     * A slot reused after a requeue would otherwise still carry that frame's
     * `carried` flag and suppress this frame's max_queue_us -- a wait figure
     * reading 0 for a frame that really waited.
     */
    q->q[i].carried = false;
    q->n++;
    return true;
}

/*
 * Put a frame back AT THE HEAD, which is what item 4 requires of an aborted
 * telemetry frame: "requeues it at the head of the telemetry class". Ordinary
 * FIFO would send it behind pages queued while it was being aborted, so a page
 * could be starved by its own preemption.
 */
static bool q_push_head(gs_queue_t *q, const gs_slot_t *sl)
{
    if (q->n >= q->cap)
    {
        return false;
    }
    q->head = (uint8_t)((q->head + q->cap - 1) % q->cap);
    q->q[q->head] = *sl;
    q->n++;
    return true;
}

static const gs_slot_t *q_peek(const gs_queue_t *q)
{
    return (q->n == 0) ? NULL : &q->q[q->head];
}

static void q_pop(gs_queue_t *q)
{
    if (q->n == 0)
    {
        return;
    }
    q->head = (uint8_t)((q->head + 1) % q->cap);
    q->n--;
}

/* ---------------------------------------------------------------- set-up --- */

void gs_init(gs_t *s, const gs_hal_t *hal)
{
    memset(s, 0, sizeof(*s));
    s->hal = hal;
    for (int c = 0; c < GS_CLASS_N; c++)
    {
        s->q[c].cap = cap_for((gs_class_t)c);
        s->q[c].head = 0;
        s->q[c].n = 0;
    }
    s->next_seq = 1;
}

void gs_reset(gs_t *s)
{
    const gs_hal_t *hal = s->hal;
    gs_init(s, hal);
}

void gs_rearm(gs_t *s, const gs_hal_t *hal)
{
    /*
     * KEEP WHAT THE HARDWARE IS HOLDING. Everything else is per-arm and is cleared.
     * See the header for why gs_init() is the wrong call here: it forgets a frame
     * that is really in the controller, and the next handover then queues behind it
     * in the driver's FIFO.
     */
    const bool       held       = s->held;
    const gs_class_t held_class = s->held_class;
    const uint32_t   held_seq   = s->held_seq;
    const int64_t    held_since = s->held_since;
    const gi_frame_t held_f     = s->held_f;
    const bool       aborting   = s->aborting;
    const int64_t    abort_since = s->abort_since;
    const uint32_t   abort_cmds_this = s->abort_cmds_this;

    gs_init(s, hal);

    s->held = held;
    s->held_class = held_class;
    s->held_seq = held_seq;
    s->held_since = held_since;
    s->held_f = held_f;
    s->aborting = aborting;
    s->abort_since = abort_since;
    s->abort_cmds_this = abort_cmds_this;
    /*
     * SPEC 5.2 ITEM 6 (2026-09-29): declare the carried-over frame, because this
     * arm will see it leave and did not queue it.
     *
     * `held_carried` is set from `held` rather than preserved, so a frame carried
     * across two re-arms in a row is declared in each arm it survives into -- each
     * arm sees one departure it did not queue.
     */
    s->held_carried = held;
    if (held)
    {
        s->st.cls[held_class].carried_in++;
    }
    /*
     * `inhibit_outstanding` is deliberately NOT restored: a new arm has no inhibit
     * of its own outstanding, and leaving it set would make the first
     * gs_queue_frame() of this arm look like an ordering violation.
     */
}

void gs_withdraw_inhibits(gs_t *s, int64_t now)
{
    while (s->q[GS_CLASS_INHIBIT].n > 0)
    {
        q_pop(&s->q[GS_CLASS_INHIBIT]);
        s->st.cls[GS_CLASS_INHIBIT].withdrawn++;
        note_skip(s, now, false);
    }
    if (s->held && s->held_class == GS_CLASS_INHIBIT && !s->aborting)
    {
        begin_abort(s, now);
    }
    s->st.skip_window = skips_in_window(s, now);
}

bool gs_queue_frame(gs_t *s, gs_class_t cls, const gi_frame_t *f, int64_t now)
{
    if (cls >= GS_CLASS_N)
    {
        return false;
    }
    if (cls == GS_CLASS_INHIBIT)
    {
        /*
         * THE DEADLINE TOKEN, consumed here. Absent means gs_command_received()
         * was not called for this command before its answer was queued.
         *
         * This is EXACT, unlike the `inhibit_outstanding` test it replaced: that
         * one fired only when the PREVIOUS inhibit was still in flight, which is
         * the rare case. With the calls swapped the previous frame had normally
         * completed milliseconds earlier, so nothing was outstanding, no violation
         * was counted, and the deadline purge silently withdrew the NEW inhibit on
         * every command. Caught by the reviewing session, 2026-09-27.
         */
        if (!s->deadline_token)
        {
            s->st.order_violations++;
        }
        s->deadline_token = false;
    }
    s->st.cls[cls].queued++;
    if (!q_push(&s->q[cls], f, now, s->next_seq))
    {
        /*
         * Item 6: telemetry is never lost SILENTLY. A full queue drops the
         * newest, and the drop is counted per class. Dropping the newest rather
         * than the oldest keeps the oldest page's wait bounded by its own
         * position instead of letting a burst starve it indefinitely.
         *
         * D3: FOR THE INHIBIT CLASS A DROP IS A MISSED INHIBIT, so it is also a
         * skip and feeds trip 7 -- counting it only as `dropped` would lose it
         * from the trip that exists to catch a stuck transmit path. With the
         * deadline purge below this should be unreachable, and saying so is the
         * point: if `dropped` ever moves for the inhibit class, the purge is not
         * doing its job.
         */
        s->st.cls[cls].dropped++;
        if (cls == GS_CLASS_INHIBIT)
        {
            note_skip(s, now, false);   /* never went out */
        }
        return false;
    }
    s->next_seq++;
    return true;
}

/* ------------------------------------------------------------ the engine --- */

/*
 * Record one skip. Every path that loses an inhibit frame comes here, so the
 * window and the counters cannot disagree about what happened.
 */
static void note_skip(gs_t *s, int64_t now, bool late)
{
    s->st.skipped++;
    if (late) { s->st.skipped_late++; }
    else      { s->st.skipped_withdrawn++; }
    s->skip_ts[s->skip_ts_head] = now;
    s->skip_ts_head = (uint8_t)((s->skip_ts_head + 1) % GS_SKIP_RING);
    if (s->skip_ts_n < GS_SKIP_RING)
    {
        s->skip_ts_n++;
    }
}

/* How many skips fall inside the last GS_SKIP_WINDOW_US, ending now. */
static uint32_t skips_in_window(const gs_t *s, int64_t now)
{
    uint32_t k = 0;
    for (uint8_t i = 0; i < s->skip_ts_n; i++)
    {
        if (now - s->skip_ts[i] < GS_SKIP_WINDOW_US)
        {
            k++;
        }
    }
    return k;
}

static void note_left_controller(gs_t *s, int64_t now, uint32_t rx_backlog)
{
    const gs_class_t c = s->held_class;
    const int64_t held_us = now - s->held_since;

    s->st.cls[c].sent++;
    /*
     * THE COMPLETION IS COUNTED; THE DURATION IS NOT, for a frame carried in
     * across a re-arm (spec 5.2 item 6, 2026-09-29). `sent` has to move or the
     * frame vanishes from the balance -- `carried_in` is the term that pays for
     * it -- but `held_since` is in the previous arm, so this arm cannot say how
     * long the hold was. It is not clamped to the re-arm instant either: that
     * would be a figure the device never measured, reported as if it had.
     */
    if (!s->held_carried
        && held_us > 0 && (uint32_t)held_us > s->st.cls[c].max_hold_us)
    {
        s->st.cls[c].max_hold_us = (uint32_t)held_us;
    }

    if (c == GS_CLASS_INHIBIT)
    {
        /*
         * AN ON-TIME CLAIM IS ONLY AS GOOD AS THE RECEIVE QUEUE WAS EMPTY.
         *
         * This completion happened before the worker dequeued the VCM's next
         * 0x051 -- that is what makes it "on time" in the only sense the device
         * can evaluate. But the dequeue lags the arrival by the backlog, so if
         * frames were already waiting here, the VCM's next command may ALREADY
         * have been on the wire when this frame completed, and the frame was
         * physically late. An empty queue rules that out, because an arrived
         * frame would be in it.
         *
         * Exact only up to the ISR latency between a frame reaching the
         * controller's hardware FIFO and the ISR moving it to the software
         * queue. Microseconds against a 4.69 ms minimum truck gap, but stated
         * rather than glossed.
         */
        if (rx_backlog == 0)
        {
            s->st.ontime++;
        }
        else
        {
            s->st.ontime_unverified++;
            if (rx_backlog > s->st.unverified_max_backlog)
            {
                s->st.unverified_max_backlog = rx_backlog;
            }
        }
        s->inhibit_outstanding = false;
    }

    s->held = false;
    s->held_carried = false;
    s->aborting = false;
}

/*
 * Start withdrawing whatever the controller holds. Item 4: re-abort while the
 * buffer is AWAITING, wait while it is TRANSMITTING. The first command is issued
 * here so that the common case -- the buffer already awaiting -- costs one tick
 * rather than two.
 */
static void begin_abort(gs_t *s, int64_t now)
{
    s->aborting = true;
    s->abort_since = now;
    s->abort_cmds_this = 0;
    s->st.aborts++;

    /*
     * ONE call, which reads the state and issues the command only if it is
     * AWAITING. Doing it as two calls let the controller slip into arbitration
     * in between, putting the command exactly where the measurement says it has
     * no effect.
     */
    if (s->hal->abort_if_awaiting(s->hal->ctx) == GS_BUF_AWAITING)
    {
        s->abort_cmds_this++;
        s->st.abort_cmds++;
    }
}

/*
 * One turn of item 4's loop. Returns true when the abort is finished with --
 * either the buffer is free, or the bound was reached.
 *
 * IT DOES NOT RE-ISSUE THE COMMAND WHILE TRANSMITTING. That is the case the
 * measurement showed has no effect (7 of 8 under contention), and repeating it
 * there would burn the bound on a no-op instead of waiting for the window where
 * the abort works -- which is the instant the frame loses arbitration and TS
 * falls to 0.
 */
static bool abort_step(gs_t *s, int64_t now)
{
    /*
     * The read and the conditional command are one operation, so there is no
     * window here for the controller to enter arbitration between deciding and
     * acting. What it returns is the state it actually acted on.
     */
    const gs_buf_t b = s->hal->abort_if_awaiting(s->hal->ctx);

    if (b == GS_BUF_EMPTY)
    {
        const int64_t took = now - s->abort_since;
        if (took > 0 && (uint32_t)took > s->st.abort_max_us)
        {
            s->st.abort_max_us = (uint32_t)took;
        }
        return true;
    }
    if (b == GS_BUF_AWAITING)
    {
        s->abort_cmds_this++;
        s->st.abort_cmds++;
    }
    /* GS_BUF_TRANSMITTING: no command was issued. Wait for TS to fall. */

    if (now - s->abort_since >= GS_ABORT_BOUND_US)
    {
        s->st.abort_bound_hit++;
        return true;
    }
    return false;
}

static gs_class_t next_class(const gs_t *s, bool *found)
{
    for (int c = 0; c < GS_CLASS_N; c++)
    {
        if (s->q[c].n > 0)
        {
            *found = true;
            return (gs_class_t)c;
        }
    }
    *found = false;
    return GS_CLASS_N;
}

void gs_tick(gs_t *s, int64_t now, uint32_t rx_backlog)
{
    /* Trip 7's window slides; nothing to advance, only to recompute. */
    s->st.skip_window = skips_in_window(s, now);

    /*
     * 1. Has what the controller held left it? This is the only completion
     *    signal available, and it cannot distinguish a real transmission from an
     *    aborted one -- the driver reports both as TX_SUCCESS. The scheduler's
     *    own record of WHAT it handed over is what makes the credit correct
     *    (item 5), which is why held_class is consulted and not the alert.
     */
    /*
     *    THE `!s->aborting` GUARD IS LOAD-BEARING. The driver reports an aborted
     *    frame exactly as it reports a real transmission, so "it left the
     *    controller" alone would credit an ABORTED frame as sent -- which is the
     *    same confusion item 5 exists to prevent, one level down in the class
     *    statistics. While an abort is in progress the departure belongs to step
     *    3, which counts it as aborted and requeues it.
     */
    if (s->held && !s->aborting && !s->hal->outstanding(s->hal->ctx))
    {
        note_left_controller(s, now, rx_backlog);
    }

    /*
     * 2. Preemption. An inhibit is waiting and the controller holds something
     *    of a lower class: abort it (item 4) and put it back at the head of its
     *    class (item 6 -- aborted and requeued, both counted).
     */
    if (!s->aborting && s->held && s->held_class != GS_CLASS_INHIBIT
        && s->q[GS_CLASS_INHIBIT].n > 0
        && s->hal->buf_state(s->hal->ctx) == GS_BUF_AWAITING)
    {
        /*
         * ONLY WHEN THE BUFFER IS AWAITING. If it is TRANSMITTING we simply
         * WAIT -- we do not enter the abort state at all -- and step 1 credits
         * the frame as sent when it finishes.
         *
         * THE FIRST VERSION ENTERED THE ABORT STATE REGARDLESS, and a test
         * caught what that costs. With the buffer transmitting, no command can
         * be issued (item 4 waits), so the scheduler sat in the abort state until
         * the frame COMPLETED ON THE WIRE -- and then, because the `!aborting`
         * guard had skipped the completion path, treated the departure as an
         * abort and REQUEUED it. Every preempted-but-completed telemetry page
         * went out TWICE. That is worse than a statistics error: it doubles
         * telemetry traffic in exactly the contended situation where the
         * scheduler exists to protect the bus.
         *
         * Waiting instead is also what spec 5.2 item 4 actually says: "A frame
         * already being transmitted cannot be aborted (the controller finishes
         * it); the inhibit waits for it." If the frame later loses arbitration
         * the buffer returns to AWAITING and the next tick aborts it -- which is
         * the only window where the command works.
         */
        begin_abort(s, now);
    }

    /*
     * 3. Turn the abort loop. Nothing is handed over while it runs: item 3 says
     *    at most one frame is with the driver at a time, and handing over during
     *    an abort would put the new frame behind the one being removed.
     */
    if (s->aborting)
    {
        if (!abort_step(s, now))
        {
            return;
        }
        s->aborting = false;
        if (s->held && !s->hal->outstanding(s->hal->ctx))
        {
            /*
             * The aborted frame is gone. Requeue it at the head of its class --
             * unless it was the inhibit being withdrawn at its deadline, which
             * gs_command_received() has already accounted for as a skip and
             * must NOT be sent later.
             */
            const gs_class_t c = s->held_class;
            /*
             * D6. The frame left during an abort, and the device CANNOT tell
             * whether the abort removed it or it completed on the wire -- the
             * driver reports both as TX_SUCCESS with msgs_to_tx 0. Requeueing is
             * the safe choice for telemetry (a page sent twice is harmless; one
             * silently lost is not), but it means `aborted` and `requeued` may
             * include double-sends, bounded by this counter.
             */
            s->st.ambiguous_departures++;
            if (c != GS_CLASS_INHIBIT)
            {
                gs_slot_t sl;
                sl.f = s->held_f;
                sl.t_queued = s->held_since;
                sl.seq = s->held_seq;
                /*
                 * `t_queued` here is the handover instant of an EARLIER ARM when
                 * the frame was carried in, so the flag travels with the frame
                 * and the next handover leaves max_queue_us alone.
                 */
                sl.carried = s->held_carried;
                s->st.cls[c].aborted++;
                if (q_push_head(&s->q[c], &sl))
                {
                    s->st.cls[c].requeued++;
                }
                else
                {
                    s->st.cls[c].dropped++;
                }
            }
            else
            {
                s->st.cls[GS_CLASS_INHIBIT].aborted++;
                /*
                 * An inhibit only ever reaches the abort loop to be WITHDRAWN:
                 * preemption (step 2) aborts lower classes, never this one, so
                 * the only callers are gs_withdraw_inhibits() and the deadline.
                 * Item 5 forbids sending it later, so this is where the frame
                 * leaves the accounting and the identity's term is booked.
                 */
                s->st.cls[GS_CLASS_INHIBIT].withdrawn++;
                s->inhibit_outstanding = false;
            }
            s->held = false;
            s->held_carried = false;
        }
        else if (s->held)
        {
            /* The bound was reached and the frame is still there. Item 4: keep
             * trying. Nothing is handed over, and the next tick starts again. */
            return;
        }
    }

    /*
     * 4. Hand over the next frame, highest class first, FIFO within a class.
     *    Item 7 falls out of this rather than being a separate rule: while an
     *    inhibit is outstanding, `held` is true, so nothing else can go.
     */
    if (!s->held)
    {
        bool found = false;
        const gs_class_t c = next_class(s, &found);
        if (!found)
        {
            return;
        }
        const gs_slot_t *sl = q_peek(&s->q[c]);
        if (sl == NULL)
        {
            return;
        }
        /*
         * A probe honours its due time; the caller owns the wait (the core's
         * `have_due` contract says spinning is a platform behaviour). If it is
         * not due yet, nothing else may jump it: the classes are a priority
         * order, not a race.
         */
        if (sl->f.have_due && now < sl->f.due_us)
        {
            return;
        }

        const int64_t wait_us = now - sl->t_queued;
        if (!sl->carried
            && wait_us > 0 && (uint32_t)wait_us > s->st.cls[c].max_queue_us)
        {
            s->st.cls[c].max_queue_us = (uint32_t)wait_us;
        }

        if (!s->hal->submit(s->hal->ctx, &sl->f))
        {
            /*
             * The driver refused. Trip 7 counts this at once for an inhibit;
             * for anything else it stays queued and is retried next tick, which
             * is why it is counted but not dropped.
             */
            s->st.tx_refused++;
            s->st.cls[c].refused++;
            return;
        }

        s->st.cls[c].handed++;
        s->held = true;
        s->held_class = c;
        s->held_seq = sl->seq;
        s->held_f = sl->f;
        s->held_since = now;
        s->held_carried = sl->carried;
        if (c == GS_CLASS_INHIBIT)
        {
            s->inhibit_outstanding = true;
            s->inhibit_seq = sl->seq;
            s->inhibit_handed = now;
        }
        q_pop(&s->q[c]);
    }
}

#if GI_INSTRUMENT_ABORTWIN
/*
 * Count how often this call is entered in the state the pre-fix defect needed.
 *
 * NOT static, deliberately: a static helper is inlined away and then an ELF
 * symbol check naming it passes on a measurement image, which is the exact gap
 * the reviewer's M5 mutation found in the txabort row of E3.
 *
 * The early return is what keeps this free. `outstanding()` is a
 * twai_get_status_info() call on the device, and the shipping code never makes
 * it while aborting -- `!s->aborting` short-circuits ahead of it -- so the read
 * has to be added here, and it is added ONLY inside the state being measured.
 *
 * WHAT IT MEASURED, 2026-10-04, bench WiCAN, 90 s saturated case 3 arm:
 * reached 0, free 0, against 1463 aborts / 8997 commands / 8983 transmissions.
 * The precondition was never reached. Since `reached` was 0 the added
 * twai_get_status_info() never ran either, so the arm cannot have been
 * perturbed by its own instrument.
 *
 * WHY IT IS 0, AND THIS CORRECTS 19577a8's COMMIT MESSAGE, which says the 1 ms
 * bound closes each abort before the next command arrives. IT DOES NOT, and the
 * counters say so: abort_step() tests GS_BUF_EMPTY *before* it tests the bound,
 * `abort_bound_hit` is 0 in every recorded arm, and `abort_max_us` -- which is
 * `now - abort_since` at the EMPTY exit -- is 0 in all but one (f977d81's single
 * 73 us sample). So every abort closed inside the tick that opened it; the bound
 * never engaged. Two distinct claims, both true, which that message conflated:
 *
 *   OBSERVED   aborts open and close within one tick, having begun 17-161 us
 *              after the inhibit was queued (inhibit max_queue_us across the
 *              arms, i.e. just after a command), while the next command is
 *              >= 2503 us away (rx_gap min). Nothing survives to the
 *              gs_command_received() call, so `reached` is 0.
 *   GUARANTEED abort_step() closes ANY abort at `now - abort_since >= 1000 us`
 *              whatever opened it -- preemption, the deadline path below, or
 *              gs_withdraw_inhibits() -- so an abort's life is capped near 1 ms
 *              even if in-tick resolution stopped happening. The bound is a cap,
 *              not an observed event. It is evaluated only when gs_tick() runs,
 *              so the real cap is 1 ms plus the wait for the next tick.
 *
 * WHAT IS UNTESTED: `skipped` is 0 on every recorded arm, so no arm has ever had
 * an inhibit still queued or held when the next 0x051 was dequeued. The bound
 * argument above covers that case too, but it has never been exercised -- if an
 * arm ever reports a skip, build this instrument again rather than reasoning
 * about it.
 */
void gs_abortwin_note(gs_t *s)
{
    if (!s->held || !s->aborting)
    {
        return;
    }
    s->st.abortwin_reached++;
    if (!s->hal->outstanding(s->hal->ctx))
    {
        /*
         * Pre-fix, THIS is the instant the frame was credited as sent while an
         * abort was removing it. Post-fix the guard declines and step 3 books
         * it. Either way nothing here decides anything -- this counts only.
         */
        s->st.abortwin_free++;
    }
}
#endif

gs_deadline_t gs_command_received(gs_t *s, int64_t now, uint32_t rx_backlog)
{
    /*
     * The deadline has been evaluated for this command. Set before anything else,
     * so every return path below leaves it set: a deadline that found nothing to
     * withdraw is still a deadline, and the ordering check asks whether it RAN,
     * not what it found.
     */
    s->deadline_token = true;

    /*
     * Observe a completion FIRST. A frame that finished before this dequeue is
     * on time in the only sense the device can evaluate, and checking after the
     * withdrawal decision would score it as a skip.
     *
     * `!s->aborting` IS THE SAME LOAD-BEARING GUARD AS IN gs_tick (step 1), AND
     * IT WAS MISSING HERE. The driver reports an aborted frame exactly as it
     * reports a real transmission, so "it left the controller" alone credits an
     * ABORTED frame as SENT -- and on this path that lost the frame outright:
     * booked sent, `aborted` and `requeued` never incremented, and the page never
     * on the wire. The identity still closed (queued 1 = sent 1), which is why no
     * balance check in the suite noticed a page that had vanished.
     *
     * Found 2026-10-03 by the reviewing session while tracing why a mutation of
     * this call site survived; spec 5.2 item 6 requires aborted and requeued to
     * both be counted. A departure during an abort belongs to gs_tick's step 3,
     * which is the code that knows how to book it.
     *
     * The window on the device is narrow but real: gen_inhibit.c runs
     * sched_advance() at :1234 and this function at :1257, so an abort command
     * issued in that tick and taking effect between the two status reads -- 6-21
     * us, measured in item 9's table -- arrives here with a frame held
     * mid-abort. It needs a contended bus, which is the condition the scheduler
     * exists for.
     */
#if GI_INSTRUMENT_ABORTWIN
    gs_abortwin_note(s);
#endif
    if (s->held && !s->aborting && !s->hal->outstanding(s->hal->ctx))
    {
        note_left_controller(s, now, rx_backlog);
    }

    /*
     * D1, THE ONE THAT LOST A SKIP AND A TRIP. Purge every inhibit still sitting
     * in its class queue. The first version asked only about
     * `inhibit_outstanding`, which is set at HANDOVER, so an inhibit that had
     * never been handed over was invisible here: it stayed queued and went out
     * AFTER the VCM's next command, with no skip counted and no trip -- exactly
     * the scenario the skip rule was written for, inverted.
     *
     * It gets into that state three ways, all reachable: the abort loop is still
     * freeing a telemetry frame, the 1 ms bound was hit, or the driver refused
     * the frame and it was left queued for a retry. Found by the reviewing
     * session, 2026-09-27.
     */
    uint32_t purged = 0;
    while (s->q[GS_CLASS_INHIBIT].n > 0)
    {
        q_pop(&s->q[GS_CLASS_INHIBIT]);
        s->st.cls[GS_CLASS_INHIBIT].withdrawn++;
        note_skip(s, now, false);       /* never handed over, never on the wire */
        purged++;
    }
    s->st.skip_window = skips_in_window(s, now);

    if (!s->inhibit_outstanding)
    {
        return (purged > 0) ? GS_DEADLINE_SKIPPED : GS_DEADLINE_OK;
    }

    /*
     * Still outstanding at the deadline. The frame is withdrawn either way; what
     * differs is what we can honestly say happened to it.
     */
    const gs_buf_t b = s->hal->buf_state(s->hal->ctx);
    gs_deadline_t verdict;

    /*
     * EVERY PATH HERE IS A SKIP, and note_skip() is called on all of them so trip
     * 7's window sees them. The three differ only in what can honestly be said
     * about where the frame went.
     *
     * Spec trip 7, amended 2026-09-27: a frame that went out late, or may have, is
     * a skip and not an immediate trip. The inverter has already acted on the
     * VCM's command carrying that counter and ignores ours, so the effect is one
     * real command acted on -- the same as a skip -- and the 3-in-1 s window is
     * the safety net if the transmitter is persistently late.
     */
    if (b == GS_BUF_TRANSMITTING)
    {
        /*
         * It may reach the wire late, and the device cannot tell that from the
         * abort succeeding: both end as TX_SUCCESS with msgs_to_tx 0.
         */
        s->st.late_on_wire++;
        verdict = GS_DEADLINE_MAYBE_LATE;
    }
    else if (b == GS_BUF_EMPTY)
    {
        /*
         * THE BUFFER IS EMPTY AND THE FRAME IS STILL OUTSTANDING, so it LEFT the
         * buffer -- and the only two ways out are the wire and an abort, which we
         * had not issued. It went out, late. Scored as a clean skip until
         * 2026-09-27, which said it never reached the wire when the evidence says
         * the opposite.
         */
        s->st.late_on_wire++;
        verdict = GS_DEADLINE_MAYBE_LATE;
    }
    else
    {
        /* AWAITING: the loop will remove it, and it never reaches the wire. */
        verdict = GS_DEADLINE_SKIPPED;
    }
    note_skip(s, now, verdict == GS_DEADLINE_MAYBE_LATE);
    s->st.skip_window = skips_in_window(s, now);

    /* Withdraw it. The loop runs on subsequent ticks; the frame is never
     * requeued, because a skipped frame is never sent (item 5). */
    if (!s->aborting)
    {
        begin_abort(s, now);
    }
    return verdict;
}

bool gs_inhibit_outstanding(const gs_t *s)
{
    return s->inhibit_outstanding;
}

bool gs_skip_trip(const gs_t *s)
{
    /*
     * A GENUINE SLIDING WINDOW: the newest GS_SKIP_TRIP_N skips must span less
     * than a second. The fixed-window version missed three skips inside 0.15 s
     * that straddled its reset.
     *
     * Read from the ring rather than from `skip_window`, so the trip does not
     * depend on gs_tick() having run since the last skip.
     */
    if (s->skip_ts_n < GS_SKIP_TRIP_N)
    {
        return false;
    }
    int64_t newest = s->skip_ts[0];
    for (uint8_t i = 1; i < s->skip_ts_n; i++)
    {
        if (s->skip_ts[i] > newest) { newest = s->skip_ts[i]; }
    }
    /* The GS_SKIP_TRIP_N newest: count how many fall inside the window ending
     * at the newest skip. */
    uint32_t k = 0;
    for (uint8_t i = 0; i < s->skip_ts_n; i++)
    {
        if (newest - s->skip_ts[i] < GS_SKIP_WINDOW_US) { k++; }
    }
    return k >= GS_SKIP_TRIP_N;
}

const gs_stats_t *gs_stats(const gs_t *s)
{
    return &s->st;
}

/* --------------------------------------------------------------- the JSON -- */

static int clamp(int n, int buflen)
{
    if (n < 0)      { return 0; }
    if (n > buflen) { return buflen; }
    return n;
}

int gs_json(const gs_t *s, char *buf, int buflen)
{
    static const char *NAMES[GS_CLASS_N] = { "inhibit", "probe", "telemetry" };
    int n = 0;

    n = clamp(n + snprintf(buf + n, buflen - n,
        "\"sched\":{\"skipped\":%lu,\"skip_window\":%lu,"
        "\"skipped_withdrawn\":%lu,\"skipped_late\":%lu,"
        "\"aborts\":%lu,\"abort_cmds\":%lu,\"abort_bound_hit\":%lu,"
        "\"abort_max_us\":%lu,"
        "\"ontime\":%lu,\"ontime_unverified\":%lu,"
        "\"unverified_max_backlog\":%lu,"
        "\"tx_failed\":%lu,\"tx_refused\":%lu,\"late_on_wire\":%lu,"
        "\"ambiguous_departures\":%lu,\"order_violations\":%lu,"
        "\"cls\":{",
        (unsigned long)s->st.skipped, (unsigned long)s->st.skip_window,
        (unsigned long)s->st.skipped_withdrawn,
        (unsigned long)s->st.skipped_late,
        (unsigned long)s->st.aborts, (unsigned long)s->st.abort_cmds,
        (unsigned long)s->st.abort_bound_hit,
        (unsigned long)s->st.abort_max_us,
        (unsigned long)s->st.ontime, (unsigned long)s->st.ontime_unverified,
        (unsigned long)s->st.unverified_max_backlog,
        (unsigned long)s->st.tx_failed, (unsigned long)s->st.tx_refused,
        (unsigned long)s->st.late_on_wire,
        (unsigned long)s->st.ambiguous_departures,
        (unsigned long)s->st.order_violations), buflen);

    for (int c = 0; c < GS_CLASS_N; c++)
    {
        n = clamp(n + snprintf(buf + n, buflen - n,
            "%s\"%s\":{\"queued\":%lu,\"sent\":%lu,\"aborted\":%lu,"
            "\"handed\":%lu,\"requeued\":%lu,\"dropped\":%lu,"
            "\"refused\":%lu,"
            "\"max_queue_us\":%lu,\"max_hold_us\":%lu,\"depth\":%u,"
            /*
             * SPEC 5.2 ITEM 6's TWO NEW TERMS (2026-09-29), status page only --
             * section 11 keeps them off the diag CAN page, which is why they are
             * here in gs_json() and not in the core's build_diag().
             *
             * `held` IS PER CLASS, so the balance closes inside one object:
             *   queued + carried_in == sent + dropped + withdrawn + depth + held
             * At most one class can read 1, because the controller holds at most
             * one frame -- and which class that is is the "with its class" half
             * of the spec's wording.
             */
            "\"carried_in\":%lu,\"withdrawn\":%lu,\"held\":%u}",
            (c == 0) ? "" : ",", NAMES[c],
            (unsigned long)s->st.cls[c].queued,
            (unsigned long)s->st.cls[c].sent,
            (unsigned long)s->st.cls[c].aborted,
            (unsigned long)s->st.cls[c].handed,
            (unsigned long)s->st.cls[c].requeued,
            (unsigned long)s->st.cls[c].dropped,
            (unsigned long)s->st.cls[c].refused,
            (unsigned long)s->st.cls[c].max_queue_us,
            (unsigned long)s->st.cls[c].max_hold_us,
            (unsigned)s->q[c].n,
            (unsigned long)s->st.cls[c].carried_in,
            (unsigned long)s->st.cls[c].withdrawn,
            (unsigned)((s->held && s->held_class == (gs_class_t)c) ? 1 : 0)),
            buflen);
    }

    /*
     * `}` closes `cls`; `}` closes `sched`. The measurement object, when it
     * exists at all, goes BETWEEN them -- a sibling of `cls`, never a member of
     * it. An earlier revision of this instrument appended it straight after
     * `"cls":{` was opened, which is valid JSON and therefore silent: it put a
     * counter inside the map of transmit classes, where anything iterating the
     * classes meets a record that is not one. (`case3_run_compare.py` iterates
     * the three class names explicitly, so it would not have caught it either.)
     *
     * Done here rather than before `"cls":{` so that the instrument-off build's
     * format literal stays the `"}}"` it always was, and gs_json() gains no call
     * on the shipping path. The whole instrument is then additive inside #if.
     */
#if GI_INSTRUMENT_ABORTWIN
    n = clamp(n + snprintf(buf + n, buflen - n,
        "},\"abortwin\":{\"reached\":%lu,\"free\":%lu}}",
        (unsigned long)s->st.abortwin_reached,
        (unsigned long)s->st.abortwin_free), buflen);
#else
    n = clamp(n + snprintf(buf + n, buflen - n, "}}"), buflen);
#endif
    return (buflen > 0 && n >= buflen) ? buflen - 1 : n;
}
