/*
 * shared/diag: the build id (SPEC.md D2).
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * The first four bytes of the running image's hash
 * (esp_app_get_description()->app_elf_sha256), read at run time, as a u32
 * with hash byte 0 most significant. Packed little-endian into a 32-bit
 * signal, the DBC's unsigned value printed as %08x reads as the hash's first
 * four bytes in order.
 */
uint32_t diag_build_id(void);

/* The same packing from a hash held in memory: what diag_build_id() calls,
 * and a host test's way in. */
static inline uint32_t diag_build_id_from(const volatile uint8_t *b)
{
    return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) |
           ((uint32_t)b[2] << 8)  |  (uint32_t)b[3];
}

#ifdef __cplusplus
}
#endif
