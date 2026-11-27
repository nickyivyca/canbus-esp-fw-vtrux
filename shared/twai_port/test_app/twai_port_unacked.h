/*
 * twai_port_unacked -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T13).
 *
 * The transmit-abort probe's no-acknowledge modes, M1-M3, asked for by
 * gen-inhibit tracker F5 (SPEC.md T13, added 2026-11-27): on a bus where no
 * node acknowledges, the controller resends the frame on its own after each
 * error frame, and these modes measure what the status register's TS bit
 * reads between those attempts, and what the abort command does during an
 * attempt and in the idle between attempts.
 *
 *   m1  the TS trace: send one frame, sample the status register and TEC for
 *       a set time, and record every change
 *   m2  the abort in the gap: send, wait for a chosen attempt (or time), then
 *       issue the abort on the first read with TS = 0
 *   m3  the abort while TS = 1: send, wait for a chosen attempt (or time),
 *       then issue the abort a set delay after TS rises
 *
 * The parameters and the answer are in test_app/README.md. The logic here is
 * chip-neutral: it reads the controller only through twai_port_probe_ll.h,
 * and the driver and the clocks only through the txp_env_*() calls below,
 * which twai_port_unacked_esp.c gives on the target and the host tests give
 * on the host (test_app/test/).
 */
#ifndef TWAI_PORT_UNACKED_H
#define TWAI_PORT_UNACKED_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { TXP_M1 = 1, TXP_M2 = 2, TXP_M3 = 3 };

#define TXP_MAX_REPS        8
#define TXP_EVENTS          2048    /* the change records, shared by a call's trials */
#define TXP_GAP_US          2       /* a gap between two samples longer than this
                                     * plus sample_us is counted and flagged */
#define TXP_CLEANUP_US      20000   /* the end-of-trial cleanup's bound */

typedef struct {
    int  mode;          /* TXP_M1..TXP_M3 */
    char label[32];
    int  reps;          /* trials per call */
    int  settle_ms;     /* the pause between trials */
    bool fresh;         /* reinstall the driver before each trial: TEC 0 */
    bool irq_off;       /* sample with interrupts masked */
    int  sample_us;     /* the sampling period; 0: as fast as it goes */
    int  window_us;     /* m1: the trace; m2, m3: the bound on the wait */
    int  trail_us;      /* keep sampling this long after the buffer frees */
    int  wait_us;       /* m2, m3: -1 when not given */
    int  wait_att;      /* m2, m3: -1 when not given */
    int  bound_us;      /* m2, m3: how long after the abort to wait for TBS */
    int  delay_us;      /* m3: from the TS rise to the abort */
    int  hold_ms;       /* after the trace, before the cleanup: the driver runs,
                         * nothing is aborted or recorded; 0: no hold */
} txp_params_t;

/* The driver's view, from twai_get_status_info(). */
typedef struct {
    uint32_t state;
    uint32_t msgs_to_tx;
    uint32_t tec, rec;
    uint32_t tx_failed, bus_error, arb_lost;
} txp_drv_t;

/* One change of the status bits or the TEC. */
typedef struct {
    uint32_t t;         /* CPU cycles from the send */
    uint16_t st;        /* TXP_ST_* */
    uint8_t  tec;       /* the TEC, capped at 255 */
    uint8_t  gap;       /* the sample before this one was more than
                         * TXP_GAP_US + sample_us earlier, so the change may
                         * have happened anywhere in that gap */
} txp_event_t;

enum {
    TXP_SKIP_NONE = 0,
    TXP_SKIP_REINSTALL,     /* the driver did not come back up */
    TXP_SKIP_BUSY,          /* the buffer or the driver held a frame */
    TXP_SKIP_TX_REFUSED,    /* twai_transmit() refused the frame */
};

enum {
    TXP_CLR_NONE = 0,       /* never freed, cleanup included */
    TXP_CLR_TBS_TCS,        /* freed inside the trace, TCS = 1 */
    TXP_CLR_TBS,            /* freed inside the trace, TCS = 0 */
    TXP_CLR_CLEANUP,        /* freed by the end-of-trial cleanup */
    TXP_CLR_HOLD,           /* freed in the hold: TBS = 1 at its end */
};

typedef struct {
    uint16_t trial;
    uint8_t  skip;
    uint8_t  reinstalled;
    uint32_t st_pre, tec_pre;   /* before the send */
    bool     have_pre;
    txp_drv_t drv_pre;
    uint32_t tx_call_cyc;       /* how long twai_transmit() took */

    /* The trace. Times are CPU cycles from the send; -1: never. */
    uint32_t ev_first, ev_n, ev_lost;
    uint32_t samples, max_gap_cyc, gaps;
    uint32_t attempts;          /* TS rises seen in the trace */
    uint32_t end_cyc;           /* the trace's last sample */
    uint8_t  cleared;           /* TXP_CLR_* */
    int32_t  clear_t;           /* the first sample with TBS = 1 */

    /* m2, m3 */
    uint8_t  wait_timeout;      /* the wait ran past window_us: no abort */
    uint8_t  freed_before;      /* the buffer freed before the abort: none */
    int32_t  met_t;             /* the wait was met */
    int32_t  anchor_t;          /* m3: the TS rise the delay counts from */
    int32_t  abort_t;           /* the abort command's critical section */
    uint8_t  ab_issued;
    uint32_t ab_st_before, ab_st_after, ab_tec, ab_attempt;
    bool     have_mid;          /* alerts and counters read before the abort */
    uint32_t alerts_before;
    txp_drv_t drv_mid;
    uint32_t snap_cyc;          /* that read's length; sampling paused */

    /* The driver after the trace: m1 the window, m2/m3 after the abort. */
    uint32_t alerts_after;
    bool     have_after;
    txp_drv_t drv_after;

    /* The hold (hold_ms), read at its end. */
    uint8_t  held;
    uint32_t hold_cyc;          /* the hold's length */
    uint32_t hold_st, hold_tec;
    uint32_t alerts_hold;
    bool     have_hold;
    txp_drv_t drv_hold;

    /* The end-of-trial cleanup, when the buffer is still full: after the
     * trace, or after the hold when there is one. */
    uint8_t  cleanup;
    int32_t  cleanup_cyc;       /* from its start to TBS; -1: never */
    uint16_t cleanup_aborts;
    uint32_t alerts_cleanup;

    uint32_t st_end;
    bool     have_end;
    txp_drv_t drv_end;

    /* The clock check: the CPU cycle count against esp_timer over the trial. */
    uint32_t clk_cyc;
    int64_t  clk_us;
} txp_trial_t;

typedef struct {
    txp_trial_t trials[TXP_MAX_REPS];
    int         n;
    uint32_t    cpu_hz;         /* read at the start of the call */
    uint32_t    cpu_hz_end;     /* ... and at its end */
    uint32_t    events_used;
} txp_run_t;

extern txp_event_t txp_events[TXP_EVENTS];

/* "m1", "m2", "m3" -> TXP_M1.. ; anything else 0. */
int txp_phase_from(const char *s);

/*
 * One query argument: 1 found (copied into val), 0 absent, -1 present but
 * unusable (too long).
 */
typedef int (*txp_get_fn)(void *ctx, const char *key, char *val, size_t vn);

/*
 * Parse the arguments for `phase` (m1, m2 or m3). False with a message in
 * `err` for an argument that is unusable, out of range, or not used by this
 * phase: the probe refuses rather than runs something else.
 */
bool txp_parse(const char *phase, txp_get_fn get, void *ctx,
               txp_params_t *p, char *err, size_t errn);

/* Run the trials. False, touching nothing, on a chip with no register file. */
bool txp_run(const txp_params_t *p, txp_run_t *run);

/* The answer, written through `emit`; false when emit fails or a line did not
 * fit. */
typedef bool (*txp_emit_fn)(void *ctx, const char *s, size_t n);
typedef struct {
    const char *git;
    long long   run_us;
    long long   uptime_us;
    unsigned    id;
    int         dlc;
} txp_head_t;
bool txp_write_json(const txp_params_t *p, const txp_run_t *run,
                    const txp_head_t *h, txp_emit_fn emit, void *ctx);

/* ------------------------------------------------- what the target gives -- */

uint32_t txp_env_cycles(void);      /* the CPU cycle counter */
uint32_t txp_env_cpu_hz(void);
int64_t  txp_env_timer_us(void);    /* esp_timer, for the clock check */
void     txp_env_irq_off(void);     /* nests with txp_ll_abort()'s section */
void     txp_env_irq_on(void);
bool     txp_env_reinstall(void);   /* driver down and up, all alerts on */
bool     txp_env_send(uint16_t trial);  /* the trial frame, no wait, resends on */
uint32_t txp_env_alerts(void);      /* read and clear, no wait */
bool     txp_env_driver(txp_drv_t *out);
void     txp_env_clear_tx(void);    /* drop the driver's queued frames */
void     txp_env_flush_rx(void);
void     txp_env_settle(int ms);
void     txp_env_hold(int ms);      /* block at least ms, interrupts on, the
                                     * driver running */

#endif /* TWAI_PORT_UNACKED_H */
