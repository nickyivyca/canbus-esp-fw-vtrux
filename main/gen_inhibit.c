/*
 * gen_inhibit -- driver shim around gen_inhibit_core.
 *
 * SPLIT (2026-09-19). The decision logic moved to gen_inhibit_core.c, which
 * has no platform dependency and is compiled by a host test harness. What is
 * left here is everything that talks to the ESP32: the worker task, the TWAI
 * driver, the bus enable/disable dance, logging, and the JSON view.
 *
 * The rule for future edits: if a change is about WHAT to decide, it belongs
 * in the core and gains host test coverage for free. If it is about HOW to
 * receive, transmit, or wait, it belongs here and is only ever proved on
 * hardware. When in doubt, put it in the core -- the core is the part that
 * three real bugs were found in by hand on the day this split was written.
 */
#include "gen_inhibit.h"
#include "gen_inhibit_core.h"
#include "gi_sched.h"
/*
 * PRIVATE HAL, in the shipping image, sanctioned by spec 5.2 items 4 and 9 and
 * cleared by the user 2026-09-27. twai_ll_set_cmd_abort_tx() is below the public
 * driver API and E3 confines it to exactly one site -- the abort inside
 * dev_abort_if_awaiting() below -- because it is the kind of call that spreads.
 */
#include "hal/twai_ll.h"

#include <string.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/twai.h"

#include "can.h"

static const char *TAG = "gen_inhibit";

/*
 * The public mode enum and the core's must agree numerically -- the shim
 * casts between them rather than translating. Asserted, not commented.
 */
_Static_assert((int)GEN_INHIBIT_OFF     == (int)GI_OFF,     "mode enum drift");
_Static_assert((int)GEN_INHIBIT_OBSERVE == (int)GI_OBSERVE, "mode enum drift");
_Static_assert((int)GEN_INHIBIT_RESPOND == (int)GI_RESPOND, "mode enum drift");
_Static_assert((int)GEN_INHIBIT_INHIBIT == (int)GI_INHIBIT, "mode enum drift");
_Static_assert((int)GEN_INHIBIT_PASSIVE == (int)GI_PASSIVE, "mode enum drift");
_Static_assert(GEN_INHIBIT_VCM_ID   == GI_VCM_ID,   "id drift");
_Static_assert(GEN_INHIBIT_PROBE_ID == GI_PROBE_ID, "id drift");

/*
 * Priority. wican-fw's can_rx_task and can_tx_task run at 5; the WiFi driver
 * task is 23. We sit above the wican tasks so their queue work cannot delay a
 * response, and below WiFi so we do not starve the stack we depend on for
 * recovery.
 */
#define GEN_INHIBIT_TASK_PRIO   18
#define GEN_INHIBIT_STACK       (1024 * 4)

/*
 * Auto-arm on boot (build-time).
 *
 *   GEN_INHIBIT_OFF     boot idle; arm manually via POST /gen_inhibit_set.
 *                       THE DEFAULT -- used for bench and truck bring-up, so the
 *                       device transmits nothing until a human arms it.
 *   GEN_INHIBIT_INHIBIT arm INHIBIT as soon as the worker starts: relay power ->
 *                       boot -> inhibiting, no human in the loop. The deployed
 *                       configuration. The Via harness powers the dongle at
 *                       truck-wake, well before any crank, so this arms in time.
 *   GEN_INHIBIT_OBSERVE / _RESPOND also valid (measurement builds).
 *
 * Safe at any setting: reactive trail transmits only in response to a received
 * 0x051, so an armed-but-idle bus produces no traffic. Override at build time
 * with -DGEN_INHIBIT_AUTOARM_MODE=... via target_compile_definitions (a plain
 * idf.py -D sets a CMake cache var, not a macro).
 */
#ifndef GEN_INHIBIT_AUTOARM_MODE
#define GEN_INHIBIT_AUTOARM_MODE        GEN_INHIBIT_OFF
#endif
#ifndef GEN_INHIBIT_AUTOARM_OFFSET_US
#define GEN_INHIBIT_AUTOARM_OFFSET_US   500
#endif

/* Bump per meaningful firmware rev and add a row to the table in
 * projects/vtrux/notes/artifacts/gen-inhibit/wican_diag_schema.md.
 *
 * 2 is the first build in which a latched section 6 release clears
 * inhibit_live, which is visible on the wire as diag_flags byte3 0x27 -> 0x07.
 * A bench log that cannot tell rev 1 from rev 2 cannot tell whether it is
 * looking at the bug or the fix, and diag_git_hash alone puts that behind a
 * lookup.
 *
 * 3 is the first build with the spec 7.1 key-state rules: transmission gated
 * on 0x592 B0 bit4, a key-on clearing every latch, and a section 7 abort
 * latching in place rather than dropping the mode to OFF. On the wire that is
 * diag_schema_ver 2 -> 3 and two new diag_flags bits. It matters for log
 * reading because a rev 2 device latches GI_ABORT_INVERTER_LOST at the end of
 * every drive and then goes silent, where a rev 3 device stays armed and says
 * so. */
#ifndef DIAG_FW_VERSION
#define DIAG_FW_VERSION              4
#endif
#ifndef GIT_SHA
#define GIT_SHA "unknown"
#endif

/*
 * Build-time override for the SoC floor. Every other tunable now lives in
 * gi_config_defaults(), where its justification is; this one keeps its macro
 * because measurement builds have used it. Adding more overrides here is the
 * obvious place for an eventual over-the-wire config to land.
 */
/* #define GEN_INHIBIT_SOC_MIN_PCT 21 */

/* Receive timeout: 200 ms rather than a full second only so a disarm is
 * acted on promptly; a timeout costs nothing but a loop iteration. */
#define GEN_INHIBIT_RX_TIMEOUT_MS   200

/* ------------------------------------------------------------------------- */

/*
 * The decision state. Shared between the worker task and the HTTP handlers,
 * with the same coarse discipline the pre-split code had: `mode` is the
 * synchronisation point and everything else is read racily for reporting.
 * Unchanged by the split, and worth flagging rather than hiding -- a
 * set_mode() arriving mid-loop still resets histograms under the worker.
 */
static gi_state_t s_core;

static bool     s_we_enabled_bus;   /* we called can_enable(), so we may disable */
static volatile bool s_release_bus; /* disarm asked us to hand the bus back */

static TaskHandle_t  s_worker;      /* the worker task, for self-call detection */
static volatile bool s_quiesce;     /* an external caller needs the driver freed */
static volatile bool s_parked;      /* worker is provably outside twai_receive() */
/*
 * Spec 7 trip 7: TWAI_ALERT_TX_FAILED seen since the current inhibit frame
 * was queued. Carried between polls because the alert can arrive before the
 * frame leaves the controller, and cleared when the next inhibit is queued.
 */
static bool s_tx_failed_latched;

/*
 * Spec 13 item 1: we put the controller into listen-only for OBSERVE.
 * Remember that we did, and what the configured value was, so leaving OBSERVE
 * restores the user's setting rather than asserting one of our own.
 */
static bool     s_forced_silent;
static uint8_t  s_silent_saved;

/*
 * The driver's own loss and error counters, read once per status page.
 *
 * ctr_bad tells you a frame went missing; these tell you where. rx_missed is a
 * full SOFTWARE queue (the preempt case, which queue depth fixes); rx_overrun is
 * a HARDWARE FIFO overrun, meaning the ISR did not run (the cache-stall case,
 * which queue depth does not fix). Keeping them apart is the whole point of
 * load_margin.py's two columns, and until now nothing on the device could tell
 * them apart at all.
 *
 * Once per status request, not per frame: a per-dequeue read of this same
 * structure measurably perturbed TX completion observation in the measurement
 * build.
 */
static int drv_json(char *buf, int buflen)
{
    twai_status_info_t info;
    if (twai_get_status_info(&info) != ESP_OK)
    {
        /* Not installed is a normal state when disarmed, and is not an error. */
        return snprintf(buf, buflen, ",\"drv\":null");
    }
    return snprintf(buf, buflen,
                    ",\"drv\":{\"rx_missed\":%lu,\"rx_overrun\":%lu,"
                    "\"arb_lost\":%lu,\"bus_error\":%lu,\"tx_failed\":%lu,"
                    "\"msgs_to_rx\":%lu,\"msgs_to_tx\":%lu}",
                    (unsigned long)info.rx_missed_count,
                    (unsigned long)info.rx_overrun_count,
                    (unsigned long)info.arb_lost_count,
                    (unsigned long)info.bus_error_count,
                    (unsigned long)info.tx_failed_count,
                    (unsigned long)info.msgs_to_rx,
                    (unsigned long)info.msgs_to_tx);
}

/* ------------------------------------------- RX backlog instrument (opt) -- */
/*
 * MEASUREMENT-ONLY. NEVER FLASH A GI_INSTRUMENT_RXQ BUILD TO THE TRUCK.
 *
 * WHAT IT MEASURES AND WHY NOTHING ELSE HERE DOES. `response` and RESPOND's
 * probe both take their timestamp from esp_timer_get_time() AFTER
 * twai_receive() returns, in the worker. Time a frame spent waiting in the
 * driver's software queue is therefore already gone before either clock starts
 * -- and that wait is exactly what the receive queue's depth buffers, and
 * exactly what load_margin.py's WORST_PREEMPT_US is supposed to bound. On
 * 2026-09-26 that gap let a 6796 us RX-to-completion reading be attributed to
 * bus arbitration, which a 0x051 on this bus cannot suffer for more than about
 * 590 us; the reviewer caught it.
 *
 * The driver already counts what is wanted. twai_get_status_info() reports
 * msgs_to_rx, the frames still queued; read at a dequeue, the backlog that
 * moment is msgs_to_rx + 1 for the frame just taken. Divided by the arrival
 * rate it IS the worker's off-CPU time, measured rather than inferred:
 * ~423 us per slot at the 2364 frames/s truck replay.
 *
 * DEPTH IS LEFT AT THE SHIPPING 32, against the suggestion to widen it to 128
 * for headroom. Censoring is not yet in play: the 2026-09-26 arms lost nothing
 * at all (every replayed 0x051 answered, ctr_bad 0), which already bounds the
 * backlog they reached below 32 -- and the observed stall is ~16 slots' worth.
 * Measuring at the shipping depth means this build differs from the shipping
 * image in one respect only, which is worth more than headroom nothing has
 * reached. If the high-water comes back at or near the depth it IS censored,
 * frames were lost, and the 128-deep variant becomes the next run.
 *
 * COUNTS AS WELL AS THE MAXIMUM, because one extreme sample and a recurring
 * tail read identically through a maximum alone -- and the maxima from the last
 * run are already known to be unpinned.
 */
#if GI_INSTRUMENT_RXQ

static const uint32_t s_rxq_edges[] = { 2, 4, 8, 16, 32, 64 };
#define RXQ_NEDGES ((int)(sizeof(s_rxq_edges) / sizeof(s_rxq_edges[0])))

static uint32_t s_rxq_samples;
static uint32_t s_rxq_max;
static uint32_t s_rxq_ge[RXQ_NEDGES];

static void rxq_sample(void)
{
    twai_status_info_t info;
    if (twai_get_status_info(&info) != ESP_OK)
    {
        return;
    }
    /* msgs_to_rx is what is STILL waiting; the frame just dequeued counts too. */
    uint32_t backlog = (uint32_t)info.msgs_to_rx + 1;

    s_rxq_samples++;
    if (backlog > s_rxq_max)
    {
        s_rxq_max = backlog;
    }
    for (int i = 0; i < RXQ_NEDGES; i++)
    {
        if (backlog >= s_rxq_edges[i])
        {
            s_rxq_ge[i]++;
        }
    }
}

/* Reset with the other per-arm statistics, so an arm's figure is that arm's. */
static void rxq_reset(void)
{
    s_rxq_samples = 0;
    s_rxq_max = 0;
    for (int i = 0; i < RXQ_NEDGES; i++)
    {
        s_rxq_ge[i] = 0;
    }
}

static int rxq_json(char *buf, int buflen)
{
    int n = snprintf(buf, buflen,
                     ",\"rxq\":{\"depth\":%d,\"samples\":%lu,\"max\":%lu,\"ge\":[",
                     (int)can_rx_queue_len(),
                     (unsigned long)s_rxq_samples, (unsigned long)s_rxq_max);
    for (int i = 0; i < RXQ_NEDGES && n < buflen; i++)
    {
        n += snprintf(buf + n, buflen - n, "%s%lu", (i ? "," : ""),
                      (unsigned long)s_rxq_ge[i]);
    }
    if (n < buflen) n += snprintf(buf + n, buflen - n, "],\"ge_edges\":[");
    for (int i = 0; i < RXQ_NEDGES && n < buflen; i++)
    {
        n += snprintf(buf + n, buflen - n, "%s%lu", (i ? "," : ""),
                      (unsigned long)s_rxq_edges[i]);
    }
    if (n < buflen) n += snprintf(buf + n, buflen - n, "]}");
    return n;
}

#else
#define rxq_sample()            do { } while (0)
#define rxq_reset()             do { } while (0)
#define rxq_json(buf, buflen)   (0)
#endif

static uint32_t fnv1a32(const char *s)
{
    uint32_t h = 2166136261u;
    while (*s)
    {
        h ^= (uint8_t)*s++;
        h *= 16777619u;
    }
    return h;
}

static void bus_snapshot(gi_bus_t *b)
{
    twai_status_info_t st;
    memset(b, 0, sizeof(*b));
    b->can_enabled = can_is_enabled();
    b->bus_ours    = s_we_enabled_bus;
    if (twai_get_status_info(&st) == ESP_OK)
    {
        b->err_valid = true;
        b->bus_error_count = st.bus_error_count;
    }
}

/*
 * Render the core's event list to the log. The core cannot log -- it has no
 * printf -- so this is where an event becomes a line. Keeping the rendering
 * here rather than in the core is what lets the host harness assert on the
 * event CODES instead of on formatted text.
 */
static void report_events(const gi_events_t *ev)
{
    for (int i = 0; i < ev->n; i++)
    {
        const gi_event_t *e = &ev->e[i];
        switch ((gi_event_kind_t)e->kind)
        {
        case GI_EV_MODE:
            ESP_LOGW(TAG, "mode -> %d, probe offset %lu us",
                     (int)e->a, (unsigned long)e->b);
            if (e->a == (int32_t)GI_INHIBIT)
            {
                ESP_LOGW(TAG, "INHIBIT ARMED -- transmitting real 0x%03X "
                              "(zero torque) on this bus", GI_VCM_ID);
            }
            break;
        case GI_EV_DISABLED:
            if (e->a == (int32_t)GI_DISABLE_LOW_SOC)
            {
                ESP_LOGW(TAG, "INHIBIT DISABLED (latched): SoC %ld.%02ld%% "
                              "below %lu%%",
                         (long)(e->b / 100), (long)(e->b % 100),
                         (unsigned long)(s_core.cfg.soc_min_raw / 100));
            }
            else
            {
                ESP_LOGW(TAG, "INHIBIT DISABLED (latched): M mode engaged");
            }
            break;
        case GI_EV_SHUTDOWN_SUPPRESS:
            ESP_LOGW(TAG, "shutdown suppression %s (0x051 B0 = 0x%02X)",
                     e->a ? "ON" : "OFF", (unsigned)e->b);
            break;
        case GI_EV_INHIBIT_LIVE:
            ESP_LOGW(TAG, "INHIBIT LIVE -- interlocks passed");
            break;
        case GI_EV_ABORT:
            ESP_LOGE(TAG, "INHIBIT ABORT (latched): %s",
                     gi_abort_name((gi_abort_t)e->a));
            break;
        case GI_EV_KEY:
            ESP_LOGW(TAG, "key %s (0x%03X B0 bit4)",
                     e->a ? "ON -- transmission permitted"
                          : "OFF -- transmission gated", GI_KEY_ID);
            break;
        case GI_EV_SOC_VALID:
            if (e->a)
            {
                ESP_LOGW(TAG, "SoC valid -- contactors closed "
                              "(0x%03X bcm_mainc_stat = %ld)",
                         GI_CONTACTOR_ID, (long)e->b);
            }
            else
            {
                ESP_LOGW(TAG, "SoC no longer valid -- 0x%03lX went stale; "
                              "readings ignored until the contactors close "
                              "again", (unsigned long)e->b);
            }
            break;
        case GI_EV_KEY_CLEAR:
            ESP_LOGW(TAG, "key-on cleared latches: abort '%s', disable '%s'"
                          " -- re-entering the arm gate",
                     gi_abort_name((gi_abort_t)e->a),
                     gi_disable_name((gi_disable_t)e->b));
            break;
        case GI_EV_TX_FAIL:
            /* Counted in the core; only the inhibit case is worth a line. */
            if (e->a == (int32_t)GI_TX_INHIBIT)
            {
                ESP_LOGE(TAG, "inhibit transmit failed");
            }
            break;
        case GI_EV_RX_ERROR_DISARM:
            ESP_LOGE(TAG, "disarming after %ld receive errors", (long)e->a);
            break;
        case GI_EV_NONE:
        default:
            break;
        }
    }
    if (ev->dropped)
    {
        ESP_LOGE(TAG, "event list overflow (%u dropped) -- GI_EVENT_MAX too small",
                 (unsigned)ev->dropped);
    }
}

/*
 * Put the core's emitted frames on the wire and feed the outcome back.
 *
 * The busy-wait for a probe's due time lives here, not in the core: spinning
 * is a platform behaviour, and a host harness replaying a capture must not do
 * it. vTaskDelay cannot express it either -- the tick is 1 ms and we are
 * aiming at sub-millisecond placement.
 */
/* ------------------------------------------------ the scheduler's device HAL -- */

/*
 * The four operations spec 5.2 item 8 names, and nothing else. Everything that
 * decides WHAT goes next and WHEN is in gi_sched.c, compiled unchanged on the host.
 */
static gs_t       s_sched;
static portMUX_TYPE s_abort_lock = portMUX_INITIALIZER_UNLOCKED;

/* Counter watermarks, so the scheduler's totals become the core's events. */
static uint32_t s_seen_inh_sent;
static uint32_t s_seen_inh_refused;
static uint32_t s_seen_probe_handed;
static uint32_t s_seen_probe_lost;
static uint32_t s_seen_skip_withdrawn;
static uint32_t s_seen_skip_late;
static bool     s_was_live;
static gi_frame_t s_last_inhibit;
static bool     s_have_last_inhibit;
static gi_frame_t s_last_probe;
static bool     s_have_last_probe;

static bool dev_submit(void *ctx, const gi_frame_t *f)
{
    twai_message_t tx = { 0 };
    (void)ctx;
    tx.identifier = f->id;
    tx.data_length_code = f->dlc;
    memcpy(tx.data, f->data, 8);
    /*
     * THE ONLY twai_transmit IN THIS COMPONENT (spec 5.2 item 1, checked by E3's
     * "one transmit owner" row). Zero timeout: the scheduler hands over only when
     * the controller is free, so a wait here would mean its own bookkeeping was
     * wrong and blocking would hide that.
     */
    return twai_transmit(&tx, 0) == ESP_OK;
}

static gs_buf_t buf_from_status(uint32_t st)
{
    if (st & TWAI_LL_STATUS_TBS) { return GS_BUF_EMPTY; }
    /*
     * TS covers the ARBITRATION FIELD as well as the data phase, so this does not
     * mean the frame is certain to reach the wire -- which is why item 4 waits here
     * instead of issuing a command that the measurement showed has no effect.
     */
    if (st & TWAI_LL_STATUS_TS)  { return GS_BUF_TRANSMITTING; }
    return GS_BUF_AWAITING;
}

static gs_buf_t dev_buf_state(void *ctx)
{
    (void)ctx;
    return buf_from_status(twai_ll_get_status(TWAI_LL_GET_HW(0)));
}

/*
 * Read the state and, if AWAITING, issue the abort -- INDIVISIBLY.
 *
 * The controller is independent hardware and does not stop between a read and a
 * write, so doing this as two calls lets it enter arbitration in between and the
 * command then lands at TS = 1, where 7 of 8 measured aborts had no effect at all.
 * One critical section narrows that to a few cycles; it cannot close it, because the
 * controller keeps running, and item 4's loop is what recovers from the residual.
 * This is exactly what gi_txabort_probe.c did for the measurement.
 *
 * The interrupt register is never touched: reading it clears interrupts and would
 * steal them from the driver.
 */
static gs_buf_t dev_abort_if_awaiting(void *ctx)
{
    twai_dev_t *hw = TWAI_LL_GET_HW(0);
    gs_buf_t seen;
    (void)ctx;

    taskENTER_CRITICAL(&s_abort_lock);
    seen = buf_from_status(twai_ll_get_status(hw));
    if (seen == GS_BUF_AWAITING)
    {
        twai_ll_set_cmd_abort_tx(hw);
    }
    taskEXIT_CRITICAL(&s_abort_lock);
    return seen;
}

static bool dev_outstanding(void *ctx)
{
    twai_status_info_t info;
    (void)ctx;
    if (twai_get_status_info(&info) != ESP_OK)
    {
        /*
         * UNKNOWN MEANS YES. If we cannot tell, saying "outstanding" stops the
         * scheduler handing over another frame, which is the safe direction: two
         * frames of ours in the controller is the one state under which trip 7's
         * completion test means nothing.
         */
        return true;
    }
    return info.msgs_to_tx != 0;
}

static const gs_hal_t DEV_HAL = {
    .submit = dev_submit,
    .abort_if_awaiting = dev_abort_if_awaiting,
    .buf_state = dev_buf_state,
    .outstanding = dev_outstanding,
    .ctx = NULL,
};

/*
 * Turn the scheduler's monotonic counters into the core's events.
 *
 * WHY COUNTERS AND NOT A CALLBACK: gi_sched is platform-free and hands out no
 * function pointers, and deltas cannot lose an event to a callback nobody
 * registered. They can lag by one tick, and a tick is every worker iteration.
 */
static void sched_pump(int64_t now, gi_events_t *ev)
{
    /*
     * DRAIN THE ALERTS FIRST, and keep TX_FAILED latched until it is consumed.
     *
     * This is spec 7 trip 7's second form -- "a frame the controller reports as
     * failed still trips at once" -- and after the switchover this is its ONLY
     * reader. poll_tx_completion() used to do it; deleting that function without
     * moving this would have made the form unreachable, and nothing would have
     * complained for a long time, because IDF documents TX_FAILED as raised for
     * single-shot transmission and these frames go out with ss = 0.
     *
     * The alert is read HERE and not in the scheduler's HAL on purpose: alerts are
     * latched bits shared by every frame, which is exactly what gi_sched must not
     * have to reason about, and the shim already knows which frame it handed over.
     */
    uint32_t alerts = 0;
    if (twai_read_alerts(&alerts, 0) == ESP_OK && (alerts & TWAI_ALERT_TX_FAILED))
    {
        s_tx_failed_latched = true;
    }

    gs_tick(&s_sched, now, can_msgs_to_rx());

    const gs_stats_t *st = gs_stats(&s_sched);

    /*
     * SKIPS, ONE CORE CALL PER SKIP, with its kind. Derived from the counters for
     * the same reason every other event here is: the worker used to call
     * gi_on_inhibit_skip() once per DEADLINE, so the D1 purge withdrawing two
     * queued frames counted two skips in the scheduler and reported one to the
     * core, and the two numbers disagreed with nothing saying why.
     *
     * The trip verdict is read fresh on each call, so the third skip inside a
     * second is the one that carries `trip` -- which is what spec 7 trip 7 as
     * amended requires.
     */
    while (s_seen_skip_withdrawn < st->skipped_withdrawn)
    {
        s_seen_skip_withdrawn++;
        gi_on_inhibit_skip(&s_core, GI_SKIP_WITHDRAWN,
                           gs_skip_trip(&s_sched), now, ev);
    }
    while (s_seen_skip_late < st->skipped_late)
    {
        s_seen_skip_late++;
        gi_on_inhibit_skip(&s_core, GI_SKIP_MAYBE_LATE,
                           gs_skip_trip(&s_sched), now, ev);
    }

    while (s_seen_inh_sent < st->cls[GS_CLASS_INHIBIT].sent)
    {
        s_seen_inh_sent++;
        /*
         * ok = false makes this trip 7's TX_FAILED form. The latch is consumed
         * here so one failure reports once rather than condemning every later
         * frame -- the defect the shared-alert-bit comment in the old
         * poll_tx_completion() recorded.
         */
        gi_on_tx_done(&s_core, !s_tx_failed_latched, now, ev);
        s_tx_failed_latched = false;
    }
    while (s_seen_inh_refused < st->cls[GS_CLASS_INHIBIT].refused)
    {
        s_seen_inh_refused++;
        if (s_have_last_inhibit)
        {
            /* Spec 7 trip 7: the driver refused it. Trips at once. */
            gi_on_tx_result(&s_core, &s_last_inhibit, false, false, now, ev);
        }
    }

    /*
     * THE PROBE IS COUNTED WHEN IT REACHES THE DRIVER, not when it is queued.
     *
     * Spec 12.1 says the probe is counted at queueing -- and when that was written,
     * queueing WAS twai_transmit. Putting a queue in the path made those two
     * different instants, and the probe's whole purpose is to measure the delay
     * between a received 0x051 and our frame reaching the controller. Counting at
     * handover is what keeps the figure comparable with the 626 us / 1611 us record;
     * counting at queueing would have reported a latency the old numbers never
     * included and made every RESPOND sweep incomparable without saying so.
     *
     * It also leaves the tx_ok step invariant untouched -- tx_ok <= inhibit
     * completions + probes handed to the driver -- where bounding by probes
     * ACCEPTED would let tx_ok credit one that was queued and then dropped.
     */
    while (s_seen_probe_handed < st->cls[GS_CLASS_PROBE].handed)
    {
        s_seen_probe_handed++;
        if (s_have_last_probe)
        {
            gi_on_tx_result(&s_core, &s_last_probe, true, false, now, ev);
        }
    }
    /*
     * A probe the scheduler dropped or the driver refused is a lost measurement, and
     * tx_fail counted it before the switchover. It does not abort -- spec 7 is
     * explicit that a dropped probe costs a data point and not a safety property.
     */
    const uint32_t probe_lost = st->cls[GS_CLASS_PROBE].dropped
                              + st->cls[GS_CLASS_PROBE].refused;
    while (s_seen_probe_lost < probe_lost)
    {
        s_seen_probe_lost++;
        if (s_have_last_probe)
        {
            gi_on_tx_result(&s_core, &s_last_probe, false, false, now, ev);
        }
    }

    /*
     * D7: THE MOMENT THE CORE STOPS BEING LIVE, EVERY QUEUED INHIBIT GOES.
     *
     * Without this a refused inhibit stays queued for a retry, the core latches off
     * on the refusal, and the retry on the next tick succeeds -- putting an inhibit
     * frame on the wire AFTER the trip that was supposed to stop us transmitting.
     * The same shape applies to any trip landing while an inhibit sits behind an
     * abort loop. Found by the reviewing session, 2026-09-27; verified in the source
     * before fixing: a refused submit returns with the frame still in its queue.
     *
     * On the falling edge only, so a device that is simply not live does not re-run
     * the purge every tick and pile up skips it never had.
     */
    const bool live_now = s_core.inhibit_live;
    if (s_was_live && !live_now)
    {
        gs_withdraw_inhibits(&s_sched, now);
    }
    s_was_live = live_now;
}

static void dispatch_emits(const gi_emit_t *em, gi_events_t *ev)
{
    for (int i = 0; i < em->n; i++)
    {
        const gi_frame_t *f = &em->f[i];

        /*
         * THE PROBE'S DUE TIME IS STILL HONOURED HERE, not in the scheduler. The
         * core's contract says spinning is a platform behaviour and the host
         * harness must not do it, and gi_sched is compiled by that harness. The
         * scheduler additionally refuses to hand over a not-yet-due frame, so the
         * offset holds even if this spin is ever removed.
         */
        if (f->have_due)
        {
            /* At priority 18 this spins for at most `offset_us`. */
            while (esp_timer_get_time() < f->due_us)
            {
                /* spin */
            }
        }

        /*
         * QUEUE IT. Nothing here transmits any more: the scheduler is the one
         * transmit owner (item 1), it hands over at most one frame at a time
         * (item 3), and priority is inhibit > probe > telemetry (item 2).
         *
         * `behind` IS NOW STRUCTURALLY ZERO and is passed as false. Spec 5's
         * hazard was an inhibit queued behind something of ours still in the
         * controller; the scheduler cannot do that, which is the point of item 3.
         * tx_queued_behind staying 0 is therefore an assertion about the
         * scheduler rather than a measurement of the bus, and item 10's
         * full-replay case checks exactly that.
         */
        const gs_class_t cls = (f->kind == GI_TX_INHIBIT) ? GS_CLASS_INHIBIT
                             : (f->kind == GI_TX_PROBE)   ? GS_CLASS_PROBE
                                                          : GS_CLASS_TELEMETRY;
        const int64_t t_now = esp_timer_get_time();
        const bool queued = gs_queue_frame(&s_sched, cls, f, t_now);

        if (f->kind == GI_TX_INHIBIT)
        {
            s_last_inhibit = *f;
            s_have_last_inhibit = true;
            /*
             * Clear a stale TX_FAILED, as the old path did. Completion is decided
             * by msgs_to_tx and no leftover alert can affect it, but the bit is
             * read for trip 7's "controller reported failed" form.
             */
            uint32_t stale = 0;
            (void)twai_read_alerts(&stale, 0);
            s_tx_failed_latched = false;
        }

        if (f->kind == GI_TX_PROBE)
        {
            /*
             * Remembered, not reported. sched_pump() reports it when it reaches the
             * driver, so its tx_ok and its response histogram measure RX-to-driver
             * exactly as they did before the scheduler existed.
             */
            s_last_probe = *f;
            s_have_last_probe = true;
        }

        /*
         * TICK IMMEDIATELY. The scheduler is reactive, not polled: with an empty
         * buffer this hands the frame over in the same few microseconds the old
         * direct twai_transmit took. Without it the frame waited for the next worker
         * iteration, which for the RESPOND probe is the measurement itself.
         *
         * For every class, not only the probe -- giving one class its own path is how
         * a second transmit owner comes back, and item 1 allows exactly one.
         */
        sched_pump(esp_timer_get_time(), ev);

        if (f->kind == GI_TX_DIAG || f->kind == GI_TX_PROBE)
        {
            continue;   /* diag is best-effort; the probe is reported at handover */
        }
        gi_on_tx_result(&s_core, f, queued, false, t_now, ev);
    }
    if (em->dropped)
    {
        ESP_LOGE(TAG, "emit list overflow (%u dropped) -- GI_EMIT_MAX too small",
                 (unsigned)em->dropped);
    }
}

/*
 * poll_tx_completion() LIVED HERE and is gone with the switchover (spec 5.2).
 *
 * It decided completion from msgs_to_tx == 0 and carried the reasoning for why
 * that, and not TWAI_ALERT_TX_SUCCESS, is the attributable signal: alerts are
 * latched bits shared by every frame, so a diag page completing could credit an
 * inhibit that had not been sent. That reasoning did not go away -- it moved into
 * gi_sched, where `outstanding` is msgs_to_tx != 0 and the scheduler's own record
 * of what it handed over is what attributes the completion. The 2026-09-27
 * measurement then showed the same thing from the hardware side: an ABORTED frame
 * is reported identically to a transmitted one, 169 times out of 169.
 *
 * Its TWAI_ALERT_TX_FAILED read moved into sched_pump(), which is now trip 7's
 * only route to the "controller reported failed" form.
 */

/* ------------------------------------------------------------ public API -- */

void gen_inhibit_reset_stats(void)
{
    gi_reset_stats(&s_core);
}

bool gen_inhibit_owns_bus(void)
{
    return s_core.mode != GI_OFF;
}

esp_err_t gen_inhibit_set_mode(gen_inhibit_mode_t mode, uint32_t offset_us)
{
    if (mode > GEN_INHIBIT_PASSIVE)
    {
        return ESP_ERR_INVALID_ARG;
    }
    /*
     * A probe scheduled beyond the VCM's shortest observed inter-frame gap
     * (4.69 ms, per notes/gen-inhibit-truck-tests.md) would land after the
     * next genuine frame and make the measurement meaningless.
     */
    if (offset_us > 4000)
    {
        return ESP_ERR_INVALID_ARG;
    }
    /*
     * Bring the bus up ourselves. can_init() only records the bitrate -- it is
     * can_enable() that installs the TWAI driver, calls twai_start() and drops
     * the transceiver's STBY line. In the stock firmware that happens when a
     * client opens the SLCAN socket, so on an idle WiCAN the driver is *not*
     * installed and twai_receive() returns ESP_ERR_INVALID_STATE immediately.
     *
     * Arming without this wedged the device: the worker spun on that error at
     * priority 18, starving httpd (5) and can_tx_task (5). The AP stayed up
     * and TCP still accepted, because lwIP also runs at 18 and round-robins
     * with us -- so the device looked alive and answered nothing. Recovery
     * needed a power cycle, on a board with no serial console.
     */
    if (mode != GEN_INHIBIT_OFF)
    {
        /* Cancel any standing quiesce so the worker resumes into the driver. */
        s_quiesce = false;
        s_parked = false;

        /*
         * Spec 3 and 13 item 1: OBSERVE must perturb nothing, and that is a
         * property of the CONTROLLER, not of the application. In
         * TWAI_MODE_NORMAL the peripheral ACKs every frame it receives and can
         * emit error frames, so an "application-silent" OBSERVE is still an
         * active node on a live VCM-to-GENE bus. Force TWAI_MODE_LISTEN_ONLY
         * instead of relying on can_mode being configured to silent.
         *
         * can_set_silent() only takes effect while OFF_BUS, so a controller
         * already up in the wrong mode has to be torn down first. can_disable()
         * quiesces this worker itself, so that is safe from here.
         *
         * Two consequences worth stating. The diag frames of section 10 cannot
         * go out in OBSERVE -- twai_transmit fails in listen-only -- which is
         * the documented cost of a genuinely passive tap. And we undo ONLY our
         * own forcing when leaving OBSERVE: a user who configured can_mode as
         * silent keeps that, rather than having us quietly hand them a
         * transmitting device.
         */
        const bool want_listen_only = (mode == GEN_INHIBIT_OBSERVE);

        if (want_listen_only && can_is_enabled() && !can_is_silent())
        {
            ESP_LOGW(TAG, "OBSERVE: re-opening bus listen-only");
            can_disable();
        }
        else if (!want_listen_only && s_forced_silent && can_is_enabled())
        {
            /* Leaving OBSERVE for a mode that must transmit. */
            ESP_LOGW(TAG, "leaving listen-only for mode %d", (int)mode);
            can_disable();
        }

        /*
         * SPEC 5.1 ITEM 2: the controller keeps accept-all while we own the bus,
         * and this is where that is made true.
         *
         * can.c forces accept-all at install time when gen_inhibit_owns_bus(),
         * which READ CORRECTLY AND DID NOTHING ON THIS PATH. The mode is not
         * recorded in the core until gi_set_mode() below, well after
         * can_enable() above, so ownership was still false inside
         * twai_driver_install() and the override could only ever fire on a
         * re-enable while already armed. Worse, an already-up driver is not
         * reinstalled by the block below at all, so a narrowed filter simply
         * survived arming. Found by review 2026-09-26.
         *
         * Clearing it at the source fixes both. The acceptance filter is a
         * property of the install, so a live driver has to come down for the
         * change to reach the hardware -- and can_set_filter()/can_set_mask()
         * refuse while ON_BUS, which is why the teardown comes first. Neither
         * writes flash; they touch can_cfg in RAM only, so this is not a spec
         * 5.1 item 3 concern.
         *
         * Latent today -- slcan.c holds the only callers and its dispatch is
         * not linked in this build -- so this is about the guarantee not
         * resting on that, which is exactly what item 2 asks for.
         */
        if (can_filter_narrowed())
        {
            ESP_LOGW(TAG, "clearing narrowed acceptance filter before arming "
                          "mode %d (spec 5.1 item 2)", (int)mode);
            if (can_is_enabled())
            {
                can_disable();
            }
            can_set_filter(0);
            can_set_mask(0xFFFFFFFF);
        }

        if (!can_is_enabled())
        {
            if (!s_forced_silent)
            {
                s_silent_saved = can_is_silent();
            }
            can_set_silent(want_listen_only ? 1 : s_silent_saved);
            s_forced_silent = want_listen_only;
            can_enable();
            s_we_enabled_bus = true;
        }
        /*
         * Belt and braces, and it must hold whatever happened above: if the
         * filter is still narrowed the bus is not ours to use, because every
         * gate and trip in section 7 reads IDs the filter may have dropped and
         * a dropped ID fails silently -- a device that never goes live, or one
         * that trips stale.
         */
        if (can_filter_narrowed())
        {
            ESP_LOGE(TAG, "cannot arm: acceptance filter still narrowed");
            return ESP_ERR_INVALID_STATE;
        }
        if (!can_is_enabled())
        {
            ESP_LOGE(TAG, "cannot arm: CAN bus would not come up");
            return ESP_ERR_INVALID_STATE;
        }
        /*
         * Spec 7 trip 7 (review C1). can.c installs the driver with
         * TWAI_ALERT_NONE, so twai_read_alerts() would block for the timeout
         * and report nothing -- every inhibit frame would then look
         * outstanding and abort on the next 0x051. Turn on exactly the two we
         * read. Done here rather than in can.c so the stock firmware's
         * behaviour is untouched when the inhibitor is not armed.
         */
        if (twai_reconfigure_alerts(TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED,
                                    NULL) != ESP_OK)
        {
            ESP_LOGE(TAG, "cannot arm: TX alerts would not enable");
            return ESP_ERR_INVALID_STATE;
        }
        /*
         * Refuse to arm OBSERVE unless the flag that selects g_config_silent
         * is actually set. Note what this does and does not prove: the TWAI
         * API exposes no read-back of the controller's mode, so this confirms
         * the input to twai_driver_install() rather than interrogating the
         * peripheral. What makes it sound is that the driver is (re)installed
         * above, in this function, immediately after the flag is written --
         * so the flag and the installed config cannot disagree. A stale
         * already-up driver, which is exactly the case item 1 is about, is
         * torn down rather than trusted.
         */
        if (want_listen_only && !can_is_silent())
        {
            ESP_LOGE(TAG, "cannot arm OBSERVE: not in listen-only");
            return ESP_ERR_INVALID_STATE;
        }
    }

    gi_events_t ev = { 0 };
    rxq_reset();
    /*
     * A fresh scheduler for every mode change. Its counters are per-arm like every
     * other figure on the status page, and a stale `held` flag from a previous arm
     * would stop the first frame of this one going out.
     */
    /*
     * D8: gs_rearm(), NOT gs_init(). The counters and the queues are per-arm; what
     * the CONTROLLER is holding is not, and a diag page may still be awaiting
     * arbitration in it. A scheduler that forgot that would hand over the next frame
     * and twai_transmit would queue it behind the old one in the driver's FIFO --
     * the priority inversion item 3 exists to prevent, with the old frame's
     * departure credited to the new one.
     */
    gs_rearm(&s_sched, &DEV_HAL);
    s_seen_inh_sent = 0;
    s_seen_inh_refused = 0;
    s_seen_probe_handed = 0;
    s_seen_probe_lost = 0;
    s_seen_skip_withdrawn = 0;
    s_seen_skip_late = 0;
    s_was_live = false;
    s_have_last_inhibit = false;
    s_have_last_probe = false;
    gi_set_mode(&s_core, (gi_mode_t)mode, offset_us, esp_timer_get_time(), &ev);
    report_events(&ev);

    /*
     * Releasing the bus is left to the worker, deliberately. can_disable()
     * calls twai_driver_uninstall(), and tearing the driver down from this
     * task while the worker is parked inside twai_receive() is a use-after-
     * free on the driver's internals. The worker disarms itself instead, from
     * a point where it is provably not inside the driver.
     */
    if (mode == GEN_INHIBIT_OFF && s_we_enabled_bus)
    {
        s_release_bus = true;
    }
    return ESP_OK;
}

void gen_inhibit_quiesce(void)
{
    /*
     * Two cases:
     *
     *   - Called from the worker itself (its own release path calls
     *     can_disable): it is already at its safe top-of-loop point, so just
     *     make sure it will not re-enter the driver, and return. Blocking here
     *     would deadlock -- the worker is the thing that sets s_parked.
     *
     *   - Called from any other task: force mode OFF, relinquish ownership so
     *     the caller owns the teardown, and block until the worker confirms it
     *     is outside twai_receive(). Bounded by the receive timeout; we allow
     *     up to 1 s.
     */
    if (s_worker == NULL)
    {
        return;
    }
    if (xTaskGetCurrentTaskHandle() == s_worker)
    {
        s_core.mode = GI_OFF;
        return;
    }

    /*
     * Spec 8 and 11: this is a mode OFF nobody asked for -- another task is
     * about to uninstall the driver and the worker has to be out of it first.
     * Record it, because in OFF the device goes silent on the wire and looks
     * identical to one that was disarmed on purpose.
     */
    s_core.self_off = GI_SELF_OFF_QUIESCE;
    s_core.mode = GI_OFF;
    s_we_enabled_bus = false;   /* caller owns the teardown now, not us */
    s_release_bus = false;
    s_quiesce = true;

    for (int i = 0; i < 100 && !s_parked; i++)
    {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (!s_parked)
    {
        ESP_LOGE(TAG, "quiesce timed out; worker did not confirm parked");
    }
}

/* ---------------------------------------------------------------- worker -- */

static void gen_inhibit_task(void *arg)
{
    twai_message_t rx;

    s_worker = xTaskGetCurrentTaskHandle();
    ESP_LOGI(TAG, "worker started (prio %d)", GEN_INHIBIT_TASK_PRIO);

    while (1)
    {
        if (s_core.mode == GI_OFF)
        {
            gi_notify_off(&s_core);
            /*
             * Safe teardown point: we are not inside twai_receive() here, so
             * uninstalling the driver cannot pull the floor out from under a
             * blocked call. Publish that fact so an external
             * gen_inhibit_quiesce() caller (sleep monitor, SLCAN close) knows
             * it can now uninstall.
             */
            s_parked = true;

            if (s_release_bus)
            {
                s_release_bus = false;
                s_we_enabled_bus = false;
                can_disable();
                /*
                 * Undo an OBSERVE listen-only forcing now that the driver is
                 * down -- can_set_silent() is a no-op while ON_BUS, so this
                 * has to happen after can_disable() and not before it.
                 */
                if (s_forced_silent)
                {
                    can_set_silent(s_silent_saved);
                    s_forced_silent = false;
                }
                ESP_LOGW(TAG, "bus released");
            }
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        /*
         * The periodic half: diag heartbeat and the spec 7 interlocks. Runs
         * BEFORE the blocking receive so both still fire on a quiet bus --
         * which is precisely the case the bus-loss and 0x471 trips exist for.
         * A trip that only ran on frame arrival could not detect the absence
         * of frames.
         */
        {
            gi_bus_t bus;
            gi_emit_t em = { 0 };
            gi_events_t ev = { 0 };
            /*
             * Drive the scheduler FIRST, so gi_tick() sees an accurate
             * outstanding-frame flag: it withholds the diag page while one is in
             * flight, and a stale flag would withhold it for nothing. This is also
             * where a completion or a refusal becomes a core event.
             */
            sched_pump(esp_timer_get_time(), &ev);
            bus_snapshot(&bus);
            gi_tick(&s_core, esp_timer_get_time(), &bus, &em, &ev);
            dispatch_emits(&em, &ev);
            report_events(&ev);
        }

        /*
         * Blocking receive: the TWAI ISR wakes us directly, so this costs the
         * ISR-to-task latency rather than wican-fw's 1 ms poll interval. That
         * difference is the whole reason this component exists.
         */
        s_parked = false;   /* about to enter the driver */
        esp_err_t rx_err = twai_receive(&rx, pdMS_TO_TICKS(GEN_INHIBIT_RX_TIMEOUT_MS));
        if (rx_err != ESP_OK)
        {
            /*
             * ESP_ERR_TIMEOUT is ordinary: it just means a quiet moment on the
             * bus, and the call already blocked for it. Anything else returns
             * *immediately* -- ESP_ERR_INVALID_STATE when the driver is not
             * installed -- so retrying without a delay is a busy-wait at
             * priority 18 that starves every lower-priority task, httpd
             * included. Always yield on the error path.
             */
            if (rx_err == ESP_ERR_TIMEOUT)
            {
                continue;
            }

            gi_events_t ev = { 0 };
            if (s_core.rx_errors == 0)
            {
                ESP_LOGE(TAG, "twai_receive failed: %s", esp_err_to_name(rx_err));
            }
            gi_on_rx_error(&s_core, esp_timer_get_time(), &ev);
            report_events(&ev);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        /*
         * FIRST THING AFTER THE DEQUEUE. Anything between the receive and this
         * read lets the queue drain further and understates the backlog.
         */
        rxq_sample();
        gi_on_rx_ok(&s_core);

        {
            gi_emit_t em = { 0 };
            gi_events_t ev = { 0 };

            const int64_t t_rx = esp_timer_get_time();

            /*
             * Collect a completion before anything judges the frame. One that
             * landed while we were blocked inside twai_receive() has to be
             * credited first, or a frame that finished in good time is scored
             * against the deadline below.
             */
            sched_pump(t_rx, &ev);

            /*
             * THE DEADLINE (spec 5.2 item 5), and it runs BEFORE gi_on_frame()
             * queues the answer to this command. That ordering is not a comment:
             * gs_command_received() sets a token which gs_queue_frame() consumes,
             * so getting it wrong increments `order_violations` instead of failing
             * silently, and E1 asserts that counter stays 0.
             *
             * The backlog is read here because an on-time verdict reached with
             * frames already waiting cannot be verified -- the dequeue lags the
             * arrival by the backlog, so the VCM's next command may already have
             * been on the wire. gi_sched counts those separately as
             * `ontime_unverified` rather than letting them pass as proof.
             */
            if (rx.identifier == GI_VCM_ID)
            {
                /*
                 * The verdict is not reported here. sched_pump() derives one core
                 * call per skip from the per-kind counters, so a deadline that
                 * withdraws TWO queued frames reports two -- which reporting from
                 * this return value could not do, and did not.
                 */
                (void)gs_command_received(&s_sched, t_rx, can_msgs_to_rx());
                sched_pump(t_rx, &ev);
            }

            gi_on_frame(&s_core, rx.identifier, rx.data_length_code, rx.data,
                        t_rx, &em, &ev);
            dispatch_emits(&em, &ev);
            report_events(&ev);
        }
    }
}

/* ------------------------------------------------------------------ JSON -- */

/*
 * Keep an append offset inside its buffer.
 *
 * snprintf returns the length it WOULD have written, so a truncated append
 * leaves `n` past the end. Then `buflen - n` is negative, converts to an
 * enormous size_t at the next call, and that call writes past the buffer --
 * and a caller that sends `n` bytes reads past it as well. Clamping after
 * every append makes `buf + n` a valid pointer and `buflen - n` non-negative
 * throughout, so an overlong page truncates instead of corrupting memory.
 *
 * Found by review 2026-09-26, latent: the page was ~1220 bytes of 1400, so the
 * NEXT field added would have been the one to do damage.
 */
static int gi_clamp(int n, int buflen)
{
    if (n < 0)      { return 0; }
    if (n > buflen) { return buflen; }
    return n;
}

static int hist_json(const gi_hist_t *h, const char *name, char *buf, int buflen)
{
    int n = snprintf(buf, buflen,
                     "\"%s\":{\"count\":%lu,\"min_us\":%lu,\"max_us\":%lu,\"mean_us\":%lu,\"buckets\":[",
                     name, (unsigned long)h->count,
                     (unsigned long)(h->count ? h->min_us : 0),
                     (unsigned long)h->max_us,
                     (unsigned long)(h->count ? (uint32_t)(h->sum_us / h->count) : 0));
    n = gi_clamp(n, buflen);
    for (int i = 0; i < GI_NBUCKETS; i++)
    {
        n = gi_clamp(n + snprintf(buf + n, buflen - n, "%s%lu",
                                  (i ? "," : ""),
                                  (unsigned long)h->buckets[i]), buflen);
    }
    n = gi_clamp(n + snprintf(buf + n, buflen - n, "]}"), buflen);
    return n;
}

int gen_inhibit_get_stats_json(char *buf, int buflen)
{
    const gi_state_t *st = &s_core;
    const int64_t t_now = esp_timer_get_time();

    /*
     * arm_block reports the abort text once a run has ended, matching the
     * pre-split behaviour where inhibit_abort() wrote the same string into
     * both fields. Cleared together on the next arm.
     */
    const char *block = (st->abort_reason != GI_ABORT_NONE)
                        ? gi_abort_name(st->abort_reason)
                        : gi_block_name(st->arm_block);

    int n = snprintf(buf, buflen,
                     "{\"mode\":%d,\"offset_us\":%lu,\"probe_id\":\"0x%03X\","
                     "\"tx_ok\":%lu,\"tx_fail\":%lu,\"other_frames\":%lu,"
                     "\"ctr_ok\":%lu,\"ctr_bad\":%lu,"
                     "\"bus_on\":%s,\"bus_ours\":%s,\"rx_errors\":%lu,"
                     "\"disabled\":%s,\"disable_reason\":\"%s\",\"soc_x100\":%lu,"
                     "\"shutdown_suppressed\":%s,"
                     "\"inhibit_live\":%s,\"arm_block\":\"%s\","
                     "\"abort_reason\":\"%s\",\"abort_latched\":%s,"
                     "\"key_on\":%s,\"key_fresh\":%s,\"gene_rpm\":%ld,"
                     "\"vcm_torque\":%ld,\"vcm_fault\":%u,"
                     "\"tx_queued_behind\":%lu,\"would_tx\":%lu,"
                     "\"emit_refused\":%lu,\"self_off\":\"%s\","
                     "\"soc_valid\":%s,\"soc_since_valid\":%s,"
                     "\"mainc_stat\":%u,\"soc_fresh\":%s,"
                     "\"contactor_fresh\":%s,\"cmd_fresh\":%s,"
                     "\"fault_fresh\":%s,\"shift_fresh\":%s,"
                     "\"fb_fresh\":%s,\"rpm_fresh\":%s,"
                     "\"fb_ever\":%s,\"rpm_ever\":%s,",
                     (int)st->mode, (unsigned long)st->offset_us, GI_PROBE_ID,
                     (unsigned long)st->tx_ok, (unsigned long)st->tx_fail,
                     (unsigned long)st->other_frames,
                     (unsigned long)st->ctr_steps_ok,
                     (unsigned long)st->ctr_steps_bad,
                     can_is_enabled() ? "true" : "false",
                     s_we_enabled_bus ? "true" : "false",
                     (unsigned long)st->rx_errors,
                     st->disabled ? "true" : "false",
                     gi_disable_name(st->disable_code),
                     (unsigned long)st->soc_raw,
                     st->shutdown_suppressed ? "true" : "false",
                     st->inhibit_live ? "true" : "false", block,
                     gi_abort_name(st->abort_reason),
                     st->abort_latched ? "true" : "false",
                     /*
                      * Reported apart rather than as one gi_key_on(): "the
                      * key is off" and "we have not heard 0x592" gate
                      * transmission identically and mean entirely different
                      * things, and a reader who cannot tell them apart will
                      * chase the wrong one.
                      */
                     st->key_on ? "true" : "false",
                     gi_fresh(st, st->have_key, st->seen_key, t_now)
                         ? "true" : "false",
                     (long)st->gene_rpm,
                     (long)st->vcm_torque, (unsigned)st->vcm_fault,
                     /*
                      * Spec 5 (review A4): inhibit frames queued behind
                      * something of ours that had not finished. The number the
                      * full-replay bench run exists to put a value on.
                      */
                     (unsigned long)st->tx_queued_behind,
                     /*
                      * Spec 3.1: what PASSIVE counted where INHIBIT would have
                      * transmitted. Its own field, never folded into tx_ok --
                      * a dry run and a drive must not be able to look alike.
                      */
                     (unsigned long)st->would_tx,
                     /* Tripwire; any non-zero value is a bug in the core. */
                     (unsigned long)st->emit_refused,
                     /*
                      * Spec 11: an OFF the device imposed on itself names
                      * itself. Empty means OFF because someone asked, or not
                      * off at all.
                      */
                     gi_self_off_name(st->self_off),
                     /*
                      * Spec 11, spec 6.2: the SoC-valid marker, and the two
                      * freshness facts it is made of. Reported apart for the
                      * same reason key_on and key_fresh are: "the contactors
                      * have not closed" and "the BMS went quiet" both read as
                      * an invalid marker and are different problems.
                      */
                     st->soc_valid ? "true" : "false",
                     st->soc_since_valid ? "true" : "false",
                     (unsigned)st->mainc_stat,
                     gi_fresh(st, st->have_soc, st->seen_soc, t_now)
                         ? "true" : "false",
                     gi_fresh(st, st->have_cont, st->seen_cont, t_now)
                         ? "true" : "false",
                     /*
                      * Spec 11 (review B3): the freshness of each interlock
                      * signal, because since trip 5 every one of them can end
                      * a drive on its own and "which one went away" is the
                      * first question anyone reading this will have.
                      *
                      * fb_ever and rpm_ever go with them. A GENE signal reads
                      * not-fresh both before the inverter wakes and after it
                      * dies, and those are opposite situations -- the *_ever
                      * flags are what separate them, and they are the reason
                      * neither trip fires in the first case.
                      */
                     gi_fresh(st, st->have_cmd, st->seen_cmd, t_now)
                         ? "true" : "false",
                     gi_fresh_w(st, st->have_fault, st->seen_fault, t_now,
                                (int64_t)st->cfg.fault_fresh_us)
                         ? "true" : "false",
                     gi_fresh(st, st->have_shift, st->seen_shift, t_now)
                         ? "true" : "false",
                     gi_fresh(st, st->have_fb, st->seen_fb, t_now)
                         ? "true" : "false",
                     gi_fresh(st, st->have_rpm, st->seen_rpm, t_now)
                         ? "true" : "false",
                     st->fb_ever ? "true" : "false",
                     st->rpm_ever ? "true" : "false");

    n = gi_clamp(n, buflen);
    n = gi_clamp(n + hist_json(&st->rx_gap, "rx_gap", buf + n, buflen - n),
                 buflen);
    n = gi_clamp(n + snprintf(buf + n, buflen - n, ","), buflen);
    n = gi_clamp(n + hist_json(&st->response, "response", buf + n, buflen - n),
                 buflen);
    n = gi_clamp(n + snprintf(buf + n, buflen - n, ",\"bucket_edges_us\":["),
                 buflen);
    for (int i = 0; i < GI_NBUCKETS - 1; i++)
    {
        n = gi_clamp(n + snprintf(buf + n, buflen - n, "%s%lu", (i ? "," : ""),
                                  (unsigned long)gi_bucket_us[i]), buflen);
    }
    /*
     * THAT CLOSING BRACE IS THE STATUS OBJECT'S, not the array's -- the "]"
     * closes bucket_edges and the "}" closes the whole page. The first
     * version of this appended the rxq object AFTER it, producing a valid
     * document followed by a stray fragment. json.loads called that "Extra
     * data", which reads like a transport problem rather than a missing
     * brace. Anything added here goes BEFORE the brace.
     */
    n = gi_clamp(n + snprintf(buf + n, buflen - n, ",\"inf\"]"), buflen);
    n = gi_clamp(n + drv_json(buf + n, buflen - n), buflen);
    /*
     * The transmit scheduler's counters (spec 5.2 item 6, section 11): per class
     * queued/sent/aborted/requeued/dropped/refused and the two waits, plus the
     * skips, the aborts and how hard they were, and the two on-time counts --
     * `ontime` and `ontime_unverified`, WHICH ARE NEVER SUMMED. An unverified
     * verdict is one reached with frames already in the receive queue, where the
     * dequeue lags the arrival and a physically late frame can score as on time.
     */
    n = gi_clamp(n + snprintf(buf + n, buflen - n, ","), buflen);
    n = gi_clamp(n + gs_json(&s_sched, buf + n, buflen - n), buflen);
    n = gi_clamp(n + rxq_json(buf + n, buflen - n), buflen);
    n = gi_clamp(n + snprintf(buf + n, buflen - n, "}\n"), buflen);

    /*
     * The CONTENT length, never the buffer length: a caller that hands `n` to
     * httpd_resp_send() must not be told to read the terminator or past it.
     */
    return (buflen > 0 && n >= buflen) ? buflen - 1 : n;
}

/* ------------------------------------------------------------------ init -- */

void gen_inhibit_init(void)
{
    gi_config_t cfg;
    gi_config_defaults(&cfg);
#ifdef GEN_INHIBIT_SOC_MIN_PCT
    cfg.soc_min_raw = GEN_INHIBIT_SOC_MIN_PCT * 100;
#endif
    cfg.fw_version = DIAG_FW_VERSION;
    cfg.git_hash   = fnv1a32(GIT_SHA);
    cfg.autoarm_configured = (GEN_INHIBIT_AUTOARM_MODE != GEN_INHIBIT_OFF);

    gi_init(&s_core, &cfg);

    xTaskCreate(gen_inhibit_task, "gen_inhibit", GEN_INHIBIT_STACK, NULL,
                GEN_INHIBIT_TASK_PRIO, NULL);

    if (GEN_INHIBIT_AUTOARM_MODE != GEN_INHIBIT_OFF)
    {
        ESP_LOGW(TAG, "auto-arm on boot: mode %d", (int)GEN_INHIBIT_AUTOARM_MODE);
        gen_inhibit_set_mode(GEN_INHIBIT_AUTOARM_MODE, GEN_INHIBIT_AUTOARM_OFFSET_US);
    }
}
