/* Minimal host esp_rom_sys.h for builds on the twai_port fake with no stand-in
 * of their own (test/README.md, "Headers"): esp_rom_delay_us(), as ESP-IDF
 * v5.4.1 declares it (components/esp_rom/include/esp_rom_sys.h). twai_fake.c
 * implements it on the fake's clock, so a busy wait in the code under test
 * moves time as it does on the chip (twai_fake.h, "Busy waits"). */
#ifndef TWAI_FAKE_HOST_ESP_ROM_SYS_H
#define TWAI_FAKE_HOST_ESP_ROM_SYS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
void esp_rom_delay_us(uint32_t us);
#ifdef __cplusplus
}
#endif

#endif
