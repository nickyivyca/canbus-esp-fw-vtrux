/*
 * fake_buf -- a model of the C3's single transmit buffer and its abort, written
 * FROM THE MEASUREMENT and not from the datasheet. Spec 5.2 item 9's second
 * model requirement: "It states what it is calibrated against."
 *
 * CALIBRATION, every number traceable to one run:
 *   artifacts/gen-inhibit/runs/txabort_full.json, firmware f6eb9ec, 2026-09-27,
 *   254 live trials in one session, recounted independently by the reviewing
 *   session.
 *
 *   air time, DLC 8 @ 500 kbit/s   242 / 249 / 253 us  (the noabort phase)
 *   abort while AWAITING           removed 162/162, buffer free 6-21 us
 *   abort while TRANSMITTING,
 *     idle bus                     a no-op: the frame completed, 37/37
 *     under contention             NO EFFECT on 7 of 8; the frame stayed in the
 *                                  buffer and kept contending, ARB_LOST only,
 *                                  and no completion event at all
 *   the item 4 loop                78/78 freed, 1-2 abort commands, 5/6/36 us
 *   empty buffer (control)         no alert
 *   an aborted frame's reporting   TX_IDLE + TX_SUCCESS, msgs_to_tx -> 0, in
 *                                  169 of 169 cases where it NEVER reached the
 *                                  wire
 *
 * WHAT IS A STRESS CASE AND NOT A TRUCK RATE. The contention numbers come from a
 * flood two witnesses measured at ~3978 frames/s, i.e. a saturated bus. Spec
 * 12.2: bench replays are burstier than the truck. Anything here driven by
 * fb_set_contended(true) is a stress case and is labelled so at the call site.
 *
 * THE ONE PROPERTY THAT MATTERS MOST. `fb_wire_count()` and `fb_outstanding()`
 * are deliberately different questions. The hardware reports an aborted frame
 * exactly as it reports a real transmission, so a model where "left the buffer"
 * implied "reached the wire" would let the scheduler pass item 5 by accident --
 * which is the whole defect item 5 exists to prevent.
 */
#ifndef FAKE_BUF_H
#define FAKE_BUF_H

#include <stdbool.h>
#include <stdint.h>

#include "gi_sched.h"

void fb_reset(void);

/* Virtual time. Nothing here reads a real clock. */
void    fb_advance(int64_t us);
int64_t fb_now(void);

/*
 * Is the bus contended? On an idle bus a submitted frame starts transmitting
 * almost at once and an abort during transmission is a no-op. Under contention a
 * frame sits AWAITING, and an abort that lands while it is in its arbitration
 * field has no effect until it loses and TS falls to 0.
 */
void fb_set_contended(bool contended);

/*
 * Make the NEXT abort issued while TRANSMITTING have no effect, as 7 of 8 did
 * under contention. The frame then returns to AWAITING after `lose_after_us`,
 * modelling the loss of arbitration that drops TS to 0 -- which is the window
 * where the item 4 loop's re-abort works.
 */
void fb_abort_no_effect_next(int64_t lose_after_us);

/* Refuse the next `n` submits, as a full driver queue would. */
void fb_refuse_next(int n);

/*
 * THE RACE THE ATOMIC OP NARROWS AND CANNOT CLOSE. On the next
 * abort_if_awaiting(), the controller enters arbitration between the state read
 * and the register write: the call sees AWAITING, issues the command, and the
 * command lands at TS = 1 where the measurement says it has no effect. The frame
 * then loses arbitration after `lose_after_us` and returns to AWAITING, which is
 * the window where a re-abort works.
 *
 * This exists because the outcome is REACHABLE and I had claimed it was not. The
 * hardware does not stop for a critical section; it only becomes very unlikely to
 * interleave.
 */
void fb_race_next(int64_t lose_after_us);

/*
 * Keep the frame TRANSMITTING for `us` instead of one air time, as an
 * error-retransmit storm would. The only way to reach item 4's 1 ms bound, since
 * the loop refuses to issue a command while transmitting and simply waits.
 */
void fb_stick_transmitting(int64_t us);

/* The HAL the scheduler is given. */
const gs_hal_t *fb_hal(void);

/* ------------------------------------------------------- what went out ---- */

/*
 * Frames that REACHED THE WIRE. Not the same as frames that left the buffer: an
 * aborted frame leaves the buffer and never reaches the wire, and the device
 * cannot tell the two apart. The test can, which is the point of a model.
 */
int      fb_wire_count(void);
uint32_t fb_wire_id(int i);
const uint8_t *fb_wire_data(int i);

/* Frames handed to the buffer at all, in order. */
int      fb_submit_count(void);
uint32_t fb_submit_id(int i);

/* How many abort commands the scheduler issued, and how many had no effect. */
int fb_abort_cmds(void);
int fb_aborts_with_no_effect(void);

/*
 * Abort commands that LANDED while the buffer was TRANSMITTING.
 *
 * NOT AN INVARIANT, and the correction matters. The scheduler never *chooses* to
 * abort a transmitting frame -- item 4 waits for TS to fall -- so on the
 * non-racing path this is 0, and a mutant that aborts regardless is caught by it.
 * But the controller can enter arbitration between the state read and the
 * register write inside abort_if_awaiting(), and the command then lands at TS = 1
 * anyway. fb_race_next() produces exactly that. So read this as a property of
 * the non-racing path, never as "the scheduler cannot reach the no-effect case".
 * I claimed it could not, and the reviewing session showed the race, 2026-09-27.
 */
int fb_abort_cmds_while_transmitting(void);

/* Is anything still in the buffer? (the HAL's `outstanding`) */
bool     fb_outstanding_now(void);
gs_buf_t fb_state_now(void);

#endif /* FAKE_BUF_H */
