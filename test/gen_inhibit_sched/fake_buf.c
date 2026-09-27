/*
 * fake_buf -- see fake_buf.h for what every number here is calibrated against.
 *
 * The model is a small state machine advanced only by fb_advance(), so two runs
 * of the same script produce the same trace. No threads, no real clock.
 */

#include <string.h>

#include "fake_buf.h"

/* From the noabort phase: 242 / 249 / 253 us. The median is used, because a
 * model that picked the max would make every timing margin look tighter than it
 * is and a model that picked the min would make it look looser. */
#define FB_AIR_US           249

/* Buffer free after an abort of an awaiting frame: measured 6-21 us. */
#define FB_ABORT_FREE_US    7

/* On a contended bus a submitted frame does not start at once. This is the
 * arbitration wait, and it is a STRESS figure: on the saturated bench bus our
 * lowest-priority ID essentially never won. */
#define FB_ARB_WAIT_US      400

#define FB_MAX 64

typedef struct
{
    uint32_t id;
    uint8_t  data[8];
    uint8_t  dlc;
} fb_frame_t;

static struct
{
    int64_t  now;
    bool     contended;

    /* the buffer */
    bool     occupied;
    bool     transmitting;      /* TS: includes the arbitration field */
    int64_t  t_submitted;
    int64_t  t_start_tx;        /* when it began transmitting, if it has */
    fb_frame_t f;

    /*
     * An abort in flight. The hardware takes 6-21 us to free the buffer, and the
     * first version of this model did it instantly -- which made the scheduler's
     * "do not credit an aborted frame as sent" guard unreachable, because the
     * departure was never visible on a later tick. The mutation harness caught
     * that the guard could be deleted with no test noticing.
     */
    bool     abort_pending;
    int64_t  abort_free_at;

    /* a pending "the abort had no effect" episode */
    bool     noeffect_armed;
    int64_t  noeffect_lose_after;
    int64_t  noeffect_since;
    bool     in_noeffect;

    int      refuse;

    fb_frame_t wire[FB_MAX];
    int        wire_n;
    fb_frame_t sub[FB_MAX];
    int        sub_n;

    int abort_cmds;
    int aborts_no_effect;
    int abort_cmds_while_tx;
} g;

void fb_reset(void)
{
    memset(&g, 0, sizeof(g));
}

int64_t fb_now(void) { return g.now; }

void fb_set_contended(bool c) { g.contended = c; }

void fb_abort_no_effect_next(int64_t lose_after_us)
{
    g.noeffect_armed = true;
    g.noeffect_lose_after = lose_after_us;
}

void fb_refuse_next(int n) { g.refuse = n; }

/*
 * Advance virtual time, stepping the buffer. Done in one-microsecond-resolution
 * jumps to the next interesting instant rather than by looping, so a long
 * advance is cheap and the transitions land exactly where the calibration says.
 */
void fb_advance(int64_t us)
{
    const int64_t target = g.now + us;

    while (g.now < target)
    {
        int64_t next = target;

        if (g.occupied && !g.transmitting && !g.in_noeffect && !g.abort_pending)
        {
            /* waiting to start: immediately on an idle bus, after the
             * arbitration wait on a contended one */
            const int64_t start = g.t_submitted
                                + (g.contended ? FB_ARB_WAIT_US : 1);
            if (start < next) { next = start; }
        }
        if (g.occupied && g.transmitting && !g.in_noeffect)
        {
            const int64_t done = g.t_start_tx + FB_AIR_US;
            if (done < next) { next = done; }
        }
        if (g.in_noeffect)
        {
            const int64_t lose = g.noeffect_since + g.noeffect_lose_after;
            if (lose < next) { next = lose; }
        }
        if (g.abort_pending && g.abort_free_at < next)
        {
            next = g.abort_free_at;
        }

        g.now = next;

        if (g.abort_pending && g.now >= g.abort_free_at)
        {
            /*
             * The buffer frees. THE WIRE LOG IS NOT TOUCHED: the frame never
             * reached the bus, and the only thing the device can see is that
             * `outstanding` went false -- indistinguishable from a real
             * completion, which is the whole point of spec 5.2 item 5.
             */
            g.abort_pending = false;
            g.occupied = false;
            g.transmitting = false;
            continue;
        }

        if (g.in_noeffect
            && g.now >= g.noeffect_since + g.noeffect_lose_after)
        {
            /*
             * The frame lost arbitration. TS falls to 0 and it goes back to
             * AWAITING -- which is the window where an abort works, and the
             * reason item 4 waits instead of re-issuing the command.
             */
            g.in_noeffect = false;
            g.transmitting = false;
            g.t_submitted = g.now;
            continue;
        }
        if (g.occupied && !g.transmitting && !g.abort_pending
            && g.now >= g.t_submitted + (g.contended ? FB_ARB_WAIT_US : 1))
        {
            g.transmitting = true;
            g.t_start_tx = g.now;
            continue;
        }
        if (g.occupied && g.transmitting
            && g.now >= g.t_start_tx + FB_AIR_US)
        {
            /* IT REACHED THE WIRE. This is the only path that appends to the
             * wire log; an abort never does. */
            if (g.wire_n < FB_MAX) { g.wire[g.wire_n++] = g.f; }
            g.occupied = false;
            g.transmitting = false;
            continue;
        }
    }
}

/* ------------------------------------------------------------ the HAL ----- */

static bool fb_submit(void *ctx, const gi_frame_t *f)
{
    (void)ctx;
    if (g.refuse > 0)
    {
        g.refuse--;
        return false;
    }
    if (g.occupied)
    {
        return false;   /* the scheduler must never do this: item 3 */
    }
    g.occupied = true;
    g.transmitting = false;
    g.t_submitted = g.now;
    g.f.id = f->id;
    g.f.dlc = f->dlc;
    memcpy(g.f.data, f->data, 8);
    if (g.sub_n < FB_MAX) { g.sub[g.sub_n++] = g.f; }
    return true;
}

static void fb_abort(void *ctx)
{
    (void)ctx;
    g.abort_cmds++;

    if (!g.occupied)
    {
        return;         /* the control case: no alert, nothing happens */
    }
    if (g.transmitting)
    {
        g.abort_cmds_while_tx++;
        if (g.noeffect_armed)
        {
            /* 7 of 8 under contention: the command does nothing and the frame
             * keeps contending until it loses. */
            g.noeffect_armed = false;
            g.in_noeffect = true;
            g.noeffect_since = g.now;
            g.aborts_no_effect++;
            return;
        }
        /* The measured no-op on an idle bus: the frame finishes. Time does the
         * rest; nothing changes here. */
        return;
    }
    /*
     * AWAITING: the abort takes effect, but NOT INSTANTLY. The measurement puts
     * the buffer free 6-21 us later, and until then the frame is still there and
     * still outstanding. That latency is what makes the scheduler's
     * "an aborted frame is not a completed frame" guard reachable at all.
     */
    if (!g.abort_pending)
    {
        g.abort_pending = true;
        g.abort_free_at = g.now + FB_ABORT_FREE_US;
    }
}

static gs_buf_t fb_state(void *ctx)
{
    (void)ctx;
    if (!g.occupied)   { return GS_BUF_EMPTY; }
    if (g.transmitting || g.in_noeffect) { return GS_BUF_TRANSMITTING; }
    return GS_BUF_AWAITING;
}

static bool fb_outstanding(void *ctx)
{
    (void)ctx;
    return g.occupied;
}

static const gs_hal_t HAL = {
    .submit = fb_submit,
    .abort = fb_abort,
    .buf_state = fb_state,
    .outstanding = fb_outstanding,
    .ctx = NULL,
};

const gs_hal_t *fb_hal(void) { return &HAL; }

/* -------------------------------------------------------- observations ---- */

int fb_wire_count(void) { return g.wire_n; }
uint32_t fb_wire_id(int i) { return g.wire[i].id; }
const uint8_t *fb_wire_data(int i) { return g.wire[i].data; }
int fb_submit_count(void) { return g.sub_n; }
uint32_t fb_submit_id(int i) { return g.sub[i].id; }
int fb_abort_cmds(void) { return g.abort_cmds; }
int fb_aborts_with_no_effect(void) { return g.aborts_no_effect; }
int fb_abort_cmds_while_transmitting(void) { return g.abort_cmds_while_tx; }
bool fb_outstanding_now(void) { return g.occupied; }
gs_buf_t fb_state_now(void) { return fb_state(NULL); }
