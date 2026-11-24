/*
 * A host stand-in for ESP-IDF's esp_app_desc.h, for diag_build_id.c in the
 * tester's driver (../driver.c): only the field D2 reads. The driver defines
 * esp_app_get_description() over a descriptor whose hash it sets.
 * shared/diag tester, 2026-11-23.
 */
#pragma once

#include <stdint.h>

typedef struct {
    uint8_t app_elf_sha256[32];
} esp_app_desc_t;

const esp_app_desc_t *esp_app_get_description(void);
