/*
 * shared/diag: the frame packer (SPEC.md D3).
 *
 * A project's frame table is generated from its published DBC by
 * tools/diag_table_gen.py; there is no hand-kept layout. The packer fills a
 * frame from that table: start bit, length, byte order and signedness as the
 * DBC gives them. It takes raw values, the integers on the wire, before any
 * DBC scale and offset.
 *
 * A value that does not fit its signal is never truncated: the packer leaves
 * that signal's bits 0 and reports it, and the project's spec says what
 * follows.
 *
 * Pure C with no ESP-IDF calls, so a host build compiles the same file. The
 * Python twin is python/diag_pack.py (D4).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* diag_signal_t.mux: not multiplexed, or the message's multiplexer. A value
 * of 0 or more is the multiplexer value the signal is present under. */
#define DIAG_MUX_NONE     (-1)
#define DIAG_MUX_SELECTOR (-2)

/* The longest frame a table may describe: classic CAN. */
#define DIAG_MAX_DLC 8

typedef struct {
    const char *name;   /* the DBC's signal name */
    uint16_t start;     /* the DBC's start bit, as written */
    uint8_t length;     /* bits, 1 to 64 */
    bool big_endian;    /* @0 (Motorola); false is @1 (Intel) */
    bool is_signed;     /* '-' in the DBC */
    int16_t mux;        /* DIAG_MUX_NONE, DIAG_MUX_SELECTOR, or a mux value */
} diag_signal_t;

typedef struct {
    const char *name;   /* the DBC's message name */
    uint32_t id;
    bool extended;
    uint8_t dlc;
    uint8_t nsignals;   /* at most 64 */
    const diag_signal_t *signals;
} diag_message_t;

typedef enum {
    DIAG_PACK_OK = 0,
    DIAG_PACK_MISFIT,       /* the value does not fit; its bits stay 0 */
    DIAG_PACK_BAD_SIGNAL,   /* the table entry is not packable: nothing written */
} diag_pack_result_t;

/* The raw range a signal holds. An unsigned 64-bit signal takes 0 to
 * INT64_MAX here, the most an int64_t holds. */
int64_t diag_signal_min(const diag_signal_t *s);
int64_t diag_signal_max(const diag_signal_t *s);

/*
 * Packs one raw value into data[0, dlc). Only the signal's own bits are
 * written. A misfit, or a signal that does not lie inside dlc bytes, writes
 * nothing.
 */
diag_pack_result_t diag_pack_signal(const diag_signal_t *s, int64_t raw,
                                    uint8_t *data, uint8_t dlc);

/*
 * Fills out[0, m->dlc) from raw[0, m->nsignals), one raw value per signal in
 * the table's order. out is zeroed first. In a multiplexed message the
 * selector's value picks the multiplexed signals packed; the others' raw
 * values are ignored.
 *
 * Returns the number of signals that did not fit, each left 0, with bit i of
 * *misfits (if not NULL) set for signal i; or -1, with out zeroed, when the
 * table entry is not packable (dlc over DIAG_MAX_DLC, over 64 signals, a
 * signal outside the frame, a multiplexed signal without a selector, or a
 * selector that does not fit).
 */
int diag_pack_message(const diag_message_t *m, const int64_t *raw,
                      uint8_t *out, uint64_t *misfits);

#ifdef __cplusplus
}
#endif
