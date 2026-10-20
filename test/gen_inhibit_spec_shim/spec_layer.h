/*
 * spec_layer -- the tester's fault behaviour ON TOP OF the implementor's fakes
 * (test/gen_inhibit_shim/fake_twai.c), for the spec 12.4 rows that judge the
 * product: "a bus with no other node, emulated" (added 2026-10-20, user).
 *
 * WHY A LAYER AND NOT AN EDIT. The fakes are the implementor's, and a test
 * that judges product code is not written by the implementor (reviewer,
 * 2026-10-20). So nothing here edits them: the layer reaches the driver calls
 * through link-time wraps (-Wl,--wrap=twai_...) and the fakes' own public
 * controls (ft_set_air_time, ft_set_bus_errors, ft_bus_off, ft_set_step_hook).
 *
 * WHAT IT MODELS, AND THAT IT IS A GUESS. With no other node on the bus,
 * nothing acknowledges, so a frame the device queues never completes: the
 * controller retransmits it at once, each attempt ends in an ACK error, and
 * each error is an error frame and a bus-error alert. The layer models that
 * as:
 *   - every frame never completes: the fake's air time is made too long to
 *     elapse, so the head of the queue reads as TRANSMITTING (TS set), which
 *     is how a frame retrying unacknowledged looks on every attempt (reviewer,
 *     2026-10-20: better than ft_stall, which reads as awaiting arbitration);
 *   - while a frame waits, one error frame per `retry_us`: bus_error_count
 *     (twai_get_status_info) rises by one and TWAI_ALERT_BUS_ERROR is latched;
 *   - TEC rises 8 per error to 128 (error passive, TWAI_ALERT_ABOVE_ERR_WARN
 *     at 96 and TWAI_ALERT_ERR_PASS at 128) and stays there, since an ACK error
 *     in error-passive does not raise it; REC stays 0;
 *   - no frame is received (the test delivers none);
 *   - on request, bus-off (ft_bus_off): no more errors, TWAI_ALERT_BUS_OFF.
 * The retry period and the alert pattern are this layer's choice, not a
 * measurement. Once the bench row ("a bus with no other node") has measured
 * the real alert pattern, the layer replays that pattern (spec 12.4, the
 * emulated row's last sentence).
 *
 * WHAT IT OBSERVES. Every twai_receive() return (a frame or a timeout) with
 * its virtual time: the device evaluates its trips at the tick right after
 * each return (spec 12.4 row: "once per received frame or per 200 ms receive
 * timeout"; reviewer, 2026-10-20), so these are the check points. Every
 * injected error's time. Every twai_initiate_recovery() call (spec 7: the
 * device starts no bus-off recovery).
 *
 * Alert values are ESP-IDF v5.4.1's (components/driver/twai/include/driver/
 * twai.h); the implementor's mock header names only the four it uses.
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
 * installed (chaining `next`, may be NULL). Call after ft_reset(). */
void sl_reset(void (*next)(void));

/* No other node from now: every frame never completes, and one error frame
 * per `retry_us` while a frame waits. */
void sl_silence(int64_t retry_us);

/* The same, but the retry period is chosen when the first frame starts waiting,
 * so the 10th error lands `tenth_us` after `t_ref` (a case's placement of the
 * threshold between two check points, whatever the diag cadence). */
void sl_silence_tenth_at(int64_t t_ref, int64_t tenth_us);

/* The period the layer used (after the first frame waited). */
int64_t sl_retry_us(void);

/* The controller goes bus-off now (ft_bus_off); errors stop. */
void sl_bus_off(void);

/* The other nodes are back: frames complete again (the fake's default air
 * time), errors stop. */
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

/* Not in the implementor's mock: the layer defines it, to count calls. */
#include "esp_err.h"
esp_err_t twai_initiate_recovery(void);

#endif
