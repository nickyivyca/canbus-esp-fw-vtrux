/*
 * twai_port_t19_parse -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T19).
 * Private to test_app/.
 *
 * The T19 app's serial command line, chip-neutral so the host tests build it
 * (test_app/test/test_t19_parse.c):
 *
 *   <command> [<key>=<value> ...]
 *
 * separated by spaces, at most T19_LINE_MAX characters. An argument is looked
 * up as the URI's query arguments are (twai_port_txabort_run.h): 1 found,
 * 0 absent, -1 present but unusable (its value does not fit), and the first
 * of two with the same key is the one found. Nothing is guessed: a token with
 * no `=`, an empty key, too many arguments, a key a command does not take
 * and a number out of range are each refused.
 */
#ifndef TWAI_PORT_T19_PARSE_H
#define TWAI_PORT_T19_PARSE_H

#include <stdbool.h>
#include <stddef.h>

#define T19_LINE_MAX    255
#define T19_ARGS_MAX    16

typedef struct {
    char        buf[T19_LINE_MAX + 1];
    const char *cmd;
    int         n;
    const char *key[T19_ARGS_MAX];
    const char *val[T19_ARGS_MAX];
} t19_line_t;

/*
 * Split `line`. True with `out` filled; false with the reason in `err`: an
 * empty line, a line over T19_LINE_MAX, too many arguments, or a token that
 * is not key=value.
 */
bool t19_parse(const char *line, t19_line_t *out, char *err, size_t errn);

/* One argument, as txp_get_fn: `ctx` is the t19_line_t. */
int t19_get(void *ctx, const char *key, char *val, size_t vn);

/* The first key not in `allowed` (NULL-terminated), or NULL. */
const char *t19_unknown(const t19_line_t *l, const char *const *allowed);

/* A whole number in [lo, hi]: false for anything else. */
bool t19_int(const char *s, long lo, long hi, long *out);

/*
 * `s` as a JSON string, quotes included, into `dst`: `"` and `\` escaped,
 * control characters as \u00XX. Returns false, with `dst` empty, if it does
 * not fit.
 */
bool t19_json_str(char *dst, size_t dn, const char *s);

#endif /* TWAI_PORT_T19_PARSE_H */
