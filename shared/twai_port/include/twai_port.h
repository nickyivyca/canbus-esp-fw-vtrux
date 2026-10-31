/*
 * vtrux/twai_port: the driver for the chip's built-in CAN controller (TWAI),
 * behind vtrux/can_port (SPEC.md T1). One port: the chip's TWAI controller 0,
 * on the board CAN port the project names.
 *
 * A project fills a twai_port_config_t (T2), calls twai_port_setup() once at
 * start-up, and registers twai_port_ops with can_port for that port index.
 * It may also call the twai_port_* functions below directly; they are the
 * same port.
 *
 * Every TWAI driver call in a project goes through here: this is the only
 * caller of twai_transmit (T6), twai_read_alerts and twai_reconfigure_alerts
 * (T5), and of the abort command (T8).
 */
#ifndef TWAI_PORT_H
#define TWAI_PORT_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "driver/twai.h"
#include "can_port.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Bus-off recovery (T2). */
typedef enum {
    TWAI_PORT_RECOVERY_NEVER = 0,   /* bus-off lasts until a teardown */
    TWAI_PORT_RECOVERY_AUTO,        /* recover, then start again */
} twai_port_recovery_t;

/* What a failed bring-up does (T2, T4). */
typedef enum {
    TWAI_PORT_INSTALL_FAIL_ABORT = 0,   /* restart, as ESP_ERROR_CHECK does */
    TWAI_PORT_INSTALL_FAIL_REPORT,      /* return the error; stay uninstalled */
} twai_port_install_fail_t;

/* The step of a bring-up that failed (T4). */
typedef enum {
    TWAI_PORT_STEP_NONE = 0,
    TWAI_PORT_STEP_PINS,        /* the board would not give the port's pins */
    TWAI_PORT_STEP_INSTALL,
    TWAI_PORT_STEP_START,
    TWAI_PORT_STEP_ALERTS,
} twai_port_step_t;

/* The acceptance filter of one install. */
typedef struct {
    uint32_t acceptance_code;
    uint32_t acceptance_mask;
    bool single_filter;
} twai_port_filter_t;

/*
 * What a project chooses (T2). The timing, the mode and the filter are the
 * starting settings; can_port's stock calls replace them for port 0 at each
 * can_enable() (can_port C3), and twai_port_set_mode() changes the mode.
 */
typedef struct {
    int board_port;                     /* the board's CAN port (shared/boards B5) */
    twai_timing_config_t timing;
    can_port_mode_t mode;
    twai_port_filter_t filter;
    bool single_shot;                   /* every send is one attempt */
    uint32_t rx_queue_len;
    uint32_t tx_queue_len;
    twai_port_recovery_t recovery;
    bool drop_rtr;                      /* remote frames are not handed up */
    twai_port_install_fail_t on_install_fail;
    /*
     * Run before every teardown, before the controller's state is read (T3).
     * It may block; it must not call back into the port.
     */
    void (*pre_teardown)(void *ctx);
    /*
     * Run before every install, with the filter that install will use, which
     * it may replace for that install (T2). It must not call back into the
     * port except twai_port_status().
     */
    void (*install_hook)(void *ctx, twai_port_filter_t *filter);
    void *hook_ctx;
} twai_port_config_t;

/* Events the alert poll passes up (T5): bits, never counts. */
#define TWAI_PORT_EV_TX_FAILED      (1u << 0)   /* held until taken */
#define TWAI_PORT_EV_TX_SUCCESS     (1u << 1)
#define TWAI_PORT_EV_BUS_ERROR      (1u << 2)
#define TWAI_PORT_EV_BUS_OFF        (1u << 3)   /* entry into bus-off */
#define TWAI_PORT_EV_RX_QUEUE_FULL  (1u << 4)

/* The transmit buffer's state (T8). */
typedef enum {
    TWAI_PORT_BUF_EMPTY = 0,
    TWAI_PORT_BUF_TRANSMITTING,     /* TS = 1: arbitration field or data phase */
    TWAI_PORT_BUF_AWAITING,         /* holds a frame waiting for arbitration */
    TWAI_PORT_BUF_UNSUPPORTED,      /* no measured register file for this chip */
} twai_port_buf_t;

/* Fill `cfg` with the defaults: board port 0, 500 kbit/s, normal mode,
 * accept-all, queues of 64 and 32, never recover, keep remote frames, abort
 * on an install failure, no hooks. */
void twai_port_config_defaults(twai_port_config_t *cfg);

/*
 * Take the project's choices, once, before anything else calls the port.
 * ESP_ERR_INVALID_STATE if the port is up; ESP_ERR_INVALID_ARG for a zero
 * queue depth or an unknown mode.
 */
esp_err_t twai_port_setup(const twai_port_config_t *cfg);

/*
 * Bring up (T4): run the install hook, install, start, enable the alerts,
 * take the transceiver out of standby. ESP_OK if already up. On a failure,
 * with abort chosen it does not return; with report it returns the error,
 * leaves the driver uninstalled and records the step (twai_port_last_step()).
 */
esp_err_t twai_port_install(void);

/*
 * Tear down (T3): run the pre-teardown hook, then read the controller's
 * state. While it is recovering from bus-off: refused, ESP_ERR_INVALID_STATE,
 * still installed. Otherwise the transceiver to standby, the queues counted
 * (T4), stop if running, uninstall. ESP_OK if it was already down, after the
 * hook. The port reports itself down only once the uninstall has succeeded.
 */
esp_err_t twai_port_teardown(void);

bool twai_port_is_installed(void);

/* The step the last failed bring-up failed at, or NONE. */
twai_port_step_t twai_port_last_step(void);

/*
 * A mode change (T4): nothing if it is the mode in effect; recorded for the
 * next install if down; otherwise a teardown, then the mode recorded, then an
 * install. A refused teardown returns its error with the record and the
 * controller left in the old mode.
 */
esp_err_t twai_port_set_mode(can_port_mode_t mode);
can_port_mode_t twai_port_mode(void);

/*
 * New timing, mode and filter, applied as a mode change is: nothing if they
 * are the settings in effect, a teardown and an install if up and different.
 */
esp_err_t twai_port_configure(const twai_timing_config_t *timing,
                              can_port_mode_t mode,
                              const twai_port_filter_t *filter);

/* Take one frame, waiting at most `wait` (T9). */
esp_err_t twai_port_receive(twai_message_t *out, TickType_t wait);

/*
 * The only twai_transmit (T6). Waits at most `wait` for queue space (0:
 * enqueue or fail). A refusal with the queue full counts tx_dropped, any
 * other tx_refused.
 */
esp_err_t twai_port_send(const twai_message_t *m, TickType_t wait);

/*
 * The one alert reader (T5): read and fold the alerts, return the events
 * they raised. Non-blocking; callable before the project's loop starts. With
 * automatic recovery, a bus-off entry counts the TX queue and starts the
 * recovery here.
 */
uint32_t twai_port_poll_alerts(void);

/* True, once, if a transmit-failed event is held; clears it. */
bool twai_port_take_tx_failed(void);

/* The alert poll, then, with automatic recovery, a restart once recovered.
 * Non-blocking. */
void twai_port_service(void);

/* One status read (T7). Not installed is not an error: the levels read down
 * and the counts are as they stand. */
esp_err_t twai_port_status(can_port_status_t *out);

/* Discard the frames received and not yet taken. */
void twai_port_flush_rx(void);

/*
 * T8, register level, in the chip's file. Neither touches the interrupt
 * register. twai_port_abort_if_awaiting() reads the buffer's state and, only
 * if AWAITING, issues the abort command, in one critical section; it is in
 * IRAM and safe from a panic handler. Both return UNSUPPORTED on a chip with
 * no measured file, and then do nothing.
 */
twai_port_buf_t twai_port_buf_state(void);
IRAM_ATTR twai_port_buf_t twai_port_abort_if_awaiting(void);

/* The port as a can_port backend (can_port C1); its context is NULL. */
extern const can_port_ops_t twai_port_ops;

#ifdef __cplusplus
}
#endif

#endif
