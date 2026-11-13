/*
 * jam_pulse.h -- the X2 jammer's pulse engine. TEST EQUIPMENT, NOT FOR
 * VEHICLE (x2_test_images SPEC T8, T13).
 *
 * Puts short dominant pulses on a CAN transceiver's TXD line from the RMT
 * peripheral, on command, so a bench can make error frames on demand. It
 * never sends a CAN frame: it knows nothing of the TWAI controller, which
 * it never starts, and nothing of SPI.
 *
 * Transport-free: commands come in as text lines through jam_line(), and
 * every reply goes out through the caller's reply callback as one line of
 * text without a prefix or a line end. The caller adds its own prefix,
 * which says NOT FOR VEHICLE (jammer/main adds "JAMMER [NOT FOR VEHICLE] "),
 * so slcan_spi can link this component unchanged (SPEC T13).
 *
 * Safety design (jammer/README.md has the reasoning):
 *  - jam_drive_recessive() drives the TXD pin high (recessive); the caller
 *    calls it first, before anything that can take time.
 *  - Jams only on an explicit command, and every command carries a
 *    duration, capped at JAM_MS_MAX. Nothing extends a deadline.
 *  - Pulses only: W is JAM_W_MIN_US..JAM_W_MAX_US and the period is at
 *    least 4 W. The RMT output is inverted, so its idle and
 *    end-of-transmission level (internal 0) is recessive on the pin.
 *  - Two independent stops at the deadline:
 *      (a) a one-shot esp_timer disables the RMT channel;
 *      (b) the RMT only ever runs finite chunks (<= JAM_CHUNK_US), each
 *          sized not to cross the deadline, and the jam task re-arms a
 *          chunk only while now < deadline.
 *    Two test-only builds show each stop alone (never pinnable):
 *      JAM_TEST_STOP_A_ONLY: (b) sizes its chunks to the deadline plus
 *          JAM_BACKSTOP_US, so only (a) can end a train at the deadline;
 *      JAM_TEST_STOP_B_ONLY: the esp_timer is never started.
 *
 * Commands (the first word is case-sensitive):
 *   TRAIN <w_us> <p_us> <ms>        periodic pulses for ms milliseconds
 *   PULSES <w_us> <n> <interval_ms> n single pulses, interval_ms apart
 *   STOP                            stop now
 *   STATUS                          state line
 */
#ifndef JAM_PULSE_H
#define JAM_PULSE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define JAM_W_MIN_US        12u     /* > 5 bit times at 500 kbit/s */
#define JAM_W_MAX_US        50u     /* far below any TXD dominant timeout */
#define JAM_P_MAX_US        20000u  /* the RMT duration field is 15 bits */
#define JAM_MS_MAX          30000u  /* hard cap on any command */
#define JAM_N_MAX           1000u
#define JAM_INTERVAL_MAX_MS 10000u
#define JAM_CHUNK_US        200000u /* layer (b) granularity */
#define JAM_LOOPS_MAX       1000u   /* S3 RMT: <= 1023 loops per batch */
#define JAM_BACKSTOP_US     2000000 /* JAM_TEST_STOP_A_ONLY's layer (b) */
#define JAM_REPLY_MAX       160     /* longest reply line, NUL included */

/* One reply line: no prefix, no line end. Called from the caller's line
 * task and from the jam task, so it must be safe from both. */
typedef void (*jam_reply_fn)(const char *line, void *ctx);

typedef struct {
    gpio_num_t   tx_gpio;    /* the transceiver's TXD */
    jam_reply_fn reply;
    void        *ctx;
    int          core;       /* the jam task's core */
    int          priority;   /* the jam task's priority */
} jam_config_t;

/* Drives the pin high (recessive) as an output, level first. */
void jam_drive_recessive(gpio_num_t gpio);

/* Creates the engine: its encoder, deadline timer, queue and task. The pin
 * is driven recessive again. */
esp_err_t jam_init(const jam_config_t *cfg);

typedef enum {
    JAM_LINE_DONE = 0,      /* the line was this engine's (or empty) */
    JAM_LINE_NOT_MINE,      /* unknown first word: the caller replies */
} jam_line_t;

/* Handles one command line (no line end). Replies through the callback. */
jam_line_t jam_line(const char *line);

bool jam_busy(void);

/* The limits as one line, for a banner. */
void jam_limits_text(char *buf, size_t n);

/* Which stops this build has, for a banner: "a+b", "a_only(b at
 * deadline+2000ms)" or "b_only". */
const char *jam_stops_text(void);

#ifdef __cplusplus
}
#endif

#endif
