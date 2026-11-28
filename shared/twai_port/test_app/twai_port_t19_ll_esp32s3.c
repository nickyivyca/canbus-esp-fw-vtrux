/*
 * twai_port_t19_ll on the ESP32-S3 -- MEASUREMENT-ONLY (SPEC.md T19). See
 * twai_port_t19_ll.h.
 *
 * CHECKED AGAINST ESP-IDF v5.4.2: components/hal/esp32s3/include/hal/
 * twai_ll.h, TWAI_LL_GET_HW() (line 28), twai_ll_enter_reset_mode() (163),
 * twai_ll_exit_reset_mode() (180), twai_ll_set_mode() (205: LOM and STM bits
 * only, in reset mode), twai_ll_set_cmd_abort_tx() (273),
 * twai_ll_get_status() (352), twai_ll_get_rec() (524) and twai_ll_get_tec()
 * (554); and components/soc/esp32s3/register/soc/twai_struct.h, mode_reg
 * (RM bit 0, LOM bit 1, STM bit 2), bus_timing_0_reg and bus_timing_1_reg,
 * read whole through `.val`.
 *
 * THE SECOND CORE: the critical section masks interrupts on the calling core
 * only, so it is indivisible against the driver's ISR only on the core that
 * installed the driver; the T19 app runs every command there.
 *
 * The interrupt register is never read: reading it clears it, and the
 * driver's ISR would lose what is being measured.
 */
#include "twai_port_t19_ll.h"
#include "hal/twai_ll.h"
#include "freertos/FreeRTOS.h"
#include "soc/soc.h"
#include "soc/system_reg.h"

static portMUX_TYPE s_t19_lock = portMUX_INITIALIZER_UNLOCKED;

/* soc/system_reg.h, SYSTEM_TWAI_CLK_EN and SYSTEM_TWAI_RST (bit 19), as the
 * S3's restart stop reads them (twai_port_ll_esp32s3.c). */
bool t19_ll_clock_on(void)
{
    return (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) != 0
           && (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) == 0;
}

void t19_ll_regs(t19_regs_t *out)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    out->status = twai_ll_get_status(hw);
    out->mode = hw->mode_reg.val;
    out->tec = twai_ll_get_tec(hw);
    out->rec = twai_ll_get_rec(hw);
    out->btr0 = hw->bus_timing_0_reg.val;
    out->btr1 = hw->bus_timing_1_reg.val;
}

uint32_t t19_ll_status(void)
{
    return twai_ll_get_status(TWAI_LL_GET_HW(0));
}

bool t19_ll_enter_reset(bool if_ts0, uint32_t *st_before, uint32_t *st_after)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    taskENTER_CRITICAL(&s_t19_lock);
    const uint32_t before = twai_ll_get_status(hw);
    const bool go = !if_ts0
                    || (before & (TWAI_LL_STATUS_TBS | TWAI_LL_STATUS_TS)) == 0;
    if (go)
    {
        twai_ll_enter_reset_mode(hw);
    }
    const uint32_t after = twai_ll_get_status(hw);
    taskEXIT_CRITICAL(&s_t19_lock);
    *st_before = before;
    *st_after = after;
    return go;
}

void t19_ll_leave_reset(bool listen_only)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    twai_ll_set_mode(hw, listen_only ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
    twai_ll_exit_reset_mode(hw);
}

void t19_ll_abort(uint32_t *st_before, uint32_t *st_after)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    taskENTER_CRITICAL(&s_t19_lock);
    *st_before = twai_ll_get_status(hw);
    twai_ll_set_cmd_abort_tx(hw);
    *st_after = twai_ll_get_status(hw);
    taskEXIT_CRITICAL(&s_t19_lock);
}
