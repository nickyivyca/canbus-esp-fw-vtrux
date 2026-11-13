/*
 * jam_parse.c -- the jammer's command parser and limits (jam_internal.h).
 * TEST EQUIPMENT, NOT FOR VEHICLE (x2_test_images SPEC T8). Pure: no
 * hardware, no RTOS, so the host tests run it as it is.
 *
 * Ported from the PlatformIO jammer (gen-inhibit/tools/x2_jammer/src/
 * main.cpp, handle()). Its refusals, their order and their words are kept.
 * One change: a number is decimal digits only, so "-5" ends the arguments
 * as a letter does, where sscanf's %u took it as 4294967291 (refused as out
 * of range either way); a number past 4294967295 reads as 4294967295.
 */
#include <stdio.h>
#include <string.h>

#include "jam_internal.h"
#include "jam_pulse.h"

static int is_space(char ch)
{
    return ch == ' ' || ch == '\t';
}

static const char *skip_space(const char *s)
{
    while (*s && is_space(*s)) s++;
    return s;
}

/* One token's end. */
static const char *token_end(const char *s)
{
    while (*s && !is_space(*s)) s++;
    return s;
}

/* [s, e) as a number: 1 if it is decimal digits only, 0 otherwise. */
static int read_number(const char *s, const char *e, uint32_t *out)
{
    if (s == e) return 0;
    uint64_t v = 0;
    for (const char *p = s; p < e; p++) {
        if (*p < '0' || *p > '9') return 0;
        if (v <= 0xFFFFFFFFull) v = v * 10 + (uint64_t)(*p - '0');
    }
    *out = v > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)v;
    return 1;
}

uint32_t jam_cmd_ms(const jam_cmd_t *c)
{
    return c->kind == JAM_K_TRAIN ? c->ms : c->n * c->interval_ms + 100;
}

jp_result_t jam_parse(const char *line, bool busy, jam_cmd_t *out,
                      char *note, size_t note_len)
{
    note[0] = 0;
    const char *s = skip_space(line);
    if (!*s) return JP_EMPTY;
    const char *e = token_end(s);
    size_t wl = (size_t)(e - s);
#define WORD_IS(w) (wl == sizeof(w) - 1 && !strncmp(s, w, wl))
    if (WORD_IS("STOP")) return JP_STOP;
    if (WORD_IS("STATUS")) return JP_STATUS;
    int train = WORD_IS("TRAIN"), pulses = WORD_IS("PULSES");
#undef WORD_IS
    if (!train && !pulses) return JP_NOT_MINE;

    /* The arguments: the leading numbers, at most 3; anything after three
     * numbers is one more (sscanf's trailing %c). */
    uint32_t a[3] = {0, 0, 0};
    int got = 0;
    const char *p = skip_space(e);
    while (*p) {
        const char *te = token_end(p);
        if (got == 3) { got = 4; break; }
        if (!read_number(p, te, &a[got])) break;
        got++;
        p = skip_space(te);
    }
    if (got != 3) {
        snprintf(note, note_len, "ERR need_exactly_3_args got=%d", got);
        return JP_REFUSED;
    }
    if (busy) {
        snprintf(note, note_len, "ERR busy");
        return JP_REFUSED;
    }
    memset(out, 0, sizeof *out);
    out->w_us = a[0];
    if (a[0] < JAM_W_MIN_US || a[0] > JAM_W_MAX_US) {
        snprintf(note, note_len, "ERR w_us_out_of_range %lu",
                 (unsigned long)a[0]);
        return JP_REFUSED;
    }
    if (train) {
        out->kind = JAM_K_TRAIN;
        out->p_us = a[1];
        out->ms = a[2];
        if ((uint64_t)a[1] < 4ull * a[0] || a[1] > JAM_P_MAX_US) {
            snprintf(note, note_len, "ERR p_us_out_of_range %lu",
                     (unsigned long)a[1]);
            return JP_REFUSED;
        }
        if (a[2] < 1) {
            snprintf(note, note_len, "ERR ms_zero");
            return JP_REFUSED;
        }
        if (a[2] > JAM_MS_MAX) {
            snprintf(note, note_len, "CAPPED ms %lu -> %lu",
                     (unsigned long)a[2], (unsigned long)JAM_MS_MAX);
            out->ms = JAM_MS_MAX;
        }
    } else {
        out->kind = JAM_K_PULSES;
        out->n = a[1];
        out->interval_ms = a[2];
        if (a[1] < 1 || a[1] > JAM_N_MAX || a[2] < 1
                || a[2] > JAM_INTERVAL_MAX_MS
                || (uint64_t)a[1] * a[2] + 100 > JAM_MS_MAX) {
            snprintf(note, note_len,
                     "ERR pulses_out_of_range n=%lu interval_ms=%lu",
                     (unsigned long)a[1], (unsigned long)a[2]);
            return JP_REFUSED;
        }
    }
    return JP_CMD;
}
