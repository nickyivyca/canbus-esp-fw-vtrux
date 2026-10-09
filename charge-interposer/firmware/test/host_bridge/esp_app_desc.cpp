#include "esp_app_desc.h"

#include <string.h>

// A fixed, obviously-synthetic description. The first four bytes of
// app_elf_sha256 are what buildId() reports in the 0x7F7 frame; they spell
// the HOST_L2_BUILD_ID constant so a test can assert on the named value
// rather than on a literal repeated in two files.
extern "C" const esp_app_desc_t* esp_app_get_description(void) {
  static esp_app_desc_t d;
  static bool init = false;
  if (!init) {
    memset(&d, 0, sizeof(d));
    strcpy(d.project_name, "interposer-host-l2");
    strcpy(d.version, "host");
    d.app_elf_sha256[0] = (uint8_t)(HOST_L2_BUILD_ID >> 24);
    d.app_elf_sha256[1] = (uint8_t)(HOST_L2_BUILD_ID >> 16);
    d.app_elf_sha256[2] = (uint8_t)(HOST_L2_BUILD_ID >> 8);
    d.app_elf_sha256[3] = (uint8_t)HOST_L2_BUILD_ID;
    init = true;
  }
  return &d;
}
