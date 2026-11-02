/* Host stand-in for esp_rom_sys.h: the restart stop's 2 us wait (T12),
 * recorded by the stub, which may change the scripted status at a given
 * wait (stub.h). */
#ifndef MOCK_ESP_ROM_SYS_H
#define MOCK_ESP_ROM_SYS_H
#include <stdint.h>
void esp_rom_delay_us(uint32_t us);
#endif
