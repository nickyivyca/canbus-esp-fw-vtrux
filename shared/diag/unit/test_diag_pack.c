/*
 * shared/diag's unit tests of the C packer (diag_pack.c) and the build id's
 * packing (diag_build_id.h). Self-checks, not verification. The expected
 * bytes are cantools 41.3.1's encode of the same layout (scaling off); the
 * same vectors are in test_diag_pack.py for the Python twin.
 */
#include "diag_pack.h"
#include "diag_build_id.h"

#include <stdio.h>
#include <string.h>

static int failures;

#define CHECK(cond) do { if (!(cond)) { failures++; \
    printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

/* T1: Intel and Motorola, unsigned and signed, in one frame. */
static const diag_signal_t t1_sigs[] = {
    { "a", 0, 8, false, false, DIAG_MUX_NONE },
    { "b", 8, 16, false, false, DIAG_MUX_NONE },
    { "c", 24, 12, false, true, DIAG_MUX_NONE },
    { "d", 47, 12, true, false, DIAG_MUX_NONE },
    { "e", 51, 9, true, true, DIAG_MUX_NONE },
};
static const diag_message_t t1 = { "T1", 1, false, 8, 5, t1_sigs };

/* M: a selector and two signals on the same bits under different values. */
static const diag_signal_t m_sigs[] = {
    { "sel", 0, 8, false, false, DIAG_MUX_SELECTOR },
    { "x", 8, 8, false, false, 1 },
    { "y", 8, 16, false, false, 2 },
};
static const diag_message_t mux = { "M", 2, false, 3, 3, m_sigs };

static void hexeq(const uint8_t *got, const char *want_hex, int n, int line)
{
    char buf[2 * DIAG_MAX_DLC + 1];
    for (int i = 0; i < n; i++)
    {
        snprintf(buf + 2 * i, 3, "%02x", got[i]);
    }
    if (strcmp(buf, want_hex) != 0)
    {
        failures++;
        printf("FAIL line %d: got %s want %s\n", line, buf, want_hex);
    }
}

static void test_layouts(void)
{
    uint8_t out[8];
    uint64_t mis = 99;
    int64_t v1[] = { 0x5A, 0x1234, -2, 0xABC, -3 };
    CHECK(diag_pack_message(&t1, v1, out, &mis) == 0 && mis == 0);
    hexeq(out, "5a3412fe0fabcfe8", 8, __LINE__);

    int64_t vmax[] = { 0xFF, 0xFFFF, 2047, 4095, 255 };
    CHECK(diag_pack_message(&t1, vmax, out, &mis) == 0);
    hexeq(out, "ffffffff07fff7f8", 8, __LINE__);

    int64_t vmin[] = { 0, 0, -2048, 0, -256 };
    CHECK(diag_pack_message(&t1, vmin, out, &mis) == 0);
    hexeq(out, "0000000008000800", 8, __LINE__);
}

static void test_misfits(void)
{
    uint8_t out[8];
    uint64_t mis = 0;
    /* a over, c under, e over: each left 0, reported; the rest packed. */
    int64_t v[] = { 256, 0x1234, -2049, 0xABC, 256 };
    CHECK(diag_pack_message(&t1, v, out, &mis) == 3);
    CHECK(mis == ((1u << 0) | (1u << 2) | (1u << 4)));
    hexeq(out, "0034120000abc000", 8, __LINE__);

    int64_t neg[] = { -1, 0, 0, 0, 0 };   /* unsigned takes no negative */
    CHECK(diag_pack_message(&t1, neg, out, &mis) == 1 && mis == 1);

    /* Packing one signal over set bits: a misfit writes nothing. */
    uint8_t d[8];
    memset(d, 0xAA, sizeof d);
    CHECK(diag_pack_signal(&t1_sigs[0], 300, d, 8) == DIAG_PACK_MISFIT);
    CHECK(d[0] == 0xAA);
    CHECK(diag_pack_signal(&t1_sigs[0], 0x0F, d, 8) == DIAG_PACK_OK);
    CHECK(d[0] == 0x0F && d[1] == 0xAA);   /* only its own bits */
}

static void test_ranges(void)
{
    const diag_signal_t u64 = { "u", 0, 64, false, false, DIAG_MUX_NONE };
    const diag_signal_t s64 = { "s", 0, 64, false, true, DIAG_MUX_NONE };
    const diag_signal_t s1 = { "s1", 0, 1, false, true, DIAG_MUX_NONE };
    CHECK(diag_signal_max(&u64) == INT64_MAX && diag_signal_min(&u64) == 0);
    CHECK(diag_signal_max(&s64) == INT64_MAX && diag_signal_min(&s64) == INT64_MIN);
    CHECK(diag_signal_max(&s1) == 0 && diag_signal_min(&s1) == -1);
    uint8_t d[8] = { 0 };
    CHECK(diag_pack_signal(&s64, INT64_MIN, d, 8) == DIAG_PACK_OK);
    hexeq(d, "0000000000000080", 8, __LINE__);
    CHECK(diag_pack_signal(&u64, INT64_MAX, d, 8) == DIAG_PACK_OK);
    hexeq(d, "ffffffffffffff7f", 8, __LINE__);
}

static void test_mux(void)
{
    uint8_t out[3];
    uint64_t mis = 0;
    int64_t v1[] = { 1, 7, 999 };
    CHECK(diag_pack_message(&mux, v1, out, &mis) == 0);
    hexeq(out, "010700", 3, __LINE__);
    int64_t v2[] = { 2, 7, 999 };
    CHECK(diag_pack_message(&mux, v2, out, &mis) == 0);
    hexeq(out, "02e703", 3, __LINE__);
    int64_t v5[] = { 5, 7, 999 };          /* no signal under 5: only the selector */
    CHECK(diag_pack_message(&mux, v5, out, &mis) == 0);
    hexeq(out, "050000", 3, __LINE__);
    int64_t vx[] = { 1, 300, 0 };          /* a present signal's misfit */
    CHECK(diag_pack_message(&mux, vx, out, &mis) == 1 && mis == 2);
    int64_t vbad[] = { 256, 1, 1 };        /* a selector that does not fit */
    memset(out, 0xEE, sizeof out);
    CHECK(diag_pack_message(&mux, vbad, out, &mis) == -1 && mis == 0);
    hexeq(out, "000000", 3, __LINE__);
}

static void test_bad_tables(void)
{
    uint8_t out[8];
    int64_t v[2] = { 0, 0 };
    const diag_signal_t past[] = { { "p", 60, 8, false, false, DIAG_MUX_NONE } };
    const diag_message_t m_past = { "P", 3, false, 8, 1, past };
    CHECK(diag_pack_message(&m_past, v, out, NULL) == -1);

    const diag_signal_t bepast[] = { { "q", 3, 8, true, false, DIAG_MUX_NONE } };
    const diag_message_t m_bepast = { "Q", 4, false, 1, 1, bepast };   /* runs into byte 1 */
    CHECK(diag_pack_message(&m_bepast, v, out, NULL) == -1);

    const diag_signal_t zero[] = { { "z", 0, 0, false, false, DIAG_MUX_NONE } };
    const diag_message_t m_zero = { "Z", 5, false, 8, 1, zero };
    CHECK(diag_pack_message(&m_zero, v, out, NULL) == -1);

    const diag_signal_t orphan[] = { { "o", 0, 8, false, false, 1 } };
    const diag_message_t m_orphan = { "O", 6, false, 8, 1, orphan };   /* muxed, no selector */
    CHECK(diag_pack_message(&m_orphan, v, out, NULL) == -1);

    const diag_message_t m_dlc = { "D", 7, false, 9, 0, NULL };
    CHECK(diag_pack_message(&m_dlc, v, out, NULL) == -1);

    const diag_signal_t two[] = { { "s", 0, 4, false, false, DIAG_MUX_SELECTOR },
                                  { "t", 4, 4, false, false, DIAG_MUX_SELECTOR } };
    const diag_message_t m_two = { "T", 8, false, 1, 2, two };
    CHECK(diag_pack_message(&m_two, v, out, NULL) == -1);
}

static void test_build_id(void)
{
    const uint8_t sha[32] = { 0xde, 0xad, 0xbe, 0xef, 0x01 };
    const uint32_t id = diag_build_id_from(sha);
    CHECK(id == 0xdeadbeefu);
    /* sent little-endian in a 32-bit signal: B0..B3 = hash bytes 3, 2, 1, 0 */
    const diag_signal_t s = { "build_id", 0, 32, false, false, DIAG_MUX_NONE };
    uint8_t d[4] = { 0 };
    CHECK(diag_pack_signal(&s, id, d, 4) == DIAG_PACK_OK);
    hexeq(d, "efbeadde", 4, __LINE__);
}

int main(void)
{
    test_layouts();
    test_misfits();
    test_ranges();
    test_mux();
    test_bad_tables();
    test_build_id();
    printf("%s: %d failure(s)\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
