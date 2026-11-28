/*
 * soc/rtc_cntl_reg.h for host builds on the twai_port fake: the ESP32-S3's
 * two registers that hold a core's stall code, and the shifts of its fields,
 * for the restart stop on a chip with two cores (SPEC.md T12, added
 * 2026-11-27). Addresses and shifts are ESP-IDF v5.4.2's
 * (components/soc/esp32s3/register/soc/rtc_cntl_reg.h and reg_base.h).
 * esp_cpu_stall() writes 2 into field C0 of RTC_CNTL_OPTIONS0_REG and 0x21
 * into field C1 of RTC_CNTL_SW_CPU_STALL_REG (hal/esp32s3
 * cpu_utility_ll.h); REG_READ() of either register is the fake's, which
 * gives those fields from its model of the stalled cores.
 */
#ifndef TWAI_FAKE_SOC_RTC_CNTL_REG_H
#define TWAI_FAKE_SOC_RTC_CNTL_REG_H

#define DR_REG_RTCCNTL_BASE             0x60008000
#define RTC_CNTL_OPTIONS0_REG           (DR_REG_RTCCNTL_BASE + 0x0)
#define RTC_CNTL_SW_CPU_STALL_REG       (DR_REG_RTCCNTL_BASE + 0xBC)
#define RTC_CNTL_SW_STALL_PROCPU_C0_S   2
#define RTC_CNTL_SW_STALL_APPCPU_C0_S   0
#define RTC_CNTL_SW_STALL_PROCPU_C1_S   26
#define RTC_CNTL_SW_STALL_APPCPU_C1_S   20

#endif /* TWAI_FAKE_SOC_RTC_CNTL_REG_H */
