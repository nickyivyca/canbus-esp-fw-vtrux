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

/* INJ_SPLIT ends a receive at its offset and injects nothing: the next receive goes on
 * from there, as when the socket holds only part of what was sent (shared/wican W16). */
typedef enum { INJ_TIMEOUT, INJ_FAIL, INJ_INVALID, INJ_CLOSE, INJ_SPLIT } fake_inj_kind_t;

/* The body the client sends (shared/wican W16 for all but BODY_GOOD):
 *   BODY_GOOD        curl -F's form: boundary line, Content-Disposition, Content-Type,
 *                    blank line, the image, the closing boundary;
 *   BODY_NO_LINE     100 bytes with no line break at all, so no boundary line;
 *   BODY_NO_TYPE     BODY_GOOD without its Content-Type line;
 *   BODY_NO_BLANK    BODY_GOOD without the blank line: the image follows the header;
 *   BODY_LONG_START  BODY_GOOD with a 6000-character filename, so the start does not
 *                    end within any buffer smaller than that;
 *   BODY_ENDS_EARLY  the boundary line and Content-Disposition only, and the body ends. */
typedef enum { BODY_GOOD, BODY_NO_LINE, BODY_NO_TYPE, BODY_NO_BLANK, BODY_LONG_START,
               BODY_ENDS_EARLY } fake_body_t;

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
    fake_body_t body;
    int trickle;             /* > 0: at most this many bytes per receive ... */
    long trickle_until;      /* ... while fewer than this many have been handed out */
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
    char *scratch;           /* the handler's receive buffer, if the entry named one */
    size_t scratch_len;
    char *file;              /* the /upload/car_data.json fakes' file (fake_file.c) */
    long file_len;
    int fopens, fwrites, unlinks;
    bool file_open;
} fake_state_t;

extern fake_state_t fake_S;
long fake_preamble_len(void);
long fake_preamble_len_of(fake_body_t body);
long fake_offset_in_start(const char *needle);   /* in BODY_GOOD's start; -1 if absent */
void fake_run(const fake_case_t *c, long img_len, int (*entry)(httpd_req_t *));
void fake_run_uri(const fake_case_t *c, long img_len, int (*entry)(httpd_req_t *), const char *uri);
/* The entry names the handler's receive buffer. While it is set, every receive into it
 * leaves the bytes after the ones it delivered, to the buffer's end, poisoned for ASan,
 * so a read of a byte not received stops the case (shared/wican W16). */
void fake_set_scratch(char *buf, size_t len);

#endif
