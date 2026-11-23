/*
 * shared/diag: the frame packer (SPEC.md D3). See include/diag_pack.h.
 *
 * Bit numbering is the DBC's. Intel (@1): the start bit is the value's least
 * significant bit, and bit n is bit n % 8 of byte n / 8, counting up. Motorola
 * (@0): the start bit is the value's most significant bit, and the next bit
 * down is the next lower bit of the same byte, or bit 7 of the next byte after
 * bit 0 (the DBC's "sawtooth" order). This is the order cantools and
 * python/diag_pack.py use.
 */
#include "diag_pack.h"

#include <stddef.h>
#include <string.h>

static bool length_ok(const diag_signal_t *s)
{
    return s->length >= 1 && s->length <= 64;
}

int64_t diag_signal_min(const diag_signal_t *s)
{
    if (!length_ok(s) || !s->is_signed)
    {
        return 0;
    }
    if (s->length == 64)
    {
        return INT64_MIN;
    }
    return -((int64_t)1 << (s->length - 1));
}

int64_t diag_signal_max(const diag_signal_t *s)
{
    if (!length_ok(s))
    {
        return 0;
    }
    if (s->is_signed)
    {
        return s->length == 64 ? INT64_MAX : ((int64_t)1 << (s->length - 1)) - 1;
    }
    return s->length >= 63 ? INT64_MAX : ((int64_t)1 << s->length) - 1;
}

/* The bit after `pos` in the signal's order, from its first written bit. */
static unsigned next_pos(const diag_signal_t *s, unsigned pos)
{
    if (!s->big_endian)
    {
        return pos + 1;
    }
    return (pos % 8 == 0) ? pos + 15 : pos - 1;
}

/* True when every bit of the signal lies inside dlc bytes. */
static bool inside(const diag_signal_t *s, uint8_t dlc)
{
    if (!length_ok(s) || dlc > DIAG_MAX_DLC)
    {
        return false;
    }
    unsigned pos = s->start;
    for (unsigned i = 0; i < s->length; i++)
    {
        if (pos >= (unsigned)dlc * 8)
        {
            return false;
        }
        pos = next_pos(s, pos);
    }
    return true;
}

diag_pack_result_t diag_pack_signal(const diag_signal_t *s, int64_t raw,
                                    uint8_t *data, uint8_t dlc)
{
    if (s == NULL || data == NULL || !inside(s, dlc))
    {
        return DIAG_PACK_BAD_SIGNAL;
    }
    if (raw < diag_signal_min(s) || raw > diag_signal_max(s))
    {
        return DIAG_PACK_MISFIT;
    }

    const uint64_t u = (uint64_t)raw;   /* two's complement for a signed value */
    unsigned pos = s->start;
    for (unsigned n = 0; n < s->length; n++)
    {
        /* Intel writes the value from its LSB up, Motorola from its MSB down. */
        const unsigned bit = s->big_endian ? (s->length - 1 - n) : n;
        const uint8_t mask = (uint8_t)(1u << (pos % 8));
        if ((u >> bit) & 1u)
        {
            data[pos / 8] |= mask;
        }
        else
        {
            data[pos / 8] &= (uint8_t)~mask;
        }
        pos = next_pos(s, pos);
    }
    return DIAG_PACK_OK;
}

static bool message_ok(const diag_message_t *m, int *selector)
{
    *selector = -1;
    if (m->dlc > DIAG_MAX_DLC || m->nsignals > 64 || (m->nsignals && m->signals == NULL))
    {
        return false;
    }
    bool muxed = false;
    for (unsigned i = 0; i < m->nsignals; i++)
    {
        const diag_signal_t *s = &m->signals[i];
        if (!inside(s, m->dlc))
        {
            return false;
        }
        if (s->mux == DIAG_MUX_SELECTOR)
        {
            if (*selector >= 0)
            {
                return false;   /* two selectors */
            }
            *selector = (int)i;
        }
        else if (s->mux >= 0)
        {
            muxed = true;
        }
        else if (s->mux != DIAG_MUX_NONE)
        {
            return false;
        }
    }
    return !(muxed && *selector < 0);
}

int diag_pack_message(const diag_message_t *m, const int64_t *raw,
                      uint8_t *out, uint64_t *misfits)
{
    if (misfits != NULL)
    {
        *misfits = 0;
    }
    if (m == NULL || out == NULL)
    {
        return -1;
    }
    memset(out, 0, m->dlc <= DIAG_MAX_DLC ? m->dlc : DIAG_MAX_DLC);
    int sel = -1;
    if (!message_ok(m, &sel) || (m->nsignals && raw == NULL))
    {
        return -1;
    }

    int64_t sel_value = 0;
    if (sel >= 0)
    {
        sel_value = raw[sel];
        if (diag_pack_signal(&m->signals[sel], sel_value, out, m->dlc) != DIAG_PACK_OK)
        {
            memset(out, 0, m->dlc);
            return -1;      /* a selector that does not fit picks nothing */
        }
    }

    int n = 0;
    for (unsigned i = 0; i < m->nsignals; i++)
    {
        const diag_signal_t *s = &m->signals[i];
        if ((int)i == sel || (s->mux >= 0 && s->mux != sel_value))
        {
            continue;
        }
        if (diag_pack_signal(s, raw[i], out, m->dlc) == DIAG_PACK_MISFIT)
        {
            n++;
            if (misfits != NULL)
            {
                *misfits |= (uint64_t)1 << i;
            }
        }
    }
    return n;
}
