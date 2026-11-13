/*
 * app_main.c -- slcan_spi: SLCAN over USB to the X2's CAN2 (the MCP2515).
 * NOT FOR VEHICLE (x2_test_images SPEC T14).
 *
 * Joins the host side (host_usb.c, slcan_proto.c) to the CAN side
 * (can_side.c, mcp2515.c), which know nothing of each other (SPEC T15).
 * Everything bound for the host goes through one ring and one output task,
 * so lines from the CAN side's task and the protocol's replies never
 * interleave mid-line, and a full link loses whole lines, counted.
 *
 * Jamming on CAN1 (SPEC T13): the jammer's pulse engine, jam_pulse from
 * ../jammer/components/, unchanged. CAN1's TXD is driven recessive before
 * anything else, and CAN1's TWAI driver is not in the image, so the TWAI
 * controller is never started and no CAN frame is sent on CAN1. Jam
 * commands come as a reserved frame through the SLCAN (jam_frame.h), which
 * is never put on a bus.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_app_desc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "board.h"
#include "can_side.h"
#include "host_usb.h"
#include "jam_frame.h"
#include "jam_pulse.h"
#include "slcan_proto.h"

#define CAN1_PORT       0       /* the board's TWAI port (board.h) */
#define CAN2_PORT       1       /* the board's SPI CAN port (board.h) */
#define OUT_RING_SIZE   16384
#define NFV             "NOT FOR VEHICLE"

static RingbufHandle_t s_out;
static slcan_t s_slcan;
static char s_identity[160];
static uint8_t s_canstat_boot;
static gpio_num_t s_jam_tx;              /* CAN1's TXD */
static uint8_t s_flags;                 /* Lawicel F flags, atomic */
static uint32_t s_tx_dropped;           /* frames refused (protocol task) */
static uint32_t s_host_lost;            /* received frames the link lost */
static uint32_t s_reports_pending;      /* e3ooo lines owed (CAN task) */
static uint32_t s_jam_replies_lost;     /* jam reply lines the link lost */
static SemaphoreHandle_t s_jam_mx;      /* jam_reply's buffers */

static void flag(uint8_t bit)
{
    __atomic_fetch_or(&s_flags, bit, __ATOMIC_RELAXED);
}

static int out_push(const char *s, size_t n)
{
    return xRingbufferSend(s_out, s, n, 0) == pdTRUE;
}

static void out_task(void *arg)
{
    for (;;) {
        size_t n = 0;
        void *p = xRingbufferReceiveUpTo(s_out, &n, portMAX_DELAY, 512);
        if (p) {
            host_usb_write(p, n, portMAX_DELAY);
            vRingbufferReturnItem(s_out, p);
        }
    }
}

/* jam_pulse's reply callback, from the protocol task and the jam task: the
 * line as frames on JAM_REPLY_ID (jam_frame.h), all of them in one push, so
 * one line's frames are never split or interleaved with another's. */
static void jam_reply(const char *line, void *ctx)
{
    static can_frame_t fr[JAM_REPLY_FRAMES_MAX];
    static char buf[JAM_REPLY_FRAMES_MAX * SLCAN_LINE_MAX];
    xSemaphoreTake(s_jam_mx, portMAX_DELAY);
    size_t n = jam_frame_reply(line, fr, JAM_REPLY_FRAMES_MAX), len = 0;
    for (size_t i = 0; i < n; i++) len += slcan_format_frame(&fr[i], buf + len);
    if (!out_push(buf, len)) s_jam_replies_lost++;
    xSemaphoreGive(s_jam_mx);
}

/* A console line: "\n# NOT FOR VEHICLE | <text>\r" (slcan_proto.h). */
static void console(const char *fmt, ...)
{
    char text[200], line[240];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    int n = snprintf(line, sizeof line, "\n# " NFV " | %s\r", text);
    if (n > 0) out_push(line, (size_t)n < sizeof line ? (size_t)n : sizeof line - 1);
}

/* The boot banner: one console line short enough for the kernel's slcan
 * (slcan_proto.h, SLCAN_UNPROMPTED_MAX), since it waits in the USB buffers
 * for whoever opens the port next, the kernel included. */
#define BOOT_LINE "\n# " NFV " slcan_spi\r"
_Static_assert(sizeof BOOT_LINE - 2 <= SLCAN_UNPROMPTED_MAX,
               "the boot line would count as an rx_over_error in the kernel");

/* The full banner, on B only: its lines are longer than the kernel's slcan
 * takes, and B comes only from a terminal, never from the kernel. */
static void banner(void *ctx)
{
    can_side_stats_t st;
    can_side_get_stats(&st);
    console("%s", s_identity);
    console("SLCAN on CAN2 (MCP2515 on SPI), 500 kbit/s only (S6); silent "
            "until O (normal) or L (listen-only); C closes");
    console("MCP2515 CANSTAT 0x%02X at boot (0x80: configuration mode, no "
            "transmit, no ACK)", s_canstat_boot);
    console("CAN1: jam pulses on GPIO%d, TWAI never started, stops=%s",
            (int)s_jam_tx, jam_stops_text());
    char lim[120];
    jam_limits_text(lim, sizeof lim);
    console("jam %s; commands: frame T%08lX8 (jam_frame.h)", lim,
            (unsigned long)JAM_CMD_ID);
    console("counters: tx_queued=%lu tx_ok=%lu tx_full=%lu tx_dropped=%lu "
            "tx_flushed=%lu rx=%lu rx_overrun=%lu host_lost=%lu "
            "jam_replies_lost=%lu",
            (unsigned long)st.tx_queued, (unsigned long)st.tx_ok,
            (unsigned long)st.tx_full, (unsigned long)s_tx_dropped,
            (unsigned long)st.tx_flushed, (unsigned long)st.rx,
            (unsigned long)st.rx_overrun, (unsigned long)s_host_lost,
            (unsigned long)s_jam_replies_lost);
}

/* ---- the CAN side's callbacks (its task) ---- */

static int pay_reports(void)
{
    char e[SLCAN_LINE_MAX];
    size_t n = slcan_format_rx_overrun(e);
    while (s_reports_pending) {
        if (!out_push(e, n)) return 0;
        s_reports_pending--;
    }
    return 1;
}

static void on_frame(const can_frame_t *f)
{
    char l[SLCAN_LINE_MAX];
    size_t n = slcan_format_frame(f, l);
    if (!pay_reports() || !out_push(l, n)) {
        s_host_lost++;
        s_reports_pending++;
        flag(SLCAN_F_RX_FIFO_FULL);
    }
}

static void on_state(char state, uint8_t rec, uint8_t tec)
{
    char l[SLCAN_LINE_MAX];
    size_t n = slcan_format_state(state, rec, tec, l);
    if (state == 'w') flag(SLCAN_F_ERR_WARNING);
    if (state == 'p') flag(SLCAN_F_ERR_PASSIVE);
    if (state == 'b') flag(SLCAN_F_BUS_ERROR);
    out_push(l, n);
}

static void on_rx_overrun(void)
{
    flag(SLCAN_F_DATA_OVERRUN);
    s_reports_pending++;
    pay_reports();
}

static const can_side_cb_t s_can_cb = {
    .on_frame = on_frame,
    .on_state = on_state,
    .on_rx_overrun = on_rx_overrun,
};

/* ---- the protocol's operations (the protocol task) ---- */

static int op_open(void *ctx, int listen_only)
{
    return can_side_open(listen_only != 0);
}

static void op_close(void *ctx)
{
    can_side_close();
}

static int op_local(void *ctx, const can_frame_t *f)
{
    char text[JAM_CMD_TEXT_MAX];
    switch (jam_frame_decode(f, text)) {
    case JAM_FRAME_NOT_JAM:
        return 0;
    case JAM_FRAME_CMD:
        if (jam_line(text) == JAM_LINE_NOT_MINE) jam_reply("ERR unknown_command", NULL);
        return 1;
    default:
        jam_reply("ERR bad_jam_frame", NULL);
        return 1;
    }
}

static int op_send(void *ctx, const can_frame_t *f)
{
    return can_side_send(f);
}

static uint8_t op_flags(void *ctx)
{
    return __atomic_exchange_n(&s_flags, 0, __ATOMIC_RELAXED);
}

static void op_dropped(void *ctx)
{
    s_tx_dropped++;
    flag(SLCAN_F_TX_FIFO_FULL);
}

static void op_write(void *ctx, const char *s, size_t n)
{
    out_push(s, n);
}

static slcan_ops_t s_ops = {
    .open = op_open,
    .close = op_close,
    .local = op_local,
    .send = op_send,
    .flags = op_flags,
    .dropped = op_dropped,
    .write = op_write,
    .banner = banner,
};

static void proto_task(void *arg)
{
    uint8_t buf[256];
    for (;;) {
        int n = host_usb_read(buf, sizeof buf, portMAX_DELAY);
        if (n > 0) slcan_input(&s_slcan, buf, (size_t)n);
    }
}

void app_main(void)
{
    board_twai_pins_t can1;
    ESP_ERROR_CHECK(board_twai_pins(CAN1_PORT, &can1));
    s_jam_tx = can1.tx;
    jam_drive_recessive(s_jam_tx);      /* first (SPEC T13: boots silent) */

    const esp_app_desc_t *d = esp_app_get_description();
    char elf[65];
    esp_app_get_elf_sha256(elf, sizeof elf);
    snprintf(s_identity, sizeof s_identity, "%s %s elf=%s",
             d->project_name, d->version, elf);
    s_ops.identity = s_identity;

    s_out = xRingbufferCreate(OUT_RING_SIZE, RINGBUF_TYPE_BYTEBUF);
    ESP_ERROR_CHECK(s_out ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(host_usb_init());
    xTaskCreatePinnedToCore(out_task, "host_out", 3072, NULL, 10, NULL, 0);

    ESP_ERROR_CHECK(can_side_init(CAN2_PORT, &s_can_cb, &s_canstat_boot));
    out_push(BOOT_LINE, sizeof BOOT_LINE - 1);

    s_jam_mx = xSemaphoreCreateMutex();
    ESP_ERROR_CHECK(s_jam_mx ? ESP_OK : ESP_ERR_NO_MEM);
    jam_config_t jc;
    memset(&jc, 0, sizeof jc);
    jc.tx_gpio = s_jam_tx;
    jc.reply = jam_reply;
    jc.core = 1;
    jc.priority = 10;
    ESP_ERROR_CHECK(jam_init(&jc));

    slcan_init(&s_slcan, &s_ops);
    xTaskCreatePinnedToCore(proto_task, "slcan", 4096, NULL, 12, NULL, 0);
}
