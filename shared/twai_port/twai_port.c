/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * vtrux/twai_port: the TWAI driver behind can_port (SPEC.md T1 to T9).
 *
 * Made at port step 1b from gen-inhibit's main/can.c and the TWAI driver
 * calls in its gen_inhibit.c (gen-inhibit/main 4020bc8, both last changed at
 * c48e05b): the install and teardown, the board's pins and standby line, the
 * one twai_transmit, the alert read. The register-level abort and buffer state
 * are in the chip's file, twai_port_ll_<chip>.c (T8).
 *
 * WHO CALLS WHAT, AND WHEN. Install, teardown, set_mode and configure change
 * the driver's installation and are not reentrant: the project calls them
 * from one task at a time (gen-inhibit: its worker, or another task once its
 * pre-teardown hook has parked the worker). Receive, send, the alert poll,
 * service and the status read may run from any task while it is installed.
 *
 * THE COUNTERS (T7) are the driver's per-install totals, folded in as deltas.
 * The driver's own counts restart at 0 with each install, so a total taken
 * from them directly would go backwards at every reinstall; the port keeps a
 * running total and the driver's value at its last fold. A fold is applied
 * only for the install it was read from (s_gen), so a status read that raced
 * a reinstall adds nothing, and within one install only a rise is taken, so a
 * stale read that lost the race to a newer one adds nothing either.
 *
 * THE PORT'S LOCK is a spinlock held for a few assignments, never across a
 * driver call: twai_get_status_info() takes the driver's own spinlock, and
 * nesting the two would give the port a lock ordering it otherwise does not
 * have. It waits on no task (T9).
 */
#include "twai_port.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "board.h"

static const char *TAG = "twai_port";

/*
 * The alerts every install enables (T5): the events the poll passes up. The
 * counts come from the status read, never from these (T5: an alert is a bit,
 * and one may cover several occurrences).
 */
#define TWAI_PORT_ALERTS    (TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED      \
                             | TWAI_ALERT_BUS_ERROR | TWAI_ALERT_BUS_OFF       \
                             | TWAI_ALERT_RX_QUEUE_FULL)

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

static twai_port_config_t s_cfg;
static bool s_setup;

/* The settings in effect: the next install's, and the current one's. */
static twai_timing_config_t s_timing;
static can_port_mode_t s_mode;
static twai_port_filter_t s_filter;

static volatile bool s_installed;
static twai_port_step_t s_last_step;

/* Changes at every install and every teardown; see THE COUNTERS above. */
static uint32_t s_gen;

/* The driver's per-install values at the last fold. */
static struct {
    uint32_t rx_missed;
    uint32_t rx_overrun;
    uint32_t tx_failed;
    uint32_t bus_error;
    uint32_t arb_lost;
} s_raw;

/* The running counts, and the bus-off level. */
static can_port_status_t s_count;

static bool s_tx_failed_held;

/* ----------------------------------------------------------- helpers -- */

static bool mode_to_twai(can_port_mode_t mode, twai_mode_t *out)
{
    switch (mode)
    {
    case CAN_PORT_MODE_NORMAL:           *out = TWAI_MODE_NORMAL;      return true;
    case CAN_PORT_MODE_LISTEN_ONLY:      *out = TWAI_MODE_LISTEN_ONLY; return true;
    case CAN_PORT_MODE_NO_ACK_SELF_TEST: *out = TWAI_MODE_NO_ACK;      return true;
    }
    return false;
}

static can_port_state_t state_from_twai(twai_state_t s)
{
    switch (s)
    {
    case TWAI_STATE_STOPPED:    return CAN_PORT_STATE_STOPPED;
    case TWAI_STATE_RUNNING:    return CAN_PORT_STATE_RUNNING;
    case TWAI_STATE_BUS_OFF:    return CAN_PORT_STATE_BUS_OFF;
    case TWAI_STATE_RECOVERING: return CAN_PORT_STATE_RECOVERING;
    default:                    return CAN_PORT_STATE_DOWN;
    }
}

/* Add the rise of a driver count to a running count. Lock held. */
static void take_rise(uint32_t raw, uint32_t *base, uint32_t *total)
{
    if (raw > *base)
    {
        *total += raw - *base;
        *base = raw;
    }
}

/* Count a bus-off entry once per episode. Lock held. */
static void note_bus_off(void)
{
    if (!s_count.bus_off)
    {
        s_count.bus_off_entries++;
        s_count.bus_off = true;
    }
}

/*
 * Fold one status read into the counts, for the install it was read from.
 * Lock not held; takes it.
 */
static void fold(const twai_status_info_t *st, uint32_t gen)
{
    taskENTER_CRITICAL(&s_lock);
    if (gen == s_gen && s_installed)
    {
        take_rise(st->rx_missed_count, &s_raw.rx_missed,
                  &s_count.rx_lost_queue_full);
        take_rise(st->rx_overrun_count, &s_raw.rx_overrun,
                  &s_count.rx_lost_overrun);
        take_rise(st->tx_failed_count, &s_raw.tx_failed, &s_count.tx_failed);
        take_rise(st->bus_error_count, &s_raw.bus_error, &s_count.bus_errors);
        take_rise(st->arb_lost_count, &s_raw.arb_lost, &s_count.arb_lost);
        s_count.rx_lost = s_count.rx_lost_queue_full + s_count.rx_lost_overrun;
        if (st->msgs_to_rx > s_count.rx_high_water)
        {
            s_count.rx_high_water = st->msgs_to_rx;
        }
        if (st->state == TWAI_STATE_BUS_OFF
            || st->state == TWAI_STATE_RECOVERING)
        {
            note_bus_off();
        }
        else if (st->state == TWAI_STATE_RUNNING)
        {
            s_count.bus_off = false;
        }
    }
    taskEXIT_CRITICAL(&s_lock);
}

/* A status read, folded in. Returns the driver's result. */
static esp_err_t read_status(twai_status_info_t *st)
{
    taskENTER_CRITICAL(&s_lock);
    const uint32_t gen = s_gen;
    taskEXIT_CRITICAL(&s_lock);
    const esp_err_t err = twai_get_status_info(st);
    if (err == ESP_OK)
    {
        fold(st, gen);
    }
    return err;
}

static void count_add(uint32_t *field, uint32_t n)
{
    taskENTER_CRITICAL(&s_lock);
    *field += n;
    taskEXIT_CRITICAL(&s_lock);
}

/* ------------------------------------------------------------- setup -- */

void twai_port_config_defaults(twai_port_config_t *cfg)
{
    const twai_timing_config_t t500 = TWAI_TIMING_CONFIG_500KBITS();
    memset(cfg, 0, sizeof(*cfg));
    cfg->board_port = 0;
    cfg->timing = t500;
    cfg->mode = CAN_PORT_MODE_NORMAL;
    cfg->filter.acceptance_code = 0;
    cfg->filter.acceptance_mask = 0xFFFFFFFF;
    cfg->filter.single_filter = true;
    cfg->single_shot = false;
    cfg->rx_queue_len = 64;
    cfg->tx_queue_len = 32;
    cfg->recovery = TWAI_PORT_RECOVERY_NEVER;
    cfg->drop_rtr = false;
    cfg->on_install_fail = TWAI_PORT_INSTALL_FAIL_ABORT;
}

esp_err_t twai_port_setup(const twai_port_config_t *cfg)
{
    twai_mode_t m;
    if (cfg == NULL || cfg->rx_queue_len == 0 || !mode_to_twai(cfg->mode, &m))
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_installed)
    {
        return ESP_ERR_INVALID_STATE;
    }
    s_cfg = *cfg;
    s_timing = cfg->timing;
    s_mode = cfg->mode;
    s_filter = cfg->filter;
    s_setup = true;
    return ESP_OK;
}

/* ----------------------------------------------------- install, T4 ---- */

/* A failed step: abort if the project chose that, otherwise report it. */
static esp_err_t install_failed(twai_port_step_t step, esp_err_t err)
{
    s_last_step = step;
    ESP_LOGE(TAG, "bring-up failed at %s: %s",
             step == TWAI_PORT_STEP_PINS    ? "pins"
             : step == TWAI_PORT_STEP_INSTALL ? "install"
             : step == TWAI_PORT_STEP_START   ? "start"
                                              : "alerts",
             esp_err_to_name(err));
    if (s_cfg.on_install_fail == TWAI_PORT_INSTALL_FAIL_ABORT)
    {
        /* As can.c's ESP_ERROR_CHECK did: the message, then a restart. */
        ESP_ERROR_CHECK(err);
    }
    return err;
}

esp_err_t twai_port_install(void)
{
    if (!s_setup)
    {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_installed)
    {
        return ESP_OK;
    }
    s_last_step = TWAI_PORT_STEP_NONE;

    /*
     * The install hook first, with this install's filter (T2): gen-inhibit
     * forces accept-all here while it owns the bus (its spec 5.1 item 2),
     * which can.c did inline.
     */
    twai_port_filter_t filter = s_filter;
    if (s_cfg.install_hook != NULL)
    {
        s_cfg.install_hook(s_cfg.hook_ctx, &filter);
    }

    twai_mode_t mode;
    if (!mode_to_twai(s_mode, &mode))
    {
        return install_failed(TWAI_PORT_STEP_INSTALL, ESP_ERR_INVALID_ARG);
    }

    /* The board's pins for its port, just before the install (B5). */
    board_twai_pins_t pins;
    esp_err_t err = board_twai_pins(s_cfg.board_port, &pins);
    if (err != ESP_OK)
    {
        return install_failed(TWAI_PORT_STEP_PINS, err);
    }

    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(pins.tx, pins.rx, mode);
    g.rx_queue_len = s_cfg.rx_queue_len;
    g.tx_queue_len = s_cfg.tx_queue_len;
    const twai_filter_config_t f = {
        .acceptance_code = filter.acceptance_code,
        .acceptance_mask = filter.acceptance_mask,
        .single_filter = filter.single_filter,
    };

    err = twai_driver_install(&g, &s_timing, &f);
    if (err != ESP_OK)
    {
        return install_failed(TWAI_PORT_STEP_INSTALL, err);
    }
    err = twai_start();
    if (err != ESP_OK)
    {
        (void)twai_driver_uninstall();
        return install_failed(TWAI_PORT_STEP_START, err);
    }
    /*
     * T5: from every install, whoever is using the port, so the bus-off and
     * loss events are there for the stock path too. Before 1b can.c installed
     * with none and gen-inhibit enabled TX success and failure when it armed.
     */
    err = twai_reconfigure_alerts(TWAI_PORT_ALERTS, NULL);
    if (err != ESP_OK)
    {
        (void)twai_stop();
        (void)twai_driver_uninstall();
        return install_failed(TWAI_PORT_STEP_ALERTS, err);
    }

    taskENTER_CRITICAL(&s_lock);
    s_gen++;
    memset(&s_raw, 0, sizeof(s_raw));
    s_count.bus_off = false;
    s_count.bring_ups++;
    s_tx_failed_held = false;
    s_installed = true;
    taskEXIT_CRITICAL(&s_lock);

    /* Normal operation. "Not supported" is a board with no standby line. */
    (void)board_can_standby(s_cfg.board_port, false);
    return ESP_OK;
}

/* ---------------------------------------------------- teardown, T3 ---- */

esp_err_t twai_port_teardown(void)
{
    /*
     * The pre-teardown hook first, every time (T3, can_port C5): gen-inhibit's
     * worker quiesce, which can.c's can_disable() called inline. Even with the
     * port down, because the stock disable runs it whatever the bus state.
     */
    if (s_cfg.pre_teardown != NULL)
    {
        s_cfg.pre_teardown(s_cfg.hook_ctx);
    }
    if (!s_installed)
    {
        return ESP_OK;
    }

    twai_status_info_t st;
    esp_err_t err = read_status(&st);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "teardown: the controller's state will not read: %s",
                 esp_err_to_name(err));
        return err;
    }
    if (st.state == TWAI_STATE_RECOVERING)
    {
        /*
         * Neither stop nor uninstall is allowed while recovering, and the 128
         * bus-free occurrences cannot be hurried. Refused, still installed and
         * saying so: can.c marked the bus off here with the driver still
         * installed, and the next install then failed on it.
         */
        ESP_LOGW(TAG, "teardown refused: the controller is recovering from bus-off");
        return ESP_ERR_INVALID_STATE;
    }

    (void)board_can_standby(s_cfg.board_port, true);

    /*
     * THE QUEUES A TEARDOWN DISCARDS (T4), counted just before they go.
     * twai_stop() empties the TX queue and twai_driver_uninstall() the RX
     * queue (IDF v5.4.1 twai.c, twai_stop_v2() and the driver object's free),
     * so TX is read before the stop and RX after it, when the stopped
     * controller can receive no more. Booked only once the uninstall has
     * succeeded.
     */
    uint32_t tx_gone = st.msgs_to_tx;
    uint32_t rx_gone = st.msgs_to_rx;
    if (st.state == TWAI_STATE_RUNNING)
    {
        err = twai_stop();
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "teardown: stop refused: %s", esp_err_to_name(err));
            (void)board_can_standby(s_cfg.board_port, false);
            return err;
        }
        if (read_status(&st) == ESP_OK)
        {
            rx_gone = st.msgs_to_rx;
        }
    }

    err = twai_driver_uninstall();
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "teardown: uninstall refused: %s", esp_err_to_name(err));
        return err;
    }

    taskENTER_CRITICAL(&s_lock);
    s_count.tx_discarded_teardown += tx_gone;
    s_count.rx_discarded_teardown += rx_gone;
    s_count.bus_off = false;
    s_installed = false;
    s_gen++;
    taskEXIT_CRITICAL(&s_lock);
    return ESP_OK;
}

bool twai_port_is_installed(void)
{
    return s_installed;
}

twai_port_step_t twai_port_last_step(void)
{
    return s_last_step;
}

/* --------------------------------------------- mode and settings, T4 -- */

esp_err_t twai_port_set_mode(can_port_mode_t mode)
{
    twai_mode_t m;
    if (!mode_to_twai(mode, &m))
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (mode == s_mode)
    {
        return ESP_OK;
    }
    if (!s_installed)
    {
        s_mode = mode;
        return ESP_OK;
    }
    /* The new mode is recorded only once the teardown has succeeded. */
    const esp_err_t err = twai_port_teardown();
    if (err != ESP_OK)
    {
        return err;
    }
    s_mode = mode;
    return twai_port_install();
}

can_port_mode_t twai_port_mode(void)
{
    return s_mode;
}

static bool same_settings(const twai_timing_config_t *timing,
                          can_port_mode_t mode,
                          const twai_port_filter_t *filter)
{
    return timing->clk_src == s_timing.clk_src
           && timing->quanta_resolution_hz == s_timing.quanta_resolution_hz
           && timing->brp == s_timing.brp
           && timing->tseg_1 == s_timing.tseg_1
           && timing->tseg_2 == s_timing.tseg_2
           && timing->sjw == s_timing.sjw
           && timing->triple_sampling == s_timing.triple_sampling
           && mode == s_mode
           && filter->acceptance_code == s_filter.acceptance_code
           && filter->acceptance_mask == s_filter.acceptance_mask
           && filter->single_filter == s_filter.single_filter;
}

esp_err_t twai_port_configure(const twai_timing_config_t *timing,
                              can_port_mode_t mode,
                              const twai_port_filter_t *filter)
{
    twai_mode_t m;
    if (timing == NULL || filter == NULL || !mode_to_twai(mode, &m))
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (same_settings(timing, mode, filter))
    {
        return ESP_OK;
    }
    const bool was_up = s_installed;
    if (was_up)
    {
        const esp_err_t err = twai_port_teardown();
        if (err != ESP_OK)
        {
            return err;
        }
    }
    s_timing = *timing;
    s_mode = mode;
    s_filter = *filter;
    return was_up ? twai_port_install() : ESP_OK;
}

/* -------------------------------------------------- receive and send -- */

esp_err_t twai_port_receive(twai_message_t *out, TickType_t wait)
{
    if (out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_installed)
    {
        return ESP_ERR_INVALID_STATE;
    }
    /*
     * At most `wait` in all (T9), remote frames dropped included: a dropped
     * one shortens what is left of the wait rather than starting it again.
     */
    const TickType_t start = xTaskGetTickCount();
    TickType_t left = wait;
    for (;;)
    {
        const esp_err_t err = twai_receive(out, left);
        if (err != ESP_OK)
        {
            return err;
        }
        if (!(s_cfg.drop_rtr && out->rtr))
        {
            count_add(&s_count.rx_frames, 1);
            return ESP_OK;
        }
        if (wait != portMAX_DELAY)
        {
            const TickType_t spent = xTaskGetTickCount() - start;
            left = (spent >= wait) ? 0 : wait - spent;
        }
    }
}

esp_err_t twai_port_send(const twai_message_t *m, TickType_t wait)
{
    if (m == NULL)
    {
        count_add(&s_count.tx_refused, 1);
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_installed)
    {
        count_add(&s_count.tx_refused, 1);
        return ESP_ERR_INVALID_STATE;
    }
    twai_message_t tx = *m;
    if (s_cfg.single_shot)
    {
        tx.ss = 1;
    }
    if (s_mode == CAN_PORT_MODE_NO_ACK_SELF_TEST)
    {
        tx.self = 1;    /* the chip's loopback: each frame sent is received */
    }
    /*
     * THE ONLY twai_transmit (T6). Waits at most what the caller gives, for
     * queue space only, never for the wire.
     */
    const esp_err_t err = twai_transmit(&tx, wait);
    if (err == ESP_OK)
    {
        count_add(&s_count.tx_frames, 1);
    }
    else if (err == ESP_ERR_TIMEOUT || err == ESP_FAIL)
    {
        /* The queue full, or, with no queue, the one buffer busy. */
        count_add(&s_count.tx_dropped, 1);
    }
    else
    {
        count_add(&s_count.tx_refused, 1);
    }
    return err;
}

void twai_port_flush_rx(void)
{
    if (s_installed)
    {
        (void)twai_clear_receive_queue();
    }
}

/* ---------------------------------------------------------- alerts, T5 -- */

uint32_t twai_port_poll_alerts(void)
{
    if (!s_installed)
    {
        return 0;
    }
    uint32_t alerts = 0;
    if (twai_read_alerts(&alerts, 0) != ESP_OK || alerts == 0)
    {
        return 0;
    }

    uint32_t ev = 0;
    if (alerts & TWAI_ALERT_TX_SUCCESS)    { ev |= TWAI_PORT_EV_TX_SUCCESS; }
    if (alerts & TWAI_ALERT_BUS_ERROR)     { ev |= TWAI_PORT_EV_BUS_ERROR; }
    if (alerts & TWAI_ALERT_RX_QUEUE_FULL) { ev |= TWAI_PORT_EV_RX_QUEUE_FULL; }
    if (alerts & TWAI_ALERT_TX_FAILED)
    {
        ev |= TWAI_PORT_EV_TX_FAILED;
        taskENTER_CRITICAL(&s_lock);
        s_tx_failed_held = true;    /* until the project takes it */
        taskEXIT_CRITICAL(&s_lock);
    }
    if (alerts & TWAI_ALERT_BUS_OFF)
    {
        ev |= TWAI_PORT_EV_BUS_OFF;
        taskENTER_CRITICAL(&s_lock);
        note_bus_off();
        taskEXIT_CRITICAL(&s_lock);
        if (s_cfg.recovery == TWAI_PORT_RECOVERY_AUTO)
        {
            /*
             * Count what recovery is about to discard, before it does (T7):
             * twai_initiate_recovery() empties the TX queue, and those frames
             * were queued, so they are in no other count.
             */
            twai_status_info_t st;
            if (read_status(&st) == ESP_OK)
            {
                count_add(&s_count.tx_discarded_bus_off, st.msgs_to_tx);
            }
            const esp_err_t err = twai_initiate_recovery();
            if (err != ESP_OK)
            {
                ESP_LOGW(TAG, "bus-off recovery would not start: %s",
                         esp_err_to_name(err));
            }
        }
    }
    return ev;
}

bool twai_port_take_tx_failed(void)
{
    taskENTER_CRITICAL(&s_lock);
    const bool held = s_tx_failed_held;
    s_tx_failed_held = false;
    taskEXIT_CRITICAL(&s_lock);
    return held;
}

void twai_port_service(void)
{
    if (!s_installed)
    {
        return;
    }
    (void)twai_port_poll_alerts();
    twai_status_info_t st;
    if (read_status(&st) != ESP_OK)
    {
        return;
    }
    /* Recovery done: the controller waits STOPPED until it is started. */
    if (s_cfg.recovery == TWAI_PORT_RECOVERY_AUTO
        && st.state == TWAI_STATE_STOPPED)
    {
        if (twai_start() == ESP_OK)
        {
            taskENTER_CRITICAL(&s_lock);
            s_count.bus_off = false;    /* the episode is over */
            taskEXIT_CRITICAL(&s_lock);
        }
    }
}

/* ---------------------------------------------------------- status, T7 -- */

esp_err_t twai_port_status(can_port_status_t *out)
{
    if (out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    twai_status_info_t st;
    bool have = false;
    if (s_installed)
    {
        const esp_err_t err = read_status(&st);
        if (err != ESP_OK)
        {
            return err;
        }
        have = true;
    }

    taskENTER_CRITICAL(&s_lock);
    *out = s_count;
    taskEXIT_CRITICAL(&s_lock);

    out->rx_lost_is_bound = false;  /* the driver counts per frame */
    out->rx_queue_len = s_cfg.rx_queue_len;
    if (have)
    {
        out->state = state_from_twai(st.state);
        out->tx_pending = st.msgs_to_tx;
        out->rx_waiting = st.msgs_to_rx;
        out->tec = st.tx_error_counter;
        out->rec = st.rx_error_counter;
    }
    else
    {
        out->state = CAN_PORT_STATE_DOWN;
        out->tx_pending = 0;
        out->rx_waiting = 0;
        out->tec = 0;
        out->rec = 0;
    }
    return ESP_OK;
}

/* ------------------------------------------------- the can_port backend -- */

static esp_err_t op_up(void *ctx)      { (void)ctx; return twai_port_install(); }
static esp_err_t op_down(void *ctx)    { (void)ctx; return twai_port_teardown(); }
static bool op_is_up(void *ctx)        { (void)ctx; return twai_port_is_installed(); }
static void op_service(void *ctx)      { (void)ctx; twai_port_service(); }
static void op_flush_rx(void *ctx)     { (void)ctx; twai_port_flush_rx(); }

static esp_err_t op_set_mode(void *ctx, can_port_mode_t mode)
{
    (void)ctx;
    return twai_port_set_mode(mode);
}

/* The stock settings, for the next bring-up; refused while up unless they
 * are the ones in effect (can_port.h). */
static esp_err_t op_configure(void *ctx, const can_port_settings_t *s)
{
    (void)ctx;
    if (s == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    const twai_port_filter_t f = {
        .acceptance_code = s->acceptance_code,
        .acceptance_mask = s->acceptance_mask,
        .single_filter = s->single_filter,
    };
    if (s_installed && !same_settings(&s->timing, s->mode, &f))
    {
        return ESP_ERR_INVALID_STATE;
    }
    return twai_port_configure(&s->timing, s->mode, &f);
}

static esp_err_t op_receive(void *ctx, twai_message_t *out, TickType_t wait)
{
    (void)ctx;
    return twai_port_receive(out, wait);
}

static esp_err_t op_send(void *ctx, const twai_message_t *m, TickType_t wait)
{
    (void)ctx;
    return twai_port_send(m, wait);
}

static esp_err_t op_status(void *ctx, can_port_status_t *out)
{
    (void)ctx;
    return twai_port_status(out);
}

const can_port_ops_t twai_port_ops = {
    .name = "TWAI",
    .up = op_up,
    .down = op_down,
    .is_up = op_is_up,
    .set_mode = op_set_mode,
    .configure = op_configure,
    .receive = op_receive,
    .send = op_send,
    .service = op_service,
    .status = op_status,
    .flush_rx = op_flush_rx,
};
