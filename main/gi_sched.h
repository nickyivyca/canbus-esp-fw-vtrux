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
#define GS_Q_PROBE      2
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
    uint32_t sent;          /* handed over AND seen to leave the controller */
    uint32_t aborted;
    uint32_t requeued;
    uint32_t dropped;       /* queue full */
    /*
     * TWO WAITS, because they answer different questions and one field could
     * only answer neither. `max_queue_us` is the spec's "longest wait" -- from
     * queued to handed to the controller. `max_hold_us` is how long the
     * CONTROLLER held it, which is the quantity item 4's one-frame guarantee is
     * about. D5 of the 2026-09-27 design review.
     */
    uint32_t max_queue_us;
    uint32_t max_hold_us;
} gs_class_stats_t;

typedef struct
{
    /*
     * SKIPPED INHIBIT FRAMES (5.2 item 5). Withdrawn because they had not
     * reached the wire by the VCM's next 0x051. Never sent, always counted.
     * `skip_window` is the count inside the current 1 s trip-7 window.
     */
    uint32_t skipped;
    uint32_t skip_window;   /* skips inside the last GS_SKIP_WINDOW_US */

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
     * Aborts, and how hard they were (item 4's loop). `abort_cmds` counts
     * commands issued, so abort_cmds > aborts means the loop had to go round.
     * `abort_bound_hit` is the case the spec says leaves the inhibit at the
     * head of its class and keeps trying.
     */
    uint32_t aborts;
    uint32_t abort_cmds;
    uint32_t abort_bound_hit;
    uint32_t abort_max_us;

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
    GS_DEADLINE_MAYBE_LATE, /* withdrawn while TRANSMITTING -- see below */
} gs_deadline_t;

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

#endif /* GI_SCHED_H */
