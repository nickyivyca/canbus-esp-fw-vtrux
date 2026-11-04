// Just enough Arduino to compile the firmware's port drivers on a PC.
//
// Named `Arduino.h` and found via -I so `port_mcp2515.cpp`'s own
// `#include <Arduino.h>` resolves here with no edit to the firmware. The
// driver under test must be the file that ships, not a copy with the
// includes changed.
//
// Everything here is backed by the virtual clock in `host_clock.h`: delay()
// and delayMicroseconds() ADVANCE it rather than sleeping, so a polling
// timeout inside setMode() is reachable in a test and a test can still
// place an event on an exact microsecond.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include "host_clock.h"

#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define INPUT 0
#define INPUT_PULLUP 2
#define MSBFIRST 1
#define SPI_MODE0 0

// Board constants main.cpp uses. LED_BUILTIN is a real GPIO on the
// CAN-X2; here it is just a pin the host pin map can hold.
#define LED_BUILTIN 2
#define HSPI 2

inline uint32_t millis() { return hostclock::millis(); }
inline uint32_t micros() { return (uint32_t)hostclock::micros(); }
inline void delay(uint32_t ms) { hostclock::advance((uint64_t)ms * 1000); }
inline void delayMicroseconds(uint32_t us) { hostclock::advance(us); }

// Pin state, and the one pin that means something: CS. The board wires the
// MCP2515's chip select to a GPIO and the driver toggles it around every
// instruction, so the shim has to turn that into framing on the model.
namespace hostpins {
void set(uint8_t pin, uint8_t level);
void mode(uint8_t pin, uint8_t m);
uint8_t get(uint8_t pin);
}  // namespace hostpins

inline void pinMode(uint8_t pin, uint8_t m) { hostpins::mode(pin, m); }
inline void digitalWrite(uint8_t pin, uint8_t level) {
  hostpins::set(pin, level);
}
inline int digitalRead(uint8_t pin) { return hostpins::get(pin); }

// A Serial that captures, for the one test that reads main.cpp's printed
// lines (test_event_layout, spec 8.3's event-line form). Compiled only
// under HOST_SERIAL_CAPTURE, which run.sh defines for that test alone:
// every other build either defines INTP_NO_SERIAL or never names Serial.
// It provides only what main.cpp calls; println ends with "\r\n" as the
// Arduino core's does.
#ifdef HOST_SERIAL_CAPTURE
#include <stdarg.h>
#include <stdio.h>
#include <string>
class HostSerial {
 public:
  std::string out;
  void setTxBufferSize(size_t) {}
  void begin(unsigned long) {}
  void setTxTimeoutMs(uint32_t) {}
  explicit operator bool() const { return true; }
  int available() { return 0; }
  int read() { return -1; }
  size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    const int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    out += buf;
    return n < 0 ? 0 : (size_t)n;
  }
  size_t print(const char* s) { out += s; return strlen(s); }
  size_t println(const char* s) { out += s; out += "\r\n"; return strlen(s) + 2; }
};
static HostSerial Serial;
#endif
