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

/* Make twai_reconfigure_alerts() fail, as a driver that will not arm would. */
void ft_fail_alerts_config(bool fail);

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

/* Make twai_reconfigure_alerts() fail, as a driver that will not arm would. */
void ft_fail_alerts_config(bool fail);

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
