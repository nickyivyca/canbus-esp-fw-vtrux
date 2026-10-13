/*
 * gi_restart_test -- TEST BUILD ONLY. See gi_restart_test.h for why it exists,
 * why it is a separate source, and the interface.
 *
 * THREE VIEWS OF ONE RESTART, so that spec 3's "both stay that way until the
 * driver is installed again" is read at both ends rather than inferred:
 *
 *   last_restart    what the hook did as the CPU went down (its record, from
 *                   RTC memory: gi_restart_quiesce.c)
 *   at_app_start    the controller and CAN_STDBY as the new boot found them,
 *                   read by a constructor -- before app_main, so before
 *                   main.c:432-434 writes CAN_STDBY and before any driver
 *                   install
 *   pre_install     the same, immediately before the first
 *                   twai_driver_install() of this boot (can_enable), or null
 *                   if there has been none -- mode 0 never installs one
 *
 * After a restart that took the controller off the bus, at_app_start and
 * pre_install should read twai_in_reset 1 and stdby_out 1. After a power-on the
 * clock is off and the peripheral is held in reset (clk.c:237), and the
 * controller's registers are not read.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/twai_ll.h"
#include "soc/soc.h"
#include "soc/system_reg.h"
#include "soc/gpio_reg.h"

#include "hw_config.h"
#include "gi_restart_quiesce.h"
#include "gi_restart_test.h"

#ifndef GIT_SHA
#define GIT_SHA "unknown"
#endif

static const char *TAG = "gi_restart_test";

#define CRASH_MS 300

typedef struct {
    bool     taken;
    int64_t  t_us;          /* esp_timer time it was taken; 0 at app start */
    uint32_t stdby_out;
    uint32_t stdby_oe;
    uint32_t twai_clk;
    uint32_t twai_rst;
    uint32_t in_reset;
    uint32_t status;
    uint32_t tec;
} snap_t;

static gi_restart_record_t s_last;
static bool                s_last_valid;
static snap_t              s_app_start;
static snap_t              s_pre_install;

static void take_snap(snap_t *s, int64_t t_us)
{
    s->taken     = true;
    s->t_us      = t_us;
    s->stdby_out = (REG_READ(GPIO_OUT_REG) >> CAN_STDBY_GPIO_NUM) & 1u;
    s->stdby_oe  = (REG_READ(GPIO_ENABLE_REG) >> CAN_STDBY_GPIO_NUM) & 1u;
    s->twai_clk  = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) ? 1u : 0u;
    s->twai_rst  = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) ? 1u : 0u;
    s->in_reset  = 0;
    s->status    = 0;
    s->tec       = 0;
    if (s->twai_clk && !s->twai_rst)
    {
        twai_dev_t *hw = TWAI_LL_GET_HW(0);
        s->in_reset = twai_ll_is_in_reset_mode(hw) ? 1u : 0u;
        s->status   = twai_ll_get_status(hw);
        s->tec      = twai_ll_get_tec(hw);
    }
}

static void __attribute__((constructor)) app_start_snapshot(void)
{
    take_snap(&s_app_start, 0);

    s_last_valid = gi_restart_record_valid();
    if (s_last_valid)
    {
        gi_restart_record_get(&s_last);
    }
    else
    {
        /*
         * A power-on, or a restart from an image without the hook. Start the
         * count at 0, so the first restart after it reads entries 1. From here
         * on the record is valid, so `last_restart` is the field that says
         * whether this boot found one -- not the count.
         */
        gi_restart_record_init();
    }
}

void gi_restart_test_pre_install(void)
{
    if (!s_pre_install.taken)
    {
        take_snap(&s_pre_install, esp_timer_get_time());
    }
}

static const char *reset_name(esp_reset_reason_t r)
{
    switch (r)
    {
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXT";
    case ESP_RST_SW:        return "SW";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";
    case ESP_RST_USB:       return "USB";
    case ESP_RST_JTAG:      return "JTAG";
    default:                return "OTHER";
    }
}

static const char *part_label(const esp_partition_t *p)
{
    return p != NULL ? p->label : "?";
}

static int snap_json(const snap_t *s, char *b, size_t cap)
{
    if (!s->taken)
    {
        return snprintf(b, cap, "null");
    }
    return snprintf(b, cap,
        "{\"t_ms\":%lld,\"stdby_out\":%lu,\"stdby_oe\":%lu,"
        "\"twai_clk\":%lu,\"twai_rst\":%lu,\"twai_in_reset\":%lu,"
        "\"twai_status\":\"0x%03lx\",\"tec\":%lu}",
        (long long)(s->t_us / 1000),
        (unsigned long)s->stdby_out, (unsigned long)s->stdby_oe,
        (unsigned long)s->twai_clk, (unsigned long)s->twai_rst,
        (unsigned long)s->in_reset, (unsigned long)s->status,
        (unsigned long)s->tec);
}

/*
 * Safe as a static for the reason the /gen_inhibit handlers' buffers are: one
 * httpd instance serves one request at a time (see the comment at the snapshot
 * statics in gen_inhibit.c).
 */
static char s_out[1536];

#define ADD(...)                                                        \
    do {                                                                \
        if (n < 0 || (size_t)n >= cap) { return -1; }                   \
        n += snprintf(b + n, cap - (size_t)n, __VA_ARGS__);             \
    } while (0)

static int page_json(char *b, size_t cap)
{
    const esp_partition_t *run  = esp_ota_get_running_partition();
    const esp_partition_t *boot = esp_ota_get_boot_partition();
    const esp_reset_reason_t why = esp_reset_reason();
    const gi_restart_record_t *l = &s_last;
    int n = 0;

    ADD("{\"build\":\"restart-test\",\"git_version\":\"%s\","
        "\"app_version\":\"%s\",\"ota_running\":\"%s\","
        "\"ota_running_addr\":\"0x%06lx\",\"ota_boot\":\"%s\","
        "\"reset_reason\":\"%s\",\"reset_reason_n\":%d,",
        GIT_SHA, esp_app_get_description()->version, part_label(run),
        run != NULL ? (unsigned long)run->address : 0ul, part_label(boot),
        reset_name(why), (int)why);

    if (s_last_valid)
    {
        ADD("\"last_restart\":{\"entries\":%lu,"
            "\"twai_clk\":%lu,\"twai_rst\":%lu,\"in_reset_at_entry\":%lu,"
            "\"status_at_entry\":\"0x%03lx\",\"tec\":%lu,\"rec\":%lu,"
            "\"did_twai\":%s,\"status_at_reset\":\"0x%03lx\","
            "\"wait_us\":%lu,\"timed_out\":%s,\"did_stdby\":%s},",
            (unsigned long)l->entries,
            (unsigned long)l->twai_clk, (unsigned long)l->twai_rst,
            (unsigned long)l->in_reset, (unsigned long)l->st_entry,
            (unsigned long)l->tec, (unsigned long)l->rec,
            l->did_twai ? "true" : "false", (unsigned long)l->st_reset,
            (unsigned long)l->wait_us, l->timed_out ? "true" : "false",
            l->did_stdby ? "true" : "false");
    }
    else
    {
        ADD("\"last_restart\":null,");
    }

    ADD("\"at_app_start\":");
    if (n < 0 || (size_t)n >= cap) { return -1; }
    n += snap_json(&s_app_start, b + n, cap - (size_t)n);
    ADD(",\"pre_install\":");
    if (n < 0 || (size_t)n >= cap) { return -1; }
    n += snap_json(&s_pre_install, b + n, cap - (size_t)n);
    ADD("}\n");
    if ((size_t)n >= cap) { return -1; }
    return n;
}

static esp_err_t get_handler(httpd_req_t *req)
{
    const int n = page_json(s_out, sizeof(s_out));
    if (n < 0)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "gi_quiesce page did not fit its buffer");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, s_out, n);
}

static void crash_task(void *arg)
{
    const bool use_abort = (arg != NULL);
    vTaskDelay(pdMS_TO_TICKS(CRASH_MS));
    ESP_LOGW(TAG, "test-only crash now (%s)", use_abort ? "abort" : "illegal");
    if (use_abort)
    {
        abort();
    }
    /*
     * An illegal instruction: `unimp` is a write to a read-only CSR, which the
     * C3 traps. If it ever returned there would be no restart, which the rig
     * sees as a cycle with no boot -- a failure that cannot read as a pass.
     */
    __asm__ volatile ("unimp");
    ESP_LOGE(TAG, "the illegal instruction did not trap");
    vTaskDelete(NULL);
}

static esp_err_t post_handler(httpd_req_t *req)
{
    char query[64];
    char val[16];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "crash", val, sizeof(val)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "expected ?crash=illegal|abort");
        return ESP_FAIL;
    }
    const bool use_abort = (strcmp(val, "abort") == 0);
    if (!use_abort && strcmp(val, "illegal") != 0)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "crash must be illegal or abort");
        return ESP_FAIL;
    }
    /*
     * Answer first, then crash from a task of its own: the rig gets its 200 and
     * the time it asked, and the fault happens in an ordinary task while the
     * device carries on as it was -- live, if it was armed.
     */
    if (xTaskCreate(crash_task, "gi_crash", 2048,
                    use_abort ? (void *)1 : NULL, 5, NULL) != pdPASS)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "could not start the crash task");
        return ESP_FAIL;
    }
    char out[64];
    const int n = snprintf(out, sizeof(out), "{\"crash\":\"%s\",\"in_ms\":%d}\n",
                           use_abort ? "abort" : "illegal", CRASH_MS);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, out, n);
}

static const httpd_uri_t get_uri = {
    .uri = "/gi_quiesce", .method = HTTP_GET,
    .handler = get_handler, .user_ctx = NULL
};
static const httpd_uri_t post_uri = {
    .uri = "/gi_quiesce", .method = HTTP_POST,
    .handler = post_handler, .user_ctx = NULL
};

esp_err_t gi_restart_test_register(httpd_handle_t server)
{
    ESP_LOGW(TAG, "TEST BUILD: /gi_quiesce is registered, with a crash "
                  "trigger. This image must never be flashed to the truck.");
    esp_err_t e = httpd_register_uri_handler(server, &get_uri);
    if (e == ESP_OK)
    {
        e = httpd_register_uri_handler(server, &post_uri);
    }
    if (e != ESP_OK)
    {
        ESP_LOGE(TAG, "/gi_quiesce registration failed: %s", esp_err_to_name(e));
    }
    return e;
}
