/*
 * gi_txabort_probe -- MEASUREMENT-ONLY. Never in an image that flies.
 *
 * Spec 5.2 item 9 (and spec 12.4's "abort behaviour of the C3 TWAI" row): the
 * transmit scheduler is to preempt a telemetry frame by aborting it, and the
 * hardware's behaviour has to be measured before it is modelled. This file is
 * that measurement and nothing else.
 *
 * It exists only when the build defines GI_INSTRUMENT_TXABORT, and the source
 * file is not even added to the component otherwise -- see main/CMakeLists.txt.
 * That is deliberate: a preprocessor guard inside a compiled file leaves the
 * symbols one #define away from an image, whereas a source that is not in the
 * build cannot be linked by accident. E3's no-instrument rows check the shipping
 * image for these symbols anyway, because a check that rests on one mechanism is
 * one mistake from being no check.
 *
 * WHY IT IS NOT IN THE SHIPPING BUILD BEHIND A RUNTIME SWITCH. The abort command
 * is a private HAL call written from outside the driver. An unexercised path of
 * that kind in a shipping image is exactly what the no-instrument rows exist to
 * refuse, and the scheduler's own abort path will be tested on the shipping
 * build later, by 5.2 item 10's bench runner. This build does not stand in for
 * that: it characterises the silicon and the driver, not the scheduler.
 */
#ifndef GI_TXABORT_PROBE_H
#define GI_TXABORT_PROBE_H

#include "esp_err.h"
#include "esp_http_server.h"

/*
 * POST /gi_txabort?phase=<p>[&gate=off|rs][&label=s][&reps=N]
 *                 [&d0=us][&d1=us][&step=us][&settle=ms]
 *
 * Runs the trial sequence synchronously and answers with the records as JSON.
 *
 * THE PHASES ARE MECHANICAL, AND SAY NOTHING ABOUT THE BUS. What the bus was
 * doing is the bench script's business, and it is recorded in `label`, which is
 * echoed back verbatim. Keeping the two apart matters: the firmware cannot know
 * whether another node was flooding, so a phase name that claimed to would be a
 * label nothing checks.
 *
 *   abort     Submit one frame, then abort it after a delay swept d0..d1 by
 *             `step`, `reps` trials per step. On an idle bus the sweep crosses
 *             the frame -- at 0 us the controller has barely started, by ~250 us
 *             it is done -- so this covers both the during-transmission case and
 *             the abort-versus-completion race. With another node flooding it
 *             also produces the arbitration-loss case.
 *   control   Abort with NOTHING in the buffer. The control for "what does the
 *             abort command do by itself", which every other row is read
 *             against: without it, "the abort raised TX_SUCCESS" cannot be told
 *             from "the abort command always raises TX_SUCCESS".
 *   noabort   Submit and do not abort. The signature of a genuine completion --
 *             alerts, msgs_to_tx, time to a free buffer -- which is the only
 *             thing an aborted frame's reporting can be COMPARED to. Spec 5.2
 *             item 5 is a question about telling those two apart, so a run
 *             without this phase cannot answer it.
 *
 * gate=rs makes the awaiting-arbitration case deterministic: the trial waits for
 * the RS 0 -> 1 edge -- the START of another node's frame -- and submits into it,
 * so ours cannot begin before that frame's EOF and the intermission, and a short
 * abort delay is guaranteed to land while ours is still awaiting. That replaces
 * the thing it is easy to get wrong: assuming a flood saturates the bus. At
 * 500 kbit/s saturation with DLC-8 frames needs roughly 3700-4300 frames a
 * second, and no measured rate on this bench establishes that.
 *
 * THE EDGE AND NOT THE LEVEL. RS = 1 holds throughout the frame in progress,
 * including its last few bits, so a level test can pass at a point where the
 * abort lands after that frame's EOF with ours already started. Two bounded waits
 * with separate skip codes: no edge at all (bus quiet) and RS never falling (bus
 * continuously busy) are different facts and are reported as different rows.
 * `edge_us` gives edge-to-abort per trial, and TS = 0 in `st_before` is the check
 * that the gate did its job.
 *
 * settle must be at least 1 ms. Each trial busy-polls and never blocks, at
 * priority 18; with no delay between trials the IDLE task never runs and the
 * task watchdog starts logging inside the measurement.
 *
 * The caller is responsible for the bus: every phase needs at least one other
 * node in normal mode to ACK. With no ACKer the controller retransmits and every
 * number here means something else.
 */
esp_err_t gi_txabort_register(httpd_handle_t server);

#endif /* GI_TXABORT_PROBE_H */
