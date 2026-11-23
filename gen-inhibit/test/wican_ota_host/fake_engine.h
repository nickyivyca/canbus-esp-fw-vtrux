/*
 * The case script and the record of the OTA handler's host build (gen-inhibit
 * spec 9.2 items 4-6). Gen-inhibit tester. See fake_idf.c.
 */
#ifndef FAKE_ENGINE_H
#define FAKE_ENGINE_H

#include "fake_idf.h"

#define CHUNK          1436     /* most bytes one receive hands back, a TCP segment's worth */
#define RECV_CALL_CAP  100000   /* a handler still asking after this many receives is a runaway */
#define MAX_INJ        8
#define MAX_BEGINS     8
#define MAX_SESSIONS   8
#define MAX_REPLIES    4

typedef enum { INJ_TIMEOUT, INJ_FAIL, INJ_INVALID, INJ_CLOSE } fake_inj_kind_t;

typedef struct {
    long at;                 /* body offset: injected once this many bytes are handed out */
    fake_inj_kind_t kind;
    int n;                   /* INJ_TIMEOUT: how many in a row, -1 for forever */
} fake_inj_t;

typedef struct {
    const char *name;
    fake_inj_t inj[MAX_INJ];
    int ninj;
    int write_fail_at;       /* the n-th esp_ota_write fails (1-based), 0 for none */
    bool begin_fail;
    bool end_fail;
    bool setboot_fail;
} fake_case_t;

typedef struct {
    const fake_case_t *c;
    int next_inj;
    int inj_left[MAX_INJ];
    char *img, *written_img, *body;
    long img_len, body_len, delivered, written;
    bool write_overrun;
    int recv_calls, timeouts_total, consec, consec_max, recv_past_end;
    bool runaway;
    unsigned long begin_sizes[MAX_BEGINS];
    int nbegin, nsessions, begun, ended, ended_by_abort, ended_by_end, misuse, writes;
    bool open[MAX_SESSIONS];
    bool restart, boot_set, end_ok;
    char status_set[8];
    char replies[MAX_REPLIES][8];
    int nreplies;
} fake_state_t;

long fake_preamble_len(void);
void fake_run(const fake_case_t *c, long img_len, int (*entry)(httpd_req_t *));

#endif
