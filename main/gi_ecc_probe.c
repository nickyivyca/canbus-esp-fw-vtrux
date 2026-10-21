/*
 * gi_ecc_probe.c -- MEASUREMENT BUILD ONLY (GI_INSTRUMENT_ECC). See
 * gi_ecc_probe.h for why it exists, where a bus error is counted, and how the
 * error code capture is read without changing the count.
 */
#include "gi_ecc_probe.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "driver/twai.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "hal/twai_hal.h"
#include "hal/twai_ll.h"
#include "sdkconfig.h"

static const char *TAG = "gi_ecc";

/*
 * Ring sizes. The longest burst on record is 12 counts from one pulse, and the
 * tester's B6 arms run 29 pulses each (reviewer, 2026-10-20), so 128 interrupt
 * entries hold a whole arm's single counts and a couple of bursts. Tick entries
 * are one per worker loop on which the counter moved, which is at most one per
 * interrupt entry and usually fewer, because one loop can see several, so 64
 * hold the same arm. If an arm ever does not fit, isr_overwritten and
 * ticks_overwritten on the page say so -- a dropped entry is counted, never
 * silent.
 */
#define ECC_RING   128
#define TICK_RING  64

typedef struct
{
    int64_t  t_us;      /* esp_timer_get_time() in the interrupt */
    uint32_t seq;       /* this probe's count of bus-error interrupts since boot */
    uint32_t status;    /* status register (twai_ll_get_status), 9 bits */
    uint16_t events;    /* the TWAI_HAL_EVENT_* bits of this interrupt invocation */
    uint8_t  ecc;       /* error code capture register, as the HAL's own read saw it */
    uint8_t  tec;       /* TEC, REC and status as the decode read them */
    uint8_t  rec;
} ecc_entry_t;

typedef struct
{
    int64_t  t_us;      /* esp_timer_get_time() right after gi_tick() */
    uint32_t bec;       /* the driver's bus_error_count gi_tick() was given */
    uint8_t  pushed;    /* error frames trip 6 recorded on this tick */
    uint8_t  err_n;     /* trip 6 ring entries after it (saturates at GI_ERR_RING) */
    uint8_t  flags;     /* TICK_SEEDED, TICK_LATCHED */
} tick_entry_t;

#define TICK_SEEDED   0x01  /* the first armed read: seeded, nothing recorded */
#define TICK_LATCHED  0x02  /* this tick latched "error-frame rate exceeded" */

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

static ecc_entry_t  s_ecc[ECC_RING];
static uint32_t     s_ecc_total;        /* entries ever written == seq of the last */
static tick_entry_t s_tick[TICK_RING];
static uint32_t     s_tick_total;
static uint32_t     s_tick_bec_seen;    /* last bus_error_count a tick was taken at */
static bool         s_tick_have_bec;

/* Snapshots for the page, copied under the lock and rendered outside it. */
static ecc_entry_t  s_snap_ecc[ECC_RING];
static tick_entry_t s_snap_tick[TICK_RING];

/* ------------------------------------------------------------ interrupt -- */

/*
 * A COPY OF twai_hal_get_events() FROM ESP-IDF v5.4.1 (hal/twai_hal_iram.c,
 * with its static helper twai_hal_decode_interrupt), for this chip's
 * configuration, differing in one place: the HAL's read of the error code
 * capture register, twai_ll_clear_err_code_cap(), discards the value, and this
 * copy keeps it. That read is the only one that sees the capture: the ESP32-C3
 * TRM (v1.4, 31.4.3.7) says the register "will no longer record new error
 * information until it is cleared (via a read from the CPU)", and its reset
 * value is 0, so any later read returns 0x00 -- which is what every row of B6
 * session 1 read (2026-10-20). The copy makes the same register reads, in the
 * same order, as the function it replaces, and no others; TEC, REC and status
 * are the values the decode itself read, not read again.
 *
 * Pinned, because a copy can drift from the function it stands in for: a
 * different IDF, or an errata option this copy leaves out, refuses to build.
 * None of those options exists for the ESP32-C3 in v5.4.1.
 */
#if ESP_IDF_VERSION != ESP_IDF_VERSION_VAL(5, 4, 1)
#error "gi_ecc_probe.c copies twai_hal_get_events() from ESP-IDF v5.4.1; re-check the copy against this IDF"
#endif
#if defined(CONFIG_TWAI_ERRATA_FIX_BUS_OFF_REC) || defined(CONFIG_TWAI_ERRATA_FIX_TX_INTR_LOST) || \
    defined(CONFIG_TWAI_ERRATA_FIX_RX_FRAME_INVALID) || defined(CONFIG_TWAI_ERRATA_FIX_RX_FIFO_CORRUPT)
#error "gi_ecc_probe.c's copy of twai_hal_get_events() leaves out this errata option's branch"
#endif

uint32_t __wrap_twai_hal_get_events(twai_hal_context_t *hal_ctx);

/* twai_hal_decode_interrupt(), unchanged but for handing status/TEC/REC back. */
static inline uint32_t ecc_decode_interrupt(twai_hal_context_t *hal_ctx,
                                            uint32_t *status_out,
                                            uint32_t *tec_out, uint32_t *rec_out)
{
    uint32_t events = 0;
    uint32_t interrupts = twai_ll_get_and_clear_intrs(hal_ctx->dev);
    uint32_t status = twai_ll_get_status(hal_ctx->dev);
    uint32_t tec = twai_ll_get_tec(hal_ctx->dev);
    uint32_t rec = twai_ll_get_rec(hal_ctx->dev);
    uint32_t state_flags = hal_ctx->state_flags;

    if (interrupts & TWAI_LL_INTR_EI) {
        if (status & TWAI_LL_STATUS_BS) {
            if (status & TWAI_LL_STATUS_ES) {
                TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_BUS_OFF);
                TWAI_HAL_SET_BITS(state_flags, TWAI_HAL_STATE_FLAG_BUS_OFF);
                TWAI_HAL_CLEAR_BITS(state_flags, TWAI_HAL_STATE_FLAG_RUNNING | TWAI_HAL_STATE_FLAG_TX_BUFF_OCCUPIED);
            } else {
                TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_BUS_RECOV_PROGRESS);
            }
        } else {
            if (status & TWAI_LL_STATUS_ES) {
                TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_ABOVE_EWL);
                TWAI_HAL_SET_BITS(state_flags, TWAI_HAL_STATE_FLAG_ERR_WARN);
            } else if (hal_ctx->state_flags & TWAI_HAL_STATE_FLAG_RECOVERING) {
                TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_BUS_RECOV_CPLT);
                TWAI_HAL_CLEAR_BITS(state_flags, TWAI_HAL_STATE_FLAG_RECOVERING | TWAI_HAL_STATE_FLAG_BUS_OFF);
            } else {
                TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_BELOW_EWL);
                TWAI_HAL_CLEAR_BITS(state_flags, TWAI_HAL_STATE_FLAG_ERR_WARN);
            }
        }
    }
    if (interrupts & TWAI_LL_INTR_RI) {
        TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_RX_BUFF_FRAME);
    }
    if (interrupts & TWAI_LL_INTR_TI) {
        TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_TX_BUFF_FREE);
        TWAI_HAL_CLEAR_BITS(state_flags, TWAI_HAL_STATE_FLAG_TX_BUFF_OCCUPIED);
    }
    if (interrupts & TWAI_LL_INTR_EPI) {
        if (tec >= TWAI_ERR_PASS_THRESH || rec >= TWAI_ERR_PASS_THRESH) {
            TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_ERROR_PASSIVE);
            TWAI_HAL_SET_BITS(state_flags, TWAI_HAL_STATE_FLAG_ERR_PASSIVE);
        } else {
            TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_ERROR_ACTIVE);
            TWAI_HAL_CLEAR_BITS(state_flags, TWAI_HAL_STATE_FLAG_ERR_PASSIVE);
        }
    }
    if (interrupts & TWAI_LL_INTR_BEI) {
        TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_BUS_ERR);
    }
    if (interrupts & TWAI_LL_INTR_ALI) {
        TWAI_HAL_SET_BITS(events, TWAI_HAL_EVENT_ARB_LOST);
    }
    hal_ctx->state_flags = state_flags;
    *status_out = status;
    *tec_out = tec;
    *rec_out = rec;
    return events;
}

/*
 * Runs in the TWAI interrupt, inside the driver's critical section, in place
 * of the one call twai.c makes to twai_hal_get_events(). The ring entry is
 * written after the last register access, so it adds time but no access.
 */
uint32_t __wrap_twai_hal_get_events(twai_hal_context_t *hal_ctx)
{
    uint32_t status, tec, rec;
    uint32_t events = ecc_decode_interrupt(hal_ctx, &status, &tec, &rec);
    uint32_t ecc = 0;

    if (events & TWAI_HAL_EVENT_BUS_OFF) {
        twai_ll_set_mode(hal_ctx->dev, TWAI_MODE_LISTEN_ONLY);
    }
    if (events & TWAI_HAL_EVENT_BUS_RECOV_CPLT) {
        twai_ll_enter_reset_mode(hal_ctx->dev);
    }
    if (events & TWAI_HAL_EVENT_BUS_ERR) {
        /* twai_ll_clear_err_code_cap(), keeping what it reads. */
        ecc = hal_ctx->dev->error_code_capture_reg.val;
    }
    if (events & TWAI_HAL_EVENT_ARB_LOST) {
        twai_ll_clear_arb_lost_cap(hal_ctx->dev);
    }

    if (events & TWAI_HAL_EVENT_BUS_ERR)
    {
        portENTER_CRITICAL_ISR(&s_mux);
        ecc_entry_t *e = &s_ecc[s_ecc_total % ECC_RING];
        e->t_us   = esp_timer_get_time();
        e->ecc    = (uint8_t)(ecc & 0xFF);
        e->tec    = (uint8_t)tec;
        e->rec    = (uint8_t)rec;
        e->status = status;
        e->events = (uint16_t)events;
        e->seq    = ++s_ecc_total;
        portEXIT_CRITICAL_ISR(&s_mux);
    }
    return events;
}

/* ----------------------------------------------------------------- tick -- */

static bool tick_latched_rate(const gi_events_t *ev)
{
    for (int i = 0; ev != NULL && i < ev->n; i++)
    {
        if (ev->e[i].kind == GI_EV_ABORT && ev->e[i].a == GI_ABORT_ERROR_RATE)
        {
            return true;
        }
    }
    return false;
}

void gi_ecc_note_tick(const gi_bus_t *bus, bool had_last_before,
                      uint32_t last_before, const gi_state_t *st,
                      const gi_events_t *ev)
{
    if (bus == NULL || !bus->err_valid)
    {
        return;
    }
    bool latched = tick_latched_rate(ev);
    if (s_tick_have_bec && bus->bus_error_count == s_tick_bec_seen && !latched)
    {
        return;
    }
    s_tick_have_bec = true;
    s_tick_bec_seen = bus->bus_error_count;

    /*
     * What trip 6 booked, read back from the core's own state rather than
     * recomputed: err_last moves only when err_history_tick() ran, and the
     * delta it books is clamped to GI_ERR_RING there.
     */
    uint8_t flags = latched ? TICK_LATCHED : 0;
    uint32_t pushed = 0;
    if (st->have_err_last && !had_last_before)
    {
        flags |= TICK_SEEDED;
    }
    else if (had_last_before && st->err_last != last_before)
    {
        pushed = st->err_last - last_before;
        if (pushed > GI_ERR_RING)
        {
            pushed = GI_ERR_RING;
        }
    }

    tick_entry_t t = {
        .t_us = esp_timer_get_time(), .bec = bus->bus_error_count,
        .pushed = (uint8_t)pushed, .err_n = st->err_n, .flags = flags,
    };
    portENTER_CRITICAL(&s_mux);
    s_tick[s_tick_total % TICK_RING] = t;
    s_tick_total++;
    portEXIT_CRITICAL(&s_mux);
}

/* ----------------------------------------------------------------- page -- */

static const char *ecc_type_name(uint8_t ecc)
{
    static const char *const n[4] = { "bit", "form", "stuff", "other" };
    return n[(ecc >> 6) & 0x3];
}

/*
 * The segment codes the ESP32-C3 TRM (v1.4, Table 31.4-12) defines, one bit
 * per code: 2-15, 17-19 and 22-28. 0 is not among them -- start of frame is
 * 3 there, though IDF's twai_ll_err_seg_t calls it 0 -- so a row reading 0x00
 * is never a real capture, and the cleared register reads 0x00.
 */
#define ECC_TRM_SEGS  0x1FCEFFFCu

static bool ecc_seg_defined(uint8_t ecc)
{
    return (ECC_TRM_SEGS >> (ecc & 0x1F)) & 1u;
}

static esp_err_t send_fmt(httpd_req_t *req, char *b, size_t len, int n)
{
    if (n < 0)
    {
        return ESP_FAIL;
    }
    if ((size_t)n >= len)
    {
        n = (int)len - 1;
    }
    return httpd_resp_send_chunk(req, b, n);
}

/*
 * GET /gi_ecc. Oldest entry first in each array. Each "isr" row is
 * [t_us, seq, ecc, type, dir, seg, tec, rec, status, events] with dir 0 = TX,
 * 1 = RX and seg the 5-bit segment code (ESP32-C3 TRM Table 31.4-12; IDF's
 * twai_ll_err_seg_t differs from it for start of frame); isr_distinct_ecc and
 * isr_undefined_seg are taken over the isr rows on the page;
 * each "ticks" row is [t_us, bus_error_count, pushed, err_n, seeded, latched].
 * driver_bus_error is null when the driver is not installed.
 */
static esp_err_t ecc_handler(httpd_req_t *req)
{
    uint32_t ecc_total, tick_total;
    portENTER_CRITICAL(&s_mux);
    ecc_total = s_ecc_total;
    tick_total = s_tick_total;
    memcpy(s_snap_ecc, s_ecc, sizeof(s_ecc));
    memcpy(s_snap_tick, s_tick, sizeof(s_tick));
    portEXIT_CRITICAL(&s_mux);

    twai_status_info_t si;
    char drv[16];
    if (twai_get_status_info(&si) == ESP_OK)
    {
        snprintf(drv, sizeof(drv), "%" PRIu32, si.bus_error_count);
    }
    else
    {
        snprintf(drv, sizeof(drv), "null");
    }

    char b[160];
    httpd_resp_set_type(req, "application/json");
    uint32_t ecc_n = ecc_total < ECC_RING ? ecc_total : ECC_RING;
    uint32_t tick_n = tick_total < TICK_RING ? tick_total : TICK_RING;
    int n = snprintf(b, sizeof(b),
                     "{\"probe\":\"gi_ecc\",\"now_us\":%" PRId64 ","
                     "\"isr_total\":%" PRIu32 ",\"isr_overwritten\":%" PRIu32 ","
                     "\"driver_bus_error\":%s,",
                     esp_timer_get_time(), ecc_total, ecc_total - ecc_n, drv);
    if (send_fmt(req, b, sizeof(b), n) != ESP_OK)
    {
        return ESP_FAIL;
    }
    /*
     * Two liveness figures over the rows on the page, so a dead readout shows
     * on the page itself (B6 session 1 read 0x00 in all 578 rows): how many
     * distinct ecc values they hold, and how many carry a segment code the
     * TRM does not define. A live readout has isr_undefined_seg 0.
     */
    uint32_t seen[8] = { 0 };
    uint32_t distinct = 0, undefined_seg = 0;
    for (uint32_t i = 0; i < ecc_n; i++)
    {
        uint8_t v = s_snap_ecc[(ecc_total - ecc_n + i) % ECC_RING].ecc;
        if (!(seen[v >> 5] & (1u << (v & 31))))
        {
            seen[v >> 5] |= 1u << (v & 31);
            distinct++;
        }
        if (!ecc_seg_defined(v))
        {
            undefined_seg++;
        }
    }
    n = snprintf(b, sizeof(b),
                 "\"isr_distinct_ecc\":%" PRIu32 ",\"isr_undefined_seg\":%" PRIu32 ","
                 "\"ticks_total\":%" PRIu32 ",\"ticks_overwritten\":%" PRIu32 ","
                 "\"isr\":[",
                 distinct, undefined_seg, tick_total, tick_total - tick_n);
    if (send_fmt(req, b, sizeof(b), n) != ESP_OK)
    {
        return ESP_FAIL;
    }
    for (uint32_t i = 0; i < ecc_n; i++)
    {
        const ecc_entry_t *e = &s_snap_ecc[(ecc_total - ecc_n + i) % ECC_RING];
        n = snprintf(b, sizeof(b),
                     "%s[%" PRId64 ",%" PRIu32 ",%u,\"%s\",%u,%u,%u,%u,%" PRIu32 ",%u]",
                     i ? "," : "", e->t_us, e->seq, (unsigned)e->ecc,
                     ecc_type_name(e->ecc), (unsigned)((e->ecc >> 5) & 0x1),
                     (unsigned)(e->ecc & 0x1F), (unsigned)e->tec, (unsigned)e->rec,
                     e->status, (unsigned)e->events);
        if (send_fmt(req, b, sizeof(b), n) != ESP_OK)
        {
            return ESP_FAIL;
        }
    }
    n = snprintf(b, sizeof(b), "],\"ticks\":[");
    if (send_fmt(req, b, sizeof(b), n) != ESP_OK)
    {
        return ESP_FAIL;
    }
    for (uint32_t i = 0; i < tick_n; i++)
    {
        const tick_entry_t *t = &s_snap_tick[(tick_total - tick_n + i) % TICK_RING];
        n = snprintf(b, sizeof(b), "%s[%" PRId64 ",%" PRIu32 ",%u,%u,%u,%u]",
                     i ? "," : "", t->t_us, t->bec, (unsigned)t->pushed,
                     (unsigned)t->err_n, (t->flags & TICK_SEEDED) ? 1u : 0u,
                     (t->flags & TICK_LATCHED) ? 1u : 0u);
        if (send_fmt(req, b, sizeof(b), n) != ESP_OK)
        {
            return ESP_FAIL;
        }
    }
    n = snprintf(b, sizeof(b), "]}\n");
    if (send_fmt(req, b, sizeof(b), n) != ESP_OK)
    {
        return ESP_FAIL;
    }
    return httpd_resp_send_chunk(req, NULL, 0);
}

static const httpd_uri_t ecc_uri = {
    .uri = "/gi_ecc", .method = HTTP_GET,
    .handler = ecc_handler, .user_ctx = NULL
};

esp_err_t gi_ecc_register(httpd_handle_t server)
{
    ESP_LOGW(TAG, "MEASUREMENT BUILD: /gi_ecc is registered and the TWAI "
                  "interrupt's event decoder is wrapped. This image must never "
                  "be flashed to the truck.");
    return httpd_register_uri_handler(server, &ecc_uri);
}
