/*
 * The tester's driver for shared/diag's C side (verify_diag.py builds it once
 * per generated table). Built with -DTBL_H="<p>.h" -DTBL=<p>_messages
 * -DTBL_N=<p>_MESSAGE_COUNT -DCONSTS_INC="<consts file>", the table's .c, and
 * the shared/diag under test's diag_pack.c and diag_build_id.c.
 *
 * Commands on stdin, one per line; one answer line each on stdout:
 *   table                      M <name> <id> <extended> <dlc> <n>, then for each
 *                              signal S <name> <start> <length> <big_endian>
 *                              <signed> <mux>; then END
 *   consts                     the header's per-signal constants (CONSTS_INC):
 *                              C <msg>.<sig> <BITS> <SIGNED> <index>, and
 *                              I <msg> <ID> <index>; then END
 *   pack <m> <v0> ... <vn-1>   diag_pack_message(): R <result> <mask hex> <bytes hex>
 *   range <m> <s>              G <diag_signal_min> <diag_signal_max>
 *   buildid <64 hex>           B <diag_build_id_from %08x> <diag_build_id() %08x>,
 *                              the descriptor's hash set to the bytes first
 *
 * shared/diag tester, 2026-11-23.
 */
#define _POSIX_C_SOURCE 200809L     /* strtok_r under -std=c11 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "diag_pack.h"
#include "diag_build_id.h"
#include "esp_app_desc.h"
#include TBL_H

static esp_app_desc_t g_desc;

const esp_app_desc_t *esp_app_get_description(void)
{
    return &g_desc;
}

static void consts_dump(void)
{
#include CONSTS_INC
}

static void hexout(const uint8_t *b, int n)
{
    for (int i = 0; i < n; i++)
        printf("%02x", b[i]);
}

int main(void)
{
    char line[8192];
    while (fgets(line, sizeof line, stdin))
    {
        char *save = NULL;
        char *cmd = strtok_r(line, " \n", &save);
        if (!cmd)
            continue;
        if (!strcmp(cmd, "table"))
        {
            for (int m = 0; m < (int)TBL_N; m++)
            {
                const diag_message_t *msg = &TBL[m];
                printf("M %s %" PRIu32 " %d %u %u\n", msg->name, msg->id, msg->extended ? 1 : 0,
                       (unsigned)msg->dlc, (unsigned)msg->nsignals);
                for (int s = 0; s < msg->nsignals; s++)
                {
                    const diag_signal_t *sg = &msg->signals[s];
                    printf("S %s %u %u %d %d %d\n", sg->name, (unsigned)sg->start, (unsigned)sg->length,
                           sg->big_endian ? 1 : 0, sg->is_signed ? 1 : 0, (int)sg->mux);
                }
            }
            printf("END\n");
        }
        else if (!strcmp(cmd, "consts"))
        {
            consts_dump();
            printf("END\n");
        }
        else if (!strcmp(cmd, "pack"))
        {
            int m = atoi(strtok_r(NULL, " \n", &save));
            const diag_message_t *msg = &TBL[m];
            int64_t raw[64] = { 0 };
            for (int i = 0; i < msg->nsignals && i < 64; i++)
            {
                char *v = strtok_r(NULL, " \n", &save);
                raw[i] = v ? strtoll(v, NULL, 10) : 0;
            }
            uint8_t out[DIAG_MAX_DLC];
            memset(out, 0xA5, sizeof out);   /* not zeroed: the packer must zero it */
            uint64_t mask = 0xDEADBEEFu;
            int r = diag_pack_message(msg, raw, out, &mask);
            printf("R %d %016" PRIx64 " ", r, mask);
            hexout(out, msg->dlc > DIAG_MAX_DLC ? DIAG_MAX_DLC : msg->dlc);
            printf("\n");
        }
        else if (!strcmp(cmd, "range"))
        {
            int m = atoi(strtok_r(NULL, " \n", &save));
            int s = atoi(strtok_r(NULL, " \n", &save));
            const diag_signal_t *sg = &TBL[m].signals[s];
            printf("G %" PRId64 " %" PRId64 "\n", diag_signal_min(sg), diag_signal_max(sg));
        }
        else if (!strcmp(cmd, "buildid"))
        {
            char *h = strtok_r(NULL, " \n", &save);
            for (int i = 0; i < 32; i++)
            {
                char two[3] = { h[2 * i], h[2 * i + 1], 0 };
                g_desc.app_elf_sha256[i] = (uint8_t)strtoul(two, NULL, 16);
            }
            printf("B %08" PRIx32 " %08" PRIx32 "\n", diag_build_id_from(g_desc.app_elf_sha256), diag_build_id());
        }
        else
        {
            printf("? %s\n", cmd);
        }
        fflush(stdout);
    }
    return 0;
}
