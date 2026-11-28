/* Host stand-in for test_t19.c: the unit mock's driver/twai.h, and the one
 * alert mask the T19 app uses that it lacks (ESP-IDF v5.4.2's value). */
#ifndef MOCK_T19_DRIVER_TWAI_H
#define MOCK_T19_DRIVER_TWAI_H
#include_next "driver/twai.h"
#ifndef TWAI_ALERT_ALL
#define TWAI_ALERT_ALL 0x0001FFFF
#endif
#endif
