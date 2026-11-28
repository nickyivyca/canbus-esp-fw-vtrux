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
 * are in the chip's file, twai_port_ll_<chip>.c (T8). The kept-driver switch
 * (T14 to T18) is below, with its register steps in the same file per chip
 * (twai_port_ll.h).
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
#include "twai_port_ll.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
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

/*
 * THE KEPT-DRIVER SWITCH (T14 to T18), when the project chose it.
 *
 * s_tx_block: the port's own flag that refuses every send in listen-only
 * (T14). The driver was installed in normal mode, so it would take them.
 *
 * THE RECORD OF FRAMES SENT (T17). The driver's TX queue cannot be read, so
 * the port keeps the frames it queued, with their tags, in a ring of
 * TWAI_PORT_SWITCH_HELD_MAX. The driver sends in queue order, and its status
 * gives how many it still holds (msgs_to_tx, the one in the buffer
 * included), so the frames held at a switch are the newest msgs_to_tx in the
 * ring. That holds while sends come from one task, so the ring's order is
 * the driver's (twai_port.h). s_ring_n counts the frames recorded since the
 * install; a ring slot is reused only after tx_queue_len + 1 newer sends, by
 * which time the driver no longer holds it.
 *
 * s_tx_failed_skip: transmit failures the driver will count for frames the
 * switch aborted, which T17 counts in tx_discarded_switch instead, so each
 * is counted once.
 *
 * s_ev_pending: events raised by the switch's own alert polls (T18), kept
 * for the project's next twai_port_poll_alerts().
 */
static volatile bool s_tx_block;
static struct {
    twai_message_t frame;
    uint32_t tag;
} s_ring[TWAI_PORT_SWITCH_HELD_MAX];
static uint32_t s_ring_n;
static uint32_t s_tx_failed_skip;
static uint32_t s_ev_pending;
static twai_port_switch_report_t s_switch;

/* The bound on the wait for the driver to let an aborted frame go (T9). */
#define SWITCH_ABORT_WAIT_MAX_US    1000u
#define SWITCH_ABORT_WAIT_STEP_US   10u

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

/* The receive losses' total, from its parts (T7). Lock held. */
static void sum_rx_lost(void)
{
    s_count.rx_lost = s_count.rx_lost_queue_full + s_count.rx_lost_overrun
                      + s_count.rx_lost_restart;
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
        const uint32_t failed_before = s_count.tx_failed;
        take_rise(st->tx_failed_count, &s_raw.tx_failed, &s_count.tx_failed);
        /* A frame the switch aborted is counted once, at the switch (T17). */
        const uint32_t rise = s_count.tx_failed - failed_before;
        const uint32_t skip = rise < s_tx_failed_skip ? rise : s_tx_failed_skip;
        s_count.tx_failed -= skip;
        s_tx_failed_skip -= skip;
        take_rise(st->bus_error_count, &s_raw.bus_error, &s_count.bus_errors);
        take_rise(st->arb_lost_count, &s_raw.arb_lost, &s_count.arb_lost);
        sum_rx_lost();
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
    twai_port_ll_build_marks();     /* R20: keep the test-only switches' marks */
    if (cfg == NULL || cfg->rx_queue_len == 0 || !mode_to_twai(cfg->mode, &m))
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_installed)
    {
        return ESP_ERR_INVALID_STATE;
    }
    if (cfg->kept_driver_switch)
    {
        /* T14: only on a chip whose register file is measured for it. */
        if (!twai_port_ll_switch_supported())
        {
            return ESP_ERR_NOT_SUPPORTED;
        }
        /* T17: every frame the driver can hold must fit the record. */
        if (cfg->tx_queue_len + 1u > TWAI_PORT_SWITCH_HELD_MAX)
        {
            return ESP_ERR_INVALID_ARG;
        }
    }
    s_cfg = *cfg;
    s_timing = cfg->timing;
    s_mode = cfg->mode;
    s_filter = cfg->filter;
    s_setup = true;
    return ESP_OK;
}

/* ----------------------------------------------------- install, T4 ---- */

static esp_err_t kept_switch(can_port_mode_t to);

/* T14: the port reaches listen-only through the registers, from a driver
 * installed in normal mode. */
static bool kept_lom(can_port_mode_t mode)
{
    return s_cfg.kept_driver_switch && mode == CAN_PORT_MODE_LISTEN_ONLY;
}

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
    if (kept_lom(s_mode))
    {
        /* T14: the driver's mode is fixed at install and gates every send. */
        mode = TWAI_MODE_NORMAL;
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
    s_ring_n = 0;
    s_tx_failed_skip = 0;
    s_tx_block = kept_lom(s_mode);
    s_installed = true;
    taskEXIT_CRITICAL(&s_lock);

    /*
     * A kept-driver port set up in listen-only is switched there now, with
     * the transceiver still in standby, so the controller is never on the
     * bus in normal mode. Nothing has been sent, so nothing is held.
     */
    if (kept_lom(s_mode))
    {
        err = kept_switch(CAN_PORT_MODE_LISTEN_ONLY);
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "bring-up: the switch to listen-only failed: %s",
                     esp_err_to_name(err));
            return err;
        }
    }

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
     * queue (IDF v5.4.1 and v5.4.2 twai.c, twai_stop_v2() and the driver
     * object's free), so TX is read before the stop and RX after it, when
     * the stopped controller can receive no more. Booked only once the
     * uninstall has succeeded.
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
        /* Still installed, so the transceiver comes back out of standby, as
         * when the stop is refused. */
        (void)board_can_standby(s_cfg.board_port, false);
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

/* --------------------------------------- the kept-driver switch, T14 -- */

/* Take the alerts, keeping the events for the project's next poll (T18). */
static uint32_t switch_poll(void)
{
    const uint32_t ev = twai_port_poll_alerts();
    taskENTER_CRITICAL(&s_lock);
    s_ev_pending |= ev;
    taskEXIT_CRITICAL(&s_lock);
    return ev;
}

/*
 * T17: remove the frames the driver holds, with the controller in reset
 * mode, so none can complete while they are counted. Returns how many.
 */
static uint32_t switch_remove_held(void)
{
    twai_status_info_t st;
    if (read_status(&st) != ESP_OK)
    {
        return 0;
    }
    uint32_t held = st.msgs_to_tx;
    taskENTER_CRITICAL(&s_lock);
    const uint32_t recorded = s_ring_n;
    taskEXIT_CRITICAL(&s_lock);
    if (held > recorded)
    {
        held = recorded;    /* cannot happen with sends from one task */
    }

    /* The queue goes now; the frame in the buffer, if any, after the switch,
     * when it can only be awaiting (twai_port_set_mode(), step 4). */
    (void)twai_clear_transmit_queue();

    taskENTER_CRITICAL(&s_lock);
    s_count.tx_discarded_switch += held;
    taskEXIT_CRITICAL(&s_lock);

    /* Oldest first: the frame in the buffer, then the queue in order. */
    if (s_cfg.switch_discard != NULL)
    {
        for (uint32_t i = 0; i < held; i++)
        {
            const uint32_t k = (recorded - held + i) % TWAI_PORT_SWITCH_HELD_MAX;
            s_cfg.switch_discard(s_cfg.hook_ctx, &s_ring[k].frame, s_ring[k].tag);
        }
    }
    return held;
}

/*
 * T17: the frame left in the transmit buffer, now in listen-only, where it
 * can only be awaiting. The abort frees the buffer; the driver's interrupt
 * then counts a transmit failure, which is not counted again (s_tx_failed_skip),
 * and raises a transmit-failed alert, which is not passed up. Waits, bounded,
 * until the driver holds no frame.
 *
 * THE WAIT IS ELAPSED TIME, from esp_timer, not a sum of its steps: it runs
 * on a task with interrupts on, so an interrupt or another task can run
 * between steps, and the status read takes time of its own. abort_wait_us is
 * T19's evidence for T16 and T17 (the charge-interposer reviewer,
 * 2026-11-28), and the 1 ms bound is 1 ms of elapsed time. Both are read
 * just after each status read, from just after the abort.
 */
static void switch_abort_head(twai_port_switch_report_t *rep)
{
    taskENTER_CRITICAL(&s_lock);
    const bool failed_was_held = s_tx_failed_held;
    const uint32_t failed_was_pending = s_ev_pending & TWAI_PORT_EV_TX_FAILED;
    taskEXIT_CRITICAL(&s_lock);

    const twai_port_buf_t seen = twai_port_ll_abort_if_awaiting();
    rep->head_aborted = (seen == TWAI_PORT_BUF_AWAITING);
    if (rep->head_aborted)
    {
        taskENTER_CRITICAL(&s_lock);
        s_tx_failed_skip++;
        taskEXIT_CRITICAL(&s_lock);
    }

    twai_status_info_t st;
    const int64_t t0 = esp_timer_get_time();
    int64_t waited = 0;
    bool driver_holds = true;
    for (;;)
    {
        const bool freed = read_status(&st) == ESP_OK && st.msgs_to_tx == 0;
        waited = esp_timer_get_time() - t0;
        if (freed)
        {
            driver_holds = false;
            break;
        }
        if (waited >= SWITCH_ABORT_WAIT_MAX_US)
        {
            break;
        }
        esp_rom_delay_us(SWITCH_ABORT_WAIT_STEP_US);
    }
    rep->abort_wait_us = (waited <= 0) ? 0u
                       : (waited >= (int64_t)UINT32_MAX) ? UINT32_MAX
                       : (uint32_t)waited;
    rep->abort_timed_out = driver_holds;
    rep->driver_tx_stuck = driver_holds
                           && twai_port_ll_buf_state() == TWAI_PORT_BUF_EMPTY;

    /* The abort's alert: nothing can fail on the wire in listen-only, so the
     * transmit-failed event this poll sees is the abort's. */
    (void)switch_poll();
    taskENTER_CRITICAL(&s_lock);
    if (rep->head_aborted)
    {
        s_ev_pending = (s_ev_pending & ~TWAI_PORT_EV_TX_FAILED) | failed_was_pending;
        s_tx_failed_held = failed_was_held;
    }
    /* A failure the driver never counted is not taken off a later one. */
    if (driver_holds)
    {
        s_tx_failed_skip = 0;
    }
    taskEXIT_CRITICAL(&s_lock);
}

/*
 * THE KEPT-DRIVER SWITCH (T14 to T18), between normal and listen-only, with
 * the driver installed and kept. The steps are listed at twai_port_set_mode()
 * in twai_port.h. The record changes only once the switch has run.
 */
static esp_err_t kept_switch(can_port_mode_t to)
{
    twai_port_switch_report_t rep;
    memset(&rep, 0, sizeof(rep));
    rep.seq = s_switch.seq + 1u;
    rep.to_mode = to;
    const bool to_lom = (to == CAN_PORT_MODE_LISTEN_ONLY);

    twai_status_info_t st;
    esp_err_t err = read_status(&st);
    if (err == ESP_OK && st.state != TWAI_STATE_RUNNING)
    {
        err = ESP_ERR_INVALID_STATE;    /* bus-off or recovering */
    }
    if (err != ESP_OK)
    {
        rep.result = err;
        s_switch = rep;
        return err;
    }

    if (to_lom)
    {
        s_tx_block = true;      /* step 1: every send refused from here */
    }

    /*
     * Step 2, THE PLACE FOR T16, the switch's placement in the inter-frame
     * gap: HELD by the user (2026-11-27) until T13's no-acknowledge
     * measurements report on the C3 and the S3. No wait and no timeout rule
     * here yet: reset mode is entered at once, and the status register just
     * before it is reported (status_at_reset, buf_at_reset).
     */
    twai_port_ll_entry_t entry;
    twai_port_ll_switch_enter(&entry);
    rep.status_at_reset = entry.status;
    rep.buf_at_reset = entry.buf;

    /* Step 3, T18: the controller sends no more, so this poll and the status
     * read below take every bus error up to the switch. */
    (void)switch_poll();

    if (to_lom)
    {
        rep.held = switch_remove_held();    /* T17 */
    }

    /* T15: REC 128 into listen-only, 0 out of it, read back. */
    rep.rec_written = to_lom ? 128u : 0u;
    twai_port_ll_switch_leave(to_lom, rep.rec_written,
                              &rep.rec_read_back, &rep.tec_read_back);

    if (to_lom && rep.held > 0)
    {
        switch_abort_head(&rep);
    }

    s_mode = to;
    if (!to_lom)
    {
        s_tx_block = false;
    }
    rep.result = ESP_OK;
    s_switch = rep;
    if (rep.rec_read_back != rep.rec_written)
    {
        ESP_LOGW(TAG, "switch: REC written %u, read back %u",
                 (unsigned)rep.rec_written, (unsigned)rep.rec_read_back);
    }
    return ESP_OK;
}

esp_err_t twai_port_last_switch(twai_port_switch_report_t *out)
{
    if (out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_cfg.kept_driver_switch)
    {
        return ESP_ERR_NOT_SUPPORTED;
    }
    *out = s_switch;
    return ESP_OK;
}

/* --------------------------------------------- mode and settings, T4 -- */

/* T14: a change the kept-driver switch makes, rather than T4. */
static bool kept_change(can_port_mode_t from, can_port_mode_t to)
{
    const bool from_kept = from == CAN_PORT_MODE_NORMAL
                           || from == CAN_PORT_MODE_LISTEN_ONLY;
    const bool to_kept = to == CAN_PORT_MODE_NORMAL
                         || to == CAN_PORT_MODE_LISTEN_ONLY;
    return s_cfg.kept_driver_switch && from_kept && to_kept;
}

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
    if (kept_change(s_mode, mode))
    {
        return kept_switch(mode);
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
    return twai_port_send_tagged(m, wait, 0);
}

esp_err_t twai_port_send_tagged(const twai_message_t *m, TickType_t wait,
                                uint32_t tag)
{
    if (m == NULL)
    {
        count_add(&s_count.tx_refused, 1);
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_installed || s_tx_block)
    {
        /* Down, or in listen-only on a kept-driver port (T14). */
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
        taskENTER_CRITICAL(&s_lock);
        s_count.tx_frames++;
        if (s_cfg.kept_driver_switch)
        {
            /* The record of frames sent (T17), in the driver's order. */
            const uint32_t k = s_ring_n % TWAI_PORT_SWITCH_HELD_MAX;
            s_ring[k].frame = *m;
            s_ring[k].tag = tag;
            s_ring_n++;
        }
        taskEXIT_CRITICAL(&s_lock);
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

/*
 * The frames a flush discards are counted, read just before the clear (T7): a
 * deliberate discard, apart from the losses. can.c's can_flush_rx() cleared
 * them uncounted, and the stock enable flushes after every bring-up.
 */
void twai_port_flush_rx(void)
{
    if (s_installed)
    {
        twai_status_info_t st;
        const bool have = (read_status(&st) == ESP_OK);
        if (twai_clear_receive_queue() == ESP_OK && have)
        {
            count_add(&s_count.rx_flushed, st.msgs_to_rx);
        }
    }
}

/* ---------------------------------------------------------- alerts, T5 -- */

uint32_t twai_port_poll_alerts(void)
{
    if (!s_installed)
    {
        return 0;
    }
    /* Events a kept-driver switch's own polls took (T18), passed up now. */
    taskENTER_CRITICAL(&s_lock);
    uint32_t ev = s_ev_pending;
    s_ev_pending = 0;
    taskEXIT_CRITICAL(&s_lock);

    uint32_t alerts = 0;
    if (twai_read_alerts(&alerts, 0) != ESP_OK || alerts == 0)
    {
        return ev;
    }

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
    /*
     * Recovery done: the controller waits STOPPED until it is started. The
     * start resets the receive queue (IDF v5.4.1 and v5.4.2 twai_start_v2()),
     * and the frames waiting in it, read just above with the controller
     * stopped, were received and never delivered: receive losses, a part of
     * their own (T7).
     */
    if (s_cfg.recovery == TWAI_PORT_RECOVERY_AUTO
        && st.state == TWAI_STATE_STOPPED)
    {
        if (twai_start() == ESP_OK)
        {
            taskENTER_CRITICAL(&s_lock);
            s_count.rx_lost_restart += st.msgs_to_rx;
            sum_rx_lost();
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
        if (s_cfg.kept_driver_switch)
        {
            /* T15: the driver reads 0 for both in listen-only, and on a
             * kept-driver port it believes it is in normal mode anyway. */
            twai_port_ll_counters(&out->tec, &out->rec);
        }
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
