/*
 * jam_frame.h -- the jam command channel of slcan_spi: a reserved CAN frame
 * with a signature, sent by the host through the SLCAN, which the image acts
 * on and never puts on a bus (x2_test_images SPEC T13's fallback; the X2
 * cannot present a second USB serial port without taking the USB pins from
 * the Serial/JTAG that flashes it). NOT FOR VEHICLE (SPEC T14).
 *
 * No ESP-IDF here: the host unit tests (../test/) build it as it is.
 *
 * The command frame: extended identifier JAM_CMD_ID, DLC 8, data
 *   byte 0..1  the signature, 'J' 'M'
 *   byte 2     the command: 'T' TRAIN, 'P' PULSES, 'S' STOP, '?' STATUS
 *   TRAIN      byte 3 w_us, bytes 4..5 p_us, bytes 6..7 ms         (big-endian)
 *   PULSES     byte 3 w_us, bytes 4..5 n,    bytes 6..7 interval_ms (big-endian)
 *   STOP, STATUS  bytes 3..7 zero
 * It becomes the jammer's own text command ("TRAIN <w> <p> <ms>", ...) for
 * jam_pulse, unchanged (SPEC T13), whose limits decide what runs.
 *
 * The replies (user, 2026-11-13, relayed by the charge-interposer reviewer):
 * frames on JAM_REPLY_ID in the host's receive stream, never put on a bus.
 * Each of jam_pulse's reply lines (ACK, PULSE, DONE, ERR, CAPPED, STATUS),
 * text as jam_pulse writes it (jam_pulse stays unchanged, SPEC T13), goes
 * out in order as one or more frames, which the host joins back:
 *   byte 0..1  the signature, 'J' 'M', so a frame on the identifier
 *              without it is never taken for a reply
 *   byte 2     the frame's index in the line, 0 first, with 0x80 set on
 *              the line's last frame
 *   bytes 3..  up to 5 characters of the line; DLC 3 + their count
 * A reply line of JAM_REPLY_MAX - 1 characters takes 32 frames. One line's
 * frames go to the host together, never interleaved with another's.
 * A frame from the host on the reply identifier is refused (ERR).
 *
 * Both reserved identifiers have J1939's EDP and DP bits set, which J1939
 * leaves to ISO 15765-3, and which no bench or truck bus uses (no Coda or
 * Vtrux DBC has one). A frame from
 * the host on either one is the image's own: it is never queued for CAN2,
 * whether or not it decodes.
 */
#ifndef JAM_FRAME_H
#define JAM_FRAME_H

#include <stddef.h>
#include "can_frame.h"

#define JAM_CMD_ID      0x1F4A4D43u     /* "JMC" */
#define JAM_REPLY_ID    0x1F4A4D52u     /* "JMR" */
#define JAM_SIG0        'J'
#define JAM_SIG1        'M'

/* Characters per reply frame, and the most frames one reply line takes
 * (jam_pulse's JAM_REPLY_MAX, 160, NUL included). */
#define JAM_REPLY_CHARS     5
#define JAM_REPLY_LAST      0x80u
#define JAM_REPLY_FRAMES_MAX 32

/* The longest jam text command, NUL included. */
#define JAM_CMD_TEXT_MAX 40

typedef enum {
    JAM_FRAME_NOT_JAM = 0,  /* not a reserved identifier: ordinary traffic */
    JAM_FRAME_CMD,          /* a jam command: text holds it */
    JAM_FRAME_BAD,          /* a reserved identifier that does not decode */
} jam_frame_kind_t;

/* Is f on a reserved identifier (the image's own, never sent)? */
int jam_frame_reserved(const can_frame_t *f);

/* Decodes a frame from the host. text must hold JAM_CMD_TEXT_MAX bytes. */
jam_frame_kind_t jam_frame_decode(const can_frame_t *f, char *text);

/* Splits one reply line (no line end) into frames on JAM_REPLY_ID; returns
 * how many, at most max. A line too long for max frames is cut; an empty
 * line is one frame with DLC 3. */
size_t jam_frame_reply(const char *line, can_frame_t *frames, size_t max);

#endif
