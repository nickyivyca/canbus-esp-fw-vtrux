/*
 * soc/system_reg.h for host builds on the twai_port fake (SPEC.md T10, T12).
 *
 * Only the TWAI's clock-enable and reset bits, which the restart stop reads
 * (T12). Addresses and bits are ESP-IDF v5.4.1's
 * (components/soc/esp32c3/register/soc/system_reg.h). REG_READ() of either
 * register gives the fake's model of the bit and 0 for every other bit
 * (twai_fake.c, "The register model").
 */
#ifndef TWAI_FAKE_SOC_SYSTEM_REG_H
#define TWAI_FAKE_SOC_SYSTEM_REG_H

#include "soc/soc.h"

#define SYSTEM_PERIP_CLK_EN0_REG (DR_REG_SYSTEM_BASE + 0x010)
#define SYSTEM_TWAI_CLK_EN       (BIT(19))
#define SYSTEM_TWAI_CLK_EN_M     (BIT(19))
#define SYSTEM_TWAI_CLK_EN_V     0x1
#define SYSTEM_TWAI_CLK_EN_S     19

#define SYSTEM_PERIP_RST_EN0_REG (DR_REG_SYSTEM_BASE + 0x018)
#define SYSTEM_TWAI_RST          (BIT(19))
#define SYSTEM_TWAI_RST_M        (BIT(19))
#define SYSTEM_TWAI_RST_V        0x1
#define SYSTEM_TWAI_RST_S        19

#endif /* TWAI_FAKE_SOC_SYSTEM_REG_H */
