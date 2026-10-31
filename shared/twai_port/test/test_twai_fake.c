/*
 * The fake's own tests (SPEC.md T10; the harness tests the harness). Each
 * case states a behaviour of ESP-IDF v5.4.1's driver, from its source
 * (README.md, "What it rests on"), and checks the fake does it. mutate.py
 * then breaks the fake one behaviour at a time and requires this to fail.
 *
 *   make -C shared/twai_port/test run
 */
#include "twai_fake.h"
#include "hal/twai_ll.h"

#include <stdio.h>
#include <string.h>

static int g_fails, g_checks;
static const char *g_case;

#define CHECK(cond) do { g_checks++; if (!(cond)) { g_fails++; \
    printf("FAIL %s: %s (line %d)\n", g_case, #cond, __LINE__); } } while (0)

static const uint8_t D8[8] = {1, 2, 3, 4, 5, 6, 7, 8};

static twai_general_config_t gcfg(twai_mode_t mode, uint32_t txq, uint32_t rxq, uint32_t alerts)
{
    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(4, 5, TWAI_MODE_NORMAL);
    g.mode = mode;
    g.tx_queue_len = txq;
    g.rx_queue_len = rxq;
    g.alerts_enabled = alerts;
    return g;
}

static const twai_timing_config_t T500 = TWAI_TIMING_CONFIG_500KBITS();
static const twai_filter_config_t FALL = TWAI_FILTER_CONFIG_ACCEPT_ALL();

/* Reset, install and (optionally) start. */
static void up(twai_mode_t mode, uint32_t txq, uint32_t rxq, uint32_t alerts, bool start)
{
    twai_general_config_t g = gcfg(mode, txq, rxq, alerts);
    twai_fake_reset();
    CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_OK);
    if (start) { CHECK(twai_start() == ESP_OK); }
}

static twai_message_t msg(uint32_t id, uint8_t dlc)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = id;
    m.data_length_code = dlc;
    memcpy(m.data, D8, dlc > 8 ? 8 : dlc);
    return m;
}

static uint32_t read_alerts(void)
{
    uint32_t a = 0;
    (void)twai_read_alerts(&a, 0);
    return a;
}

/* ------------------------------------------------------------- cases -- */

static void case_no_driver(void)
{
    twai_message_t m = msg(0x100, 8);
    twai_status_info_t s;
    uint32_t a;
    g_case = "no driver: every call ESP_ERR_INVALID_ARG (twai.c handle check)";
    twai_fake_reset();
    CHECK(twai_start() == ESP_ERR_INVALID_ARG);
    CHECK(twai_stop() == ESP_ERR_INVALID_ARG);
    CHECK(twai_driver_uninstall() == ESP_ERR_INVALID_ARG);
    CHECK(twai_transmit(&m, 0) == ESP_ERR_INVALID_ARG);
    CHECK(twai_receive(&m, 0) == ESP_ERR_INVALID_ARG);
    CHECK(twai_read_alerts(&a, 0) == ESP_ERR_INVALID_ARG);
    CHECK(twai_reconfigure_alerts(0, NULL) == ESP_ERR_INVALID_ARG);
    CHECK(twai_get_status_info(&s) == ESP_ERR_INVALID_ARG);
    CHECK(twai_initiate_recovery() == ESP_ERR_INVALID_ARG);
    CHECK(twai_clear_receive_queue() == ESP_ERR_INVALID_ARG);
    CHECK(twai_fake_wrong_state_calls() == 10);
}

static void case_install_args(void)
{
    twai_general_config_t g = gcfg(TWAI_MODE_NORMAL, 5, 5, 0);
    twai_timing_config_t t = T500;
    g_case = "install: argument checks, brp from the quanta, INVALID_STATE twice";
    twai_fake_reset();
    CHECK(twai_driver_install(NULL, &T500, &FALL) == ESP_ERR_INVALID_ARG);
    CHECK(twai_driver_install(&g, NULL, &FALL) == ESP_ERR_INVALID_ARG);
    CHECK(twai_driver_install(&g, &T500, NULL) == ESP_ERR_INVALID_ARG);
    g.controller_id = 1;
    CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_ERR_INVALID_ARG);
    g.controller_id = 0;
    g.rx_queue_len = 0;
    CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_ERR_INVALID_ARG);
    g.rx_queue_len = 5;
    t.quanta_resolution_hz = 3000000;           /* does not divide 80 MHz */
    CHECK(twai_driver_install(&g, &t, &FALL) == ESP_ERR_INVALID_ARG);
    t.quanta_resolution_hz = 0;
    t.brp = 7;                                  /* odd */
    CHECK(twai_driver_install(&g, &t, &FALL) == ESP_ERR_INVALID_ARG);
    t.brp = 0;
    CHECK(twai_driver_install(&g, &t, &FALL) == ESP_ERR_INVALID_ARG);
    CHECK(!twai_fake_installed());
    CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_OK);
    CHECK(twai_fake_installed_brp() == 8);
    CHECK(twai_fake_bit_ns() == 2000);
    CHECK(twai_fake_state() == TWAI_STATE_STOPPED);
    CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_ERR_INVALID_STATE);
    CHECK(twai_fake_install_count() == 1);
    twai_fake_fail_install_next(ESP_ERR_NO_MEM);
    CHECK(twai_driver_uninstall() == ESP_OK);
    CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_ERR_NO_MEM);
    CHECK(!twai_fake_installed());
}

static void case_states(void)
{
    g_case = "states: start from STOPPED only, stop from RUNNING only, uninstall from STOPPED or BUS_OFF";
    up(TWAI_MODE_NORMAL, 5, 5, 0, false);
    CHECK(twai_stop() == ESP_ERR_INVALID_STATE);
    CHECK(twai_start() == ESP_OK);
    CHECK(twai_start() == ESP_ERR_INVALID_STATE);
    CHECK(twai_driver_uninstall() == ESP_ERR_INVALID_STATE);
    CHECK(twai_initiate_recovery() == ESP_ERR_INVALID_STATE);
    CHECK(twai_stop() == ESP_OK);
    CHECK(twai_driver_uninstall() == ESP_OK);
    twai_fake_fail_start_next(ESP_ERR_INVALID_STATE);
    up(TWAI_MODE_NORMAL, 5, 5, 0, false);
    twai_fake_fail_start_next(ESP_FAIL);
    CHECK(twai_start() == ESP_FAIL);
    CHECK(twai_fake_installed() && twai_fake_state() == TWAI_STATE_STOPPED);
}

static void case_listen_only_first(void)
{
    twai_message_t m = msg(0x100, 8);
    g_case = "transmit: listen-only refused (NOT_SUPPORTED) before the state is checked";
    up(TWAI_MODE_LISTEN_ONLY, 5, 5, 0, false);
    CHECK(twai_transmit(&m, 0) == ESP_ERR_NOT_SUPPORTED);
    CHECK(twai_start() == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_ERR_NOT_SUPPORTED);
    CHECK(twai_fake_listen_only_tx_attempts() == 2);
    CHECK(twai_fake_sent_count() == 0);
    m.data_length_code = 9;
    CHECK(twai_transmit(&m, 0) == ESP_ERR_INVALID_ARG);
}

static void case_tx_capacity(void)
{
    twai_message_t m = msg(0x100, 8);
    twai_status_info_t s;
    g_case = "transmit: the buffer plus tx_queue_len; then TIMEOUT, or ESP_FAIL with no queue";
    up(TWAI_MODE_NORMAL, 2, 5, 0, true);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 5) == ESP_ERR_TIMEOUT);     /* no wait hook */
    CHECK(twai_get_status_info(&s) == ESP_OK && s.msgs_to_tx == 3);
    up(TWAI_MODE_NORMAL, 0, 5, 0, true);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_FAIL);
}

static void case_air_time(void)
{
    twai_message_t m = msg(0x100, 8);
    g_case = "air time: 111 bits at 2 us for a standard 8-byte frame, back to back";
    up(TWAI_MODE_NORMAL, 5, 5, TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE, true);
    CHECK(twai_fake_frame_us(8, false, false, 2000) == 222);
    CHECK(twai_fake_frame_us(0, true, false, 2000) == 134);
    CHECK(twai_fake_frame_us(8, false, true, 2000) == 94);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(221);
    CHECK(twai_fake_wire_count() == 0);
    twai_fake_advance(1);
    CHECK(twai_fake_wire_count() == 1 && twai_fake_wire(0)->t_done == 222);
    CHECK(read_alerts() == TWAI_ALERT_TX_SUCCESS);
    twai_fake_advance(222);
    CHECK(twai_fake_wire_count() == 2 && twai_fake_wire(1)->t_done == 444);
    CHECK(read_alerts() == (TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE));
    twai_fake_set_air_time_us(200);
    twai_fake_set_air_time_id(0x200, 50);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    m.identifier = 0x200;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(250);
    CHECK(twai_fake_wire_count() == 4 && twai_fake_wire(3)->t_done == 444 + 250);
}

static void case_alerts(void)
{
    twai_message_t m = msg(0x100, 8);
    uint32_t cur = 0, a = 1;
    g_case = "alerts: latched only if enabled when they occur; reconfigure hands back the triggered";
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_reconfigure_alerts(TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_BUS_ERROR, &cur) == ESP_OK);
    CHECK(cur == 0);
    CHECK(twai_read_alerts(&a, 0) == ESP_ERR_TIMEOUT && a == 0);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_reconfigure_alerts(TWAI_ALERT_TX_SUCCESS, &cur) == ESP_OK);
    CHECK(cur == TWAI_ALERT_TX_SUCCESS);     /* the triggered, not the old mask */
    /* The semaphore was given and is still there; the alerts were cleared. */
    CHECK(twai_read_alerts(&a, 0) == ESP_OK && a == 0);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_read_alerts(&a, 0) == ESP_OK && a == TWAI_ALERT_TX_SUCCESS);
    CHECK(twai_read_alerts(&a, 0) == ESP_ERR_TIMEOUT);
    twai_fake_fail_reconfigure_alerts(true);
    CHECK(twai_reconfigure_alerts(0, NULL) == ESP_ERR_INVALID_STATE);
}

static void case_rx(void)
{
    twai_message_t m;
    twai_status_info_t s;
    g_case = "receive: only while running; a full queue loses (rx_missed); a stop keeps the queue";
    up(TWAI_MODE_NORMAL, 5, 2, TWAI_ALERT_RX_QUEUE_FULL, false);
    twai_fake_deliver_std(0x10, D8, 8);
    CHECK(twai_fake_rx_not_received() == 1 && twai_fake_rx_pending() == 0);
    CHECK(twai_start() == ESP_OK);
    twai_fake_deliver_std(0x11, D8, 8);
    twai_fake_deliver_std(0x12, D8, 8);
    twai_fake_deliver_std(0x13, D8, 8);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.msgs_to_rx == 2 && s.rx_missed_count == 1);
    CHECK(read_alerts() == TWAI_ALERT_RX_QUEUE_FULL);
    CHECK(twai_stop() == ESP_OK);
    CHECK(twai_receive(&m, 0) == ESP_OK && m.identifier == 0x11);
    CHECK(twai_start() == ESP_OK);                      /* resets the queue */
    CHECK(twai_receive(&m, 0) == ESP_ERR_TIMEOUT);
    CHECK(twai_fake_rx_discarded() == 1);
    twai_fake_rx_overrun(3);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.rx_overrun_count == 3);
    CHECK(twai_clear_receive_queue() == ESP_OK);
}

static void case_filter(void)
{
    twai_general_config_t g = gcfg(TWAI_MODE_NORMAL, 5, 5, 0);
    twai_filter_config_t f;
    g_case = "acceptance filter: single-filter mode, standard IDs in bits 31-21";
    twai_fake_reset();
    f.acceptance_code = 0x123u << 21;
    f.acceptance_mask = ~(0x7FFu << 21);
    f.single_filter = true;
    CHECK(twai_driver_install(&g, &T500, &f) == ESP_OK);
    CHECK(twai_start() == ESP_OK);
    twai_fake_deliver_std(0x124, D8, 8);
    twai_fake_deliver_std(0x123, D8, 8);
    CHECK(twai_fake_rx_pending() == 1 && twai_fake_rx_filtered() == 1);
}

static void case_bus_off(void)
{
    twai_message_t m = msg(0x100, 8);
    twai_status_info_t s;
    g_case = "bus-off: one alert per episode, queues kept, recovery ends STOPPED after 1408 bits";
    up(TWAI_MODE_NORMAL, 5, 5, TWAI_ALERT_BUS_OFF | TWAI_ALERT_BUS_RECOVERED, true);
    twai_fake_stall_next(1);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_deliver_std(0x20, D8, 8);
    twai_fake_bus_off();
    CHECK(read_alerts() == TWAI_ALERT_BUS_OFF);
    twai_fake_bus_off();                    /* already bus-off: not an event */
    CHECK(read_alerts() == 0);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.state == TWAI_STATE_BUS_OFF && s.msgs_to_tx == 2);
    CHECK(twai_receive(&m, 0) == ESP_OK && m.identifier == 0x20);
    CHECK(twai_transmit(&m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(twai_stop() == ESP_ERR_INVALID_STATE);
    twai_fake_deliver_std(0x21, D8, 8);
    CHECK(twai_fake_rx_pending() == 0);
    CHECK(twai_initiate_recovery() == ESP_OK);
    CHECK(twai_fake_tx_discarded_by_recovery() == 2);
    CHECK(twai_fake_state() == TWAI_STATE_RECOVERING);
    CHECK(twai_driver_uninstall() == ESP_ERR_INVALID_STATE);
    twai_fake_advance(2815);
    CHECK(twai_fake_state() == TWAI_STATE_RECOVERING);
    twai_fake_advance(1);
    CHECK(twai_fake_state() == TWAI_STATE_STOPPED);
    CHECK(read_alerts() == TWAI_ALERT_BUS_RECOVERED);
    CHECK(twai_start() == ESP_OK);

    g_case = "bus-off: uninstall from bus-off discards the queue; manual recovery";
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    twai_fake_set_auto_recovery(false);
    twai_fake_stall_next(1);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_bus_off();
    CHECK(twai_initiate_recovery() == ESP_OK);
    twai_fake_advance(100000);
    CHECK(twai_fake_state() == TWAI_STATE_RECOVERING);
    twai_fake_complete_recovery();
    CHECK(twai_fake_state() == TWAI_STATE_STOPPED);
    CHECK(twai_start() == ESP_OK);
    twai_fake_stall_next(1);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_bus_off();
    CHECK(twai_driver_uninstall() == ESP_OK);
    CHECK(twai_fake_tx_discarded_by_uninstall() == 1);
}

static void case_inflight_branch(void)
{
    twai_message_t m = msg(0x100, 8);
    twai_status_info_t s;
    g_case = "bus-off branch a: the frame in the buffer booked failed";
    up(TWAI_MODE_NORMAL, 5, 5, TWAI_ALERT_TX_FAILED | TWAI_ALERT_BUS_OFF, true);
    twai_fake_set_busoff_inflight_fails(true);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_bus_off_after_sent(1);
    twai_fake_advance(222);
    CHECK(twai_fake_state() == TWAI_STATE_BUS_OFF);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.tx_failed_count == 1 && s.msgs_to_tx == 0);
    CHECK(twai_fake_tx_failed_at_busoff() == 1 && twai_fake_wire_count() == 1);
    CHECK(read_alerts() == (TWAI_ALERT_TX_FAILED | TWAI_ALERT_BUS_OFF));
}

static void case_ack(void)
{
    twai_message_t m = msg(0x100, 8);
    twai_status_info_t s;
    g_case = "no ACK: normal mode retransmits for ever, single-shot fails once, no-ACK mode completes";
    up(TWAI_MODE_NORMAL, 5, 5, TWAI_ALERT_TX_FAILED, true);
    twai_fake_set_acked(false);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(100000);
    CHECK(twai_fake_wire_count() == 0 && twai_fake_tx_pending() == 1);
    twai_fake_set_acked(true);
    twai_fake_advance(221);
    CHECK(twai_fake_wire_count() == 0);
    twai_fake_advance(1);
    CHECK(twai_fake_wire_count() == 1);
    twai_fake_set_acked(false);
    m.ss = 1;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(222);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.tx_failed_count == 1 && s.msgs_to_tx == 0);
    CHECK(read_alerts() == TWAI_ALERT_TX_FAILED);
    up(TWAI_MODE_NO_ACK, 5, 5, 0, true);
    twai_fake_set_acked(false);
    m.ss = 0;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(222);
    CHECK(twai_fake_wire_count() == 1);
}

static void case_stop_discards(void)
{
    twai_message_t m = msg(0x100, 8);
    g_case = "stop: the frame on the wire cut, the queue discarded, the IDs recorded";
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    m.identifier = 0x101;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    m.identifier = 0x102;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(300);                 /* 0x100 done, 0x101 on the wire */
    CHECK(twai_stop() == ESP_OK);
    CHECK(twai_fake_wire_count() == 1);
    CHECK(twai_fake_tx_cut_by_stop() == 1 && twai_fake_tx_discarded_by_stop() == 1);
    CHECK(twai_fake_dropped_at_stop_count() == 2);
    CHECK(twai_fake_dropped_at_stop_id(0) == 0x101 && twai_fake_dropped_at_stop_id(1) == 0x102);
}

static void case_counters_reinstall(void)
{
    twai_status_info_t s;
    g_case = "reinstall: the driver's counts restart at zero; the fake's totals do not";
    up(TWAI_MODE_NORMAL, 5, 1, 0, true);
    twai_fake_deliver_std(0x1, D8, 8);
    twai_fake_deliver_std(0x2, D8, 8);
    twai_fake_bus_error(4);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.rx_missed_count == 1 && s.bus_error_count == 4);
    CHECK(twai_stop() == ESP_OK);
    CHECK(twai_driver_uninstall() == ESP_OK);
    {
        twai_general_config_t g = gcfg(TWAI_MODE_NORMAL, 5, 1, 0);
        CHECK(twai_driver_install(&g, &T500, &FALL) == ESP_OK);
    }
    CHECK(twai_get_status_info(&s) == ESP_OK && s.rx_missed_count == 0 && s.bus_error_count == 0);
    CHECK(twai_fake_total_rx_missed() == 1);
    twai_fake_set_error_counters(9, 7);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.tx_error_counter == 9 && s.rx_error_counter == 7);
    up(TWAI_MODE_LISTEN_ONLY, 5, 1, 0, true);
    twai_fake_set_error_counters(9, 7);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.tx_error_counter == 0 && s.rx_error_counter == 0);
}

static void case_registers(twai_fake_chip_t chip)
{
    twai_message_t m = msg(0x100, 8);
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    g_case = chip == TWAI_FAKE_CHIP_C3 ? "registers (C3): TBS, TS, and the measured abort"
                                       : "registers (S3): as the C3, every call counted unmeasured";
    up(TWAI_MODE_NORMAL, 5, 5, TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE, true);
    twai_fake_set_chip(chip);
    CHECK(twai_ll_get_status(hw) == TWAI_LL_STATUS_TBS);
    twai_fake_stall_next(1);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    m.identifier = 0x101;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_ll_get_status(hw) == 0);                 /* awaiting */
    twai_ll_set_cmd_abort_tx(hw);
    CHECK(twai_fake_ll_removed() == 1);
    CHECK(read_alerts() == TWAI_ALERT_TX_SUCCESS);
    CHECK(twai_ll_get_status(hw) == TWAI_LL_STATUS_TS);  /* 0x101 loaded, on the wire */
    twai_ll_set_cmd_abort_tx(hw);
    CHECK(twai_fake_ll_aborts_while_tx() == 1 && twai_fake_ll_removed() == 1);
    twai_fake_advance(222);
    CHECK(twai_fake_wire_count() == 1 && twai_fake_wire(0)->id == 0x101);
    CHECK(twai_fake_ll_aborts() == 2);
    CHECK(twai_fake_ll_unmeasured_calls() == (chip == TWAI_FAKE_CHIP_C3 ? 0 : 5));
    twai_fake_set_chip(TWAI_FAKE_CHIP_C3);
}

/* A wait hook that moves the clock and runs a scripted action. */
static int g_wait_calls;
static int g_wait_action;
static bool test_wait(int64_t deadline, void *ctx)
{
    (void)ctx;
    g_wait_calls++;
    if (g_wait_action == 1) { twai_fake_deliver_std(0x55, D8, 8); }
    if (g_wait_action == 2)
    {
        CHECK(twai_fake_in_receive());
        (void)twai_stop();
        (void)twai_driver_uninstall();
    }
    g_wait_action = 0;
    {
        int64_t step = deadline - twai_fake_now();
        twai_fake_advance(step < 1000 ? step : 1000);
    }
    return true;
}

static void case_wait_hook(void)
{
    twai_message_t m;
    g_case = "receive with a wait: the hook runs, the deadline holds, a teardown inside it is counted";
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    twai_fake_set_wait_hook(test_wait, NULL);
    g_wait_calls = 0;
    g_wait_action = 1;
    CHECK(twai_receive(&m, 200) == ESP_OK && m.identifier == 0x55);
    CHECK(g_wait_calls == 1 && twai_fake_now() == 1000);
    CHECK(twai_receive(&m, 5) == ESP_ERR_TIMEOUT);
    CHECK(twai_fake_now() == 6000);
    g_wait_action = 2;
    CHECK(twai_receive(&m, 200) == ESP_ERR_INVALID_STATE);
    CHECK(twai_fake_unsafe_teardowns() == 2);
    twai_fake_set_wait_hook(NULL, NULL);
}

static int64_t g_ext_clock;
static int64_t ext_clock(void *ctx) { (void)ctx; return g_ext_clock; }

static void case_clock_adapter(void)
{
    twai_message_t m = msg(0x100, 8);
    g_case = "clock adapter: the project's clock drives completions";
    twai_fake_set_clock(ext_clock, NULL);
    g_ext_clock = 5000;
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_fake_sent(0)->t_queued == 5000);
    g_ext_clock = 5221;
    CHECK(twai_fake_wire_count() == 0);
    g_ext_clock = 5222;
    CHECK(twai_fake_wire_count() == 1);
    twai_fake_set_clock(NULL, NULL);
}

static void case_self_reception(void)
{
    twai_message_t m = msg(0x51, 6);
    g_case = "self-reception: a frame sent with self = 1 comes back to the receive queue";
    up(TWAI_MODE_NO_ACK, 5, 5, 0, true);
    m.self = 1;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_fake_self_received() == 1 && twai_fake_rx_pending() == 1);
}

static void case_timing_modes(void)
{
    twai_message_t m = msg(0x100, 8);
    g_case = "INSTANT completes in the call; HELD waits for the flush";
    up(TWAI_MODE_NORMAL, 5, 5, TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE, true);
    twai_fake_set_tx_timing(TWAI_FAKE_TX_INSTANT);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_fake_wire_count() == 1 && twai_fake_tx_pending() == 0);
    twai_fake_set_tx_timing(TWAI_FAKE_TX_HELD);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(100000);
    CHECK(twai_fake_tx_pending() == 2);
    (void)read_alerts();
    twai_fake_flush_tx();
    CHECK(twai_fake_wire_count() == 3);
    CHECK(read_alerts() == (TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE));
    CHECK(twai_stop() == ESP_OK && twai_fake_tx_cut_by_stop() == 0);
}

static void case_rules(void)
{
    twai_message_t m = msg(0x100, 8);
    twai_status_info_t s;
    g_case = "test rules: refuse by ID with its code, fail by ID after its air time";
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    twai_fake_refuse(0x100, 1, ESP_ERR_TIMEOUT);
    CHECK(twai_transmit(&m, 0) == ESP_ERR_TIMEOUT);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_fail(0x200, -1);
    m.identifier = 0x200;
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    CHECK(twai_transmit(&m, 0) == ESP_OK);
    twai_fake_advance(10000);
    CHECK(twai_get_status_info(&s) == ESP_OK && s.tx_failed_count == 2);
    CHECK(twai_fake_wire_count() == 1 && twai_fake_failed_by_rule() == 2);
    CHECK(twai_fake_refused_by_rule() == 1);
    CHECK(twai_fake_max_inflight() == 3);
}

static void case_tick_length(void)
{
    twai_message_t m;
    g_case = "tick length: a wait of n ticks is n x the tick";
    up(TWAI_MODE_NORMAL, 5, 5, 0, true);
    twai_fake_set_tick_us(10000);
    twai_fake_set_wait_hook(test_wait, NULL);
    CHECK(twai_receive(&m, 2) == ESP_ERR_TIMEOUT && twai_fake_now() == 20000);
    twai_fake_set_wait_hook(NULL, NULL);
    twai_fake_set_tick_us(1000);
}

int main(void)
{
    case_no_driver();
    case_install_args();
    case_states();
    case_listen_only_first();
    case_tx_capacity();
    case_air_time();
    case_alerts();
    case_rx();
    case_filter();
    case_bus_off();
    case_inflight_branch();
    case_ack();
    case_stop_discards();
    case_counters_reinstall();
    case_registers(TWAI_FAKE_CHIP_C3);
    case_registers(TWAI_FAKE_CHIP_S3);
    case_wait_hook();
    case_clock_adapter();
    case_self_reception();
    case_timing_modes();
    case_rules();
    case_tick_length();
    printf("test_twai_fake: %d checks, %d failed\n", g_checks, g_fails);
    return g_fails ? 1 : 0;
}
