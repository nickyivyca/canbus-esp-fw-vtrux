/* Minimal host esp_cpu.h for builds on the twai_port fake with no stand-in
 * of their own (test/README.md, "Headers"): esp_cpu_stall(),
 * esp_cpu_unstall() and esp_cpu_get_core_id(), as ESP-IDF v5.4.2 declares
 * them (components/esp_hw_support/include/esp_cpu.h; esp_cpu_get_core_id()
 * is an inline there). twai_fake.c implements them on its model of the
 * chip's cores: the restart stop on a chip with two cores stalls the other
 * one first (twai_port T12, added 2026-11-27), and the fake's sender on the
 * other core stops when it is stalled (twai_fake.h, "Two cores"). */
#ifndef TWAI_FAKE_HOST_ESP_CPU_H
#define TWAI_FAKE_HOST_ESP_CPU_H

#ifdef __cplusplus
extern "C" {
#endif
void esp_cpu_stall(int core_id);
void esp_cpu_unstall(int core_id);
int  esp_cpu_get_core_id(void);
#ifdef __cplusplus
}
#endif

#endif
