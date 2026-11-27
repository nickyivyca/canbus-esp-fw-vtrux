/*
 * twai_port_probe_ll on a chip with no register file here (the S3 among
 * them) -- MEASUREMENT-ONLY (SPEC.md T13). The no-acknowledge modes refuse
 * with "not supported", as T8 and T12 do, and nothing touches a register. A
 * chip gets its own file, twai_port_probe_ll_<chip>.c; the S3's comes with
 * the charge interposer's T19 work.
 */
#include "twai_port_probe_ll.h"

bool txp_ll_supported(void)
{
    return false;
}

uint32_t txp_ll_status(void)
{
    return 0;
}

uint32_t txp_ll_tec(void)
{
    return 0;
}

void txp_ll_abort(txp_abort_when_t when, txp_abort_t *out)
{
    (void)when;
    out->st_before = 0;
    out->st_after = 0;
    out->issued = false;
}
