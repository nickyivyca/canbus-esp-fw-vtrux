/*
 * The scripted stub of the TWAI driver API, the board calls and the tick
 * (SPEC.md T11; README.md's unit/ row). Each case sets what every call returns
 * and reads back the calls the port made, in order. The stub has no states,
 * raises no alerts and does no arbitration of its own: a status read returns
 * the next scripted status, whatever was called before it.
 */
#ifndef STUB_H
#define STUB_H

#include <setjmp.h>
#include "driver/twai.h"
#include "board.h"
#include "hal/twai_ll.h"

#define STUB_MAX_CALLS   64
#define STUB_MAX_STATUS  8
#define STUB_MAX_RX      8

typedef struct {
    /* Returns. */
    esp_err_t install_ret, uninstall_ret, start_ret, stop_ret;
    esp_err_t alerts_ret, recovery_ret, clear_rx_ret;
    esp_err_t transmit_ret;
    esp_err_t pins_ret;
    board_twai_pins_t pins;
    esp_err_t read_alerts_ret;
    uint32_t alerts;                /* returned once, then 0 */
    /* Status reads: each returns the next, and the last repeats. */
    twai_status_info_t status[STUB_MAX_STATUS];
    esp_err_t status_ret[STUB_MAX_STATUS];
    int n_status, status_i;
    /* Receives: each returns the next; past the end, ESP_ERR_TIMEOUT. */
    twai_message_t rx[STUB_MAX_RX];
    esp_err_t rx_ret[STUB_MAX_RX];
    int n_rx, rx_i;
    TickType_t tick;                /* xTaskGetTickCount(); rx_tick_step added per receive */
    TickType_t rx_tick_step;

    /* What the port called. */
    const char *calls[STUB_MAX_CALLS];
    int n_calls;
    twai_general_config_t g;
    twai_timing_config_t t;
    twai_filter_config_t f;
    uint32_t alerts_enabled;
    twai_message_t tx;
    TickType_t tx_wait;
    TickType_t rx_waits[STUB_MAX_RX];
    bool standby[STUB_MAX_CALLS];   /* each standby call's `on` */
    int n_standby;
    int board_port;
    /* The abort path: ESP_ERROR_CHECK on a failure jumps here. */
    jmp_buf *abort_to;
    esp_err_t abort_err;
} stub_t;

extern stub_t stub;

/* Reset the stub: every call succeeds; one RUNNING status with no counts. */
void stub_reset(void);
/* Script the status reads, in order. */
void stub_status(int n, const twai_status_info_t *seq);
/* The calls made, joined with ' ', for comparing in a case. */
const char *stub_calls(void);
/* How many times `name` was called. */
int stub_count(const char *name);

#endif
