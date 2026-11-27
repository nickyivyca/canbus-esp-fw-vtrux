/* Host stand-in for the esp32s3's soc/rtc_cntl_reg.h (ESP-IDF v5.4.2): the
 * two registers that hold a core's stall code, and its fields' shifts. The
 * register addresses are tokens stub_reg_read() tells apart. */
#ifndef MOCK_SOC_RTC_CNTL_REG_H
#define MOCK_SOC_RTC_CNTL_REG_H
#define RTC_CNTL_OPTIONS0_REG           0x3u
#define RTC_CNTL_SW_CPU_STALL_REG       0x4u
#define RTC_CNTL_SW_STALL_PROCPU_C0_S   2
#define RTC_CNTL_SW_STALL_APPCPU_C0_S   0
#define RTC_CNTL_SW_STALL_PROCPU_C1_S   26
#define RTC_CNTL_SW_STALL_APPCPU_C1_S   20
#endif
