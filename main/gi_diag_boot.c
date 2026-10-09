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
 *      from the outside. ESP_RST_BROWNOUT is the USB 5 V supply, which on this
 *      rig cannot be metered (the bench is USB-powered, user 2026-10-08).
 *      ESP_RST_USB or ESP_RST_JTAG is the USB-Serial-JTAG console resetting the
 *      chip, i.e. the host end, not the firmware at all. ESP_RST_SW is a
 *      deliberate esp_restart() somewhere in the image. ESP_RST_INT_WDT or
 *      ESP_RST_TASK_WDT is a firmware path -- though on THIS image a panic or
 *      watchdog halts instead of resetting, so it shows up as a dump and a
 *      hang rather than as a reason code on the next boot. Measured
 *      2026-10-08: after a halt and a USB-JTAG hard reset the reason reads
 *      ESP_RST_USB (11), NOT ESP_RST_PANIC, because with PANIC_PRINT_HALT the
 *      panic never becomes a reset and the reason register never learns of it.
 *      The backtrace is the whole record of a panic here.
 *
 *   2. An uptime line once a second for the first 30 s. A reset inside the
 *      first second prints no uptime line at all, which distinguishes "it died
 *      during startup" from "it started, then died" -- and a console capture
 *      that opens late still learns the reset reason, because every line
 *      carries it.
 *
 * WHY esp_rom_printf AND NOT ESP_LOGx, which is what this file used first.
 * `main/main.c` runs `esp_log_level_set("*", ESP_LOG_NONE)` as the LAST
 * statement of `app_main`, so once startup finishes no ESP_LOGx from any
 * component in this firmware reaches the console. Only ROM-path output does:
 * the boot banner, panic dumps, watchdog dumps. (Line 627 on this branch, 623
 * at `a71b90a` -- this file's own four lines in main.c move it. Grep for the
 * call rather than trusting either number.)
 *
 * With ESP_LOGW the instrument failed in the one direction that matters.
 * Measured on the bench 2026-10-08: between a boot and a deliberate abort()
 * 180 s later the console recorded ZERO lines -- not sparse output, none --
 * and then the panic dump arrived in full, because the panic handler writes by
 * this path instead. The reset-reason line survived only because
 * gi_diag_boot_init() runs at the top of app_main, before line 627. So the
 * image printed its reason once at 231 ms and then went quiet for 30 s, which
 * is EXACTLY what a chip that died during startup looks like -- the one
 * distinction item 2 exists to make, destroyed by the instrument itself.
 *
 * esp_rom_printf cannot be silenced by a log level, needs no tag registration
 * and was just proven to reach the console minutes after boot. Its printf is
 * the cut-down ROM one: no 64-bit conversions and no width or precision, so
 * the uptime is printed as an unsigned long of milliseconds (which wraps after
 * ~49 days and so cannot wrap inside a 30 s window).
 *
 * The uptime task prints and nothing else: it touches no gen-inhibit state, no
 * CAN peripheral and no NVS, so it cannot be the thing that makes the reset
 * appear or disappear.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "gi_diag_boot.h"

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
        esp_rom_printf("GI_DIAG: up %d s, uptime %u ms, last reset %s (%d)\n",
                       s, (unsigned)(esp_timer_get_time() / 1000),
                       name, (int)reason);
    }

    esp_rom_printf("GI_DIAG: boot diagnostic done after %d s; "
                   "last reset %s (%d)\n",
                   GI_DIAG_BOOT_SECONDS, name, (int)reason);
    vTaskDelete(NULL);
}

void gi_diag_boot_init(void)
{
    const esp_reset_reason_t reason = esp_reset_reason();

    /* Printed before the task is created, so it appears even if the task
     * cannot be created or the chip dies in the first second. */
    esp_rom_printf("GI_DIAG: DIAG BUILD, NOT FOR VEHICLE. reset reason %s "
                   "(%d)\n", reset_reason_name(reason), (int)reason);

    /* Low priority on purpose: this must not displace the gen-inhibit worker
     * or the TWAI driver, or the thing being measured changes. */
    xTaskCreate(gi_diag_boot_task, "gi_diag_boot", 3072,
                (void *)(intptr_t)reason, 1, NULL);
}
