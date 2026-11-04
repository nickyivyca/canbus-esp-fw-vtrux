// Just enough of ESP-IDF's app description for `main.cpp` to compile and
// link on the host.
//
// `buildId()` takes the first four bytes of `app_elf_sha256` and puts them
// in the 0x7F7 diagnostic frame, so the host build needs SOMETHING there.
// It is deliberately a fixed, obviously-synthetic value rather than a
// plausible-looking hash: a test that asserted on a real-looking build id
// would be asserting on the host toolchain, and an L2 run must never be
// mistaken for evidence about what is actually flashed to the board.

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  uint32_t magic_word;
  uint32_t secure_version;
  uint32_t reserv1[2];
  char version[32];
  char project_name[32];
  char time[16];
  char date[16];
  char idf_ver[32];
  uint8_t app_elf_sha256[32];
  uint32_t reserv2[20];
} esp_app_desc_t;

const esp_app_desc_t* esp_app_get_description(void);

// The value the host build reports, so a test can name it rather than
// hard-coding the same magic number in two places.
#define HOST_L2_BUILD_ID 0xD15EA5E0u

#ifdef __cplusplus
}
#endif
