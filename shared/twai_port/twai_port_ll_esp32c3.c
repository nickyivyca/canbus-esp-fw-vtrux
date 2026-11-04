/*
 * vtrux/twai_port, T8 on the ESP32-C3: the transmit buffer's state and the
 * abort-if-awaiting, register level.
 *
 * CHECKED AGAINST ESP-IDF v5.4.1 AND v5.4.2: components/hal/esp32c3/include/
 * hal/twai_ll.h, TWAI_LL_GET_HW(), twai_ll_get_status(), TWAI_LL_STATUS_TBS
 * and TWAI_LL_STATUS_TS, and twai_ll_set_cmd_abort_tx(), all static inline.
 * twai_ll.h and the soc headers below are byte-identical in the two (port
 * step 1d, 2026-11-03). A project on another IDF version re-checks those
 * before taking this file.
 *
 * MEASURED on the bench C3 (gen-inhibit spec 5.2 item 9, its table of 300
 * trials): an abort awaiting arbitration removes the frame; at TS = 1 it may
 * complete, be removed, or have no effect while the frame keeps contending.
 * This is the only chip with a measured file; others build
 * twai_port_ll_unsupported.c.
 *
 * Moved at port step 1b from gen-inhibit's gen_inhibit.c (buf_from_status(),
 * dev_buf_state(), dev_abort_if_awaiting(); gen-inhibit/main 4020bc8), where
 * the reasoning below was first written.
 *
 * PRIVATE HAL, below the public driver API, sanctioned by gen-inhibit spec
 * 5.2 items 4 and 9 (user, 2026-09-27). twai_ll_set_cmd_abort_tx() is at
 * exactly one site, twai_port_abort_if_awaiting() below (gen-inhibit spec
 * 12.4, "the private TWAI abort is confined"), because it is the kind of call
 * that spreads.
 *
 * Both read the controller's registers, so both are for while the driver is
 * installed (the controller's clock on). The restart stop checks the clock
 * before it calls in.
 *
 * T12, THE RESTART STOP, from step 1c: moved from gen-inhibit's
 * gi_restart_quiesce.c (gen-inhibit/main 69d97a2), with the same steps,
 * bounds and order. Also checked against ESP-IDF v5.4.1 and v5.4.2: in
 * twai_ll.h, twai_ll_is_in_reset_mode(), twai_ll_enter_reset_mode(),
 * twai_ll_get_tec(), twai_ll_get_rec() and TWAI_LL_STATUS_RS; in
 * soc/system_reg.h (with soc/soc.h and soc/reg_base.h),
 * SYSTEM_PERIP_CLK_EN0_REG with SYSTEM_TWAI_CLK_EN and
 * SYSTEM_PERIP_RST_EN0_REG with SYSTEM_TWAI_RST. Line numbers below are
 * v5.4.1's, with v5.4.2's after the slash where they moved.
 */
#include "twai_port.h"
#include "hal/twai_ll.h"
#include "esp_attr.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "soc/soc.h"
#include "soc/system_reg.h"

static portMUX_TYPE s_abort_lock = portMUX_INITIALIZER_UNLOCKED;

static IRAM_ATTR twai_port_buf_t buf_from_status(uint32_t st)
{
    if (st & TWAI_LL_STATUS_TBS) { return TWAI_PORT_BUF_EMPTY; }
    /*
     * TS covers the ARBITRATION FIELD as well as the data phase, so this does
     * not mean the frame is certain to reach the wire -- which is why the
     * caller's loop waits here instead of issuing a command that the
     * measurement showed has no effect.
     */
    if (st & TWAI_LL_STATUS_TS)  { return TWAI_PORT_BUF_TRANSMITTING; }
    return TWAI_PORT_BUF_AWAITING;
}

twai_port_buf_t twai_port_buf_state(void)
{
    return buf_from_status(twai_ll_get_status(TWAI_LL_GET_HW(0)));
}

/*
 * Read the state and, if AWAITING, issue the abort -- INDIVISIBLY.
 *
 * The controller is independent hardware and does not stop between a read and
 * a write, so doing this as two calls lets it enter arbitration in between and
 * the command then lands at TS = 1, where 7 of 8 measured aborts had no effect
 * at all. One critical section narrows that to a few cycles; it cannot close
 * it, because the controller keeps running, and the caller's recheck loop is
 * what recovers from the residual.
 *
 * The interrupt register is never touched: reading it clears interrupts and
 * would steal them from the driver.
 *
 * IN IRAM, because gen-inhibit's restart step calls it (its spec 3) and may
 * run from a panic, so this must not depend on the flash cache. What it calls
 * is inline LL or IRAM already: buf_from_status() above, and on the C3, a
 * single-core part, taskENTER/EXIT_CRITICAL are vPortEnterCritical() and
 * vPortExitCritical(), which mask interrupts and keep a nesting count -- no
 * spinlock is spun on, so a crash taken inside this same section cannot
 * deadlock the restart on it. This lock is the port's own; on one core any
 * critical section excludes every other, as gen_inhibit.c's shared lock did.
 */
IRAM_ATTR twai_port_buf_t twai_port_abort_if_awaiting(void)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    twai_port_buf_t seen;

    taskENTER_CRITICAL(&s_abort_lock);
    seen = buf_from_status(twai_ll_get_status(hw));
    if (seen == TWAI_PORT_BUF_AWAITING)
    {
        twai_ll_set_cmd_abort_tx(hw);
    }
    taskEXIT_CRITICAL(&s_abort_lock);
    return seen;
}

#define RESTART_WAIT_MAX_US  1000u
#define RESTART_WAIT_STEP_US 2u

/*
 * T12, THE RESTART STOP. Why a restart needs it (on the C3, esp_restart_noos()
 * neither resets nor stops the TWAI peripheral, and drops the CPU clock from
 * 80 to 40 MHz before the CPU reset) is in shared/restart_guard's
 * restart_guard.c, which calls this through can_port C7.
 *
 * RESET MODE, AFTER THE FRAME ON THE WIRE (if any) HAS FINISHED. Wait,
 * bounded at 1 ms, for Transmit Status and Receive Status both to read 0, so
 * for the bus to be between frames, and on EVERY poll abort our frame if it
 * is still awaiting the bus. Entering reset mode in the middle of our own
 * frame would cut it off, and every other node would flag that.
 *
 * It aborts only an AWAITING frame, the only state in which the command was
 * measured to act (7 of 8 at TS = 1 had no effect), and on every poll, so a
 * frame that failed on the wire and dropped back to awaiting its retry is
 * aborted then, rather than retried into the 1 ms bound. Once nothing of ours
 * is awaiting, nothing of ours can start: interrupts are off, so the driver
 * cannot load another frame. If the wait still runs out, another node's frame
 * is on the wire, and reset mode is entered anyway; the report says so.
 * Nothing takes the controller out of reset mode again until
 * twai_driver_install() does (twai.c:497-499 / 499-501), so it stays there
 * through the CPU reset, the ROM, the bootloader and app_main.
 *
 * SKIPPED when the TWAI clock is off or the peripheral is held in reset: the
 * registers are then unreachable, and the controller is not on the bus. That
 * is the state after a power-on (clk.c:237 / 240) or a
 * twai_driver_uninstall() (twai.c:566-572 / 568-574). It is NOT the state of
 * a boot that never installed the driver: a CPU reset from a live state
 * leaves the controller clocked and running with no driver. So it reads the
 * two bits rather than the port's state, and acts whether or not the port
 * was set up in this boot.
 *
 * IRAM, and what it calls is inline LL, ROM (esp_rom_delay_us) or IRAM (the
 * abort above). No lock of its own, no allocation, no log.
 */
IRAM_ATTR esp_err_t twai_port_restart_stop(can_port_restart_report_t *r)
{
    if (r == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    r->clock_on        = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) != 0;
    r->held_in_reset   = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) != 0;
    r->was_in_reset    = false;
    r->status_at_entry = 0;
    r->tec             = 0;
    r->rec             = 0;
    r->status_at_reset = 0;
    r->wait_us         = 0;
    r->timed_out       = false;
    r->aborts          = 0;
    r->entered_reset   = false;

    if (!r->clock_on || r->held_in_reset)
    {
        return ESP_OK;
    }

    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    r->was_in_reset    = twai_ll_is_in_reset_mode(hw);
    r->status_at_entry = twai_ll_get_status(hw);
    r->tec             = twai_ll_get_tec(hw);
    r->rec             = twai_ll_get_rec(hw);

    uint32_t waited = 0;
    for (;;)
    {
        if (twai_port_abort_if_awaiting() == TWAI_PORT_BUF_AWAITING)
        {
            r->aborts++;
        }
        if ((twai_ll_get_status(hw) &
             (TWAI_LL_STATUS_TS | TWAI_LL_STATUS_RS)) == 0 ||
            waited >= RESTART_WAIT_MAX_US)
        {
            break;
        }
        esp_rom_delay_us(RESTART_WAIT_STEP_US);
        waited += RESTART_WAIT_STEP_US;
    }
    r->wait_us         = waited;
    r->timed_out       = waited >= RESTART_WAIT_MAX_US;
    r->status_at_reset = twai_ll_get_status(hw);
    twai_ll_enter_reset_mode(hw);
    r->entered_reset   = true;
    return ESP_OK;
}
