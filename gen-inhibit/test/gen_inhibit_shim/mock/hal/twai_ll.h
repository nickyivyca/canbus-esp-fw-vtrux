/*
 * MOCK of ESP-IDF's private TWAI low-level layer, for the mock-HAL build (E1).
 *
 * Spec 12.3 item 5: the mock-HAL build runs the REAL scheduler code against a model
 * of the single transmit buffer that distinguishes *awaiting arbitration* from
 * *transmitting* and implements abort as measured on the C3. The scheduler's device
 * HAL in gen_inhibit.c reaches those through twai_ll_get_status() and
 * twai_ll_set_cmd_abort_tx(), so the shim has to provide them or it cannot compile
 * the file it exists to test.
 *
 * ONLY WHAT gen_inhibit.c USES. Not a general model of the peripheral: three status
 * bits and one command. Adding more would invite a test to depend on a register this
 * mock has no measurement behind.
 *
 * THE MAPPING onto fake_twai's queue, and why it is faithful enough to be worth
 * having:
 *
 *   queue empty                     TBS set          -- GS_BUF_EMPTY
 *   head present, stalled           TBS clear, TS 0  -- GS_BUF_AWAITING
 *   head present, not stalled       TBS clear, TS 1  -- GS_BUF_TRANSMITTING
 *
 * A stalled frame in fake_twai is one that never completes, which is what a busy
 * bus looks like to the shim -- so it is exactly the awaiting-arbitration case, and
 * ft_stall_id() is how a test asks for it. The abort then behaves as the 2026-09-27
 * measurement found: it removes an AWAITING frame WITHOUT putting it on the wire and
 * reports TX_SUCCESS anyway, and it is a no-op on a transmitting one.
 */
#ifndef MOCK_HAL_TWAI_LL_H
#define MOCK_HAL_TWAI_LL_H

#include <stdint.h>

/* Status-register bit positions, same values as the real hal/twai_ll.h. */
#define TWAI_LL_STATUS_RBS      (0x1 << 0)
#define TWAI_LL_STATUS_DOS      (0x1 << 1)
#define TWAI_LL_STATUS_TBS      (0x1 << 2)
#define TWAI_LL_STATUS_TCS      (0x1 << 3)
#define TWAI_LL_STATUS_RS       (0x1 << 4)
#define TWAI_LL_STATUS_TS       (0x1 << 5)
#define TWAI_LL_STATUS_ES       (0x1 << 6)
#define TWAI_LL_STATUS_BS       (0x1 << 7)

typedef struct twai_dev_s twai_dev_t;

/* One controller, as on the C3. The pointer is never dereferenced by the mock. */
#define TWAI_LL_GET_HW(n) ((twai_dev_t *)(uintptr_t)((n) + 1))

/* Implemented in fake_twai.c, against its queue. */
uint32_t twai_ll_get_status(twai_dev_t *hw);
void     twai_ll_set_cmd_abort_tx(twai_dev_t *hw);

#endif /* MOCK_HAL_TWAI_LL_H */
