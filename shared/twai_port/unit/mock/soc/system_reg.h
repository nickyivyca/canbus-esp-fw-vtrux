/* Host stand-in for the esp32c3's soc/system_reg.h: the TWAI clock-enable
 * and reset bits, ESP-IDF 5.4.1's (bit 19 of each). The register addresses
 * are tokens stub_reg_read() tells apart. */
#ifndef MOCK_SOC_SYSTEM_REG_H
#define MOCK_SOC_SYSTEM_REG_H
#define SYSTEM_PERIP_CLK_EN0_REG    0x1u
#define SYSTEM_PERIP_RST_EN0_REG    0x2u
#define SYSTEM_TWAI_CLK_EN          (1u << 19)
#define SYSTEM_TWAI_RST             (1u << 19)
#endif
