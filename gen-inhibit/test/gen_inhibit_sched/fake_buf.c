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

    /* the race inside abort_if_awaiting, and a frame that will not finish */
    bool     race_armed;
    int64_t  race_lose_after;
    int64_t  stick_us;

    /* no node acknowledges: the frame is retried, with a gap between retries */
    bool     unacked;
    int64_t  unacked_gap_us;
    bool     in_retry_gap;
    int      abort_reads;

    /* the re-read past the bound: the HAL's busy-wait and clock */
    int      spins;
    int64_t  spin_us_total;
    int      clock_reads;
    int64_t  last_cmd_at;       /* -1: no command yet */
    int64_t  gap_began_at;      /* the last retry gap's start; -1: none */

    /* the driver's count falls this long after the buffer frees */
    int64_t  count_lag_us;
    int64_t  counted_until;     /* outstanding() reads true before this */

    fb_frame_t wire[FB_MAX];
    int        wire_n;
    fb_frame_t sub[FB_MAX];
    int        sub_n;

    int abort_cmds;
    int aborts_no_effect;
    int abort_cmds_while_tx;

    /* the receive queue's count, and a frame that arrives at the next free read */
    uint32_t rx_waiting;
    uint32_t arrive_on_free;
    int      rx_reads;
} g;

void fb_reset(void)
{
    memset(&g, 0, sizeof(g));
    g.last_cmd_at = -1;
    g.gap_began_at = -1;
}

int64_t fb_now(void) { return g.now; }

void fb_set_contended(bool c) { g.contended = c; }

void fb_abort_no_effect_next(int64_t lose_after_us)
{
    g.noeffect_armed = true;
    g.noeffect_lose_after = lose_after_us;
}

void fb_refuse_next(int n) { g.refuse = n; }

void fb_race_next(int64_t lose_after_us)
{
    g.race_armed = true;
    g.race_lose_after = lose_after_us;
}

void fb_stick_transmitting(int64_t us) { g.stick_us = us; }

void fb_set_unacked(bool on, int64_t gap_us)
{
    g.unacked = on;
    g.unacked_gap_us = gap_us;
}

int fb_abort_reads(void) { return g.abort_reads; }

void fb_set_count_lag(int64_t us) { g.count_lag_us = us; }

/* The buffer has just freed: the driver's count stays up for the lag. */
static void fb_freed(void)
{
    g.occupied = false;
    g.transmitting = false;
    g.counted_until = g.now + g.count_lag_us;
}

/* How long the current frame will transmit for. */
static int64_t fb_air(void)
{
    return (g.stick_us > 0) ? g.stick_us : FB_AIR_US;
}

/* When an awaiting frame starts transmitting: after the gap between two
 * unacknowledged retries, else at once on an idle bus, else after the
 * arbitration wait on a contended one. */
static int64_t fb_start_at(void)
{
    if (g.in_retry_gap) { return g.t_submitted + g.unacked_gap_us; }
    return g.t_submitted + (g.contended ? FB_ARB_WAIT_US : 1);
}

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
            const int64_t start = fb_start_at();
            if (start < next) { next = start; }
        }
        if (g.occupied && g.transmitting && !g.in_noeffect)
        {
            const int64_t done = g.t_start_tx + fb_air();
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
            fb_freed();
            continue;
        }

        if (g.in_noeffect && g.noeffect_lose_after > 0
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
            && g.now >= fb_start_at())
        {
            g.transmitting = true;
            g.in_retry_gap = false;
            g.t_start_tx = g.now;
            continue;
        }
        if (g.occupied && g.transmitting && g.unacked && g.unacked_gap_us == 0
            && g.now >= g.t_start_tx + fb_air())
        {
            /* NOT ACKNOWLEDGED, ERROR ACTIVE: the retry follows at once and TS
             * never reads 0 (VALIDATION.md 5.2, the C3 measurement). */
            g.t_start_tx = g.now;
            continue;
        }
        if (g.occupied && g.transmitting && g.unacked
            && g.now >= g.t_start_tx + fb_air())
        {
            /* NOT ACKNOWLEDGED: no completion, and the controller retries it
             * after the gap. TS reads 0 meanwhile. */
            g.transmitting = false;
            g.in_retry_gap = true;
            g.t_submitted = g.now;
            g.gap_began_at = g.now;
            continue;
        }
        if (g.occupied && g.transmitting
            && g.now >= g.t_start_tx + fb_air())
        {
            /* IT REACHED THE WIRE. This is the only path that appends to the
             * wire log; an abort never does. */
            if (g.wire_n < FB_MAX) { g.wire[g.wire_n++] = g.f; }
            fb_freed();
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
    g.in_retry_gap = false;
    g.t_submitted = g.now;
    g.f.id = f->id;
    g.f.dlc = f->dlc;
    memcpy(g.f.data, f->data, 8);
    if (g.sub_n < FB_MAX) { g.sub[g.sub_n++] = g.f; }
    return true;
}

/*
 * The HAL's one atomic operation: read the state and, if AWAITING, issue the
 * command. Returns the state it acted on.
 */
static gs_buf_t fb_abort_if_awaiting(void *ctx)
{
    const gs_buf_t seen = (!g.occupied) ? GS_BUF_EMPTY
                        : ((g.transmitting || g.in_noeffect)
                           ? GS_BUF_TRANSMITTING : GS_BUF_AWAITING);
    (void)ctx;
    g.abort_reads++;

    if (seen != GS_BUF_AWAITING)
    {
        /* No command is issued. The control case (EMPTY) raises no alert, and a
         * TRANSMITTING buffer is left alone. */
        return seen;
    }

    /*
     * THE RACE. The read said AWAITING; the controller now enters arbitration
     * before the register write takes effect, so the command lands at TS = 1 and
     * has no effect. That is the outcome the measurement found on 7 of 8
     * contended trials, and the reason item 4 is a loop.
     */
    g.last_cmd_at = g.now;
    if (g.race_armed)
    {
        g.race_armed = false;
        g.transmitting = true;
        g.t_start_tx = g.now;
        g.abort_cmds++;
        g.abort_cmds_while_tx++;
        g.in_noeffect = true;
        g.noeffect_since = g.now;
        g.noeffect_lose_after = g.race_lose_after;
        g.aborts_no_effect++;
        return GS_BUF_AWAITING;     /* what the caller observed */
    }

    g.abort_cmds++;
    /*
     * The abort takes effect, but NOT INSTANTLY: 6-21 us measured. Until then the
     * frame is still there and still outstanding, which is what makes the
     * scheduler's "an aborted frame is not a completed frame" guard reachable.
     */
    if (!g.abort_pending)
    {
        g.abort_pending = true;
        g.abort_free_at = g.now + FB_ABORT_FREE_US;
    }
    return GS_BUF_AWAITING;
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
    if (!g.occupied && g.arrive_on_free > 0)
    {
        /* Frames that arrive just after this read says the buffer is free. */
        g.rx_waiting += g.arrive_on_free;
        g.arrive_on_free = 0;
    }
    /* The buffer, or the driver's count while it lags (fb_set_count_lag()). */
    return g.occupied || g.now < g.counted_until;
}

static uint32_t fb_rx_waiting_read(void *ctx)
{
    (void)ctx;
    g.rx_reads++;
    return g.rx_waiting;
}

/* The re-read past the bound's clock and busy-wait: the model's own time. */
static int64_t fb_now_us(void *ctx)
{
    (void)ctx;
    g.clock_reads++;
    return g.now;
}

static void fb_spin(void *ctx, uint32_t us)
{
    (void)ctx;
    g.spins++;
    g.spin_us_total += us;
    fb_advance((int64_t)us);
}

static const gs_hal_t HAL = {
    .submit = fb_submit,
    .abort_if_awaiting = fb_abort_if_awaiting,
    .buf_state = fb_state,
    .outstanding = fb_outstanding,
    .rx_waiting = fb_rx_waiting_read,
    .now_us = fb_now_us,
    .spin_us = fb_spin,
    .ctx = NULL,
};

int     fb_spins(void) { return g.spins; }
int64_t fb_spin_us_total(void) { return g.spin_us_total; }
int     fb_clock_reads(void) { return g.clock_reads; }
int64_t fb_last_cmd_at(void) { return g.last_cmd_at; }
int64_t fb_gap_began_at(void) { return g.gap_began_at; }

void fb_set_rx_waiting(uint32_t n) { g.rx_waiting = n; }
void fb_arrive_on_free(uint32_t n) { g.arrive_on_free = n; }
uint32_t fb_rx_waiting(void) { return g.rx_waiting; }
int fb_rx_reads(void) { return g.rx_reads; }

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
bool fb_counted_now(void) { return g.occupied || g.now < g.counted_until; }
gs_buf_t fb_state_now(void) { return fb_state(NULL); }
