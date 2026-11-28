/*
 * The kept-driver switch (SPEC.md T14, T15, T17, T18) on the fake, through
 * the port's interface (../include/twai_port.h): the cases are written from
 * the spec and the fake's model, not from the port's code.
 *
 * Built against the tester's stand-in port (switch_standin.c), as the cases'
 * can-fail: as written it must pass every case, and each broken form
 * (SWITCH_BROKEN) must fail one (`make switch-standins`). Built against the
 * real twai_port.c once its code and the host-only define that lets the
 * fake's chip report the switch as supported have landed.
 *
 * The behaviours T19 measures are knobs of the fake. The environment picks
 * them for a run: SWITCH_RESET_TX (keep, resume, release) and
 * SWITCH_ABORT_REPORT (success, failed). The defaults are the fake's. Until
 * T16 is written and T19 has measured, no result here is final for the
 * frames held at a switch (charge-interposer reviewer, 2026-11-27).
 *
 * One process per case (main's argument): the port's state is file statics.
 */
#include "twai_port.h"
#include "twai_fake.h"
#include "hal/twai_ll.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fails, g_checks;
static const char *g_case;

#define CHECK(cond) do { g_checks++; if (!(cond)) { g_fails++; \
    printf("FAIL %s: %s (line %d)\n", g_case, #cond, __LINE__); } } while (0)

static const uint8_t D8[8] = {1, 2, 3, 4, 5, 6, 7, 8};

/* What the switch_discard hook was handed, in order. */
#define HELDMAX 70
static struct { twai_message_t m; uint32_t tag; } g_held[HELDMAX];
static int g_n_held;

static void on_discard(void *ctx, const twai_message_t *f, uint32_t tag)
{
    (void)ctx;
    if (g_n_held < HELDMAX)
    {
        g_held[g_n_held].m = *f;
        g_held[g_n_held].tag = tag;
    }
    g_n_held++;
}

static twai_message_t msg(uint32_t id)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = id;
    m.data_length_code = 8;
    memcpy(m.data, D8, 8);
    m.data[7] = (uint8_t)id;
    return m;
}

static can_port_status_t st(void)
{
    can_port_status_t s;
    memset(&s, 0, sizeof(s));
    CHECK(twai_port_status(&s) == ESP_OK);
    return s;
}

static twai_port_switch_report_t rep(void)
{
    twai_port_switch_report_t r;
    memset(&r, 0, sizeof(r));
    CHECK(twai_port_last_switch(&r) == ESP_OK);
    return r;
}

static void knobs_from_env(void)
{
    const char *r = getenv("SWITCH_RESET_TX");
    const char *a = getenv("SWITCH_ABORT_REPORT");
    if (r && strcmp(r, "resume") == 0) { twai_fake_set_reset_tx(TWAI_FAKE_RESET_TX_RESUME); }
    if (r && strcmp(r, "release") == 0) { twai_fake_set_reset_tx(TWAI_FAKE_RESET_TX_RELEASE); }
    if (a && strcmp(a, "failed") == 0) { twai_fake_set_abort_report(TWAI_FAKE_ABORT_REPORTS_FAILED); }
}

/* A fresh fake on the S3 (the chip T19 measures) and a port with the switch,
 * brought up. */
static void up(twai_port_config_t *cfg, can_port_mode_t mode)
{
    twai_fake_reset();
    twai_fake_set_chip(TWAI_FAKE_CHIP_S3);
    knobs_from_env();
    twai_port_config_defaults(cfg);
    cfg->kept_driver_switch = true;
    cfg->switch_discard = on_discard;
    cfg->mode = mode;
    g_n_held = 0;
    CHECK(twai_port_setup(cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
}

/* A healthy run of the register calls: none outside reset mode, no exit
 * from a reset mode the driver keeps, the driver never refusing for its
 * state. */
static void clean_registers(void)
{
    CHECK(twai_fake_ll_writes_outside_reset() == 0);
    CHECK(twai_fake_ll_exits_misused() == 0);
    CHECK(twai_fake_wrong_state_calls() == 0);
}

/* ------------------------------------------------------------- cases -- */

static void case_t14_keeps_driver(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100), r;
    uint32_t calls;
    can_port_status_t s;
    g_case = "T14: to listen-only and back with the driver kept: one install, no queue discarded";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    twai_fake_deliver_std(0x30, D8, 8);
    twai_fake_deliver_std(0x31, D8, 8);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(twai_port_mode() == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(twai_fake_install_count() == 1 && twai_fake_installed());
    CHECK(twai_fake_mode() == TWAI_MODE_NORMAL);            /* the driver's, fixed at install */
    CHECK(twai_fake_hw_mode() == TWAI_MODE_LISTEN_ONLY);
    CHECK(twai_fake_state() == TWAI_STATE_RUNNING && !twai_fake_in_reset_mode());
    CHECK(twai_fake_rx_pending() == 2 && twai_fake_rx_discarded() == 0);

    g_case = "T14: in listen-only every send refused by the port, as tx_refused; frames still received";
    calls = twai_fake_transmit_calls();
    s = st();
    CHECK(twai_port_send(&m, 0) != ESP_OK);
    CHECK(twai_port_send_tagged(&m, 0, 7) != ESP_OK);
    CHECK(twai_fake_transmit_calls() == calls);
    CHECK(st().tx_refused == s.tx_refused + 2 && st().tx_frames == s.tx_frames);
    twai_fake_deliver_std(0x32, D8, 8);
    CHECK(twai_fake_rx_pending() == 3);
    CHECK(twai_port_receive(&r, 0) == ESP_OK && r.identifier == 0x30);

    g_case = "T14: back to normal with the driver kept; sends reach the wire";
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NORMAL) == ESP_OK);
    CHECK(twai_port_mode() == CAN_PORT_MODE_NORMAL);
    CHECK(twai_fake_install_count() == 1 && twai_fake_hw_mode() == TWAI_MODE_NORMAL);
    CHECK(twai_fake_rx_pending() == 2 && twai_fake_rx_discarded() == 0);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_fake_wire_count() == 1);
    clean_registers();
}

static void case_t14_setup_listen_only(void)
{
    twai_port_config_t cfg;
    g_case = "T14: a port set up in listen-only installs in normal mode and reaches listen-only by the registers";
    up(&cfg, CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(twai_fake_install_count() == 1);
    CHECK(twai_fake_install_log_mode(0) == TWAI_MODE_NORMAL);
    CHECK(twai_fake_hw_mode() == TWAI_MODE_LISTEN_ONLY);
    CHECK(twai_port_mode() == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(twai_fake_rec() == 128);
    clean_registers();
}

static void case_t14_no_ack_is_t4(void)
{
    twai_port_config_t cfg;
    g_case = "T14: a change to no-ACK self-test is still T4's teardown and install";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NO_ACK_SELF_TEST) == ESP_OK);
    CHECK(twai_fake_install_count() == 2);
    CHECK(twai_fake_mode() == TWAI_MODE_NO_ACK);
    CHECK(twai_port_mode() == CAN_PORT_MODE_NO_ACK_SELF_TEST);
}

static void case_t14_not_running(void)
{
    twai_port_config_t cfg;
    twai_port_switch_report_t r;
    g_case = "T14: a switch while bus-off: ESP_ERR_INVALID_STATE, the mode kept, nothing written";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    twai_fake_bus_off();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_ERR_INVALID_STATE);
    CHECK(twai_port_mode() == CAN_PORT_MODE_NORMAL);
    CHECK(twai_fake_install_count() == 1 && twai_fake_rec_write_count() == 0);
    r = rep();
    CHECK(r.result == ESP_ERR_INVALID_STATE && r.to_mode == CAN_PORT_MODE_LISTEN_ONLY);
}

static void case_t14_held_bound(void)
{
    twai_port_config_t cfg;
    g_case = "T14/T17: setup refuses tx_queue_len + 1 above TWAI_PORT_SWITCH_HELD_MAX";
    twai_fake_reset();
    twai_fake_set_chip(TWAI_FAKE_CHIP_S3);
    twai_port_config_defaults(&cfg);
    cfg.kept_driver_switch = true;
    cfg.tx_queue_len = TWAI_PORT_SWITCH_HELD_MAX;
    CHECK(twai_port_setup(&cfg) == ESP_ERR_INVALID_ARG);
    cfg.tx_queue_len = TWAI_PORT_SWITCH_HELD_MAX - 1;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
}

/* The check test_port_on_fake.c's t14_refused makes on the real port, here
 * so that the stand-in, set up as a chip not measured for the switch
 * (SWITCH_UNMEASURED=1), shows it can fail. Without that it is skipped. */
static void case_t14_unmeasured_refused(void)
{
    twai_port_config_t cfg;
    g_case = "T14: setup with the kept-driver switch refused, ESP_ERR_NOT_SUPPORTED, on a chip not measured for it";
    if (getenv("SWITCH_UNMEASURED") == NULL) { return; }
    twai_fake_reset();
    twai_port_config_defaults(&cfg);
    cfg.kept_driver_switch = true;
    CHECK(twai_port_setup(&cfg) == ESP_ERR_NOT_SUPPORTED);
}

static void case_t15_rec(void)
{
    twai_port_config_t cfg;
    twai_port_switch_report_t r;
    can_port_status_t s;
    g_case = "T15: REC 128 written in reset mode into listen-only, read back and reported";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    twai_fake_set_error_counters(5, 3);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(twai_fake_rec_write_count() == 1 && twai_fake_rec_write(0) == 128);
    CHECK(twai_fake_rec() == 128);
    CHECK(twai_fake_ll_rec_reads() >= 1);
    r = rep();
    CHECK(r.seq >= 1 && r.to_mode == CAN_PORT_MODE_LISTEN_ONLY && r.result == ESP_OK);
    CHECK(r.rec_written == 128 && r.rec_read_back == 128 && r.tec_read_back == 5);

    g_case = "T15: the status's error counters from the registers in listen-only";
    s = st();
    CHECK(s.tec == 5 && s.rec == 128);

    g_case = "T15: REC 0 written on the way out, read back and reported";
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NORMAL) == ESP_OK);
    CHECK(twai_fake_rec_write_count() == 2 && twai_fake_rec_write(1) == 0);
    r = rep();
    CHECK(r.to_mode == CAN_PORT_MODE_NORMAL && r.rec_written == 0 && r.rec_read_back == 0);
    s = st();
    CHECK(s.rec == 0);
    clean_registers();
}

static void case_t15_read_back_is_read(void)
{
    twai_port_config_t cfg;
    int reads;
    g_case = "T15: the read-back is a register read after each switch";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    reads = twai_fake_ll_rec_reads();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(twai_fake_ll_rec_reads() > reads);
    reads = twai_fake_ll_rec_reads();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NORMAL) == ESP_OK);
    CHECK(twai_fake_ll_rec_reads() > reads);
}

static void case_t15_no_active_flag(void)
{
    twai_port_config_t cfg;
    g_case = "T15: a listen-only controller is error-passive: a bus error sends no active error flag";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    twai_fake_bus_error(1);
    CHECK(twai_fake_active_error_flags() == 1);     /* error-active in normal mode */
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    twai_fake_bus_error(3);
    CHECK(twai_fake_active_error_flags() == 1);
}

static void case_t17_held(void)
{
    twai_port_config_t cfg;
    twai_port_switch_report_t r;
    twai_status_info_t d;
    twai_message_t m = msg(0x200);
    can_port_status_t s;
    g_case = "T17: with no ACK, the head retrying and four queued: each removed frame handed to the hook once, "
             "head first then the queue in order, with its tag";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    twai_fake_set_acked(false);
    for (uint32_t i = 0; i < 5; i++)
    {
        twai_message_t f = msg(0x100 + i);
        CHECK(twai_port_send_tagged(&f, 0, 11 + i) == ESP_OK);
    }
    twai_fake_advance(500);
    (void)twai_port_poll_alerts();
    s = st();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(g_n_held == 5);
    for (int i = 0; i < 5 && i < g_n_held; i++)
    {
        CHECK(g_held[i].m.identifier == 0x100u + (uint32_t)i && g_held[i].tag == 11u + (uint32_t)i);
        CHECK(memcmp(g_held[i].m.data, msg(0x100 + (uint32_t)i).data, 8) == 0);
    }

    g_case = "T17: counted once, as tx_discarded_switch, never as tx_failed; nothing left in the driver";
    r = rep();
    CHECK(r.held == 5);
    CHECK(st().tx_discarded_switch == s.tx_discarded_switch + 5);
    CHECK(st().tx_failed == s.tx_failed);
    CHECK(st().tx_discarded_teardown == s.tx_discarded_teardown);
    twai_fake_driver_status(&d);
    CHECK(d.msgs_to_tx == 0 && twai_fake_tx_pending() == 0);
    CHECK(!r.driver_tx_stuck);
    CHECK(twai_fake_wire_count() == 0);

    g_case = "T17: after the switch back, a send reaches the wire";
    twai_fake_set_acked(true);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_NORMAL) == ESP_OK);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    CHECK(twai_fake_wire_count() == 1 && twai_fake_wire(0)->id == 0x200);
    CHECK(st().tx_failed == s.tx_failed);
    clean_registers();
}

static void case_t17_none_held(void)
{
    twai_port_config_t cfg;
    twai_message_t m = msg(0x100);
    twai_port_switch_report_t r;
    g_case = "T17: with every frame sent before the switch, nothing removed and the hook not called";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    CHECK(twai_port_send(&m, 0) == ESP_OK);
    twai_fake_advance(1000);
    (void)twai_port_poll_alerts();
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    r = rep();
    CHECK(g_n_held == 0 && r.held == 0 && !r.head_aborted);
    CHECK(st().tx_discarded_switch == 0);
}

static void case_t17_no_hook(void)
{
    twai_port_config_t cfg;
    g_case = "T17: with no hook the held frames are still removed and counted";
    twai_fake_reset();
    twai_fake_set_chip(TWAI_FAKE_CHIP_S3);
    knobs_from_env();
    twai_port_config_defaults(&cfg);
    cfg.kept_driver_switch = true;
    CHECK(twai_port_setup(&cfg) == ESP_OK);
    CHECK(twai_port_install() == ESP_OK);
    twai_fake_set_acked(false);
    for (uint32_t i = 0; i < 3; i++)
    {
        twai_message_t f = msg(0x100 + i);
        CHECK(twai_port_send(&f, 0) == ESP_OK);
    }
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    CHECK(st().tx_discarded_switch == 3 && twai_fake_tx_pending() == 0);
}

static void case_t18_bus_errors(void)
{
    twai_port_config_t cfg;
    twai_status_info_t d;
    uint32_t at_switch;
    g_case = "T18: every bus error up to the switch counted, the last ones with no poll between them and the switch";
    up(&cfg, CAN_PORT_MODE_NORMAL);
    twai_fake_set_acked(false);
    twai_fake_set_ack_errors(true);
    {
        twai_message_t f = msg(0x100);
        CHECK(twai_port_send(&f, 0) == ESP_OK);
    }
    twai_fake_advance(300);
    (void)twai_port_poll_alerts();                  /* one ACK error seen */
    twai_fake_advance(700);                         /* three more, not polled */
    twai_fake_bus_error(2);
    CHECK(twai_port_set_mode(CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK);
    twai_fake_driver_status(&d);
    at_switch = d.bus_error_count;
    CHECK(at_switch >= 6);
    CHECK(st().bus_errors == at_switch);
    twai_fake_advance(10000);                       /* listen-only: nothing sent, no more ACK errors */
    twai_fake_driver_status(&d);
    CHECK(d.bus_error_count == at_switch);
}

static const struct { const char *name; void (*fn)(void); } CASES[] = {
    {"t14_keeps_driver", case_t14_keeps_driver},
    {"t14_setup_listen_only", case_t14_setup_listen_only},
    {"t14_no_ack_is_t4", case_t14_no_ack_is_t4},
    {"t14_not_running", case_t14_not_running},
    {"t14_held_bound", case_t14_held_bound},
    {"t14_unmeasured_refused", case_t14_unmeasured_refused},
    {"t15_rec", case_t15_rec},
    {"t15_read_back_is_read", case_t15_read_back_is_read},
    {"t15_no_active_flag", case_t15_no_active_flag},
    {"t17_held", case_t17_held},
    {"t17_none_held", case_t17_none_held},
    {"t17_no_hook", case_t17_no_hook},
    {"t18_bus_errors", case_t18_bus_errors},
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
            printf("switch %s: %d checks, %d failed\n", CASES[i].name, g_checks, g_fails);
            return g_fails ? 1 : 0;
        }
    }
    printf("no case %s\n", argv[1]);
    return 2;
}
