/*
 * twai_port_t19 -- MEASUREMENT-ONLY. Never in a release image.
 * vtrux/twai_port's T19 app (SPEC.md T19): the S3 measurement of T8, T12,
 * T14-T18 and T13's no-acknowledge modes, started by serial commands, since
 * the X2 has no URI hook. Compiled in only when the build sets both
 * TWAI_PORT_TXABORT_PROBE and TWAI_PORT_S3_PROBE on the S3 (CMakeLists.txt);
 * the build-switch check refuses a release image with them (shared/tools
 * R20). The commands and their answers are in test_app/README.md.
 *
 * The project (the charge interposer's s3probe variant) gives the serial
 * line in and out, and the G4 record, which twai_port cannot read itself
 * (shared/restart_guard depends on the port's stop, not the other way).
 * It runs every command on one task, on the core it wants the driver's
 * interrupt on: the driver is installed from that task.
 */
#ifndef TWAI_PORT_T19_H
#define TWAI_PORT_T19_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef bool (*twai_port_t19_emit_fn)(void *ctx, const char *s, size_t n);

typedef struct {
    int board_port;                 /* the board's CAN port that is the TWAI */
    twai_port_t19_emit_fn emit;     /* the serial output, in pieces */
    void *emit_ctx;
    void (*flush)(void);            /* everything emitted sent: before a restart */
    /*
     * The G4 record (shared/restart_guard) as one JSON value into `buf`:
     * an object, or null when no valid record. False if it did not fit.
     * NULL: `g4` answers "g4":null.
     */
    bool (*restart_record)(char *buf, size_t n);
} twai_port_t19_cfg_t;

/*
 * Set the port up for the measurement: twai_port with the kept-driver switch
 * (T14) on `board_port`, registered as can_port's port 0 (the stock CAN calls
 * the probe drives, can_port C3), at 500 kbit/s. Nothing is installed: the
 * first command that needs the bus brings it up.
 */
esp_err_t twai_port_t19_init(const twai_port_t19_cfg_t *cfg);

/*
 * One command line, without its line end; `too_long`: the project cut it.
 * Answers with one JSON line through `emit`.
 */
void twai_port_t19_line(const char *line, bool too_long);

#ifdef __cplusplus
}
#endif

#endif /* TWAI_PORT_T19_H */
