/*
 * fake_twai -- a TWAI driver model with the properties that actually bit us.
 *
 * Review E1. The host suite in ../gen_inhibit_host compiles the pure CORE and
 * cannot see the shim at all, and by 2026-09-25 four defects in a row had
 * lived exactly there:
 *
 *   1. alerts read only after an inhibit frame, crediting whatever was
 *      pending -- so a diag frame's TX_SUCCESS completed an inhibit that had
 *      not been sent;
 *   2. a late completion staying latched and crediting the NEXT inhibit at
 *      queue time, for ever after;
 *   3. a completion arriving after a bounded read gave up, producing a false
 *      TX_LATE on a frame that finished in good time;
 *   4. a diag frame still in the buffer when the inhibit was queued behind it,
 *      which the pre-queue drain could not see because it had not completed.
 *
 * Every one of those is a property of the DRIVER, not of the decision logic:
 * alerts are latched bits shared by every frame, the queue is FIFO, and
 * completion takes time. So the model has to have those properties or it
 * proves nothing.
 *
 * WHAT THIS IS NOT. It is not the ESP32's TWAI peripheral. It has no
 * arbitration, no bit timing, no error counters beyond what a test sets, and
 * no ISR. It answers "does the shim attribute completions correctly", and a
 * green run here is not evidence about the hardware -- that is what the bench
 * is for.
 *
 * DETERMINISM. The worker runs on its own thread, but never concurrently with
 * the test: every blocking call hands control back and waits. Time is virtual
 * and only the test advances it. Two runs of the same script produce the same
 * trace.
 */
#ifndef FAKE_TWAI_H
#define FAKE_TWAI_H

#include <stdbool.h>
#include <stdint.h>

/* ------------------------------------------------------------ the model -- */

void ft_reset(void);

/*
 * How long a queued frame takes to complete, in microseconds of virtual time,
 * counted from when the controller starts transmitting it -- which is when it
 * reaches the head of the FIFO, not when it was queued. Default 200 us, about
 * one 8-byte frame's air time at 500 kbit.
 */
void ft_set_air_time(int64_t us);

/*
 * Make the next `n` frames never complete. The bus is busy, or the peripheral
 * is wedged: the frame sits at the head of the queue for ever and msgs_to_tx
 * never falls. This is what produces a genuine TX_LATE.
 */
void ft_stall_next(int n);

/*
 * Stall by ID instead. ft_stall_next() marks whichever frame is queued next,
 * which is order-dependent and not under the test's control: gi_tick() may
 * emit a diag page before the inhibit, and the stall then lands on the diag.
 * A case that means "the INHIBIT frame never completes" has to say so.
 */
void ft_stall_id(uint32_t id, int n);

/*
 * Air time for ONE id, overriding ft_set_air_time().
 *
 * Needed to build the diag-ahead-of-inhibit ordering deterministically: the
 * diag has to COMPLETE while the inhibit is still in flight, and with a single
 * air time the two windows cannot be separated -- whatever value is chosen,
 * either both complete or neither does, depending on when the diag happened to
 * be queued. Case 4 failed twice that way.
 */
void ft_set_air_time_id(uint32_t id, int64_t us);

/* How many frames of `id` have completed on the wire. */
int ft_wire_count_id(uint32_t id);

/* How many frames of `id` were QUEUED (the probe is counted there). */
int ft_sent_count_id(uint32_t id);

/* Make twai_transmit() refuse the next `n` frames (queue full / not running). */
void ft_refuse_next(int n);

/* Raise TWAI_ALERT_TX_FAILED when the next completing frame finishes. */
void ft_fail_next(void);

/* Queue a frame from ANOTHER task, as an SLCAN or MQTT path would. Spec 3.2
 * forbids this while the inhibitor owns the bus; the model allows it so a test
 * can show what it costs when the refusal is missing. */
bool ft_foreign_transmit(uint32_t id, const uint8_t *data, uint8_t dlc);

/* Deliver a frame to the worker's receive path. */
void ft_deliver(uint32_t id, const uint8_t *data, uint8_t dlc);

/* Make the next `n` twai_receive() calls fail with a hard error. */
void ft_rx_error_next(int n);

/* ------------------------------------------------------- what went out --- */

typedef struct
{
    int64_t  t_queued;
    int64_t  t_done;        /* -1 while still outstanding */
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
    bool     foreign;       /* queued by a task other than the worker */
    bool     failed;

    /*
     * THE FRAME FLAGS, recorded because dropping them hid five defects at once.
     *
     * This struct used to keep id, dlc and data and discard everything else, so
     * no test anywhere could see what the driver had actually been handed.
     * Round 6 (2026-09-25) mutated dispatch_emits() to set extd, to set self,
     * to send the wrong DLC, and to copy the wrong number of payload bytes --
     * and all four suites stayed green, because the core's emit list, which is
     * what invariants.py and the goldens read, was correct in every one of
     * them. The defect was in the copy AFTER it.
     *
     * Two of them would matter on the truck:
     *
     *   extd = 1  sends the inhibit as a 29-bit identifier. The GENE inverter
     *             filters on the 11-bit 0x051 and never sees it, so the VCM's
     *             torque command stands -- while tx_ok still counts up and
     *             every diag page reports a healthy inhibit. Silently
     *             ineffective is the worst failure this firmware has.
     *   self = 1  makes the controller deliver the frame back to our own
     *             receive path. The worker reads its own 0x051, cannot tell it
     *             from the VCM's, and answers it with counter + 1 -- a
     *             self-sustaining transmit loop on a live powertrain bus.
     */
    bool     extd;
    bool     self;
    bool     rtr;
    bool     ss;
} ft_frame_t;

/*
 * SPEC 8: was the driver torn down while a thread was inside twai_receive()?
 *
 * On the device that is a use-after-free -- twai_driver_uninstall() frees the
 * memory the blocked call is using -- and gen_inhibit_quiesce() exists to make
 * it impossible by waiting for the worker to park. A non-concurrent model
 * cannot produce the crash, but it CAN detect the violation, which is the
 * useful half: the fake knows whether anyone is inside the driver and records
 * every teardown that happened anyway.
 *
 * Non-zero means the handshake was skipped. Removing the wait in
 * gen_inhibit_quiesce() left E1 green until this existed.
 */
int ft_unsafe_teardowns(void);

/* The mode twai_driver_install() was last given (spec 3, OBSERVE). */
int ft_installed_mode(void);

/*
 * The rest of what twai_driver_install() was last given.
 *
 * The TWAI driver exposes no read-back of any of these, so the value handed to
 * install is the only thing a test can check -- the same argument the mode
 * above is recorded under. Added 2026-09-26: the queue depth and the acceptance
 * filter were not observable here at all, which is why the review's Q1, Q2 and
 * F1 mutations survived E1.
 *
 * `rx_queue_len` is spec 5.1 item 1 (at least 32 whenever the driver is
 * installed). The acceptance pair is item 2: accept-all is code 0 with mask
 * 0xFFFFFFFF, the TWAI mask being don't-care-bits-set.
 */
/*
 * How many times twai_driver_install() has been called since ft_reset().
 *
 * Needed because zero is also what the accessors below report when no
 * install has happened, so without this a case cannot tell "the firmware
 * installed a queue of 0" from "nothing installed and you are reading the
 * reset value". Case 18 failed that way first time out.
 */
int ft_install_count(void);

uint32_t ft_installed_rx_queue_len(void);
uint32_t ft_installed_acc_code(void);
uint32_t ft_installed_acc_mask(void);
int      ft_installed_single_filter(void);

/*
 * THE PRIVATE HAL THE TRANSMIT SCHEDULER USES (spec 5.2 item 8, 12.3 item 5).
 *
 * `aborts` counts commands issued; `aborts_while_tx` counts those that landed on a
 * TRANSMITTING head, which item 4 says the scheduler must never do -- and which the
 * scheduler's own statistics cannot distinguish from a command it did not send;
 * `removed` counts frames the abort actually took out, which is NOT the same as
 * frames that left the controller, because a completion looks identical.
 */
int ft_ll_aborts(void);
int ft_ll_aborts_while_tx(void);
int ft_ll_removed(void);

/* Make twai_reconfigure_alerts() fail, as a driver that will not arm would. */
void ft_fail_alerts_config(bool fail);

/* ------------------------------------------------- the wire invariant --- */

/*
 * What may be handed to twai_transmit() AT ALL, checked on every frame.
 *
 * This is deliberately a property of the fake rather than of a test case. A
 * case asserts what its scenario should produce; this asserts what no scenario
 * may ever produce, so it covers the paths nobody thought to write a case for.
 * Same argument as ft_set_step_hook(), one layer lower.
 *
 * SCOPE, and why it stops where it does. The fake checks only what a CAN
 * controller can know: the frame flags, the DLC for the IDs the spec fixes,
 * the two payload bytes that must never carry torque, and whether anything was
 * transmitted at all while the driver is installed listen-only. It does NOT
 * reach into the core for the current mode. The reviewing session asked for
 * "ID in the allowed set for the current mode"; the mode lives a layer above
 * the driver, and a fake that read it would be asserting the core's state
 * against the core. ft_allow_ids() is the seam instead -- the TEST declares
 * what this scenario may put on the wire, and the fake enforces it on every
 * frame. Mode knowledge stays where it belongs and the check still runs
 * everywhere.
 */
int         ft_wire_violations(void);
const char *ft_wire_violation(int i);

/*
 * Restrict the IDs that may reach the driver.
 *
 *   n  > 0   only these IDs may be queued
 *   n == 0   no restriction (the default)
 *   n  < 0   NOTHING may be queued -- spec 3's OBSERVE, where the allowed set
 *            is empty rather than merely narrow
 *
 * The three cases are distinct on purpose. An API where "empty" and "unset"
 * were the same value would make the strictest rule in the spec the one that
 * checks nothing, which is the shape of a check that cannot fail.
 *
 * A frame outside the set is a violation, reported with the caller's label so
 * the failure names the rule rather than the symptom.
 */
void ft_allow_ids(const uint32_t *ids, int n, const char *why);

/*
 * How many frames the controller delivered back to our own receive path
 * because they were queued with self = 1.
 *
 * Non-zero is the loop, observed as behaviour rather than as a flag: the
 * worker really does receive its own transmission here, and really does
 * respond to it, which is what makes the cost legible.
 */
int ft_self_received(void);

/*
 * When the driver last handed a frame to the worker, in virtual time.
 *
 * This is the lower bound on the core's t_rx: gen_inhibit.c reads the clock
 * immediately after twai_receive() returns, so its t_rx is this or a shade
 * later. A probe due at t_rx + offset_us therefore cannot legitimately be
 * queued before this + offset_us, which is what makes the spin testable
 * without the test having to guess when the worker woke up.
 */
int64_t ft_last_rx_time(void);

/*
 * How many frames of `id` the TEST delivered -- ft_deliver() only.
 *
 * Deliberately NOT counting self-received frames. The one property that
 * catches a self-reception loop by its behaviour is "no more responses went
 * out than commands came in", and if the loop's own frames counted as
 * commands it would raise its own ceiling and the check would pass while the
 * queue ran away. This suite has already been fooled four times by a model
 * that erred in the same direction as the defect; this is that trap exactly,
 * so the counter is narrow on purpose.
 */
int ft_delivered_count_id(uint32_t id);

int              ft_sent_count(void);
const ft_frame_t *ft_sent(int i);

/* Frames that actually completed on the "wire", in completion order. */
int              ft_wire_count(void);
const ft_frame_t *ft_wire(int i);

/* ------------------------------------------------------------- driving --- */

/*
 * Run the worker until it is next blocked in twai_receive(), advancing virtual
 * time by at most `us`. Returns the virtual time now.
 */
int64_t ft_run(int64_t us);

/*
 * A hook run at EVERY step boundary -- after each ft_run(), and after each
 * blocking call inside the worker resolves.
 *
 * WHY. tx_ok can never exceed the number of frames that completed on the wire.
 * That is an ALWAYS-property, and checking it only at the end of a case misses
 * a window that opens and closes: the reviewing session reinstated 632af32's
 * completion logic and every case still passed, because the early credit
 * happened at ~8 ms and the counts had agreed again by the time anything
 * looked. A property that holds at the end is not the same as one that always
 * holds.
 */
void ft_set_step_hook(void (*fn)(void));

int64_t ft_now(void);
void    ft_start_worker(void);
void    ft_stop_worker(void);

#endif /* FAKE_TWAI_H */
