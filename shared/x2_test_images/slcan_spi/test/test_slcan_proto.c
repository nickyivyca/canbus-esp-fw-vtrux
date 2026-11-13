/*
 * test_slcan_proto.c -- host unit tests of slcan_spi's protocol layer,
 * ../main/slcan_proto.c, built with the machine's compiler (Makefile).
 * NOT FOR VEHICLE (x2_test_images SPEC T14).
 *
 * The protocol is driven through a fake of the rest of the image that
 * records what was asked of it. mutate.py breaks one behaviour of
 * slcan_proto.c at a time and checks these tests fail on each.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "slcan_proto.h"

static int failures;

#define CHECK(cond, ...) do { \
    if (!(cond)) { \
        failures++; \
        printf("FAIL %s:%d: ", __FILE__, __LINE__); \
        printf(__VA_ARGS__); \
        printf("\n"); \
    } \
} while (0)

/* ---- the fake ---- */

static char out[4096];
static size_t out_len;
static int opened, open_listen, closed, sent, dropped, banners;
static int send_result, open_result, locals;
static can_frame_t last_sent;
static uint8_t flags_val;

static int f_open(void *c, int l) { opened++; open_listen = l; return open_result; }
static void f_close(void *c) { closed++; }
static int f_send(void *c, const can_frame_t *f)
{
    if (send_result == 0) { sent++; last_sent = *f; }
    return send_result;
}
static uint8_t f_flags(void *c) { uint8_t v = flags_val; flags_val = 0; return v; }
static void f_dropped(void *c) { dropped++; }
static void f_write(void *c, const char *s, size_t n)
{
    if (out_len + n < sizeof out) { memcpy(out + out_len, s, n); out_len += n; out[out_len] = 0; }
}
static void f_banner(void *c) { banners++; }
/* The image's own frames: extended 0x1F4A4D43 here (the jam channel). */
static int f_local(void *c, const can_frame_t *f)
{
    if (f->ext && f->id == 0x1F4A4D43u) { locals++; return 1; }
    return 0;
}

static const slcan_ops_t ops = {
    .open = f_open, .close = f_close, .local = f_local, .send = f_send, .flags = f_flags,
    .dropped = f_dropped, .write = f_write, .banner = f_banner,
    .identity = "slcan_spi_NOT_FOR_VEHICLE v-test elf=0123", .ctx = NULL,
};

static slcan_t s;

static void reset(void)
{
    out_len = 0; out[0] = 0;
    opened = open_listen = closed = sent = dropped = banners = locals = 0;
    send_result = 0; open_result = 0; flags_val = 0;
    memset(&last_sent, 0, sizeof last_sent);
    slcan_init(&s, &ops);
}

static void feed(const char *txt)
{
    out_len = 0; out[0] = 0;
    slcan_input(&s, (const uint8_t *)txt, strlen(txt));
}

/* ---- tests ---- */

static void test_silent_until_open(void)
{
    reset();
    feed("t1230\r");
    CHECK(strcmp(out, "\a") == 0, "frame while closed: reply %s", out);
    CHECK(sent == 0, "frame while closed reached send()");
    CHECK(dropped == 0, "frame while closed counted as an open-mode drop");
    feed("S6\rO\r");
    CHECK(strcmp(out, "\r\r") == 0, "S6, O: reply %s", out);
    CHECK(opened == 1 && open_listen == 0, "O did not open normal");
    feed("t1230\r");
    CHECK(strcmp(out, "z\r") == 0 && sent == 1, "frame when open: %s sent=%d", out, sent);
    feed("C\r");
    CHECK(strcmp(out, "\r") == 0 && closed == 1, "C: %s closed=%d", out, closed);
    feed("t1230\r");
    CHECK(strcmp(out, "\a") == 0 && sent == 1, "frame after C: %s sent=%d", out, sent);
}

static void test_rate(void)
{
    reset();
    feed("O\r");
    CHECK(strcmp(out, "\a") == 0 && opened == 0, "O without S6 opened");
    feed("L\r");
    CHECK(strcmp(out, "\a") == 0 && opened == 0, "L without S6 opened");
    for (char d = '0'; d <= '8'; d++) {
        char cmd[8];
        if (d == '6') continue;
        reset();
        snprintf(cmd, sizeof cmd, "S%c\r", d);
        feed(cmd);
        CHECK(strcmp(out, "\a") == 0, "S%c accepted", d);
    }
    reset();
    feed("S6\rS4\rO\r");
    CHECK(strcmp(out, "\r\a\a") == 0 && opened == 0, "refused S4 kept the rate: %s", out);
    reset();
    feed("S6\rs011C\rO\r");
    CHECK(strcmp(out, "\r\a\a") == 0 && opened == 0, "refused s kept the rate: %s", out);
    reset();
    feed("S6\rO\rS6\r");
    CHECK(strcmp(out, "\r\r\a") == 0, "S while open accepted: %s", out);
    reset();
    feed("S6\rO\rO\r");
    CHECK(strcmp(out, "\r\r\a") == 0 && opened == 1, "O while open accepted: %s", out);
    reset();
    open_result = -1;
    feed("S6\rO\rt1230\r");
    CHECK(strcmp(out, "\r\a\a") == 0 && sent == 0, "failed open left it open: %s", out);
}

static void test_listen_only(void)
{
    reset();
    feed("S6\rL\r");
    CHECK(opened == 1 && open_listen == 1, "L did not open listen-only");
    feed("t1230\r");
    CHECK(sent == 0, "listen-only sent a frame");
    CHECK(dropped == 1, "listen-only frame not counted dropped");
    CHECK(strcmp(out, "\ae3OOO\r") == 0, "listen-only frame reply %s", out);
}

static void test_queue_full(void)
{
    reset();
    feed("S6\rO\r");
    send_result = -1;
    feed("T12ABCDEF2AA55\r");
    CHECK(strcmp(out, "\ae3OOO\r") == 0, "queue full reply %s", out);
    CHECK(dropped == 1, "queue full not counted");
}

static void test_frames(void)
{
    reset();
    feed("S6\rO\r");
    feed("t4563112233\r");
    CHECK(strcmp(out, "z\r") == 0, "t reply %s", out);
    CHECK(last_sent.id == 0x456 && !last_sent.ext && !last_sent.rtr && last_sent.dlc == 3 &&
          last_sent.data[0] == 0x11 && last_sent.data[2] == 0x33, "t parsed wrong");
    feed("T12ABCDEF2aa55\r");
    CHECK(strcmp(out, "Z\r") == 0, "T reply %s", out);
    CHECK(last_sent.id == 0x12ABCDEF && last_sent.ext && last_sent.dlc == 2 &&
          last_sent.data[0] == 0xAA && last_sent.data[1] == 0x55, "T parsed wrong");
    feed("r1238\r");
    CHECK(strcmp(out, "z\r") == 0 && last_sent.rtr && last_sent.dlc == 8, "r: %s", out);
    feed("R1FFFFFFF0\r");
    CHECK(strcmp(out, "Z\r") == 0 && last_sent.rtr && last_sent.ext &&
          last_sent.id == 0x1FFFFFFF, "R: %s", out);
    int before = sent;
    const char *bad[] = {
        "t8000\r",              /* 11-bit id above 0x7FF */
        "T200000000\r",         /* 29-bit id above 0x1FFFFFFF */
        "t1239\r",              /* dlc 9 */
        "r1239\r",              /* remote, dlc 9 */
        "t12311\r",             /* short data */
        "t1232111\r",           /* odd data */
        "t1231112\r",           /* long data */
        "t12G0\r",              /* not hex */
        "t1231G1\r",            /* data not hex */
        "r12300\r",             /* remote frame with data */
        "t12\r",                /* short id */
        NULL,
    };
    for (int i = 0; bad[i]; i++) {
        feed(bad[i]);
        CHECK(strcmp(out, "\a") == 0, "bad frame %.*s accepted: %s",
              (int)strlen(bad[i]) - 1, bad[i], out);
    }
    CHECK(sent == before, "a bad frame reached send()");
    CHECK(dropped == 0, "a bad frame counted as dropped");
}

static void test_format(void)
{
    char l[SLCAN_LINE_MAX];
    can_frame_t f = {.id = 0x456, .dlc = 3, .data = {0x11, 0x22, 0x33}};
    slcan_format_frame(&f, l);
    CHECK(strcmp(l, "t4563112233\r") == 0, "t format %s", l);
    can_frame_t g = {.id = 0x12ABCDEF, .ext = 1, .dlc = 2, .data = {0xAA, 0x55}};
    slcan_format_frame(&g, l);
    CHECK(strcmp(l, "T12ABCDEF2AA55\r") == 0, "T format %s", l);
    can_frame_t r = {.id = 0x7FF, .rtr = 1, .dlc = 4};
    slcan_format_frame(&r, l);
    CHECK(strcmp(l, "r7FF4\r") == 0, "r format %s", l);
    can_frame_t R = {.id = 0x18FFD4C0, .ext = 1, .rtr = 1, .dlc = 0};
    slcan_format_frame(&R, l);
    CHECK(strcmp(l, "R18FFD4C00\r") == 0, "R format %s", l);
    can_frame_t e = {.id = 0x123, .dlc = 0};
    slcan_format_frame(&e, l);
    CHECK(strcmp(l, "t1230\r") == 0, "empty format %s", l);

    /* Round trip through the parser, as the kernel would send it. */
    can_frame_t back;
    slcan_format_frame(&g, l);
    CHECK(slcan_parse_frame(l, strlen(l) - 1, &back) == 0 && back.id == g.id &&
          back.ext && back.dlc == 2 && back.data[1] == 0x55, "round trip");

    /* State: s<state><rx3><tx3>, as slcan_bump_state reads it. */
    slcan_format_state('a', 57, 33, l);
    CHECK(strcmp(l, "sa057033\r") == 0, "state format %s", l);
    slcan_format_state('b', 0, 255, l);
    CHECK(strcmp(l, "sb000255\r") == 0, "bus-off format %s", l);
    slcan_format_state('p', 128, 7, l);
    CHECK(strcmp(l, "sp128007\r") == 0, "passive format %s", l);

    /* Error lines: the kernel parses a line only when more than 4
     * characters precede the CR, and checks the length digit. */
    size_t n = slcan_format_tx_dropped(l);
    CHECK(n == strlen(l) && strcmp(l, "e3OOO\r") == 0, "tx drop %s", l);
    CHECK(n - 1 > 4 && (size_t)(l[1] - '0') + 2 == n - 1, "tx drop line not parsable");
    n = slcan_format_rx_overrun(l);
    CHECK(strcmp(l, "e3ooo\r") == 0, "rx overrun %s", l);
    CHECK(n - 1 > 4 && (size_t)(l[1] - '0') + 2 == n - 1, "rx overrun line not parsable");
}

static void test_status_identity(void)
{
    reset();
    flags_val = 0x2A;
    feed("F\r");
    CHECK(strcmp(out, "F2A\r") == 0, "F reply %s", out);
    feed("F\r");
    CHECK(strcmp(out, "F00\r") == 0, "F not cleared %s", out);
    feed("V\r");
    CHECK(strcmp(out, "V0100 slcan_spi_NOT_FOR_VEHICLE v-test elf=0123\r") == 0, "V reply %s", out);
    feed("N\r");
    CHECK(strcmp(out, "Nslcan_spi_NOT_FOR_VEHICLE v-test elf=0123\r") == 0, "N reply %s", out);
    feed("B\r");
    CHECK(banners == 1 && strcmp(out, "\r") == 0, "B: banners=%d reply %s", banners, out);
}

static void test_lines(void)
{
    reset();
    feed("\n\rS6\r\nO\r\n");
    CHECK(strcmp(out, "\r\r\r") == 0 && opened == 1, "LF not ignored: %s", out);
    reset();
    char big[200];
    memset(big, 'x', sizeof big);
    big[0] = 'V';
    big[sizeof big - 2] = '\r';
    big[sizeof big - 1] = 0;
    feed(big);
    CHECK(strcmp(out, "\a") == 0, "too long line: %s", out);
    feed("Q\r");
    CHECK(strcmp(out, "\a") == 0, "unknown command accepted: %s", out);
    feed("Cx\r");
    CHECK(strcmp(out, "\a") == 0 && closed == 0, "Cx accepted: %s", out);
}

/* SPEC T13: a frame for the image itself is acted on, open or closed, and
 * never sent, counted as dropped or refused. */
static void test_local(void)
{
    static const char *jam = "T1F4A4D4384A4D541400641388\r";
    reset();
    feed(jam);
    CHECK(strcmp(out, "Z\r") == 0 && locals == 1, "closed: %s locals=%d", out, locals);
    CHECK(sent == 0 && dropped == 0, "closed: sent=%d dropped=%d", sent, dropped);
    feed("S6\rL\r");
    feed(jam);
    CHECK(strcmp(out, "Z\r") == 0 && locals == 2, "listen-only: %s locals=%d", out, locals);
    CHECK(sent == 0 && dropped == 0, "listen-only: sent=%d dropped=%d", sent, dropped);
    feed("C\rO\r");
    feed(jam);
    CHECK(strcmp(out, "Z\r") == 0 && locals == 3, "open: %s locals=%d", out, locals);
    CHECK(sent == 0 && dropped == 0, "open: sent=%d dropped=%d", sent, dropped);
    /* send_result nonzero: a full queue does not matter to it */
    send_result = 1;
    feed(jam);
    CHECK(strcmp(out, "Z\r") == 0 && dropped == 0, "queue full: %s dropped=%d", out, dropped);
    send_result = 0;
    /* other frames still go to send() */
    feed("T1F4A4D440\r");
    CHECK(strcmp(out, "Z\r") == 0 && sent == 1 && locals == 4, "ordinary frame: %s sent=%d", out, sent);
    CHECK(last_sent.id == 0x1F4A4D44u, "ordinary frame id %lx", (unsigned long)last_sent.id);
    /* a malformed line is refused before it is asked */
    feed("T1F4A4D4384A4D\r");
    CHECK(strcmp(out, "\a") == 0 && locals == 4, "malformed: %s locals=%d", out, locals);
}

/* Without a local op the protocol is as before. */
static void test_no_local(void)
{
    static const slcan_ops_t ops2 = {
        .open = f_open, .close = f_close, .send = f_send, .flags = f_flags,
        .dropped = f_dropped, .write = f_write, .banner = f_banner,
        .identity = "x", .ctx = NULL,
    };
    reset();
    slcan_init(&s, &ops2);
    feed("T1F4A4D430\r");
    CHECK(strcmp(out, "\a") == 0 && sent == 0, "no local, closed: %s", out);
}

int main(void)
{
    test_local();
    test_no_local();
    test_silent_until_open();
    test_rate();
    test_listen_only();
    test_queue_full();
    test_frames();
    test_format();
    test_status_identity();
    test_lines();
    if (failures) {
        printf("%d FAILED\n", failures);
        return 1;
    }
    printf("ALL PASS\n");
    return 0;
}
