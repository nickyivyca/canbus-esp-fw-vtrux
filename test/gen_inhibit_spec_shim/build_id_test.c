/*
 * Spec 12.4 "0x7F3 carries the build id", host half, and "the page names the
 * binary", on the mock-HAL shim (port step 0c, diag schema 8; user 2026-10-25).
 *
 * The product (main/gen_inhibit.c and the rest, as shipped) is built against the
 * implementor's fakes, whose app descriptor (mock/esp_app_desc.h) carries a fixed
 * ELF SHA-256, MOCK_ELF_SHA256: that hash is this test's stimulus. What correct
 * looks like comes from the spec, not the product:
 *   - spec 10: 0x7F3 B0-B3 are diag_build_id, the first four bytes of the image
 *     hash, packed as the charge interposer's 0x7F7 build id: a u32 with the
 *     hash's first byte most significant, sent little-endian. So B0-B3 on the
 *     wire are hash[3], hash[2], hash[1], hash[0], and the value printed %08x
 *     reads as the hash's first four bytes in order;
 *   - spec 10: schema 8 states itself in 0x7F3 B6 (diag_schema_ver_build);
 *   - spec 11 / 12.4: the page's elf_sha256 is the image hash, and its first four
 *     bytes are 0x7F3's build id.
 * The rule is worked once more by hand for this descriptor (WANT_WIRE), so the
 * arithmetic below is checked too; if the fakes' hash changes, that check says so.
 *
 * Usage: build_id_test            (one case, "build_id"; RESULT lines as
 *                                  nonode_test prints them)
 * Built by build.sh with NN_TEST=build_id_test.c. Gen-inhibit tester, 2026-10-25.
 */
#include "fake_twai.h"
#include "gen_inhibit.h"
#include "driver/twai.h"
#include "esp_app_desc.h"
#include "can.h"
#include "spec_layer.h"

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_pass, g_bad;

static void result(const char *req, bool ok, const char *detail)
{
    printf("RESULT build_id %s %s %s\n", req, ok ? "PASS" : "FAIL", detail);
    if (ok) g_pass++;
    else g_bad++;
    fflush(stdout);
}

static void hex(const uint8_t *b, int n, char *out)
{
    for (int i = 0; i < n; i++) sprintf(out + 2 * i, "%02x", b[i]);
    out[2 * n] = 0;
}

/* The page's "elf_sha256" value, or "" if it has none. */
static void page_elf(char *out, int n)
{
    static char buf[8192];
    const char *key = "\"elf_sha256\":\"";
    buf[0] = 0;
    out[0] = 0;
    gen_inhibit_get_stats_json(buf, sizeof(buf));
    const char *p = strstr(buf, key);
    if (!p) return;
    p += strlen(key);
    int i = 0;
    while (p[i] && p[i] != '"' && i < n - 1) { out[i] = p[i]; i++; }
    out[i] = 0;
}

int main(int argc, char **argv)
{
    static const uint8_t H[32] = MOCK_ELF_SHA256;          /* the stimulus */
    static const uint8_t WANT_WIRE[4] = { 0xff, 0xf0, 0x0f, 0x00 };   /* by hand, for H */
    const uint8_t want[4] = { H[3], H[2], H[1], H[0] };     /* spec 10's rule */
    char hash_hex[65], want_id[9], d[256];

    if (argc < 2) { puts("build_id"); return 0; }           /* the case list */
    hex(H, 32, hash_hex);
    hex(H, 4, want_id);
    printf("HASH %s\n", hash_hex);

    snprintf(d, sizeof d, "rule gives %02x%02x%02x%02x, by hand %02x%02x%02x%02x",
             want[0], want[1], want[2], want[3], WANT_WIRE[0], WANT_WIRE[1], WANT_WIRE[2], WANT_WIRE[3]);
    result("rule", memcmp(want, WANT_WIRE, 4) == 0, d);

    ft_reset();
    sl_reset(NULL);
    gen_inhibit_init(CAN_500K, 0);
    ft_start_worker();
    ft_run(1000);
    gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
    for (int i = 0; i < 40; i++) ft_run(100000);            /* 4 s: several diag rounds */

    int n = 0, bad_bytes = 0, bad_schema = 0, bad_print = 0, bad_dlc = 0;
    char first[17] = "", printed[9] = "";
    for (int i = 0; i < ft_wire_count(); i++) {
        const ft_frame_t *f = ft_wire(i);
        if (f->id != 0x7F3) continue;
        char row[17];
        hex(f->data, 8, row);
        printf("WIRE7F3 %s\n", row);
        if (!n) strcpy(first, row);
        n++;
        if (f->dlc != 8) bad_dlc++;
        if (memcmp(f->data, want, 4) != 0) bad_bytes++;
        if (f->data[6] != 8) bad_schema++;
        const uint32_t v = (uint32_t)f->data[0] | (uint32_t)f->data[1] << 8 |
                           (uint32_t)f->data[2] << 16 | (uint32_t)f->data[3] << 24;
        snprintf(printed, sizeof printed, "%08x", v);
        if (strcmp(printed, want_id) != 0) bad_print++;
    }
    snprintf(d, sizeof d, "%d on the wire", n);
    result("sent", n > 0, d);
    snprintf(d, sizeof d, "%d of %d differ; first %s, B0-B3 wanted %02x%02x%02x%02x", bad_bytes, n, first,
             want[0], want[1], want[2], want[3]);
    result("bytes", n > 0 && !bad_bytes && !bad_dlc, d);
    snprintf(d, sizeof d, "%d of %d not 8 in B6", bad_schema, n);
    result("schema", n > 0 && !bad_schema, d);
    snprintf(d, sizeof d, "diag_build_id %%08x = %s, the hash's first four bytes %s (%d differ)", printed, want_id,
             bad_print);
    result("printed", n > 0 && !bad_print, d);

    char elf[80];
    page_elf(elf, sizeof elf);
    snprintf(d, sizeof d, "page elf_sha256 %.8s..., 0x7F3 build id %s", elf, printed);
    result("page_id", strlen(elf) == 64 && n > 0 && strncmp(elf, printed, 8) == 0, d);
    snprintf(d, sizeof d, "page elf_sha256 %s %s the descriptor's", elf[0] ? elf : "(none)",
             strcmp(elf, hash_hex) == 0 ? "=" : "!=");
    result("page_hash", strcmp(elf, hash_hex) == 0, d);

    gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
    ft_run(100000);
    ft_stop_worker();
    printf("build_id: %d PASS, %d FAIL\n", g_pass, g_bad);
    return g_bad ? 1 : 0;
}
