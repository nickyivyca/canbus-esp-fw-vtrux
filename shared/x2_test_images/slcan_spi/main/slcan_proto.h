/*
 * slcan_proto.h -- the Lawicel SLCAN protocol as slcan_spi speaks it, with
 * no ESP-IDF in it, so the host unit tests (../test/) build it with the
 * machine's compiler. NOT FOR VEHICLE (x2_test_images SPEC T14).
 *
 * Host to image, one command per line ended by CR (LF is ignored):
 *   S6        500 kbit/s, the only rate (SPEC T12). Any other Sn, and s
 *             (custom bit timing), is refused and clears the rate, so a
 *             later O or L is refused too: the image never opens at a rate
 *             the host did not ask for.
 *   O / L     open in normal mode (transmits and ACKs) / listen-only (never
 *             ACKs); refused unless closed and S6 was given (SPEC T8a, T12)
 *   C         close: back to silent (accepted when already closed)
 *   tiiildd.. / Tiiiiiiiildd.. / riiil / Riiiiiiiil   send a frame; a
 *             frame for the image itself (the jam channel, jam_frame.h) is
 *             acted on, open or closed, and never sent
 *   F         status flags, read and cleared: Fxx
 *   V / N     the build's identity (SPEC T12, T10)
 *   B         the banner again (console lines, SPEC T14)
 * Replies: CR for OK, BEL for refused, z CR / Z CR after a queued frame.
 *
 * Image to host, each line ended by CR, as Linux 6.12's slcan driver
 * (drivers/net/can/slcan/slcan-core.c) parses them (SPEC T12):
 *   t/T/r/R lines           received frames
 *   e3OOO                   one frame the host asked to send was dropped
 *                           (transmit queue full, or not open normal)
 *   e3ooo                   a receive overrun: at least one frame lost
 *   s<a|w|p|b><rec3><tec3>  a bus state change, with both error counters
 * The kernel only parses a line longer than 4 characters, so an error line
 * repeats its one code three times ("e1O" would be dropped unread). It
 * counts tx_errors, rx_errors and rx_over_errors once per line, so one line
 * per event keeps the host's counters exact.
 * Console lines start "\n# NOT FOR VEHICLE" and end in CR; the leading LF
 * puts them on their own line in a terminal without leaving a stray LF ahead
 * of the next frame line. The kernel buffers at most SLCAN_UNPROMPTED_MAX
 * characters of a line (its SLCAN_MTU) and counts one rx_over_error for
 * any longer line, so a line the image sends unprompted, which may reach
 * the kernel, is no longer than that: the boot line. A line starting with
 * LF is otherwise dropped unread. The full banner's lines are longer and go
 * out only on B, which the kernel never sends.
 */
#ifndef SLCAN_PROTO_H
#define SLCAN_PROTO_H

#include <stddef.h>
#include <stdint.h>
#include "can_frame.h"

/* Lawicel F status flags (one byte). */
#define SLCAN_F_RX_FIFO_FULL    0x01    /* the host link overflowed: frames lost */
#define SLCAN_F_TX_FIFO_FULL    0x02    /* a frame to send was dropped */
#define SLCAN_F_ERR_WARNING     0x04
#define SLCAN_F_DATA_OVERRUN    0x08    /* the CAN controller overran */
#define SLCAN_F_ERR_PASSIVE     0x20
#define SLCAN_F_BUS_ERROR       0x80    /* bus-off */

/* The longest line the image writes, CR and NUL included, banner lines
 * aside. */
#define SLCAN_LINE_MAX 40

/* The longest line, CR not counted, that Linux 6.12's slcan takes without
 * counting an rx_over_error: its SLCAN_MTU,
 * sizeof("T1111222281122334455667788EA5F\r") + 1. */
#define SLCAN_UNPROMPTED_MAX 33

/* What the protocol asks of the rest of the image. */
typedef struct {
    /* Open the CAN side; 0 on success. */
    int  (*open)(void *ctx, int listen_only);
    /* Close the CAN side: silent again. */
    void (*close)(void *ctx);
    /* A frame the host sent to the image itself, never to a bus (the jam
     * channel, SPEC T13): nonzero if f is one, having acted on it. It is
     * asked first, open or closed, so such a frame is never sent; the host
     * gets z / Z. May be NULL. */
    int  (*local)(void *ctx, const can_frame_t *f);
    /* Queue a frame to send; 0 queued, nonzero dropped. */
    int  (*send)(void *ctx, const can_frame_t *f);
    /* The F flags, read and cleared. */
    uint8_t (*flags)(void *ctx);
    /* Mark a frame to send as dropped: flags and counters. */
    void (*dropped)(void *ctx);
    /* Write text to the host. */
    void (*write)(void *ctx, const char *s, size_t n);
    /* Write the banner (console lines) to the host. */
    void (*banner)(void *ctx);
    /* The identity text for V and N, without the command letter. */
    const char *identity;
    void *ctx;
} slcan_ops_t;

typedef enum {
    SLCAN_CLOSED = 0,
    SLCAN_OPEN_NORMAL,
    SLCAN_OPEN_LISTEN,
} slcan_mode_t;

typedef struct {
    const slcan_ops_t *ops;
    char   line[64];
    size_t len;
    int    too_long;
    int    rate_ok;         /* S6 given since the last refused S or s */
    slcan_mode_t mode;
} slcan_t;

void slcan_init(slcan_t *s, const slcan_ops_t *ops);

/* Feed bytes from the host. */
void slcan_input(slcan_t *s, const uint8_t *buf, size_t n);

/* Lines to the host, each ended by CR and NUL-terminated; return the
 * length without the NUL. out must hold SLCAN_LINE_MAX bytes. */
size_t slcan_format_frame(const can_frame_t *f, char *out);
size_t slcan_format_state(char state, unsigned rec, unsigned tec, char *out);
size_t slcan_format_tx_dropped(char *out);
size_t slcan_format_rx_overrun(char *out);

/* Parse one frame command (t, T, r, R) without its CR; 0 on success. */
int slcan_parse_frame(const char *line, size_t len, can_frame_t *f);

#endif
