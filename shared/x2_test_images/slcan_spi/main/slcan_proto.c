/*
 * slcan_proto.c -- the Lawicel SLCAN protocol for slcan_spi (slcan_proto.h).
 * NOT FOR VEHICLE (x2_test_images SPEC T14). No ESP-IDF here: the host
 * unit tests build this file as it is.
 */
#include "slcan_proto.h"

#include <stdio.h>
#include <string.h>

#define CR  "\r"
#define BEL "\a"

static void put(slcan_t *s, const char *txt)
{
    s->ops->write(s->ops->ctx, txt, strlen(txt));
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int hexnum(const char *p, size_t n, uint32_t *out)
{
    uint32_t v = 0;
    for (size_t i = 0; i < n; i++) {
        int h = hexval(p[i]);
        if (h < 0) return -1;
        v = (v << 4) | (uint32_t)h;
    }
    *out = v;
    return 0;
}

int slcan_parse_frame(const char *line, size_t len, can_frame_t *f)
{
    if (len < 1) return -1;
    memset(f, 0, sizeof *f);
    size_t idlen;
    switch (line[0]) {
    case 't': idlen = 3; break;
    case 'r': idlen = 3; f->rtr = 1; break;
    case 'T': idlen = 8; f->ext = 1; break;
    case 'R': idlen = 8; f->ext = 1; f->rtr = 1; break;
    default: return -1;
    }
    if (len < 1 + idlen + 1) return -1;
    uint32_t id;
    if (hexnum(line + 1, idlen, &id)) return -1;
    if (id > (f->ext ? 0x1FFFFFFFu : 0x7FFu)) return -1;
    f->id = id;
    char d = line[1 + idlen];
    if (d < '0' || d > '8') return -1;
    f->dlc = (uint8_t)(d - '0');
    size_t want = 1 + idlen + 1 + (f->rtr ? 0 : 2u * f->dlc);
    if (len != want) return -1;
    if (!f->rtr) {
        const char *p = line + 1 + idlen + 1;
        for (unsigned i = 0; i < f->dlc; i++) {
            uint32_t b;
            if (hexnum(p + 2 * i, 2, &b)) return -1;
            f->data[i] = (uint8_t)b;
        }
    }
    return 0;
}

size_t slcan_format_frame(const can_frame_t *f, char *out)
{
    static const char hex[] = "0123456789ABCDEF";
    char *p = out;
    unsigned dlc = f->dlc > 8 ? 8 : f->dlc;
    if (f->ext) {
        *p++ = f->rtr ? 'R' : 'T';
        for (int i = 7; i >= 0; i--) *p++ = hex[(f->id >> (4 * i)) & 0xF];
    } else {
        *p++ = f->rtr ? 'r' : 't';
        for (int i = 2; i >= 0; i--) *p++ = hex[(f->id >> (4 * i)) & 0xF];
    }
    *p++ = (char)('0' + dlc);
    if (!f->rtr) {
        for (unsigned i = 0; i < dlc; i++) {
            *p++ = hex[f->data[i] >> 4];
            *p++ = hex[f->data[i] & 0xF];
        }
    }
    *p++ = '\r';
    *p = '\0';
    return (size_t)(p - out);
}

size_t slcan_format_state(char state, unsigned rec, unsigned tec, char *out)
{
    if (rec > 999) rec = 999;
    if (tec > 999) tec = 999;
    return (size_t)snprintf(out, SLCAN_LINE_MAX, "s%c%03u%03u\r", state, rec, tec);
}

size_t slcan_format_tx_dropped(char *out)
{
    strcpy(out, "e3OOO\r");
    return 6;
}

size_t slcan_format_rx_overrun(char *out)
{
    strcpy(out, "e3ooo\r");
    return 6;
}

void slcan_init(slcan_t *s, const slcan_ops_t *ops)
{
    memset(s, 0, sizeof *s);
    s->ops = ops;
    s->mode = SLCAN_CLOSED;
}

static void frame_cmd(slcan_t *s)
{
    can_frame_t f;
    if (slcan_parse_frame(s->line, s->len, &f)) {
        put(s, BEL);
        return;
    }
    if (s->ops->local && s->ops->local(s->ops->ctx, &f)) {
        put(s, f.ext ? "Z" CR : "z" CR);
        return;
    }
    if (s->mode == SLCAN_CLOSED) {
        put(s, BEL);
        return;
    }
    if (s->mode != SLCAN_OPEN_NORMAL || s->ops->send(s->ops->ctx, &f) != 0) {
        char e[SLCAN_LINE_MAX];
        size_t n = slcan_format_tx_dropped(e);
        s->ops->dropped(s->ops->ctx);
        put(s, BEL);
        s->ops->write(s->ops->ctx, e, n);
        return;
    }
    put(s, f.ext ? "Z" CR : "z" CR);
}

static void command(slcan_t *s)
{
    const char *l = s->line;
    size_t n = s->len;
    char out[SLCAN_LINE_MAX];

    if (n == 0) {
        put(s, CR);
        return;
    }
    switch (l[0]) {
    case 'S':
        if (s->mode != SLCAN_CLOSED) {
            put(s, BEL);
        } else if (n == 2 && l[1] == '6') {
            s->rate_ok = 1;
            put(s, CR);
        } else {
            s->rate_ok = 0;
            put(s, BEL);
        }
        return;
    case 's':
        if (s->mode == SLCAN_CLOSED) s->rate_ok = 0;
        put(s, BEL);
        return;
    case 'O':
    case 'L':
        if (n != 1 || s->mode != SLCAN_CLOSED || !s->rate_ok) {
            put(s, BEL);
            return;
        }
        if (s->ops->open(s->ops->ctx, l[0] == 'L') != 0) {
            put(s, BEL);
            return;
        }
        s->mode = (l[0] == 'L') ? SLCAN_OPEN_LISTEN : SLCAN_OPEN_NORMAL;
        put(s, CR);
        return;
    case 'C':
        if (n != 1) {
            put(s, BEL);
            return;
        }
        s->ops->close(s->ops->ctx);
        s->mode = SLCAN_CLOSED;
        put(s, CR);
        return;
    case 't':
    case 'T':
    case 'r':
    case 'R':
        frame_cmd(s);
        return;
    case 'F':
        if (n != 1) {
            put(s, BEL);
            return;
        }
        snprintf(out, sizeof out, "F%02X\r", s->ops->flags(s->ops->ctx));
        put(s, out);
        return;
    case 'V':
    case 'N':
        if (n != 1) {
            put(s, BEL);
            return;
        }
        /* V keeps Lawicel's four digits first, so tools that read
         * V<hw2><sw2> still parse it; N is the identity alone. */
        put(s, l[0] == 'V' ? "V0100 " : "N");
        put(s, s->ops->identity);
        put(s, CR);
        return;
    case 'B':
        if (n != 1) {
            put(s, BEL);
            return;
        }
        s->ops->banner(s->ops->ctx);
        put(s, CR);
        return;
    default:
        put(s, BEL);
        return;
    }
}

void slcan_input(slcan_t *s, const uint8_t *buf, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        char c = (char)buf[i];
        if (c == '\n') continue;
        if (c == '\r') {
            if (s->too_long) put(s, BEL);
            else command(s);
            s->len = 0;
            s->too_long = 0;
            continue;
        }
        if (s->len < sizeof s->line - 1) {
            s->line[s->len++] = c;
            s->line[s->len] = '\0';
        } else {
            s->too_long = 1;
        }
    }
}
