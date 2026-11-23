/*
 * The cases of the multipart upload handlers' host build (shared/wican W16).
 * Gen-inhibit tester. Each case runs one handler once against the fake client
 * of ../wican_ota_host/fake_idf.c and prints its record; run_upload_host.py
 * judges.
 *
 * Written from W16's words: both handlers look for the first boundary line,
 * the part's Content-Type header and the blank line after it only within the
 * bytes received so far, keep reading until they find them, the buffer is
 * full or the body ends, and only then refuse a start that lacks any of them,
 * with 400, nothing written (for the OTA upload, a failed exit under W12).
 *
 *   upload_host ota|car <case>
 */
#include "fake_engine.h"

#include <stdlib.h>

int ota_entry(httpd_req_t *r);       /* the generated handler.c */
int car_entry(httpd_req_t *r);

#define IMG_LEN 50000L

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: upload_host ota|car <case>\n");
        return 2;
    }
    const long P = fake_preamble_len();                      /* the image starts here */
    const long BL = fake_offset_in_start("Content-Disposition");  /* after the boundary line */
    const long TYPE = fake_offset_in_start("Type:");         /* in the Content-Type line */

    const fake_case_t cases[] = {
        /* the control: a well-formed body in full-size receives */
        {.name = "complete"},
        /* well-formed, the start split across receives: accepted, every byte kept */
        {.name = "split_in_boundary", .inj = {{10, INJ_SPLIT, 0}}, .ninj = 1},
        {.name = "split_after_boundary", .inj = {{BL, INJ_SPLIT, 0}}, .ninj = 1},
        {.name = "split_in_type", .inj = {{TYPE + 2, INJ_SPLIT, 0}}, .ninj = 1},
        {.name = "split_before_blank", .inj = {{P - 2, INJ_SPLIT, 0}}, .ninj = 1},
        {.name = "split_in_blank", .inj = {{P - 1, INJ_SPLIT, 0}}, .ninj = 1},
        {.name = "trickle_1", .trickle = 1, .trickle_until = P + 1},
        {.name = "trickle_3", .trickle = 3, .trickle_until = P + 9},
        /* malformed: refused with 400, nothing written */
        {.name = "no_line", .body = BODY_NO_LINE},
        {.name = "no_type", .body = BODY_NO_TYPE},
        {.name = "no_blank", .body = BODY_NO_BLANK},
        {.name = "ends_early", .body = BODY_ENDS_EARLY},
        /* a start that does not end before the buffer fills */
        {.name = "long_start", .body = BODY_LONG_START},
        {.name = "long_start_trickle", .body = BODY_LONG_START, .trickle = 7, .trickle_until = 7000},
    };
    if (BL <= 10 || TYPE < 0) {
        fprintf(stderr, "the fake's start has no Content-Disposition or Type: (BL %ld, TYPE %ld)\n", BL, TYPE);
        return 2;
    }
    const int n = (int)(sizeof cases / sizeof cases[0]);
    int (*entry)(httpd_req_t *) = strcmp(argv[1], "ota") == 0 ? ota_entry : car_entry;
    const char *uri = strcmp(argv[1], "ota") == 0 ? "/upload/ota.bin" : "/upload/car_data.json";
    for (int i = 0; i < n; i++) {
        if (strcmp(argv[2], cases[i].name) != 0) continue;
        fake_run_uri(&cases[i], IMG_LEN, entry, uri);
        return 0;
    }
    fprintf(stderr, "no case %s\n", argv[2]);
    return 2;
}
