/*
 * vtrux/twai_port on the ESP32-S3: T8's transmit buffer state and
 * abort-if-awaiting, T12's restart stop, and T14/T15's switch steps, register
 * level (SPEC.md T19).
 *
 * NOT YET MEASURED (T19). Every public call here reports itself unsupported,
 * and twai_port_ll_switch_supported() is false, unless the build sets
 * TWAI_PORT_S3_PROBE, the switch of the T19 measurement build, which is never
 * set in a release image (shared/tools R20). Once the tester's measurement
 * passes, a commit makes this file supported without the switch, citing the
 * VERIFICATION.md entry.
 *
 * CHECKED AGAINST ESP-IDF v5.4.2 (2026-11-27):
 * components/hal/esp32s3/include/hal/twai_ll.h, all static inline:
 * TWAI_LL_GET_HW(), twai_ll_get_status() with TWAI_LL_STATUS_TBS, _TS and
 * _RS, twai_ll_set_cmd_abort_tx(), twai_ll_is_in_reset_mode(),
 * twai_ll_enter_reset_mode(), twai_ll_exit_reset_mode(), twai_ll_set_mode(),
 * twai_ll_set_rec(), twai_ll_get_rec() and twai_ll_get_tec(). That file
 * differs from the C3's only in its copyright line, two continuation-line
 * indents and the bitfield names inside twai_ll_enable_bus_clock() and
 * twai_ll_reset_register(), which nothing here calls.
 * components/soc/esp32s3/register/soc/system_reg.h: SYSTEM_PERIP_CLK_EN0_REG
 * (DR_REG_SYSTEM_BASE + 0x18) with SYSTEM_TWAI_CLK_EN (bit 19), and
 * SYSTEM_PERIP_RST_EN0_REG (+ 0x20) with SYSTEM_TWAI_RST (bit 19), the same
 * names as the C3's.
 *
 * WHY THE S3 NEEDS T12 TOO: esp_restart_noos() on the S3
 * (esp_system/port/soc/esp32s3/system_internal.c:70, its reset list at
 * esp_system_reset_modules_on_exit(), :34-60) resets the timers, SPI0/1,
 * UART, the system timer, PWM, DMA and crypto, not TWAI.
 *
 * THE SECOND CORE. The S3 has two cores, and the guard's caller turns
 * interrupts off only on its own core. Left running, the other core's TWAI
 * interrupt, or a task there, could load the next queued frame after each
 * abort or completion during the wait. So the restart stop first stalls the
 * other core, before T12's steps, and does not start it again: the restart
 * follows (T12, user 2026-11-27). Nothing here takes the port's spinlock on
 * the restart path, because the other core may have been stalled while
 * holding it. The restart stop reads the status and aborts inline; with
 * interrupts off on this core and the other core stalled, that read and
 * abort is T8's one critical section (charge-interposer reviewer,
 * 2026-11-27).
 *
 * The stall is esp_cpu_stall() (esp_hw_support cpu.c, placed in IRAM by its
 * linker.lf), which writes the stall code with
 * cpu_utility_ll_stall_cpu() (hal/esp32s3/include/hal/cpu_utility_ll.h:29-48):
 * field C0 of RTC_CNTL_OPTIONS0_REG to 2 and field C1 of
 * RTC_CNTL_SW_CPU_STALL_REG to 0x21, each by a clear and then a set. The
 * panic handler has already stalled the other core on a panic
 * (panic_handler.c:240-246). A second esp_cpu_stall() on that core is not a
 * no-op: between each clear and its set the code is broken, so the core can
 * run for the span of one register write, twice, before it is stalled again.
 * So the stop reads both fields first and calls esp_cpu_stall() only when
 * the core is not already stalled.
 */
#include "twai_port.h"
#include "twai_port_ll.h"
#include "hal/twai_ll.h"
#include "esp_attr.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "soc/soc.h"
#include "soc/system_reg.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_cpu.h"

/* Two switches, kept apart. S3_PROBE_BUILD: the T19 measurement build, which
 * alone has the stall record and its R20 mark. S3_SUPPORTED: T8, T12 and the
 * switch act. Until T19's measurement passes, only the measurement build is
 * supported; when it passes, S3_SUPPORTED becomes 1 in every build, and the
 * stall record stays in the measurement build only. */
#if defined(TWAI_PORT_S3_PROBE) && TWAI_PORT_S3_PROBE
#define S3_PROBE_BUILD 1
#else
#define S3_PROBE_BUILD 0
#endif
#define S3_SUPPORTED S3_PROBE_BUILD     /* not measured yet (T19) */

/* The host-only switch (twai_port_ll.h, which stops an ESP-IDF compile
 * that sets it). */
#if defined(TWAI_PORT_HOST_SWITCH) && TWAI_PORT_HOST_SWITCH
#define S3_SWITCH_SUPPORTED 1
#else
#define S3_SWITCH_SUPPORTED S3_SUPPORTED
#endif

static portMUX_TYPE s_abort_lock = portMUX_INITIALIZER_UNLOCKED;

static IRAM_ATTR twai_port_buf_t buf_from_status(uint32_t st)
{
    if (st & TWAI_LL_STATUS_TBS) { return TWAI_PORT_BUF_EMPTY; }
    if (st & TWAI_LL_STATUS_TS)  { return TWAI_PORT_BUF_TRANSMITTING; }
    return TWAI_PORT_BUF_AWAITING;
}

/* The read and the abort, with no lock: the caller makes it indivisible. */
static IRAM_ATTR twai_port_buf_t abort_if_awaiting_raw(twai_dev_t *hw)
{
    const twai_port_buf_t seen = buf_from_status(twai_ll_get_status(hw));
    if (seen == TWAI_PORT_BUF_AWAITING)
    {
        twai_ll_set_cmd_abort_tx(hw);
    }
    return seen;
}

twai_port_buf_t twai_port_buf_state(void)
{
    if (!S3_SUPPORTED)
    {
        return TWAI_PORT_BUF_UNSUPPORTED;
    }
    return buf_from_status(twai_ll_get_status(TWAI_LL_GET_HW(0)));
}

/*
 * T8, as the C3's: the read and the abort in one critical section. On the
 * S3 the section is a spinlock shared by both cores, so it is not for the
 * restart path (see THE SECOND CORE above); the restart stop calls
 * abort_if_awaiting_raw() with interrupts already off.
 */
IRAM_ATTR twai_port_buf_t twai_port_abort_if_awaiting(void)
{
    if (!S3_SUPPORTED)
    {
        return TWAI_PORT_BUF_UNSUPPORTED;
    }
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    taskENTER_CRITICAL(&s_abort_lock);
    const twai_port_buf_t seen = abort_if_awaiting_raw(hw);
    taskEXIT_CRITICAL(&s_abort_lock);
    return seen;
}

#define RESTART_WAIT_MAX_US  1000u
#define RESTART_WAIT_STEP_US 2u

/*
 * True if `core` holds the stall code cpu_utility_ll_stall_cpu() writes: C0
 * = 2 and C1 = 0x21. The shifts are chosen by a conditional, not an array,
 * so nothing is read from flash (as cpu_utility_ll.h does, IDF-5214).
 */
static IRAM_ATTR bool core_stalled(int core)
{
    const uint32_t c0_s = (core == 0) ? RTC_CNTL_SW_STALL_PROCPU_C0_S
                                      : RTC_CNTL_SW_STALL_APPCPU_C0_S;
    const uint32_t c1_s = (core == 0) ? RTC_CNTL_SW_STALL_PROCPU_C1_S
                                      : RTC_CNTL_SW_STALL_APPCPU_C1_S;
    return ((REG_READ(RTC_CNTL_OPTIONS0_REG) >> c0_s) & 0x3u) == 0x2u
           && ((REG_READ(RTC_CNTL_SW_CPU_STALL_REG) >> c1_s) & 0x3Fu) == 0x21u;
}

#if S3_PROBE_BUILD
/*
 * T19, THE STALL RECORD (twai_port_ll.h): the measurement build only. What
 * the probe build adds to the stop: two register reads at entry, before the
 * stall check, and one write of this record after T12's steps, at each
 * return after the stall. Nothing else in the stop differs.
 */
#if !defined(RTC_NOINIT_ATTR) && !defined(ESP_PLATFORM)
#define RTC_NOINIT_ATTR     /* host builds whose esp_attr.h lacks it */
#endif
static RTC_NOINIT_ATTR twai_port_s3_stall_record_t s_stall_rec;

static IRAM_ATTR uint32_t stall_check(const twai_port_s3_stall_record_t *s)
{
    return s->magic ^ s->core ^ s->other ^ s->options0 ^ s->sw_cpu_stall
           ^ s->stalled_at_entry ^ s->stall_called ^ s->entered_reset
           ^ TWAI_PORT_S3_STALL_MAGIC;
}

#define STALL_CAPTURE()                                                     \
    const uint32_t cap_options0 = REG_READ(RTC_CNTL_OPTIONS0_REG);          \
    const uint32_t cap_sw_stall = REG_READ(RTC_CNTL_SW_CPU_STALL_REG)

#define STALL_RECORD(core_, other_, stalled_, called_, entered_)            \
    do {                                                                    \
        s_stall_rec.magic = TWAI_PORT_S3_STALL_MAGIC;                       \
        s_stall_rec.core = (uint32_t)(core_);                               \
        s_stall_rec.other = (uint32_t)(other_);                             \
        s_stall_rec.options0 = cap_options0;                                \
        s_stall_rec.sw_cpu_stall = cap_sw_stall;                            \
        s_stall_rec.stalled_at_entry = (stalled_) ? 1u : 0u;                \
        s_stall_rec.stall_called = (called_) ? 1u : 0u;                     \
        s_stall_rec.entered_reset = (entered_) ? 1u : 0u;                   \
        s_stall_rec.check = stall_check(&s_stall_rec);                      \
    } while (0)

bool twai_port_ll_s3_stall_record(twai_port_s3_stall_record_t *out)
{
    *out = s_stall_rec;
    return out->magic == TWAI_PORT_S3_STALL_MAGIC
           && out->check == stall_check(out);
}

void twai_port_ll_s3_stall_record_clear(void)
{
    s_stall_rec.magic = 0;
    s_stall_rec.check = 0;
}

const char twai_port_s3_probe_build[] =
    "vtrux/twai_port S3 probe build (T19 measurement only)";
#else
#define STALL_CAPTURE()                                     do { } while (0)
#define STALL_RECORD(core_, other_, stalled_, called_, entered_) do { } while (0)
#endif

#if defined(TWAI_PORT_HOST_SWITCH) && TWAI_PORT_HOST_SWITCH
const char twai_port_host_switch_build[] =
    "vtrux/twai_port host switch build (T10 fake only)";
#endif

/*
 * THE BUILD MARKS (twai_port_ll.h): the address of each mark goes into an
 * empty asm, so the compiler must make it and the linker keeps the mark.
 */
void twai_port_ll_build_marks(void)
{
#if S3_PROBE_BUILD
    __asm__ volatile("" : : "r"(twai_port_s3_probe_build) : "memory");
#endif
#if defined(TWAI_PORT_HOST_SWITCH) && TWAI_PORT_HOST_SWITCH
    __asm__ volatile("" : : "r"(twai_port_host_switch_build) : "memory");
#endif
}

/*
 * T12, THE RESTART STOP: the C3's steps, bounds and order (see
 * twai_port_ll_esp32c3.c for why each step is there), with the abort inline.
 */
IRAM_ATTR esp_err_t twai_port_restart_stop(can_port_restart_report_t *r)
{
    if (!S3_SUPPORTED)
    {
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (r == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    /* First, THE OTHER CORE STALLED, and not started again (T12; see THE
     * SECOND CORE above). */
    STALL_CAPTURE();
    const int core = esp_cpu_get_core_id();
    const int other = (core == 0) ? 1 : 0;
    const bool stalled_at_entry = core_stalled(other);
    if (!stalled_at_entry)
    {
        esp_cpu_stall(other);
    }

    r->clock_on        = (REG_READ(SYSTEM_PERIP_CLK_EN0_REG) & SYSTEM_TWAI_CLK_EN) != 0;
    r->held_in_reset   = (REG_READ(SYSTEM_PERIP_RST_EN0_REG) & SYSTEM_TWAI_RST) != 0;
    r->was_in_reset    = false;
    r->status_at_entry = 0;
    r->tec             = 0;
    r->rec             = 0;
    r->status_at_reset = 0;
    r->wait_us         = 0;
    r->timed_out       = false;
    r->aborts          = 0;
    r->entered_reset   = false;

    if (!r->clock_on || r->held_in_reset)
    {
        STALL_RECORD(core, other, stalled_at_entry, !stalled_at_entry, false);
        return ESP_OK;
    }

    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    r->was_in_reset    = twai_ll_is_in_reset_mode(hw);
    r->status_at_entry = twai_ll_get_status(hw);
    r->tec             = twai_ll_get_tec(hw);
    r->rec             = twai_ll_get_rec(hw);

    uint32_t waited = 0;
    for (;;)
    {
        if (abort_if_awaiting_raw(hw) == TWAI_PORT_BUF_AWAITING)
        {
            r->aborts++;
        }
        if ((twai_ll_get_status(hw) &
             (TWAI_LL_STATUS_TS | TWAI_LL_STATUS_RS)) == 0 ||
            waited >= RESTART_WAIT_MAX_US)
        {
            break;
        }
        esp_rom_delay_us(RESTART_WAIT_STEP_US);
        waited += RESTART_WAIT_STEP_US;
    }
    r->wait_us         = waited;
    r->timed_out       = waited >= RESTART_WAIT_MAX_US;
    r->status_at_reset = twai_ll_get_status(hw);
    twai_ll_enter_reset_mode(hw);
    r->entered_reset   = true;
    STALL_RECORD(core, other, stalled_at_entry, !stalled_at_entry, true);
    return ESP_OK;
}

/* ------------------------------------------- T14, T15: the switch steps -- */

bool twai_port_ll_switch_supported(void)
{
    return S3_SWITCH_SUPPORTED;
}

twai_port_buf_t twai_port_ll_buf_state(void)
{
    return buf_from_status(twai_ll_get_status(TWAI_LL_GET_HW(0)));
}

/* T8's one critical section, as twai_port_abort_if_awaiting() has it. */
twai_port_buf_t twai_port_ll_abort_if_awaiting(void)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    taskENTER_CRITICAL(&s_abort_lock);
    const twai_port_buf_t seen = abort_if_awaiting_raw(hw);
    taskEXIT_CRITICAL(&s_abort_lock);
    return seen;
}

/*
 * Reset mode halts a frame in transmission and misses one arriving
 * (twai_ll.h, twai_ll_set_mode() needs reset mode). T16 will place this in
 * the inter-frame gap; until the user's ruling it goes in at once, and the
 * status read just before is reported, so the measurement shows where it
 * landed.
 */
void twai_port_ll_switch_enter(twai_port_ll_entry_t *out)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    out->status = twai_ll_get_status(hw);
    out->buf = buf_from_status(out->status);
    twai_ll_enter_reset_mode(hw);
}

/*
 * T15: REC 128 on the way into listen-only, as the driver's errata
 * workaround does in twai_hal_start() (v5.4.2 twai_hal.c:70-92), so the
 * listen-only controller is error-passive and sends no active error flag; 0
 * on the way out, as twai_hal_stop() does. Both counters are read back while
 * still in reset mode, where nothing on the bus can move them. Unlike
 * twai_hal_start(), it does not clear the latched interrupts: reading the
 * interrupt register would take them from the driver (T8).
 */
void twai_port_ll_switch_leave(bool listen_only, uint32_t rec,
                               uint32_t *rec_back, uint32_t *tec_back)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    twai_ll_set_mode(hw, listen_only ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
    twai_ll_set_rec(hw, rec);
    *rec_back = twai_ll_get_rec(hw);
    *tec_back = twai_ll_get_tec(hw);
    twai_ll_exit_reset_mode(hw);
}

void twai_port_ll_counters(uint32_t *tec, uint32_t *rec)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    *tec = twai_ll_get_tec(hw);
    *rec = twai_ll_get_rec(hw);
}
