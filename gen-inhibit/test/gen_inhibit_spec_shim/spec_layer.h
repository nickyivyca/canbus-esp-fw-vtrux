/*
 * spec_layer -- the tester's fault behaviour ON TOP OF the shim's driver, for
 * the spec 12.4 rows that judge the product: "a bus with no other node,
 * emulated" (added 2026-10-20, user).
 *
 * THE DRIVER IS twai_port's HOST FAKE since port step 1b (2026-10-31):
 * shared/twai_port/test/twai_fake.c, with the implementor's shim_harness.c
 * around it (test/gen_inhibit_shim/). Until then it was the implementor's
 * fake_twai.c, and this layer had its own model of what the fake now models
 * itself: a frame nobody acknowledges, bus errors, bus-off.
 *
 * WHY A LAYER AND NOT AN EDIT. The harness is the implementor's, and a test
 * that judges product code is not written by the implementor (reviewer,
 * 2026-10-20). So nothing here edits it, nor the fake: the layer reaches the
 * driver calls through link-time wraps (-Wl,--wrap=twai_...), the harness's
 * controls (ft_set_bus_errors, ft_bus_off, ft_set_step_hook) and the fake's
 * own public knobs (twai_fake_set_acked, twai_fake_set_error_counters).
 *
 * WHAT IT MODELS, AND THAT IT IS A GUESS. With no other node on the bus,
 * nothing acknowledges, so a frame the device queues never completes: the
 * controller retransmits it at once, each attempt ends in an ACK error, and
 * each error is an error frame and a bus-error alert. The layer models that
 * as:
 *   - every frame never completes: the fake's ACK is taken away
 *     (twai_fake_set_acked), so a normal-mode frame is retransmitted for
 *     ever and the head of the queue reads as TRANSMITTING (TS set), which is
 *     how a frame retrying unacknowledged looks on every attempt (reviewer,
 *     2026-10-20: better than a stall, which reads as awaiting arbitration);
 *   - while a frame waits, one error frame per `retry_us`: bus_error_count
 *     (twai_get_status_info) rises by one and the fake latches
 *     TWAI_ALERT_BUS_ERROR;
 *   - TEC rises 8 per error to 128 (error passive, TWAI_ALERT_ABOVE_ERR_WARN
 *     at 96 and TWAI_ALERT_ERR_PASS at 128, which the layer raises) and stays
 *     there, since an ACK error in error-passive does not raise it; REC stays
 *     0. The status reads them from the fake's counters
 *     (twai_fake_set_error_counters);
 *   - no frame is received (the test delivers none);
 *   - on request, bus-off (ft_bus_off): no more errors, the fake's
 *     TWAI_ALERT_BUS_OFF, TEC 256; it ends at the next install.
 * Every alert, the fake's and the layer's, is latched only if it is enabled
 * when it occurs, and a reconfigure hands back and clears what is pending, as
 * in ESP-IDF v5.4.1.
 * ABORTING A FRAME THAT IS RETRYING is also the layer's model (reviewer,
 * 2026-10-20). Between attempts a frame retrying unacknowledged is not on the
 * wire, so the controller's abort can take it out. The answer-stuck case
 * (nonode_test.c) presents the device's answer in that phase: held with the
 * harness's ft_stall_id(), which reads TS clear (awaiting arbitration), so
 * the fake's abort removes it and it never completes, before or after the
 * nodes return. Every other frame reads as transmitting, as above.
 * The retry period and the alert pattern of sl_silence() and
 * sl_silence_tenth_at() are this layer's choice, not a measurement.
 * sl_silence_measured() replays the pattern the bench row ("a bus with no
 * other node", nonode_bench.py, 2026-10-20 17:04) measured on the wire: one
 * error frame per active_us until TEC reaches 128 (16 errors, error active),
 * then one attempt per passive_us, each still an ACK error and a bus-error
 * alert, TEC staying 128, REC 0, no bus-off (spec 12.4, the emulated row's
 * last sentence; reviewer, 2026-10-20).
 *
 * WHAT IT OBSERVES. Every twai_receive() return (a frame or a timeout) with
 * its virtual time: the device evaluates its trips at the tick right after
 * each return (spec 12.4 row: "once per received frame or per 200 ms receive
 * timeout"; reviewer, 2026-10-20), so these are the check points. Every
 * injected error's time. Every twai_initiate_recovery() call (spec 7: the
 * device starts no bus-off recovery), counted and then passed to the fake.
 *
 * Alert values are ESP-IDF v5.4.1's (components/driver/twai/include/driver/
 * twai.h), named here so the cases' checks read as the spec does.
 */
#ifndef SPEC_LAYER_H
#define SPEC_LAYER_H

#include <stdbool.h>
#include <stdint.h>

#define SL_ALERT_ABOVE_ERR_WARN 0x00000100u
#define SL_ALERT_BUS_ERROR      0x00000200u
#define SL_ALERT_ERR_PASS       0x00001000u
#define SL_ALERT_BUS_OFF        0x00002000u

#define SL_MAX_RX   20000
#define SL_MAX_ERR  20000

typedef struct
{
    int64_t t;      /* virtual time the call returned */
    int     ok;     /* 1: a frame, 0: a timeout or error */
    uint32_t id;    /* the frame's id when ok */
} sl_rx_t;

/* Back to a clean bus: no silence, no errors, counters zeroed, the step hook
 * installed (chaining `next`, may be NULL). Call after ft_reset(), before the
 * driver is installed (ft_set_bus_errors(0) refuses a live count above it). */
void sl_reset(void (*next)(void));

/* No other node from now: every frame never completes, and one error frame
 * per `retry_us` while a frame waits. */
void sl_silence(int64_t retry_us);

/* The same, but the retry period is chosen when the first frame starts waiting,
 * so the 10th error lands `tenth_us` after `t_ref` (a case's placement of the
 * threshold between two check points, whatever the diag cadence). */
void sl_silence_tenth_at(int64_t t_ref, int64_t tenth_us);

/* The bench's pattern: one error per `active_us` while TEC < 128, then one per
 * `passive_us` (error passive). */
void sl_silence_measured(int64_t active_us, int64_t passive_us);

/* The period the layer used (after the first frame waited). */
int64_t sl_retry_us(void);

/* The controller goes bus-off now (ft_bus_off); errors stop. */
void sl_bus_off(void);

/* The other nodes are back: frames are acknowledged and complete again,
 * errors stop. */
void sl_nodes_back(void);

/* What happened. */
int            sl_rx_count(void);
const sl_rx_t *sl_rx(int i);
int            sl_err_count(void);
int64_t        sl_err_time(int i);
uint32_t       sl_tec(void);
int            sl_recovery_calls(void);
uint32_t       sl_alerts_enabled(void);
uint32_t       sl_alerts_delivered(void);   /* every injected alert bit the device read */

/* The step the layer runs at every fake step boundary; exposed so the
 * harness test can drive it without a worker. */
void sl_step(void);

#endif
