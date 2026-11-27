/* Host stand-in for esp_hw_support's esp_cpu.h, for the S3 restart stop's
 * stall of the other core (T12): the core id the case sets, and each stall
 * recorded (stub.h). */
#ifndef MOCK_ESP_CPU_H
#define MOCK_ESP_CPU_H
int esp_cpu_get_core_id(void);
void esp_cpu_stall(int core_id);
#endif
