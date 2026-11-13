/*
 * test_jam_frame.c -- host unit tests of slcan_spi's jam channel,
 * ../main/jam_frame.c (x2_test_images SPEC T13's reserved-frame fallback),
 * built with the machine's compiler (Makefile). NOT FOR VEHICLE (SPEC T14).
 *
 * mutate.py breaks one behaviour of jam_frame.c at a time and checks these
 * tests fail on each.
 */
#include <stdio.h>
#include <string.h>

#include "jam_frame.h"

static int failures;

#define CHECK(cond, ...) do { \
    if (!(cond)) { \
        failures++; \
        printf("FAIL %s:%d: ", __FILE__, __LINE__); \
        printf(__VA_ARGS__); \
        printf("\n"); \
    } \
} while (0)

static can_frame_t cmd(uint8_t op, uint8_t b3, unsigned u45, unsigned u67)
{
    can_frame_t f;
    memset(&f, 0, sizeof f);
    f.id = JAM_CMD_ID;
    f.ext = 1;
    f.dlc = 8;
    f.data[0] = 'J';
    f.data[1] = 'M';
    f.data[2] = op;
    f.data[3] = b3;
    f.data[4] = (uint8_t)(u45 >> 8);
    f.data[5] = (uint8_t)u45;
    f.data[6] = (uint8_t)(u67 >> 8);
    f.data[7] = (uint8_t)u67;
    return f;
}

static void test_ids(void)
{
    /* both have J1939's EDP and DP bits (24, 25) set, and differ */
    CHECK(((JAM_CMD_ID >> 24) & 3) == 3, "command ID EDP/DP not both set");
    CHECK(((JAM_REPLY_ID >> 24) & 3) == 3, "reply ID EDP/DP not both set");
    CHECK(JAM_CMD_ID != JAM_REPLY_ID, "command and reply IDs equal");
    CHECK(JAM_CMD_ID <= 0x1FFFFFFFu && JAM_REPLY_ID <= 0x1FFFFFFFu, "ID over 29 bits");
}

static void test_decode(void)
{
    char t[JAM_CMD_TEXT_MAX];
    can_frame_t f = cmd('T', 20, 100, 5000);
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_CMD && strcmp(t, "TRAIN 20 100 5000") == 0,
          "TRAIN: %s", t);
    f = cmd('T', 50, 20000, 65535);
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_CMD && strcmp(t, "TRAIN 50 20000 65535") == 0,
          "TRAIN big-endian, past the cap (jam_pulse caps it): %s", t);
    f = cmd('P', 12, 10, 1000);
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_CMD && strcmp(t, "PULSES 12 10 1000") == 0,
          "PULSES: %s", t);
    f = cmd('S', 0, 0, 0);
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_CMD && strcmp(t, "STOP") == 0, "STOP: %s", t);
    f = cmd('?', 0, 0, 0);
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_CMD && strcmp(t, "STATUS") == 0, "STATUS: %s", t);
}

static void test_refused(void)
{
    char t[JAM_CMD_TEXT_MAX];
    can_frame_t f;

    f = cmd('T', 20, 100, 5000);
    f.data[0] = 'j';
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "bad signature byte 0 decoded");
    f = cmd('T', 20, 100, 5000);
    f.data[1] = 'X';
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "bad signature byte 1 decoded");
    f = cmd('T', 20, 100, 5000);
    f.dlc = 7;
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "DLC 7 decoded");
    f = cmd('T', 20, 100, 5000);
    f.rtr = 1;
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "remote frame decoded");
    f = cmd('X', 20, 100, 5000);
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "unknown command decoded");
    for (int i = 3; i < 8; i++) {
        f = cmd('S', 0, 0, 0);
        f.data[i] = 1;
        CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "STOP with byte %d set decoded", i);
        f = cmd('?', 0, 0, 0);
        f.data[i] = 1;
        CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "STATUS with byte %d set decoded", i);
    }
    /* the reply identifier from the host: reserved, never a command */
    f = cmd('T', 20, 100, 5000);
    f.id = JAM_REPLY_ID;
    CHECK(jam_frame_reserved(&f), "reply ID not reserved");
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_BAD, "reply ID decoded as a command");
}

static void test_not_jam(void)
{
    char t[JAM_CMD_TEXT_MAX];
    can_frame_t f = cmd('T', 20, 100, 5000);
    f.ext = 0;
    f.id = JAM_CMD_ID & 0x7FF;
    CHECK(!jam_frame_reserved(&f), "11-bit frame reserved");
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_NOT_JAM, "11-bit frame taken");
    f = cmd('T', 20, 100, 5000);
    f.id = JAM_CMD_ID + 1;
    CHECK(!jam_frame_reserved(&f), "neighbouring ID reserved");
    CHECK(jam_frame_decode(&f, t) == JAM_FRAME_NOT_JAM, "neighbouring ID taken");
    f = cmd('T', 20, 100, 5000);
    CHECK(jam_frame_reserved(&f), "command ID not reserved");
}

/* Joins frames back as the bench harness does; -1 on any malformed frame. */
static int join(const can_frame_t *fr, size_t n, char *out, size_t cap)
{
    size_t len = 0;
    for (size_t i = 0; i < n; i++) {
        const can_frame_t *f = &fr[i];
        if (!f->ext || f->id != JAM_REPLY_ID || f->rtr) return -1;
        if (f->dlc < 3 || f->data[0] != 'J' || f->data[1] != 'M') return -1;
        if ((f->data[2] & 0x7F) != i) return -1;
        int last = (f->data[2] & JAM_REPLY_LAST) != 0;
        if (last != (i == n - 1)) return -1;
        for (unsigned k = 3; k < f->dlc; k++) {
            if (len + 1 >= cap) return -1;
            out[len++] = (char)f->data[k];
        }
    }
    out[len] = 0;
    return 0;
}

static void test_reply(void)
{
    can_frame_t fr[JAM_REPLY_FRAMES_MAX + 4];
    char line[200], back[200];
    size_t n;

    n = jam_frame_reply("ACK STOP busy=1", fr, JAM_REPLY_FRAMES_MAX);
    CHECK(n == 3, "15 characters: %zu frames, want 3", n);
    CHECK(join(fr, n, back, sizeof back) == 0 && strcmp(back, "ACK STOP busy=1") == 0,
          "joined: %s", back);
    CHECK(fr[0].dlc == 8 && fr[2].dlc == 8, "full frames DLC %u, %u", fr[0].dlc, fr[2].dlc);

    n = jam_frame_reply("DONE TRAIN", fr, JAM_REPLY_FRAMES_MAX);
    CHECK(n == 2 && fr[1].dlc == 8, "10 characters: %zu frames", n);

    n = jam_frame_reply("ERR", fr, JAM_REPLY_FRAMES_MAX);
    CHECK(n == 1 && fr[0].dlc == 6 && fr[0].data[2] == JAM_REPLY_LAST, "3 characters: n=%zu dlc=%u",
          n, fr[0].dlc);

    n = jam_frame_reply("", fr, JAM_REPLY_FRAMES_MAX);
    CHECK(n == 1 && fr[0].dlc == 3 && fr[0].data[2] == JAM_REPLY_LAST, "empty: n=%zu", n);

    /* the longest jam_pulse reply line: JAM_REPLY_MAX 160, NUL included */
    for (int i = 0; i < 159; i++) line[i] = (char)('a' + i % 26);
    line[159] = 0;
    n = jam_frame_reply(line, fr, JAM_REPLY_FRAMES_MAX);
    CHECK(n == 32, "159 characters: %zu frames, want 32", n);
    CHECK(join(fr, n, back, sizeof back) == 0 && strcmp(back, line) == 0, "159 not joined back");
    for (size_t i = 0; i < n; i++) {
        CHECK(fr[i].ext && fr[i].id == JAM_REPLY_ID && !fr[i].rtr, "frame %zu not on the reply ID", i);
        CHECK(fr[i].data[0] == 'J' && fr[i].data[1] == 'M', "frame %zu without the signature", i);
    }

    /* cut, not overrun, when max is too small; the last frame says so */
    n = jam_frame_reply(line, fr, 4);
    CHECK(n == 4 && (fr[3].data[2] & JAM_REPLY_LAST), "cut at 4: n=%zu", n);
}

int main(void)
{
    test_ids();
    test_decode();
    test_refused();
    test_not_jam();
    test_reply();
    if (failures) {
        printf("%d FAILED\n", failures);
        return 1;
    }
    printf("ALL PASS\n");
    return 0;
}
