/*
 * The misfit wrapper's own cases (test_diag_host.py builds and runs this):
 *
 *   misfit_wrap_test fit       a value that fits: no record, the frame packed
 *   misfit_wrap_test misfit    signal 1 one past its maximum: one record, id, count 1,
 *                              mask bit 1, its name and value, the caller's mask set,
 *                              the frame's other signal packed, the misfit's bits 0
 *   misfit_wrap_test bad       a table entry the packer refuses (DLC 9): a record, -1
 *   misfit_wrap_test untaken   a misfit nobody takes: exit 3, the line on stderr
 *
 * Prints "ok" lines and exits 0, or "FAIL" lines and exits 1 (untaken: see above).
 * Gen-inhibit tester, 2026-11-23.
 */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "diag_pack.h"
#include "misfit_wrap.h"

static const diag_signal_t SIGS[2] = {
    { "sig_a", 0, 8, false, false, DIAG_MUX_NONE },
    { "sig_b", 8, 4, false, false, DIAG_MUX_NONE },
};
static const diag_message_t MSG = { "Test_Msg", 0x7FAu, false, 8, 2, SIGS };
static const diag_message_t BAD = { "Bad_Msg", 0x7FBu, false, 9, 2, SIGS };

static int fails;

static void check(const char *what, int ok)
{
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    fails += !ok;
}

int main(int argc, char **argv)
{
    const char *c = argc > 1 ? argv[1] : "";
    uint8_t out[8];
    uint64_t mask = 0xFFu;
    gi_misfit_t r;

    if (!strcmp(c, "fit"))
    {
        int64_t raw[2] = { 0x5A, 0xF };
        int n = diag_pack_message(&MSG, raw, out, &mask);
        check("fit: the packer's result passed through (0)", n == 0);
        check("fit: the caller's mask is 0", mask == 0);
        check("fit: no record", gi_misfit_total() == 0 && !gi_misfit_take(&r));
        check("fit: the call was seen", gi_misfit_calls() == 1);
        check("fit: the frame packed (5A 0F)", out[0] == 0x5A && out[1] == 0x0F);
    }
    else if (!strcmp(c, "misfit"))
    {
        int64_t raw[2] = { 0x5A, 16 };
        int n = diag_pack_message(&MSG, raw, out, &mask);
        check("misfit: the packer's result passed through (1)", n == 1);
        check("misfit: the caller's mask is bit 1", mask == 2u);
        check("misfit: one record", gi_misfit_total() == 1 && gi_misfit_take(&r));
        check("misfit: the record's id, count, mask", r.id == 0x7FAu && r.result == 1 && r.mask == 2u);
        check("misfit: the record names sig_b and its value 16",
              r.first && !strcmp(r.first, "sig_b") && r.first_raw == 16);
        check("misfit: the other signal packed, the misfit's bits 0", out[0] == 0x5A && out[1] == 0);
        check("misfit: taken, none left", !gi_misfit_take(&r));
        /* NULL for the mask, as the core passes it */
        n = diag_pack_message(&MSG, raw, out, NULL);
        check("misfit, NULL mask: still recorded", n == 1 && gi_misfit_take(&r) && r.mask == 2u);
        char buf[200];
        gi_misfit_format(&r, buf, sizeof buf);
        check("misfit: the line", !strcmp(buf, "DIAG MISFIT id=7FA count=1 mask=0000000000000002 first=sig_b raw=16"));
    }
    else if (!strcmp(c, "bad"))
    {
        int64_t raw[2] = { 1, 1 };
        int n = diag_pack_message(&BAD, raw, out, &mask);
        check("bad: the packer's -1 passed through", n == -1);
        check("bad: recorded as -1", gi_misfit_take(&r) && r.result == -1 && r.id == 0x7FBu);
        char buf[200];
        gi_misfit_format(&r, buf, sizeof buf);
        check("bad: the line", !strcmp(buf, "DIAG UNPACKABLE id=7FB result=-1"));
    }
    else if (!strcmp(c, "untaken"))
    {
        int64_t raw[2] = { 0x5A, 16 };
        (void)diag_pack_message(&MSG, raw, out, NULL);
        printf("returning 0 with a record not taken\n");
        return 0;
    }
    else
    {
        printf("FAIL unknown case %s\n", c);
        return 1;
    }
    return fails ? 1 : 0;
}
