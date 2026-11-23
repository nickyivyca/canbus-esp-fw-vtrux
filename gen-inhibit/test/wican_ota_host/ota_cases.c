/*
 * The cases of the OTA handler's host build (gen-inhibit spec 9.2 items 4-6;
 * shared/wican W12-W14). Gen-inhibit tester. Each case runs the handler once
 * against the script below and prints its record; run_ota_host.py judges.
 *
 * Written from the spec's words: W12, every exit after the update starts but
 * the restart into the new image ends the session; W13, dropped after 4
 * receive timeouts in a row, the count restarting whenever a chunk arrives;
 * W14, the update starts in OTA_WITH_SEQUENTIAL_WRITES. The kinds of failed
 * exit are the ones a client or the flash can cause: a reset, a close, an
 * invalid socket, a stall, a failed write, a failure at the trailing
 * boundary, an image ESP-IDF refuses, and a failed boot-partition switch.
 */
#include "fake_engine.h"

#include <stdlib.h>

int ota_entry(httpd_req_t *r);       /* the generated handler.c */

#define IMG_LEN 50000L

int main(int argc, char **argv)
{
    const long P = fake_preamble_len();
    const long mid = P + IMG_LEN / 2;
    /* The trailing boundary starts here: the last receive of the image body ends at it. */
    const long trailer = P + IMG_LEN;

    const fake_case_t cases[] = {
        {.name = "complete"},
        /* W12: the client's kinds of failed exit, mid-body and at the first byte */
        {.name = "reset_mid", .inj = {{mid, INJ_FAIL, 0}}, .ninj = 1},
        {.name = "close_mid", .inj = {{mid, INJ_CLOSE, 0}}, .ninj = 1},
        {.name = "invalid_mid", .inj = {{mid, INJ_INVALID, 0}}, .ninj = 1},
        {.name = "reset_first", .inj = {{0, INJ_FAIL, 0}}, .ninj = 1},
        {.name = "close_after_preamble", .inj = {{P + 1, INJ_CLOSE, 0}}, .ninj = 1},
        {.name = "reset_at_trailer", .inj = {{trailer, INJ_FAIL, 0}}, .ninj = 1},
        /* W12: the device's kinds */
        {.name = "write_fails", .write_fail_at = 5},
        {.name = "end_refuses", .end_fail = true},
        {.name = "setboot_fails", .setboot_fail = true},
        {.name = "begin_fails", .begin_fail = true},
        /* W13: a stall, mid-body and before any byte of the body */
        {.name = "stall_mid", .inj = {{mid, INJ_TIMEOUT, -1}}, .ninj = 1},
        {.name = "stall_first", .inj = {{0, INJ_TIMEOUT, -1}}, .ninj = 1},
        {.name = "stall_at_trailer", .inj = {{trailer, INJ_TIMEOUT, -1}}, .ninj = 1},
        /* W13: 3 in a row is not 4 */
        {.name = "timeouts3_mid", .inj = {{mid, INJ_TIMEOUT, 3}}, .ninj = 1},
        /* W13: the count restarts whenever a chunk arrives */
        {.name = "timeouts3_x4",
         .inj = {{P + IMG_LEN / 5, INJ_TIMEOUT, 3}, {P + 2 * IMG_LEN / 5, INJ_TIMEOUT, 3},
                 {P + 3 * IMG_LEN / 5, INJ_TIMEOUT, 3}, {P + 4 * IMG_LEN / 5, INJ_TIMEOUT, 3}},
         .ninj = 4},
        {.name = "timeouts2_x2", .inj = {{P + IMG_LEN / 4, INJ_TIMEOUT, 2}, {mid, INJ_TIMEOUT, 2}}, .ninj = 2},
    };
    const int n = (int)(sizeof cases / sizeof cases[0]);
    const char *only = argc > 1 ? argv[1] : NULL;
    for (int i = 0; i < n; i++) {
        if (only && strcmp(only, cases[i].name) != 0) continue;
        fake_run(&cases[i], IMG_LEN, ota_entry);
    }
    return 0;
}
