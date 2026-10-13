/*
 * gi_restart_quiesce -- TRIAL BUILD ONLY. See gi_restart_quiesce.h for why it
 * exists and for the interface.
 *
 * WHAT A RESTART DOES TO THE BUS TODAY (IDF v5.4.1, read 2026-10-12/13).
 * esp_restart() runs the shutdown handlers and calls esp_restart_noos()
 * (esp_system.c:63); a panic calls it from panic_restart() (panic_handler.c:266
 * -- the noos_dig branch above it is ESP32-only). On the C3 that function
 * (port/soc/esp32c3/system_internal.c:64) resets a list of peripherals that
 * does NOT include TWAI, drops the CPU to XTAL at :107, which takes APB from
 * 80 to 40 MHz, and resets the CPU only at :110. Nothing stops or uninstalls
 * the driver. So the controller keeps its configuration, its clock and any
 * pending frame through the reset, now timed from half its clock: half the bit
 * rate on a bus it is still connected to. The burst the tester characterised
 * (107-133 error frames in 24-29 ms, live only) is consistent with that; this
 * build is the test of it.
 *
 * THE HOOK. A linker wrap of esp_restart_noos() (`-Wl,--wrap`, added in
 * main/CMakeLists.txt only under the trial flag). Every caller is in another
 * object -- esp_system.c, panic_handler.c, cpu_start.c, usb_console.c -- so
 * all of them reach __wrap_esp_restart_noos(), which runs before the clock drop
 * and then calls the real function. That covers a crash as well as a requested
 * restart; the shutdown handlers a crash skips come earlier and are not used.
 *
 * WHAT IT DOES, WHEN THE SWITCH IS ON. Both of these, and the order matters:
 *
 *   1. The controller is put in RESET MODE, after the frame it is on (if any)
 *      has finished. Abort first -- that drops frames still waiting and stops
 *      the one on the wire from being retried -- then wait, bounded at 1 ms,
 *      for Transmit Status and Receive Status both to read 0, i.e. for the
 *      bus to be between frames. Entering reset mode in the middle of our own
 *      frame would cut it off, and every other node would flag that: one error
 *      frame of our own making, in a test that counts them. In reset mode the
 *      controller drives nothing, and it stays there through the CPU reset,
 *      the ROM and the bootloader, because nothing resets it until
 *      twai_driver_install() does.
 *
 *   2. The transceiver is put in STANDBY: CAN_STDBY (GPIO6 on this board)
 *      driven 1, which is the level main.c uses for standby. This is the
 *      half that does not depend on the controller's state surviving.
 *      Whether the pin HOLDS through a CPU-only reset is what the boot
 *      snapshot below shows on the bench: from the C3 TRM's reset levels, a
 *      CPU reset resets the CPU core and not the GPIO peripheral, and
 *      esp_restart_noos() itself rewrites only the flash pins' input
 *      routing (:96-101) -- but that is a reading, not a measurement, which is
 *      why the snapshot exists. app_main then writes 1 again (main.c:432-434)
 *      and leaves it there until can_enable() takes it to 0, so on the
 *      intended path the transceiver is in standby from the hook until the
 *      bus is wanted.
 *
 * Both rather than either: they fail differently. Reset mode silences the
 * controller and says nothing about the pin; standby silences the transceiver
 * whatever the controller does. The record says which of them ran.
 *
 * WITH THE SWITCH OFF the hook still runs and still writes its record, and
 * does nothing else. That is the control: it shows the wrap is in the image
 * and was reached, so a burst with the switch off is a burst with the hook
 * present, and a zero with it on is not a zero from an image without one.
 *
 * MODE 0. With the driver not installed the TWAI bus clock is expected to be
 * off -- twai_driver_uninstall() gates it, and it is off from power-on -- and
 * then the controller's registers cannot be read or written. The hook reads
 * the clock-enable and reset bits rather than assuming, skips step 1 when
 * they say the controller is unreachable, and records which. Step 2 still runs.
 *
 * IRAM, AND NOTHING THAT NEEDS THE CACHE. The wrapper and the checksum are in
 * IRAM, and they touch registers, the DRAM switch and the RTC record only.
 * esp_rom_delay_us() is in ROM. Both callers have the cache enabled when they
 * get here, but the real function disables it, and nothing here should depend
 * on where that line is.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_rom_sys.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "riscv/rv_utils.h"
#include "hal/twai_ll.h"
#include "soc/soc.h"
#include "soc/system_reg.h"
#include "soc/gpio_reg.h"

#include "hw_config.h"
#include "gi_restart_quiesce.h"

#ifndef GIT_SHA
#define GIT_SHA "unknown"
#endif

_Static_assert(CAN_STDBY_GPIO_NUM < 32,
               "the hook drives CAN_STDBY through GPIO_OUT_W1TS_REG, which "
               "covers GPIO0-31 only");

static const char *TAG = "gi_quiesce";

#define QZ_MAGIC        0x51E5CE0Du
#define QZ_WAIT_MAX_US  1000u
#define QZ_WAIT_STEP_US 2u
#define QZ_CRASH_MS     300

/*
 * THE RECORD OF THE LAST HOOK ENTRY. In RTC memory with no initialiser, so it
 * survives a software restart and a panic and is garbage after a power-on --
 * which the magic and the checksum tell apart. `entries` keeps counting across
 * restarts, so a reading that moved by exactly one says the hook ran on THIS
 * restart and is not an older record left over.
 */
typedef struct {
    uint32_t magic;
    uint32_t entries;
    uint32_t hook;          /* the switch at entry, 0 or 1 */
    uint32_t twai_clk;      /* TWAI bus clock enabled at entry */
    uint32_t twai_rst;      /* TWAI held in peripheral reset at entry */
    uint32_t in_reset;      /* controller already in reset mode at entry */
    uint32_t st_entry;      /* status register at entry */
    uint32_t tec;
    uint32_t rec;
    uint32_t did_twai;      /* step 1 ran */
    uint32_t st_reset;      /* status register as reset mode was entered */
    uint32_t wait_us;
    uint32_t timed_out;     /* the bus was not between frames within 1 ms */
    uint32_t did_stdby;     /* step 2 ran */
    uint32_t check;
} qz_record_t;

static RTC_NOINIT_ATTR qz_record_t s_rec;

/* The switch. In DRAM, so 0 -- off -- at every boot; it does not persist. */
static volatile uint32_t s_hook;

/* Taken by the constructor at app start, before anything in app_main runs. */
static qz_record_t s_last;
static bool        s_last_valid;
static uint32_t    s_boot_stdby_out;
static uint32_t    s_boot_stdby_oe;
static uint32_t    s_boot_twai_clk;
static uint32_t    s_boot_twai_rst;
static uint32_t    s_boot_in_reset;
static uint32_t    s_boot_st;
static uint32_t    s_boot_tec;

static IRAM_ATTR uint32_t qz_sum(const qz_record_t *r)
{
    const uint32_t *w = (const uint32_t *)r;
    uint32_t x = 0x9E3779B9u;
    for (size_t i = 0; i < offsetof(qz_record_t, check) / sizeof(uint32_t); i++)
    {
        x = (x ^ w[i]) * 0x01000193u;
    }
    return x;
}

static IRAM_ATTR bool qz_valid(const qz_record_t *r)
{
    return r->magic == QZ_MAGIC && r->check == qz_sum(r);
}

void __real_esp_restart_noos(void) __attribute__((noreturn));
void __wrap_esp_restart_noos(void) __attribute__((noreturn));

void IRAM_ATTR __wrap_esp_restart_noos(void)
{
    /*
     * The real function's first act, done here first: the driver's ISR must
     * not run while the controller is being taken off the bus under it.
     */
    rv_utils_intr_global_disable();

    const uint32_t hook = s_hook ? 1u : 0u;
    const uint32_t prev = qz_valid(&s_rec) ? s_rec.entries : 0u;

    qz_record_t r;
    r.magic     = QZ_MAGIC;
    r.entries   = prev + 1u;
    r.hook      = hook;
    r.twai_clk  = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) ? 1u : 0u;
    r.twai_rst  = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) ? 1u : 0u;
    r.in_reset  = 0;
    r.st_entry  = 0;
    r.tec       = 0;
    r.rec       = 0;
    r.did_twai  = 0;
    r.st_reset  = 0;
    r.wait_us   = 0;
    r.timed_out = 0;
    r.did_stdby = 0;

    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    const bool reachable = r.twai_clk && !r.twai_rst;
    if (reachable)
    {
        r.in_reset = twai_ll_is_in_reset_mode(hw) ? 1u : 0u;
        r.st_entry = twai_ll_get_status(hw);
        r.tec      = twai_ll_get_tec(hw);
        r.rec      = twai_ll_get_rec(hw);
    }

    if (hook)
    {
        if (reachable)
        {
            twai_ll_set_cmd_abort_tx(hw);
            uint32_t waited = 0;
            while ((twai_ll_get_status(hw) &
                    (TWAI_LL_STATUS_TS | TWAI_LL_STATUS_RS)) != 0 &&
                   waited < QZ_WAIT_MAX_US)
            {
                esp_rom_delay_us(QZ_WAIT_STEP_US);
                waited += QZ_WAIT_STEP_US;
            }
            r.wait_us   = waited;
            r.timed_out = (waited >= QZ_WAIT_MAX_US) ? 1u : 0u;
            r.st_reset  = twai_ll_get_status(hw);
            twai_ll_enter_reset_mode(hw);
            r.did_twai  = 1;
        }
        REG_WRITE(GPIO_OUT_W1TS_REG, BIT(CAN_STDBY_GPIO_NUM));
        r.did_stdby = 1;
    }

    r.check = qz_sum(&r);
    s_rec = r;

    __real_esp_restart_noos();
}

/*
 * THE BOOT SNAPSHOT, read before app_main -- and so before main.c:432-434
 * writes CAN_STDBY and before the driver is installed. After a hooked restart
 * the pin should still read output-enabled and 1, and the controller should
 * still be in reset mode with its clock on. After a power-on all of it is at
 * its reset value. That difference is the measurement of whether the hook's
 * state survives the CPU reset, the ROM and the bootloader.
 */
static void __attribute__((constructor)) qz_boot_snapshot(void)
{
    s_boot_stdby_out = (REG_READ(GPIO_OUT_REG) >> CAN_STDBY_GPIO_NUM) & 1u;
    s_boot_stdby_oe  = (REG_READ(GPIO_ENABLE_REG) >> CAN_STDBY_GPIO_NUM) & 1u;
    s_boot_twai_clk  = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) ? 1u : 0u;
    s_boot_twai_rst  = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) ? 1u : 0u;
    if (s_boot_twai_clk && !s_boot_twai_rst)
    {
        twai_dev_t *hw = TWAI_LL_GET_HW(0);
        s_boot_in_reset = twai_ll_is_in_reset_mode(hw) ? 1u : 0u;
        s_boot_st       = twai_ll_get_status(hw);
        s_boot_tec      = twai_ll_get_tec(hw);
    }

    s_last_valid = qz_valid(&s_rec);
    if (s_last_valid)
    {
        s_last = s_rec;
    }
    else
    {
        /*
         * A power-on, or a record that is not one. Start the count at zero, so
         * the first hooked restart after it reads 1.
         */
        memset(&s_rec, 0, sizeof(s_rec));
        s_rec.magic = QZ_MAGIC;
        s_rec.check = qz_sum(&s_rec);
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

/*
 * Safe as a static for the reason the /gen_inhibit handlers' buffers are: one
 * httpd instance serves one request at a time (see the comment at the snapshot
 * statics in gen_inhibit.c).
 */
static char s_out[1536];

static int qz_json(char *b, size_t cap)
{
    const esp_partition_t *run  = esp_ota_get_running_partition();
    const esp_partition_t *boot = esp_ota_get_boot_partition();
    const esp_reset_reason_t why = esp_reset_reason();
    const qz_record_t *l = &s_last;

    int n = snprintf(b, cap,
        "{\"trial\":\"restart-quiesce\",\"git_version\":\"%s\","
        "\"app_version\":\"%s\","
        "\"quiesce_hook\":%s,\"default_at_boot\":false,\"persists\":false,"
        "\"ota_running\":\"%s\",\"ota_running_addr\":\"0x%06lx\","
        "\"ota_boot\":\"%s\","
        "\"reset_reason\":\"%s\",\"reset_reason_n\":%d,",
        GIT_SHA, esp_app_get_description()->version,
        s_hook ? "true" : "false",
        part_label(run), run != NULL ? (unsigned long)run->address : 0ul,
        part_label(boot),
        reset_name(why), (int)why);
    if (n < 0 || (size_t)n >= cap) { return -1; }

    if (s_last_valid)
    {
        n += snprintf(b + n, cap - n,
            "\"last_restart\":{\"entries\":%lu,\"hook\":%s,"
            "\"twai_clk\":%lu,\"twai_rst\":%lu,\"in_reset_at_entry\":%lu,"
            "\"status_at_entry\":\"0x%03lx\",\"tec\":%lu,\"rec\":%lu,"
            "\"did_twai\":%s,\"status_at_reset\":\"0x%03lx\","
            "\"wait_us\":%lu,\"timed_out\":%s,\"did_stdby\":%s},",
            (unsigned long)l->entries, l->hook ? "true" : "false",
            (unsigned long)l->twai_clk, (unsigned long)l->twai_rst,
            (unsigned long)l->in_reset, (unsigned long)l->st_entry,
            (unsigned long)l->tec, (unsigned long)l->rec,
            l->did_twai ? "true" : "false", (unsigned long)l->st_reset,
            (unsigned long)l->wait_us, l->timed_out ? "true" : "false",
            l->did_stdby ? "true" : "false");
    }
    else
    {
        n += snprintf(b + n, cap - n, "\"last_restart\":null,");
    }
    if ((size_t)n >= cap) { return -1; }

    n += snprintf(b + n, cap - n,
        "\"at_app_start\":{\"stdby_out\":%lu,\"stdby_oe\":%lu,"
        "\"twai_clk\":%lu,\"twai_rst\":%lu,\"twai_in_reset\":%lu,"
        "\"twai_status\":\"0x%03lx\",\"tec\":%lu}}\n",
        (unsigned long)s_boot_stdby_out, (unsigned long)s_boot_stdby_oe,
        (unsigned long)s_boot_twai_clk, (unsigned long)s_boot_twai_rst,
        (unsigned long)s_boot_in_reset, (unsigned long)s_boot_st,
        (unsigned long)s_boot_tec);
    if ((size_t)n >= cap) { return -1; }
    return n;
}

static esp_err_t qz_send(httpd_req_t *req)
{
    const int n = qz_json(s_out, sizeof(s_out));
    if (n < 0)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "gi_quiesce page did not fit its buffer");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, s_out, n);
}

static esp_err_t qz_get_handler(httpd_req_t *req)
{
    return qz_send(req);
}

static void qz_crash_task(void *arg)
{
    const bool use_abort = (arg != NULL);
    vTaskDelay(pdMS_TO_TICKS(QZ_CRASH_MS));
    ESP_LOGW(TAG, "test-only crash now (%s)", use_abort ? "abort" : "illegal");
    if (use_abort)
    {
        abort();
    }
    /*
     * An illegal instruction: `unimp` is a write to a read-only CSR, which the
     * C3 traps. Deterministic, unlike a store to an address someone believes
     * is unmapped. If it ever returned there would be no restart, which the
     * rig sees as a cycle with no boot -- a failure that cannot read as a pass.
     */
    __asm__ volatile ("unimp");
    ESP_LOGE(TAG, "the illegal instruction did not trap");
    vTaskDelete(NULL);
}

static esp_err_t qz_post_handler(httpd_req_t *req)
{
    char query[64];
    char val[16];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "expected ?hook=1|0|on|off or ?crash=illegal|abort");
        return ESP_FAIL;
    }

    if (httpd_query_key_value(query, "hook", val, sizeof(val)) == ESP_OK)
    {
        if (strcmp(val, "1") == 0 || strcmp(val, "on") == 0)
        {
            s_hook = 1;
        }
        else if (strcmp(val, "0") == 0 || strcmp(val, "off") == 0)
        {
            s_hook = 0;
        }
        else
        {
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                "hook must be 1, 0, on or off");
            return ESP_FAIL;
        }
        ESP_LOGW(TAG, "restart hook %s", s_hook ? "ON" : "OFF");
        return qz_send(req);
    }

    if (httpd_query_key_value(query, "crash", val, sizeof(val)) == ESP_OK)
    {
        const bool use_abort = (strcmp(val, "abort") == 0);
        if (!use_abort && strcmp(val, "illegal") != 0)
        {
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                "crash must be illegal or abort");
            return ESP_FAIL;
        }
        /*
         * Answer first, then crash from a task of its own: the rig gets its
         * 200 and the time it asked, and the fault happens in an ordinary task
         * while the device carries on as it was -- live, if it was armed.
         */
        if (xTaskCreate(qz_crash_task, "qz_crash", 2048,
                        use_abort ? (void *)1 : NULL, 5, NULL) != pdPASS)
        {
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                                "could not start the crash task");
            return ESP_FAIL;
        }
        char out[96];
        const int n = snprintf(out, sizeof(out),
                               "{\"crash\":\"%s\",\"in_ms\":%d,"
                               "\"quiesce_hook\":%s}\n",
                               use_abort ? "abort" : "illegal", QZ_CRASH_MS,
                               s_hook ? "true" : "false");
        httpd_resp_set_type(req, "application/json");
        return httpd_resp_send(req, out, n);
    }

    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                        "expected ?hook=1|0|on|off or ?crash=illegal|abort");
    return ESP_FAIL;
}

int gi_quiesce_append_page(char *page, int n, size_t cap)
{
    /*
     * gen_inhibit_get_stats_json() ends the page "}\n" and returns cap - 1
     * when it truncated, so anything else is a page not to be touched.
     */
    if (page == NULL || n < 2 || (size_t)n >= cap - 1 ||
        page[n - 2] != '}' || page[n - 1] != '\n')
    {
        return n;
    }
    char add[96];
    char ent[16];
    if (qz_valid(&s_rec))
    {
        snprintf(ent, sizeof(ent), "%lu", (unsigned long)s_rec.entries);
    }
    else
    {
        snprintf(ent, sizeof(ent), "null");
    }
    const int m = snprintf(add, sizeof(add),
                           ",\"quiesce_hook\":%s,\"quiesce_entries\":%s,"
                           "\"ota_running\":\"%s\"}\n",
                           s_hook ? "true" : "false", ent,
                           part_label(esp_ota_get_running_partition()));
    if (m < 0 || (size_t)m >= sizeof(add) || (size_t)(n - 2 + m) >= cap)
    {
        return n;
    }
    memcpy(page + n - 2, add, (size_t)m + 1);
    return n - 2 + m;
}

static const httpd_uri_t qz_get_uri = {
    .uri = "/gi_quiesce", .method = HTTP_GET,
    .handler = qz_get_handler, .user_ctx = NULL
};
static const httpd_uri_t qz_post_uri = {
    .uri = "/gi_quiesce", .method = HTTP_POST,
    .handler = qz_post_handler, .user_ctx = NULL
};

esp_err_t gi_quiesce_register(httpd_handle_t server)
{
    ESP_LOGW(TAG, "TRIAL BUILD: /gi_quiesce is registered and esp_restart_noos "
                  "is wrapped. This image must never be flashed to the truck.");
    esp_err_t e = httpd_register_uri_handler(server, &qz_get_uri);
    if (e == ESP_OK)
    {
        e = httpd_register_uri_handler(server, &qz_post_uri);
    }
    if (e != ESP_OK)
    {
        ESP_LOGE(TAG, "/gi_quiesce registration failed: %s", esp_err_to_name(e));
    }
    return e;
}
