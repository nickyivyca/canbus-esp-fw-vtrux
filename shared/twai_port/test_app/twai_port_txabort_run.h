/*
 * twai_port_txabort_run -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T13,
 * T19). Private to test_app/.
 *
 * The transmit-abort probe with its transport taken out: the arguments come
 * in through a getter and the answer goes out through an emitter, so the same
 * parse, run and answer serve the URI (twai_port_txabort_http.c, POST
 * /gi_txabort, T13) and the X2's serial commands (T19, which has no URI
 * hook). The arguments, the refusals and the answer are the URI's, unchanged
 * (test_app/README.md): this split moves code and changes none of them
 * (charge-interposer reviewer, 2026-11-27).
 */
#ifndef TWAI_PORT_TXABORT_RUN_H
#define TWAI_PORT_TXABORT_RUN_H

#include <stdbool.h>
#include <stddef.h>

#include "twai_port_unacked.h"      /* txp_get_fn, txp_emit_fn */

/* The refusal body while the project owns the bus, when it gives none. */
#define TXAB_OWNED_BODY "the project owns the bus; release it first"

/* The size of the refusal message buffer the caller passes: the longest
 * refusal (an unusable argument) needs 256. */
#define TXAB_ERR_MAX    256

typedef struct {
    bool        have_query; /* any argument was given: `query_given` */
    txp_get_fn  get;        /* one argument: 1 found, 0 absent, -1 unusable */
    void       *get_ctx;
    txp_emit_fn emit;       /* the answer, in pieces */
    void       *emit_ctx;
    bool      (*owns_bus)(void);    /* NULL: every run is refused */
    const char *owned_body;         /* NULL: TXAB_OWNED_BODY */
} txab_io_t;

/* What twai_port_txabort_run() returns: the URI's status codes. */
enum {
    TXAB_EMIT_FAILED = 0,   /* the answer was cut off: an emit failed or a
                             * piece did not fit, after something was sent */
    TXAB_OK = 200,          /* answered in full */
    TXAB_BAD = 400,         /* refused, `err` says why; nothing emitted */
    TXAB_FAILED = 500,      /* ditto */
    TXAB_UNSUPPORTED = 501, /* ditto */
};

/*
 * Parse, refuse or run, and answer: every phase, abort, control, noabort and
 * m1-m3. Runs synchronously at priority 18 on the calling task, so on a chip
 * with two cores the caller runs it on the core the driver was installed on,
 * where the driver's interrupt runs and the probe's critical sections mask
 * it. `err` takes TXAB_ERR_MAX bytes.
 */
int twai_port_txabort_run(const txab_io_t *io, char *err, size_t errn);

#endif /* TWAI_PORT_TXABORT_RUN_H */
