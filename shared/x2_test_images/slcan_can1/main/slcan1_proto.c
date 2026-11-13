/*
 * slcan1_proto.c -- slcan_can1's protocol layer (slcan1_proto.h), moved from
 * charge-interposer/firmware/main/slcan_main.cpp with its behaviour kept.
 */
#include "slcan1_proto.h"

#include <stdio.h>
#include <string.h>

static int hex_val(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void put(slcan1_t *s, const char *text)
{
    s->ops->write(s->ops->ctx, text, strlen(text));
}

static void ok(slcan1_t *s)
{
    put(s, "\r");
}

static void err(slcan1_t *s)
{
    s->rejected++;
    put(s, "\a");
}

/* tIIILDD.. -- standard id, one length digit, then len*2 hex digits. */
static void transmit(slcan1_t *s, const char *l, uint8_t n)
{
    if (!s->open || s->listen_only) {
        err(s);
        return;
    }
    if (n < 5) {
        err(s);
        return;
    }
    int i0 = hex_val(l[1]), i1 = hex_val(l[2]), i2 = hex_val(l[3]);
    int dl = hex_val(l[4]);
    if (i0 < 0 || i1 < 0 || i2 < 0 || dl < 0 || dl > 8) {
        err(s);
        return;
    }
    uint32_t id = ((uint32_t)i0 << 8) | ((uint32_t)i1 << 4) | (uint32_t)i2;
    uint8_t len = (uint8_t)dl;
    if (n < (uint8_t)(5 + len * 2)) {
        err(s);
        return;
    }
    uint8_t data[8];
    for (uint8_t k = 0; k < len; k++) {
        int hi = hex_val(l[5 + k * 2]);
        int lo = hex_val(l[6 + k * 2]);
        if (hi < 0 || lo < 0) {
            err(s);
            return;
        }
        data[k] = (uint8_t)((hi << 4) | lo);
    }
    /* No reply, queued or not (slcan1_proto.h). */
    if (s->ops->send(s->ops->ctx, id, data, len)) {
        s->accepted++;
    } else {
        s->full++;
    }
}

static void report(slcan1_t *s)
{
    slcan1_port_counts_t c;
    memset(&c, 0, sizeof c);
    s->ops->counts(s->ops->ctx, &c);
    char out[256];
    int n = snprintf(out, sizeof out,
        "slcan1 open=%d listen_only=%d accepted=%lu queue_full=%lu rejected=%lu "
        "tx=%lu tx_dropped=%lu rx=%lu rx_dropped=%lu bus_errors=%lu bus_off=%d\r\n",
        s->open ? 1 : 0, s->listen_only ? 1 : 0,
        (unsigned long)s->accepted, (unsigned long)s->full,
        (unsigned long)s->rejected,
        (unsigned long)c.tx, (unsigned long)c.tx_dropped,
        (unsigned long)c.rx, (unsigned long)c.rx_dropped,
        (unsigned long)c.bus_errors, c.bus_off ? 1 : 0);
    if (n > 0) s->ops->write(s->ops->ctx, out, (size_t)n < sizeof out ? (size_t)n : sizeof out - 1);
}

static void line(slcan1_t *s, const char *l, uint8_t n)
{
    if (n == 0) return;
    switch (l[0]) {
    case 'S':
        /* Only 500 kbit/s (slcan1_proto.h). */
        if (n >= 2 && l[1] == '6') ok(s); else err(s);
        break;
    case 'O':
    case 'L':
        if (s->open) {
            ok(s);
            break;
        }
        s->listen_only = (l[0] == 'L');
        if (s->ops->open(s->ops->ctx, s->listen_only)) {
            s->open = 1;
            ok(s);
        } else {
            err(s);
        }
        break;
    case 'C':
        if (s->open) {
            s->ops->close(s->ops->ctx);
            s->open = 0;
            s->listen_only = 0;
        }
        ok(s);
        break;
    case 't':
        transmit(s, l, n);
        break;
    case 'V':
        put(s, "V0101\r");
        break;
    case 'N':
        put(s, "NF001\r");
        break;
    case '?':
        report(s);
        break;
    default:
        /* Everything else, T, r and R included, is refused, not ignored. */
        err(s);
        break;
    }
}

void slcan1_init(slcan1_t *s, const slcan1_ops_t *ops)
{
    memset(s, 0, sizeof *s);
    s->ops = ops;
}

void slcan1_input(slcan1_t *s, const uint8_t *data, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        char c = (char)data[i];
        if (c == '\r' || c == '\n') {
            if (!s->skip) {
                s->line[s->len] = '\0';
                line(s, s->line, s->len);
            }
            s->len = 0;
            s->skip = 0;
        } else if (s->skip) {
            /* the rest of an overlong line: dropped */
        } else if (s->len < sizeof s->line - 1) {
            s->line[s->len++] = c;
        } else {
            /* Overlong: drop it and resynchronise at the next terminator,
             * rather than truncate it into a different valid command, or
             * run its tail as one (slcan1_proto.h). */
            s->len = 0;
            s->skip = 1;
            err(s);
        }
    }
}

int slcan1_is_open(const slcan1_t *s)
{
    return s->open;
}
