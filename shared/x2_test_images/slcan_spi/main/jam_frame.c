/*
 * jam_frame.c -- the jam command channel of slcan_spi (jam_frame.h).
 * NOT FOR VEHICLE (x2_test_images SPEC T14). No ESP-IDF here.
 */
#include "jam_frame.h"

#include <stdio.h>
#include <string.h>

static unsigned be16(const uint8_t *p)
{
    return ((unsigned)p[0] << 8) | p[1];
}

int jam_frame_reserved(const can_frame_t *f)
{
    return f->ext && (f->id == JAM_CMD_ID || f->id == JAM_REPLY_ID);
}

jam_frame_kind_t jam_frame_decode(const can_frame_t *f, char *text)
{
    text[0] = '\0';
    if (!jam_frame_reserved(f)) return JAM_FRAME_NOT_JAM;
    if (f->id != JAM_CMD_ID || f->rtr || f->dlc != 8) return JAM_FRAME_BAD;
    const uint8_t *d = f->data;
    if (d[0] != JAM_SIG0 || d[1] != JAM_SIG1) return JAM_FRAME_BAD;
    switch (d[2]) {
    case 'T':
        snprintf(text, JAM_CMD_TEXT_MAX, "TRAIN %u %u %u", (unsigned)d[3],
                 be16(d + 4), be16(d + 6));
        return JAM_FRAME_CMD;
    case 'P':
        snprintf(text, JAM_CMD_TEXT_MAX, "PULSES %u %u %u", (unsigned)d[3],
                 be16(d + 4), be16(d + 6));
        return JAM_FRAME_CMD;
    case 'S':
    case '?':
        for (int i = 3; i < 8; i++)
            if (d[i]) return JAM_FRAME_BAD;
        strcpy(text, d[2] == 'S' ? "STOP" : "STATUS");
        return JAM_FRAME_CMD;
    default:
        return JAM_FRAME_BAD;
    }
}

size_t jam_frame_reply(const char *line, can_frame_t *frames, size_t max)
{
    size_t len = strlen(line), n = 0, pos = 0;
    if (max == 0) return 0;
    do {
        can_frame_t *f = &frames[n];
        memset(f, 0, sizeof *f);
        f->id = JAM_REPLY_ID;
        f->ext = 1;
        size_t k = len - pos > JAM_REPLY_CHARS ? JAM_REPLY_CHARS : len - pos;
        f->data[0] = JAM_SIG0;
        f->data[1] = JAM_SIG1;
        f->data[2] = (uint8_t)n;
        memcpy(f->data + 3, line + pos, k);
        pos += k;
        f->dlc = (uint8_t)(3 + k);
        n++;
        if (pos >= len || n == max) f->data[2] |= JAM_REPLY_LAST;
    } while (pos < len && n < max);
    return n;
}
