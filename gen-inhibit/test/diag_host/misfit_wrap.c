/*
 * The packer's misfit report, taken at the packer: see misfit_wrap.h.
 * Gen-inhibit tester, 2026-11-23.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include "diag_pack.h"
#include "misfit_wrap.h"

int __real_diag_pack_message(const diag_message_t *m, const int64_t *raw,
                             uint8_t *out, uint64_t *misfits);
int __wrap_diag_pack_message(const diag_message_t *m, const int64_t *raw,
                             uint8_t *out, uint64_t *misfits);

#define RING 256u

static gi_misfit_t g_ring[RING];
static unsigned g_head;         /* index of the oldest record not taken */
static unsigned g_n;            /* records not taken (at most RING) */
static unsigned long g_calls, g_total, g_lost;

static void at_exit(void)
{
    if (g_n == 0 && g_lost == 0)
        return;
    gi_misfit_t r;
    char buf[200];
    while (gi_misfit_take(&r))
    {
        gi_misfit_format(&r, buf, sizeof buf);
        fprintf(stderr, "!! %s (not taken by the program)\n", buf);
    }
    if (g_lost)
        fprintf(stderr, "!! DIAG MISFIT records lost: %lu (ring full)\n", g_lost);
    fflush(stderr);
    _exit(3);
}

__attribute__((constructor)) static void register_at_exit(void)
{
    atexit(at_exit);
}

int __wrap_diag_pack_message(const diag_message_t *m, const int64_t *raw,
                             uint8_t *out, uint64_t *misfits)
{
    uint64_t mask = 0;
    int r = __real_diag_pack_message(m, raw, out, &mask);
    g_calls++;
    if (misfits)
        *misfits = mask;
    if (r != 0)
    {
        gi_misfit_t rec = { m ? m->id : 0u, r, mask, NULL, 0 };
        if (r > 0 && m)
        {
            for (unsigned i = 0; i < m->nsignals && i < 64u; i++)
            {
                if (mask & (UINT64_C(1) << i))
                {
                    rec.first = m->signals[i].name;
                    rec.first_raw = raw ? raw[i] : 0;
                    break;
                }
            }
        }
        g_total++;
        if (g_n < RING)
        {
            g_ring[(g_head + g_n) % RING] = rec;
            g_n++;
        }
        else
        {
            g_lost++;
        }
    }
    return r;
}

unsigned long gi_misfit_calls(void) { return g_calls; }
unsigned long gi_misfit_total(void) { return g_total; }

int gi_misfit_take(gi_misfit_t *out)
{
    if (g_n == 0)
        return 0;
    *out = g_ring[g_head];
    g_head = (g_head + 1u) % RING;
    g_n--;
    return 1;
}

void gi_misfit_format(const gi_misfit_t *r, char *buf, unsigned len)
{
    if (r->result < 0)
        snprintf(buf, len, "DIAG UNPACKABLE id=%03" PRIX32 " result=%d", r->id, r->result);
    else
        snprintf(buf, len, "DIAG MISFIT id=%03" PRIX32 " count=%d mask=%016" PRIX64
                 " first=%s raw=%" PRId64, r->id, r->result, r->mask,
                 r->first ? r->first : "?", r->first_raw);
}
