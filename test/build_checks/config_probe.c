/*
 * Print gi_config_defaults() so a host check can compare it with the spec.
 *
 * It compiles the SAME gen_inhibit_core.c the device runs, so what it prints is
 * what the firmware is built with -- not a transcription that could drift. The
 * comparison table lives in config_vs_spec.py and cites a spec section per row.
 */
#include "gen_inhibit_core.h"

#include <stdio.h>

int main(void)
{
    gi_config_t c;
    gi_config_defaults(&c);

    printf("soc_min_raw %u\n", (unsigned)c.soc_min_raw);
    printf("soc_debounce %u\n", (unsigned)c.soc_debounce);
    printf("start_abort_rpm %d\n", (int)c.start_abort_rpm);
    printf("rpm_debounce_us %lld\n", (long long)c.rpm_debounce_us);
    printf("fresh_us %lld\n", (long long)c.fresh_us);
    printf("fault_fresh_us %lld\n", (long long)c.fault_fresh_us);
    printf("err_window_us %lld\n", (long long)c.err_window_us);
    printf("err_min_trip %u\n", (unsigned)c.err_min_trip);
    printf("diag_period_ms %u\n", (unsigned)c.diag_period_ms);
    printf("max_rx_errors %u\n", (unsigned)c.max_rx_errors);
    return 0;
}
