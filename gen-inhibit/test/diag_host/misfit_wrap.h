/*
 * The packer's misfit report, taken at the packer (spec 10, 12.3 item 2).
 *
 * The core calls shared/diag's diag_pack_message() and discards what it
 * returns, as spec 10 says the device does. A host build linked with
 * misfit_wrap.c and -Wl,--wrap=diag_pack_message routes every call through
 * __wrap_diag_pack_message(), which calls the real packer, records each call
 * that reported a misfit (or refused its table entry), and hands the caller
 * the result unchanged.
 *
 * A record not taken by the program when it exits is printed to stderr and
 * the process exits 3, so a host build that never asks still fails on a
 * misfit. Gen-inhibit tester, 2026-11-23.
 */
#pragma once

#include <stdint.h>

typedef struct {
    uint32_t id;            /* the message's frame id */
    int result;             /* the packer's return: misfit count, or -1 */
    uint64_t mask;          /* bit i: signal i did not fit */
    const char *first;      /* the first misfit signal's name, or NULL */
    int64_t first_raw;      /* the raw value it was given */
} gi_misfit_t;

/* Calls of diag_pack_message() seen, misfit or not. */
unsigned long gi_misfit_calls(void);

/* Records made, taken or not. */
unsigned long gi_misfit_total(void);

/* Takes the oldest record not yet taken into *out; 1 if there was one, else 0. */
int gi_misfit_take(gi_misfit_t *out);

/* Formats a record as one line, "DIAG MISFIT id=... count=... mask=... first=... raw=..."
 * (or "DIAG UNPACKABLE id=..." for -1), without a newline. */
void gi_misfit_format(const gi_misfit_t *r, char *buf, unsigned len);
