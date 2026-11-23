/*
 * The ESP-IDF and component calls the status page's handler makes, faked for a
 * host build (shared/wican W15, gen-inhibit spec 9.2's page). Gen-inhibit tester.
 *
 * The JSON is made by ESP-IDF v5.4.2's own cJSON (components/json/cJSON), so the
 * body is the one the device's cJSON would print. esp_app_desc_t is v5.4.2's
 * layout. The descriptor's version string is the case's input: fake_status.c
 * writes it into the descriptor that esp_app_get_description() and
 * esp_ota_get_partition_description() hand back.
 */
#ifndef FAKE_STATUS_H
#define FAKE_STATUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#include "cJSON.h"

typedef int esp_err_t;
#define ESP_OK    0
#define ESP_FAIL  -1

/* ESP-IDF v5.4.2 esp_app_format/include/esp_app_desc.h */
typedef struct {
    uint32_t magic_word;
    uint32_t secure_version;
    uint32_t reserv1[2];
    char version[32];
    char project_name[32];
    char time[16];
    char date[16];
    char idf_ver[32];
    uint8_t app_elf_sha256[32];
    uint16_t min_efuse_blk_rev_full;
    uint16_t max_efuse_blk_rev_full;
    uint8_t mmu_page_size;
    uint8_t reserv3[3];
    uint32_t reserv2[18];
} esp_app_desc_t;

typedef struct {
    int type;
    int subtype;
    unsigned long address;
} esp_partition_t;

typedef struct httpd_req {
    const char *uri;
    void *user_ctx;
} httpd_req_t;

#define HTTPD_RESP_USE_STRLEN -1

#define ESP_LOGE(tag, ...) fake_log(tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) fake_log(tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) fake_log(tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) fake_log(tag, __VA_ARGS__)
void fake_log(const char *tag, const char *fmt, ...);
static const char *TAG = "fake_status";

const esp_app_desc_t *esp_app_get_description(void);
const esp_partition_t *esp_ota_get_running_partition(void);
esp_err_t esp_ota_get_partition_description(const esp_partition_t *p, esp_app_desc_t *out);

esp_err_t httpd_resp_set_type(httpd_req_t *r, const char *type);
esp_err_t httpd_resp_send(httpd_req_t *r, const char *buf, ssize_t len);

/* The component's own calls the handler makes. */
void config_server_get_sta_ip(char *ip);
char *board_hw_name(void);
bool wifi_network_is_connected(void);
char *wc_mdns_get_hostname(void);
int can_get_bitrate(void);
int8_t sleep_mode_get_voltage(float *val);
bool autopid_get_ecu_status(void);

#endif
