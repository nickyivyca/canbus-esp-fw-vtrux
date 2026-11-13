/*
 * The host check of x2_test_images SPEC T8a on the whole image:
 * ../main/app_main.c with ../main/slcan1_proto.c, built on fakes of ESP-IDF,
 * the board and the shared ports (fake/fake_idf.h).
 *
 * T8a: "nothing is transmitted before an open command, a reset returns it to
 * silent, and a listen-only open never ACKs". On the image that means: at
 * boot CAN1's port is set up and registered but never brought up; it comes
 * up only on O (normal) or L (listen-only, the mode recorded before the
 * bring-up); C takes it down; a frame is offered to the port only while it
 * is up in normal mode; and a boot after a reset is silent again, whatever
 * the image was doing before. Also the port's settings, which the image
 * keeps from slcan_main.cpp (app_main.c's header).
 */
#include <string.h>

#include "check.h"
#include "fake_idf.h"
#include "slcan1_app.h"

#define FRAME "t7E08AAAAAAAAAAAAAAAA\r"

static void steps(int n)
{
    for (int i = 0; i < n; i++) slcan1_app_step();
}

/* Send, then run enough passes for the image to take it all. */
static void host(const char *s)
{
    fake_host_sends(s);
    steps(1 + (int)strlen(s) / 64);
}

static void boot(void)
{
    fake_reset();
    slcan1_app_boot();
}

static void test_boot_silent(void)
{
    boot();
    CHECK(fake.setups == 1, "port set up %d times at boot, not once", fake.setups);
    CHECK(fake.registers == 1 && fake.registered_port == 0 &&
          fake.registered_ops == &twai_port_ops,
          "twai_port not registered with can_port for index 0");
    CHECK(fake.ups == 0, "the port was brought up at boot (%d)", fake.ups);
    CHECK(fake.usb_installs == 1, "USB driver installed %d times", fake.usb_installs);
    CHECK(fake.out_len == 0, "the image wrote at boot: '%s'", fake_host_reads());
    steps(200);
    CHECK(fake.ups == 0 && fake.sent == 0 && fake.violations == 0,
          "idle passes: ups %d sent %d violations %d", fake.ups, fake.sent, fake.violations);
    CHECK(fake.services == 0, "the port serviced while closed (%d)", fake.services);
    CHECK(fake.out_len == 0, "the image wrote while idle: '%s'", fake_host_reads());
}

static void test_port_settings(void)
{
    boot();
    const twai_port_config_t *c = &fake.config;
    CHECK(c->board_port == 0, "board port %d, not 0 (CAN1)", c->board_port);
    CHECK(c->timing == FAKE_TIMING_500K, "timing not the defaults' 500 kbit/s");
    CHECK(c->mode == CAN_PORT_MODE_NORMAL, "setup mode %d", (int)c->mode);
    CHECK(c->filter.acceptance_code == 0 && c->filter.acceptance_mask == 0xFFFFFFFFu &&
          c->filter.single_filter, "filter not accept-all");
    CHECK(!c->single_shot, "single-shot: retries off");
    CHECK(c->rx_queue_len == 16 && c->tx_queue_len == 64, "queues rx %u tx %u, not 16 and 64",
          (unsigned)c->rx_queue_len, (unsigned)c->tx_queue_len);
    CHECK(c->recovery == TWAI_PORT_RECOVERY_AUTO, "bus-off recovery not automatic");
    CHECK(c->drop_rtr, "remote frames not dropped");
    CHECK(c->on_install_fail == TWAI_PORT_INSTALL_FAIL_REPORT,
          "an install failure is not reported (would restart)");
    CHECK(c->pre_teardown == NULL && c->install_hook == NULL, "hooks set");
    CHECK(fake.usb_config.rx_buffer_size == 8192 && fake.usb_config.tx_buffer_size == 2048,
          "USB buffers rx %u tx %u, not 8192 and 2048",
          (unsigned)fake.usb_config.rx_buffer_size, (unsigned)fake.usb_config.tx_buffer_size);
}

static void test_frame_before_open(void)
{
    boot();
    host(FRAME);
    host("S6\r");
    host(FRAME);
    CHECK(fake.sent == 0 && fake.violations == 0, "frame before open reached the port: "
          "sent %d violations %d", fake.sent, fake.violations);
    CHECK(fake.ups == 0, "the port came up without O or L");
    CHECK(strcmp(fake_host_reads(), "\a\r\a") == 0, "replies '%s'", fake_host_reads());
}

static void test_listen_only(void)
{
    boot();
    host("S6\rL\r");
    CHECK(fake.ups == 1 && fake.up_mode[0] == CAN_PORT_MODE_LISTEN_ONLY,
          "L: ups %d, mode %d, not one listen-only bring-up", fake.ups, (int)fake.up_mode[0]);
    host(FRAME FRAME);
    CHECK(fake.sent == 0 && fake.violations == 0,
          "listen-only offered frames: sent %d violations %d", fake.sent, fake.violations);
    host("O\r");
    CHECK(fake.ups == 1 && fake.mode == CAN_PORT_MODE_LISTEN_ONLY,
          "O while listen-only changed the port: ups %d mode %d", fake.ups, (int)fake.mode);
    CHECK(fake.services > 0, "the port not serviced while open");
}

static void test_close_and_reopen(void)
{
    boot();
    host("S6\rO\r");
    CHECK(fake.ups == 1 && fake.up_mode[0] == CAN_PORT_MODE_NORMAL, "O: ups %d mode %d",
          fake.ups, (int)fake.up_mode[0]);
    host(FRAME);
    CHECK(fake.sent == 1 && fake.sent_frames[0].identifier == 0x7E0 &&
          !fake.sent_frames[0].extd && fake.sent_frames[0].data_length_code == 8 &&
          fake.sent_frames[0].data[7] == 0xAA, "open normal: frame not sent as asked");
    host("C\r");
    CHECK(fake.teardowns == 1 && !fake.up, "C: teardowns %d up %d", fake.teardowns, fake.up);
    int services = fake.services;
    host(FRAME);
    steps(50);
    CHECK(fake.sent == 1 && fake.violations == 0, "after C: sent %d violations %d",
          fake.sent, fake.violations);
    CHECK(fake.services == services, "the port serviced after C");
    host("L\r");
    CHECK(fake.ups == 2 && fake.up_mode[1] == CAN_PORT_MODE_LISTEN_ONLY,
          "L after a normal session: mode %d", (int)fake.up_mode[1]);
    host(FRAME);
    CHECK(fake.sent == 1 && fake.violations == 0, "listen-only after normal: sent %d viol %d",
          fake.sent, fake.violations);
    host("C\rO\r");
    CHECK(fake.ups == 3 && fake.up_mode[2] == CAN_PORT_MODE_NORMAL,
          "O after a listen-only session: mode %d", (int)fake.up_mode[2]);
}

/* A reset returns it to silent: the chip resets (the fake too), the image
 * boots again, closed, whatever it was doing before. */
static void test_reset_silent(void)
{
    boot();
    host("S6\rO\r");
    host(FRAME);
    CHECK(fake.sent == 1, "setup for the reset test: not open");
    boot();
    steps(100);
    CHECK(fake.ups == 0, "the port came up after a reset");
    host(FRAME);
    CHECK(fake.sent == 0 && fake.violations == 0,
          "a frame after a reset reached the port: sent %d violations %d",
          fake.sent, fake.violations);
    CHECK(strcmp(fake_host_reads(), "\a") == 0, "frame after reset not refused: '%s'",
          fake_host_reads());
}

static void test_no_board_port(void)
{
    fake_reset();
    fake.board_has_twai = false;
    slcan1_app_boot();
    host("O\r");
    CHECK(fake.ups == 0, "the port came up on a board without its TWAI port");
    CHECK(strcmp(fake_host_reads(), "\a") == 0, "O without the port not refused: '%s'",
          fake_host_reads());
}

static void test_received_drained(void)
{
    boot();
    host("O\r");
    fake_host_clear();
    fake.rx_pending = 40;
    steps(2);
    CHECK(fake.rx_pending == 0 && fake.rx_taken == 40, "received frames not drained: %d left",
          fake.rx_pending);
    CHECK(fake.out_len == 0, "received frames forwarded to the host: '%s'", fake_host_reads());
}

static void test_counters_from_port(void)
{
    boot();
    host("O\r");
    fake_host_clear();
    fake.status.tx_frames = 5;
    fake.status.tx_dropped = 2;
    fake.status.tx_refused = 1;
    fake.status.rx_frames = 7;
    fake.status.rx_lost = 3;
    fake.status.bus_errors = 4;
    fake.status.bus_off = true;
    host("?\r");
    const char *want = "slcan1 open=1 listen_only=0 accepted=0 queue_full=0 rejected=0 "
                       "tx=5 tx_dropped=3 rx=7 rx_dropped=3 bus_errors=4 bus_off=1\r\n";
    CHECK(strcmp(fake_host_reads(), want) == 0, "? line:\n  got  %s  want %s",
          fake_host_reads(), want);
}

int main(void)
{
    test_boot_silent();
    test_port_settings();
    test_frame_before_open();
    test_listen_only();
    test_close_and_reopen();
    test_reset_silent();
    test_no_board_port();
    test_received_drained();
    test_counters_from_port();
    return check_report("test_app");
}
