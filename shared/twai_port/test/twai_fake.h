/*
 * twai_fake -- the one host fake of the ESP-IDF TWAI driver (SPEC.md T10).
 *
 * Both projects' host builds compile the real twai_port (and anything else
 * that calls the TWAI driver) on this. It implements the driver's entry
 * points declared in include/driver/twai.h and the register calls in
 * include/hal/twai_ll.h, and models what the controller does behind them:
 * its four states, the transmit buffer and queue, the receive queue and
 * filter, alerts, the counters, bus-off and recovery, and the transmit
 * buffer's status register as measured on the C3.
 *
 * Merged from gen-inhibit's fake_twai.c (with its register fakes) and the
 * interposer's twai_fake.cpp (with its S3 calibration and recovery), and
 * checked against ESP-IDF v5.4.1's components/driver/twai/twai.c, which
 * decides wherever the two disagreed. README.md has what each behaviour
 * rests on, and what changed from each source fake.
 *
 * WHAT IT IS NOT. It is not a CAN bus. There is no arbitration between
 * nodes, no bit timing beyond frame air time, and no error counter that
 * climbs by itself. A pass on it is not evidence about the hardware.
 *
 * Time comes from a clock adapter (twai_fake_set_clock) in place of
 * Arduino millis(): each project hands over its own virtual clock. The
 * model is lazy: every entry point first brings the controller up to the
 * clock's "now", so frames complete when their air time has passed,
 * whoever looks.
 *
 * C, in the common subset of C11 and C++11, so the interposer's g++ build
 * compiles it as it is; cpp/ holds the interposer's C++ interface to it.
 */
#ifndef TWAI_FAKE_H
#define TWAI_FAKE_H

#include <stdbool.h>
#include <stdint.h>

#include "driver/twai.h"

#ifdef __cplusplus
extern "C" {
#endif

/* --------------------------------------------------------------- wiring -- */

/*
 * Back to power-on: no driver, empty queues, every counter and log zero,
 * every knob at its default. The wiring (clock, tick length, chip, hooks)
 * is kept: it is the project's harness, not the controller's state.
 */
void twai_fake_reset(void);

/*
 * The clock adapter: a function returning the project's virtual time in
 * microseconds, never going backwards. NULL (the default) is the fake's
 * own clock, which only twai_fake_advance() moves.
 */
typedef int64_t (*twai_fake_clock_fn)(void *ctx);
void    twai_fake_set_clock(twai_fake_clock_fn now_us, void *ctx);
int64_t twai_fake_now(void);
/* Move the fake's own clock (only when no adapter is set), then catch up. */
void    twai_fake_advance(int64_t us);
/* Bring the controller up to the clock's now. Every entry point does this
 * first; a test that reads nothing else can call it. */
void    twai_fake_poll(void);

/* How long one FreeRTOS tick is, in microseconds. Default 1000. */
void    twai_fake_set_tick_us(int64_t us);

/*
 * Where a driver call would block (twai_receive, twai_read_alerts, or
 * twai_transmit on a full queue, with a non-zero wait): the fake calls this
 * with the absolute deadline, and calls it again until the wait is
 * satisfied or the clock reaches the deadline. The project's scheduler
 * decides what happens meanwhile (gen-inhibit's hands the CPU to the test
 * thread). It returns false to give up at once. With no hook the call
 * gives up without waiting, as a zero wait would.
 */
typedef bool (*twai_fake_wait_fn)(int64_t deadline_us, void *ctx);
void twai_fake_set_wait_hook(twai_fake_wait_fn fn, void *ctx);
/*
 * A scheduling choice of the harness, not a driver behaviour: with a wait
 * hook, every twai_receive() with a non-zero wait calls it once before
 * taking a frame, even with one waiting, so a worker loop takes one frame
 * per turn the test grants (gen-inhibit's shim). Default off: a waiting
 * frame is returned at once, as the driver's queue does.
 */
void twai_fake_set_receive_yields_first(bool on);

/*
 * Every twai_transmit() call, before any check, so a project's invariant on
 * what may be handed to the driver at all runs on refused calls too
 * (gen-inhibit's wire invariant). And every frame the driver accepted.
 */
typedef void (*twai_fake_tx_fn)(const twai_message_t *m, void *ctx);
/* (Neither is called for twai_fake_foreign_transmit().) */
void twai_fake_set_tx_hook(twai_fake_tx_fn fn, void *ctx);
void twai_fake_set_tx_accepted_hook(twai_fake_tx_fn fn, void *ctx);

/*
 * The chip (README.md, "The C3 and the S3"). In ESP-IDF v5.4.1 the two have
 * the same driver, the same TWAI registers and the same TWAI capabilities;
 * what differs is that only the C3's transmit-buffer register behaviour is
 * measured (twai_port T8). On the S3 every twai_ll_* call is counted in
 * twai_fake_ll_unmeasured_calls(), which a healthy S3 run keeps at zero.
 * Default C3.
 */
typedef enum {
    TWAI_FAKE_CHIP_C3 = 0,
    TWAI_FAKE_CHIP_S3,
} twai_fake_chip_t;
void twai_fake_set_chip(twai_fake_chip_t chip);

/* ---------------------------------------- the peripheral and reset mode -- */

/*
 * The TWAI's bus clock enable and reset bits (soc/system_reg.h, read with
 * REG_READ), and the controller's reset mode (twai_ll_is_in_reset_mode),
 * which the restart stop reads (twai_port T12). As ESP-IDF v5.4.1 drives
 * them:
 *   power-on (twai_fake_reset)  clock off, reset not held
 *   twai_driver_install()       clock on, reset pulsed (not held), and the
 *                               controller in reset mode (twai.c:497-499,
 *                               twai_hal.c:27)
 *   twai_start()                out of reset mode (twai_hal.c:91)
 *   twai_stop()                 reset mode; any transmission halted
 *                               (twai_hal.c:96-99)
 *   bus-off                     reset mode, until twai_initiate_recovery()
 *                               takes it out to recover (twai_hal_iram.c:162-165)
 *   recovery complete           reset mode (twai_hal_iram.c:110)
 *   twai_driver_uninstall()     clock off (twai.c:568-571)
 * Reset mode is derived from those, never kept beside them, except for the
 * one register write the driver does not know about:
 * twai_ll_enter_reset_mode() from code outside the driver. That takes the
 * controller off the bus with the driver still RUNNING: a frame on the wire
 * is cut (twai_fake_tx_cut_by_reset()), nothing more is sent or received,
 * and no bus-off can happen, until twai_start(), twai_initiate_recovery()
 * or the next install takes it out again.
 *
 * While the clock is off or the reset held, the registers cannot be used:
 * every twai_ll_* call is counted in twai_fake_ll_unclocked_calls(), reads
 * give 0 and writes do nothing. A healthy run keeps that count at zero.
 */
void twai_fake_set_periph(bool clock_on, bool reset_held);
bool twai_fake_periph_clock(void);
bool twai_fake_periph_reset_held(void);
bool twai_fake_in_reset_mode(void);

/*
 * The controller is receiving a frame for the next `us` microseconds: the
 * status register's RS bit is set, while running and out of reset mode.
 * Nothing is delivered by it (twai_fake_deliver() does that). Entering
 * reset mode cuts it. 0 ends it.
 */
void twai_fake_set_receiving(int64_t us);

/* ------------------------------------------ the register-level switch -- */

/*
 * What code outside the driver can do through hal/twai_ll.h to switch a
 * running controller between normal and listen-only with the driver kept
 * (twai_port T14-T18), as ESP-IDF v5.4.2's twai_hal.c and twai_ll.h drive
 * the same registers:
 *   - the mode register: listen-only at install (twai_hal_init), the
 *     install's mode at twai_start(), listen-only at twai_stop() and at
 *     bus-off. twai_ll_set_mode() writes it in reset mode. The driver's own
 *     mode (twai_fake_mode()), which gates twai_transmit(), never changes.
 *     In listen-only the controller sends nothing: a frame in the buffer
 *     stays there, reading as awaiting arbitration.
 *   - the error counters: twai_start() writes TEC 0 and REC 128 in
 *     listen-only (the LISTEN_ONLY_DOM errata) or 0; install writes both 0.
 *     twai_ll_set_rec() and twai_ll_set_tec() write them in reset mode.
 *   - twai_ll_exit_reset_mode() leaves a reset mode that
 *     twai_ll_enter_reset_mode() entered.
 * A mode or counter write outside reset mode does nothing and is counted
 * (twai_fake_ll_writes_outside_reset()); so is an exit from a reset mode the
 * driver keeps (stopped, bus-off; twai_fake_ll_exits_misused()). A healthy
 * run keeps both at zero.
 */
twai_mode_t twai_fake_hw_mode(void);            /* the mode register */
uint32_t    twai_fake_tec(void);                /* the registers, no call counted */
uint32_t    twai_fake_rec(void);
int         twai_fake_ll_writes_outside_reset(void);
int         twai_fake_ll_exits_misused(void);
int         twai_fake_ll_rec_reads(void);       /* twai_ll_get_rec() calls */
/* Every value twai_ll_set_rec() wrote in reset mode, in order (the first 64). */
int         twai_fake_rec_write_count(void);
uint32_t    twai_fake_rec_write(int i);

/*
 * What reset mode, entered from outside the driver, does to a frame in the
 * transmit buffer (on the wire or awaiting arbitration). Not measured
 * (twai_port T19 measures it on the S3); every behaviour is selectable so a
 * port can be tested on each.
 *   KEEP     the frame stays, its transmit request cancelled: never sent,
 *            the buffer occupied and reading as awaiting, until an abort
 *            removes it or the driver discards it (the default).
 *   RESUME   the request survives: the frame is sent once reset mode is left.
 *   RELEASE  the hardware frees the buffer with no transmit interrupt. The
 *            driver's TX-buffer-occupied flag and its msgs_to_tx stay set,
 *            so every later twai_transmit() queues behind a frame that is
 *            not there, until a stop, uninstall or recovery resets them
 *            (twai_fake_driver_buf_stuck()).
 */
typedef enum {
    TWAI_FAKE_RESET_TX_KEEP = 0,
    TWAI_FAKE_RESET_TX_RESUME,
    TWAI_FAKE_RESET_TX_RELEASE,
} twai_fake_reset_tx_t;
void     twai_fake_set_reset_tx(twai_fake_reset_tx_t b);
uint32_t twai_fake_tx_kept_by_reset(void);
uint32_t twai_fake_tx_released_by_reset(void);
bool     twai_fake_driver_buf_stuck(void);

/*
 * An abort on a frame being transmitted does not stop it but stops its
 * retransmission (twai_ll.h). On a frame nobody ACKs, the frame then ends
 * with its current attempt, never on the wire, and the driver reports it
 * as TX_SUCCESS (the default: the abort sets TCS, as the C3's abort of an
 * awaiting frame measured) or TX_FAILED. Not measured (twai_port T19).
 * Each attempt is one air time, back to back from the frame's start.
 */
typedef enum {
    TWAI_FAKE_ABORT_REPORTS_SUCCESS = 0,
    TWAI_FAKE_ABORT_REPORTS_FAILED,
} twai_fake_abort_report_t;
void     twai_fake_set_abort_report(twai_fake_abort_report_t r);
uint32_t twai_fake_aborted_attempts(void);

/* Every unacknowledged attempt that ends raises one bus error (an ACK
 * error). Default off, so frames retrying for ever do not count bus errors
 * in harnesses written before it. */
void     twai_fake_set_ack_errors(bool on);

/* Active error flags sent: one per bus error while the controller is on the
 * bus (running, out of reset mode) with both error counters below 128, in
 * listen-only too (the LISTEN_ONLY_DOM errata). */
uint32_t twai_fake_active_error_flags(void);

/* ------------------------------------------------------------ two cores -- */

/*
 * The S3 has two cores, the C3 one. esp_cpu_stall(), esp_cpu_unstall() and
 * esp_cpu_get_core_id() (host/esp_cpu.h) are the fake's. The code under
 * test runs on core twai_fake_set_core() (default 0). A sender on the other
 * core queues a frame every `every_us` of the fake's time through the
 * driver (twai_fake_foreign_transmit()), while that core runs and the
 * driver takes it, until the core is stalled; NULL stops it. Time passes
 * for it in twai_fake_advance() and esp_rom_delay_us(). On the C3 there is
 * no other core: a stall there, or of the calling core, is counted in
 * twai_fake_stall_misused() and does nothing.
 * twai_fake_ll_calls_at_stall() is how many twai_ll calls and REG_READs of
 * the TWAI's clock and reset bits (twai_fake_ll_calls(); not the stall
 * fields) had been made when the first stall took effect,
 * or -1, so a test can tell whether the stall came first.
 */
void     twai_fake_set_core(int core);
void     twai_fake_set_other_core_sender(const twai_message_t *m, int64_t every_us);
uint32_t twai_fake_other_core_sent(void);
bool     twai_fake_core_stalled(int core);
int      twai_fake_stall_calls(void);
int      twai_fake_unstall_calls(void);
int      twai_fake_stall_misused(void);
/* esp_cpu_stall() of a core already stalled: in ESP-IDF v5.4.2 it clears
 * and sets the stall fields again, so the core runs for about one register
 * write, twice. REG_READ() of RTC_CNTL_OPTIONS0_REG and
 * RTC_CNTL_SW_CPU_STALL_REG (include/soc/rtc_cntl_reg.h) gives the stall
 * fields of each core stalled. */
int      twai_fake_restalls(void);
int      twai_fake_stall_reg_reads(void);   /* REG_READs of the stall fields */
int      twai_fake_ll_calls(void);
int      twai_fake_ll_calls_at_stall(void);

/*
 * The driver's interrupt runs on the core that installed the driver
 * (twai_fake_isr_core(), the core twai_fake_set_core() named at install). It
 * cannot run while that core has interrupts off, as on the restart path
 * (twai_fake_set_interrupts_off(), default off), or while that core is
 * stalled. Meanwhile the controller still finishes, fails or drops the
 * frame in its buffer, but nothing books it or loads the next: the
 * driver's TX-buffer-occupied flag stays set (twai_fake_driver_buf_stuck()),
 * no alert is raised, and twai_fake_isr_held() counts the events held.
 */
void     twai_fake_set_interrupts_off(bool off);
int      twai_fake_isr_core(void);
uint32_t twai_fake_isr_held(void);

/* ----------------------------------------------------------- busy waits -- */

/*
 * esp_rom_delay_us() (host/esp_rom_sys.h) is the fake's: a busy wait moves
 * the fake's own clock by `us`. With a clock adapter it calls the wait
 * hook with a deadline `us` from now until the clock gets there (the
 * project's scheduler moves its clock), and with no hook it only counts.
 */
uint32_t twai_fake_delay_calls(void);
int64_t  twai_fake_delay_us_total(void);

/* ------------------------------------------------------- transmit model -- */

/*
 * When a frame in the transmit buffer completes.
 *   TIMED    after its air time (the default): one frame at a time, back to
 *            back, the first starting when it is handed to an idle buffer.
 *   INSTANT  at once, inside the twai_transmit() that queued it.
 *   HELD     not until twai_fake_flush_tx(), so a test can have frames
 *            pending when something else happens.
 * In every mode a frame nobody ACKs in normal mode is retransmitted and
 * never completes (unless single-shot), and a stalled frame never starts.
 */
typedef enum {
    TWAI_FAKE_TX_TIMED = 0,
    TWAI_FAKE_TX_INSTANT,
    TWAI_FAKE_TX_HELD,
} twai_fake_tx_timing_t;
void twai_fake_set_tx_timing(twai_fake_tx_timing_t t);
/* Complete every frame now (HELD), in order, then return. */
void twai_fake_flush_tx(void);

/*
 * Air time. 0 (the default) is each frame's unstuffed length at the
 * installed bit timing: 47 bits for a standard data frame, 67 for an
 * extended one, plus 8 per data byte, none for a remote frame; a lower
 * bound on the real length. A non-zero value gives every frame that air
 * time (gen-inhibit's harness uses 200 us). A per-ID value overrides both;
 * 0 clears it.
 */
void    twai_fake_set_air_time_us(int64_t us);
void    twai_fake_set_air_time_id(uint32_t id, int64_t us);
/* The unstuffed length of a frame at `bit_ns` per bit, in microseconds. */
int64_t twai_fake_frame_us(uint32_t dlc, bool extd, bool rtr, int64_t bit_ns);
/* The installed bit time in nanoseconds, from brp, tseg_1 and tseg_2 at
 * 80 MHz; 0 with no driver. */
int64_t twai_fake_bit_ns(void);

/* Whether another node ACKs. Default true. A normal-mode frame with no ACK
 * is retransmitted for ever (single-shot: fails once); no-ACK mode needs
 * none. Setting it true restarts the head's attempt now. */
void twai_fake_set_acked(bool acked);

/* The next `n` frames handed over (any ID, or only `id`) never win
 * arbitration: they sit in the buffer awaiting it, and frames behind them
 * wait. This is the AWAITING state of the register model. */
void twai_fake_stall_next(int n);
void twai_fake_stall_id(uint32_t id, int n);

/* The next `n` frames (any ID, or only `id`; n < 0: every one) are accepted
 * and then complete failed: TWAI_ALERT_TX_FAILED, tx_failed_count, never on
 * the wire. They still take their air time. */
#define TWAI_FAKE_ANY_ID 0xFFFFFFFFu
void twai_fake_fail(uint32_t id, int n);
void twai_fake_clear_fail_rules(void);

/* The next `n` twai_transmit() calls (any ID, or only `id`; n < 0: every
 * one) are refused with `rc`, nothing queued. A test knob, not the
 * driver's own refusals, which the model makes itself. */
void twai_fake_refuse(uint32_t id, int n, esp_err_t rc);
void twai_fake_clear_refuse_rules(void);

/* A frame queued by another task's code, outside the code under test. Goes
 * through the same transmit path, flagged foreign in the logs. */
esp_err_t twai_fake_foreign_transmit(const twai_message_t *m);

/* ------------------------------------------------------------ the bus -- */

/* A frame arrives from the bus. Received only while running, through the
 * acceptance filter; a full receive queue loses it (rx_missed_count,
 * TWAI_ALERT_RX_QUEUE_FULL). Otherwise not received, and counted in
 * twai_fake_rx_not_received() (not a loss). */
void twai_fake_deliver(const twai_message_t *m);
void twai_fake_deliver_std(uint32_t id, const uint8_t *data, uint8_t dlc);

/* Frames lost in the controller's FIFO (rx_overrun_count,
 * TWAI_ALERT_RX_FIFO_OVERRUN), while running. */
void twai_fake_rx_overrun(uint32_t n);

/* Bus errors (bus_error_count, TWAI_ALERT_BUS_ERROR), while running. */
void twai_fake_bus_error(uint32_t n);

/* The error counters the status reports (frozen and read 0 in listen-only,
 * as the driver reports them). Nothing raises them by itself. */
void twai_fake_set_error_counters(uint32_t tec, uint32_t rec);

/*
 * Bus-off, from running only; idempotent while bus-off (the controller does
 * not re-enter a state it is in, so TWAI_ALERT_BUS_OFF is raised once per
 * episode). Queued frames stay queued and received frames stay readable;
 * nothing is received or sent until a recovery or an uninstall.
 * The frame in the buffer at the strike: kept, and discarded with the queue
 * by the recovery (the default), or with `inflight_fails` booked failed
 * (tx_failed_count, TWAI_ALERT_TX_FAILED), then TWAI_ALERT_TX_IDLE with the
 * queue left as it is (ESP-IDF v5.4.2 loads no next frame while bus-off;
 * v5.4.1 did). Which one the silicon takes is not settled from IDF's source.
 */
void twai_fake_bus_off(void);
void twai_fake_set_busoff_inflight_fails(bool inflight_fails);
/* Go bus-off the moment the n-th frame from now has completed on the wire.
 * n < 0 cancels. */
void twai_fake_bus_off_after_sent(int n);

/* Recovery ends in STOPPED after 128 bus-free signals, 1408 bit times; with
 * auto (the default) on the clock, else at twai_fake_complete_recovery(). */
void twai_fake_set_auto_recovery(bool on);
void twai_fake_complete_recovery(void);

/* ------------------------------------------------- knobs on the driver -- */

/* The next twai_driver_install() fails with `rc` (ESP_ERR_NO_MEM, say) and
 * installs nothing. The next twai_start() fails with `rc`, the driver left
 * installed and stopped. */
void twai_fake_fail_install_next(esp_err_t rc);
/* The next `n` installs fail with `rc` (n < 0: every one; 0: none). */
void twai_fake_fail_installs(int n, esp_err_t rc);
void twai_fake_fail_start_next(esp_err_t rc);
/* twai_reconfigure_alerts() fails with ESP_ERR_INVALID_STATE while set. */
void twai_fake_fail_reconfigure_alerts(bool fail);
/* The next `n` twai_receive() calls fail with ESP_FAIL. */
void twai_fake_rx_error_next(int n);
/* A can-fail knob: msgs_to_tx reads 0 whatever is queued. */
void twai_fake_misreport_msgs_to_tx(bool on);

/* ---------------------------------------------------------- inspection -- */

twai_state_t twai_fake_state(void);
bool         twai_fake_installed(void);
twai_mode_t  twai_fake_mode(void);          /* the current install's */
uint32_t     twai_fake_alerts_enabled(void); /* as reconfigured since */

/* The driver's own counts and levels as twai_get_status_info() would read
 * them, without making a driver call (no wrong-state count; zero with no
 * driver). */
void twai_fake_driver_status(twai_status_info_t *s);

/* What the last twai_driver_install() was handed. */
const twai_general_config_t *twai_fake_installed_general(void);
const twai_timing_config_t  *twai_fake_installed_timing(void);
const twai_filter_config_t  *twai_fake_installed_filter(void);
uint32_t twai_fake_installed_brp(void);     /* resolved from the quanta */
int      twai_fake_install_count(void);     /* successful installs */
/* The mode and the clock's time of each successful install, in order. */
int          twai_fake_install_log_count(void);
twai_mode_t  twai_fake_install_log_mode(int i);
int64_t      twai_fake_install_log_time(int i);

/* One frame handed to or finished by the driver. */
typedef struct {
    int64_t  t_queued;      /* when twai_transmit() accepted it */
    int64_t  t_done;        /* completion, or -1 */
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
    bool     extd, rtr, ss, self;
    bool     foreign;       /* twai_fake_foreign_transmit() */
    bool     failed;        /* completed failed (fail rule, single-shot) */
} twai_fake_frame_t;

/* Every frame the driver accepted, in order. */
int                      twai_fake_sent_count(void);
const twai_fake_frame_t *twai_fake_sent(int i);
int                      twai_fake_sent_count_id(uint32_t id);
/* Every frame that completed on the wire (not failed, not aborted), in
 * completion order. */
int                      twai_fake_wire_count(void);
const twai_fake_frame_t *twai_fake_wire(int i);
int                      twai_fake_wire_count_id(uint32_t id);

/* Frames in the controller now: the one in the buffer plus the queue. */
int      twai_fake_tx_pending(void);
/* The most of the code under test's own frames (not foreign) in the
 * controller at once. */
int      twai_fake_max_inflight(void);
int      twai_fake_rx_pending(void);

uint32_t twai_fake_transmit_calls(void);        /* every call, any outcome */
uint32_t twai_fake_listen_only_tx_attempts(void);
uint32_t twai_fake_refused_by_rule(void);       /* twai_fake_refuse() */
uint32_t twai_fake_failed_by_rule(void);        /* twai_fake_fail() */
/* Calls the driver refused for its state: no driver (ESP_ERR_INVALID_ARG,
 * IDF's handle check) or the wrong state (ESP_ERR_INVALID_STATE). A
 * healthy run keeps this at zero. */
uint32_t twai_fake_wrong_state_calls(void);

/* Where frames went that never reached the wire. */
uint32_t twai_fake_tx_discarded_by_stop(void);   /* queued, cut at stop */
uint32_t twai_fake_tx_cut_by_stop(void);         /* on the wire at stop */
uint32_t twai_fake_tx_discarded_by_uninstall(void);
uint32_t twai_fake_tx_discarded_by_recovery(void);
uint32_t twai_fake_tx_discarded_by_start(void);
uint32_t twai_fake_tx_discarded_by_clear(void);  /* twai_clear_transmit_queue */
uint32_t twai_fake_tx_failed_at_busoff(void);    /* inflight_fails */
/* The ID of every frame a stop or an uninstall threw away, in order. */
int      twai_fake_dropped_at_stop_count(void);
uint32_t twai_fake_dropped_at_stop_id(int i);

/* Receive side. */
uint32_t twai_fake_rx_not_received(void);   /* not running, or bus-off */
uint32_t twai_fake_rx_filtered(void);       /* the acceptance filter */
uint32_t twai_fake_rx_discarded(void);      /* uninstall, start, clear */
uint32_t twai_fake_rx_handed_out(void);     /* twai_receive() returned */
uint32_t twai_fake_self_received(void);     /* own frames, self = 1 */
int64_t  twai_fake_last_rx_time(void);      /* -1 before the first */
/* The cumulative counters since power-on, which an install does not zero
 * (the driver's own restart at every install). */
uint32_t twai_fake_total_rx_missed(void);
uint32_t twai_fake_total_rx_overrun(void);
uint32_t twai_fake_total_tx_failed(void);

/* Is a thread blocked inside twai_receive()? And how many stops and
 * uninstalls happened while one was: on the device a use-after-free. */
bool     twai_fake_in_receive(void);
int      twai_fake_unsafe_teardowns(void);

/* The register model (T8). */
int      twai_fake_ll_aborts(void);             /* commands issued */
int      twai_fake_ll_aborts_while_tx(void);    /* on a transmitting buffer */
int      twai_fake_ll_removed(void);            /* frames an abort removed */
int      twai_fake_ll_unmeasured_calls(void);   /* any twai_ll call or REG_READ on the S3 */
/* The register model (T12). */
int      twai_fake_ll_reset_entries(void);      /* twai_ll_enter_reset_mode() that took effect */
int      twai_fake_ll_unclocked_calls(void);    /* twai_ll calls with the clock off or reset held */
uint32_t twai_fake_tx_cut_by_reset(void);       /* on the wire when reset mode was entered */
uint32_t twai_fake_reg_reads(void);             /* REG_READ() of the two modelled registers */
uint32_t twai_fake_reg_unknown_reads(void);     /* REG_READ() of any other address */

/* What a behaviour rests on (README.md has the full table). */
typedef enum {
    TWAI_FAKE_SOURCE,       /* read in ESP-IDF v5.4.1's source or header */
    TWAI_FAKE_MEASURED,     /* measured on our bench; artifact named */
    TWAI_FAKE_ASSUMED,      /* modelled one way with no evidence either way */
    TWAI_FAKE_NOT_MODELLED, /* the fake does not do this */
} twai_fake_basis_t;
typedef struct {
    twai_fake_basis_t basis;
    const char *cite;
    const char *what;
} twai_fake_assumption_t;
const twai_fake_assumption_t *twai_fake_assumptions(int *count);

#ifdef __cplusplus
}
#endif

#endif /* TWAI_FAKE_H */
