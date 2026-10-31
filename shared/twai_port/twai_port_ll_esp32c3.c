/*
 * vtrux/twai_port, T8 on the ESP32-C3: the transmit buffer's state and the
 * abort-if-awaiting, register level.
 *
 * CHECKED AGAINST ESP-IDF v5.4.1: components/hal/esp32c3/include/hal/
 * twai_ll.h, TWAI_LL_GET_HW(), twai_ll_get_status(), TWAI_LL_STATUS_TBS and
 * TWAI_LL_STATUS_TS, and twai_ll_set_cmd_abort_tx(), all static inline. A
 * project on another IDF version re-checks those before taking this file.
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
 * installed (the controller's clock on). The restart step checks the clock
 * before it calls in.
 */
#include "twai_port.h"
#include "hal/twai_ll.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"

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
