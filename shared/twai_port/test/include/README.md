# test/include/

The ESP-IDF v5.4.1 TWAI headers for host builds on the fake: `driver/twai.h`
and `hal/twai_ll.h`, and for the restart stop (T12) `soc/soc.h`,
`soc/reg_base.h` and `soc/system_reg.h` (the TWAI's clock-enable and reset
bits), laid out as ESP-IDF lays them out, so a source's `#include` lines
resolve unchanged. What each holds, and where it came from, is in its header
comment and in `../README.md`. Their ESP-IDF sources are byte-identical in
v5.4.2 (`../README.md`, "ESP-IDF v5.4.2").
