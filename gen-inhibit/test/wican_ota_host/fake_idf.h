/*
 * The ESP-IDF calls the OTA upload handler makes, faked for a host build
 * (gen-inhibit spec 9.2 items 4-6; shared/wican W12-W14). Gen-inhibit tester.
 *
 * Only the names, types and constants the handler's text uses. The constants
 * are ESP-IDF v5.4.2's own (esp_ota_ops.h, esp_http_server.h), so a handler
 * that passes OTA_SIZE_UNKNOWN or OTA_WITH_SEQUENTIAL_WRITES passes the value
 * ESP-IDF would see. The behaviour is in fake_idf.c, driven by a case's script.
 */
#ifndef FAKE_IDF_H
#define FAKE_IDF_H

#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int esp_err_t;
#define ESP_OK    0
#define ESP_FAIL  -1
#define ESP_ERR_OTA_BASE            0x1500
#define ESP_ERR_OTA_VALIDATE_FAILED (ESP_ERR_OTA_BASE + 0x03)
#define ESP_ERR_NO_MEM              0x101

#define OTA_SIZE_UNKNOWN           0xffffffff
#define OTA_WITH_SEQUENTIAL_WRITES 0xfffffffe

typedef uint32_t esp_ota_handle_t;
typedef struct {
    int type;
    int subtype;
    unsigned long address;
} esp_partition_t;

typedef struct httpd_req {
    const char *uri;
    size_t content_len;
    void *user_ctx;
} httpd_req_t;

typedef enum {
    HTTPD_500_INTERNAL_SERVER_ERROR = 0,
    HTTPD_501_METHOD_NOT_IMPLEMENTED,
    HTTPD_505_VERSION_NOT_SUPPORTED,
    HTTPD_400_BAD_REQUEST,
    HTTPD_401_UNAUTHORIZED,
    HTTPD_403_FORBIDDEN,
    HTTPD_404_NOT_FOUND,
    HTTPD_405_METHOD_NOT_ALLOWED,
    HTTPD_408_REQ_TIMEOUT,
    HTTPD_411_LENGTH_REQUIRED,
    HTTPD_414_URI_TOO_LONG,
    HTTPD_431_REQ_HDR_FIELDS_TOO_LARGE,
    HTTPD_ERR_CODE_MAX
} httpd_err_code_t;

#define HTTPD_SOCK_ERR_FAIL     -1
#define HTTPD_SOCK_ERR_INVALID  -2
#define HTTPD_SOCK_ERR_TIMEOUT  -3

/* Inputs FILE_PATH_MAX and struct file_server_data are written in. */
#define ESP_VFS_PATH_MAX            15
#define CONFIG_SPIFFS_OBJ_NAME_LEN  32

typedef void *TimerHandle_t;
extern TimerHandle_t xrestartTimer;
#define portTICK_PERIOD_MS 1
#ifndef MIN
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#endif

/* Logging goes nowhere; the arguments are still evaluated for type errors. */
#define ESP_LOGE(tag, ...) fake_log(tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) fake_log(tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) fake_log(tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) fake_log(tag, __VA_ARGS__)
void fake_log(const char *tag, const char *fmt, ...);
static const char *TAG = "fake_ota";

int httpd_req_recv(httpd_req_t *r, char *buf, size_t len);
esp_err_t httpd_resp_send_err(httpd_req_t *r, httpd_err_code_t code, const char *msg);
esp_err_t httpd_resp_set_status(httpd_req_t *r, const char *status);
esp_err_t httpd_resp_set_hdr(httpd_req_t *r, const char *field, const char *value);
esp_err_t httpd_resp_sendstr(httpd_req_t *r, const char *str);

const esp_partition_t *esp_ota_get_boot_partition(void);
const esp_partition_t *esp_ota_get_running_partition(void);
const esp_partition_t *esp_ota_get_next_update_partition(const esp_partition_t *start);
esp_err_t esp_ota_begin(const esp_partition_t *p, size_t image_size, esp_ota_handle_t *out);
esp_err_t esp_ota_write(esp_ota_handle_t h, const void *data, size_t size);
esp_err_t esp_ota_end(esp_ota_handle_t h);
esp_err_t esp_ota_abort(esp_ota_handle_t h);
esp_err_t esp_ota_set_boot_partition(const esp_partition_t *p);
const char *esp_err_to_name(esp_err_t e);

int xTimerStart(TimerHandle_t t, int ticks);
void vTaskDelay(int ticks);

const char *get_path_from_uri(char *dest, const char *base_path, const char *uri, size_t destsize);

#endif
