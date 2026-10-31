/*
 * hal/twai_ll.h for host builds on the twai_port fake (SPEC.md T8, T10).
 *
 * Only what twai_port's register file reaches for: TWAI_LL_GET_HW(), the
 * status register's bits and twai_ll_get_status(), and
 * twai_ll_set_cmd_abort_tx(). The bit values are ESP-IDF v5.4.1's
 * (components/hal/esp32c3/include/hal/twai_ll.h; the S3's file has the same
 * registers). The real calls are static inline register accesses; here
 * they are functions of the fake, which derives the status from its
 * transmit buffer (twai_fake.c, "The register model").
 */
#ifndef TWAI_FAKE_HAL_TWAI_LL_H
#define TWAI_FAKE_HAL_TWAI_LL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TWAI_LL_STATUS_RBS      (0x1 << 0)
#define TWAI_LL_STATUS_DOS      (0x1 << 1)
#define TWAI_LL_STATUS_TBS      (0x1 << 2)
#define TWAI_LL_STATUS_TCS      (0x1 << 3)
#define TWAI_LL_STATUS_RS       (0x1 << 4)
#define TWAI_LL_STATUS_TS       (0x1 << 5)
#define TWAI_LL_STATUS_ES       (0x1 << 6)
#define TWAI_LL_STATUS_BS       (0x1 << 7)
#define TWAI_LL_STATUS_MS       (0x1 << 8)

typedef struct twai_dev_s twai_dev_t;

/* One controller on both chips (SOC_TWAI_CONTROLLER_NUM 1). The pointer is a
 * token the fake checks, never dereferenced. */
twai_dev_t *twai_fake_ll_hw(int controller_id);
#define TWAI_LL_GET_HW(n) twai_fake_ll_hw(n)

uint32_t twai_ll_get_status(twai_dev_t *hw);
void     twai_ll_set_cmd_abort_tx(twai_dev_t *hw);

#ifdef __cplusplus
}
#endif

#endif /* TWAI_FAKE_HAL_TWAI_LL_H */
