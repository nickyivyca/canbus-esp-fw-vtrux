/*
 * The port's unit tests on the S3 register file (SPEC.md T11, T14 to T19):
 * twai_port.c and twai_port_ll_esp32s3.c against the scripted stub (stub.h).
 * Built three times (Makefile):
 *   - with TWAI_PORT_S3_PROBE=1, the T19 measurement build, where the S3
 *     file runs: the kept-driver switch (T14, T15, T17, T18, with T16's place
 *     and no wait), T8's buffer state and abort, and T12's restart stop with
 *     the other core stalled first and its abort inline;
 *   - with TWAI_PORT_HOST_SWITCH=1 only, the testers' host build on the
 *     fake: the switch runs, and T8 and T12 report themselves unsupported;
 *   - with neither, a release build until the measurement passes, where every
 *     S3 call reports itself unsupported and touches nothing.
 * Self-checks for the implementor, not verification.
 */
#include "../twai_port.c"
#include "../twai_port_ll_esp32s3.c"
#include "stub.h"

#ifndef TWAI_PORT_HOST_SWITCH
#define TWAI_PORT_HOST_SWITCH 0
#endif
#define SWITCH_RUNS (TWAI_PORT_S3_PROBE || TWAI_PORT_HOST_SWITCH)

#include <stdio.h>
#include <string.h>

static int s_fail, s_checks;
static const char *s_case;

#define CHECK(cond) do { s_checks++; if (!(cond)) { s_fail++; \
    printf("FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, s_case, #cond); } } while (0)
#define CHECK_CALLS(want) do { s_checks++; if (strcmp(stub_calls(), (want)) != 0) { s_fail++; \
    printf("FAIL %s:%d [%s] calls\n  got:  %s\n  want: %s\n", __FILE__, __LINE__, \
           s_case, stub_calls(), (want)); } } while (0)

#define ST_TBS  TWAI_LL_STATUS_TBS
#define ST_TS   TWAI_LL_STATUS_TS
#define ST_RS   TWAI_LL_STATUS_RS

/* The frames the switch_discard hook was given, in order. */
#define MAX_SEEN 70
static uint32_t s_seen_tag[MAX_SEEN];
static uint32_t s_seen_id[MAX_SEEN];
static int s_seen;

/* The helpers below are unused in the build without the switch. */
#define HELPER static __attribute__((unused))

HELPER void on_discard(void *ctx, const twai_message_t *f, uint32_t tag)
{
    (void)ctx;
    if (s_seen < MAX_SEEN)
    {
        s_seen_tag[s_seen] = tag;
        s_seen_id[s_seen] = f->identifier;
        s_seen++;
    }
    if (stub.n_calls < STUB_MAX_CALLS)
    {
        stub.calls[stub.n_calls++] = "HOOK";
    }
}

static void reset(void)
{
    memset(&s_cfg, 0, sizeof(s_cfg));
    s_setup = false;
    memset(&s_timing, 0, sizeof(s_timing));
    s_mode = CAN_PORT_MODE_NORMAL;
    memset(&s_filter, 0, sizeof(s_filter));
    s_installed = false;
    s_last_step = TWAI_PORT_STEP_NONE;
    s_gen = 0;
    memset(&s_raw, 0, sizeof(s_raw));
    memset(&s_count, 0, sizeof(s_count));
    s_tx_failed_held = false;
    s_tx_block = false;
    memset(s_ring, 0, sizeof(s_ring));
    s_ring_n = 0;
    s_tx_failed_skip = 0;
    s_ev_pending = 0;
    memset(&s_switch, 0, sizeof(s_switch));
    stub_reset();
    stub_twai_dev.status = ST_TBS;      /* buffer free, bus idle */
    s_seen = 0;
}

HELPER twai_status_info_t st(twai_state_t s, uint32_t to_tx)
{
    twai_status_info_t x;
    memset(&x, 0, sizeof(x));
    x.state = s;
    x.msgs_to_tx = to_tx;
    return x;
}

HELPER void clear_log(void)
{
    stub.n_calls = 0;
    stub.n_standby = 0;
}

#if SWITCH_RUNS

/* Set up a kept-driver port in `mode`, install it, clear the log. */
static void up(can_port_mode_t mode)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    c.kept_driver_switch = true;
    c.switch_discard = on_discard;
    c.mode = mode;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    clear_log();
}

static void send_ids(uint32_t first, int n)
{
    for (int i = 0; i < n; i++)
    {
        twai_message_t m;
        memset(&m, 0, sizeof(m));
        m.identifier = first + (uint32_t)i;
        CHECK(twai_port_send_tagged(&m, 0, 0x100u + m.identifier) == ESP_OK);
    }
}

static twai_port_switch_report_t last(void)
{
    twai_port_switch_report_t r;
    memset(&r, 0xA5, sizeof(r));
    CHECK(twai_port_last_switch(&r) == ESP_OK);
    return r;
}

static void case_setup(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.kept_driver_switch = true;
    c.tx_queue_len = TWAI_PORT_SWITCH_HELD_MAX;     /* + 1 does not fit */
    CHECK(twai_port_setup(&c) == ESP_ERR_INVALID_ARG);
    CHECK(!s_setup);
    c.tx_queue_len = TWAI_PORT_SWITCH_HELD_MAX - 1;
    CHECK(twai_port_setup(&c) == ESP_OK);
    twai_port_switch_report_t r;
    CHECK(twai_port_last_switch(&r) == ESP_OK && r.seq == 0);
    CHECK(twai_port_ll_switch_supported());
}

/* Set up in listen-only: installed in normal mode, switched before the
 * transceiver leaves standby, sends refused. */
static void case_install_listen_only(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    c.kept_driver_switch = true;
    c.mode = CAN_PORT_MODE_LISTEN_ONLY;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(stub.g.mode == TWAI_MODE_NORMAL);
    CHECK_CALLS("pins install start alerts status read_alerts status clear_tx standby_off");
    CHECK(stub_twai_dev.mode == TWAI_MODE_LISTEN_ONLY && stub_twai_dev.mode_set_in_reset);
    CHECK(stub_twai_dev.rec == 128 && stub_twai_dev.rec_set_in_reset);
    CHECK(stub_twai_dev.enter_reset_cmds == 1 && stub_twai_dev.exit_reset_cmds == 1);
    CHECK(!stub_twai_dev.reset_mode);
    CHECK(twai_port_mode() == CAN_PORT_MODE_LISTEN_ONLY);
    twai_port_switch_report_t r = last();
    CHECK(r.seq == 1 && r.result == ESP_OK && r.held == 0 && !r.head_aborted);
    CHECK(r.rec_written == 128 && r.rec_read_back == 128);

    clear_log();
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    CHECK(twai_port_send(&m, 0) == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("");
    CHECK(s_count.tx_refused == 1 && s_count.tx_frames == 0);
}

/*
 * Normal to listen-only with frames held: reset mode at once (T16's place),
 * one more alert poll (T18), the held frames passed up oldest first and
 * counted once (T17), the queue cleared, listen-only with REC 128 read back
 * (T15), the head frame aborted after the switch, and its transmit failure
 * not counted again.
 */
static void case_switch_to_listen_only(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x10, 5);
    CHECK(s_count.tx_frames == 5);
    clear_log();

    const twai_status_info_t seq[] = {
        st(TWAI_STATE_RUNNING, 3),      /* the switch's check */
        st(TWAI_STATE_RUNNING, 3),      /* in reset mode: 3 held */
        st(TWAI_STATE_RUNNING, 0),      /* the driver let the head go */
    };
    stub_status(3, seq);
    /* Transmitting as it went in. The stub's status does not change, so it
     * still reads TS = 1 after the switch, and the head is not aborted
     * (case_switch_aborts_head has the abort). */
    stub_twai_dev.status = ST_TS;
    stub.alerts = TWAI_ALERT_BUS_ERROR;     /* raised before the switch */
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);

    CHECK_CALLS("status read_alerts status clear_tx HOOK HOOK HOOK status read_alerts");
    CHECK(stub_twai_dev.delays_at_enter == 0);      /* no wait before it */
    CHECK(stub_twai_dev.status_at_enter == ST_TS);
    twai_port_switch_report_t r = last();
    CHECK(r.seq == 1 && r.to_mode == CAN_PORT_MODE_LISTEN_ONLY && r.result == ESP_OK);
    CHECK(r.status_at_reset == ST_TS && r.buf_at_reset == TWAI_PORT_BUF_TRANSMITTING);
    CHECK(r.held == 3);
    CHECK(r.rec_written == 128 && r.rec_read_back == 128);
    CHECK(s_seen == 3);
    CHECK(s_seen_id[0] == 0x12 && s_seen_id[1] == 0x13 && s_seen_id[2] == 0x14);
    CHECK(s_seen_tag[0] == 0x112 && s_seen_tag[2] == 0x114);
    CHECK(s_count.tx_discarded_switch == 3);
    CHECK(stub_twai_dev.mode == TWAI_MODE_LISTEN_ONLY && stub_twai_dev.mode_set_in_reset);
    CHECK(stub_twai_dev.rec_set_in_reset && !stub_twai_dev.reset_mode);
    CHECK(twai_port_mode() == CAN_PORT_MODE_LISTEN_ONLY);
    /* TS still read 1 here, so the head was not awaiting: no abort. */
    CHECK(!r.head_aborted && stub_twai_dev.abort_cmds == 0);
    CHECK(r.abort_wait_us == 0 && !r.abort_timed_out && !r.driver_tx_stuck);

    /* The bus error the switch's own poll took reaches the project's poll,
     * once. */
    clear_log();
    CHECK(twai_port_poll_alerts() == TWAI_PORT_EV_BUS_ERROR);
    CHECK(twai_port_poll_alerts() == 0);
    CHECK(!twai_port_take_tx_failed());
}

/* The head frame awaiting after the switch: aborted, its failure counted
 * once (in tx_discarded_switch, not tx_failed), its event not passed up. A
 * later real failure still counts. */
static void case_switch_aborts_head(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x20, 2);
    clear_log();
    const twai_status_info_t seq[] = {
        st(TWAI_STATE_RUNNING, 2),
        st(TWAI_STATE_RUNNING, 2),
        st(TWAI_STATE_RUNNING, 1),      /* the interrupt not yet run */
        st(TWAI_STATE_RUNNING, 0),
    };
    stub_status(4, seq);
    stub.status[3].tx_failed_count = 1;
    stub_twai_dev.status = 0;           /* awaiting: TBS 0, TS 0 */
    stub.alerts = 0;
    stub.alerts_then = TWAI_ALERT_TX_FAILED;
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK_CALLS("status read_alerts status clear_tx HOOK HOOK status status read_alerts");
    twai_port_switch_report_t r = last();
    CHECK(r.held == 2 && r.head_aborted && stub_twai_dev.abort_cmds == 1);
    CHECK(r.abort_wait_us == SWITCH_ABORT_WAIT_STEP_US && !r.abort_timed_out);
    CHECK(!r.driver_tx_stuck);
    CHECK(s_count.tx_discarded_switch == 2 && s_count.tx_failed == 0);
    CHECK(s_tx_failed_skip == 0);
    CHECK(!twai_port_take_tx_failed());
    CHECK((twai_port_poll_alerts() & TWAI_PORT_EV_TX_FAILED) == 0);

    /* A later failure the driver counts is a real one. */
    twai_status_info_t later = st(TWAI_STATE_RUNNING, 0);
    later.tx_failed_count = 2;
    stub_status(1, &later);
    can_port_status_t s;
    CHECK(twai_port_status(&s) == ESP_OK && s.tx_failed == 1);
}

/* A transmit failure held from before the switch survives it. */
static void case_switch_keeps_earlier_failure(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x30, 1);
    clear_log();
    const twai_status_info_t seq[] = {
        st(TWAI_STATE_RUNNING, 1),
        st(TWAI_STATE_RUNNING, 1),
        st(TWAI_STATE_RUNNING, 0),
    };
    stub_status(3, seq);
    stub_twai_dev.status = 0;
    stub.alerts = TWAI_ALERT_TX_FAILED;         /* a real one, before */
    stub.alerts_then = TWAI_ALERT_TX_FAILED;    /* the abort's */
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(twai_port_take_tx_failed());
    CHECK((twai_port_poll_alerts() & TWAI_PORT_EV_TX_FAILED) != 0);
}

/* The driver never lets the head go, and the buffer reads empty: reported,
 * the wait bounded, and no later failure is taken off. */
static void case_switch_driver_stuck(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x40, 1);
    clear_log();
    const twai_status_info_t seq[] = { st(TWAI_STATE_RUNNING, 1) };
    stub_status(1, seq);
    stub_twai_dev.status = ST_TBS;      /* released, with no interrupt */
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    twai_port_switch_report_t r = last();
    CHECK(r.held == 1 && !r.head_aborted && stub_twai_dev.abort_cmds == 0);
    CHECK(r.abort_timed_out && r.abort_wait_us == SWITCH_ABORT_WAIT_MAX_US);
    CHECK(r.driver_tx_stuck);
    CHECK(stub.delay_us_total == SWITCH_ABORT_WAIT_MAX_US);
    CHECK(s_tx_failed_skip == 0);
}

/* The head aborted, but the driver never counts it: the wait is bounded, and
 * the failure the driver never counted is not taken off a later real one. */
static void case_switch_abort_never_seen(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x48, 1);
    clear_log();
    const twai_status_info_t seq[] = { st(TWAI_STATE_RUNNING, 1) };
    stub_status(1, seq);
    stub_twai_dev.status = 0;           /* awaiting, and stays so */
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    twai_port_switch_report_t r = last();
    CHECK(r.head_aborted && r.abort_timed_out && !r.driver_tx_stuck);
    CHECK(s_tx_failed_skip == 0);
    twai_status_info_t later = st(TWAI_STATE_RUNNING, 1);
    later.tx_failed_count = 1;
    stub_status(1, &later);
    can_port_status_t s;
    CHECK(twai_port_status(&s) == ESP_OK && s.tx_failed == 1);
}

/* The abort wait is elapsed time from esp_timer, not the sum of its 10 us
 * steps: 90 us pass outside the waits at each clock read (an interrupt or
 * another task between steps), and both the report and the 1 ms bound count
 * them. Read just after each status read, from just after the abort. */
static void case_switch_abort_wait_elapsed(void)
{
    /* Never let go: the bound is reached after 11 reads, 1090 us elapsed,
     * with only 10 waits (100 us) made. The clock reads at i-th status read:
     * 100 i - 10 us from the abort. */
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x50, 1);
    clear_log();
    const twai_status_info_t stuck[] = { st(TWAI_STATE_RUNNING, 1) };
    stub_status(1, stuck);
    stub_twai_dev.status = ST_TBS;
    stub.timer_per_read_us = 90;
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    twai_port_switch_report_t r = last();
    CHECK(r.abort_timed_out && r.driver_tx_stuck);
    CHECK(r.abort_wait_us == 1090);
    CHECK(stub.delay_us_total == 100);
    CHECK(stub.timer_reads == 12);      /* t0, then one per status read */
}

/* As above, let go at the second read in the wait: 190 us, one 10 us wait. */
static void case_switch_abort_wait_elapsed_freed(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x58, 2);
    clear_log();
    const twai_status_info_t freed[] = {
        st(TWAI_STATE_RUNNING, 2),
        st(TWAI_STATE_RUNNING, 2),
        st(TWAI_STATE_RUNNING, 1),
        st(TWAI_STATE_RUNNING, 0),
    };
    stub_status(4, freed);
    stub.status[3].tx_failed_count = 1;
    stub_twai_dev.status = 0;
    stub.alerts_then = TWAI_ALERT_TX_FAILED;
    stub.timer_per_read_us = 90;
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    const twai_port_switch_report_t r = last();
    CHECK(r.head_aborted && !r.abort_timed_out && !r.driver_tx_stuck);
    CHECK(r.abort_wait_us == 190);
    CHECK(stub.delay_us_total == SWITCH_ABORT_WAIT_STEP_US);
    CHECK(stub.timer_reads == 3);
}

/* Back to normal: reset mode, normal, REC 0 read back, sends taken again. */
static void case_switch_to_normal(void)
{
    up(CAN_PORT_MODE_LISTEN_ONLY);
    stub_twai_dev.rec = 128;
    stub_twai_dev.tec = 0;
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NORMAL) == ESP_OK);
    CHECK_CALLS("status read_alerts");
    CHECK(stub_twai_dev.mode == TWAI_MODE_NORMAL && stub_twai_dev.mode_set_in_reset);
    CHECK(stub_twai_dev.rec == 0 && stub_twai_dev.rec_set_in_reset);
    CHECK(!stub_twai_dev.reset_mode);
    twai_port_switch_report_t r = last();
    CHECK(r.seq == 2 && r.to_mode == CAN_PORT_MODE_NORMAL && r.held == 0);
    CHECK(r.rec_written == 0 && r.rec_read_back == 0);
    CHECK(twai_port_mode() == CAN_PORT_MODE_NORMAL);
    clear_log();
    send_ids(0x50, 1);
    CHECK_CALLS("transmit");
}

/* Not running: refused, nothing touched, the mode kept. */
static void case_switch_refused_bus_off(void)
{
    up(CAN_PORT_MODE_NORMAL);
    const twai_status_info_t seq[] = { st(TWAI_STATE_BUS_OFF, 4) };
    stub_status(1, seq);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("status");
    CHECK(stub_twai_dev.enter_reset_cmds == 0);
    CHECK(twai_port_mode() == CAN_PORT_MODE_NORMAL && !s_tx_block);
    twai_port_switch_report_t r = last();
    CHECK(r.seq == 1 && r.result == ESP_ERR_INVALID_STATE);
    CHECK(s_count.tx_discarded_switch == 0);
}

/* A change to or from no-ACK self-test is still T4's teardown and install. */
static void case_no_ack_is_t4(void)
{
    up(CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NO_ACK_SELF_TEST) == ESP_OK);
    CHECK(stub_count("uninstall") == 1 && stub_count("install") == 1);
    CHECK(stub.g.mode == TWAI_MODE_NO_ACK);
    clear_log();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(stub_count("uninstall") == 1 && stub_count("install") == 1);
    CHECK(stub.g.mode == TWAI_MODE_NORMAL && s_tx_block);
}

/* T15: on a kept-driver port the counters come from the registers. */
static void case_counters_from_registers(void)
{
    up(CAN_PORT_MODE_LISTEN_ONLY);
    twai_status_info_t x = st(TWAI_STATE_RUNNING, 0);
    x.tx_error_counter = 0;
    x.rx_error_counter = 0;
    stub_status(1, &x);
    stub_twai_dev.tec = 7;
    stub_twai_dev.rec = 130;
    can_port_status_t s;
    CHECK(twai_port_status(&s) == ESP_OK && s.tec == 7 && s.rec == 130);
}

/* The record of frames sent wraps: the newest are the ones handed back. */
static void case_ring_wraps(void)
{
    up(CAN_PORT_MODE_NORMAL);
    send_ids(0x100, (int)TWAI_PORT_SWITCH_HELD_MAX + 5);
    const twai_status_info_t seq[] = {
        st(TWAI_STATE_RUNNING, 4),
        st(TWAI_STATE_RUNNING, 4),
        st(TWAI_STATE_RUNNING, 0),
    };
    stub_status(3, seq);
    stub_twai_dev.status = 0;
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    const uint32_t newest = 0x100 + TWAI_PORT_SWITCH_HELD_MAX + 4;
    CHECK(s_seen == 4 && s_seen_id[0] == newest - 3 && s_seen_id[3] == newest);
}

#else   /* !SWITCH_RUNS */

/* Not measured: the switch refused at set-up. */
static void case_switch_refused(void)
{
    CHECK(!twai_port_ll_switch_supported());
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.kept_driver_switch = true;
    CHECK(twai_port_setup(&c) == ESP_ERR_NOT_SUPPORTED);
    c.kept_driver_switch = false;
    CHECK(twai_port_setup(&c) == ESP_OK);
}

#endif

#if TWAI_PORT_S3_PROBE

/* T8 on the S3, with the measurement switch. */
static void case_s3_abort(void)
{
    stub_twai_dev.status = ST_TBS;
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_EMPTY);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_EMPTY);
    stub_twai_dev.status = ST_TS;
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_TRANSMITTING);
    CHECK(stub_twai_dev.abort_cmds == 0);
    stub_twai_dev.status = 0;
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING);
    CHECK(stub_twai_dev.abort_cmds == 1);
}

/* T12 on the S3: the C3's steps, with the abort inline. */
static void case_s3_restart_stop(void)
{
    can_port_restart_report_t r;
    CHECK(twai_port_restart_stop(NULL) == ESP_ERR_INVALID_ARG);

    CHECK(stub.stalls == 0);            /* a NULL report: nothing done */

    /* The other core stalled first, even with the clock off. */
    stub.core_id = 1;
    stub.clk_en0 = 0;                   /* clock off: TWAI not touched */
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(stub.stalls == 1 && stub.stalled_core == 0);
    CHECK(!r.clock_on && !r.entered_reset && stub_twai_dev.enter_reset_cmds == 0);

    /* Already stalled (the panic handler's): not stalled again. Core 0's
     * code: C0 = 2 at bit 2, C1 = 0x21 at bit 26. */
    stub.rtc_options0 = 0x2u << 2;
    stub.rtc_sw_stall = 0x21u << 26;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(stub.stalls == 1);
    stub.rtc_sw_stall = 0x20u << 26;    /* half a code: not stalled */
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(stub.stalls == 2 && stub.stalled_core == 0);

    /* From core 0, core 1's fields. */
    stub.core_id = 0;
    stub.rtc_options0 = 0x2u << 0;
    stub.rtc_sw_stall = 0x21u << 20;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(stub.stalls == 2);
    stub.rtc_options0 = 0;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(stub.stalls == 3 && stub.stalled_core == 1);

    stub.clk_en0 = SYSTEM_TWAI_CLK_EN;
    stub.rst_en0 = SYSTEM_TWAI_RST;     /* held in reset: nothing touched */
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(r.held_in_reset && !r.entered_reset && stub_twai_dev.enter_reset_cmds == 0);

    stub.rst_en0 = 0;                   /* ours awaiting, another node's frame on */
    stub_twai_dev.status = ST_RS;
    stub_twai_dev.tec = 3;
    stub_twai_dev.rec = 9;
    stub.status_change_at = 5;
    stub.status_after = ST_TBS;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(r.clock_on && !r.held_in_reset && r.status_at_entry == ST_RS);
    CHECK(r.tec == 3 && r.rec == 9);
    CHECK(r.aborts == 5 && stub_twai_dev.abort_cmds == 5);  /* every poll */
    CHECK(r.wait_us == 10 && !r.timed_out);
    CHECK(r.entered_reset && stub_twai_dev.reset_mode);
    CHECK(r.status_at_reset == ST_TBS);

    stub_twai_dev.reset_mode = false;   /* never frees: the 1 ms bound */
    stub_twai_dev.status = ST_TS;
    stub.status_change_at = 0;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(r.timed_out && r.wait_us == RESTART_WAIT_MAX_US && r.entered_reset);
}

/* T19's stall record (twai_port_ll.h): the fields read at entry, written
 * only after T12's steps. */
static uint32_t s_magic_at_reset;
static void look_at_reset(void)
{
    s_magic_at_reset = s_stall_rec.magic;
}

static void case_s3_stall_record(void)
{
    twai_port_s3_stall_record_t s;
    can_port_restart_report_t r;
    twai_port_ll_s3_stall_record_clear();
    CHECK(!twai_port_ll_s3_stall_record(&s));

    /* Running from core 1, core 0 not stalled: not yet written as reset
     * mode is entered, written after it, with the raw fields as read. */
    stub.core_id = 1;
    stub.clk_en0 = SYSTEM_TWAI_CLK_EN;
    stub.rtc_options0 = 0x1u << 2;
    stub.rtc_sw_stall = 0x5u << 26;
    stub_twai_dev.status = ST_TBS;
    stub_twai_dev.on_enter_reset = look_at_reset;
    s_magic_at_reset = 1;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(r.entered_reset && stub_twai_dev.enter_reset_cmds == 1);
    CHECK(s_magic_at_reset == 0);
    CHECK(twai_port_ll_s3_stall_record(&s));
    CHECK(s.magic == TWAI_PORT_S3_STALL_MAGIC && s.core == 1 && s.other == 0);
    CHECK(s.options0 == (0x1u << 2) && s.sw_cpu_stall == (0x5u << 26));
    CHECK(s.stalled_at_entry == 0 && s.stall_called == 1 && s.entered_reset == 1);
    CHECK(stub.stalls == 1 && stub.stalled_core == 0);

    /* Cleared: not read again. */
    twai_port_ll_s3_stall_record_clear();
    CHECK(!twai_port_ll_s3_stall_record(&s));

    /* From core 0, core 1 already stalled, clock off: written at that
     * return too, with no stall called and no reset mode. */
    stub.core_id = 0;
    stub.clk_en0 = 0;
    stub.rtc_options0 = 0x2u << 0;
    stub.rtc_sw_stall = 0x21u << 20;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(twai_port_ll_s3_stall_record(&s));
    CHECK(s.core == 0 && s.other == 1);
    CHECK(s.options0 == (0x2u << 0) && s.sw_cpu_stall == (0x21u << 20));
    CHECK(s.stalled_at_entry == 1 && s.stall_called == 0 && s.entered_reset == 0);
    CHECK(stub.stalls == 1);

    /* Held in reset: written at that return as well. */
    twai_port_ll_s3_stall_record_clear();
    stub.clk_en0 = SYSTEM_TWAI_CLK_EN;
    stub.rst_en0 = SYSTEM_TWAI_RST;
    CHECK(twai_port_restart_stop(&r) == ESP_OK);
    CHECK(twai_port_ll_s3_stall_record(&s) && s.entered_reset == 0);

    /* A field changed after the write: the check refuses the record. */
    s_stall_rec.stall_called ^= 1u;
    CHECK(!twai_port_ll_s3_stall_record(&s));

    /* A NULL report: nothing done, nothing written. */
    twai_port_ll_s3_stall_record_clear();
    CHECK(twai_port_restart_stop(NULL) == ESP_ERR_INVALID_ARG);
    CHECK(!twai_port_ll_s3_stall_record(&s));
}

/* R20's mark for the measurement build (twai_port_ll.h). */
static void case_s3_probe_mark(void)
{
    CHECK(strstr(twai_port_s3_probe_build, "S3 probe build") != NULL);
}

#else   /* !TWAI_PORT_S3_PROBE */

/* Not measured: T8 and T12 unsupported, touching nothing (with the host
 * switch too: it changes the switch only). */
static void case_s3_unsupported(void)
{
    stub_twai_dev.status = 0;           /* would be awaiting */
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_UNSUPPORTED);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_UNSUPPORTED);
    CHECK(stub_twai_dev.abort_cmds == 0);
    can_port_restart_report_t r;
    memset(&r, 0xA5, sizeof(r));
    stub.clk_en0 = SYSTEM_TWAI_CLK_EN;
    CHECK(twai_port_restart_stop(&r) == ESP_ERR_NOT_SUPPORTED);
    CHECK(stub_twai_dev.enter_reset_cmds == 0 && stub.stalls == 0);
}

#endif

#if TWAI_PORT_HOST_SWITCH
/* R20's mark for the host-only switch (twai_port_ll.h). */
static void case_host_switch_mark(void)
{
    CHECK(strstr(twai_port_host_switch_build, "host switch build") != NULL);
}
#endif

int main(void)
{
    static const struct { const char *name; void (*fn)(void); } cases[] = {
#if SWITCH_RUNS
        { "setup", case_setup },
        { "install_listen_only", case_install_listen_only },
        { "switch_to_listen_only", case_switch_to_listen_only },
        { "switch_aborts_head", case_switch_aborts_head },
        { "switch_keeps_earlier_failure", case_switch_keeps_earlier_failure },
        { "switch_driver_stuck", case_switch_driver_stuck },
        { "switch_abort_never_seen", case_switch_abort_never_seen },
        { "switch_abort_wait_elapsed", case_switch_abort_wait_elapsed },
        { "switch_abort_wait_elapsed_freed", case_switch_abort_wait_elapsed_freed },
        { "switch_to_normal", case_switch_to_normal },
        { "switch_refused_bus_off", case_switch_refused_bus_off },
        { "no_ack_is_t4", case_no_ack_is_t4 },
        { "counters_from_registers", case_counters_from_registers },
        { "ring_wraps", case_ring_wraps },
#else
        { "switch_refused", case_switch_refused },
#endif
#if TWAI_PORT_S3_PROBE
        { "s3_abort", case_s3_abort },
        { "s3_restart_stop", case_s3_restart_stop },
        { "s3_stall_record", case_s3_stall_record },
        { "s3_probe_mark", case_s3_probe_mark },
#else
        { "s3_unsupported", case_s3_unsupported },
#endif
#if TWAI_PORT_HOST_SWITCH
        { "host_switch_mark", case_host_switch_mark },
#endif
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        s_case = cases[i].name;
        reset();
        cases[i].fn();
    }
    printf("%s: %d checks, %d failed, %u cases (S3%s)\n", s_fail ? "FAIL" : "PASS",
           s_checks, s_fail, (unsigned)(sizeof(cases) / sizeof(cases[0])),
           TWAI_PORT_S3_PROBE ? ", measurement build"
           : TWAI_PORT_HOST_SWITCH ? ", host switch" : "");
    return s_fail ? 1 : 0;
}
