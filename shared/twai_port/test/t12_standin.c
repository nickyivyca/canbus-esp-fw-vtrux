/*
 * The tester's own restart stop, written from SPEC.md T12 and the fake's
 * register calls, never from twai_port's code: the can-fail of the T12
 * cases in test_port_on_fake.c (AGENTS.md: a check that cannot fail is not
 * a check). Those cases, built with -DT12_STANDIN, call this in place of
 * twai_port_restart_stop(). As written it must pass every one of them; with
 * T12_BROKEN set in the environment to one of the names below it breaks one
 * rule of T12, and some case must fail on it.
 *
 *   no wait              puts the controller into reset mode at once
 *   no bound             waits up to 10 ms, not 1 ms
 *   no reset on timeout  leaves the controller alone when the wait runs out
 *   no abort             never runs the abort-if-awaiting
 *   ignores the clock    goes on with the TWAI clock off
 *   ignores reset held   goes on with the peripheral held in reset
 *   wrong step           polls every 10 us, not every 2 us
 *   status not recorded  reports 0 for the status at entry
 *   counters not read    reports 0 for the error counters
 *   stalls a core        (one core) calls esp_cpu_stall()
 *
 * Built with -DT12_STANDIN_TWO_CORES (the S3), it first stalls the other
 * core, unless its stall fields show it stalled already (T12, added
 * 2026-11-27), and these break that:
 *
 *   no stall             never stalls the other core
 *   stall after steps    stalls it only after T12's steps
 *   restalls             stalls it even when it is stalled already
 *   unstalls             starts it again at the end
 *
 *   make -C shared/twai_port/test t12-standins
 */
#include "twai_port.h"
#include "hal/twai_ll.h"
#include "soc/system_reg.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_rom_sys.h"
#include "esp_cpu.h"

#include <stdlib.h>
#include <string.h>

esp_err_t t12_standin_restart_stop(can_port_restart_report_t *out);

static bool broken(const char *name)
{
    const char *b = getenv("T12_BROKEN");
    return b != NULL && strcmp(b, name) == 0;
}

#ifdef T12_STANDIN_TWO_CORES
/* The stall code esp_cpu_stall() writes: C0 = 2 and C1 = 0x21. */
static bool other_stalled(int core)
{
    const uint32_t c0 = REG_READ(RTC_CNTL_OPTIONS0_REG) >> (core == 0 ? RTC_CNTL_SW_STALL_PROCPU_C0_S
                                                                    : RTC_CNTL_SW_STALL_APPCPU_C0_S);
    const uint32_t c1 = REG_READ(RTC_CNTL_SW_CPU_STALL_REG) >> (core == 0 ? RTC_CNTL_SW_STALL_PROCPU_C1_S
                                                                       : RTC_CNTL_SW_STALL_APPCPU_C1_S);
    return (c0 & 0x3u) == 0x2u && (c1 & 0x3Fu) == 0x21u;
}

static void stall_other(void)
{
    const int other = esp_cpu_get_core_id() == 0 ? 1 : 0;
    if (broken("no stall")) { return; }
    if (!other_stalled(other) || broken("restalls")) { esp_cpu_stall(other); }
}
#endif

static esp_err_t steps(can_port_restart_report_t *out);

esp_err_t t12_standin_restart_stop(can_port_restart_report_t *out)
{
    esp_err_t e;
    if (out == NULL) { return ESP_ERR_INVALID_ARG; }
#ifdef T12_STANDIN_TWO_CORES
    if (!broken("stall after steps")) { stall_other(); }
#else
    if (broken("stalls a core")) { esp_cpu_stall(1); }
#endif
    e = steps(out);
#ifdef T12_STANDIN_TWO_CORES
    if (broken("stall after steps")) { stall_other(); }
    if (broken("unstalls")) { esp_cpu_unstall(esp_cpu_get_core_id() == 0 ? 1 : 0); }
#endif
    return e;
}

static esp_err_t steps(can_port_restart_report_t *out)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    const uint32_t step = broken("wrong step") ? 10u : 2u;
    const uint32_t limit = broken("no bound") ? 10000u : 1000u;
    uint32_t waited = 0;

    memset(out, 0, sizeof(*out));
    out->clock_on = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) != 0;
    out->held_in_reset = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) != 0;
    if (!(out->clock_on || broken("ignores the clock"))) { return ESP_OK; }
    if (out->held_in_reset && !broken("ignores reset held")) { return ESP_OK; }

    out->was_in_reset = twai_ll_is_in_reset_mode(hw);
    out->status_at_entry = broken("status not recorded") ? 0u : twai_ll_get_status(hw);
    if (!broken("counters not read"))
    {
        out->tec = twai_ll_get_tec(hw);
        out->rec = twai_ll_get_rec(hw);
    }
    for (;;)
    {
        if (!broken("no abort") && twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING)
        {
            out->aborts++;
        }
        if (broken("no wait")
            || (twai_ll_get_status(hw) & (TWAI_LL_STATUS_TS | TWAI_LL_STATUS_RS)) == 0) { break; }
        if (waited >= limit)
        {
            out->timed_out = true;
            break;
        }
        esp_rom_delay_us(step);
        waited += step;
    }
    out->wait_us = waited;
    if (out->timed_out && broken("no reset on timeout")) { return ESP_OK; }
    out->status_at_reset = twai_ll_get_status(hw);
    twai_ll_enter_reset_mode(hw);
    out->entered_reset = twai_ll_is_in_reset_mode(hw);
    return ESP_OK;
}
