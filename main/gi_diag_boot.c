/*
 * gi_diag_boot.c -- BOOT DIAGNOSTIC, DIAG BUILD ONLY. NOT FOR THE VEHICLE.
 *
 * This file exists only on the `gen-inhibit/diag-reset` branch and must never
 * be merged into a shipping branch. It is in the build unconditionally on that
 * branch rather than behind an `#if`, for the reason `main/CMakeLists.txt`
 * gives about the TX-abort probe: a guard inside a compiled file leaves the
 * behaviour one #define away from a shipping image, while a file that is not
 * in the build cannot be linked by accident. Here the whole branch is the
 * guard.
 *
 * WHAT IT IS FOR. The WiCAN resets 0.1-0.5 s after being put in mode 3
 * (INHIBIT) with no 0x051 on the bus, 6 times out of 6, on `a71b90a` and on
 * `aaab89a` alike. Every reset so far has been observed only through its
 * AFTERMATH -- a post-reset state read over the wire -- so the cause is
 * unknown. The two things this prints are the two that a post-reset read
 * cannot recover:
 *
 *   1. esp_reset_reason(), by name. This separates causes that look identical
 *      from the outside. ESP_RST_PANIC or ESP_RST_INT_WDT or ESP_RST_TASK_WDT
 *      is a firmware path and the panic handler's backtrace (this build halts
 *      rather than rebooting, see sdkconfig) says where. ESP_RST_BROWNOUT is
 *      the USB 5 V supply, which on this rig cannot be metered (the bench is
 *      USB-powered, user 2026-10-08). ESP_RST_USB or ESP_RST_JTAG is the
 *      USB-Serial-JTAG console resetting the chip, i.e. the host end, not the
 *      firmware at all. ESP_RST_SW is a deliberate esp_restart() somewhere in
 *      the image.
 *
 *   2. An uptime line once a second for the first 30 s. A reset inside the
 *      first second prints no uptime line at all, which distinguishes "it died
 *      during startup" from "it started, then died" -- and a console capture
 *      that opens late still learns the reset reason, because every line
 *      carries it.
 *
 * The uptime task prints and nothing else: it touches no gen-inhibit state, no
 * CAN peripheral and no NVS, so it cannot be the thing that makes the reset
 * appear or disappear.
 */
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "gi_diag_boot.h"

#define TAG "GI_DIAG"

/* Seconds of uptime to report, one line per second. */
#define GI_DIAG_BOOT_SECONDS 30

static const char *reset_reason_name(esp_reset_reason_t r)
{
    switch (r)
    {
        case ESP_RST_UNKNOWN:    return "ESP_RST_UNKNOWN";
        case ESP_RST_POWERON:    return "ESP_RST_POWERON";
        case ESP_RST_EXT:        return "ESP_RST_EXT";
        case ESP_RST_SW:         return "ESP_RST_SW";
        case ESP_RST_PANIC:      return "ESP_RST_PANIC";
        case ESP_RST_INT_WDT:    return "ESP_RST_INT_WDT";
        case ESP_RST_TASK_WDT:   return "ESP_RST_TASK_WDT";
        case ESP_RST_WDT:        return "ESP_RST_WDT";
        case ESP_RST_DEEPSLEEP:  return "ESP_RST_DEEPSLEEP";
        case ESP_RST_BROWNOUT:   return "ESP_RST_BROWNOUT";
        case ESP_RST_SDIO:       return "ESP_RST_SDIO";
        case ESP_RST_USB:        return "ESP_RST_USB";
        case ESP_RST_JTAG:       return "ESP_RST_JTAG";
        case ESP_RST_EFUSE:      return "ESP_RST_EFUSE";
        case ESP_RST_PWR_GLITCH: return "ESP_RST_PWR_GLITCH";
        case ESP_RST_CPU_LOCKUP: return "ESP_RST_CPU_LOCKUP";
        default:                 break;
    }
    return "UNNAMED";
}

static void gi_diag_boot_task(void *arg)
{
    const esp_reset_reason_t reason = (esp_reset_reason_t)(intptr_t)arg;
    const char *name = reset_reason_name(reason);

    for (int s = 1; s <= GI_DIAG_BOOT_SECONDS; s++)
    {
        vTaskDelay(pdMS_TO_TICKS(1000));
        ESP_LOGW(TAG, "up %d s, uptime %" PRId64 " us, last reset %s (%d)",
                 s, esp_timer_get_time(), name, (int)reason);
    }

    ESP_LOGW(TAG, "boot diagnostic done after %d s; last reset %s (%d)",
             GI_DIAG_BOOT_SECONDS, name, (int)reason);
    vTaskDelete(NULL);
}

void gi_diag_boot_init(void)
{
    const esp_reset_reason_t reason = esp_reset_reason();

    /* Printed before the task is created, so it appears even if the task
     * cannot be created or the chip dies in the first second. */
    ESP_LOGW(TAG, "DIAG BUILD, NOT FOR VEHICLE. reset reason %s (%d)",
             reset_reason_name(reason), (int)reason);

    /* Low priority on purpose: this must not displace the gen-inhibit worker
     * or the TWAI driver, or the thing being measured changes. */
    xTaskCreate(gi_diag_boot_task, "gi_diag_boot", 3072,
                (void *)(intptr_t)reason, 1, NULL);
}
