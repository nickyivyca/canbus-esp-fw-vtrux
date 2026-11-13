/*
 * slcan1_proto.h -- slcan_can1's protocol layer: the SLCAN subset python-can
 * sends, and nothing more, for a transmitter on the X2's CAN1 (TWAI).
 *
 * Moved from the charge interposer's slcan variant,
 * charge-interposer/firmware/main/slcan_main.cpp (x2_test_images SPEC T1),
 * with its behaviour kept: the same commands, replies and counters line.
 * Kept apart from the board, the TWAI port and the USB link, which reach it
 * only through slcan1_ops_t, so the host unit tests (../test/) run it
 * against a fake of the rest of the image.
 *
 * SILENT UNTIL TOLD TO OPEN (SPEC T8a): nothing is sent before an O, a C
 * closes, a reset returns the image to silent (it boots closed), and an L
 * opens listen-only, which never ACKs and cannot send.
 *
 *     S6           500 kbit/s. The only rate accepted
 *     O            open: the port comes up normal (transmits, ACKs)
 *     L            open listen-only (receives, never ACKs, cannot transmit)
 *     C            close
 *     tIIILDD..    standard-id data frame, e.g. t7E08AAAAAAAAAAAAAAAA
 *     V / N        V0101 / NF001, for tools that ask: fixed, naming the
 *                  protocol, not the build (SPEC T10 falls back to
 *                  esptool's verify_flash)
 *     ?            the counters line. Not SLCAN: see below
 *
 * Replies: CR on success, BEL on error, except for t, which replies nothing:
 * python-can's slcan writes and never reads an acknowledgement, and at ~2000
 * frames/s a per-frame reply is USB traffic competing with the frames.
 *
 * FOUR DELIBERATE REFUSALS, each because silence would be worse:
 *   - Only S6. Any other rate is an error rather than a best effort: a
 *     controller brought up at the wrong rate on a live 500 kbit/s bus
 *     produces error frames on every message.
 *   - Only t. Extended (T) and remote (r, R) frames are refused: a filler
 *     needs none of them, and accepting them would let a typo put an
 *     unexpected frame onto a shared bus.
 *   - Received frames are not forwarded to the host. The image is a
 *     transmitter, and the bench's witness counts the wire better than a
 *     USB link would. The app still drains them, so the controller cannot
 *     overrun.
 *   - No F (status flags): ? reports more.
 *
 * ? asks what standard SLCAN cannot: did every frame I sent go out. It
 * prints, on one line ending CR LF:
 *   slcan1 open= listen_only= accepted= queue_full= rejected= tx=
 *   tx_dropped= rx= rx_dropped= bus_errors= bus_off=
 * accepted: t commands queued; queue_full: t commands the port's transmit
 * queue had no room for (no reply, as for a queued one); rejected: commands
 * answered BEL; the rest are the port's (slcan1_port_counts_t).
 */
#ifndef SLCAN1_PROTO_H
#define SLCAN1_PROTO_H

#include <stddef.h>
#include <stdint.h>

/* A line is kept up to SLCAN1_LINE_MAX - 1 characters. A longer one is
 * dropped whole, answered BEL once, and the input resynchronises at the
 * next CR or LF.
 *
 * ONE CHANGE FROM slcan_main.cpp, for T8a: its loop dropped the characters
 * kept so far but then took the rest of an overlong line as a new line, so
 * the tail of garbage ending in, say, "O" opened the port, against its own
 * comment ("resynchronise at the next terminator"). Here the tail is
 * dropped too, as that comment says. */
#define SLCAN1_LINE_MAX 64

/* The port's counters, as the ? line prints them. */
typedef struct {
    uint32_t tx;            /* sends the port queued */
    uint32_t tx_dropped;    /* sends it refused, the queue full or otherwise */
    uint32_t rx;            /* frames received and handed up */
    uint32_t rx_dropped;    /* frames lost on receive */
    uint32_t bus_errors;
    int bus_off;            /* inside a bus-off episode */
} slcan1_port_counts_t;

/* The rest of the image, as the protocol sees it. */
typedef struct {
    /* Bring the port up, listen-only or normal, at 500 kbit/s. 1: up. */
    int (*open)(void *ctx, int listen_only);
    /* Take it down. Called only when open. */
    void (*close)(void *ctx);
    /* Queue one standard-id data frame, never waiting. 1: queued. */
    int (*send)(void *ctx, uint32_t id, const uint8_t *data, uint8_t len);
    void (*counts)(void *ctx, slcan1_port_counts_t *out);
    /* Bytes to the host. */
    void (*write)(void *ctx, const char *s, size_t n);
    void *ctx;
} slcan1_ops_t;

typedef struct {
    const slcan1_ops_t *ops;
    char line[SLCAN1_LINE_MAX];
    uint8_t len;
    int skip;               /* inside an overlong line, until CR or LF */
    int open;              /* the port is up and the image may transmit */
    int listen_only;
    uint32_t accepted;
    uint32_t rejected;
    uint32_t full;
} slcan1_t;

/* Closed, counters zero. Calls nothing in ops. */
void slcan1_init(slcan1_t *s, const slcan1_ops_t *ops);

/* Bytes from the host. Lines end at CR or LF. */
void slcan1_input(slcan1_t *s, const uint8_t *data, size_t n);

int slcan1_is_open(const slcan1_t *s);

#endif
