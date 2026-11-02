/* Host stand-in for soc/soc.h: REG_READ of the two system registers the
 * restart stop reads (T12), whose values the case sets (stub.h). */
#ifndef MOCK_SOC_SOC_H
#define MOCK_SOC_SOC_H
#include <stdint.h>
uint32_t stub_reg_read(uint32_t reg);
#define REG_READ(reg) stub_reg_read(reg)
#endif
