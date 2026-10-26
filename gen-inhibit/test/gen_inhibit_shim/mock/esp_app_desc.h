/*
 * Mock esp_app_desc.h. gen_inhibit.c reads app_elf_sha256 for the page's
 * elf_sha256 (spec 11) and for 0x7F3's build id (spec 10, case 59). version is
 * not read by the shim build; "unknown" stays as a plain placeholder. Only the
 * fields read are mocked; their sizes are the real ones (ESP-IDF v5.4.1
 * esp_app_desc.h).
 */
#ifndef MOCK_ESP_APP_DESC_H
#define MOCK_ESP_APP_DESC_H

#include <stdint.h>

typedef struct {
    char version[32];
    uint8_t app_elf_sha256[32];
} esp_app_desc_t;

/*
 * The ELF SHA-256 the page reports (spec 11, "The page names the binary it
 * runs") and whose first four bytes 0x7F3 carries (spec 10). Chosen so a
 * formatting slip shows: a leading zero byte (dropped by an unpadded %x), both
 * nibbles above 9 (case), and no two bytes alike in a way that would hide a
 * byte-order swap. Cases 46 and 59 hold the expected text and bytes as
 * literals, so they do not share this file's arithmetic.
 */
#define MOCK_ELF_SHA256 { \
    0x00, 0x0f, 0xf0, 0xff, 0x01, 0x23, 0x45, 0x67, \
    0x89, 0xab, 0xcd, 0xef, 0x10, 0x32, 0x54, 0x76, \
    0x98, 0xba, 0xdc, 0xfe, 0xa5, 0x5a, 0x3c, 0xc3, \
    0x7e, 0xe7, 0x08, 0x80, 0x9d, 0xd9, 0xc0, 0x0c }

static inline const esp_app_desc_t *esp_app_get_description(void)
{
    static const esp_app_desc_t desc = {
        .version = "unknown",
        .app_elf_sha256 = MOCK_ELF_SHA256,
    };
    return &desc;
}

#endif
