/*
 * The fake ESP-IDF behind the OTA upload handler's host build, and the record
 * each case leaves (gen-inhibit spec 9.2 items 4-6; shared/wican W12-W14).
 * Gen-inhibit tester.
 *
 * The client side is a script: a multipart body as `curl -F ota_file=@...`
 * sends it, handed out in chunks of at most CHUNK bytes, with injections at
 * byte offsets of that body: receive timeouts (a count, or forever), a reset
 * (HTTPD_SOCK_ERR_FAIL), an invalid socket (HTTPD_SOCK_ERR_INVALID) or a close
 * (0, the peer's FIN). A failed esp_ota_begin, esp_ota_write, esp_ota_end or
 * esp_ota_set_boot_partition can be injected too.
 *
 * The record says what the handler did with ESP-IDF's update sessions: every
 * esp_ota_begin's image_size argument, every session begun, ended (by
 * esp_ota_abort or esp_ota_end, which frees it whether it passes or fails),
 * still open when the handler returned, and any call on a session not open;
 * the receive timeouts it took, in a row and in all; whether it started the
 * restart timer; the reply it sent; and whether the image written equals the
 * image sent. It prints it as one JSON object per case; run_ota_host.py judges.
 */
#include "fake_idf.h"
#include "fake_engine.h"

#include <stdarg.h>
#include <stdlib.h>

TimerHandle_t xrestartTimer = (TimerHandle_t)0x1;

static const esp_partition_t PART_RUNNING = {0, 0x10, 0x10000};
static const esp_partition_t PART_NEXT = {0, 0x11, 0x1C0000};

static fake_state_t S;

void fake_log(const char *tag, const char *fmt, ...)
{
    (void)tag;
    va_list ap;
    va_start(ap, fmt);
    char sink[512];
    vsnprintf(sink, sizeof sink, fmt, ap);
    va_end(ap);
}

const char *get_path_from_uri(char *dest, const char *base_path, const char *uri, size_t destsize)
{
    (void)base_path;
    snprintf(dest, destsize, "%s", uri);
    return dest;
}

/* ------------------------------------------------------------ the client -- */

static const fake_inj_t *pending(void)
{
    while (S.next_inj < S.c->ninj) {
        const fake_inj_t *in = &S.c->inj[S.next_inj];
        if (in->kind == INJ_TIMEOUT && in->n == 0) {
            S.next_inj++;
            continue;
        }
        return in;
    }
    return NULL;
}

int httpd_req_recv(httpd_req_t *r, char *buf, size_t len)
{
    (void)r;
    if (++S.recv_calls > RECV_CALL_CAP) {
        S.runaway = true;
        return HTTPD_SOCK_ERR_FAIL;
    }
    const fake_inj_t *in = pending();
    if (in && in->at <= S.delivered) {
        switch (in->kind) {
        case INJ_TIMEOUT:
            S.timeouts_total++;
            S.consec++;
            if (S.consec > S.consec_max) S.consec_max = S.consec;
            if (in->n > 0) {
                S.inj_left[S.next_inj]--;
                if (S.inj_left[S.next_inj] == 0) S.next_inj++;
            }
            return HTTPD_SOCK_ERR_TIMEOUT;
        case INJ_FAIL:
            return HTTPD_SOCK_ERR_FAIL;
        case INJ_INVALID:
            return HTTPD_SOCK_ERR_INVALID;
        case INJ_CLOSE:
            return 0;
        }
    }
    if (len == 0) return 0;
    long left = S.body_len - S.delivered;
    if (left <= 0) {
        /* Nothing more from this client: a real socket waits and times out. */
        S.timeouts_total++;
        S.consec++;
        if (S.consec > S.consec_max) S.consec_max = S.consec;
        S.recv_past_end++;
        return HTTPD_SOCK_ERR_TIMEOUT;
    }
    long n = (long)len;
    if (n > CHUNK) n = CHUNK;
    if (n > left) n = left;
    if (in && in->at > S.delivered && in->at - S.delivered < n) n = in->at - S.delivered;
    memcpy(buf, S.body + S.delivered, (size_t)n);
    S.delivered += n;
    S.consec = 0;
    return (int)n;
}

static void reply(const char *status)
{
    if (S.nreplies < MAX_REPLIES) snprintf(S.replies[S.nreplies], sizeof S.replies[0], "%s", status);
    S.nreplies++;
}

esp_err_t httpd_resp_send_err(httpd_req_t *r, httpd_err_code_t code, const char *msg)
{
    (void)r;
    (void)msg;
    switch (code) {
    case HTTPD_400_BAD_REQUEST: reply("400"); break;
    case HTTPD_403_FORBIDDEN: reply("403"); break;
    case HTTPD_404_NOT_FOUND: reply("404"); break;
    case HTTPD_408_REQ_TIMEOUT: reply("408"); break;
    case HTTPD_500_INTERNAL_SERVER_ERROR: reply("500"); break;
    default: reply("other"); break;
    }
    return ESP_OK;
}

esp_err_t httpd_resp_set_status(httpd_req_t *r, const char *status)
{
    (void)r;
    snprintf(S.status_set, sizeof S.status_set, "%.3s", status);
    return ESP_OK;
}

esp_err_t httpd_resp_set_hdr(httpd_req_t *r, const char *field, const char *value)
{
    (void)r;
    (void)field;
    (void)value;
    return ESP_OK;
}

esp_err_t httpd_resp_sendstr(httpd_req_t *r, const char *str)
{
    (void)r;
    (void)str;
    reply(S.status_set[0] ? S.status_set : "200");
    return ESP_OK;
}

/* ------------------------------------------------------- update sessions -- */

const esp_partition_t *esp_ota_get_boot_partition(void) { return &PART_RUNNING; }
const esp_partition_t *esp_ota_get_running_partition(void) { return &PART_RUNNING; }
const esp_partition_t *esp_ota_get_next_update_partition(const esp_partition_t *start)
{
    (void)start;
    return &PART_NEXT;
}

esp_err_t esp_ota_begin(const esp_partition_t *p, size_t image_size, esp_ota_handle_t *out)
{
    if (S.nbegin < MAX_BEGINS) S.begin_sizes[S.nbegin] = (unsigned long)image_size;
    S.nbegin++;
    if (p != &PART_NEXT) S.misuse++;
    if (S.c->begin_fail) return ESP_ERR_NO_MEM;
    if (S.nsessions >= MAX_SESSIONS) return ESP_ERR_NO_MEM;
    S.open[S.nsessions] = true;
    S.nsessions++;
    S.begun++;
    *out = (esp_ota_handle_t)S.nsessions;      /* handles 1.. */
    return ESP_OK;
}

static bool live(esp_ota_handle_t h)
{
    return h >= 1 && h <= (esp_ota_handle_t)S.nsessions && S.open[h - 1];
}

esp_err_t esp_ota_write(esp_ota_handle_t h, const void *data, size_t size)
{
    if (!live(h)) {
        S.misuse++;
        return ESP_FAIL;
    }
    S.writes++;
    if (S.c->write_fail_at > 0 && S.writes == S.c->write_fail_at) return ESP_FAIL;
    if (S.written + (long)size > S.img_len) {
        S.write_overrun = true;
        size = (size_t)(S.img_len - S.written);
    }
    memcpy(S.written_img + S.written, data, size);
    S.written += (long)size;
    return ESP_OK;
}

esp_err_t esp_ota_end(esp_ota_handle_t h)
{
    if (!live(h)) {
        S.misuse++;
        return ESP_FAIL;
    }
    S.open[h - 1] = false;     /* ESP-IDF frees the session whether it passes or fails */
    S.ended++;
    S.ended_by_end++;
    if (S.c->end_fail) return ESP_ERR_OTA_VALIDATE_FAILED;
    if (S.write_overrun || S.written != S.img_len || memcmp(S.written_img, S.img, (size_t)S.img_len) != 0)
        return ESP_ERR_OTA_VALIDATE_FAILED;
    S.end_ok = true;
    return ESP_OK;
}

esp_err_t esp_ota_abort(esp_ota_handle_t h)
{
    if (!live(h)) {
        S.misuse++;
        return ESP_FAIL;
    }
    S.open[h - 1] = false;
    S.ended++;
    S.ended_by_abort++;
    return ESP_OK;
}

esp_err_t esp_ota_set_boot_partition(const esp_partition_t *p)
{
    if (p != &PART_NEXT) S.misuse++;
    if (S.c->setboot_fail) return ESP_FAIL;
    S.boot_set = true;
    return ESP_OK;
}

const char *esp_err_to_name(esp_err_t e)
{
    return e == ESP_OK ? "ESP_OK" : "ESP_ERR";
}

int xTimerStart(TimerHandle_t t, int ticks)
{
    (void)ticks;
    if (t == xrestartTimer) S.restart = true;
    return 1;
}

void vTaskDelay(int ticks) { (void)ticks; }

/* ---------------------------------------------------------------- driver -- */

static const char BOUNDARY[] = "------------------------fakeota0123456789";

long fake_preamble_len(void)
{
    return (long)(2 + strlen(BOUNDARY) + 2
                  + strlen("Content-Disposition: form-data; name=\"ota_file\"; filename=\"ota.bin\"\r\n")
                  + strlen("Content-Type: application/octet-stream\r\n\r\n"));
}

static void build_body(long img_len)
{
    S.img_len = img_len;
    S.img = malloc((size_t)img_len);
    S.written_img = calloc(1, (size_t)img_len);
    uint32_t x = 2463534242u;
    for (long i = 0; i < img_len; i++) {      /* xorshift: any bytes, '\n' and '-' included */
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        S.img[i] = (char)(x & 0xff);
    }
    S.body = malloc((size_t)(img_len + 512));
    long o = 0;
    o += sprintf(S.body + o, "--%s\r\n", BOUNDARY);
    o += sprintf(S.body + o, "Content-Disposition: form-data; name=\"ota_file\"; filename=\"ota.bin\"\r\n");
    o += sprintf(S.body + o, "Content-Type: application/octet-stream\r\n\r\n");
    memcpy(S.body + o, S.img, (size_t)img_len);
    o += img_len;
    o += sprintf(S.body + o, "\r\n--%s--\r\n", BOUNDARY);
    S.body_len = o;
}

static void json_case(const char *name, int ret)
{
    printf("{\"case\": \"%s\", \"ret\": %d, \"begin_sizes\": [", name, ret);
    for (int i = 0; i < S.nbegin && i < MAX_BEGINS; i++) printf("%s%lu", i ? ", " : "", S.begin_sizes[i]);
    int open_now = 0;
    for (int i = 0; i < S.nsessions; i++) open_now += S.open[i] ? 1 : 0;
    printf("], \"begun\": %d, \"ended\": %d, \"ended_by_abort\": %d, \"ended_by_end\": %d, "
           "\"open_at_return\": %d, \"misuse\": %d, \"restart\": %s, \"boot_set\": %s, \"end_ok\": %s, "
           "\"replies\": [",
           S.begun, S.ended, S.ended_by_abort, S.ended_by_end, open_now, S.misuse,
           S.restart ? "true" : "false", S.boot_set ? "true" : "false", S.end_ok ? "true" : "false");
    for (int i = 0; i < S.nreplies && i < MAX_REPLIES; i++) printf("%s\"%s\"", i ? ", " : "", S.replies[i]);
    printf("], \"timeouts_total\": %d, \"consec_at_return\": %d, \"consec_max\": %d, "
           "\"recv_past_end\": %d, \"runaway\": %s, \"delivered\": %ld, \"body_len\": %ld, "
           "\"written\": %ld, \"img_len\": %ld, \"written_ok\": %s}\n",
           S.timeouts_total, S.consec, S.consec_max, S.recv_past_end, S.runaway ? "true" : "false",
           S.delivered, S.body_len, S.written, S.img_len,
           (!S.write_overrun && S.written == S.img_len && memcmp(S.written_img, S.img, (size_t)S.img_len) == 0)
               ? "true" : "false");
    fflush(stdout);
}

void fake_run(const fake_case_t *c, long img_len, int (*entry)(httpd_req_t *))
{
    memset(&S, 0, sizeof S);
    S.c = c;
    for (int i = 0; i < c->ninj; i++) S.inj_left[i] = c->inj[i].n;
    build_body(img_len);
    httpd_req_t req = {"/upload/ota.bin", (size_t)S.body_len, NULL};   /* the entry sets user_ctx */
    int ret = entry(&req);
    json_case(c->name, ret);
    free(S.img);
    free(S.written_img);
    free(S.body);
}
