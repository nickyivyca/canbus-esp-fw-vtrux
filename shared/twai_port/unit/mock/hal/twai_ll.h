/*
 * Host stand-in for ESP-IDF 5.4.1's esp32c3 hal/twai_ll.h: the status register
 * is a value the case sets, and the abort command is recorded. No controller
 * behaviour: writing the command changes nothing the next read returns.
 */
#ifndef MOCK_TWAI_LL_H
#define MOCK_TWAI_LL_H
#include <stdint.h>
typedef struct { uint32_t status; int abort_cmds; } twai_dev_t;
extern twai_dev_t stub_twai_dev;
#define TWAI_LL_GET_HW(id) ((id) == 0 ? &stub_twai_dev : (twai_dev_t *)0)
#define TWAI_LL_STATUS_TBS (0x1 << 2)
#define TWAI_LL_STATUS_TS  (0x1 << 5)
static inline uint32_t twai_ll_get_status(twai_dev_t *hw) { return hw->status; }
static inline void twai_ll_set_cmd_abort_tx(twai_dev_t *hw) { hw->abort_cmds++; }
#endif
