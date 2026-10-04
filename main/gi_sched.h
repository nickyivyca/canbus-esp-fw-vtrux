/*
 * gi_sched -- the one transmit owner. Spec 5.2.
 *
 * PLATFORM-FREE, by item 8: this file and gi_sched.c compile unchanged on the
 * host against a virtual clock and a fake buffer. Everything that decides WHAT
 * goes next and WHEN lives here; only the calls into the TWAI driver and the
 * abort register live on the device side, behind gs_hal_t below.
 *
 * WHY A SCHEDULER AT ALL. The TWAI controller has ONE transmit buffer, fed
 * first-in-first-out by the driver, so a lowest-priority frame sitting in it
 * holds back a highest-priority frame queued behind it. On 2026-09-27 a 300 s
 * full-bus replay put one inhibit frame on the wire AFTER the VCM's next
 * command, with our diag page 0x7F3 going out 0.17 ms ahead of it having waited
 * ~10.5 ms for arbitration. The bus would have let the inhibit win; the
 * controller never offered it. Placement alone cannot fix that (5.1 item 4 is
 * amended to say so) -- preemption can.
 *
 * ------------------------------------------------------------------------
 * THE HARDWARE FACTS THIS IS BUILT ON, measured before being modelled (item 9,
 * run file artifacts/gen-inhibit/runs/txabort_full.json, firmware f6eb9ec):
 *
 *   - abort a frame AWAITING arbitration: removed, 162/162, never on the wire,
 *     buffer free in 6-21 us;
 *   - abort while TRANSMITTING on an idle bus: a no-op, the frame completes;
 *   - abort while TS = 1 UNDER CONTENTION: 7 of 8 had NO EFFECT -- the frame
 *     stayed in the buffer contending, with ARB_LOST and no completion event at
 *     all. THIS is why item 4 is a loop and not one abort;
 *   - the loop (re-abort while TS = 0, wait while TS = 1): 78 of 78 freed, at
 *     most 2 abort commands, 5/6/36 us from the first abort;
 *   - an aborted frame is reported as a SUCCESSFUL TRANSMISSION -- TX_IDLE plus
 *     TX_SUCCESS, msgs_to_tx falling to 0 -- in 169 of 169 cases where it never
 *     reached the wire.
 *
 * That last one is why the scheduler keeps its OWN record of what it handed
 * over (`held_class`, `held_seq`, `held_f`) and credits a completion from that
 * rather than from the driver. Nothing here trusts a completion by itself.
 * ------------------------------------------------------------------------
 */
#ifndef GI_SCHED_H
#define GI_SCHED_H

#include <stdbool.h>
#include <stdint.h>

#include "gen_inhibit_core.h"

/* ------------------------------------------------------- the narrow HAL --- */

/*
 * The controller's transmit buffer, as item 8 names it. Three states and not
 * two, because the difference between them is the difference between an abort
 * that works and an abort that does nothing.
 */
typedef enum
{
    GS_BUF_EMPTY = 0,       /* TBS set: nothing to abort */
    GS_BUF_AWAITING,        /* TBS clear, TS clear: abort removes it */
    GS_BUF_TRANSMITTING,    /* TBS clear, TS set: INCLUDES the arbitration
                             * field, so this does NOT mean the frame is
                             * certain to reach the wire, and an abort here may
                             * do nothing at all */
} gs_buf_t;

typedef struct
{
    /* Hand exactly one frame to the controller. false = refused. */
    bool     (*submit)(void *ctx, const gi_frame_t *f);

    /*
     * READ THE BUFFER STATE AND, IF IT IS AWAITING, ISSUE THE ABORT -- AS ONE
     * INDIVISIBLE STEP. Returns the state it found.
     *
     * WHY THIS IS ONE OPERATION AND NOT TWO. An earlier version had a separate
     * abort() and let the caller decide from buf_state(). That is unsafe on
     * hardware: the controller is independent and does not stop between the two
     * calls, so it can enter arbitration after the read and before the write, and
     * the command then lands at TS = 1 -- the measured no-effect case. On the
     * device this is the status read and the register write inside one critical
     * section, which is exactly what the item 9 probe did and for the same reason.
     *
     * It NARROWS the race; it cannot close it, because the controller keeps
     * running while our few cycles execute. The residual is the no-effect
     * outcome, and item 4's loop is what recovers from it. Raised by the
     * reviewing session, 2026-09-27, correcting my claim that the scheduler could
     * not reach that outcome at all.
     */
    gs_buf_t (*abort_if_awaiting)(void *ctx);

    /* A plain read, for decisions that do not issue a command. */
    gs_buf_t (*buf_state)(void *ctx);

    /*
     * Is anything of ours still in the controller? On the device this is
     * msgs_to_tx != 0, which is attributable BY CONSTRUCTION rather than by an
     * invariant someone maintains -- the driver queues FIFO and the scheduler
     * hands over one frame at a time, so zero means our frame is done with.
     *
     * It does NOT say whether the frame reached the wire. Nothing available to
     * the device says that; see the deadline discussion below.
     */
    bool     (*outstanding)(void *ctx);

    void *ctx;
} gs_hal_t;

/* ----------------------------------------------------------- the classes -- */

typedef enum
{
    GS_CLASS_INHIBIT = 0,   /* 0x051, highest */
    GS_CLASS_PROBE,         /* 0x7F0 RESPOND probe */
    GS_CLASS_TELEMETRY,     /* diag pages, and any later logging */
    GS_CLASS_N,
} gs_class_t;

/*
 * TELEMETRY IS A CLASS, NOT A LIST OF PAGES. The user's constraint: "even if we
 * do not have it yet now, we may in the future want to have functionality
 * logged during inhibit. So we should structure such that this is easier." A
 * later logging feature enqueues into GS_CLASS_TELEMETRY and inherits every
 * guarantee here without touching the inhibit path.
 */

#define GS_Q_INHIBIT    2   /* one live, one arriving: a third would mean the
                             * deadline logic failed, and a bigger queue would
                             * hide that instead of reporting it */
#define GS_Q_PROBE      1   /* ONE. A probe measures one 0x051, so a second
                             * queued behind it is stale by definition -- and a
                             * queue of 2 let the shim's single "last probe"
                             * record carry the wrong frame's identity. A drop is
                             * counted, which is the honest report. */
#define GS_Q_TELEMETRY  6   /* 5.2 item 6 requires drops to be COUNTED, so the
                             * depth is a tuning choice and not a guarantee */

/*
 * Skip timestamps kept for trip 7's sliding window. Larger than GS_SKIP_TRIP_N
 * so `skip_window` can report the true count in the last second rather than
 * saturating at the trip threshold -- the status page is meant to show how close
 * a run came, not just whether it crossed.
 */
#define GS_SKIP_RING    8

/* --------------------------------------------------------------- counters -- */

typedef struct
{
    uint32_t queued;
    /*
     * HANDED TO THE DRIVER. Distinct from `queued` (accepted into the class queue)
     * and from `sent` (seen to leave the controller), and all three are needed: the
     * RESPOND probe is counted at handover so its figure stays comparable with the
     * pre-scheduler record, where queueing and handing over were the same call.
     */
    uint32_t handed;
    uint32_t sent;          /* handed over AND seen to leave the controller */
    uint32_t aborted;
    uint32_t requeued;
    uint32_t dropped;       /* queue full */
    /*
     * The driver would not take the frame at handover. PER CLASS, because the
     * consequence differs: a refused INHIBIT is spec 7 trip 7's
     * GI_ABORT_TX_NOT_QUEUED and trips at once, while a refused telemetry page
     * stays queued and is retried. A single global count could not tell the
     * caller which had happened.
     */
    uint32_t refused;
    /*
     * TWO WAITS, because they answer different questions and one field could
     * only answer neither. `max_queue_us` is the spec's "longest wait" -- from
     * queued to handed to the controller. `max_hold_us` is how long the
     * CONTROLLER held it, which is the quantity item 4's one-frame guarantee is
     * about. D5 of the 2026-09-27 design review.
     */
    uint32_t max_queue_us;
    uint32_t max_hold_us;
    /*
     * FRAMES THIS ARM DID NOT QUEUE BUT MAY STILL SEE LEAVE. Spec 5.2 item 6 as
     * amended 2026-09-29.
     *
     * gs_rearm() deliberately keeps whatever the controller is holding -- the
     * hardware may really still have it, and forgetting it is the priority
     * inversion item 3 exists to prevent. The per-arm counters are cleared, so
     * without this term that frame's completion lands in the new arm as a `sent`
     * with nothing queued against it, and the balance reads one over. Measured on
     * 3 bench arms, +1 each.
     *
     * It is 0 or 1 in practice: only one frame can be in the controller. A
     * counter and not a flag because a frame carried across two re-arms in a row
     * is carried in twice, and a flag would report the second as the first.
     */
    uint32_t carried_in;
    /*
     * FRAMES GIVEN UP AT THE DEADLINE AND NEVER TO BE SENT (spec 5.2 item 6,
     * 2026-09-29). Only the inhibit class can have any: item 5 forbids sending a
     * skipped inhibit later, so the frame leaves the accounting without ever
     * reaching `sent`, and without this term `queued` moves and nothing on the
     * right does.
     *
     * IT INCLUDES A FRAME THAT MAY HAVE REACHED THE WIRE LATE, which is the part
     * that reads wrong at first glance. At the deadline every outstanding inhibit
     * is handed to the abort loop -- the MAYBE_LATE verdict too -- and from then
     * on the `!aborting` guard in gs_tick() step 1 keeps the departure out of
     * note_left_controller(), so step 3 books it as aborted whether the abort
     * removed it or the controller finished it. "Withdrawn" here means withdrawn
     * from OUR accounting, not proven off the wire; the device cannot prove the
     * latter, which is what `skipped_late` and `late_on_wire` exist to say.
     *
     * So: cls[INHIBIT].withdrawn + cls[INHIBIT].dropped == skipped, once any
     * in-progress abort has completed. While one is still running the frame is
     * counted in `held` instead, which is what keeps the identity closed.
     */
    uint32_t withdrawn;
} gs_class_stats_t;

typedef struct
{
    /*
     * SKIPPED INHIBIT FRAMES (5.2 item 5). Withdrawn because they had not
     * reached the wire by the VCM's next 0x051. Never sent, always counted.
     * `skip_window` is the count inside the current 1 s trip-7 window.
     */
    /*
     * `skipped` IS THE TOTAL OF EVERY SKIP, and `late_on_wire` below is a SUBSET
     * of it, not an addition. Never sum them. Trip 7's window counts every skip,
     * because a frame that went out late and a frame that never went have the
     * same effect -- the inverter acted on one real VCM command either way -- and
     * a persistently late transmitter has to reach the 3-in-1 s window.
     */
    uint32_t skipped;
    uint32_t skip_window;   /* skips inside the last GS_SKIP_WINDOW_US */
    /*
     * THE SAME TOTAL, BROKEN DOWN BY KIND, and the breakdown is what the caller
     * reports to the core -- one call per skip, with its kind. The first version
     * had the worker call gi_on_inhibit_skip() once per DEADLINE, so a purge that
     * withdrew two queued frames counted two skips here and reported one there,
     * and the two numbers disagreed with nothing saying why. Reported through
     * counters like everything else the caller derives, so they cannot.
     *
     * skipped == skipped_withdrawn + skipped_late, always.
     */
    uint32_t skipped_withdrawn; /* removed at the deadline, never on the wire */
    uint32_t skipped_late;      /* went out late, or may have (late_on_wire) */

    /*
     * D6. A telemetry frame that COMPLETES GENUINELY while an abort is in
     * progress -- the measured no-op at TS = 1 -- is indistinguishable from one
     * the abort removed, because the driver reports both as TX_SUCCESS with
     * msgs_to_tx falling to 0. The scheduler requeues it, so the page goes out
     * twice. Harmless for a diag page and NOT harmless for the statistics, so
     * this counts every departure whose cause could not be determined:
     * `aborted` and `requeued` may each include up to this many double-sends.
     * A number that states what it does not know beats one that implies
     * precision it lacks.
     */
    uint32_t ambiguous_departures;

    /*
     * THE INTEGRATION-ORDER INVARIANT, and the only thing that pins an ordering
     * this file cannot see.
     *
     * Spec item 5 requires the deadline (gs_command_received) to be evaluated
     * when the VCM's next 0x051 is dequeued, BEFORE the answer to that command is
     * queued. gs_command_received() sets a DEADLINE TOKEN;
     * gs_queue_frame(GS_CLASS_INHIBIT) requires it, counts a violation if it is
     * absent, and clears it. One command, one deadline, one inhibit.
     *
     * AN EARLIER VERSION TESTED `inhibit_outstanding` INSTEAD AND DID NOT FIRE IN
     * THE COMMON CASE. It caught the ordering only when the PREVIOUS inhibit was
     * still outstanding; normally that one completed milliseconds earlier, so with
     * the calls swapped nothing was outstanding, no violation was counted, and the
     * deadline purge then withdrew the NEW inhibit as a skip on every command.
     * Trip 7 would have fired after three -- loud, but not through the counter
     * named as the pin, so the pin was not one. Caught by the reviewing session,
     * 2026-09-27.
     *
     * Counted and not refused: refusing would turn a caller-ordering bug into a
     * missed inhibit on the truck, which is worse than sending the frame and
     * saying loudly that the order was wrong. A mock-HAL case asserts this stays
     * 0, and that is what a mutation of the call order in gen_inhibit.c moves.
     */
    uint32_t order_violations;

    /*
     * Aborts, and how hard they were (item 4's loop). `abort_cmds` counts
     * commands issued, so abort_cmds > aborts means the loop had to go round.
     * `abort_bound_hit` is the case the spec says leaves the inhibit at the
     * head of its class and keeps trying.
     */
    uint32_t aborts;
    uint32_t abort_cmds;
    uint32_t abort_bound_hit;
    uint32_t abort_max_us;

#if GI_INSTRUMENT_ABORTWIN
    /*
     * MEASUREMENT ONLY -- how often the device reaches the window the pre-fix
     * `note_left_controller()` defect needed. `reached` is the precondition
     * (`held && aborting` at the gs_command_received call site); `free` adds
     * the live `outstanding()` test, which is the exact condition the pre-fix
     * code acted on. See gs_abortwin_note() for why the split matters and why
     * the shipping build cannot observe this without being asked to.
     */
    uint32_t abortwin_reached;
    uint32_t abortwin_free;
#endif

    /*
     * ON-TIME CLAIMS THAT CANNOT BE VERIFIED, and this counter is the whole
     * point of the honesty in the deadline note below. An inhibit's completion
     * is judged against the moment the worker DEQUEUES the VCM's next 0x051,
     * which is later than that frame's arrival by the receive backlog. A
     * completion observed while frames were already waiting in the RX queue
     * therefore proves nothing about ordering on the wire.
     *
     * A completion observed with an EMPTY RX queue is a genuine proof: the
     * VCM's next command had not arrived, because if it had it would be in the
     * queue. That is exact only up to the ISR latency between a frame reaching
     * the controller's hardware FIFO and the ISR moving it into the software
     * queue -- microseconds, and worth stating rather than glossing.
     *
     * So `ontime` counts verified on-time completions and `ontime_unverified`
     * counts the rest. Reported separately on the status page, never summed,
     * because a total would be a number whose failure looks like success.
     */
    uint32_t ontime;
    uint32_t ontime_unverified;
    uint32_t unverified_max_backlog;

    /* Trip causes, reported rather than acted on: the core owns the trips. */
    uint32_t tx_failed;     /* the controller reported a failure */
    uint32_t tx_refused;    /* the driver would not take the frame */
    /*
     * A SUBSET OF `skipped`: the frame went out late, or may have. Spec trip 7 as
     * amended 2026-09-27 makes this a skip rather than an immediate trip, because
     * the inverter has already acted on the VCM's command with that counter and
     * ignores ours. Counted separately so the status page shows which kind of skip
     * it was.
     */
    uint32_t late_on_wire;  /* completion observed AFTER the next 0x051 was
                             * dequeued: unambiguously late, trips at once */

    gs_class_stats_t cls[GS_CLASS_N];
} gs_stats_t;

/* ------------------------------------------------------------- the state -- */

typedef struct
{
    gi_frame_t f;
    int64_t    t_queued;
    uint32_t   seq;         /* identity, so a completion can be attributed */
    /*
     * THIS FRAME'S WAIT STARTED IN AN EARLIER ARM, so no wait figure in this arm
     * may be computed from `t_queued`. Set when a carried-in frame is aborted and
     * requeued: the requeue puts the OLD arm's timestamp back into the slot
     * (there is nowhere else to get one from -- the frame really has been waiting
     * that long), and the next handover would otherwise book an arm-length
     * max_queue_us. Same argument the spec makes for max_hold_us: it would be a
     * measurement the device did not make.
     */
    bool       carried;
} gs_slot_t;

typedef struct
{
    gs_slot_t q[GS_Q_TELEMETRY > GS_Q_INHIBIT ? GS_Q_TELEMETRY : GS_Q_INHIBIT];
    uint8_t   head;
    uint8_t   n;
    uint8_t   cap;
} gs_queue_t;

typedef struct
{
    const gs_hal_t *hal;

    gs_queue_t q[GS_CLASS_N];
    uint32_t   next_seq;

    /* What is in the controller right now, if anything. */
    bool       held;
    gs_class_t held_class;
    uint32_t   held_seq;
    int64_t    held_since;
    /*
     * A COPY of the frame handed over, not a pointer into the queue. It has been
     * popped, so a pointer would dangle -- and an aborted frame has to be
     * requeued from somewhere. This is also the record item 5 requires: the
     * scheduler knows which frame it aborted, because the driver cannot tell it.
     */
    gi_frame_t held_f;
    /*
     * THE HELD FRAME WAS HANDED OVER IN AN EARLIER ARM (spec 5.2 item 6, 2026-09-29).
     * Its hold began before this arm's counters existed, so its completion is
     * counted -- in `sent`, balanced by `carried_in` -- but contributes to no
     * duration. Booking it into max_hold_us produced 142-310 s figures on 90 s
     * arms, which is what put the whole identity in doubt.
     */
    bool       held_carried;

    /*
     * THE ABORT LOOP's state, kept across ticks so the loop does not block the
     * worker. Item 4: re-abort while the buffer is AWAITING, wait while it is
     * TRANSMITTING, bounded at 1 ms; on the bound the inhibit stays at the head
     * of its class and we keep trying.
     */
    bool       aborting;
    int64_t    abort_since;
    uint32_t   abort_cmds_this;

    /* The inhibit's deadline is the VCM's next 0x051 (item 5). */
    bool       inhibit_outstanding;
    uint32_t   inhibit_seq;
    int64_t    inhibit_handed;

    /*
     * THE DEADLINE TOKEN. Set by gs_command_received(), consumed by queueing an
     * inhibit. One bit, because the property is one bit: "has a deadline been
     * evaluated since the last inhibit was queued". A counter would invite
     * reasoning about how far apart the two calls are, which is not the question.
     */
    bool       deadline_token;

    /*
     * TRIP 7'S SLIDING WINDOW. A ring of recent skip timestamps, because "3
     * within 1 s" is a sliding window and the first implementation made it a
     * fixed one: it started at the first skip and reset a second later, so three
     * skips inside 0.15 s that straddled the reset never tripped. D2 of the
     * 2026-09-27 review.
     *
     * There is no sentinel. The old code used `start == 0` for "not started",
     * which is a legal timestamp on a virtual clock that begins at 0 -- the same
     * trap gen_inhibit_core.h records for due_us, in a file that had just
     * quoted it.
     */
    int64_t    skip_ts[GS_SKIP_RING];
    uint8_t    skip_ts_n;   /* valid entries, saturating at GS_SKIP_RING */
    uint8_t    skip_ts_head;

    gs_stats_t st;
} gs_t;

/* --------------------------------------------------------------- the API -- */

void gs_init(gs_t *s, const gs_hal_t *hal);

/*
 * Reset the counters and drop every queue, as an arm does. Does NOT touch the
 * controller: the caller owns bus bring-up and teardown.
 */
void gs_reset(gs_t *s);

/*
 * Re-arm: clear the queues and the counters, KEEP the hardware-facing state.
 *
 * Use this on a mode change, never gs_init(). gs_init() memsets everything
 * including `held`, `held_f` and `aborting`, and the controller may still be
 * holding a frame from the previous arm -- a diag page awaiting arbitration, or an
 * inhibit on an INHIBIT->INHIBIT re-arm. A scheduler that has forgotten what the
 * hardware holds will hand over the next frame, twai_transmit will queue it BEHIND
 * the old one in the driver's FIFO, and that is the priority inversion item 3
 * exists to prevent -- with the old frame's departure credited to the new one.
 *
 * THE HAL IS A PARAMETER AND NOT PRESERVED FROM THE STRUCT. The first version took
 * only `s` and kept the existing pointer, which is NULL on a statically-allocated
 * scheduler that has never been gs_init()ed -- so the very first arm produced a
 * scheduler with no HAL and segfaulted on the first submit. Passing it makes that
 * state unreachable instead of relying on an init call nobody can see from here.
 */
void gs_rearm(gs_t *s, const gs_hal_t *hal);

/*
 * Withdraw every inhibit: purge the class queue and start the abort loop on a held
 * one. Each purged frame is counted as a skip.
 *
 * CALL THIS WHENEVER THE CORE STOPS BEING LIVE -- any abort, any disable, any mode
 * change out of INHIBIT. Without it a refused inhibit stays queued and is retried
 * every tick: the core latches off on the refusal, the retry then succeeds, and an
 * inhibit frame reaches the wire AFTER the trip that was supposed to stop us
 * transmitting.
 */
void gs_withdraw_inhibits(gs_t *s, int64_t now);

/*
 * Enqueue one frame. false = the class queue was full and the frame was
 * DROPPED, which is counted (item 6: telemetry is never lost silently).
 */
bool gs_queue_frame(gs_t *s, gs_class_t cls, const gi_frame_t *f, int64_t now);

/*
 * Drive the scheduler. Call on every worker iteration, not at one point: a
 * completion is then observed whenever it happens rather than only inside a
 * window, and the reactive path carries no blocking call. `rx_backlog` is
 * msgs_to_rx, used only to decide whether an on-time claim is verifiable.
 */
void gs_tick(gs_t *s, int64_t now, uint32_t rx_backlog);

/*
 * What the deadline found. Three values and not a bool, because "skipped" and
 * "may have reached the wire late" feed DIFFERENT trips -- 3-within-1s for the
 * first, immediately for the second -- and a bool would have to pick one.
 */
typedef enum
{
    GS_DEADLINE_OK = 0,     /* the inhibit had already left the controller */
    GS_DEADLINE_SKIPPED,    /* withdrawn while awaiting: never on the wire */
    GS_DEADLINE_MAYBE_LATE, /* it went out late, or may have -- see below */
} gs_deadline_t;

/*
 * BOTH NON-OK VERDICTS ARE SKIPS and both move trip 7's window (spec trip 7,
 * amended 2026-09-27). They are distinct only so the caller and the status page
 * can say WHICH kind: never sent, or sent late. Neither trips on its own; only
 * TX_FAILED and a driver refusal still do.
 */

/*
 * The worker dequeued a VCM 0x051. This is the inhibit's DEADLINE (item 5).
 *
 * GS_DEADLINE_MAYBE_LATE IS DELIBERATELY PESSIMISTIC, and this is the one place
 * the measurement forces a judgement call. If the buffer is TRANSMITTING at the
 * deadline, the frame may complete on the wire (late) or the item 4 loop may
 * remove it -- and the device CANNOT TELL THOSE APART, because the driver
 * reports an aborted frame and a real completion identically, as TX_SUCCESS
 * with msgs_to_tx falling to 0 (169 of 169 measured). Spec 7 trip 7 says a frame
 * reaching the wire after the VCM's next command trips at once, so the safe
 * reading of an ambiguous case is the trip: under-reporting a late frame would
 * let a silently ineffective inhibit look healthy, which is the worst failure
 * this firmware has. The bench witness is what resolves it for real.
 *
 * `rx_backlog` is msgs_to_rx at this instant.
 *
 * WHY THIS IS THE DEQUEUE AND NOT THE ARRIVAL, stated here because it is the
 * limit of what the device can know. twai_message_t carries no timestamp in the
 * public API, and msgs_to_rx > 0 does not mean a 0x051 has arrived -- the
 * acceptance filter is accept-all, so the backlog holds every ID. There is no
 * way to learn the true arrival instant, so the deadline is evaluated here, and
 * the gap between arrival and dequeue is exposure that only the bench witness
 * can measure. It is NOT bounded by one frame time: a frame handed over earlier
 * can complete in that gap, physically late, and the device would see its
 * completion first and score it on time. `ontime_unverified` is what keeps that
 * honest. Corrected 2026-09-27 after the reviewing session showed the
 * one-frame-time bound was wrong.
 */
gs_deadline_t gs_command_received(gs_t *s, int64_t now,
                                 uint32_t rx_backlog);

/* Is an inhibit frame outstanding? Section 10: no telemetry goes out while one
 * is, which item 7 makes a property of the scheduler rather than a rule. */
bool gs_inhibit_outstanding(const gs_t *s);

/* Trip 7: 3 skips within 1 s. The window is advanced by gs_tick(). */
bool gs_skip_trip(const gs_t *s);

const gs_stats_t *gs_stats(const gs_t *s);

/* Rendered by the caller; the scheduler has no printf. */
int gs_json(const gs_t *s, char *buf, int buflen);

#if GI_INSTRUMENT_ABORTWIN
/* Measurement build only. See the definition in gi_sched.c. */
void gs_abortwin_note(gs_t *s);
#endif

#endif /* GI_SCHED_H */
