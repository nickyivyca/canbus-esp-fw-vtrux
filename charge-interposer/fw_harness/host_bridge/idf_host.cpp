// The platform calls the shared vtrux/twai_port makes besides the TWAI
// driver and the board (port step 2b; the driver is the shared fake's,
// the board board_fake.cpp's): ESP-IDF's error names, what ESP_ERROR_CHECK
// does on a failure, logging, and the FreeRTOS tick count. The shared
// fake's own host_platform.c has the same calls, but run.sh does not build
// it: its board answers are not the X2's (board_fake.cpp's are).
//
//   esp_err_to_name()          the name of each code esp_err.h defines
//   host_error_check_failed()  ESP_ERROR_CHECK's failure path. On the
//                              device it prints and restarts; here it
//                              prints a line and counts, in
//                              host_error_checks, for a test to read
//   host_log()                 ESP_LOGE/W/I (esp_log.h): one indented line
//                              each, errors and warnings counted
//   xTaskGetTickCount()        one tick per millisecond (the host
//                              FreeRTOS.h's configTICK_RATE_HZ 1000), on
//                              the virtual clock, hostclock::millis()

#include <stdarg.h>
#include <stdio.h>

#include "esp_err.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "host_clock.h"

extern "C" {

int host_log_e = 0;
int host_log_w = 0;
int host_error_checks = 0;

const char* esp_err_to_name(esp_err_t e) {
  switch (e) {
    case ESP_OK: return "ESP_OK";
    case ESP_FAIL: return "ESP_FAIL";
    case ESP_ERR_NO_MEM: return "ESP_ERR_NO_MEM";
    case ESP_ERR_INVALID_ARG: return "ESP_ERR_INVALID_ARG";
    case ESP_ERR_INVALID_STATE: return "ESP_ERR_INVALID_STATE";
    case ESP_ERR_INVALID_SIZE: return "ESP_ERR_INVALID_SIZE";
    case ESP_ERR_NOT_FOUND: return "ESP_ERR_NOT_FOUND";
    case ESP_ERR_NOT_SUPPORTED: return "ESP_ERR_NOT_SUPPORTED";
    case ESP_ERR_TIMEOUT: return "ESP_ERR_TIMEOUT";
    default: return "ESP_ERR_?";
  }
}

void host_error_check_failed(esp_err_t e, const char* expr) {
  host_error_checks++;
  printf("    ESP_ERROR_CHECK(%s) failed: %s (the device would restart)\n",
         expr, esp_err_to_name(e));
}

void host_log(char level, const char* tag, const char* fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (level == 'E') host_log_e++;
  if (level == 'W') host_log_w++;
  printf("    %8lu %c %s: %s\n", (unsigned long)hostclock::millis(), level,
         tag, buf);
}

TickType_t xTaskGetTickCount(void) {
  return (TickType_t)hostclock::millis();
}

}  // extern "C"
