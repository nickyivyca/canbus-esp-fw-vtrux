# test/include/soc/

ESP-IDF v5.4.1's `soc/soc.h`, `soc/reg_base.h` and `soc/system_reg.h`, cut
down to what the restart stop (T12) reads: `BIT()`, `REG_READ()` (a call
into the fake) and the TWAI's clock-enable and reset bits. See
`../README.md` and `../../README.md`.
