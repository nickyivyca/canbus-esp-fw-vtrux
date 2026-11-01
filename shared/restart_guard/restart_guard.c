/*
 * vtrux/restart_guard -- every software restart takes the CAN ports off the
 * bus first (SPEC.md G2, G3). Moved at port step 1c from gen-inhibit's
 * main/gi_restart_quiesce.c (gen-inhibit/main 69d97a2), with the same steps,
 * bounds and order. gen-inhibit spec 3, "No restart leaves the controller on
 * the bus" (added 2026-10-13, user), from the trial build 089cd21.
 *
 * WHY (IDF v5.4.1). esp_restart() runs the shutdown handlers and calls
 * esp_restart_noos() (esp_system.c:63); a panic calls it from panic_restart()
 * (panic_handler.c:266 -- the noos_dig branch above it is ESP32-only); and
 * call_start_cpu0 calls it on two early-boot failures (cpu_start.c:769, :790).
 * On the C3 that function (port/soc/esp32c3/system_internal.c:64) resets a list
 * of peripherals that does NOT include TWAI, drops the CPU to XTAL at :107,
 * which takes APB from 80 to 40 MHz, and resets the CPU only at :110. Nothing
 * stops or uninstalls the driver, and after a CPU reset esp_perip_clk_init
 * gates only the clocks that were already off (clk.c:215-217). So a running
 * controller keeps its configuration, its clock and any pending frame through
 * the reset, timed from half its clock, on a bus it is still connected to:
 * 107-139 error frames per live restart on gen-inhibit's bench without the
 * step, 0 with it (context from before the move, VALIDATION.md G2).
 *
 * THE GUARD is a linker wrap of esp_restart_noos() (`-Wl,--wrap`), which the
 * component sets itself (CMakeLists.txt; G2), so a project gets it by
 * depending on the component. Every caller is in another object file, so all
 * of them reach __wrap_esp_restart_noos(), which runs before the clock drop
 * and then calls the real function. It is the point every software restart
 * passes through, after the last code that could skip it -- the shutdown
 * handlers a crash skips are earlier. Its known limits, a panic action that
 * does not restart and the panic output before it, are in VALIDATION.md G2.
 *
 * WHAT IT DOES (G3), in this order, with interrupts off from the start, for
 * each CAN port the board has (shared/boards B5), in port order:
 *
 *   1. THE PORT'S RESTART STOP, can_port_restart_stop() (can_port C7), which
 *      calls the stop of the driver for the port's kind: twai_port's (T12)
 *      for a TWAI port. Each driver stops its own controller, so the guard
 *      holds no register code. For a TWAI port that is reset mode after the
 *      frame on the wire has finished, bounded at 1 ms; why, and why only an
 *      awaiting frame is aborted, is written at twai_port_restart_stop().
 *
 *   2. STANDBY: board_can_standby(port, true), the board's restart-safe call
 *      (B5): one register write, in IRAM. On wican_v3 it drives the
 *      transceiver's STBY, GPIO6, to 1. The project's board_can_init() puts it
 *      in standby again at start-up, and it stays there until the port is
 *      brought up, so the transceiver is in standby from the guard until the
 *      bus is wanted. This half does not depend on the controller's state
 *      surviving; reset mode does not depend on the pin.
 *
 * A port whose stop or standby answers "not supported" does not stop the
 * steps (G3). Its standby bit stays 0; the record's TWAI fields are filled
 * only from a TWAI port's stop that ran (ESP_OK), and stay 0 otherwise. Then
 * the record (G4), then the real esp_restart_noos().
 *
 * THE RECORD is written in every build, so that the test build runs exactly
 * the guard a release build runs. Only a test build reads it (test_app/).
 *
 * IRAM, AND NOTHING THAT NEEDS THE CACHE. The wrapper and what it calls are in
 * IRAM or ROM (board_can_ports(), board_can_kind() and board_can_standby() by
 * B5, can_port C7, twai_port T12), and it touches registers and the RTC
 * record only. Its callers have the cache enabled when they get here, but the
 * real function disables it, and nothing here should depend on where that
 * line is.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#include "sdkconfig.h"
#include "esp_attr.h"
#include "esp_err.h"
#if CONFIG_IDF_TARGET_ARCH_RISCV
#include "riscv/rv_utils.h"
#else
#include "esp_cpu.h"
#endif

#include "board.h"
#include "can_port.h"
#include "restart_guard.h"

_Static_assert(sizeof(restart_guard_record_t) == 15 * sizeof(uint32_t),
               "the record's layout is gen-inhibit's gi_restart_record_t's "
               "(G4): fifteen words, check last");

static RTC_NOINIT_ATTR restart_guard_record_t s_rec;

static IRAM_ATTR uint32_t rg_sum(const restart_guard_record_t *r)
{
    const uint32_t *w = (const uint32_t *)r;
    uint32_t x = 0x9E3779B9u;
    for (size_t i = 0;
         i < offsetof(restart_guard_record_t, check) / sizeof(uint32_t); i++)
    {
        x = (x ^ w[i]) * 0x01000193u;
    }
    return x;
}

static IRAM_ATTR bool rg_valid(const restart_guard_record_t *r)
{
    return r->magic == RESTART_GUARD_RECORD_MAGIC && r->check == rg_sum(r);
}

bool restart_guard_record_valid(void)
{
    return rg_valid(&s_rec);
}

void restart_guard_record_get(restart_guard_record_t *out)
{
    *out = s_rec;
}

void restart_guard_record_init(void)
{
    memset(&s_rec, 0, sizeof(s_rec));
    s_rec.magic = RESTART_GUARD_RECORD_MAGIC;
    s_rec.check = rg_sum(&s_rec);
}

void __real_esp_restart_noos(void) __attribute__((noreturn));
void __wrap_esp_restart_noos(void) __attribute__((noreturn));

void IRAM_ATTR __wrap_esp_restart_noos(void)
{
    /*
     * The real function's first act, done here first: no driver's ISR may run
     * while its controller is being taken off the bus under it.
     */
#if CONFIG_IDF_TARGET_ARCH_RISCV
    rv_utils_intr_global_disable();
#else
    esp_cpu_intr_disable(0xFFFFFFFF);
#endif

    restart_guard_record_t r;
    r.magic     = RESTART_GUARD_RECORD_MAGIC;
    r.entries   = (rg_valid(&s_rec) ? s_rec.entries : 0u) + 1u;
    r.twai_clk  = 0;
    r.twai_rst  = 0;
    r.in_reset  = 0;
    r.st_entry  = 0;
    r.tec       = 0;
    r.rec       = 0;
    r.did_twai  = 0;
    r.st_reset  = 0;
    r.wait_us   = 0;
    r.timed_out = 0;
    r.did_stdby = 0;
    r.aborts    = 0;

    const int ports = board_can_ports();
    for (int port = 0; port < ports && port < 32; port++)
    {
        can_port_restart_report_t s;
        if (can_port_restart_stop(port, &s) == ESP_OK &&
            board_can_kind(port) == BOARD_CAN_TWAI)
        {
            r.twai_clk  = s.clock_on ? 1u : 0u;
            r.twai_rst  = s.held_in_reset ? 1u : 0u;
            r.in_reset  = s.was_in_reset ? 1u : 0u;
            r.st_entry  = s.status_at_entry;
            r.tec       = s.tec;
            r.rec       = s.rec;
            r.did_twai  = s.entered_reset ? 1u : 0u;
            r.st_reset  = s.status_at_reset;
            r.wait_us   = s.wait_us;
            r.timed_out = s.timed_out ? 1u : 0u;
            r.aborts    = s.aborts;
        }
        if (board_can_standby(port, true) == ESP_OK)
        {
            r.did_stdby |= 1u << port;
        }
    }

    r.check = rg_sum(&r);
    s_rec = r;

    __real_esp_restart_noos();
}
