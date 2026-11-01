/*
 * soc/soc.h for host builds on the twai_port fake (SPEC.md T10, T12).
 *
 * Only what twai_port's register file reaches for: BIT() and REG_READ().
 * ESP-IDF v5.4.1's REG_READ (components/soc/esp32c3/include/soc/soc.h)
 * dereferences the address; here it is a function of the fake, which
 * answers for the two system registers it models (soc/system_reg.h) and
 * counts every other address read (twai_fake_reg_unknown_reads()).
 */
#ifndef TWAI_FAKE_SOC_SOC_H
#define TWAI_FAKE_SOC_SOC_H

#include <stdint.h>

#include "soc/reg_base.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifndef BIT
#define BIT(nr) (1UL << (nr))
#endif

uint32_t twai_fake_reg_read(uint32_t addr);
#define REG_READ(_r) twai_fake_reg_read((uint32_t)(_r))

#ifdef __cplusplus
}
#endif

#endif /* TWAI_FAKE_SOC_SOC_H */
