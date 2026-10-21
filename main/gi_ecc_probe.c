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
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "hal/twai_hal.h"
#include "hal/twai_ll.h"

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
    uint8_t  ecc;       /* error code capture register, read after the HAL's release */
    uint8_t  tec;
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

uint32_t __real_twai_hal_get_events(twai_hal_context_t *hal_ctx);
uint32_t __wrap_twai_hal_get_events(twai_hal_context_t *hal_ctx);

/*
 * Runs in the TWAI interrupt, inside the driver's critical section, in place
 * of every call twai.c makes to twai_hal_get_events(). The real function runs
 * first and unchanged; see the header for why the order matters.
 */
uint32_t __wrap_twai_hal_get_events(twai_hal_context_t *hal_ctx)
{
    uint32_t events = __real_twai_hal_get_events(hal_ctx);
    if (events & TWAI_HAL_EVENT_BUS_ERR)
    {
        twai_dev_t *hw = hal_ctx->dev;
        portENTER_CRITICAL_ISR(&s_mux);
        ecc_entry_t *e = &s_ecc[s_ecc_total % ECC_RING];
        e->t_us   = esp_timer_get_time();
        e->ecc    = (uint8_t)(hw->error_code_capture_reg.val & 0xFF);
        e->tec    = (uint8_t)twai_ll_get_tec(hw);
        e->rec    = (uint8_t)twai_ll_get_rec(hw);
        e->status = twai_ll_get_status(hw);
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
 * 1 = RX and seg the 5-bit segment code (hal/twai_ll.h, twai_ll_err_seg_t);
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
    n = snprintf(b, sizeof(b),
                 "\"ticks_total\":%" PRIu32 ",\"ticks_overwritten\":%" PRIu32 ","
                 "\"isr\":[",
                 tick_total, tick_total - tick_n);
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
