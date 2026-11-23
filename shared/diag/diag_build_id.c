/*
 * shared/diag: the build id (SPEC.md D2). See include/diag_build_id.h.
 *
 * NOT esp_app_get_elf_sha256(): it copies app_elf_sha256_str, which holds only
 * CONFIG_APP_RETRIEVE_LEN_ELF_SHA characters, a prefix kept for the panic
 * handler, not the hash.
 *
 * Read through a volatile pointer, as ESP-IDF's own conversion does
 * (esp_app_format/esp_app_desc.c, esp_app_format_init_elf_sha256()): the
 * descriptor is a const object whose initializer is a placeholder that
 * esptool overwrites in the image, so a compiler that can see the initializer
 * may fold the read to the placeholder.
 */
#include "diag_build_id.h"

#include "esp_app_desc.h"

uint32_t diag_build_id(void)
{
    return diag_build_id_from(esp_app_get_description()->app_elf_sha256);
}
