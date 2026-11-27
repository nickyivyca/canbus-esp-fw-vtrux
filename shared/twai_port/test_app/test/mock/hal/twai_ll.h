/*
 * Host stand-in for ESP-IDF v5.4.2's esp32c3 hal/twai_ll.h, for the probe's
 * C3 register file (twai_port_probe_ll_esp32c3.c): the status register and
 * the TEC are values the case sets; the abort command is counted, with the
 * status it was written at, and may set the status the next read returns
 * (`status_after_abort`), so a case can tell a read before the command from
 * one after it. The status bit values are v5.4.2's.
 */
#ifndef MOCK_TWAI_LL_H
#define MOCK_TWAI_LL_H
#include <stdint.h>
typedef struct {
    uint32_t status;
    uint32_t tec;
    int      abort_cmds;
    uint32_t status_at_abort;
    int      set_after_abort;       /* 1: status becomes status_after_abort */
    uint32_t status_after_abort;
    int      in_critical;           /* the case checks the command is inside */
    int      abort_outside;
} twai_dev_t;
extern twai_dev_t mock_twai_dev;
#define TWAI_LL_GET_HW(id) ((id) == 0 ? &mock_twai_dev : (twai_dev_t *)0)
#define TWAI_LL_STATUS_RBS      (0x1 << 0)
#define TWAI_LL_STATUS_DOS      (0x1 << 1)
#define TWAI_LL_STATUS_TBS      (0x1 << 2)
#define TWAI_LL_STATUS_TCS      (0x1 << 3)
#define TWAI_LL_STATUS_RS       (0x1 << 4)
#define TWAI_LL_STATUS_TS       (0x1 << 5)
#define TWAI_LL_STATUS_ES       (0x1 << 6)
#define TWAI_LL_STATUS_BS       (0x1 << 7)
#define TWAI_LL_STATUS_MS       (0x1 << 8)
static inline uint32_t twai_ll_get_status(twai_dev_t *hw) { return hw->status; }
static inline uint32_t twai_ll_get_tec(twai_dev_t *hw) { return hw->tec; }
static inline void twai_ll_set_cmd_abort_tx(twai_dev_t *hw)
{
    hw->abort_cmds++;
    hw->status_at_abort = hw->status;
    if (!hw->in_critical) { hw->abort_outside++; }
    if (hw->set_after_abort) { hw->status = hw->status_after_abort; }
}
#endif
