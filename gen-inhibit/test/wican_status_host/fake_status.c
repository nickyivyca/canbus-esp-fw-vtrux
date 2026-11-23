/*
 * The fake behind the status page's host build (shared/wican W15). Gen-inhibit
 * tester. See fake_status.h.
 *
 *     status_host <version>
 *
 * writes <version> into the app descriptor's version field, runs the handler once,
 * and prints its record on three lines:
 *     RET <the handler's return> SENDS <httpd_resp_send calls> LEN <body bytes>
 *     TYPE <the content type set, or ->
 *     BODYHEX <the body sent, hex>
 * run_status_host.py parses the body as JSON and judges it.
 * Exit 2 if the version does not fit the 32-byte field.
 */
#include "fake_status.h"

#include <stdarg.h>

int status_entry(httpd_req_t *r);       /* the generated handler.c */

static esp_app_desc_t desc;
static const esp_partition_t running = {0, 0x10, 0x10000};
static char *sent;
static long sent_len;
static int sends;
static char type[64] = "-";

void fake_log(const char *tag, const char *fmt, ...)
{
    (void)tag;
    va_list ap;
    va_start(ap, fmt);
    char buf[256];
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
}

const esp_app_desc_t *esp_app_get_description(void) { return &desc; }
const esp_partition_t *esp_ota_get_running_partition(void) { return &running; }

esp_err_t esp_ota_get_partition_description(const esp_partition_t *p, esp_app_desc_t *out)
{
    if (p != &running || !out) return ESP_FAIL;
    memcpy(out, &desc, sizeof desc);
    return ESP_OK;
}

esp_err_t httpd_resp_set_type(httpd_req_t *r, const char *t)
{
    (void)r;
    snprintf(type, sizeof type, "%s", t ? t : "(null)");
    return ESP_OK;
}

esp_err_t httpd_resp_send(httpd_req_t *r, const char *buf, ssize_t len)
{
    (void)r;
    long n = buf ? (len == HTTPD_RESP_USE_STRLEN ? (long)strlen(buf) : (long)len) : 0;
    char *grown = realloc(sent, (size_t)(sent_len + n + 1));
    if (!grown) abort();
    sent = grown;
    if (n) memcpy(sent + sent_len, buf, (size_t)n);
    sent_len += n;
    sent[sent_len] = 0;
    sends++;
    return ESP_OK;
}

void config_server_get_sta_ip(char *ip) { strcpy(ip, "fake-sta-ip"); }
char *board_hw_name(void) { return "fake-board"; }
bool wifi_network_is_connected(void) { return false; }
char *wc_mdns_get_hostname(void) { return "fake-mdns"; }
int can_get_bitrate(void) { return 0; }
int8_t sleep_mode_get_voltage(float *val) { *val = 12.5f; return 0; }
bool autopid_get_ecu_status(void) { return false; }

int main(int argc, char **argv)
{
    if (argc != 2 || strlen(argv[1]) >= sizeof desc.version) {
        fprintf(stderr, "usage: status_host <version, at most %zu chars>\n", sizeof desc.version - 1);
        return 2;
    }
    desc.magic_word = 0xABCD5432;
    strncpy(desc.version, argv[1], sizeof desc.version - 1);
    strncpy(desc.project_name, "fake-project", sizeof desc.project_name - 1);
    httpd_req_t req = {.uri = "/check_status"};
    int ret = status_entry(&req);
    printf("RET %d SENDS %d LEN %ld\n", ret, sends, sent_len);
    printf("TYPE %s\n", type);
    printf("BODYHEX ");
    for (long i = 0; i < sent_len; i++) printf("%02x", (unsigned char)sent[i]);
    printf("\n");
    free(sent);
    return 0;
}
