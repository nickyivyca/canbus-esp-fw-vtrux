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
 * PORT 0 IS vtrux/twai_port's, behind vtrux/can_port (spec 3.3, port step 1b,
 * user 2026-10-31). Every TWAI driver call this file used to make goes through
 * the port: the one twai_transmit (twai_port T6), the alert read (T5), the
 * status (T7), and the private abort command, which is twai_port's
 * abort-if-awaiting (T8; spec 5.2 items 4 and 9, 12.4's confinement row).
 */
#include "twai_port.h"
#include "can_port.h"
#include "esp_attr.h"
#if GI_TEST_RESTART_QUIESCE
/* Restart test build only, spec 12.4 -- see main/CMakeLists.txt. */
#include "gi_restart_test.h"
#endif

#include <string.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"      /* esp_reset_reason(), spec 11 */
#include "esp_app_desc.h"    /* the build's name, spec 10 0x7F3 */
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
 * gen-inhibit/diag/wican_diag_schema.md.
 *
 * 2 is the first build in which a latched section 6 release clears
 * inhibit_live, which is visible on the wire as diag_flags byte3 0x27 -> 0x07.
 * A bench log that cannot tell rev 1 from rev 2 cannot tell whether it is
 * looking at the bug or the fix, and the 0x7F3 hash alone puts that behind a
 * lookup.
 *
 * 3 is the first build with the spec 7.1 key-state rules: transmission gated
 * on 0x592 B0 bit4, a key-on clearing every latch, and a section 7 abort
 * latching in place rather than dropping the mode to OFF. On the wire that is
 * diag_schema_ver 2 -> 3 and two new diag_flags bits. It matters for log
 * reading because a rev 2 device latches GI_ABORT_INVERTER_LOST at the end of
 * every drive and then goes silent, where a rev 3 device stays armed and says
 * so.
 *
 * 5 is the bus_on change (spec 11, 2026-10-06): diag_flags bit1 and the status
 * page's bus_on report the CONTROLLER running rather than the driver being
 * installed. No field moves, which is exactly why this rev exists -- a rev 4
 * log and a rev 5 log are byte-identical in layout, so a reader holding the
 * old meaning would read the bit correctly and interpret it wrongly.
 *
 * On the wire the bit reads 1 under both meanings in practice, since a
 * controller that is not running sends no pages; the difference shows on the
 * status page, where the old meaning read true through 42.1 s of bus-off on
 * 2026-10-06. (Corrected 2026-10-07: this comment previously said a reader
 * would take a bus-off device for one on the bus, which cannot happen in a CAN
 * log -- the witness counted 0 WiCAN diag frames across that bus-off.)
 *
 * 6 is spec 7's recency rule for trip 6 (user, 2026-10-08). Another rev with
 * no layout change, and for the same reason as 5: WHEN the error-frame rate
 * trip fires has changed, so diag_abort_reason 6 means something different
 * across the boundary and two captures of the same bus can disagree.
 *
 * A rev 5 device trips on any burst of 10 error frames within 10 s of each
 * other that has occurred since the last key-on, at every later live check,
 * which makes trip 6 a latch until key-on -- measured: a re-arm re-latched
 * 50 ms later with no new error frames, and a burst 32 s stale still blocked
 * going live. A rev 6 device additionally requires the burst's newest error
 * frame to be no more than 10 s before the check, so an expired burst causes
 * no trip and a re-arm can go live once the bus has been quiet for 10 s.
 *
 * Note 477171a reports 5 while implementing the burst test without the
 * recency test, so it is neither a rev 5 device nor a rev 6 one. It was never
 * flashed or released; the schema doc's rev table records it.
 *
 * The accompanying diag_schema_ver 5 -> 6 guards decoders; this guards the
 * human.
 *
 * A REV 7 DEVICE (the 2026-10-10 go-live batch, user) differs in three ways a
 * log reader has to know about, and none of them moves a field.
 *
 * It will not go live while its receive queue still holds a frame, and says
 * so as diag_arm_block 15 (spec 7 condition 9). A rev 7 log can therefore
 * show a gap between the gate's conditions being satisfied and the inhibit
 * going live where a rev 6 log shows none.
 *
 * It evaluates trip 6 at the moment the gate passes rather than after the
 * first answer (spec 7, "Checked before going live"). A rev 6 log of a re-arm
 * into a recent burst carries exactly one inhibit frame before the latch; a
 * rev 7 log of the same bus carries none. Reason 6 still means what it meant.
 *
 * A mode change wakes its worker (spec 3), so the up-to-50 ms window on an
 * arm from OFF and the up-to-100 ms window on a re-arm from OBSERVE, in which
 * earlier revs went live behind a queue that was already filling, are gone.
 * This is the change that makes the two above rarely visible in practice,
 * which is the reason all three are in one rev rather than three.
 *
 * diag_schema_ver goes 6 -> 7 with this rev, for arm_block 15. */
#ifndef DIAG_FW_VERSION
#define DIAG_FW_VERSION              7
#endif
/*
 * THE COMMIT ENTERS THE IMAGE ONCE, in esp_app_desc.version (spec 12.3 item 3,
 * shared/tools R4): ESP-IDF's default, the `git describe` it runs at configure.
 * It is a label (shared/tools R6); 0x7F3 names the binary by its image hash.
 * These two settings would put something else in the version field.
 */
#if defined(CONFIG_APP_EXCLUDE_PROJECT_VER_VAR) && CONFIG_APP_EXCLUDE_PROJECT_VER_VAR
#error "CONFIG_APP_EXCLUDE_PROJECT_VER_VAR empties esp_app_desc.version, where the commit enters the image (shared/tools R4)"
#endif
#if defined(CONFIG_APP_PROJECT_VER_FROM_CONFIG) && CONFIG_APP_PROJECT_VER_FROM_CONFIG
#error "CONFIG_APP_PROJECT_VER_FROM_CONFIG replaces ESP-IDF's git describe in esp_app_desc.version (shared/tools R4)"
#endif

/* Receive timeout: 200 ms rather than a full second only so a disarm is
 * acted on promptly; a timeout costs nothing but a loop iteration. */
#define GEN_INHIBIT_RX_TIMEOUT_MS   200

/*
 * SPEC 5.1 ITEM 1: the software RX queue holds at least 64 frames whenever the
 * driver is installed (amended 2026-09-26 from 32, user). Port 0's setting
 * since port step 1b (gen_inhibit_port_register()); can.c's CAN_RX_QUEUE_LEN
 * until then, whose comment carried the reasoning below.
 *
 * TWAI_GENERAL_CONFIG_DEFAULT gives 5, which is ~2.2 ms of traffic at the
 * truck's measured ~2250 frames/s -- against a worst measured WiFi preemption
 * of 2.39 ms. 64 is ~27 ms at 2364 frames/s. THIS IS THE REMEDY THAT MAKES THE
 * MARGIN POSITIVE rather than smaller: WiFi/lwIP preemption blocks the WORKER
 * TASK, not the TWAI interrupt, so the ISR keeps moving frames into this queue
 * while the task is off-CPU, and the queue depth is the buffer that has to
 * cover preemption x frame rate. A deeper queue turns loss into lateness, and
 * lateness past the VCM's next 0x051 is caught by section 7 trip 7.
 *
 * WHAT IT DOES NOT FIX, and the reason spec 5.1 item 3 exists: while the flash
 * cache is disabled for a write, the ISR cannot run at all (the ISR is not in
 * IRAM, section 5), so nothing reaches this queue and only the controller's
 * hardware FIFO buffers.
 */
#define GEN_INHIBIT_RX_QUEUE_LEN    64

/* ------------------------------------------------------------------------- */

/*
 * The decision state. WRITTEN BY THE WORKER ONLY (spec 3, "The worker makes
 * every mode change", 2026-10-16): a mode change is posted to the worker and
 * applied at the top of its loop, see the mailbox below. Other tasks read it
 * for reporting, through the snapshot the status page takes. There is no
 * exception (spec 3 and 8.1, user 2026-10-16): a teardown by another caller and
 * an OTA upload are requests the worker takes like a mode change.
 */
static gi_state_t s_core;

static bool     s_we_enabled_bus;   /* we called can_enable(), so we may disable */
static volatile bool s_release_bus; /* disarm asked us to hand the bus back */

static TaskHandle_t  s_worker;      /* the worker task: self-call detection, and the wake */

/*
 * SPEC 8.1, THE WORKER STATE MACHINE (user, 2026-10-16). Written by the worker
 * only, at its update point (update_point(), the top of each pass); read by
 * other tasks for gen_inhibit_owns_bus() and the status page.
 *   IDLE      mode OFF; the driver is not ours; the worker is outside it.
 *   ARMED     a non-OFF mode; the worker installed the driver and receives.
 *   UPDATING  an OTA upload holds the bus (rows 4, 10-12).
 * The core's mode says what an ARMED worker does with a frame; this says who
 * holds the bus. They differ for one pass after the core switches itself off
 * (row 7), which is the event that brings them back together.
 */
typedef enum
{
    GI_WORKER_IDLE = 0,
    GI_WORKER_ARMED,
    GI_WORKER_UPDATING,
} gi_worker_state_t;

static volatile gi_worker_state_t s_wstate;
static uint32_t     s_violations;   /* rows 9 and 13: teardowns by another caller */
static TaskHandle_t s_ota_task;     /* the task row 4 handed the bus to */

static const char *worker_state_name(gi_worker_state_t s)
{
    switch (s)
    {
    case GI_WORKER_IDLE:     return "idle";
    case GI_WORKER_ARMED:    return "armed";
    case GI_WORKER_UPDATING: return "updating";
    }
    return "?";
}

/*
 * SPEC 3, "A mode change takes effect at once" (user, 2026-10-10).
 *
 * The worker used to poll for a mode change every 50 ms in its OFF branch and
 * every 100 ms on its receive-error path. Both sleeps happen with the driver
 * already installed and already receiving into the queue, so the queue filled
 * for the rest of the sleep and the device went live answering commands the
 * VCM had already passed: about 120 frames on every re-arm from OBSERVE, and
 * 1 boot in 3 on the auto-arm path (review tracker, re-arm stale frames).
 *
 * A task notification rather than a flag or a semaphore, for one property: it
 * LATCHES. gen_inhibit_set_mode() can run before the worker ever reaches a
 * sleep -- it does on an auto-arm boot -- and the notification then sits in
 * the TCB and makes the first ulTaskNotifyTake() return at once. A condition
 * variable or a flag checked at the top of the sleep has a window where the
 * wake is lost; this has none, which is the half of the spec paragraph that
 * says the wake must survive arriving early.
 *
 * Index 0 is shared with anything else that notifies this task. Nothing does:
 * the TWAI driver hands frames over a queue, which does not touch the
 * notification value, and nothing else holds the handle. A spurious
 * notification from a future caller would shorten one sleep, which is why
 * every sleep below is still bounded and the loop still re-reads the mode.
 *
 * NOT a cure for twai_receive() blocking up to GEN_INHIBIT_RX_TIMEOUT_MS in
 * the old mode: a notification cannot interrupt that call. It does not have
 * to. The driver is up and this worker is draining it in that case, so the
 * queue is not growing, and spec 7 condition 9 holds back the go-live if it
 * is.
 */
static void worker_sleep(uint32_t ms)
{
    (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(ms));
}

static void worker_wake(void)
{
    /*
     * NULL before gen_inhibit_init() runs, and that window is real:
     * config_server_start() is six lines ahead of gen_inhibit_init() in
     * app_main(), so a POST can reach gen_inhibit_set_mode() before the
     * worker exists. There is nothing to wake then -- the worker reads the
     * mode in its first iteration anyway.
     */
    if (s_worker != NULL)
    {
        xTaskNotifyGive(s_worker);
    }
}

/*
 * SPEC 3, "THE WORKER MAKES EVERY MODE CHANGE" (user, 2026-10-16).
 *
 * A mode change is a REQUEST in this one-slot mailbox. The HTTP setter and the
 * boot auto-arm post it and wake the worker; the worker takes it at the top of
 * its loop -- outside twai_receive(), between frames -- applies it
 * (apply_mode(): driver teardown and install, gs_rearm(), gi_set_mode()) and
 * leaves the result here for the poster. No other task writes the worker's
 * state.
 *
 * Why: on c6fdab5 gen_inhibit_set_mode() ran all of that in the httpd task. The
 * worker, at priority 18, wakes on the next frame, so it could run part-way
 * through gs_rearm()'s memset-and-restore or gi_set_mode()'s resets.
 *
 * THE 1 s BOUND RUNS FROM THE SETTER'S CALL. Spec 3: "The setter waits at most
 * 1 s". gen_inhibit_post_mode() fixes the deadline, call + 1 s, before it waits
 * for the slot, and the request carries it, so the wait for a free slot and the
 * wait for the worker's take share the one second (until 2026-10-16 each had
 * its own, about 2 s in all). A request posted late in the second is withdrawn
 * at the same deadline.
 *
 * THE RACE. A request still POSTED at its deadline is WITHDRAWN by its poster
 * and refused (ESP_ERR_TIMEOUT). Withdrawing and taking are each one
 * test-and-set of `state` inside s_req_lock, so exactly one of them happens to
 * any request:
 *   - the worker takes it first (POSTED -> TAKEN): the poster may no longer
 *     withdraw it and waits for DONE, however long past the second that is.
 *     apply_mode() does not block beyond can_block()'s 1 ms delay, and the
 *     worker is the highest-priority task that touches this;
 *   - the poster withdraws it first (POSTED -> IDLE): the worker finds nothing
 *     to take, now or later, so a withdrawn request is never applied.
 * And the worker never takes a request whose deadline has passed: its poster,
 * at a lower priority, may not yet have run to withdraw it, and a change not
 * taken within the second is refused, not applied late.
 *
 * ONE SLOT, AND A POSTER WAITS FOR IT TO BE FREE within its second. Two posters
 * can meet: config_server starts before gen_inhibit_init(), so a POST can
 * coincide with the boot auto-arm. A slot is freed only by its own poster
 * (reading the result, or withdrawing), so one poster's result is never
 * overwritten by the next. A poster that finds the slot busy for its whole
 * second posts nothing and is refused with ESP_ERR_NOT_FINISHED: another change
 * had not finished.
 */
typedef enum
{
    REQ_IDLE = 0,   /* free */
    REQ_POSTED,     /* waiting for the worker; its poster may still withdraw it */
    REQ_TAKEN,      /* the worker is applying it; it will finish */
    REQ_DONE,       /* applied; `result` waits for its poster */
} req_state_t;

/*
 * WHAT THE SLOT HOLDS: a mode change, or the OTA handler asking for the bus
 * (spec 8.1 rows 4 and 8). Both want a reply and both obey the same 1 s, so
 * they share the slot; the web server runs one request at a time, so the OTA
 * handler and the HTTP setter never compete for it.
 */
typedef enum
{
    REQ_KIND_MODE = 0,
    REQ_KIND_OTA_BEGIN,
} req_kind_t;

static portMUX_TYPE s_req_lock = portMUX_INITIALIZER_UNLOCKED;
static struct
{
    req_state_t        state;
    req_kind_t         kind;
    TaskHandle_t       poster;      /* who posted it: row 4 hands the bus to it */
    uint32_t           ticket;
    gen_inhibit_mode_t mode;
    uint32_t           offset_us;
    int64_t            deadline_us; /* the poster's call + 1 s */
    esp_err_t          result;
} s_req;
static uint32_t s_req_tickets;

/*
 * THE TWO REQUESTS THAT ARE FLAGS, NOT SLOTS (spec 8.1), under s_req_lock.
 *
 * A TEARDOWN BY ANOTHER CALLER (rows 9 and 13, the backstop). Its caller tears
 * the driver down whatever it is told, so it may neither wait behind the slot
 * nor be refused for it: gen_inhibit_quiesce() bumps s_td_posted and waits for
 * the worker to serve it. `candidates` counts those posted while the bus was
 * up, which is what row 13 counts in IDLE or UPDATING; in ARMED every one
 * counts. Never withdrawn: the caller goes on regardless, so the worker must go
 * OFF whenever it gets there.
 *
 * AN OTA THAT FAILED (row 11). Every error exit of the upload handler bumps
 * s_ota_end_posted; the worker returns to IDLE. Nothing waits for it.
 *
 * The *_served counts are the worker's; posters read them under the lock.
 */
static uint32_t s_td_posted, s_td_candidates;
static uint32_t s_td_served, s_td_cand_served;
static uint32_t s_ota_end_posted, s_ota_end_served;

#define GEN_INHIBIT_MODE_WAIT_US    1000000

/* One tick between polls; never 0, which would spin a lower-priority poster. */
static void req_poll_delay(void)
{
    const TickType_t t = pdMS_TO_TICKS(1);
    vTaskDelay(t > 0 ? t : 1);
}

static esp_err_t post_request(req_kind_t kind, gen_inhibit_mode_t mode,
                              uint32_t offset_us, uint32_t *ticket);

esp_err_t gen_inhibit_post_mode(gen_inhibit_mode_t mode, uint32_t offset_us,
                                uint32_t *ticket)
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
    /* The worker would wait for itself. gen_inhibit_set_mode() handles it. */
    if (s_worker != NULL && xTaskGetCurrentTaskHandle() == s_worker)
    {
        return ESP_ERR_INVALID_STATE;
    }
    return post_request(REQ_KIND_MODE, mode, offset_us, ticket);
}

static esp_err_t post_request(req_kind_t kind, gen_inhibit_mode_t mode,
                              uint32_t offset_us, uint32_t *ticket)
{
    const int64_t deadline_us = esp_timer_get_time() + GEN_INHIBIT_MODE_WAIT_US;
    for (;;)
    {
        bool posted = false;
        bool expired = false;
        taskENTER_CRITICAL(&s_req_lock);
        if (esp_timer_get_time() >= deadline_us)
        {
            expired = true;
        }
        else if (s_req.state == REQ_IDLE)
        {
            s_req.ticket      = ++s_req_tickets;
            s_req.kind        = kind;
            s_req.poster      = xTaskGetCurrentTaskHandle();
            s_req.mode        = mode;
            s_req.offset_us   = offset_us;
            s_req.deadline_us = deadline_us;
            s_req.result      = ESP_FAIL;
            s_req.state     = REQ_POSTED;
            *ticket = s_req.ticket;
            posted = true;
        }
        taskEXIT_CRITICAL(&s_req_lock);
        if (posted)
        {
            /* SPEC 3, "A MODE CHANGE TAKES EFFECT AT ONCE": the wake. */
            worker_wake();
            return ESP_OK;
        }
        if (expired)
        {
            return ESP_ERR_NOT_FINISHED;    /* another change held the slot */
        }
        req_poll_delay();
    }
}

esp_err_t gen_inhibit_await_mode(uint32_t ticket)
{
    for (;;)
    {
        esp_err_t r = ESP_OK;
        bool finished = false;
        taskENTER_CRITICAL(&s_req_lock);
        if (s_req.ticket != ticket || s_req.state == REQ_IDLE)
        {
            r = ESP_ERR_INVALID_ARG;    /* not a request this caller holds */
            finished = true;
        }
        else if (s_req.state == REQ_DONE)
        {
            r = s_req.result;
            s_req.state = REQ_IDLE;
            finished = true;
        }
        else if (s_req.state == REQ_POSTED
                 && esp_timer_get_time() >= s_req.deadline_us)
        {
            /* WITHDRAWN: never taken, so never applied. See above. */
            s_req.state = REQ_IDLE;
            r = ESP_ERR_TIMEOUT;
            finished = true;
        }
        /* REQ_TAKEN: the worker is applying it, and it will finish. */
        taskEXIT_CRITICAL(&s_req_lock);
        if (finished)
        {
            return r;
        }
        req_poll_delay();
    }
}

/*
 * Spec 7 trip 7's "controller reported failed" form: the transmit-failed event
 * is HELD BY THE PORT until taken (twai_port T5), which is what this file's own
 * latch did until port step 1b. Carried between polls because the alert can
 * arrive before the frame leaves the controller; taken by the next inhibit
 * completion, and cleared when the next inhibit is queued.
 */

/*
 * THE COUNTERS AT THIS PROJECT'S OWN INSTALL (twai_port T7). The port's counts
 * are monotonic across reinstalls; spec 11's `drv` figures and the bus-error
 * count spec 7 trip 6 reads are per install, so they are the port's counts
 * minus these. Taken in the install hook, which runs before every install of
 * port 0 whichever path makes it (port_install_hook()).
 */
static can_port_status_t s_at_install;

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
/*
 * The controller's state as a word, not the raw enum. A log read months later
 * should not need the IDF header to say whether 2 meant bus-off.
 */
/* Non-static: declared in gen_inhibit.h so shim case 26 can ask it for its
 * longest value rather than keeping a second copy of this table. */
const char *drv_state_name(can_port_state_t s)
{
    switch (s)
    {
        case CAN_PORT_STATE_STOPPED:    return "stopped";
        case CAN_PORT_STATE_RUNNING:    return "running";
        case CAN_PORT_STATE_BUS_OFF:    return "bus_off";
        case CAN_PORT_STATE_RECOVERING: return "recovering";
        default:                        return "unknown";
    }
}

/*
 * `at` is s_at_install as the page copied it: the cumulative fields are this
 * install's, the port's counts minus their values at it (twai_port T7), as the
 * driver reported them before port step 1b.
 */
static int drv_json(const can_port_status_t *info, const can_port_status_t *at,
                    bool ok, char *buf, int buflen)
{
    if (!ok)
    {
        /* Not installed is a normal state when disarmed, and is not an error. */
        return snprintf(buf, buflen, ",\"drv\":null");
    }
    /*
     * `state`, `tec` and `rec` are INSTANTANEOUS, unlike every other field in
     * this object, which is cumulative since the driver was installed. Do not
     * difference them across a run (spec 11, 2026-10-06).
     *
     * They are here because nothing else on the page can stand in for them.
     * `bus_on` now reports the controller's state as well, but only as one
     * bool, so it cannot separate bus-off from stopped or recovering -- and
     * `bus_error` counts error EVENTS without saying whether any of them moved
     * the controller towards bus-off. Bus-off keys on TEC, which rises only on
     * transmit errors, so a device merely receiving a corrupted bus and a
     * device being driven to bus-off look identical in `bus_error`.
     *
     * The conclusive run is the 2026-10-06 TEC sweep
     * (artifacts/gen-inhibit/runs/tec_busoff_sweep.json), which reached bus-off
     * and read the state directly. The 2026-10-05 tx_failed probe before it
     * read 1,012,036 bus errors in 40 s and could not say which of the two it
     * had produced.
     */
    return snprintf(buf, buflen,
                    ",\"drv\":{\"state\":\"%s\",\"tec\":%lu,\"rec\":%lu,"
                    "\"rx_missed\":%lu,\"rx_overrun\":%lu,"
                    "\"arb_lost\":%lu,\"bus_error\":%lu,\"tx_failed\":%lu,"
                    "\"msgs_to_rx\":%lu,\"msgs_to_tx\":%lu}",
                    drv_state_name(info->state),
                    (unsigned long)info->tec,
                    (unsigned long)info->rec,
                    (unsigned long)(info->rx_lost_queue_full - at->rx_lost_queue_full),
                    (unsigned long)(info->rx_lost_overrun - at->rx_lost_overrun),
                    (unsigned long)(info->arb_lost - at->arb_lost),
                    (unsigned long)(info->bus_errors - at->bus_errors),
                    (unsigned long)(info->tx_failed - at->tx_failed),
                    (unsigned long)info->rx_waiting,
                    (unsigned long)info->tx_pending);
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

/*
 * Takes the backlog rather than reading it: spec 7 condition 9 needs the same
 * figure unconditionally, so the status call moved to rx_backlog_sample()
 * below and this is now only the histogram. `backlog` counts the frame just
 * dequeued, as it always did.
 */
static void rxq_note(uint32_t backlog)
{
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

/*
 * Section 11's one-snapshot rule reaches here too: these come from the copy taken
 * in the critical section, not from the live instrument counters, or a histogram
 * could show more samples than its own maximum was drawn from.
 */
static int rxq_json(uint32_t samples, uint32_t maxv, const uint32_t *ge,
                    char *buf, int buflen)
{
    int n = snprintf(buf, buflen,
                     ",\"rxq\":{\"depth\":%d,\"samples\":%lu,\"max\":%lu,\"ge\":[",
                     (int)can_rx_queue_len(),
                     (unsigned long)samples, (unsigned long)maxv);
    for (int i = 0; i < RXQ_NEDGES && n < buflen; i++)
    {
        n += snprintf(buf + n, buflen - n, "%s%lu", (i ? "," : ""),
                      (unsigned long)ge[i]);
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
#define rxq_note(backlog)       do { (void)(backlog); } while (0)
#define rxq_reset()             do { } while (0)
#define RXQ_NEDGES              1
#define rxq_json(samples, maxv, ge, buf, buflen)    (0)
#endif

/*
 * SPEC 7 CONDITION 9 (user, 2026-10-10): how many frames were STILL WAITING
 * when this worker last took one. Zero means the frame in hand was the last
 * one waiting, which is the condition for going live.
 *
 * PRODUCT STATE, NOT INSTRUMENTATION. It lives outside GI_INSTRUMENT_RXQ
 * because a build without the instrument must gate identically -- those are
 * the builds that go to the truck.
 *
 * STARTS AT 1, AND THAT IS THE WHOLE ARM-ENTRY QUESTION. Until this worker
 * has actually taken a frame in this arm there is no "frame just taken", so
 * the condition is not satisfied and the honest reading is not-drained.
 * Reporting drained instead would leave the window the condition exists to
 * close wide open: gi_set_mode() deliberately does NOT clear signal
 * freshness, so a 0x051 seen 100 ms before a re-arm is still fresh and the
 * gate could otherwise pass on the first tick after arming -- before the
 * worker had looked at the queue even once. On a live bus the cost is the one
 * frame it takes to find out, a couple of milliseconds; on a bus that has
 * gone silent the device does not go live, which is correct for other reasons
 * as well.
 */
static uint32_t s_rx_waiting = 1;

/*
 * FIRST THING AFTER THE DEQUEUE, and that is the point of it. Anything between
 * the receive and this read lets the queue drain further and understates the
 * backlog -- and reading it at the top of the loop instead, where the gate
 * runs, asks a different question entirely ("is anything waiting now"), which
 * on a 400 frame/s bus is routinely yes and stops the device ever going live.
 * Measured: 31 checks across 18 shim cases, every one of them because nothing
 * went live.
 */
static void rx_backlog_sample(void)
{
    /*
     * ONLY WHILE ARMED AND NOT YET LIVE, because that is the only state in
     * which anything reads the answer: condition 9 is checked for going live
     * and the gate is not re-run afterwards.
     *
     * THE READ IS NOT FREE, and this is measured rather than supposed. It is
     * the same twai_get_status_info() the RXQ instrument made per dequeue,
     * and that instrument is recorded as perturbing the device: tx_queued_behind
     * read 1 in both instrumented runs and 0 in every shipping run (evidence
     * doc, "The instrument perturbs the device"). On the truck the filter is
     * accept-all, so a per-dequeue read is ~2,400 of them a second on top of
     * the per-command can_msgs_to_rx() and the per-tick snapshot. Going live
     * costs a few frames' worth; staying live costs nothing.
     */
    const bool needed = gi_mode_decides(s_core.mode) && !s_core.inhibit_live;

#if GI_INSTRUMENT_RXQ
    /*
     * The instrumented build still reads on every dequeue, so its histogram
     * keeps covering the whole arm rather than only the frames before going
     * live. That build is not a shipping build and is already known to
     * perturb; what matters is that it GATES identically, which is why the
     * figure below is still forced to not-drained when the shipping build
     * would not have looked.
     */
    const bool read_it = true;
#else
    const bool read_it = needed;
#endif

    can_port_status_t info;
    if (!read_it || twai_port_status(&info) != ESP_OK
        || info.state == CAN_PORT_STATE_DOWN)
    {
        /*
         * FAILS CLOSED, in both cases. An arm that has not measured has not
         * satisfied condition 9 -- the spec's words are "the frame the worker
         * has just taken was the last one waiting" -- so not-drained is the
         * honest reading, and reporting drained here would be the permissive
         * direction in the one place the device is deciding to go live.
         *
         * It also matters on the way OUT of live: this leaves 1 behind while
         * live, so if a trip or a key-on puts the device back in front of the
         * gate, it blocks until a dequeue has actually measured rather than
         * passing on a reading taken before it went live.
         */
        s_rx_waiting = 1;
        return;
    }
    rxq_note(info.rx_waiting + 1);
    s_rx_waiting = needed ? info.rx_waiting : 1u;
}

static void bus_snapshot(gi_bus_t *b)
{
    can_port_status_t st;
    memset(b, 0, sizeof(*b));
    b->bus_ours = s_we_enabled_bus;
    /*
     * can_enabled carries the CONTROLLER's state, the same meaning spec 11
     * gives `bus_on` -- this field is the 0x02 bit on 0x7F1, and the spec says
     * the bit and the status-page field mean the same thing. It is taken from
     * the status read this function already makes for the section 7 error-rate
     * trip, so the new meaning costs no additional driver call. The memset
     * above leaves it false when that read fails, which is what the driver not
     * being installed looks like from here.
     *
     * The field keeps its old name: renaming it reaches into the host runner,
     * whose owner is not settled. The name is now a misnomer -- asked.
     */
    if (twai_port_status(&st) == ESP_OK && st.state != CAN_PORT_STATE_DOWN)
    {
        b->can_enabled     = (st.state == CAN_PORT_STATE_RUNNING);
        b->err_valid       = true;
        /* Per install, as the driver's own count was (s_at_install). */
        b->bus_error_count = st.bus_errors - s_at_install.bus_errors;
    }
    /*
     * Spec 7 condition 9 (user, 2026-10-10). NOT st.msgs_to_rx from the read
     * above: that is the queue as it stands at the top of the worker loop,
     * and the condition is about the moment the last frame was taken. See
     * rx_backlog_sample(). Set outside the ESP_OK branch because it is our
     * own record either way.
     */
    b->rx_waiting = s_rx_waiting;
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
        /*
         * D9. THIS CASE WAS MISSING, and the `default` below swallowed it, so
         * -Wswitch never said a word. The kind is the whole point of
         * gi_skip_kind_t: "withdrawn" means the frame never reached the wire,
         * "may have gone out late" means it may have reached it after the VCM's
         * next command. Without a line here the device cannot tell those apart,
         * or tell either from nothing having happened -- and the reviewer's
         * mutation I9, which corrupts exactly this kind, survived because the
         * output it corrupts did not exist.
         *
         * A skip is tolerated (three within 1 s is what trips, spec 7 trip 7 as
         * amended), so it is a warning and not an error. The trip itself arrives
         * separately as GI_EV_ABORT with GI_ABORT_SKIPS.
         */
        case GI_EV_SKIP:
            ESP_LOGW(TAG, "inhibit frame skipped: %s",
                     gi_skip_name((gi_skip_kind_t)e->a));
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
static uint32_t s_seen_standdown;
static bool     s_was_live;
static gi_frame_t s_last_inhibit;
static bool     s_have_last_inhibit;
static gi_frame_t s_last_probe;
static bool     s_have_last_probe;
/*
 * The instant the probe actually reached the driver, set in dev_submit().
 * Spec 12.1 defines RESPOND's figure as RX to handover, and this is the only
 * site that knows when handover happened -- see the note in sched_advance().
 */
static int64_t  s_probe_submit_us;

static bool dev_submit(void *ctx, const gi_frame_t *f)
{
    twai_message_t tx = { 0 };
    (void)ctx;
    tx.identifier = f->id;
    tx.data_length_code = f->dlc;
    memcpy(tx.data, f->data, 8);
    /*
     * THE SCHEDULER'S ONLY SEND (spec 5.2 item 1): through the port's send, the
     * one twai_transmit in the firmware (twai_port T6, 12.4's confinement row).
     * Zero timeout: the scheduler hands over only when the controller is free,
     * so a wait here would mean its own bookkeeping was wrong and blocking would
     * hide that.
     */
    const bool ok = (twai_port_send(&tx, 0) == ESP_OK);

    /*
     * THE HANDOVER INSTANT, for the probe only (spec 12.1). Read immediately
     * after the call that performs the handover, because everything between
     * sched_advance()'s single clock reading and this line -- the alert read,
     * can_msgs_to_rx(), gs_tick's buf_state round trips -- used to be omitted
     * from the reported figure. Filtered on the kind because dev_submit runs
     * for every class and an unfiltered stamp would sometimes describe an
     * inhibit or a diag page instead.
     */
    if (ok && f->kind == GI_TX_PROBE)
    {
        s_probe_submit_us = esp_timer_get_time();
    }
    return ok;
}

/*
 * The port's buffer state as the scheduler's (spec 5.2 item 8). The register
 * read, the TBS/TS decoding and the abort's critical section are twai_port's
 * T8, in its C3 file, moved there from here at port step 1b.
 *
 * UNSUPPORTED reads as TRANSMITTING, the direction in which the scheduler
 * waits and issues no abort. It is unreachable on the C3, the only chip this
 * project builds for, which has a measured register file.
 *
 * Ifs, not a switch: a switch can compile to a jump table in flash, and this is
 * on the restart step's path, which may run with the cache off.
 */
static IRAM_ATTR gs_buf_t buf_from_port(twai_port_buf_t b)
{
    if (b == TWAI_PORT_BUF_EMPTY)    { return GS_BUF_EMPTY; }
    if (b == TWAI_PORT_BUF_AWAITING) { return GS_BUF_AWAITING; }
    return GS_BUF_TRANSMITTING;
}

static gs_buf_t dev_buf_state(void *ctx)
{
    (void)ctx;
    return buf_from_port(twai_port_buf_state());
}

/*
 * Read the state and, if AWAITING, issue the abort -- indivisibly, in one
 * critical section, in twai_port's abort-if-awaiting (T8). Why one section, and
 * why the interrupt register is never touched, is written there.
 *
 * IN IRAM, because the restart step calls it too (gen_inhibit_restart_abort_tx(),
 * below; spec 3 and spec 12.4's confinement row, 2026-10-13). That caller may run
 * from a panic, so this must not depend on the flash cache: buf_from_port() above
 * and the port's abort-if-awaiting are in IRAM too.
 */
static IRAM_ATTR gs_buf_t dev_abort_if_awaiting(void *ctx)
{
    (void)ctx;
    return buf_from_port(twai_port_abort_if_awaiting());
}

static bool dev_outstanding(void *ctx)
{
    can_port_status_t info;
    (void)ctx;
    if (twai_port_status(&info) != ESP_OK || info.state == CAN_PORT_STATE_DOWN)
    {
        /*
         * UNKNOWN MEANS YES, and so does a port that is not installed, as it did
         * when this read the driver directly and the read failed. If we cannot
         * tell, saying "outstanding" stops the scheduler handing over another
         * frame, which is the safe direction: two frames of ours in the
         * controller is the one state under which trip 7's completion test
         * means nothing.
         */
        return true;
    }
    return info.tx_pending != 0;
}

static const gs_hal_t DEV_HAL = {
    .submit = dev_submit,
    .abort_if_awaiting = dev_abort_if_awaiting,
    .buf_state = dev_buf_state,
    .outstanding = dev_outstanding,
    .ctx = NULL,
};

/*
 * THE RESTART STEP'S ABORT GOES THROUGH THE SCHEDULER'S, so the private abort
 * command stays at one site (spec 12.4, "the private TWAI abort is confined";
 * spec 3, "No restart leaves the controller on the bus"). See
 * gi_restart_quiesce.c, which calls this on every poll of its wait.
 */
IRAM_ATTR bool gen_inhibit_restart_abort_tx(void)
{
    return dev_abort_if_awaiting(NULL) == GS_BUF_AWAITING;
}

/*
 * Turn the scheduler's monotonic counters into the core's events.
 *
 * WHY COUNTERS AND NOT A CALLBACK: gi_sched is platform-free and hands out no
 * function pointers, and deltas cannot lose an event to a callback nobody
 * registered. They can lag by one tick, and a tick is every worker iteration.
 */
static void sched_advance(int64_t now, gi_events_t *ev)
{
    /*
     * Called sched_pump() until 2026-09-27 (user: the name "reads like we are
     * working with the pumps on the truck"). It is not a pump of anything -- it
     * advances the transmit scheduler one iteration and turns the counter deltas
     * that iteration produced into core events.
     *
     * `now` IS READ BY THE CALLER, ONCE, and it is the time base for gs_tick()
     * and for every event reported below EXCEPT the probe's own figure.
     *
     * THE PROBE IS THE EXCEPTION, because spec 12.1 defines its figure as RX to
     * handing the probe to the driver, and this function does real work between
     * that single clock reading and the handover: the alert read,
     * can_msgs_to_rx(), and gs_tick's buf_state round trips. Using `now` therefore
     * UNDER-reported the figure -- measured at ~9 us on the 2026-09-27 bench,
     * where the Kvaser's wire idle mean sat +9 us above three pre-scheduler runs
     * whose own idle means span 2.6 us, while the device's figure moved -1 us. The
     * device could not see its own pre-submit cost, which is exactly the interval
     * `now` omitted. dev_submit() now records the handover instant and the probe
     * is reported against that (user: "just do it", 2026-09-28).
     *
     * A QUEUE WAIT WAS NEVER LOST THIS WAY, before or after: a probe deferred to a
     * later iteration is reported against that later iteration, so waiting behind
     * a telemetry frame has always shown in the figure. Only the within-iteration
     * cost was missing.
     */
    /*
     * POLL THE PORT'S ALERTS FIRST, which holds a transmit failure until it is
     * taken (twai_port T5; port step 1b). Before 1b this read the driver's alerts
     * itself and kept its own latch.
     *
     * This is spec 7 trip 7's second form -- "a frame the controller reports as
     * failed still trips at once" -- and this file's ONLY route to it.
     * poll_tx_completion() used to do it; deleting that function without moving
     * this would have made the form unreachable, and nothing would have
     * complained for a long time, because IDF documents TX_FAILED as raised for
     * single-shot transmission and these frames go out with ss = 0.
     *
     * The event is taken HERE and not in the scheduler's HAL on purpose: alerts are
     * latched bits shared by every frame, which is exactly what gi_sched must not
     * have to reason about, and the shim already knows which frame it handed over.
     */
    (void)twai_port_poll_alerts();

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

    /*
     * STAND-DOWNS, ONE CORE CALL EACH (spec 5.2 items 5 and 6, user 2026-10-16).
     * Each ends the core's wait for a frame whose completion will never be
     * reported, so diag pages keep their cadence after a release or a trip.
     *
     * Read HERE, on the next pass, and not straight after gs_withdraw_inhibits()
     * at the bottom of this function: when a refusal trips, dispatch_emits()
     * reports the frame as queued AFTER this function returns, and that sets the
     * wait again. Reporting on the next pass ends it after that, and it still
     * comes before any new inhibit, because the core queues one only through
     * dispatch_emits(), which runs this first.
     */
    while (s_seen_standdown < st->standdown)
    {
        s_seen_standdown++;
        gi_on_inhibit_standdown(&s_core);
    }

    while (s_seen_inh_sent < st->cls[GS_CLASS_INHIBIT].sent)
    {
        s_seen_inh_sent++;
        /*
         * ok = false makes this trip 7's TX_FAILED form. The held event is taken
         * here so one failure reports once rather than condemning every later
         * frame -- the defect the shared-alert-bit comment in the old
         * poll_tx_completion() recorded.
         */
        gi_on_tx_done(&s_core, !twai_port_take_tx_failed(), now, ev);
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
            /*
             * THE HANDOVER INSTANT, NOT THIS ITERATION'S ENTRY. dev_submit()
             * set it during the gs_tick() above, so it is fresh: `handed`
             * advancing is precisely the evidence that a submit happened.
             * Falls back to `now` only if no probe has ever been submitted,
             * which cannot coexist with `handed` advancing and is here so the
             * figure can never be computed against a zero clock.
             */
            const int64_t t_handover =
                (s_probe_submit_us != 0) ? s_probe_submit_us : now;
            gi_on_tx_result(&s_core, &s_last_probe, true, false,
                            t_handover, ev);
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
             * Clear a stale TX_FAILED, as the old path did: poll, then drop the
             * held event. Completion is decided by msgs_to_tx and no leftover
             * alert can affect it, but the event is read for trip 7's
             * "controller reported failed" form.
             */
            (void)twai_port_poll_alerts();
            (void)twai_port_take_tx_failed();
        }

        if (f->kind == GI_TX_PROBE)
        {
            /*
             * Remembered, not reported. sched_advance() reports it when it reaches the
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
        sched_advance(esp_timer_get_time(), ev);

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
 * Its TWAI_ALERT_TX_FAILED read moved into sched_advance(), which is now trip 7's
 * only route to the "controller reported failed" form.
 */

/* ------------------------------------------------------------ public API -- */

void gen_inhibit_reset_stats(void)
{
    gi_reset_stats(&s_core);
}

/*
 * SPEC 3.2: A REFUSED ATTEMPT IS COUNTED, NEVER SILENTLY DROPPED (user,
 * 2026-10-12, replacing "logged"). The counters live here rather than at the
 * refusal sites because this is the file that reports them, and because the
 * sites are in upstream code that should carry as little of ours as possible.
 *
 * INSIDE THE CRITICAL SECTION, which for a counter looks like overkill and is
 * not. `x++` is read-modify-write, the refusing paths are ordinary tasks that
 * preempt each other on a unicore part, and a lost increment is exactly the
 * silently-dropped refusal the spec change exists to stop. The cost is a few
 * microseconds on a path that is already refusing an attempt.
 */
static uint32_t s_refused_slcan;
static uint32_t s_refused_can_send;

void gen_inhibit_note_refused_slcan(void)
{
    taskENTER_CRITICAL(&s_abort_lock);
    s_refused_slcan++;
    taskEXIT_CRITICAL(&s_abort_lock);
}

void gen_inhibit_note_refused_can_send(void)
{
    taskENTER_CRITICAL(&s_abort_lock);
    s_refused_can_send++;
    taskEXIT_CRITICAL(&s_abort_lock);
}

bool gen_inhibit_owns_bus(void)
{
    /*
     * The core's mode, as before, OR the worker's state (spec 8.1): ARMED for
     * the one pass after the core switches itself off with the driver still
     * ours (row 7), and UPDATING while an OTA holds the bus.
     */
    return s_core.mode != GI_OFF || s_wstate != GI_WORKER_IDLE;
}

/*
 * Is the controller on the bus? The port's reading of the driver's state, not
 * the stock layer's bus_state: that records ON_BUS when it brings the port up
 * and nothing changes it at a bus-off, so can_is_enabled() stays true for a
 * controller that has left the bus. Unknown (the port will not say) counts as
 * not running, the direction that reinstalls rather than arms onto a dead
 * controller.
 *
 * RECOVERING is unreachable here: port 0 never recovers from bus-off (spec 7;
 * spec 3.3's settings). If it were reached, the arm's can_disable() would be
 * refused by the port (twai_port T3), which keeps the driver installed and
 * says so, and the stock layer then keeps the bus on (can_port C5); the
 * reinstall below would not run, and the arm would be refused by the
 * controller_running() check after it.
 */
static bool controller_running(void)
{
    can_port_status_t st;
    return twai_port_status(&st) == ESP_OK
           && st.state == CAN_PORT_STATE_RUNNING;
}

/*
 * WITHDRAW THE PENDING ANSWERS (spec 8.1 rows 5, 6, 7 and 9, the exit action of
 * ARMED; 5.2 item 5): every inhibit still queued or in the controller is given
 * up as a stand-down. The item 4 loop finishes removing one already in the
 * controller on later passes, or the next arm counts it (5.2 item 6).
 */
static void withdraw_pending(void)
{
    const int64_t now = esp_timer_get_time();
    gi_events_t ev = { 0 };
    gs_withdraw_inhibits(&s_sched, now);
    sched_advance(now, &ev);
    report_events(&ev);
}

/*
 * APPLY A MODE CHANGE. THE WORKER'S OWN CODE (spec 3, "The worker makes every
 * mode change"): reached only from take_request() at the update point (spec
 * 8.1), or from gen_inhibit_set_mode() when the worker itself calls it. The
 * arguments were validated when the request was posted. Until 2026-10-16 this
 * was the body of gen_inhibit_set_mode() and ran in the caller's task.
 *
 * In order: the driver teardown and install, the scheduler re-arm, the core's
 * mode. can_disable() from here quiesces nothing: gen_inhibit_quiesce() sees
 * the worker calling and returns at once, and the worker is outside the driver.
 */
static esp_err_t apply_mode(gen_inhibit_mode_t mode, uint32_t offset_us)
{
    /*
     * COLLECT WHAT THE OLD ARM HAS ALREADY FINISHED, before anything is torn
     * down or re-armed. This runs at the top of the worker loop, where the
     * scheduler has not yet looked at the controller since the last frame or
     * timeout, so a frame that completed in the meantime is still `held`.
     * gs_rearm() would carry it into the new arm as carried_in and time it there
     * as an on-time completion with no tx_ok behind it. E1 case 33 read
     * ontime 29978 against tx_ok 29977 that way, the extra one being the
     * previous case's last frame.
     */
    {
        gi_events_t ev = { 0 };
        sched_advance(esp_timer_get_time(), &ev);
        report_events(&ev);
    }
    /*
     * SPEC 8.1 ROWS 5 AND 6, THE EXIT ACTION: an armed worker leaving its mode
     * withdraws the answers it still has pending, as stand-downs (5.2 item 5).
     */
    if (s_wstate == GI_WORKER_ARMED)
    {
        withdraw_pending();
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
        /*
         * SPEC 7, "A BUS-OFF ENDS THE DEVICE'S PART UNTIL A RE-ARM OR A
         * REBOOT" (user, 2026-10-14): the re-arm half. A controller that is
         * installed but not running -- bus-off, recovering or stopped -- is
         * taken down here, and the block below installs it again, so every arm
         * puts the controller back on the bus.
         *
         * Before 2026-10-14 an arm into INHIBIT, RESPOND or PASSIVE skipped
         * the install whenever can_is_enabled() was true, which it stays
         * through a bus-off, so the arm replied 200 with the controller still
         * off the bus and the gate waiting for 0x051 indefinitely. Only a move
         * into or out of OBSERVE, or mode 0 first, reinstalled it.
         *
         * The check is HERE and not in can_is_enabled(), whose callers read it
         * as "the stock layer brought port 0 up"; and folding it in would not
         * reinstall anyway, because the enable returns at once while bus_state is
         * ON_BUS. The teardown is the ordinary can_disable(), which quiesces
         * the worker first. In BUS_OFF its twai_stop() is refused
         * (INVALID_STATE) and ignored, and twai_driver_uninstall() is allowed.
         *
         * Not recovery: nothing here calls twai_initiate_recovery(), and nothing
         * happens without an arm. A bus-off still ends the device's part until
         * someone arms it or it reboots.
         */
        if (can_is_enabled() && !controller_running())
        {
            ESP_LOGW(TAG, "arming mode %d: controller not running, "
                          "reinstalling the driver (spec 7)", (int)mode);
            can_disable();
        }

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
         * The install forces accept-all when gen_inhibit_owns_bus() (port 0's
         * install hook since 1b, can.c before), which READ CORRECTLY AND DID
         * NOTHING ON THIS PATH. The mode is not
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
            /*
             * SPEC 3.3, AN INSTALL FAILURE REFUSES (user, 2026-10-31): the
             * reporting form of the stock enable, not can_enable(), which
             * restarts on a failure (can_port C3). Any failed step -- the pins,
             * the install, the start or the alerts -- refuses the arm or the
             * mode change with the reason (8.1 rows 2 and 5) and leaves the bus
             * down until the next enable. Until 1b the install and the start
             * restarted the device through can.c's ESP_ERROR_CHECK, and only
             * the alerts refused.
             *
             * ESP_ERR_INVALID_STATE whatever the port returned, as the alerts
             * refusal was: the handler words every other code as the bus not
             * coming up, but reads INVALID_ARG, which the install can return,
             * as a bad offset.
             */
            const esp_err_t up = can_port_stock_enable();
            if (up != ESP_OK)
            {
                ESP_LOGE(TAG, "cannot arm: port 0 would not come up, step %d: "
                              "%s (spec 3.3)",
                         (int)twai_port_last_step(), esp_err_to_name(up));
                return ESP_ERR_INVALID_STATE;
            }
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
         * And the controller itself, which can_is_enabled() does not report:
         * an arm that could not bring it back onto the bus replies with an
         * error, never 200 (spec 7, user, 2026-10-14).
         */
        if (!controller_running())
        {
            ESP_LOGE(TAG, "cannot arm: controller not running after install");
            return ESP_ERR_INVALID_STATE;
        }
        /*
         * Spec 7 trip 7 (review C1) needs the TX alerts on. Since port step 1b
         * the port enables them at every install, as a step of the install
         * (twai_port T4, T5), so an install whose alerts would not enable is
         * an install failure (spec 3.3). Until 1b this file turned them on
         * here, at every arm.
         */
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
     * Back to not-drained for the new arm: this arm has taken no frames, and
     * last arm's reading says nothing about this one. See s_rx_waiting.
     */
    s_rx_waiting = 1;
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
    /*
     * RESPOND times its probe; every other mode times its inhibit frame (spec
     * 11, user 2026-10-12). Passed per arm because the mode is what decides
     * it -- see gs_t's timed_class for why this is not a setter.
     */
    gs_rearm(&s_sched, &DEV_HAL,
             (mode == GEN_INHIBIT_RESPOND) ? GS_CLASS_PROBE
                                           : GS_CLASS_INHIBIT);
    s_seen_inh_sent = 0;
    s_seen_inh_refused = 0;
    s_seen_probe_handed = 0;
    s_seen_probe_lost = 0;
    s_seen_skip_withdrawn = 0;
    s_seen_skip_late = 0;
    /*
     * PAST ANY STAND-DOWN THE RE-ARM ITSELF BOOKED (spec 5.2 item 6, "A re-arm
     * while a withdrawal is still running"). That answer belonged to the old
     * arm, and gi_set_mode() below ends the core's wait for it anyway, so
     * reporting it would only end a wait the new arm has not started.
     */
    s_seen_standdown = gs_stats(&s_sched)->standdown;
    s_was_live = false;
    s_have_last_inhibit = false;
    s_have_last_probe = false;
    gi_set_mode(&s_core, (gi_mode_t)mode, offset_us, esp_timer_get_time(), &ev);
    report_events(&ev);

    /*
     * Releasing the bus is done in the worker's OFF branch, from a point where
     * it is provably not inside the driver -- the next thing the worker does
     * after this returns.
     */
    if (mode == GEN_INHIBIT_OFF && s_we_enabled_bus)
    {
        s_release_bus = true;
    }
    /*
     * No wake here any more: this runs in the worker. The poster woke it
     * (gen_inhibit_post_mode()).
     */
    return ESP_OK;
}

/*
 * A mode request, by state (spec 8.1 rows 1-3, 5, 6 and 12).
 */
static esp_err_t apply_mode_request(gen_inhibit_mode_t mode, uint32_t offset_us)
{
    if (s_wstate == GI_WORKER_UPDATING)
    {
        return ESP_ERR_NOT_ALLOWED;                     /* row 12 */
    }
    if (s_wstate == GI_WORKER_IDLE && mode == GEN_INHIBIT_OFF)
    {
        return ESP_OK;                                  /* row 3 */
    }
    const esp_err_t r = apply_mode(mode, offset_us);   /* rows 1, 2, 5, 6 */
    /*
     * Where that left us. A refused arm from IDLE (row 2) leaves the core OFF;
     * a refused re-arm whose reinstall had already taken the old driver down
     * (row 5) left it OFF too, through gen_inhibit_quiesce()'s worker branch.
     * Either way IDLE, and IDLE means the driver is not ours: one the refused
     * arm brought up is given back by the OFF branch.
     */
    s_wstate = (s_core.mode != GI_OFF) ? GI_WORKER_ARMED : GI_WORKER_IDLE;
    if (s_wstate == GI_WORKER_IDLE && s_we_enabled_bus)
    {
        s_release_bus = true;
    }
    return r;
}

/*
 * The OTA handler asks for the bus (spec 8.1 rows 4 and 8, 9.3). Taken at the
 * update point, so in IDLE the worker is outside the driver by construction
 * and the handler may tear it down once this returns.
 */
static esp_err_t apply_ota_begin(TaskHandle_t poster)
{
    if (s_wstate != GI_WORKER_IDLE)
    {
        return ESP_ERR_INVALID_STATE;   /* row 8: armed, refused, nothing touched */
    }
    s_ota_task = poster;
    s_wstate = GI_WORKER_UPDATING;      /* row 4 */
    ESP_LOGW(TAG, "firmware update: the bus is the OTA handler's (spec 8.1 row 4)");
    return ESP_OK;
}

/*
 * The worker's half of the slot: take a posted request and apply it. Called
 * from the update point only, where the worker is outside the driver and
 * between frames (spec 3, 8.1).
 */
static void take_request(void)
{
    req_kind_t kind = REQ_KIND_MODE;
    TaskHandle_t poster = NULL;
    gen_inhibit_mode_t mode = GEN_INHIBIT_OFF;
    uint32_t offset_us = 0;
    bool took = false;

    taskENTER_CRITICAL(&s_req_lock);
    /* Past its deadline it is its poster's to withdraw, never ours to apply. */
    if (s_req.state == REQ_POSTED && esp_timer_get_time() < s_req.deadline_us)
    {
        s_req.state = REQ_TAKEN;
        kind = s_req.kind;
        poster = s_req.poster;
        mode = s_req.mode;
        offset_us = s_req.offset_us;
        took = true;
    }
    taskEXIT_CRITICAL(&s_req_lock);
    if (!took)
    {
        return;
    }

    const esp_err_t r = (kind == REQ_KIND_OTA_BEGIN)
                            ? apply_ota_begin(poster)
                            : apply_mode_request(mode, offset_us);

    taskENTER_CRITICAL(&s_req_lock);
    s_req.result = r;
    s_req.state = REQ_DONE;
    taskEXIT_CRITICAL(&s_req_lock);
}

/*
 * A TEARDOWN BY ANOTHER CALLER (spec 8.1 rows 9 and 13, the backstop). Every
 * one posted so far is served at once.
 */
static void serve_teardown(void)
{
    taskENTER_CRITICAL(&s_req_lock);
    const uint32_t posted = s_td_posted;
    const uint32_t cand = s_td_candidates;
    taskEXIT_CRITICAL(&s_req_lock);
    const uint32_t n = posted - s_td_served;
    const uint32_t c = cand - s_td_cand_served;
    if (n == 0)
    {
        return;
    }

    if (s_wstate == GI_WORKER_ARMED)
    {
        /*
         * Row 9. Spec 8 and 11: a mode OFF nobody asked for. Record it and log
         * it, because in OFF the device goes silent on the wire and looks
         * identical to one that was disarmed on purpose.
         */
        withdraw_pending();
        ESP_LOGW(TAG, "forced quiesce: another task is tearing the bus down "
                      "(spec 8.1 row 9)");
        s_core.self_off = GI_SELF_OFF_QUIESCE;
        s_core.mode = GI_OFF;
        s_we_enabled_bus = false;   /* the caller owns the teardown now, not us */
        s_release_bus = false;
        s_violations += n;
        s_wstate = GI_WORKER_IDLE;
    }
    else if (c > 0)
    {
        /* Row 13: nothing is forced; the bus was up and not the OTA handler's. */
        ESP_LOGW(TAG, "bus torn down by a caller spec 3.2 disables "
                      "(spec 8.1 row 13)");
        s_violations += c;
    }

    taskENTER_CRITICAL(&s_req_lock);
    s_td_served = posted;
    s_td_cand_served = cand;
    taskEXIT_CRITICAL(&s_req_lock);
}

/* AN OTA THAT FAILED (spec 8.1 row 11). */
static void serve_ota_end(void)
{
    taskENTER_CRITICAL(&s_req_lock);
    const uint32_t posted = s_ota_end_posted;
    taskEXIT_CRITICAL(&s_req_lock);
    if (posted == s_ota_end_served)
    {
        return;
    }
    s_ota_end_served = posted;
    if (s_wstate == GI_WORKER_UPDATING)
    {
        s_wstate = GI_WORKER_IDLE;
        s_ota_task = NULL;
        ESP_LOGW(TAG, "firmware update failed: back to IDLE (spec 8.1 row 11)");
    }
}

/* THE CORE SWITCHED ITSELF OFF (spec 8.1 row 7: receive errors, section 7). */
static void serve_self_off(void)
{
    if (s_wstate != GI_WORKER_ARMED || s_core.mode != GI_OFF)
    {
        return;
    }
    withdraw_pending();
    ESP_LOGW(TAG, "switched itself off: %s (spec 8.1 row 7)",
             gi_self_off_name(s_core.self_off));
    if (s_we_enabled_bus)
    {
        s_release_bus = true;   /* the OFF branch, next */
    }
    s_wstate = GI_WORKER_IDLE;
}

/*
 * THE UPDATE POINT (spec 8.1): the top of each worker pass, and nowhere else.
 * Pending events are taken in the spec's order -- a teardown by another
 * caller, an OTA starting or ending, a mode request, the core switching itself
 * off -- each seeing the state the one before it left. The OTA start shares the
 * slot with a mode request, so at most one of those two is pending.
 */
static void update_point(void)
{
    serve_teardown();
    serve_ota_end();
    take_request();
    serve_self_off();
}

esp_err_t gen_inhibit_ota_begin(void)
{
    if (s_worker == NULL)
    {
        return ESP_OK;      /* nothing to hand over */
    }
    if (xTaskGetCurrentTaskHandle() == s_worker)
    {
        return ESP_ERR_INVALID_STATE;
    }
    uint32_t ticket = 0;
    const esp_err_t perr = post_request(REQ_KIND_OTA_BEGIN, GEN_INHIBIT_OFF, 0,
                                        &ticket);
    if (perr != ESP_OK)
    {
        return perr;
    }
    return gen_inhibit_await_mode(ticket);
}

void gen_inhibit_ota_failed(void)
{
    if (s_worker == NULL)
    {
        return;
    }
    taskENTER_CRITICAL(&s_req_lock);
    s_ota_end_posted++;
    taskEXIT_CRITICAL(&s_req_lock);
    worker_wake();
}

esp_err_t gen_inhibit_set_mode(gen_inhibit_mode_t mode, uint32_t offset_us)
{
    /*
     * The worker itself applies directly: posting would have it wait for its
     * own loop. Nothing in the worker calls this today; the guard keeps a
     * future caller from deadlocking it.
     */
    if (s_worker != NULL && xTaskGetCurrentTaskHandle() == s_worker)
    {
        if (mode > GEN_INHIBIT_PASSIVE || offset_us > 4000)
        {
            return ESP_ERR_INVALID_ARG;
        }
        return apply_mode(mode, offset_us);
    }
    uint32_t ticket = 0;
    const esp_err_t perr = gen_inhibit_post_mode(mode, offset_us, &ticket);
    if (perr != ESP_OK)
    {
        return perr;
    }
    return gen_inhibit_await_mode(ticket);
}

void gen_inhibit_quiesce(void)
{
    /*
     * Two cases:
     *
     *   - Called from the worker itself (its own release path calls
     *     can_disable): it is already at its safe top-of-loop point, so just
     *     make sure it will not re-enter the driver, and return. Blocking here
     *     would deadlock -- the worker is the thing that serves the quiesce.
     *
     *   - Called from any other task: post a teardown (see s_td_posted) and
     *     block until the worker has served it at its update point, which is
     *     outside twai_receive() by construction (spec 8.1 rows 9 and 13).
     *     Bounded by the receive timeout; 1 s from the call.
     */
    if (s_worker == NULL)
    {
        return;
    }
    const TaskHandle_t me = xTaskGetCurrentTaskHandle();
    if (me == s_worker)
    {
        s_core.mode = GI_OFF;
        return;
    }
    /*
     * THE OTA HANDLER'S OWN TEARDOWN, which row 4 handed it: the worker is in
     * UPDATING and outside the driver, and nothing is counted (row 13). Only
     * that task, and only while UPDATING -- which only that task can end.
     */
    if (s_wstate == GI_WORKER_UPDATING && me == s_ota_task)
    {
        return;
    }

    /*
     * "The bus was up" (row 13): can.c's own state. A bus never enabled since
     * boot reads END_BUS, which is not up (spec 8.1 row 13, finding F3).
     */
    const bool bus_up = can_is_enabled();
    const int64_t deadline_us = esp_timer_get_time() + GEN_INHIBIT_MODE_WAIT_US;
    taskENTER_CRITICAL(&s_req_lock);
    const uint32_t mine = ++s_td_posted;
    if (bus_up)
    {
        s_td_candidates++;
    }
    taskEXIT_CRITICAL(&s_req_lock);
    worker_wake();

    for (;;)
    {
        taskENTER_CRITICAL(&s_req_lock);
        const bool served = (int32_t)(s_td_served - mine) >= 0;
        taskEXIT_CRITICAL(&s_req_lock);
        if (served)
        {
            return;
        }
        if (esp_timer_get_time() >= deadline_us)
        {
            /* Left posted: the worker goes OFF when it reaches the top. */
            ESP_LOGE(TAG, "quiesce timed out; worker did not confirm parked");
            return;
        }
        req_poll_delay();
    }
}

/* -------------------------------------------------------------- port 0 -- */

/*
 * THE PRE-TEARDOWN HOOK (spec 8, the quiesce backstop; twai_port T2 and T3,
 * can_port C5): every teardown of port 0, the stock can_disable()'s included,
 * runs gen_inhibit_quiesce() first, which can.c's can_disable() called inline
 * until port step 1b.
 */
static void port_pre_teardown(void *ctx)
{
    (void)ctx;
    gen_inhibit_quiesce();
}

/*
 * THE INSTALL HOOK (twai_port T2), run before every install of port 0, with
 * the filter that install will use.
 *
 * SPEC 5.1 ITEM 2: accept-all while the inhibitor owns the bus, forced here,
 * which can.c did inline. It is not the primary guarantee: apply_mode() clears
 * a narrowed filter before the bus comes up and refuses to arm if it is still
 * narrowed, because on the arming path ownership is still false here (the
 * mode is recorded after the install). What this covers is a re-install while
 * already owned. Review, 2026-09-26.
 *
 * THE COUNTERS AT THIS INSTALL (s_at_install), for the per-install figures.
 *
 * And, in the restart test build, the far end of spec 3's "until the driver is
 * installed again".
 */
static void port_install_hook(void *ctx, twai_port_filter_t *f)
{
    (void)ctx;
    if (gen_inhibit_owns_bus()
        && (f->acceptance_code != 0 || f->acceptance_mask != 0xFFFFFFFF))
    {
        ESP_LOGE(TAG, "acceptance filter is narrowed (code 0x%08lX mask 0x%08lX) "
                      "while the inhibitor owns the bus -- forcing accept-all "
                      "per spec 5.1 item 2",
                 (unsigned long)f->acceptance_code,
                 (unsigned long)f->acceptance_mask);
        f->acceptance_code = 0;
        f->acceptance_mask = 0xFFFFFFFF;
    }

    can_port_status_t now;
    if (twai_port_status(&now) == ESP_OK)
    {
        taskENTER_CRITICAL(&s_abort_lock);
        s_at_install = now;
        taskEXIT_CRITICAL(&s_abort_lock);
    }

#if GI_TEST_RESTART_QUIESCE
    gi_restart_test_pre_install();
#endif
}

static void at_install_copy(can_port_status_t *out)
{
    taskENTER_CRITICAL(&s_abort_lock);
    *out = s_at_install;
    taskEXIT_CRITICAL(&s_abort_lock);
}

/*
 * PORT 0 AS SPEC 3.3 SETS IT UP (port step 1b, user, 2026-10-31): the board's
 * port 0; the configured bitrate and listen-only from the install for OBSERVE,
 * both through the stock calls (can_set_bitrate(), can_set_silent()), which
 * replace the timing and mode below at every can_enable(); accept-all, forced
 * by the install hook while owned (5.1 item 2); an RX queue of 64 (5.1 item
 * 1); the TX queue of 5 that can.c's TWAI_GENERAL_CONFIG_DEFAULT gave; no
 * bus-off recovery (7); an install failure reported, not a restart, so that
 * apply_mode() can refuse with the reason (user, 2026-10-31; the stock
 * can_enable() still restarts, can_port C3); the quiesce as the pre-teardown
 * hook (8). Remote frames are kept and sends are not single-shot,
 * as before.
 *
 * Registered as port 0's backend with can_port, and gen_inhibit_owns_bus() and
 * the refusal count as its owner hook (can_port C4), which can.c's can_send()
 * called directly.
 */
void gen_inhibit_port_register(void)
{
    twai_port_config_t c;
    twai_port_config_defaults(&c);
    c.board_port      = 0;
    c.mode            = CAN_PORT_MODE_NORMAL;
    c.rx_queue_len    = GEN_INHIBIT_RX_QUEUE_LEN;
    c.tx_queue_len    = 5;
    c.recovery        = TWAI_PORT_RECOVERY_NEVER;
    c.drop_rtr        = false;
    c.single_shot     = false;
    c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
    c.pre_teardown    = port_pre_teardown;
    c.install_hook    = port_install_hook;
    c.hook_ctx        = NULL;
    ESP_ERROR_CHECK(twai_port_setup(&c));
    ESP_ERROR_CHECK(can_port_register(0, &twai_port_ops, NULL));
    can_port_register_owner(gen_inhibit_owns_bus,
                            gen_inhibit_note_refused_can_send);
}

/* ---------------------------------------------------------------- worker -- */

static void gen_inhibit_task(void *arg)
{
    twai_message_t rx;

    /*
     * s_worker is set by gen_inhibit_init() from xTaskCreate(), NOT here.
     * Spec 3's wake needs a handle that exists before this task's first
     * instruction, because gen_inhibit_init() arms an auto-arm build
     * immediately after creating it and the wake must not be lost.
     */
    ESP_LOGI(TAG, "worker started (prio %d)", GEN_INHIBIT_TASK_PRIO);

    while (1)
    {
        /*
         * SPEC 3: A POSTED MODE CHANGE IS APPLIED HERE, AND ONLY HERE -- at the
         * top of the loop, outside twai_receive() and between frames. Every
         * path through the loop comes back to this point: after a frame, after
         * a receive timeout (up to GEN_INHIBIT_RX_TIMEOUT_MS on a quiet bus,
         * which a wake cannot cut short), and out of either sleep, which the
         * poster's wake ends. Spec 8.1: this is the update point, where every
         * pending request is taken, in the table's order.
         */
        update_point();

        if (s_core.mode == GI_OFF)
        {
            gi_notify_off(&s_core);
            /*
             * Safe teardown point: we are not inside twai_receive() here, so
             * uninstalling the driver cannot pull the floor out from under a
             * blocked call.
             */
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
            worker_sleep(50);
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
            sched_advance(esp_timer_get_time(), &ev);
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
        esp_err_t rx_err = twai_port_receive(&rx, pdMS_TO_TICKS(GEN_INHIBIT_RX_TIMEOUT_MS));
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
            /*
             * Interruptible for spec 3, and this path is the one that
             * MEASURED worst. A re-arm from OBSERVE tears the driver down and
             * reinstalls it in normal mode from the caller's task, so this
             * worker's twai_receive() returns ESP_ERR_INVALID_STATE and lands
             * here -- then slept 100 ms while the new driver was already
             * filling the queue. That is the 120 frames the spec 3 paragraph
             * cites, and a wake that only covered the OFF branch would not
             * have touched it.
             *
             * Still a yield in every other case: the notification is
             * consumed, so at most one iteration per mode change skips the
             * delay, and the busy-wait at priority 18 that this delay exists
             * to prevent cannot come back.
             */
            worker_sleep(100);
            continue;
        }
        /*
         * FIRST THING AFTER THE DEQUEUE. Anything between the receive and this
         * read lets the queue drain further and understates the backlog.
         */
        rx_backlog_sample();
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
            sched_advance(t_rx, &ev);

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
                 * The verdict is not reported here. sched_advance() derives one core
                 * call per skip from the per-kind counters, so a deadline that
                 * withdraws TWO queued frames reports two -- which reporting from
                 * this return value could not do, and did not.
                 */
                (void)gs_command_received(&s_sched, t_rx, can_msgs_to_rx());
                sched_advance(t_rx, &ev);
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

/*
 * SPEC 11, "THE PAGE NAMES THE BINARY IT RUNS" (user, 2026-10-14): the ELF
 * SHA-256 from the running image's app descriptor, all 32 bytes as 64 lowercase
 * hex characters. esptool writes it into the descriptor when it makes the image,
 * so the image file carries the same value and the two can be compared.
 *
 * NOT esp_app_get_elf_sha256(). It copies app_elf_sha256_str, which holds only
 * CONFIG_APP_RETRIEVE_LEN_ELF_SHA characters (16 in this sdkconfig): a prefix
 * kept for the panic handler, not the hash. The spec asks for the hash.
 *
 * Read through a volatile pointer, as ESP-IDF's own conversion does
 * (esp_app_format/esp_app_desc.c, esp_app_format_init_elf_sha256()): the
 * descriptor is a const object whose initializer is a placeholder that esptool
 * overwrites in the image, so a compiler that can see the initializer may fold
 * the read to the placeholder. This file cannot see it today; the guard costs
 * 32 loads.
 *
 * Outside the page's snapshot, and it need not be in it: it is a property of
 * the image, the same at every reading, not a reading.
 */
#define GI_ELF_SHA256_HEX_LEN 64

static void elf_sha256_hex(char out[GI_ELF_SHA256_HEX_LEN + 1])
{
    static const char hex[] = "0123456789abcdef";
    const volatile uint8_t *b = esp_app_get_description()->app_elf_sha256;

    for (int i = 0; i < GI_ELF_SHA256_HEX_LEN / 2; i++)
    {
        const uint8_t v = b[i];
        out[2 * i]     = hex[v >> 4];
        out[2 * i + 1] = hex[v & 0x0F];
    }
    out[GI_ELF_SHA256_HEX_LEN] = '\0';
}

/*
 * SPEC 10, "0x7F3 NAMES THE BINARY" (port step 0c; user, 2026-10-19 and
 * 2026-10-25): diag_build_id, the first four bytes of the same hash, packed as
 * the charge interposer's 0x7F7 build id -- hash byte 0 most significant. The
 * encoder sends the u32 little-endian, so B0..B3 on the wire are hash bytes
 * 3, 2, 1, 0, and the DBC's unsigned value printed as eight hex digits reads
 * as the hash's first four bytes in order. Read through a volatile pointer for
 * elf_sha256_hex()'s reason.
 */
static uint32_t build_id(void)
{
    const volatile uint8_t *b = esp_app_get_description()->app_elf_sha256;

    return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) |
           ((uint32_t)b[2] << 8)  |  (uint32_t)b[3];
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

/*
 * SPEC SECTION 11 (2026-09-29): THE PAGE IS BUILT FROM A COPY, NEVER FROM THE
 * LIVE STATE.
 *
 * File-scope and not locals: together these are over a kilobyte, and the httpd
 * task's stack is not the place for it.
 *
 * SAFE AS STATICS ONLY BECAUSE EXACTLY ONE httpd INSTANCE SERVES ONE REQUEST AT
 * A TIME (user, 2026-10-12). Both callers are URI handlers in config_server.c,
 * and one server task runs its handlers in turn, so two pages are never built
 * at once. That is a property of how the server is started, not of this file.
 *
 * config_server_restart() calls httpd_start() a second time, and nothing calls
 * it today. Reviving it, or starting any other second server instance, would
 * let two requests run here at once: the second request's copy would overwrite
 * these while the first is still formatting from them, and the first page would
 * be built partly from one reading and partly from another. That is a torn
 * snapshot, which spec 11's one-snapshot rule exists to forbid, and nothing on
 * the page would show it.
 *
 * THE FIX THEN IS PER-REQUEST STORAGE: a stack-local snapshot, which means
 * growing the httpd task's stack (config_server_init() sets stack_size to 5120,
 * over HTTPD_DEFAULT_CONFIG's 4096), or one allocated per request. The two
 * handlers' own `static char out[GI_STATUS_PAGE_CAP]` buffers rest on the same
 * assumption and would need the same change.
 */
/*
 * Spec 3.2's refusal counters (user, 2026-10-12), copied with everything else
 * so the page is one reading. They are written by whichever task was refused
 * -- httpd, the SLCAN dispatch, MQTT -- and read here.
 */
static uint32_t   s_snap_refused_slcan;
static uint32_t   s_snap_refused_can_send;
/* Spec 8.1: the worker's state and the violation count, in the same reading. */
static gi_worker_state_t s_snap_wstate;
static uint32_t   s_snap_violations;

static gi_state_t s_snap_core;
static gs_t       s_snap_sched;
static uint32_t   s_snap_us;            /* the last copy's duration */
static uint32_t   s_snap_us_max;        /* the worst since boot */
#if GI_INSTRUMENT_RXQ
static uint32_t   s_snap_rxq_samples;
static uint32_t   s_snap_rxq_max;
static uint32_t   s_snap_rxq_ge[RXQ_NEDGES];
#endif

/*
 * THE SNAPSHOT'S EXCLUSION IS SINGLE-CORE-ONLY AND IT IS LOAD-BEARING. The CAN
 * worker does not take s_abort_lock; what stops it running mid-copy is that
 * taskENTER_CRITICAL masks interrupts and suspends preemption on a unicore
 * target. On a dual-core part the worker would keep running on the other core and
 * every reading could tear again -- silently, and looking exactly like a real
 * imbalance in 5.2 item 6. A build failure is the only honest outcome there.
 */
#if !CONFIG_FREERTOS_UNICORE
#error "gen_inhibit status snapshot assumes a unicore critical section excludes the CAN worker"
#endif

/*
 * Spec 11: the reset that started this boot, read ONCE at boot rather than once
 * per page. esp_reset_reason() latches its value before app_main runs, so a
 * per-page call would return the same thing -- but reading it once is what the
 * spec says, and it makes the value trivially part of the page's one snapshot:
 * an immutable cannot be torn.
 *
 * ESP_RST_UNKNOWN AS THE INITIAL VALUE IS DELIBERATE, AND IT IS NOT A SENTINEL.
 * config_server_start() runs at main.c:427, six lines BEFORE
 * gen_inhibit_init(), so there is a window in which the HTTP handler exists and
 * this has not been latched yet. Nothing can reach it today --
 * wifi_network_init() is at main.c:516, so there is no network to ask -- but
 * that is an ordering in another file, and if it ever is reached the honest
 * answer is the enum's own "cannot be determined", which the spec already
 * covers. An invented marker like "UNREAD" would put a state on the page that
 * no reader could look up.
 */
static esp_reset_reason_t s_reset_reason = ESP_RST_UNKNOWN;

/*
 * Spec 11: the enum name without its ESP_RST_ prefix, or the NUMBER when this
 * firmware does not know the value.
 *
 * THERE IS DELIBERATELY NO `default:` THAT MAPS TO A NAME. A value this switch
 * does not list is a reason ESP-IDF added since this was written, and reporting
 * it as "UNKNOWN" would file a new cause under an existing one -- the page would
 * read "the chip could not determine the reset reason" at exactly the moment the
 * chip determined it precisely. The bare number is unlovely and unambiguous, and
 * it can be looked up in esp_system.h.
 */
static void reset_reason_name(esp_reset_reason_t r, char *out, int n)
{
    const char *name = NULL;

    switch (r)
    {
        case ESP_RST_UNKNOWN:    name = "UNKNOWN";    break;
        case ESP_RST_POWERON:    name = "POWERON";    break;
        case ESP_RST_EXT:        name = "EXT";        break;
        case ESP_RST_SW:         name = "SW";         break;
        case ESP_RST_PANIC:      name = "PANIC";      break;
        case ESP_RST_INT_WDT:    name = "INT_WDT";    break;
        case ESP_RST_TASK_WDT:   name = "TASK_WDT";   break;
        case ESP_RST_WDT:        name = "WDT";        break;
        case ESP_RST_DEEPSLEEP:  name = "DEEPSLEEP";  break;
        case ESP_RST_BROWNOUT:   name = "BROWNOUT";   break;
        case ESP_RST_SDIO:       name = "SDIO";       break;
        case ESP_RST_USB:        name = "USB";        break;
        case ESP_RST_JTAG:       name = "JTAG";       break;
        case ESP_RST_EFUSE:      name = "EFUSE";      break;
        case ESP_RST_PWR_GLITCH: name = "PWR_GLITCH"; break;
        case ESP_RST_CPU_LOCKUP: name = "CPU_LOCKUP"; break;
        default:                 break;
    }

    if (name != NULL)
    {
        snprintf(out, (size_t)n, "%s", name);
    }
    else
    {
        snprintf(out, (size_t)n, "%d", (int)r);
    }
}

int gen_inhibit_get_stats_json(char *buf, int buflen)
{
    const int64_t t_now = esp_timer_get_time();

    /*
     * Read OUTSIDE the section, and this is the one value on the page that is not
     * in the same copy as the rest. The port's status read takes the TWAI
     * driver's own spinlock and the port's; nesting those inside ours would
     * create a lock ordering this component does not otherwise have, for
     * counters that are cumulative since the driver was installed and are terms
     * in no balance.
     *
     * The install's counters go with it, and must be the same install's: the
     * hook takes them just before an install, so a reading of install N+1 has
     * bring_ups one above them. A reinstall between the copy and the read is
     * caught that way and read again (at_install_copy()).
     */
    can_port_status_t drv;
    can_port_status_t drv_at;
    bool drv_ok = false;
    for (int tries = 0; tries < 2; tries++)
    {
        at_install_copy(&drv_at);
        drv_ok = (twai_port_status(&drv) == ESP_OK
                  && drv.state != CAN_PORT_STATE_DOWN);
        if (!drv_ok || drv.bring_ups == drv_at.bring_ups + 1)
        {
            break;
        }
    }
    /*
     * bus_on is the CONTROLLER's state, not the driver's installation (spec 11,
     * changed 2026-10-06): false when the controller is stopped, bus-off or
     * recovering, and false when the status read itself fails -- which is what
     * the driver not being installed looks like from here. "Driver installed"
     * stays readable as `drv` being non-null in the JSON, so nothing is lost.
     * It comes from the read above, so it is in that read's snapshot and not
     * the critical section's -- the same caveat as the rest of `drv`.
     *
     * It was can_is_enabled() until now, which read `true` for the whole 42.1 s
     * the device sat bus-off and silent on 2026-10-06. Mode switching still
     * keys on the driver being installed; only the reported value changes.
     */
    const bool bus_on = drv_ok && (drv.state == CAN_PORT_STATE_RUNNING);

    const int64_t t_snap0 = esp_timer_get_time();
    taskENTER_CRITICAL(&s_abort_lock);
    s_snap_core  = s_core;
    s_snap_sched = s_sched;
    s_snap_refused_slcan    = s_refused_slcan;
    s_snap_refused_can_send = s_refused_can_send;
    s_snap_wstate           = s_wstate;
    s_snap_violations       = s_violations;
#if GI_INSTRUMENT_RXQ
    s_snap_rxq_samples = s_rxq_samples;
    s_snap_rxq_max     = s_rxq_max;
    for (int i = 0; i < RXQ_NEDGES; i++)
    {
        s_snap_rxq_ge[i] = s_rxq_ge[i];
    }
#endif
    taskEXIT_CRITICAL(&s_abort_lock);
    const int64_t t_snap1 = esp_timer_get_time();

    /*
     * MEASURED, NOT ASSERTED. Section 11 requires the section to be short enough
     * not to move any 5.2 timing bound; reporting how long it actually took makes
     * that a number the bench can check against the 4.69 ms minimum inter-frame
     * gap rather than a claim in a comment.
     */
    s_snap_us = (uint32_t)(t_snap1 - t_snap0);
    if (s_snap_us > s_snap_us_max)
    {
        s_snap_us_max = s_snap_us;
    }

    const gi_state_t *st = &s_snap_core;

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
                     bus_on ? "true" : "false",
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

    /*
     * CLAMP BEFORE ANYTHING ELSE IS APPENDED. snprintf returns what it WOULD have
     * written, so on a short buffer `n` above is larger than `buflen` -- and the
     * next snprintf would then get `buf + n` past the end and a negative size
     * that converts to a huge size_t. That is a heap overflow, not a truncation,
     * and it is what case 20 of the shim suite caught when these two fields were
     * first appended here.
     */
    n = gi_clamp(n, buflen);

    /*
     * Spec 5.2 item 5 / section 10: the same skip counts the 0x7F9 page carries,
     * from the CORE. The scheduler's own `sched.skipped_withdrawn` and
     * `sched.skipped_late` are below, and the two are derived along different
     * paths -- the core's from one gi_on_inhibit_skip() per skip, the
     * scheduler's from its own counters. They must agree, and a bench check that
     * they do is worth more than either number alone.
     *
     * `snapshot_us` is section 11's critical section, measured.
     */
    /*
     * Spec 11, "Uptime and reset reason are reported" (user, 2026-10-09).
     *
     * uptime_ms comes from `t_now` -- the SAME esp_timer_get_time() reading that
     * every freshness field on this page is computed against, not a fresh call
     * here. That is what puts it inside the one-snapshot rule: a reader
     * comparing uptime_ms against key_fresh is comparing two statements about
     * one instant rather than two instants a page-build apart.
     *
     * 64-BIT, NOT 32. A u32 millisecond counter wraps after 49.7 days, and a
     * wrap presents as uptime going DOWN -- which is exactly and only what this
     * field exists to mean. A field whose single job is "a drop is a reboot"
     * must not be able to drop for any other reason, so it gets the width that
     * cannot. Spec 11 says it only rises while the device stays up; at u32 that
     * sentence would be false roughly every seven weeks on a device left
     * powered, and the false reading would be indistinguishable from the true
     * one.
     */
    char reason[GI_RESET_REASON_LEN];
    reset_reason_name(s_reset_reason, reason, (int)sizeof(reason));
    char elf_sha[GI_ELF_SHA256_HEX_LEN + 1];
    elf_sha256_hex(elf_sha);

    n = gi_clamp(n + snprintf(buf + n, buflen - n,
                     "\"skips_withdrawn\":%lu,\"skips_late\":%lu,"
                     "\"snapshot_us\":%lu,\"snapshot_us_max\":%lu,"
                     "\"uptime_ms\":%llu,\"reset_reason\":\"%s\","
                     "\"elf_sha256\":\"%s\",",
                     (unsigned long)st->skips_withdrawn,
                     (unsigned long)st->skips_late,
                     (unsigned long)s_snap_us,
                     (unsigned long)s_snap_us_max,
                     (unsigned long long)(t_now / 1000),
                     reason, elf_sha), buflen);

    /*
     * SPEC 11, "TRIP 6's ERROR HISTORY IS REPORTED" (user, 2026-10-12). From
     * the snapshot copy, at the reading's time, through the trip's own test.
     *
     * The reason this exists: on 2026-10-10 a re-arm latched trip 6 with the
     * controller's bus_error reading 0, and nothing on the page showed the
     * history the trip had acted on, so every answer about it was inference.
     *
     * err_age_ms is null rather than 0 when nothing has been recorded since
     * key-on, because 0 would read as "an error frame just now", which is the
     * opposite.
     */
    {
        gi_err_view_t ev6;
        gi_err_view(st, t_now, &ev6);

        n = gi_clamp(n + snprintf(buf + n, buflen - n,
                         "\"err_n\":%lu,\"err_burst\":%s,\"err_age_ms\":",
                         (unsigned long)ev6.n,
                         ev6.burst ? "true" : "false"), buflen);
        if (ev6.have_newest)
        {
            n = gi_clamp(n + snprintf(buf + n, buflen - n, "%llu,",
                             (unsigned long long)((t_now - ev6.newest_us)
                                                  / 1000)), buflen);
        }
        else
        {
            n = gi_clamp(n + snprintf(buf + n, buflen - n, "null,"), buflen);
        }
    }

    /*
     * SPEC 3.2 + 11, "REFUSED TRANSMITS ARE COUNTED" (user, 2026-10-12). Since
     * boot, and from the snapshot like everything else. 3.2 used to say a
     * refusal was logged, which recorded nothing: every log is muted after
     * boot on a truck unit.
     */
    n = gi_clamp(n + snprintf(buf + n, buflen - n,
                     "\"refused_slcan\":%lu,\"refused_can_send\":%lu,",
                     (unsigned long)s_snap_refused_slcan,
                     (unsigned long)s_snap_refused_can_send), buflen);
    /*
     * SPEC 8.1: who holds the bus, and the teardowns by another caller since
     * boot (rows 9 and 13). Why the worker last switched itself off is
     * `self_off`, above.
     */
    n = gi_clamp(n + snprintf(buf + n, buflen - n,
                     "\"worker\":\"%s\",\"bus_violations\":%lu,",
                     worker_state_name(s_snap_wstate),
                     (unsigned long)s_snap_violations), buflen);

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
    n = gi_clamp(n + drv_json(&drv, &drv_at, drv_ok, buf + n, buflen - n), buflen);
    /*
     * The transmit scheduler's counters (spec 5.2 item 6, section 11): per class
     * queued/sent/aborted/requeued/dropped/refused and the two waits, plus the
     * skips, the aborts and how hard they were, and the two on-time counts --
     * `ontime` and `ontime_unverified`, WHICH ARE NEVER SUMMED. An unverified
     * verdict is one reached with frames already in the receive queue, where the
     * dequeue lags the arrival and a physically late frame can score as on time.
     */
    n = gi_clamp(n + snprintf(buf + n, buflen - n, ","), buflen);
    n = gi_clamp(n + gs_json(&s_snap_sched, buf + n, buflen - n), buflen);
#if GI_INSTRUMENT_RXQ
    n = gi_clamp(n + rxq_json(s_snap_rxq_samples, s_snap_rxq_max,
                              s_snap_rxq_ge, buf + n, buflen - n), buflen);
#else
    n = gi_clamp(n + rxq_json(0, 0, NULL, buf + n, buflen - n), buflen);
#endif
    n = gi_clamp(n + snprintf(buf + n, buflen - n, "}\n"), buflen);

    /*
     * The CONTENT length, never the buffer length: a caller that hands `n` to
     * httpd_resp_send() must not be told to read the terminator or past it.
     */
    return (buflen > 0 && n >= buflen) ? buflen - 1 : n;
}

/* ------------------------------------------------------------------ init -- */

void gen_inhibit_init(uint8_t bitrate, uint8_t silent)
{
    /* Spec 11: read once at boot, not once per page. See reset_reason_name(). */
    s_reset_reason = esp_reset_reason();

    /*
     * SPEC 3.2, "THE BOOT-TIME CONFIGURATION STAYS, AND ARMING COMES AFTER IT"
     * (user, 2026-10-16). The configured bitrate and can_mode are what the
     * worker installs with and what leaving OBSERVE restores, so they are
     * stored here, before the worker exists or anything can arm. On 65e9446
     * main.c stored them after gen_inhibit_init(), whose auto-arm had already
     * installed the stock defaults; the setters then did nothing (ON_BUS).
     */
    can_set_bitrate(bitrate);
    can_set_silent(silent);

    /*
     * Spec 8.1: IDLE at boot, nothing posted, nothing counted. Reset here as
     * well as by the statics' initialisers because the host suite calls this
     * once per case.
     */
    s_wstate = GI_WORKER_IDLE;
    s_violations = 0;
    s_ota_task = NULL;
    taskENTER_CRITICAL(&s_req_lock);
    s_req.state = REQ_IDLE;
    s_td_posted = s_td_candidates = s_td_served = s_td_cand_served = 0;
    s_ota_end_posted = s_ota_end_served = 0;
    taskEXIT_CRITICAL(&s_req_lock);

    gi_config_t cfg;
    gi_config_defaults(&cfg);
    cfg.fw_version = DIAG_FW_VERSION;
    cfg.build_id   = build_id();     /* spec 10, 0x7F3 */
    cfg.autoarm_configured = (GEN_INHIBIT_AUTOARM_MODE != GEN_INHIBIT_OFF);

    gi_init(&s_core, &cfg);

    /*
     * The handle comes out of xTaskCreate() rather than out of the task
     * itself, which is what makes the wake below safe to send: FreeRTOS
     * writes *pxCreatedTask before the new task can be scheduled, so
     * s_worker is valid here even though the worker has not run a single
     * instruction, and a notification sent now is latched in its TCB and
     * ends its first sleep.
     *
     * The auto-arm boot path is exactly that race: xTaskCreate() then
     * gen_inhibit_set_mode() a few lines below. It was 1 in 3 boots going
     * live with a backlog.
     */
    xTaskCreate(gen_inhibit_task, "gen_inhibit", GEN_INHIBIT_STACK, NULL,
                GEN_INHIBIT_TASK_PRIO, &s_worker);

    if (GEN_INHIBIT_AUTOARM_MODE != GEN_INHIBIT_OFF)
    {
        ESP_LOGW(TAG, "auto-arm on boot: mode %d", (int)GEN_INHIBIT_AUTOARM_MODE);
        gen_inhibit_set_mode(GEN_INHIBIT_AUTOARM_MODE, GEN_INHIBIT_AUTOARM_OFFSET_US);
    }
}
