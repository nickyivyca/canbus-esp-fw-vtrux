/*
 * A build that defines its own esp_timer_get_time() and links the fake keeps
 * its own (twai_fake.c's is weak), as gen-inhibit's gen_inhibit_shim does
 * (shim_harness.c). If the fake's definition were strong this would not
 * link; if the fake's were taken, the value would be the fake's clock.
 * Charge-interposer tester, 2026-11-27.
 */
#include "twai_fake.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"

#include <stdio.h>

static int64_t g_project_now = 424242;

int64_t esp_timer_get_time(void) { return g_project_now++; }

int main(void)
{
    int fails = 0;
    twai_fake_reset();
    esp_rom_delay_us(100);              /* the fake's clock moves; the project's does not */
    if (twai_fake_now() != 100) { printf("FAIL the fake's clock reads %lld\n", (long long)twai_fake_now()); fails++; }
    int64_t a = esp_timer_get_time(), b = esp_timer_get_time();
    if (a != 424242 || b != 424243)
    {
        printf("FAIL esp_timer_get_time read %lld then %lld, not the build's own 424242, 424243\n",
               (long long)a, (long long)b);
        fails++;
    }
    printf("test_esp_timer_strong: %s\n", fails ? "FAILED" : "the build's own esp_timer_get_time kept");
    return fails ? 1 : 0;
}
