/*
 * The port's unit tests (SPEC.md T11): twai_port.c and the C3 register file
 * against the scripted stub (stub.h). Each case scripts the driver's returns
 * and checks the calls the port makes and what it counts. Self-checks for the
 * implementor, not verification: acceptance runs on the testers' T10 fake.
 *
 * The port's files are included, not linked, so each case can put the port's
 * statics back to their start-up values (reset()).
 */
#include "../twai_port.c"
#include "../twai_port_ll_esp32c3.c"
#include "stub.h"

#include <stdio.h>
#include <string.h>

static int s_fail, s_checks;
static const char *s_case;

#define CHECK(cond) do { s_checks++; if (!(cond)) { s_fail++; \
    printf("FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, s_case, #cond); } } while (0)
#define CHECK_CALLS(want) do { s_checks++; if (strcmp(stub_calls(), (want)) != 0) { s_fail++; \
    printf("FAIL %s:%d [%s] calls\n  got:  %s\n  want: %s\n", __FILE__, __LINE__, \
           s_case, stub_calls(), (want)); } } while (0)

/* ---------------------------------------------------------------- setup -- */

static int s_hook_calls, s_teardown_calls;
static const char *s_hook_saw;          /* the calls made before the hook ran */
static twai_port_filter_t s_hook_replace;
static bool s_hook_replaces;

static void install_hook(void *ctx, twai_port_filter_t *f)
{
    (void)ctx;
    s_hook_calls++;
    s_hook_saw = stub_calls();
    if (s_hook_replaces)
    {
        *f = s_hook_replace;
    }
}

static void pre_teardown(void *ctx)
{
    (void)ctx;
    s_teardown_calls++;
    stub.calls[stub.n_calls++] = "HOOK";
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
    stub_reset();
    s_hook_calls = s_teardown_calls = 0;
    s_hook_saw = NULL;
    s_hook_replaces = false;
}

/* Set up with the defaults, report on failure, the two hooks. */
static void setup(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    c.install_hook = install_hook;
    c.pre_teardown = pre_teardown;
    CHECK(twai_port_setup(&c) == ESP_OK);
}

/* Set up and install, then clear the call log. */
static void up(void)
{
    setup();
    CHECK(twai_port_install() == ESP_OK);
    stub.n_calls = 0;
    stub.n_standby = 0;
}

static twai_status_info_t st(twai_state_t s)
{
    twai_status_info_t x;
    memset(&x, 0, sizeof(x));
    x.state = s;
    return x;
}

static can_port_status_t status(void)
{
    can_port_status_t s;
    memset(&s, 0xA5, sizeof(s));
    CHECK(twai_port_status(&s) == ESP_OK);
    return s;
}

/* ---------------------------------------------------------------- cases -- */

static void case_setup(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    CHECK(c.board_port == 0 && c.rx_queue_len == 64 && c.tx_queue_len == 32);
    CHECK(c.mode == CAN_PORT_MODE_NORMAL && c.recovery == TWAI_PORT_RECOVERY_NEVER);
    CHECK(c.on_install_fail == TWAI_PORT_INSTALL_FAIL_ABORT && !c.drop_rtr && !c.single_shot);
    CHECK(c.filter.acceptance_code == 0 && c.filter.acceptance_mask == 0xFFFFFFFF
          && c.filter.single_filter);

    CHECK(twai_port_install() == ESP_ERR_INVALID_STATE);    /* before setup */
    CHECK(stub.n_calls == 0);
    c.rx_queue_len = 0;
    CHECK(twai_port_setup(&c) == ESP_ERR_INVALID_ARG);
    c.rx_queue_len = 64;
    c.mode = (can_port_mode_t)7;
    CHECK(twai_port_setup(&c) == ESP_ERR_INVALID_ARG);
    CHECK(twai_port_setup(NULL) == ESP_ERR_INVALID_ARG);

    up();
    twai_port_config_defaults(&c);
    CHECK(twai_port_setup(&c) == ESP_ERR_INVALID_STATE);    /* while up */
}

static void case_install(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.board_port = 1;
    c.rx_queue_len = 100;
    c.tx_queue_len = 7;
    c.mode = CAN_PORT_MODE_LISTEN_ONLY;
    c.filter.acceptance_code = 0x123;
    c.filter.acceptance_mask = 0x7FF;
    c.install_hook = install_hook;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);

    /* The hook before anything else; the board's pins for its port; the
     * install, the start, the alerts, then out of standby. */
    CHECK(s_hook_calls == 1 && s_hook_saw != NULL && s_hook_saw[0] == '\0');
    CHECK_CALLS("pins install start alerts standby_off");
    CHECK(stub.board_port == 1);
    CHECK(stub.g.tx_io == 5 && stub.g.rx_io == 4);
    CHECK(stub.g.mode == TWAI_MODE_LISTEN_ONLY);
    CHECK(stub.g.rx_queue_len == 100 && stub.g.tx_queue_len == 7);
    CHECK(stub.f.acceptance_code == 0x123 && stub.f.acceptance_mask == 0x7FF);
    CHECK(stub.alerts_enabled == (TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED
                                  | TWAI_ALERT_BUS_ERROR | TWAI_ALERT_BUS_OFF
                                  | TWAI_ALERT_RX_QUEUE_FULL));
    CHECK(twai_port_is_installed());
    CHECK(status().bring_ups == 1);

    /* Already up: nothing. */
    stub.n_calls = 0;
    CHECK(twai_port_install() == ESP_OK);
    CHECK(stub.n_calls == 0 && s_hook_calls == 1);
}

static void case_install_hook_replaces_filter(void)
{
    s_hook_replaces = true;
    s_hook_replace.acceptance_code = 0;
    s_hook_replace.acceptance_mask = 0xFFFFFFFF;
    s_hook_replace.single_filter = true;
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.filter.acceptance_code = 0x51;
    c.filter.acceptance_mask = 0;
    c.install_hook = install_hook;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(stub.f.acceptance_code == 0 && stub.f.acceptance_mask == 0xFFFFFFFF);
    /* For that install only: the recorded filter is unchanged. */
    CHECK(s_filter.acceptance_code == 0x51 && s_filter.acceptance_mask == 0);
}

static void case_modes(void)
{
    setup();
    const struct { can_port_mode_t m; twai_mode_t t; } map[] = {
        { CAN_PORT_MODE_NORMAL,           TWAI_MODE_NORMAL },
        { CAN_PORT_MODE_LISTEN_ONLY,      TWAI_MODE_LISTEN_ONLY },
        { CAN_PORT_MODE_NO_ACK_SELF_TEST, TWAI_MODE_NO_ACK },
    };
    for (unsigned i = 0; i < sizeof(map) / sizeof(map[0]); i++)
    {
        CHECK(twai_port_set_mode(map[i].m) == ESP_OK);
        CHECK(twai_port_install() == ESP_OK);
        CHECK(stub.g.mode == map[i].t);
        CHECK(twai_port_teardown() == ESP_OK);
    }
    CHECK(twai_port_set_mode((can_port_mode_t)3) == ESP_ERR_INVALID_ARG);
}

static void case_install_failures(void)
{
    /* Each step, with report chosen: the error back, uninstalled, the step. */
    setup();
    stub.pins_ret = ESP_ERR_NOT_SUPPORTED;
    CHECK(twai_port_install() == ESP_ERR_NOT_SUPPORTED);
    CHECK_CALLS("pins");
    CHECK(!twai_port_is_installed() && twai_port_last_step() == TWAI_PORT_STEP_PINS);

    reset(); setup();
    stub.install_ret = ESP_ERR_NO_MEM;
    CHECK(twai_port_install() == ESP_ERR_NO_MEM);
    CHECK_CALLS("pins install");
    CHECK(!twai_port_is_installed() && twai_port_last_step() == TWAI_PORT_STEP_INSTALL);

    reset(); setup();
    stub.start_ret = ESP_ERR_INVALID_STATE;
    CHECK(twai_port_install() == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("pins install start uninstall");
    CHECK(!twai_port_is_installed() && twai_port_last_step() == TWAI_PORT_STEP_START);

    reset(); setup();
    stub.alerts_ret = ESP_ERR_INVALID_ARG;
    CHECK(twai_port_install() == ESP_ERR_INVALID_ARG);
    CHECK_CALLS("pins install start alerts stop uninstall");
    CHECK(!twai_port_is_installed() && twai_port_last_step() == TWAI_PORT_STEP_ALERTS);
    CHECK(status().bring_ups == 0);

    /* A later bring-up that works clears the step. */
    stub.alerts_ret = ESP_OK;
    CHECK(twai_port_install() == ESP_OK);
    CHECK(twai_port_last_step() == TWAI_PORT_STEP_NONE);
}

static void case_install_failure_aborts(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);     /* abort on failure, the default */
    CHECK(twai_port_setup(&c) == ESP_OK);
    stub.start_ret = ESP_ERR_INVALID_STATE;
    jmp_buf jb;
    stub.abort_to = &jb;
    if (setjmp(jb) == 0)
    {
        (void)twai_port_install();
        CHECK(!"returned from an aborting failure");
    }
    CHECK(stub.abort_err == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("pins install start uninstall ABORT");
    stub.abort_to = NULL;
}

static void case_teardown_running(void)
{
    up();
    twai_status_info_t seq[2] = { st(TWAI_STATE_RUNNING), st(TWAI_STATE_STOPPED) };
    seq[0].msgs_to_tx = 3;      /* before the stop, which empties TX */
    seq[0].msgs_to_rx = 1;
    seq[1].msgs_to_tx = 0;
    seq[1].msgs_to_rx = 4;      /* after it, just before the uninstall */
    stub_status(2, seq);
    CHECK(twai_port_teardown() == ESP_OK);
    CHECK_CALLS("HOOK status standby_on stop status uninstall");
    CHECK(!twai_port_is_installed());
    can_port_status_t s = status();
    CHECK(s.tx_discarded_teardown == 3 && s.rx_discarded_teardown == 4);
    CHECK(s.state == CAN_PORT_STATE_DOWN);

    /* Down: the hook still runs, and nothing else. */
    stub.n_calls = 0;
    CHECK(twai_port_teardown() == ESP_OK);
    CHECK_CALLS("HOOK");
    CHECK(s_teardown_calls == 2);
}

static void case_teardown_from_bus_off_and_stopped(void)
{
    const twai_state_t from[] = { TWAI_STATE_BUS_OFF, TWAI_STATE_STOPPED };
    for (int i = 0; i < 2; i++)
    {
        reset(); up();
        twai_status_info_t s0 = st(from[i]);
        s0.msgs_to_tx = 2;
        s0.msgs_to_rx = 5;
        stub_status(1, &s0);
        CHECK(twai_port_teardown() == ESP_OK);
        CHECK_CALLS("HOOK status standby_on uninstall");   /* no stop */
        can_port_status_t s = status();
        CHECK(s.tx_discarded_teardown == 2 && s.rx_discarded_teardown == 5);
    }
}

static void case_teardown_refused_recovering(void)
{
    up();
    twai_status_info_t s0 = st(TWAI_STATE_RECOVERING);
    s0.msgs_to_tx = 2;
    stub_status(1, &s0);
    CHECK(twai_port_teardown() == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("HOOK status");     /* standby untouched, no stop, no uninstall */
    CHECK(twai_port_is_installed());
    CHECK(stub_log_w == 1);
    CHECK(status().tx_discarded_teardown == 0);
}

static void case_teardown_step_failures(void)
{
    up();
    stub.stop_ret = ESP_ERR_INVALID_STATE;
    CHECK(twai_port_teardown() == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("HOOK status standby_on stop standby_off");
    CHECK(twai_port_is_installed());

    reset(); up();
    twai_status_info_t s0 = st(TWAI_STATE_BUS_OFF);
    s0.msgs_to_tx = 6;
    stub_status(1, &s0);
    stub.uninstall_ret = ESP_ERR_INVALID_STATE;
    CHECK(twai_port_teardown() == ESP_ERR_INVALID_STATE);
    /* Still installed: out of standby again, as when the stop is refused. */
    CHECK_CALLS("HOOK status standby_on uninstall standby_off");
    CHECK(twai_port_is_installed());
    CHECK(status().tx_discarded_teardown == 0);     /* booked only once gone */

    reset(); up();
    stub.status_ret[0] = ESP_ERR_INVALID_ARG;
    CHECK(twai_port_teardown() == ESP_ERR_INVALID_ARG);
    CHECK_CALLS("HOOK status");
    CHECK(twai_port_is_installed());
}

static void case_set_mode(void)
{
    /* Down: recorded for the next install, no driver call. */
    setup();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(stub.n_calls == 0 && twai_port_mode() == CAN_PORT_MODE_LISTEN_ONLY);

    /* Up, the same mode: nothing, not even the hook. */
    CHECK(twai_port_install() == ESP_OK);
    stub.n_calls = 0;
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(stub.n_calls == 0);

    /* Up, another: a teardown, then an install in the new mode. */
    twai_status_info_t seq[2] = { st(TWAI_STATE_RUNNING), st(TWAI_STATE_STOPPED) };
    stub_status(2, seq);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NORMAL) == ESP_OK);
    CHECK_CALLS("HOOK status standby_on stop status uninstall "
                "pins install start alerts standby_off");
    CHECK(stub.g.mode == TWAI_MODE_NORMAL && twai_port_mode() == CAN_PORT_MODE_NORMAL);
    CHECK(twai_port_is_installed());

    /* A refused teardown: the record and the controller in the old mode. */
    stub.n_calls = 0;
    twai_status_info_t rec = st(TWAI_STATE_RECOVERING);
    stub_status(1, &rec);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_ERR_INVALID_STATE);
    CHECK_CALLS("HOOK status");
    CHECK(twai_port_mode() == CAN_PORT_MODE_NORMAL && twai_port_is_installed());
}

static void case_configure(void)
{
    setup();
    const twai_timing_config_t t500 = TWAI_TIMING_CONFIG_500KBITS();
    const twai_timing_config_t t250 = TWAI_TIMING_CONFIG_250KBITS();
    const twai_port_filter_t all = { 0, 0xFFFFFFFF, true };
    CHECK(twai_port_install() == ESP_OK);

    /* Nothing changes: nothing done. */
    stub.n_calls = 0;
    CHECK(twai_port_configure(&t500, CAN_PORT_MODE_NORMAL, &all) == ESP_OK);
    CHECK(stub.n_calls == 0);

    /* The backend's configure: refused while up with other settings. */
    can_port_settings_t s = { .timing = t250, .mode = CAN_PORT_MODE_NORMAL,
                              .acceptance_code = 0, .acceptance_mask = 0xFFFFFFFF,
                              .single_filter = true };
    CHECK(twai_port_ops.configure(NULL, &s) == ESP_ERR_INVALID_STATE);
    CHECK(stub.n_calls == 0);
    s.timing = t500;
    CHECK(twai_port_ops.configure(NULL, &s) == ESP_OK);     /* the same: fine */

    /* Up and different, directly: a reinstall with the new timing. */
    twai_status_info_t seq[2] = { st(TWAI_STATE_RUNNING), st(TWAI_STATE_STOPPED) };
    stub_status(2, seq);
    CHECK(twai_port_configure(&t250, CAN_PORT_MODE_NORMAL, &all) == ESP_OK);
    CHECK(stub.t.quanta_resolution_hz == 5000000);
    CHECK(stub_count("uninstall") == 1 && stub_count("install") == 1);

    /* Down: the backend's configure records for the next install. */
    CHECK(twai_port_teardown() == ESP_OK);
    s.timing = t500;
    s.mode = CAN_PORT_MODE_LISTEN_ONLY;
    s.acceptance_code = 0x10;
    s.acceptance_mask = 0x20;
    CHECK(twai_port_ops.configure(NULL, &s) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(stub.t.quanta_resolution_hz == 10000000 && stub.g.mode == TWAI_MODE_LISTEN_ONLY);
    CHECK(stub.f.acceptance_code == 0x10 && stub.f.acceptance_mask == 0x20);
}

static void case_send(void)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = 0x51;
    m.data_length_code = 6;

    /* Down: refused and counted, the driver not called. */
    setup();
    CHECK(twai_port_send(&m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(stub.n_calls == 0 && status().tx_refused == 1);
    CHECK(twai_port_send(NULL, 0) == ESP_ERR_INVALID_ARG);
    CHECK(status().tx_refused == 2);

    CHECK(twai_port_install() == ESP_OK);
    stub.n_calls = 0;
    CHECK(twai_port_send(&m, 7) == ESP_OK);
    CHECK_CALLS("transmit");
    CHECK(stub.tx_wait == 7 && stub.tx.identifier == 0x51 && stub.tx.data_length_code == 6);
    CHECK(!stub.tx.ss && !stub.tx.self);
    CHECK(status().tx_frames == 1);

    stub.transmit_ret = ESP_ERR_TIMEOUT;    /* queue full */
    CHECK(twai_port_send(&m, 0) == ESP_ERR_TIMEOUT);
    stub.transmit_ret = ESP_FAIL;           /* no queue, buffer busy */
    CHECK(twai_port_send(&m, 0) == ESP_FAIL);
    stub.transmit_ret = ESP_ERR_INVALID_STATE;  /* bus-off */
    CHECK(twai_port_send(&m, 0) == ESP_ERR_INVALID_STATE);
    stub.transmit_ret = ESP_ERR_NOT_SUPPORTED;  /* listen-only */
    CHECK(twai_port_send(&m, 0) == ESP_ERR_NOT_SUPPORTED);
    can_port_status_t s = status();
    CHECK(s.tx_frames == 1 && s.tx_dropped == 2 && s.tx_refused == 4);

    /* Single-shot, and the self-test's self-reception, on every send. */
    reset();
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.single_shot = true;
    c.mode = CAN_PORT_MODE_NO_ACK_SELF_TEST;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(stub.tx.ss && stub.tx.self);
    CHECK(m.ss == 0 && m.self == 0);    /* the caller's frame untouched */
}

static void case_receive(void)
{
    twai_message_t out;
    setup();
    CHECK(twai_port_receive(&out, 10) == ESP_ERR_INVALID_STATE);
    CHECK(stub.n_calls == 0);
    CHECK(twai_port_install() == ESP_OK);
    stub.n_calls = 0;

    stub.rx[0].identifier = 0x51;
    stub.n_rx = 1;
    CHECK(twai_port_receive(&out, 200) == ESP_OK);
    CHECK(out.identifier == 0x51 && stub.rx_waits[0] == 200);
    CHECK(twai_port_receive(&out, 200) == ESP_ERR_TIMEOUT);
    CHECK(status().rx_frames == 1);

    /* Remote frames kept unless dropping was chosen. */
    reset(); setup();
    CHECK(twai_port_install() == ESP_OK);
    stub.rx[0].rtr = 1;
    stub.n_rx = 1;
    CHECK(twai_port_receive(&out, 0) == ESP_OK && out.rtr);

    /* Dropped: the wait goes on with what is left of it. */
    reset();
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.drop_rtr = true;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    stub.rx[0].rtr = 1;
    stub.rx[0].identifier = 0x100;
    stub.rx[1].identifier = 0x200;
    stub.n_rx = 2;
    stub.rx_tick_step = 30;
    CHECK(twai_port_receive(&out, 100) == ESP_OK);
    CHECK(out.identifier == 0x200 && !out.rtr);
    CHECK(stub.rx_waits[0] == 100 && stub.rx_waits[1] == 70);
    CHECK(status().rx_frames == 1);

    /* Past the wait, one more try at zero, never longer. */
    stub.rx[0].rtr = 1;
    stub.rx_i = 0;
    stub.n_rx = 1;
    stub.rx_tick_step = 150;
    CHECK(twai_port_receive(&out, 100) == ESP_ERR_TIMEOUT);
    CHECK(stub.rx_waits[0] == 100 && stub.rx_waits[1] == 0);
}

static void case_counts_carry_across_reinstalls(void)
{
    up();
    twai_status_info_t a = st(TWAI_STATE_RUNNING);
    a.rx_missed_count = 5;
    a.rx_overrun_count = 2;
    a.tx_failed_count = 1;
    a.bus_error_count = 9;
    a.arb_lost_count = 4;
    a.msgs_to_rx = 11;
    stub_status(1, &a);
    can_port_status_t s = status();
    CHECK(s.rx_lost == 7 && s.rx_lost_queue_full == 5 && s.rx_lost_overrun == 2);
    CHECK(s.tx_failed == 1 && s.bus_errors == 9 && s.arb_lost == 4);
    CHECK(s.rx_high_water == 11 && !s.rx_lost_is_bound && s.rx_queue_len == 64);
    CHECK(s.state == CAN_PORT_STATE_RUNNING && s.rx_waiting == 11);

    /* Reading the same totals again adds nothing; a stale lower read neither. */
    s = status();
    CHECK(s.rx_lost == 7 && s.bus_errors == 9);
    twai_status_info_t lower = a;
    lower.bus_error_count = 3;
    lower.msgs_to_rx = 2;
    stub_status(1, &lower);
    s = status();
    CHECK(s.bus_errors == 9 && s.rx_high_water == 11);

    /* A reinstall: the driver's counts restart at 0, the port's go on. */
    twai_status_info_t seq[2] = { a, a };
    seq[1].state = TWAI_STATE_STOPPED;
    stub_status(2, seq);
    CHECK(twai_port_teardown() == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_status_info_t b = st(TWAI_STATE_RUNNING);
    b.rx_missed_count = 1;
    b.bus_error_count = 2;
    stub_status(1, &b);
    s = status();
    CHECK(s.rx_lost == 8 && s.bus_errors == 11 && s.tx_failed == 1);
    CHECK(s.bring_ups == 2);

    /* Down: the counts as they stand, no driver call, the levels down. */
    twai_status_info_t seq2[2] = { b, b };
    seq2[1].state = TWAI_STATE_STOPPED;
    stub_status(2, seq2);
    CHECK(twai_port_teardown() == ESP_OK);
    stub.n_calls = 0;
    s = status();
    CHECK(stub.n_calls == 0);
    CHECK(s.state == CAN_PORT_STATE_DOWN && s.tx_pending == 0 && s.rx_waiting == 0);
    CHECK(s.tec == 0 && s.rec == 0 && s.rx_lost == 8 && s.bus_errors == 11);
}

static void case_bus_off_once_per_episode(void)
{
    up();
    twai_status_info_t off = st(TWAI_STATE_BUS_OFF);
    stub_status(1, &off);
    CHECK(status().bus_off_entries == 1);
    CHECK(status().bus_off_entries == 1);   /* the same episode */
    CHECK(status().bus_off);

    /* The alert as well as the read: still one. */
    stub.alerts = TWAI_ALERT_BUS_OFF;
    CHECK(twai_port_poll_alerts() & TWAI_PORT_EV_BUS_OFF);
    CHECK(status().bus_off_entries == 1);

    twai_status_info_t on = st(TWAI_STATE_RUNNING);
    stub_status(1, &on);
    CHECK(!status().bus_off);

    /* A second episode, seen first by the alert. */
    stub.alerts = TWAI_ALERT_BUS_OFF;
    (void)twai_port_poll_alerts();
    stub_status(1, &off);
    can_port_status_t s = status();
    CHECK(s.bus_off_entries == 2 && s.bus_off);
    /* Recovering is still the episode. */
    twai_status_info_t rec = st(TWAI_STATE_RECOVERING);
    stub_status(1, &rec);
    CHECK(status().bus_off_entries == 2);

    /* Never recovering: no recovery started, nothing counted discarded. */
    CHECK(stub_count("recovery") == 0 && s.tx_discarded_bus_off == 0);
}

static void case_alerts(void)
{
    setup();
    CHECK(twai_port_poll_alerts() == 0);
    CHECK(stub.n_calls == 0);           /* down: no read */
    CHECK(twai_port_install() == ESP_OK);

    stub.alerts = TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED | TWAI_ALERT_BUS_ERROR
                  | TWAI_ALERT_RX_QUEUE_FULL;
    const uint32_t ev = twai_port_poll_alerts();
    CHECK(ev == (TWAI_PORT_EV_TX_SUCCESS | TWAI_PORT_EV_TX_FAILED
                 | TWAI_PORT_EV_BUS_ERROR | TWAI_PORT_EV_RX_QUEUE_FULL));
    /* Held until taken, across later polls; taken once. */
    CHECK(twai_port_poll_alerts() == 0);
    CHECK(twai_port_take_tx_failed());
    CHECK(!twai_port_take_tx_failed());

    /* Events count nothing (T5). */
    can_port_status_t s = status();
    CHECK(s.bus_errors == 0 && s.rx_lost == 0 && s.tx_failed == 0);

    /* A failed read is no events. */
    stub.alerts = TWAI_ALERT_TX_FAILED;
    stub.read_alerts_ret = ESP_ERR_TIMEOUT;
    CHECK(twai_port_poll_alerts() == 0 && !twai_port_take_tx_failed());

    /* A held failure does not survive a reinstall. */
    stub.read_alerts_ret = ESP_OK;
    stub.alerts = TWAI_ALERT_TX_FAILED;
    (void)twai_port_poll_alerts();
    twai_status_info_t seq[2] = { st(TWAI_STATE_RUNNING), st(TWAI_STATE_STOPPED) };
    stub_status(2, seq);
    CHECK(twai_port_teardown() == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(!twai_port_take_tx_failed());
}

static void case_auto_recovery(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.recovery = TWAI_PORT_RECOVERY_AUTO;
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    stub.n_calls = 0;

    twai_status_info_t off = st(TWAI_STATE_BUS_OFF);
    off.msgs_to_tx = 4;
    stub_status(1, &off);
    stub.alerts = TWAI_ALERT_BUS_OFF;
    (void)twai_port_poll_alerts();
    CHECK_CALLS("read_alerts status recovery");     /* counted before recovery */
    can_port_status_t s = status();
    CHECK(s.tx_discarded_bus_off == 4 && s.bus_off_entries == 1);

    /* Recovered: stopped, so service starts it, and the episode ends. The
     * start resets the receive queue: the frames waiting in it are receive
     * losses, a part of their own (T7). */
    twai_status_info_t stopped = st(TWAI_STATE_STOPPED);
    stopped.msgs_to_rx = 3;
    stopped.rx_missed_count = 2;
    stub_status(1, &stopped);
    stub.n_calls = 0;
    twai_port_service();
    CHECK_CALLS("read_alerts status start");
    s = status();
    CHECK(!s.bus_off);
    CHECK(s.rx_lost_restart == 3 && s.rx_lost_queue_full == 2 && s.rx_lost == 5);
    CHECK(s.rx_flushed == 0 && s.rx_discarded_teardown == 0);

    /* A start that fails resets nothing, and counts nothing. */
    stub.start_ret = ESP_ERR_INVALID_STATE;
    twai_port_service();
    s = status();
    CHECK(s.rx_lost_restart == 3 && s.rx_lost == 5);
    stub.start_ret = ESP_OK;

    /* Never recovering: service starts nothing. */
    reset();
    twai_port_config_defaults(&c);
    CHECK(twai_port_setup(&c) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    stub_status(1, &stopped);
    stub.n_calls = 0;
    twai_port_service();
    CHECK_CALLS("read_alerts status");
}

/* T7: a flush counts the frames it discards, read just before the clear,
 * apart from the losses. */
static void case_flush(void)
{
    setup();
    twai_port_flush_rx();
    CHECK(stub.n_calls == 0);
    CHECK(twai_port_install() == ESP_OK);
    twai_status_info_t waiting = st(TWAI_STATE_RUNNING);
    waiting.msgs_to_rx = 7;
    stub_status(1, &waiting);
    stub.n_calls = 0;
    twai_port_flush_rx();
    CHECK_CALLS("status clear_rx");
    can_port_status_t s = status();
    CHECK(s.rx_flushed == 7 && s.rx_lost == 0);

    /* A clear that fails discards nothing, and counts nothing. */
    stub.clear_rx_ret = ESP_ERR_INVALID_STATE;
    twai_port_flush_rx();
    CHECK(status().rx_flushed == 7);
    stub.clear_rx_ret = ESP_OK;

    /* A status that will not read: the clear still runs, uncounted. */
    stub.status_ret[0] = ESP_ERR_INVALID_STATE;
    stub.n_calls = 0;
    twai_port_flush_rx();
    CHECK_CALLS("status clear_rx");
    stub.status_ret[0] = ESP_OK;
    CHECK(status().rx_flushed == 7);
}

static void case_backend_ops(void)
{
    const can_port_ops_t *o = &twai_port_ops;
    CHECK(o->up && o->down && o->is_up && o->set_mode && o->configure && o->receive
          && o->send && o->service && o->status && o->flush_rx);
    CHECK(strcmp(o->name, "TWAI") == 0);
    setup();
    CHECK(o->up(NULL) == ESP_OK && o->is_up(NULL));
    twai_status_info_t seq[2] = { st(TWAI_STATE_RUNNING), st(TWAI_STATE_STOPPED) };
    stub_status(2, seq);
    CHECK(o->down(NULL) == ESP_OK && !o->is_up(NULL));
    CHECK(s_teardown_calls == 1);
}

/* T8 on the C3: the buffer's state from TBS and TS; the abort only when
 * awaiting. */
static void case_c3_abort(void)
{
    stub_twai_dev.status = TWAI_LL_STATUS_TBS;
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_EMPTY);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_EMPTY);
    stub_twai_dev.status = TWAI_LL_STATUS_TS;
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_TRANSMITTING);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_TRANSMITTING);
    CHECK(stub_twai_dev.abort_cmds == 0);
    stub_twai_dev.status = 0;
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_AWAITING);
    CHECK(stub_twai_dev.abort_cmds == 0);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING);
    CHECK(stub_twai_dev.abort_cmds == 1);
    /* TBS wins over TS, as the register reads it. */
    stub_twai_dev.status = TWAI_LL_STATUS_TBS | TWAI_LL_STATUS_TS;
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_EMPTY);
    CHECK(stub_twai_dev.abort_cmds == 1);
}

int main(void)
{
    static const struct { const char *name; void (*fn)(void); } cases[] = {
        { "setup", case_setup },
        { "install", case_install },
        { "install_hook_replaces_filter", case_install_hook_replaces_filter },
        { "modes", case_modes },
        { "install_failures", case_install_failures },
        { "install_failure_aborts", case_install_failure_aborts },
        { "teardown_running", case_teardown_running },
        { "teardown_from_bus_off_and_stopped", case_teardown_from_bus_off_and_stopped },
        { "teardown_refused_recovering", case_teardown_refused_recovering },
        { "teardown_step_failures", case_teardown_step_failures },
        { "set_mode", case_set_mode },
        { "configure", case_configure },
        { "send", case_send },
        { "receive", case_receive },
        { "counts_carry_across_reinstalls", case_counts_carry_across_reinstalls },
        { "bus_off_once_per_episode", case_bus_off_once_per_episode },
        { "alerts", case_alerts },
        { "auto_recovery", case_auto_recovery },
        { "flush", case_flush },
        { "backend_ops", case_backend_ops },
        { "c3_abort", case_c3_abort },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        s_case = cases[i].name;
        reset();
        cases[i].fn();
    }
    printf("%s: %d checks, %d failed, %u cases\n", s_fail ? "FAIL" : "PASS",
           s_checks, s_fail, (unsigned)(sizeof(cases) / sizeof(cases[0])));
    return s_fail ? 1 : 0;
}
