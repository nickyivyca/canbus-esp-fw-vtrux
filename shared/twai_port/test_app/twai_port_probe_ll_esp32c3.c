/*
 * twai_port_probe_ll on the ESP32-C3 -- MEASUREMENT-ONLY (SPEC.md T13).
 * See twai_port_probe_ll.h for the layer and why it is not in
 * twai_port_ll_esp32c3.c.
 *
 * CHECKED AGAINST ESP-IDF v5.4.2: components/hal/esp32c3/include/hal/
 * twai_ll.h, TWAI_LL_GET_HW(), twai_ll_get_status(), twai_ll_get_tec(),
 * twai_ll_set_cmd_abort_tx() and TWAI_LL_STATUS_TBS, _TCS, _RS, _TS, _ES and
 * _BS, all static inline. The same file in v5.4.1 is byte-identical (port
 * step 1d, 2026-11-03).
 *
 * The interrupt register is never read here: reading it clears it, and the
 * driver's ISR would lose what the modes are measuring.
 */
#include "twai_port_probe_ll.h"
#include "hal/twai_ll.h"
#include "freertos/FreeRTOS.h"

static portMUX_TYPE s_probe_lock = portMUX_INITIALIZER_UNLOCKED;

static uint32_t to_neutral(uint32_t st)
{
    uint32_t out = 0;
    if (st & TWAI_LL_STATUS_TBS) { out |= TXP_ST_TBS; }
    if (st & TWAI_LL_STATUS_TCS) { out |= TXP_ST_TCS; }
    if (st & TWAI_LL_STATUS_RS)  { out |= TXP_ST_RS; }
    if (st & TWAI_LL_STATUS_TS)  { out |= TXP_ST_TS; }
    if (st & TWAI_LL_STATUS_ES)  { out |= TXP_ST_ES; }
    if (st & TWAI_LL_STATUS_BS)  { out |= TXP_ST_BS; }
    return out;
}

bool txp_ll_supported(void)
{
    return true;
}

uint32_t txp_ll_status(void)
{
    return to_neutral(twai_ll_get_status(TWAI_LL_GET_HW(0)));
}

uint32_t txp_ll_tec(void)
{
    return twai_ll_get_tec(TWAI_LL_GET_HW(0));
}

void txp_ll_abort(txp_abort_when_t when, txp_abort_t *out)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);

    taskENTER_CRITICAL(&s_probe_lock);
    const uint32_t before = to_neutral(twai_ll_get_status(hw));
    const bool issue = (when == TXP_ABORT_ALWAYS)
                       || (before & (TXP_ST_TBS | TXP_ST_TS)) == 0;
    if (issue)
    {
        twai_ll_set_cmd_abort_tx(hw);
    }
    const uint32_t after = to_neutral(twai_ll_get_status(hw));
    taskEXIT_CRITICAL(&s_probe_lock);

    out->st_before = before;
    out->st_after = after;
    out->issued = issue;
}
