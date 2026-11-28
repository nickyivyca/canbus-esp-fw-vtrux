/*
 * twai_port_t19_parse -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T19). See
 * twai_port_t19_parse.h. Chip-neutral: no ESP-IDF header.
 */
#include "twai_port_t19_parse.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void say(char *err, size_t errn, const char *msg)
{
    if (errn > 0)
    {
        snprintf(err, errn, "%s", msg);
    }
}

bool t19_parse(const char *line, t19_line_t *out, char *err, size_t errn)
{
    memset(out, 0, sizeof(*out));
    const size_t len = strlen(line);
    if (len > T19_LINE_MAX)
    {
        say(err, errn, "the line is too long; refused rather than cut");
        return false;
    }
    memcpy(out->buf, line, len + 1);

    char *p = out->buf;
    char *tok;
    while (*p == ' ') { p++; }
    if (*p == '\0')
    {
        say(err, errn, "empty line");
        return false;
    }
    out->cmd = p;
    while (*p != '\0' && *p != ' ') { p++; }

    for (;;)
    {
        while (*p == ' ') { *p++ = '\0'; }
        if (*p == '\0')
        {
            return true;
        }
        tok = p;
        while (*p != '\0' && *p != ' ') { p++; }
        if (*p == ' ') { *p++ = '\0'; }
        char *eq = strchr(tok, '=');
        if (eq == NULL || eq == tok)
        {
            say(err, errn, "an argument is not key=value");
            return false;
        }
        if (out->n >= T19_ARGS_MAX)
        {
            say(err, errn, "too many arguments");
            return false;
        }
        *eq = '\0';
        out->key[out->n] = tok;
        out->val[out->n] = eq + 1;
        out->n++;
    }
}

int t19_get(void *ctx, const char *key, char *val, size_t vn)
{
    const t19_line_t *l = (const t19_line_t *)ctx;
    for (int i = 0; i < l->n; i++)
    {
        if (strcmp(l->key[i], key) == 0)
        {
            const size_t len = strlen(l->val[i]);
            if (vn == 0 || len >= vn)
            {
                return -1;
            }
            memcpy(val, l->val[i], len + 1);
            return 1;
        }
    }
    return 0;
}

const char *t19_unknown(const t19_line_t *l, const char *const *allowed)
{
    for (int i = 0; i < l->n; i++)
    {
        bool ok = false;
        for (const char *const *a = allowed; *a != NULL; a++)
        {
            if (strcmp(l->key[i], *a) == 0)
            {
                ok = true;
                break;
            }
        }
        if (!ok)
        {
            return l->key[i];
        }
    }
    return NULL;
}

bool t19_int(const char *s, long lo, long hi, long *out)
{
    char *end = NULL;
    const long v = strtol(s, &end, 10);
    if (end == s || *end != '\0' || v < lo || v > hi)
    {
        return false;
    }
    *out = v;
    return true;
}

bool t19_json_str(char *dst, size_t dn, const char *s)
{
    size_t o = 0;
    if (dn < 3)
    {
        if (dn > 0) { dst[0] = '\0'; }
        return false;
    }
    dst[o++] = '"';
    for (; *s != '\0'; s++)
    {
        const unsigned char c = (unsigned char)*s;
        char tmp[8];
        size_t k;
        if (c == '"' || c == '\\')
        {
            tmp[0] = '\\';
            tmp[1] = (char)c;
            k = 2;
        }
        else if (c < 0x20)
        {
            snprintf(tmp, sizeof(tmp), "\\u%04x", c);
            k = 6;
        }
        else
        {
            tmp[0] = (char)c;
            k = 1;
        }
        if (o + k + 2 > dn)     /* room for the closing quote and the NUL */
        {
            dst[0] = '\0';
            return false;
        }
        memcpy(dst + o, tmp, k);
        o += k;
    }
    dst[o++] = '"';
    dst[o] = '\0';
    return true;
}
