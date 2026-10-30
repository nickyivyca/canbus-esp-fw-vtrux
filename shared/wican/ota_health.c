#include "ota_health.h"

#include "freertos/task.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"

static const char *TAG = "OTA_HEALTH";

static EventGroupHandle_t s_health_group = NULL;
/* Reports that arrive before the gate is armed are held here, not dropped. */
static volatile EventBits_t s_pending_bits = 0;

/*
 * Fault injection for the rollback rehearsal. The named subsystems never report
 * in, so the gate times out and the image rolls back. This is how we prove the
 * safety net catches before we rely on it, rather than trusting the Kconfig
 * alone.
 *
 * Build it with:
 *
 *   idf.py -B build-otafault -DOTA_HEALTH_FAULT_INJECT=1 build
 *
 * and NOT by passing -DOTA_HEALTH_FAULT_INJECT to the compiler yourself. This
 * comment used to say the latter; `idf.py -D` sets a CMake cache variable, so
 * the macro below stayed at 0 and the build produced an ordinary image with no
 * warning. This component's CMakeLists.txt now forwards the value as a real
 * compile definition (SPEC.md W7), and a project can put a marker such as
 * "_flt" in the image name from the same cache variable, so a fault build is
 * visible in the filename and in esp_app_desc.project_name rather than only in
 * behaviour.
 *
 * NOTE WHAT THIS DOES NOT DO: it suppresses the REPORT, not the subsystem. The
 * SoftAP and HTTP server are already up by the time their reports are
 * intercepted, so a fault-injected image is still reachable and can be replaced
 * over the air.
 */
#ifndef OTA_HEALTH_FAULT_INJECT
#define OTA_HEALTH_FAULT_INJECT 0
#endif

#if OTA_HEALTH_FAULT_INJECT
/*
 * THE HOOK IS A FUNCTION OF ITS OWN, KEPT OUT OF LINE (SPEC.md W7). At
 * shared/tools R28's release log levels the ESP_LOGW below is compiled out,
 * and the hook was otherwise a few instructions inside ota_health_report(),
 * so a switch-on image had nothing a refusal row could find. This function's
 * symbol is in the ELF if and only if the switch is on, at any log level, and
 * the component's refusal row judges it (shared/tools R20).
 *
 * noipa, not noinline alone: GCC's interprocedural passes may still clone a
 * noinline function (.constprop.0, .isra.0) and call the clone, and
 * --gc-sections then drops the original, so its exact name would vanish.
 * noipa keeps one out-of-line copy under this name, called as it is.
 *
 * Returns the bits still to report.
 */
EventBits_t ota_health_fault_inject_suppress(EventBits_t bits);

__attribute__((noipa)) EventBits_t ota_health_fault_inject_suppress(EventBits_t bits)
{
    ESP_LOGW(TAG, "FAULT INJECT: suppressing report of 0x%02x", (unsigned)(bits & OTA_HEALTH_FAULT_INJECT));
    return bits & ~(EventBits_t)OTA_HEALTH_FAULT_INJECT;
}
#endif

void ota_health_report(EventBits_t bits)
{
#if OTA_HEALTH_FAULT_INJECT
    if (OTA_HEALTH_FAULT_INJECT & bits)
    {
        bits = ota_health_fault_inject_suppress(bits);
        if (bits == 0)
        {
            return;
        }
    }
#endif

    if (s_health_group != NULL)
    {
        xEventGroupSetBits(s_health_group, bits);
    }
    else
    {
        s_pending_bits |= bits;
    }
    ESP_LOGI(TAG, "subsystem up: 0x%02x", (unsigned)bits);
}

static const char *img_state_str(esp_ota_img_states_t s)
{
    switch (s)
    {
        case ESP_OTA_IMG_NEW:            return "NEW";
        case ESP_OTA_IMG_PENDING_VERIFY: return "PENDING_VERIFY";
        case ESP_OTA_IMG_VALID:          return "VALID";
        case ESP_OTA_IMG_INVALID:        return "INVALID";
        case ESP_OTA_IMG_ABORTED:        return "ABORTED";
        default:                         return "UNDEFINED";
    }
}

static void ota_health_gate_task(void *arg)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    esp_ota_get_state_partition(running, &state);

    ESP_LOGI(TAG, "gate armed: running %s, state %s, waiting up to %d ms for 0x%02x",
             running ? running->label : "?", img_state_str(state),
             OTA_HEALTH_TIMEOUT_MS, (unsigned)OTA_HEALTH_ALL);

    EventBits_t got = xEventGroupWaitBits(s_health_group,
                                          OTA_HEALTH_ALL,
                                          pdFALSE,   /* leave bits set */
                                          pdTRUE,    /* need ALL of them */
                                          pdMS_TO_TICKS(OTA_HEALTH_TIMEOUT_MS));

    /* Re-read: an OTA may have completed while we waited. */
    esp_ota_get_state_partition(running, &state);

    if ((got & OTA_HEALTH_ALL) == OTA_HEALTH_ALL)
    {
        if (state == ESP_OTA_IMG_PENDING_VERIFY)
        {
            ESP_LOGI(TAG, "all subsystems up -- marking image valid, rollback cancelled");
            esp_ota_mark_app_valid_cancel_rollback();
        }
        else
        {
            ESP_LOGI(TAG, "all subsystems up -- image already %s, nothing to do", img_state_str(state));
        }
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGE(TAG, "UNHEALTHY: after %d ms have 0x%02x, need 0x%02x (missing 0x%02x)",
             OTA_HEALTH_TIMEOUT_MS, (unsigned)got, (unsigned)OTA_HEALTH_ALL,
             (unsigned)(OTA_HEALTH_ALL & ~got));

    if (state == ESP_OTA_IMG_PENDING_VERIFY)
    {
        ESP_LOGE(TAG, "rolling back to the previous slot");
        /* Marks this image invalid and reboots into the other partition. */
        esp_ota_mark_app_invalid_rollback_and_reboot();
        /* not reached */
    }

    /*
     * No rollback available -- this image is already the accepted one (e.g. a
     * USB flash, or a previously validated build). Rebooting would only
     * produce a boot loop that is harder to recover from than a degraded
     * device that is still running, so stay up and be loud instead.
     */
    ESP_LOGE(TAG, "image state is %s, not PENDING_VERIFY -- no rollback target. "
                  "Staying up degraded; recover over USB if unreachable.",
             img_state_str(state));
    vTaskDelete(NULL);
}

void ota_health_init(void)
{
    if (s_health_group != NULL)
    {
        return;
    }

    s_health_group = xEventGroupCreate();
    if (s_health_group == NULL)
    {
        ESP_LOGE(TAG, "event group alloc failed -- cannot gate rollback");
        return;
    }

    if (s_pending_bits != 0)
    {
        xEventGroupSetBits(s_health_group, s_pending_bits);
        ESP_LOGI(TAG, "replayed %d early report bits: 0x%02x",
                 __builtin_popcount((unsigned)s_pending_bits), (unsigned)s_pending_bits);
        s_pending_bits = 0;
    }

    xTaskCreate(ota_health_gate_task, "ota_health", 1024 * 3, NULL, 4, NULL);
}
