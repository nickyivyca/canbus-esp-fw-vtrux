/*
 * The real twai_port, compiled as shipped, on the fake (SPEC.md T10: "Both
 * projects' host builds compile the real twai_port on it").
 *
 * Built twice: with the C3's register file on the fake's C3, and with the
 * unsupported register file on the fake's S3 (-DPORT_ON_S3). The cases are
 * written from SPEC.md T2 to T9 and the fake's model of the driver, not
 * from the port's code: they show the port runs on the fake and does what
 * the spec says there. They are harness self-checks. What counts for
 * twai_port's VERIFICATION.md is the acceptance run on a build the
 * reviewer names.
 *
 *   make -C shared/twai_port/test run
 */
#include "twai_port.h"
#include "twai_fake.h"

#include <stdio.h>
#include <string.h>

extern int host_error_checks, host_standby, host_log_e;

static int g_fails, g_checks;
static const char *g_case;

#define CHECK(cond) do { g_checks++; if (!(cond)) { g_fails++; \
    printf("FAIL %s: %s (line %d)\n", g_case, #cond, __LINE__); } } while (0)

static const uint8_t D8[8] = {1, 2, 3, 4, 5, 6, 7, 8};
static int g_pre_teardowns, g_install_hooks;

static void pre_teardown(void *ctx) { (void)ctx; g_pre_teardowns++; }

static void install_hook(void *ctx, twai_port_filter_t *f)
{
    (void)ctx;
    g_install_hooks++;
    if (g_install_hooks == 1)
    {
        /* The first install only: accept 0x123 alone. */
        f->acceptance_code = 0x123u << 21;
        f->acceptance_mask = ~(0x7FFu << 21);
        f->single_filter = true;
    }
}

/*
 * A fresh port and fake. The port's state is file statics with no reset
 * hook, so each case is its own process (main's argument), as the
 * interposer's L2 cases are.
 */
static void fresh(twai_port_config_t *cfg)
{
    twai_fake_reset();
#ifdef PORT_ON_S3
    twai_fake_set_chip(TWAI_FAKE_CHIP_S3);
#endif
    twai_port_config_defaults(cfg);
}

static twai_message_t msg(uint32_t id)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = id;
    m.data_length_code = 8;
    memcpy(m.data, D8, 8);
    return m;
}

static can_port_status_t st(void)
{
    can_port_status_t s;
    memset(&s, 0, sizeof(s));
    CHECK(twai_port_status(&s) == ESP_OK);
    return s;
}

static bool wait_hook(int64_t deadline, void *ctx)
{
    (void)ctx;
    int64_t step = deadline - twai_fake_now();
    twai_fake_advance(step < 1000 ? step : 1000);
    return true;
}

/* ------------------------------------------------------------- cases -- */

static void case_bring_up(void)
{
    twai_port_config_t cfg;
    const uint32_t events = TWAI_ALERT_TX_FAILED | TWAI_ALERT_TX_SUCCESS
                          | TWAI_ALERT_BUS_ERROR | TWAI_ALERT_BUS_OFF
                          | TWAI_ALERT_RX_QUEUE_FULL;
    g_case = "T2/T5: defaults installed and started, the events' alerts on, out of standby";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(twai_port_is_installed());
    CHECK(twai_fake_state() == TWAI_STATE_RUNNING);
    CHECK(twai_fake_mode() == TWAI_MODE_NORMAL);
    CHECK(twai_fake_installed_general()->rx_queue_len == 64);
    CHECK(twai_fake_installed_general()->tx_queue_len == 32);
    CHECK(twai_fake_installed_brp() == 8);
    CHECK((twai_fake_alerts_enabled() & events) == events);
    CHECK(host_standby == 0);
    CHECK(twai_port_install() == ESP_OK);           /* already up */
    CHECK(twai_fake_install_count() == 1);
    CHECK(twai_fake_wrong_state_calls() == 0);
}

static void case_teardown(void)
{
    twai_port_config_t cfg;
    g_case = "T3: the hook, then stop and uninstall; down only after; standby on";
    fresh(&cfg);
    cfg.pre_teardown = pre_teardown;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(twai_port_teardown() == ESP_OK);
    CHECK(g_pre_teardowns == 1);
    CHECK(!twai_port_is_installed() && !twai_fake_installed());
    CHECK(host_standby == 1);
    CHECK(twai_port_teardown() == ESP_OK);          /* already down, after the hook */
    CHECK(g_pre_teardowns == 2);
    CHECK(twai_fake_wrong_state_calls() == 0);
    CHECK(twai_fake_unsafe_teardowns() == 0);
}

static void case_teardown_recovering(void)
{
    twai_port_config_t cfg;
    can_port_status_t s;
    g_case = "T3: refused while recovering, still installed and reported installed";
    fresh(&cfg);
    cfg.recovery = TWAI_PORT_RECOVERY_AUTO;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_set_auto_recovery(false);
    twai_fake_bus_off();
    (void)twai_port_poll_alerts();                  /* auto: recovery starts */
    CHECK(twai_fake_state() == TWAI_STATE_RECOVERING);
    CHECK(twai_port_teardown() == ESP_ERR_INVALID_STATE);
    CHECK(twai_port_is_installed() && twai_fake_installed());
    s = st();
    CHECK(s.state == CAN_PORT_STATE_RECOVERING);
    twai_fake_complete_recovery();
    twai_port_service();                            /* recovered: start again */
    CHECK(twai_fake_state() == TWAI_STATE_RUNNING);
    s = st();
    CHECK(s.bus_off_entries == 1 && !s.bus_off);
}

static void case_queues_at_teardown(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100);
    can_port_status_t s;
    g_case = "T4/T7: frames in the TX and RX queues at a teardown are counted";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_stall_next(1);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    twai_fake_deliver_std(0x10, D8, 8);
    twai_fake_deliver_std(0x11, D8, 8);
    s = st();
    CHECK(s.tx_pending == 3 && s.rx_waiting == 2);
    CHECK(twai_port_teardown() == ESP_OK);
    s = st();
    CHECK(s.tx_discarded_teardown == 3 && s.rx_discarded_teardown == 2);
    CHECK(s.state == CAN_PORT_STATE_DOWN);
}

static void case_send_counts(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100);
    can_port_status_t s;
    g_case = "T6/T7: a full TX queue counts tx_dropped, the port down tx_refused";
    fresh(&cfg);
    cfg.tx_queue_len = 2;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_stall_next(1);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_send(&m, 0) != ESP_OK);
    s = st();
    CHECK(s.tx_frames == 3 && s.tx_dropped == 1 && s.tx_refused == 0);
    CHECK(twai_port_teardown() == ESP_OK);
    CHECK(twai_port_send(&m, 0) != ESP_OK);
    s = st();
    CHECK(s.tx_dropped == 1 && s.tx_refused == 1);
}

static void case_counts_across_reinstall(void)
{
    twai_port_config_t cfg;
    can_port_status_t s;
    g_case = "T7: losses carried across a reinstall as deltas, never backwards";
    fresh(&cfg);
    cfg.rx_queue_len = 2;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_deliver_std(0x10, D8, 8);
    twai_fake_deliver_std(0x11, D8, 8);
    twai_fake_deliver_std(0x12, D8, 8);
    twai_fake_rx_overrun(2);
    twai_fake_bus_error(3);
    s = st();
    CHECK(s.rx_lost == 3 && s.rx_lost_queue_full == 1 && s.rx_lost_overrun == 2);
    CHECK(s.bus_errors == 3 && s.rx_high_water == 2);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    s = st();
    CHECK(s.rx_lost == 3 && s.bus_errors == 3);
    twai_fake_deliver_std(0x13, D8, 8);
    twai_fake_deliver_std(0x14, D8, 8);
    twai_fake_deliver_std(0x15, D8, 8);
    s = st();
    CHECK(s.rx_lost == 4 && s.rx_lost_queue_full == 2);
    CHECK(s.bring_ups == 2);
}

static void case_mode_change(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100);
    can_port_status_t s;
    g_case = "T2/T4: a mode change reinstalls; the same mode does nothing; listen-only refuses sends";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(twai_fake_mode() == TWAI_MODE_LISTEN_ONLY && twai_fake_install_count() == 2);
    CHECK(twai_port_mode() == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(twai_fake_install_count() == 2);
    CHECK(twai_port_send(&m, 0) != ESP_OK);
    s = st();
    CHECK(s.tx_refused == 1 && s.tx_dropped == 0);
    CHECK(twai_fake_sent_count() == 0);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NO_ACK_SELF_TEST) == ESP_OK);
    CHECK(twai_fake_mode() == TWAI_MODE_NO_ACK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_fake_sent(0)->self);                 /* the chip's loopback */
    CHECK(twai_fake_rx_pending() == 1);
}

static void case_register_file(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100);
    g_case = "T8: the buffer state and the abort-if-awaiting";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
#ifdef PORT_ON_S3
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_UNSUPPORTED);
    twai_fake_stall_next(1);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_UNSUPPORTED);
    CHECK(twai_fake_ll_aborts() == 0);
    CHECK(twai_fake_ll_unmeasured_calls() == 0);
#else
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_EMPTY);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_TRANSMITTING);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_TRANSMITTING);
    CHECK(twai_fake_ll_aborts() == 0);
    twai_fake_advance(1000);
    twai_fake_stall_next(1);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_AWAITING);
    CHECK(twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING);
    CHECK(twai_fake_ll_aborts() == 1 && twai_fake_ll_removed() == 1);
    CHECK(twai_port_buf_state() == TWAI_PORT_BUF_EMPTY);
    CHECK(twai_fake_wire_count() == 1);
#endif
}

static void case_bus_off_once(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100);
    can_port_status_t s;
    uint32_t ev;
    g_case = "T5/T7: bus-off one event and one entry per episode; never recovers by default";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_stall_next(1);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    twai_fake_bus_off();
    ev = twai_port_poll_alerts();
    CHECK((ev & TWAI_PORT_EV_BUS_OFF) != 0);
    ev = twai_port_poll_alerts();
    CHECK((ev & TWAI_PORT_EV_BUS_OFF) == 0);
    twai_port_service();
    s = st();
    CHECK(s.bus_off_entries == 1 && s.bus_off && s.state == CAN_PORT_STATE_BUS_OFF);
    CHECK(twai_fake_state() == TWAI_STATE_BUS_OFF);
    CHECK(twai_port_teardown() == ESP_OK);          /* uninstall from bus-off */
    CHECK(!twai_fake_installed());
}

static void case_install_fail(void)
{
    twai_port_config_t cfg;
    g_case = "T2/T4: a failed install or start reported, the step named, left uninstalled";
    fresh(&cfg);
    cfg.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    twai_fake_fail_install_next(ESP_ERR_NO_MEM);
    CHECK(twai_port_install() != ESP_OK);
    CHECK(twai_port_last_step() == TWAI_PORT_STEP_INSTALL);
    CHECK(!twai_port_is_installed() && !twai_fake_installed());
    twai_fake_fail_start_next(ESP_ERR_INVALID_STATE);
    CHECK(twai_port_install() != ESP_OK);
    CHECK(twai_port_last_step() == TWAI_PORT_STEP_START);
    CHECK(!twai_port_is_installed() && !twai_fake_installed());
    CHECK(host_error_checks == 0);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(twai_port_last_step() == TWAI_PORT_STEP_NONE || twai_port_is_installed());
}

static void case_install_fail_abort(void)
{
    twai_port_config_t cfg;
    g_case = "T2: abort on an install failure is the restart (ESP_ERROR_CHECK)";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    twai_fake_fail_install_next(ESP_ERR_NO_MEM);
    (void)twai_port_install();
    CHECK(host_error_checks == 1);
}

static void case_flush(void)
{
    twai_port_config_t cfg;
    can_port_status_t s;
    g_case = "T7: a flush counts what it discards, apart from the losses";
    fresh(&cfg);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_deliver_std(0x10, D8, 8);
    twai_fake_deliver_std(0x11, D8, 8);
    twai_fake_deliver_std(0x12, D8, 8);
    twai_port_flush_rx();
    s = st();
    CHECK(s.rx_flushed == 3 && s.rx_lost == 0 && s.rx_waiting == 0);
}

static void case_receive_wait(void)
{
    twai_port_config_t cfg;
    twai_message_t m;
    int64_t t0;
    g_case = "T9: a receive waits no longer than its caller gives";
    fresh(&cfg);
    twai_fake_set_wait_hook(wait_hook, NULL);
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    t0 = twai_fake_now();
    CHECK(twai_port_receive(&m, pdMS_TO_TICKS(200)) == ESP_ERR_TIMEOUT);
    CHECK(twai_fake_now() - t0 == 200000);
    twai_fake_deliver_std(0x10, D8, 8);
    CHECK(twai_port_receive(&m, pdMS_TO_TICKS(200)) == ESP_OK && m.identifier == 0x10);
    twai_fake_set_wait_hook(NULL, NULL);
}

static void case_install_hook(void)
{
    twai_port_config_t cfg;
    g_case = "T2: the install hook runs before every install and may replace that install's filter";
    fresh(&cfg);
    cfg.install_hook = install_hook;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    CHECK(g_install_hooks == 1);
    CHECK(twai_fake_installed_filter()->acceptance_code == (0x123u << 21));
    twai_fake_deliver_std(0x124, D8, 8);
    twai_fake_deliver_std(0x123, D8, 8);
    CHECK(twai_fake_rx_pending() == 1);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(g_install_hooks == 2);
    CHECK(twai_fake_installed_filter()->acceptance_mask == 0xFFFFFFFFu);
}

static const struct { const char *name; void (*fn)(void); } CASES[] = {
    {"bring_up", case_bring_up},
    {"teardown", case_teardown},
    {"teardown_recovering", case_teardown_recovering},
    {"queues_at_teardown", case_queues_at_teardown},
    {"send_counts", case_send_counts},
    {"counts_across_reinstall", case_counts_across_reinstall},
    {"mode_change", case_mode_change},
    {"register_file", case_register_file},
    {"bus_off_once", case_bus_off_once},
    {"install_fail", case_install_fail},
    {"install_fail_abort", case_install_fail_abort},
    {"flush", case_flush},
    {"receive_wait", case_receive_wait},
    {"install_hook", case_install_hook},
};
#define NCASES ((int)(sizeof(CASES) / sizeof(CASES[0])))

int main(int argc, char **argv)
{
    if (argc < 2)
    {
        for (int i = 0; i < NCASES; i++) { printf("%s\n", CASES[i].name); }
        return 0;
    }
    for (int i = 0; i < NCASES; i++)
    {
        if (strcmp(argv[1], CASES[i].name) == 0)
        {
            CASES[i].fn();
            printf("%s %s: %d checks, %d failed\n",
#ifdef PORT_ON_S3
                   "S3",
#else
                   "C3",
#endif
                   CASES[i].name, g_checks, g_fails);
            return g_fails ? 1 : 0;
        }
    }
    printf("no case %s\n", argv[1]);
    return 2;
}
