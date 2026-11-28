/* Host stand-in for test_t19.c: the restart calls the T19 app makes. The
 * test defines them. */
#ifndef MOCK_T19_ESP_SYSTEM_H
#define MOCK_T19_ESP_SYSTEM_H
#include <stdlib.h>     /* abort(), which ESP-IDF's headers bring in */
typedef int esp_reset_reason_t;
esp_reset_reason_t esp_reset_reason(void);
void esp_restart(void);
#endif
