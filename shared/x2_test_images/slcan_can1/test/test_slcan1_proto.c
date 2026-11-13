/*
 * Host unit tests of slcan_can1's protocol layer, ../main/slcan1_proto.c,
 * through a fake of the rest of the image (the ops). Written from the
 * protocol slcan_main.cpp's header sets out, which slcan1_proto.h restates,
 * and from x2_test_images SPEC T8a: silent until opened, a close returns it
 * to silent, listen-only never sends.
 */
#include <string.h>

#include "check.h"
#include "slcan1_proto.h"

typedef struct {
    int opens, closes;
    int open_listen_only;       /* the last open's argument */
    int open_ok;                /* what open answers */
    int sends;
    uint32_t id;
    uint8_t data[8], len;
    int send_ok;                /* what send answers */
    slcan1_port_counts_t counts;
    char out[1024];
    size_t out_len;
} env_t;

static env_t E;

static int f_open(void *ctx, int lo)
{
    (void)ctx;
    E.opens++;
    E.open_listen_only = lo;
    return E.open_ok;
}

static void f_close(void *ctx)
{
    (void)ctx;
    E.closes++;
}

static int f_send(void *ctx, uint32_t id, const uint8_t *data, uint8_t len)
{
    (void)ctx;
    E.sends++;
    E.id = id;
    E.len = len;
    memcpy(E.data, data, len);
    return E.send_ok;
}

static void f_counts(void *ctx, slcan1_port_counts_t *out)
{
    (void)ctx;
    *out = E.counts;
}

static void f_write(void *ctx, const char *s, size_t n)
{
    (void)ctx;
    if (E.out_len + n >= sizeof E.out) n = sizeof E.out - 1 - E.out_len;
    memcpy(E.out + E.out_len, s, n);
    E.out_len += n;
    E.out[E.out_len] = '\0';
}

static const slcan1_ops_t OPS = {
    .open = f_open, .close = f_close, .send = f_send,
    .counts = f_counts, .write = f_write, .ctx = NULL,
};

static slcan1_t S;

static void fresh(void)
{
    memset(&E, 0, sizeof E);
    E.open_ok = 1;
    E.send_ok = 1;
    slcan1_init(&S, &OPS);
}

static void in(const char *s)
{
    slcan1_input(&S, (const uint8_t *)s, strlen(s));
}

/* The bytes written since the last call, then forgotten. */
static const char *out(void)
{
    static char copy[1024];
    memcpy(copy, E.out, E.out_len + 1);
    E.out_len = 0;
    E.out[0] = '\0';
    return copy;
}

#define FRAME "t7E08AAAAAAAAAAAAAAAA\r"

static void test_init_calls_nothing(void)
{
    fresh();
    CHECK(E.opens == 0 && E.closes == 0 && E.sends == 0 && E.out_len == 0,
          "init touched the ops: opens %d closes %d sends %d out %zu",
          E.opens, E.closes, E.sends, E.out_len);
    CHECK(!slcan1_is_open(&S), "open at init");
}

/* T8a: nothing is sent before an open. */
static void test_silent_until_open(void)
{
    fresh();
    in(FRAME);
    CHECK(E.sends == 0, "a frame was sent before any open (%d)", E.sends);
    CHECK(strcmp(out(), "\a") == 0, "a frame before open is not refused with BEL");
    in("S6\r");
    in(FRAME);
    CHECK(E.sends == 0, "a frame was sent after S6 alone (%d)", E.sends);
    CHECK(strcmp(out(), "\r\a") == 0, "S6 then a frame: not CR then BEL");
    CHECK(E.opens == 0, "S6 opened the port");
    CHECK(S.rejected == 2, "rejected %lu, not 2", (unsigned long)S.rejected);
}

static void test_open_normal_sends(void)
{
    fresh();
    in("S6\rO\r");
    CHECK(strcmp(out(), "\r\r") == 0, "S6, O: not CR CR");
    CHECK(E.opens == 1 && E.open_listen_only == 0, "O: opens %d listen_only %d",
          E.opens, E.open_listen_only);
    CHECK(slcan1_is_open(&S), "not open after O");
    in(FRAME);
    CHECK(E.sends == 1 && E.id == 0x7E0 && E.len == 8, "frame: sends %d id %03lX len %u",
          E.sends, (unsigned long)E.id, E.len);
    int all_aa = 1;
    for (int i = 0; i < 8; i++) all_aa &= E.data[i] == 0xAA;
    CHECK(all_aa, "frame data not AA x 8");
    CHECK(strcmp(out(), "") == 0, "a queued t was answered (it replies nothing)");
    CHECK(S.accepted == 1, "accepted %lu", (unsigned long)S.accepted);
}

/* T8a: a listen-only open never ACKs: the port opens listen-only, and the
 * protocol never offers it a frame. */
static void test_listen_only_never_sends(void)
{
    fresh();
    in("S6\rL\r");
    CHECK(E.opens == 1 && E.open_listen_only == 1, "L: opens %d listen_only %d",
          E.opens, E.open_listen_only);
    CHECK(strcmp(out(), "\r\r") == 0, "S6, L: not CR CR");
    in(FRAME);
    CHECK(E.sends == 0, "listen-only offered a frame to the port");
    CHECK(strcmp(out(), "\a") == 0, "a frame while listen-only is not refused with BEL");
    /* O while open changes nothing: still listen-only. */
    in("O\r");
    CHECK(E.opens == 1, "O while open (listen-only) reopened the port");
    CHECK(strcmp(out(), "\r") == 0, "O while open: not CR");
    in(FRAME);
    CHECK(E.sends == 0, "O while listen-only made it send");
}

/* T8a: a close returns it to silent. */
static void test_close_silences(void)
{
    fresh();
    in("S6\rO\rC\r");
    CHECK(E.closes == 1, "C: closes %d", E.closes);
    CHECK(!slcan1_is_open(&S), "open after C");
    CHECK(strcmp(out(), "\r\r\r") == 0, "S6 O C: not three CRs");
    in(FRAME);
    CHECK(E.sends == 0, "a frame was sent after C");
    CHECK(strcmp(out(), "\a") == 0, "a frame after C not refused");
    in("C\r");
    CHECK(E.closes == 1, "C while closed called close again");
    CHECK(strcmp(out(), "\r") == 0, "C while closed: not CR");
    /* L after a normal session, then C, then O: each open in its own mode. */
    in("L\r");
    CHECK(E.opens == 2 && E.open_listen_only == 1, "L after C: opens %d lo %d",
          E.opens, E.open_listen_only);
    in("C\rO\r");
    CHECK(E.opens == 3 && E.open_listen_only == 0, "O after L, C: opens %d lo %d",
          E.opens, E.open_listen_only);
    out();
    in(FRAME);
    CHECK(E.sends == 1, "O after a listen-only session does not send");
}

static void test_open_refused(void)
{
    fresh();
    E.open_ok = 0;
    in("O\r");
    CHECK(strcmp(out(), "\a") == 0, "a refused open is not answered BEL");
    CHECK(!slcan1_is_open(&S), "open after a refused open");
    in(FRAME);
    CHECK(E.sends == 0, "a frame was sent after a refused open");
}

static void test_rates(void)
{
    const char *bad[] = {"S0\r", "S3\r", "S5\r", "S7\r", "S8\r", "S\r", "s1C\r"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        fresh();
        in(bad[i]);
        CHECK(strcmp(out(), "\a") == 0, "rate command %.3s accepted", bad[i]);
    }
    fresh();
    in("S6\r");
    CHECK(strcmp(out(), "\r") == 0, "S6 refused");
}

static void test_frames_refused(void)
{
    const char *bad[] = {
        "T000007E08AAAAAAAAAAAAAAAA\r",   /* extended */
        "r7E00\r",                        /* remote */
        "R000007E00\r",
        "t7E09AAAAAAAAAAAAAAAAAA\r",      /* DLC 9 */
        "t7E\r",                          /* short */
        "t7G08AAAAAAAAAAAAAAAA\r",        /* bad id digit */
        "t7E02AA\r",                      /* fewer data digits than the DLC */
        "t7E02AZAA\r",                    /* bad data digit */
    };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        fresh();
        in("O\r");
        out();
        in(bad[i]);
        CHECK(E.sends == 0, "refused frame %s was sent", bad[i]);
        CHECK(strcmp(out(), "\a") == 0, "frame %s not answered BEL", bad[i]);
    }
    fresh();
    in("O\rt1230\r");
    CHECK(E.sends == 1 && E.id == 0x123 && E.len == 0, "zero-length frame: sends %d", E.sends);
    in("tfff1ab\r");
    CHECK(E.sends == 2 && E.id == 0xFFF && E.len == 1 && E.data[0] == 0xAB,
          "lower-case hex frame: sends %d id %03lX", E.sends, (unsigned long)E.id);
}

static void test_queue_full(void)
{
    fresh();
    in("O\r");
    out();
    E.send_ok = 0;
    in(FRAME);
    CHECK(strcmp(out(), "") == 0, "a frame the queue had no room for was answered");
    CHECK(S.full == 1 && S.accepted == 0 && S.rejected == 0,
          "full %lu accepted %lu rejected %lu", (unsigned long)S.full,
          (unsigned long)S.accepted, (unsigned long)S.rejected);
}

static void test_identity(void)
{
    fresh();
    in("V\r");
    CHECK(strcmp(out(), "V0101\r") == 0, "V: not V0101");
    in("N\r");
    CHECK(strcmp(out(), "NF001\r") == 0, "N: not NF001");
    CHECK(E.opens == 0 && E.sends == 0, "V/N touched the port");
}

static void test_counters_line(void)
{
    fresh();
    in("L\r");                     /* opens listen-only */
    in(FRAME);                     /* rejected 1 */
    in("C\rO\r");
    E.send_ok = 1;
    in(FRAME FRAME);               /* accepted 2 */
    E.send_ok = 0;
    in(FRAME);                     /* full 1 */
    in("x\r");                     /* rejected 2 */
    out();
    E.counts.tx = 11;
    E.counts.tx_dropped = 12;
    E.counts.rx = 13;
    E.counts.rx_dropped = 14;
    E.counts.bus_errors = 15;
    E.counts.bus_off = 1;
    in("?\r");
    const char *want = "slcan1 open=1 listen_only=0 accepted=2 queue_full=1 rejected=2 "
                       "tx=11 tx_dropped=12 rx=13 rx_dropped=14 bus_errors=15 bus_off=1\r\n";
    const char *got = out();
    CHECK(strcmp(got, want) == 0, "? line:\n  got  %s  want %s", got, want);
    fresh();
    in("L\r?\r");
    got = out();
    CHECK(strstr(got, "open=1 listen_only=1 ") != NULL, "? after L: %s", got);
}

static void test_lines(void)
{
    fresh();
    in("V\nN\r");
    CHECK(strcmp(out(), "V0101\rNF001\r") == 0, "LF does not end a line as CR does");
    in("\r\n\r");
    CHECK(strcmp(out(), "") == 0, "empty lines answered");
    in("X\r");
    CHECK(strcmp(out(), "\a") == 0, "an unknown command is not refused");
    in("F\r");
    CHECK(strcmp(out(), "\a") == 0, "F answered (no status flags in this image)");
    /* split across reads */
    in("V");
    CHECK(strcmp(out(), "") == 0, "a line answered before its CR");
    in("\r");
    CHECK(strcmp(out(), "V0101\r") == 0, "a line split across reads");
}

/* An overlong line is dropped whole, answered BEL once, and its tail is not
 * run as a command (slcan1_proto.h: the one change from slcan_main.cpp). */
static void test_overlong(void)
{
    fresh();
    char l[200];
    memset(l, 'x', 100);
    strcpy(l + 100, "O\r");
    in(l);
    CHECK(strcmp(out(), "\a") == 0, "overlong line: not one BEL");
    CHECK(E.opens == 0, "the tail of an overlong line opened the port");
    in("V\r");
    CHECK(strcmp(out(), "V0101\r") == 0, "no resync after an overlong line");
    /* SLCAN1_LINE_MAX - 1 characters is still a line */
    fresh();
    in("O\r");
    out();
    char m[SLCAN1_LINE_MAX + 2];
    memset(m, '0', sizeof m);
    memcpy(m, "t7E08AAAAAAAAAAAAAAAA", 21);
    m[SLCAN1_LINE_MAX - 1] = '\r';
    m[SLCAN1_LINE_MAX] = '\0';
    in(m);
    CHECK(E.sends == 1, "a %d-character line was not taken", SLCAN1_LINE_MAX - 1);
    m[SLCAN1_LINE_MAX - 1] = '0';
    m[SLCAN1_LINE_MAX] = '\r';
    m[SLCAN1_LINE_MAX + 1] = '\0';
    in(m);
    CHECK(E.sends == 1, "a %d-character line was taken", SLCAN1_LINE_MAX);
    CHECK(strcmp(out(), "\a") == 0, "a %d-character line not refused", SLCAN1_LINE_MAX);
}

int main(void)
{
    test_init_calls_nothing();
    test_silent_until_open();
    test_open_normal_sends();
    test_listen_only_never_sends();
    test_close_silences();
    test_open_refused();
    test_rates();
    test_frames_refused();
    test_queue_full();
    test_identity();
    test_counters_line();
    test_lines();
    test_overlong();
    return check_report("test_slcan1_proto");
}
