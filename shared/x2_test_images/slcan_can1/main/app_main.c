/*
 * app_main.c -- slcan_can1: a plain SLCAN transmitter on the X2's CAN1, the
 * ESP32-S3's TWAI (x2_test_images SPEC T1). Test equipment, the
 * charge-interposer tester's (SPEC T4). Moved from the charge interposer's
 * slcan variant, charge-interposer/firmware/main/slcan_main.cpp, onto
 * ESP-IDF without the Arduino core (SPEC T6), with its behaviour kept; why
 * the image exists is in ../README.md.
 *
 * SILENT UNTIL TOLD TO OPEN (SPEC T8a). CAN1's port is set up and
 * registered at boot and never brought up until an O or an L
 * (slcan1_proto.h), so nothing is transmitted before an open, and a reset
 * returns the image to silent. Before app_main, through the ROM and the
 * bootloader, CAN1's TXD is an input, and the transceiver's TXD pull-up
 * holds the bus recessive (../../jammer/README.md).
 *
 * THE PORT is the shared vtrux/twai_port behind vtrux/can_port, on the
 * board's TWAI port 0, with the interposer's vehicle-port settings that
 * slcan_main.cpp ran with (charge-interposer vehicle_port.h): 500 kbit/s,
 * every identifier accepted, retries (not single-shot), automatic bus-off
 * recovery, remote frames dropped, an install failure reported (never a
 * restart); its own queue depths, receive 16 and transmit 64.
 */
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/usb_serial_jtag.h"

#include "board.h"
#include "can_port.h"
#include "twai_port.h"

#include "slcan1_app.h"
#include "slcan1_proto.h"

#define CAN1_PORT       0       /* the board's TWAI port, and can_port's index */

/*
 * A filler run offers frames as fast as the host can write them and lets
 * arbitration place them, so the transmit queue is what is under pressure:
 * 64 absorbs a USB burst without reporting a drop that is only a burst. The
 * receive queue only buffers frames between drains.
 */
#define CAN1_RX_QUEUE   16
#define CAN1_TX_QUEUE   64

/*
 * A big USB receive buffer, and it is not optional at filler rates: the host
 * offers ~2600 frames/s, ~57 kB/s of 22-byte commands in bursts. With
 * Arduino's default buffer, measured 2026-09-29 on the slcan variant, of
 * 12,930 frames written the board saw only 7,058, and 856 of those were cut
 * in half. The transmit side carries only replies.
 */
#define USB_RX_BUFFER   8192
#define USB_TX_BUFFER   2048

/* Never wait on the USB host for more than a tick: the image must reach and
 * keep its silent state whether or not anyone is listening (the interposer's
 * spec 8.3, which slcan_main.cpp followed). */
#define USB_TX_WAIT     1
#define USB_RX_WAIT     1

static slcan1_t s_slcan;
static int s_setup;             /* the port set up and registered */

static int can1_setup(void)
{
    board_twai_pins_t pins;
    if (board_twai_pins(CAN1_PORT, &pins) != ESP_OK) return 0;
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.board_port = CAN1_PORT;
    c.mode = CAN_PORT_MODE_NORMAL;
    c.filter.acceptance_code = 0;
    c.filter.acceptance_mask = 0xFFFFFFFFu;
    c.filter.single_filter = true;
    c.single_shot = false;
    c.rx_queue_len = CAN1_RX_QUEUE;
    c.tx_queue_len = CAN1_TX_QUEUE;
    c.recovery = TWAI_PORT_RECOVERY_AUTO;
    c.drop_rtr = true;
    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    c.pre_teardown = NULL;
    c.install_hook = NULL;
    c.hook_ctx = NULL;
    if (twai_port_setup(&c) != ESP_OK) return 0;
    return can_port_register(CAN1_PORT, &twai_port_ops, NULL) == ESP_OK;
}

/* ---- the protocol's operations ---- */

static int op_open(void *ctx, int listen_only)
{
    /* The port is down here, so the mode is only recorded for the
     * bring-up that follows (twai_port T4). */
    return s_setup &&
           twai_port_set_mode(listen_only ? CAN_PORT_MODE_LISTEN_ONLY
                                          : CAN_PORT_MODE_NORMAL) == ESP_OK &&
           can_port_up(CAN1_PORT) == ESP_OK;
}

static void op_close(void *ctx)
{
    (void)twai_port_teardown();
}

static int op_send(void *ctx, uint32_t id, const uint8_t *data, uint8_t len)
{
    twai_message_t m;
    memset(&m, 0, sizeof m);
    m.identifier = id;
    m.extd = 0;
    m.data_length_code = len > 8 ? 8 : len;
    memcpy(m.data, data, m.data_length_code);
    /* A wait of 0: enqueue or fail, never block. */
    return can_port_send(CAN1_PORT, &m, 0) == ESP_OK;
}

static void op_counts(void *ctx, slcan1_port_counts_t *out)
{
    can_port_status_t st;
    memset(&st, 0, sizeof st);
    if (can_port_status(CAN1_PORT, &st) != ESP_OK) memset(&st, 0, sizeof st);
    out->tx = st.tx_frames;
    out->tx_dropped = st.tx_dropped + st.tx_refused;
    out->rx = st.rx_frames;
    out->rx_dropped = st.rx_lost;
    out->bus_errors = st.bus_errors;
    out->bus_off = st.bus_off ? 1 : 0;
}

static void op_write(void *ctx, const char *s, size_t n)
{
    usb_serial_jtag_write_bytes(s, n, USB_TX_WAIT);
}

static const slcan1_ops_t s_ops = {
    .open = op_open,
    .close = op_close,
    .send = op_send,
    .counts = op_counts,
    .write = op_write,
    .ctx = NULL,
};

void slcan1_app_boot(void)
{
    usb_serial_jtag_driver_config_t usb = {
        .tx_buffer_size = USB_TX_BUFFER,
        .rx_buffer_size = USB_RX_BUFFER,
    };
    (void)usb_serial_jtag_driver_install(&usb);
    s_setup = can1_setup();
    slcan1_init(&s_slcan, &s_ops);
}

void slcan1_app_step(void)
{
    uint8_t buf[256];
    int n = usb_serial_jtag_read_bytes(buf, sizeof buf, USB_RX_WAIT);
    if (n > 0) slcan1_input(&s_slcan, buf, (size_t)n);
    if (slcan1_is_open(&s_slcan)) {
        can_port_service(CAN1_PORT);
        /* Drained and discarded, never forwarded (slcan1_proto.h); draining
         * keeps the controller from overrunning on a shared bus. */
        twai_message_t m;
        while (can_port_receive(CAN1_PORT, &m, 0) == ESP_OK) {
        }
    }
}

static void slcan1_task(void *arg)
{
    for (;;) slcan1_app_step();
}

void app_main(void)
{
    slcan1_app_boot();
    /* On core 1, as the Arduino loop ran; the USB read's one-tick wait lets
     * the idle task run, which the task watchdog checks. */
    xTaskCreatePinnedToCore(slcan1_task, "slcan1", 4096, NULL, 5, NULL, 1);
}
