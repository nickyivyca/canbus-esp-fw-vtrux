/*
 * Host tests of the probe's register files (twai_port_probe_ll_*.c; SPEC.md
 * T13). Built twice: with the C3 file against mock/hal/twai_ll.h, and with
 * the unsupported file (-DPROBE_LL_UNSUPPORTED). Self-checks, not
 * verification.
 */
#include "../twai_port_probe_ll.h"
#include "hal/twai_ll.h"

#include <stdio.h>
#include <string.h>

twai_dev_t mock_twai_dev;

static int g_fail;
#define CHECK(c) do { if (!(c)) { printf("  FAIL %s:%d: %s\n", __FILE__, \
                                         __LINE__, #c); g_fail++; } } while (0)

static void reset(uint32_t status, uint32_t tec)
{
    memset(&mock_twai_dev, 0, sizeof(mock_twai_dev));
    mock_twai_dev.status = status;
    mock_twai_dev.tec = tec;
}

#ifndef PROBE_LL_UNSUPPORTED

static void test_bits(void)
{
    printf("c3: each status bit maps to its neutral bit, and nothing else\n");
    const struct { uint32_t ll, neutral; } map[] = {
        { TWAI_LL_STATUS_TBS, TXP_ST_TBS }, { TWAI_LL_STATUS_TCS, TXP_ST_TCS },
        { TWAI_LL_STATUS_RS, TXP_ST_RS },   { TWAI_LL_STATUS_TS, TXP_ST_TS },
        { TWAI_LL_STATUS_ES, TXP_ST_ES },   { TWAI_LL_STATUS_BS, TXP_ST_BS },
        { TWAI_LL_STATUS_RBS, 0 }, { TWAI_LL_STATUS_DOS, 0 },
        { TWAI_LL_STATUS_MS, 0 },
    };
    for (unsigned i = 0; i < sizeof(map) / sizeof(map[0]); i++)
    {
        reset(map[i].ll, 0);
        CHECK(txp_ll_status() == map[i].neutral);
    }
    reset(0x1FF, 0);
    CHECK(txp_ll_status() == TXP_ST_ALL);
    CHECK(txp_ll_supported());
    reset(0, 137);
    CHECK(txp_ll_tec() == 137);
}

static void test_abort(void)
{
    txp_abort_t a;

    printf("c3: if-TS0 issues only with a frame held and TS = 0\n");
    const struct { uint32_t st; int issue; } cases[] = {
        { 0, 1 },
        { TWAI_LL_STATUS_RS | TWAI_LL_STATUS_ES | TWAI_LL_STATUS_TCS, 1 },
        { TWAI_LL_STATUS_TS, 0 },
        { TWAI_LL_STATUS_TBS, 0 },
        { TWAI_LL_STATUS_TBS | TWAI_LL_STATUS_TS, 0 },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        reset(cases[i].st, 0);
        txp_ll_abort(TXP_ABORT_IF_TS0, &a);
        CHECK(a.issued == (cases[i].issue != 0));
        CHECK(mock_twai_dev.abort_cmds == cases[i].issue);
        CHECK(a.st_before == txp_ll_status());
        CHECK(mock_twai_dev.in_critical == 0 && mock_twai_dev.abort_outside == 0);
    }

    printf("c3: always issues, at TS = 1 too\n");
    reset(TWAI_LL_STATUS_TS, 0);
    txp_ll_abort(TXP_ABORT_ALWAYS, &a);
    CHECK(a.issued && mock_twai_dev.abort_cmds == 1);
    CHECK(a.st_before == TXP_ST_TS);
    CHECK(mock_twai_dev.abort_outside == 0 && mock_twai_dev.in_critical == 0);

    printf("c3: the reads are before and after the command\n");
    reset(0, 0);
    mock_twai_dev.set_after_abort = 1;
    mock_twai_dev.status_after_abort = TWAI_LL_STATUS_TBS | TWAI_LL_STATUS_TCS;
    txp_ll_abort(TXP_ABORT_IF_TS0, &a);
    CHECK(a.issued && a.st_before == 0
          && a.st_after == (TXP_ST_TBS | TXP_ST_TCS));
    CHECK(mock_twai_dev.status_at_abort == 0);

    printf("c3: not issued: the after read is still taken\n");
    reset(TWAI_LL_STATUS_TS, 0);
    txp_ll_abort(TXP_ABORT_IF_TS0, &a);
    CHECK(!a.issued && a.st_before == TXP_ST_TS && a.st_after == TXP_ST_TS);
}

int main(void)
{
    test_bits();
    test_abort();
    printf("%s: %d failure(s)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}

#else

int main(void)
{
    txp_abort_t a;

    printf("unsupported: refuses and touches nothing\n");
    reset(TWAI_LL_STATUS_TS | TWAI_LL_STATUS_ES, 99);
    CHECK(!txp_ll_supported());
    CHECK(txp_ll_status() == 0 && txp_ll_tec() == 0);
    memset(&a, 0xA5, sizeof(a));
    txp_ll_abort(TXP_ABORT_ALWAYS, &a);
    CHECK(!a.issued && a.st_before == 0 && a.st_after == 0);
    txp_ll_abort(TXP_ABORT_IF_TS0, &a);
    CHECK(!a.issued);
    reset(0, 0);
    txp_ll_abort(TXP_ABORT_IF_TS0, &a);
    CHECK(!a.issued && mock_twai_dev.abort_cmds == 0);
    printf("%s: %d failure(s)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}

#endif
