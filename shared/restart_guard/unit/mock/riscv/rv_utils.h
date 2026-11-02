/* Host stand-in for riscv/rv_utils.h: the global interrupt disable, which the
 * test records in its call log. */
#ifndef MOCK_RV_UTILS_H
#define MOCK_RV_UTILS_H
void rv_utils_intr_global_disable(void);
#endif
