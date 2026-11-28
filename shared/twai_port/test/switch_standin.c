/*
 * The switch cases' can-fail (test_switch_on_fake.c): the tester's own port,
 * written from SPEC.md T14, T15, T17 and T18 and the interface in
 * ../include/twai_port.h, on the fake's driver and register calls, not from
 * twai_port.c. It is only the part of the port the switch cases call. As
 * written it must pass every switch case on the fake's defaults; each form
 * SWITCH_BROKEN names breaks one rule, and `make switch-standins` passes only
 * if each is caught by a check.
 *
 * Its own order for the switch to listen-only: refuse sends; the queue
 * cleared and the head aborted and waited for, at most 1 ms in steps of
 * 2 us (an abort ends a retrying frame with its attempt); the status read;
 * reset mode; one more alert poll (T18); listen-only, REC 128, read back
 * (T15); out of reset mode; each frame the driver held passed to the hook
 * and counted once (T17). That order is a choice of this stand-in, not of
 * the spec: where to wait before reset mode is T16's, which is held.
 */
#include "twai_port.h"
#include "twai_fake.h"
#include "hal/twai_ll.h"
#include "esp_rom_sys.h"

#include <stdlib.h>
#include <string.h>

static const char *broken(void)
{
    const char *b = getenv("SWITCH_BROKEN");
    return b ? b : "";
}

static bool is_broken(const char *name) { return strcmp(broken(), name) == 0; }

static twai_port_config_t s_cfg;
static bool s_setup, s_up, s_lo;
static can_port_mode_t s_mode;
static can_port_status_t s_st;
static twai_port_switch_report_t s_rep;
static uint32_t s_failed_at_switch, s_seq;

/* The frames sent, in the driver's order (sends from one task, T2). */
typedef struct { twai_message_t m; uint32_t tag; } sent_t;
static sent_t s_ring[TWAI_PORT_SWITCH_HELD_MAX];
static int s_ring_n;

void twai_port_config_defaults(twai_port_config_t *cfg)
{
    const twai_timing_config_t t500 = TWAI_TIMING_CONFIG_500KBITS();
    memset(cfg, 0, sizeof(*cfg));
    cfg->timing = t500;
    cfg->mode = CAN_PORT_MODE_NORMAL;
    cfg->filter.acceptance_mask = 0xFFFFFFFFu;
    cfg->filter.single_filter = true;
    cfg->rx_queue_len = 64;
    cfg->tx_queue_len = 32;
}

esp_err_t twai_port_setup(const twai_port_config_t *cfg)
{
    if (s_up) { return ESP_ERR_INVALID_STATE; }
    if (cfg->rx_queue_len == 0 || cfg->tx_queue_len == 0) { return ESP_ERR_INVALID_ARG; }
    /* SWITCH_UNMEASURED: the stand-in plays a chip not measured for it. */
    if (cfg->kept_driver_switch && getenv("SWITCH_UNMEASURED") != NULL && !is_broken("accepts unmeasured"))
    {
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (cfg->kept_driver_switch && cfg->tx_queue_len + 1 > TWAI_PORT_SWITCH_HELD_MAX
        && !is_broken("no held bound"))
    {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = *cfg;
    s_mode = cfg->mode;
    s_setup = true;
    return ESP_OK;
}

static twai_mode_t drv_mode(can_port_mode_t m)
{
    if (m == CAN_PORT_MODE_NO_ACK_SELF_TEST) { return TWAI_MODE_NO_ACK; }
    if (m == CAN_PORT_MODE_LISTEN_ONLY && !s_cfg.kept_driver_switch) { return TWAI_MODE_LISTEN_ONLY; }
    return TWAI_MODE_NORMAL;
}

static esp_err_t bring_up(void)
{
    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(4, 5, TWAI_MODE_NORMAL);
    twai_filter_config_t f;
    g.mode = drv_mode(s_mode);
    g.tx_queue_len = s_cfg.tx_queue_len;
    g.rx_queue_len = s_cfg.rx_queue_len;
    g.alerts_enabled = TWAI_ALERT_TX_FAILED | TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_BUS_ERROR
                     | TWAI_ALERT_BUS_OFF | TWAI_ALERT_RX_QUEUE_FULL;
    f.acceptance_code = s_cfg.filter.acceptance_code;
    f.acceptance_mask = s_cfg.filter.acceptance_mask;
    f.single_filter = s_cfg.filter.single_filter;
    if (twai_driver_install(&g, &s_cfg.timing, &f) != ESP_OK) { return ESP_FAIL; }
    if (twai_start() != ESP_OK) { return ESP_FAIL; }
    s_up = true;
    s_ring_n = 0;
    s_st.bring_ups++;
    return ESP_OK;
}

static esp_err_t ll_switch(can_port_mode_t to);

esp_err_t twai_port_install(void)
{
    esp_err_t e;
    if (s_up) { return ESP_OK; }
    e = bring_up();
    if (e == ESP_OK && s_cfg.kept_driver_switch && s_mode == CAN_PORT_MODE_LISTEN_ONLY)
    {
        e = ll_switch(CAN_PORT_MODE_LISTEN_ONLY);
    }
    return e;
}

/* Keep the record to the frames the driver still holds: the last ones sent. */
static int trim_ring(void)
{
    twai_status_info_t s;
    int n;
    if (twai_get_status_info(&s) != ESP_OK) { return 0; }
    n = (int)s.msgs_to_tx;
    if (n > s_ring_n) { n = s_ring_n; }
    if (n < s_ring_n)
    {
        memmove(&s_ring[0], &s_ring[s_ring_n - n], (size_t)n * sizeof(s_ring[0]));
        s_ring_n = n;
    }
    return (int)s.msgs_to_tx;
}

uint32_t twai_port_poll_alerts(void)
{
    uint32_t a = 0, ev = 0;
    twai_status_info_t s;
    if (!s_up) { return 0; }
    (void)twai_read_alerts(&a, 0);
    if (twai_get_status_info(&s) == ESP_OK)
    {
        s_st.bus_errors = s.bus_error_count;
        s_st.tx_failed = s.tx_failed_count - (is_broken("aborted head counted failed") ? 0 : s_failed_at_switch);
    }
    if (a & TWAI_ALERT_TX_FAILED) { ev |= TWAI_PORT_EV_TX_FAILED; }
    if (a & TWAI_ALERT_TX_SUCCESS) { ev |= TWAI_PORT_EV_TX_SUCCESS; }
    if (a & TWAI_ALERT_BUS_ERROR) { ev |= TWAI_PORT_EV_BUS_ERROR; }
    (void)trim_ring();
    return ev;
}

static twai_port_buf_t buf_of(uint32_t st)
{
    if (st & TWAI_LL_STATUS_TBS) { return TWAI_PORT_BUF_EMPTY; }
    return (st & TWAI_LL_STATUS_TS) ? TWAI_PORT_BUF_TRANSMITTING : TWAI_PORT_BUF_AWAITING;
}

/* The frames the driver holds, snapshot before they are removed. */
static sent_t s_held[TWAI_PORT_SWITCH_HELD_MAX];
static int s_n_held;
static uint32_t s_failed0;

/* Before reset mode: the record of the frames held, the queue cleared, the
 * head aborted and waited for (an abort ends a retrying frame with its
 * attempt), at most 1 ms in steps of 2 us. */
static void stop_held(void)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    twai_status_info_t s;
    s_n_held = trim_ring();
    if (s_n_held > s_ring_n) { s_n_held = s_ring_n; }
    memcpy(s_held, s_ring, (size_t)s_n_held * sizeof(s_held[0]));
    s_failed0 = twai_get_status_info(&s) == ESP_OK ? s.tx_failed_count : 0;
    if (!is_broken("queue left")) { (void)twai_clear_transmit_queue(); }
    if (twai_ll_get_status(hw) & TWAI_LL_STATUS_TBS) { return; }
    twai_ll_set_cmd_abort_tx(hw);
    s_rep.head_aborted = true;
    for (;;)
    {
        if (twai_get_status_info(&s) == ESP_OK && s.msgs_to_tx == 0) { break; }
        if (s_rep.abort_wait_us >= 1000) { s_rep.abort_timed_out = true; break; }
        esp_rom_delay_us(2);
        s_rep.abort_wait_us += 2;
    }
}

/* After it: a head the driver reported failed is not a tx_failed; each
 * frame to the hook, counted once. */
static void book_held(void)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    twai_status_info_t s;
    int i, n = s_n_held;
    if (twai_get_status_info(&s) == ESP_OK)
    {
        s_failed_at_switch += s.tx_failed_count - s_failed0;
        s_rep.driver_tx_stuck = s.msgs_to_tx > 0 && (twai_ll_get_status(hw) & TWAI_LL_STATUS_TBS);
    }
    if (is_broken("held reversed"))
    {
        for (i = 0; i < n / 2; i++) { sent_t t = s_held[i]; s_held[i] = s_held[n - 1 - i]; s_held[n - 1 - i] = t; }
    }
    for (i = 0; i < n; i++)
    {
        if (s_cfg.switch_discard && !is_broken("no hook")) { s_cfg.switch_discard(s_cfg.hook_ctx, &s_held[i].m, s_held[i].tag); }
    }
    s_rep.held = (uint32_t)n;
    s_st.tx_discarded_switch += is_broken("head only counted") ? (n > 0 ? 1u : 0u) : (uint32_t)n;
    s_ring_n = 0;
}

static esp_err_t ll_switch(can_port_mode_t to)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    const bool lo = to == CAN_PORT_MODE_LISTEN_ONLY;
    const uint32_t rec = lo ? 128u : 0u;
    twai_status_info_t s;
    memset(&s_rep, 0, sizeof(s_rep));
    s_rep.seq = ++s_seq;
    s_rep.to_mode = to;
    if (twai_get_status_info(&s) != ESP_OK || s.state != TWAI_STATE_RUNNING)
    {
        s_rep.result = ESP_ERR_INVALID_STATE;
        return ESP_ERR_INVALID_STATE;
    }
    if (lo && !is_broken("sends not refused")) { s_lo = true; }
    if (lo) { stop_held(); }
    s_rep.status_at_reset = twai_ll_get_status(hw);
    s_rep.buf_at_reset = buf_of(s_rep.status_at_reset);
    twai_ll_enter_reset_mode(hw);
    if (!is_broken("no last poll")) { (void)twai_port_poll_alerts(); }
    twai_ll_set_mode(hw, lo ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
    if (!(lo && is_broken("no REC 128")) && !(!lo && is_broken("no REC 0"))) { twai_ll_set_rec(hw, rec); }
    s_rep.rec_written = rec;
    s_rep.rec_read_back = is_broken("REC not read back") ? rec : twai_ll_get_rec(hw);
    s_rep.tec_read_back = twai_ll_get_tec(hw);
    twai_ll_exit_reset_mode(hw);
    if (lo) { book_held(); }
    else { s_lo = false; }
    if (!is_broken("no last poll")) { (void)twai_port_poll_alerts(); }
    s_mode = to;
    s_rep.result = ESP_OK;
    return ESP_OK;
}

esp_err_t twai_port_set_mode(can_port_mode_t mode)
{
    esp_err_t e;
    const bool kept_pair = s_cfg.kept_driver_switch && mode != CAN_PORT_MODE_NO_ACK_SELF_TEST
                           && s_mode != CAN_PORT_MODE_NO_ACK_SELF_TEST;
    if (mode == s_mode) { return ESP_OK; }
    if (!s_up)
    {
        s_mode = mode;
        return ESP_OK;
    }
    if (kept_pair && !is_broken("teardown switch")) { return ll_switch(mode); }
    /* T4: teardown, then install. */
    (void)twai_stop();
    (void)twai_driver_uninstall();
    s_up = false;
    s_lo = false;
    s_mode = mode;
    e = bring_up();
    if (e == ESP_OK && kept_pair && mode == CAN_PORT_MODE_LISTEN_ONLY)
    {
        /* the broken "teardown switch": listen-only through a reinstall */
        (void)twai_stop();
        (void)twai_driver_uninstall();
        {
            twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(4, 5, TWAI_MODE_LISTEN_ONLY);
            twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
            g.tx_queue_len = s_cfg.tx_queue_len;
            g.rx_queue_len = s_cfg.rx_queue_len;
            (void)twai_driver_install(&g, &s_cfg.timing, &f);
            (void)twai_start();
        }
        s_lo = true;
    }
    return e;
}

can_port_mode_t twai_port_mode(void) { return s_mode; }

esp_err_t twai_port_send_tagged(const twai_message_t *m, TickType_t wait, uint32_t tag)
{
    esp_err_t e;
    if (!s_up || s_lo)
    {
        s_st.tx_refused++;
        return ESP_ERR_INVALID_STATE;
    }
    e = twai_transmit(m, wait);
    if (e == ESP_OK)
    {
        s_st.tx_frames++;
        (void)trim_ring();
        if (s_ring_n < (int)TWAI_PORT_SWITCH_HELD_MAX)
        {
            s_ring[s_ring_n].m = *m;
            s_ring[s_ring_n].tag = tag;
            s_ring_n++;
        }
    }
    else if (e == ESP_ERR_TIMEOUT) { s_st.tx_dropped++; }
    else { s_st.tx_refused++; }
    return e;
}

esp_err_t twai_port_send(const twai_message_t *m, TickType_t wait)
{
    return twai_port_send_tagged(m, wait, 0);
}

esp_err_t twai_port_receive(twai_message_t *out, TickType_t wait)
{
    return twai_receive(out, wait);
}

esp_err_t twai_port_status(can_port_status_t *out)
{
    twai_status_info_t s;
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    *out = s_st;
    if (!s_up || twai_get_status_info(&s) != ESP_OK) { return ESP_OK; }
    out->tx_pending = s.msgs_to_tx;
    out->rx_waiting = s.msgs_to_rx;
    out->tec = s_cfg.kept_driver_switch ? twai_ll_get_tec(hw) : s.tx_error_counter;
    out->rec = s_cfg.kept_driver_switch ? twai_ll_get_rec(hw) : s.rx_error_counter;
    out->state = s.state == TWAI_STATE_RUNNING ? CAN_PORT_STATE_RUNNING
               : s.state == TWAI_STATE_BUS_OFF ? CAN_PORT_STATE_BUS_OFF
               : s.state == TWAI_STATE_RECOVERING ? CAN_PORT_STATE_RECOVERING
               : CAN_PORT_STATE_STOPPED;
    return ESP_OK;
}

esp_err_t twai_port_last_switch(twai_port_switch_report_t *out)
{
    if (out == NULL) { return ESP_ERR_INVALID_ARG; }
    if (!s_cfg.kept_driver_switch) { return ESP_ERR_NOT_SUPPORTED; }
    *out = s_rep;
    return ESP_OK;
}
