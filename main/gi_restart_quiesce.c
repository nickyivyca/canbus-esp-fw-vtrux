/*
 * gi_restart_quiesce -- every software restart takes the CAN controller off the
 * bus first. Spec 3, "No restart leaves the controller on the bus" (added
 * 2026-10-13, user), from the trial build 089cd21 (evidence doc 12.4,
 * "Restart-quiesce trial: the hook on and off at a live restart").
 *
 * WHY (IDF v5.4.1). esp_restart() runs the shutdown handlers and calls
 * esp_restart_noos() (esp_system.c:63); a panic calls it from panic_restart()
 * (panic_handler.c:266 -- the noos_dig branch above it is ESP32-only); and
 * call_start_cpu0 calls it on two early-boot failures (cpu_start.c:769, :790).
 * On the C3 that function (port/soc/esp32c3/system_internal.c:64) resets a list
 * of peripherals that does NOT include TWAI, drops the CPU to XTAL at :107,
 * which takes APB from 80 to 40 MHz, and resets the CPU only at :110. Nothing
 * stops or uninstalls the driver, and after a CPU reset esp_perip_clk_init
 * gates only the clocks that were already off (clk.c:215-217). So a running
 * controller keeps its configuration, its clock and any pending frame through
 * the reset, timed from half its clock, on a bus it is still connected to:
 * 107-139 error frames per live restart on the bench without this, 0 with it.
 *
 * THE HOOK is a linker wrap of esp_restart_noos() (`-Wl,--wrap`, in
 * main/CMakeLists.txt). Every caller is in another object file, so all of them
 * reach __wrap_esp_restart_noos(), which runs before the clock drop and then
 * calls the real function. It is the point every software restart passes
 * through, after the last code that could skip it -- the shutdown handlers a
 * crash skips are earlier, and none of them is used.
 *
 * WHAT IT DOES, in this order, as the trial did:
 *
 *   1. RESET MODE, after the frame on the wire (if any) has finished. Abort
 *      first -- that drops frames still waiting and stops the one on the wire
 *      from being retried -- then wait, bounded at 1 ms, for Transmit Status
 *      and Receive Status both to read 0, i.e. for the bus to be between
 *      frames. Entering reset mode in the middle of our own frame would cut it
 *      off, and every other node would flag that. If the wait runs out it is
 *      because another node's frame is on the wire, not ours (the abort stops
 *      ours being retried), and reset mode is entered anyway; the record says
 *      so. Nothing resets the controller again until twai_driver_install()
 *      does (twai.c:497-499), so it stays in reset mode through the CPU reset,
 *      the ROM, the bootloader and app_main.
 *
 *      Skipped when the TWAI clock is off or the peripheral is held in reset:
 *      the registers are then unreachable, and the controller is not on the
 *      bus. That is the state after a power-on (clk.c:237) or a
 *      twai_driver_uninstall() (twai.c:566-572). It is NOT the state of mode 0
 *      in general: a CPU reset from a live state leaves the controller clocked
 *      and running with no driver, and mode 0 never installs or uninstalls one
 *      to clear it (reviewer's reading of the trial, 2026-10-13). The hook
 *      reads the two bits rather than assuming either.
 *
 *   2. STANDBY: CAN_STDBY (GPIO6 on this board) driven 1, the level main.c
 *      uses for standby. app_main writes 1 again (main.c:432-434) and holds it
 *      until can_enable() takes it to 0, so the transceiver is in standby from
 *      the hook until the bus is wanted. This half does not depend on the
 *      controller's state surviving; reset mode does not depend on the pin.
 *
 * THE RECORD is written in every build, so that the test build runs exactly
 * the hook a prod build runs. Only a test build reads it (gi_restart_test.c).
 *
 * IRAM, AND NOTHING THAT NEEDS THE CACHE. The wrapper and what it calls are in
 * IRAM or ROM, and it touches registers and the RTC record only. Its callers
 * have the cache enabled when they get here, but the real function disables
 * it, and nothing here should depend on where that line is.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_rom_sys.h"
#include "riscv/rv_utils.h"
#include "hal/twai_ll.h"
#include "soc/soc.h"
#include "soc/system_reg.h"
#include "soc/gpio_reg.h"

#include "hw_config.h"
#include "gi_restart_quiesce.h"

_Static_assert(CAN_STDBY_GPIO_NUM < 32,
               "the hook drives CAN_STDBY through GPIO_OUT_W1TS_REG, which "
               "covers GPIO0-31 only");

#define QZ_WAIT_MAX_US  1000u
#define QZ_WAIT_STEP_US 2u

static RTC_NOINIT_ATTR gi_restart_record_t s_rec;

static IRAM_ATTR uint32_t qz_sum(const gi_restart_record_t *r)
{
    const uint32_t *w = (const uint32_t *)r;
    uint32_t x = 0x9E3779B9u;
    for (size_t i = 0;
         i < offsetof(gi_restart_record_t, check) / sizeof(uint32_t); i++)
    {
        x = (x ^ w[i]) * 0x01000193u;
    }
    return x;
}

static IRAM_ATTR bool qz_valid(const gi_restart_record_t *r)
{
    return r->magic == GI_RESTART_RECORD_MAGIC && r->check == qz_sum(r);
}

bool gi_restart_record_valid(void)
{
    return qz_valid(&s_rec);
}

void gi_restart_record_get(gi_restart_record_t *out)
{
    *out = s_rec;
}

void gi_restart_record_init(void)
{
    memset(&s_rec, 0, sizeof(s_rec));
    s_rec.magic = GI_RESTART_RECORD_MAGIC;
    s_rec.check = qz_sum(&s_rec);
}

void __real_esp_restart_noos(void) __attribute__((noreturn));
void __wrap_esp_restart_noos(void) __attribute__((noreturn));

void IRAM_ATTR __wrap_esp_restart_noos(void)
{
    /*
     * The real function's first act, done here first: the driver's ISR must
     * not run while the controller is being taken off the bus under it.
     */
    rv_utils_intr_global_disable();

    gi_restart_record_t r;
    r.magic     = GI_RESTART_RECORD_MAGIC;
    r.entries   = (qz_valid(&s_rec) ? s_rec.entries : 0u) + 1u;
    r.twai_clk  = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) ? 1u : 0u;
    r.twai_rst  = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) ? 1u : 0u;
    r.in_reset  = 0;
    r.st_entry  = 0;
    r.tec       = 0;
    r.rec       = 0;
    r.did_twai  = 0;
    r.st_reset  = 0;
    r.wait_us   = 0;
    r.timed_out = 0;
    r.did_stdby = 0;

    if (r.twai_clk && !r.twai_rst)
    {
        twai_dev_t *hw = TWAI_LL_GET_HW(0);
        r.in_reset = twai_ll_is_in_reset_mode(hw) ? 1u : 0u;
        r.st_entry = twai_ll_get_status(hw);
        r.tec      = twai_ll_get_tec(hw);
        r.rec      = twai_ll_get_rec(hw);

        twai_ll_set_cmd_abort_tx(hw);
        uint32_t waited = 0;
        while ((twai_ll_get_status(hw) &
                (TWAI_LL_STATUS_TS | TWAI_LL_STATUS_RS)) != 0 &&
               waited < QZ_WAIT_MAX_US)
        {
            esp_rom_delay_us(QZ_WAIT_STEP_US);
            waited += QZ_WAIT_STEP_US;
        }
        r.wait_us   = waited;
        r.timed_out = (waited >= QZ_WAIT_MAX_US) ? 1u : 0u;
        r.st_reset  = twai_ll_get_status(hw);
        twai_ll_enter_reset_mode(hw);
        r.did_twai  = 1;
    }

    REG_WRITE(GPIO_OUT_W1TS_REG, BIT(CAN_STDBY_GPIO_NUM));
    r.did_stdby = 1;

    r.check = qz_sum(&r);
    s_rec = r;

    __real_esp_restart_noos();
}
