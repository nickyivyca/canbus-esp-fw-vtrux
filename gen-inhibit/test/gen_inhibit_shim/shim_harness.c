/*
 * shim_harness -- the shim's harness on the one TWAI driver fake. See
 * shim_harness.h for what it gives a case, and why.
 *
 * THE DRIVER IS NOT HERE. Since port step 1b the TWAI driver, its controller
 * and its register file are shared/twai_port/test/'s fake (twai_port T10),
 * compiled from the pinned shared checkout; this file wires it up and keeps
 * what is the shim's own: the worker scheduler, the virtual clock, the log
 * capture, the task handles and notifications, the reset reason, and the wire
 * invariant, which the fake's transmit hooks call. The ft_* calls on the
 * driver are this harness's names for the fake's knobs and readings (gen-inhibit
 * reviewer, 2026-10-31: logic-level fakes stay the implementor's; a fault the
 * fake cannot inject goes to the tester, never into a copy here).
 *
 * THE SCHEDULER. The worker runs on its own pthread because gen_inhibit.c's
 * worker is an infinite loop that cannot be called a step at a time. But the
 * two threads never run concurrently: every point where the worker would block
 * on the real device -- twai_receive() and vTaskDelay() -- hands control to
 * the test and waits to be woken. So exactly one thread is runnable at a time,
 * virtual time only moves when the test moves it, and two runs of the same
 * script produce the same trace. A blocking receive reaches the scheduler
 * through the fake's wait hook (wait_turn() below).
 *
 * That is deliberate and it is also a limitation worth stating: this cannot
 * find a RACE. It finds attribution and sequencing bugs, which is what the
 * four shim defects of 2026-09-25 were. A race between the worker and the
 * httpd task is out of reach here and belongs on hardware.
 */
#include "shim_harness.h"

#include "driver/twai.h"
#include "twai_fake.h"
#include "esp_log.h"
#include "esp_system.h"
#include "gen_inhibit_core.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * THE FRAMES THE TEST DELIVERED, counted here because the fake counts what it
 * received, self-received frames included (ft_delivered_count_id() says why
 * that would defeat the one check that catches a self-reception loop).
 */
#define DELIVMAX 16
static struct { uint32_t id; int n; } g_deliv[DELIVMAX];
static int g_ndeliv;

int ft_delivered_count_id(uint32_t id)
{
    for (int i = 0; i < g_ndeliv; i++)
    {
        if (g_deliv[i].id == id) return g_deliv[i].n;
    }
    return 0;
}

static void deliv_note(uint32_t id)
{
    for (int i = 0; i < g_ndeliv; i++)
    {
        if (g_deliv[i].id == id) { g_deliv[i].n++; return; }
    }
    if (g_ndeliv < DELIVMAX)
    {
        g_deliv[g_ndeliv].id = id;
        g_deliv[g_ndeliv].n = 1;
        g_ndeliv++;
    }
}

/* ------------------------------------------------------------ the clock -- */

static int64_t g_now;

/*
 * READING THE CLOCK COSTS TIME, and modelling that is not a nicety -- without
 * it the suite HANGS.
 *
 * gen_inhibit.c's probe path spins: `while (esp_timer_get_time() < f->due_us)`.
 * With a clock that only moved when the test moved it, that loop could never
 * terminate: the worker held the CPU, the test never ran, and nothing advanced
 * g_now. Found by the reviewing session on 2026-09-25, when a mutation made
 * OBSERVE emit a probe -- shim_test then ran at 100 % CPU for 11 minutes until
 * it was killed by PID. A HANG REPORTS NOTHING; it is worse than a failure,
 * because a failure names the rule.
 *
 * One microsecond per call is the honest model: on the device that spin really
 * does consume time, at priority 18, which is exactly why spec 3 caps the probe
 * offset at 4000 us. Determinism is unaffected -- the same sequence of calls
 * produces the same drift -- and the scale is irrelevant beside the
 * millisecond quantities everything here is measured in.
 */
int64_t esp_timer_get_time(void) { return g_now++; }
int64_t ft_now(void) { return g_now; }

/*
 * Spec 11's reset reason. gen_inhibit_init() latches it once, and setup() calls
 * gen_inhibit_init(), so a case that wants a particular boot reason sets this
 * BEFORE setup().
 *
 * DELIBERATELY NOT CLEARED BY ft_reset(). ft_reset() is the first statement of
 * setup(), so a reason cleared there could never be staged by a case at all --
 * the window between the two does not exist from the outside. The cost is that
 * the value persists across cases, so a case that changes it puts it back.
 */
static esp_reset_reason_t g_reset_reason = ESP_RST_POWERON;

esp_reset_reason_t esp_reset_reason(void) { return g_reset_reason; }
void ft_set_reset_reason(esp_reset_reason_t r) { g_reset_reason = r; }

/* -------------------------------------------------------------- logging -- */

#define LOGMAX 512
static char g_log[LOGMAX][200];
static int  g_nlog;

void ml_note(char level, const char *fmt, ...)
{
    char buf[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (g_nlog < LOGMAX)
    {
        snprintf(g_log[g_nlog], sizeof(g_log[0]), "%c %s", level, buf);
        g_nlog++;
    }
    printf("    %8lld LOG %c %s\n", (long long)g_now, level, buf);
}

int ml_count(const char *needle)
{
    int n = 0;
    for (int i = 0; i < g_nlog; i++)
    {
        if (strstr(g_log[i], needle) != NULL) n++;
    }
    return n;
}

void ml_clear(void) { g_nlog = 0; }


/*
 * THE FAKE'S CLOCK IS THIS ONE, read without the 1 us that esp_timer_get_time()
 * charges: the cost models the firmware's own reads, and the fake reading the
 * time to catch its controller up must not move it.
 */
static int64_t clock_now(void *ctx)
{
    (void)ctx;
    return g_now;
}

/* The air time this harness gives every frame; see ft_set_air_time(). */
#define SHIM_AIR_TIME_US 200
static int64_t g_air_time = SHIM_AIR_TIME_US;   /* for ft_timing_config() */

/* Driver installs made from a thread other than the worker (spec 3). */
static int g_install_off_worker;


/* ------------------------------------------------- the wire invariant --- */

#define WVMAX 32
static char g_wv[WVMAX][200];
static int  g_nwv;

static uint32_t g_allow[8];
static int      g_nallow;
static char     g_allow_why[80];

int ft_wire_violations(void) { return g_nwv; }

const char *ft_wire_violation(int i)
{
    return (i >= 0 && i < g_nwv) ? g_wv[i] : "";
}

void ft_allow_ids(const uint32_t *ids, int n, const char *why)
{
    if (n > (int)(sizeof(g_allow) / sizeof(g_allow[0])))
    {
        n = (int)(sizeof(g_allow) / sizeof(g_allow[0]));
    }
    for (int i = 0; i < n; i++) g_allow[i] = ids[i];
    g_nallow = n;              /* negative means the empty set, not "unset" */
    snprintf(g_allow_why, sizeof(g_allow_why), "%s", why ? why : "");
}

static void wv(const char *fmt, ...)
{
    va_list ap;
    if (g_nwv >= WVMAX) return;
    va_start(ap, fmt);
    vsnprintf(g_wv[g_nwv], sizeof(g_wv[0]), fmt, ap);
    va_end(ap);
    g_nwv++;
}

/*
 * THE WIRE INVARIANT: everything the firmware hands to twai_transmit() passes
 * through here.
 *
 * These are not restatements of what the core decided -- invariants.py already
 * judges that, and judged all five round-6 mutations correct, because they
 * were. These are statements about the frame that leaves the controller, which
 * nothing had ever looked at.
 */
static void check_wire(const twai_message_t *m)
{
    const uint32_t id = m->identifier;

    /* Spec 4: a standard 11-bit data frame, not extended, not remote. */
    if (m->extd)
    {
        wv("0x%03X queued as a 29-bit extended identifier: the GENE inverter "
           "filters on the 11-bit ID and would never see it, so the VCM's "
           "torque stands while tx_ok still counts up", (unsigned)id);
    }
    if (m->rtr)
    {
        wv("0x%03X queued as a remote-transmission request, which carries no "
           "data at all", (unsigned)id);
    }

    /*
     * Self-reception off. On this bus our own 0x051 is indistinguishable from
     * the VCM's, so receiving it means answering it: a transmit loop.
     */
    if (m->self)
    {
        wv("0x%03X queued with self-reception: the worker receives its own "
           "frame, cannot tell it from the VCM's, and answers it", (unsigned)id);
    }

    /*
     * Single-shot off. The user's decision of 2026-09-25, recorded in spec 4:
     * retransmission stays, because in a regime producing 0.07-0.21 error
     * frames per second a single bus error would otherwise abandon the frame,
     * and a retry lands well inside the VCM's 4.69 ms gap.
     */
    if (m->ss)
    {
        wv("0x%03X queued single-shot: one bus error would abandon the frame "
           "instead of retrying inside the 4.69 ms gap", (unsigned)id);
    }

    /* Spec 4: the inhibit frame matches the VCM's DLC of 6; everything else
     * this firmware sends is a diag page or the probe, at 8. */
    if (id == GI_VCM_ID)
    {
        if (m->data_length_code != 6)
        {
            wv("0x051 queued with DLC %u; spec 4 sends the VCM's own length, "
               "which is 6", (unsigned)m->data_length_code);
        }
    }
    else if (m->data_length_code != 8)
    {
        wv("0x%03X queued with DLC %u; the diag pages and the probe are 8",
           (unsigned)id, (unsigned)m->data_length_code);
    }

    /*
     * Spec 4.1, on the bytes as the controller received them. B0 is held at
     * 0x08 so that transmitting the VCM's 0x10 shutdown value is structurally
     * impossible, and the torque bytes are the whole point of the frame: a
     * copy that truncated before byte 5 would leave the counter byte stale,
     * which the inverter rejects, and one that mangled bytes 1-2 would command
     * torque from a module whose job is to remove it.
     */
    if (id == GI_VCM_ID && m->data_length_code >= 3)
    {
        if (m->data[0] != GI_B0_ENGINE_OFF)
        {
            wv("0x051 queued with B0 = 0x%02X; spec 4.1 holds it at 0x08",
               m->data[0]);
        }
        if (m->data[1] != 0x00 || m->data[2] != 0x80)
        {
            wv("0x051 queued with torque bytes %02X %02X; this module exists "
               "to command zero, which is 00 80", m->data[1], m->data[2]);
        }
    }

    /*
     * THE FIXED BYTES OF THE OTHER TWO FRAME SHAPES.
     *
     * Only 0x051's payload was checked here at first, and a mutation copying
     * six bytes instead of eight survived the whole suite because of it: at
     * DLC 6 the inhibit frame loses nothing, while the diag pages and the
     * probe are DLC 8 and their last two bytes carry content. The probe's
     * trailing 0x5A went to zero and nothing anywhere noticed.
     *
     * These are constants, so the fake can assert them without reaching into
     * the core for anything. What they cannot cover is the VARIABLE content --
     * a truncated soc_x100 in the status page reads as a legitimate 0 %. That
     * needs an independent second report of the same number to compare
     * against, which is what case 16 does with the diag JSON.
     */
    if (id == GI_PROBE_ID && m->data_length_code == 8)
    {
        if (m->data[7] != 0x5A)
        {
            wv("the probe's trailing sentinel is 0x%02X, not 0x5A -- a capture "
               "cannot tell this frame from anything else on 0x7F0, and the "
               "bytes before it are equally suspect", m->data[7]);
        }
    }
    else if (id == GI_DIAG_ID_STATUS && m->data_length_code == 8)
    {
        /*
         * The STATUS page ALONE carries the schema version, in byte 0. The
         * other three pages use byte 0 for content -- STATUS2's is the abort
         * reason code -- so this check belongs to one ID and not to the
         * family, which is what the first version got wrong.
         */
        if (m->data[0] != GI_DIAG_SCHEMA_VER)
        {
            wv("0x%03X queued with schema version %u; this build writes %u, "
               "and a decoder reading the wrong schema misreads every byte "
               "after it", (unsigned)id, m->data[0],
               (unsigned)GI_DIAG_SCHEMA_VER);
        }
    }

}

/*
 * WHICH IDs MAY REACH THE BUS -- judged on frames the controller ACCEPTED, not
 * on every call the firmware made.
 *
 * The split from check_wire() above is the whole point, and it was found the
 * hard way. The frame flags and the payload are properties of what the
 * firmware BUILT: a frame constructed with extd set is wrong whether or not
 * the driver happened to take it. Which IDs go out is a property of what
 * reaches the BUS, and a call the controller refuses never does.
 *
 * Collapsing the two reported OBSERVE as broken on the first run. It is not.
 * gen_inhibit.c:495 states the design outright: the section 10 diag pages are
 * handed to twai_transmit() in OBSERVE and the listen-only controller refuses
 * them, "the documented cost of a genuinely passive tap". The firmware really
 * does construct a diag page there, on purpose, and really does rely on the
 * driver to drop it.
 */
static void check_wire_accepted(const twai_message_t *m)
{
    const uint32_t id = m->identifier;

    if (g_nallow < 0)
    {
        wv("0x%03X reached the bus, and %s", (unsigned)id,
           g_allow_why[0] ? g_allow_why : "nothing may be queued here");
    }
    else if (g_nallow > 0)
    {
        bool ok = false;
        for (int i = 0; i < g_nallow; i++)
        {
            if (g_allow[i] == id) { ok = true; break; }
        }
        if (!ok)
        {
            wv("0x%03X reached the bus, and %s", (unsigned)id,
               g_allow_why[0] ? g_allow_why : "this scenario does not allow it");
        }
    }
}

/*
 * Is the worker parked inside twai_receive() right now? For feed(), which may
 * grant it one more turn to take a frame it has not taken -- but must not do
 * that to a worker parked anywhere else, because a turn in the OFF branch
 * costs that branch's whole 50 ms sleep.
 */
bool ft_in_receive(void) { return twai_fake_in_receive(); }

int  ft_unsafe_teardowns(void) { return twai_fake_unsafe_teardowns(); }
int  ft_installed_mode(void)
{
    return twai_fake_installed() ? (int)twai_fake_mode() : -1;
}
/* Delivered while the controller was not receiving: no driver, stopped, or
 * bus-off. The acceptance filter's drops are not these. */
int  ft_rx_dropped_off(void)   { return (int)twai_fake_rx_not_received(); }
int      ft_install_count(void)          { return twai_fake_install_count(); }
uint32_t ft_installed_brp(void)          { return twai_fake_installed_brp(); }
int      ft_installs_off_worker(void)    { return g_install_off_worker; }
uint32_t ft_installed_rx_queue_len(void)
{
    return twai_fake_installed_general()->rx_queue_len;
}
uint32_t ft_installed_acc_code(void) { return twai_fake_installed_filter()->acceptance_code; }
uint32_t ft_installed_acc_mask(void) { return twai_fake_installed_filter()->acceptance_mask; }
int      ft_installed_single_filter(void)
{
    return twai_fake_install_count() == 0
           ? -1 : (int)twai_fake_installed_filter()->single_filter;
}
void ft_fail_alerts_config(bool fail) { twai_fake_fail_reconfigure_alerts(fail); }
void ft_bus_off(void) { twai_fake_bus_off(); }
/* ESP_FAIL and ESP_ERR_INVALID_STATE, the values the old fake returned. */
void ft_fail_install_next(void) { twai_fake_fail_install_next(ESP_FAIL); }
void ft_fail_start_next(void) { twai_fake_fail_start_next(ESP_ERR_INVALID_STATE); }

int              ft_sent_count(void) { return twai_fake_sent_count(); }
const ft_frame_t *ft_sent(int i) { return twai_fake_sent(i); }
int              ft_wire_count(void) { return twai_fake_wire_count(); }
int ft_rx_pending(void) { return twai_fake_rx_pending(); }
int ft_self_received(void) { return (int)twai_fake_self_received(); }
int64_t ft_last_rx_time(void) { return twai_fake_last_rx_time(); }

/*
 * The fake's logs grow as they need to, so they never stop recording and every
 * count taken from them is exact. Kept, returning false, because the long arms
 * ask before trusting a count: the question still has to be asked of any log
 * that could fill.
 */
bool ft_logs_saturated(void) { return false; }
const ft_frame_t *ft_wire(int i) { return twai_fake_wire(i); }


static void (*g_step_hook)(void);
void ft_set_step_hook(void (*fn)(void)) { g_step_hook = fn; }

/* Every step boundary, so an always-property is checked as one. */
static void step(void)
{
    if (g_step_hook) g_step_hook();
}

/* Bound on per-ID air-time overrides. Declared here because the high-water record
 * below is the first user; the live table further down shares it. */
#define AIRMAX 8

/*
 * HIGH-WATER RECORD OF THE TIMING KNOBS, for ft_timing_config(). Separate from the
 * live values because a case that clears a knob must still report having used it.
 */
static int64_t g_air_time_set;          /* non-default default-air-time, if any */
static int     g_stalls_armed;
static struct { uint32_t id; int64_t us; } g_air_id_ever[AIRMAX];
static int     g_n_air_id_ever;

static void note_air_id(uint32_t id, int64_t us)
{
    if (us == 0) { return; }            /* clearing is not a use */
    for (int i = 0; i < g_n_air_id_ever; i++)
    {
        if (g_air_id_ever[i].id == id)
        {
            if (us > g_air_id_ever[i].us) { g_air_id_ever[i].us = us; }
            return;
        }
    }
    if (g_n_air_id_ever < AIRMAX)
    {
        g_air_id_ever[g_n_air_id_ever].id = id;
        g_air_id_ever[g_n_air_id_ever].us = us;
        g_n_air_id_ever++;
    }
}

void ft_timing_config(char *buf, int len)
{
    int n = snprintf(buf, (size_t)len, "air=%lldus",
                     (long long)(g_air_time_set ? g_air_time_set : g_air_time));
    if (g_air_time_set)
    {
        n += snprintf(buf + n, (size_t)(len - n), " (set)");
    }
    for (int i = 0; i < g_n_air_id_ever && n < len - 24; i++)
    {
        n += snprintf(buf + n, (size_t)(len - n), ", 0x%03X=%lldus",
                      g_air_id_ever[i].id, (long long)g_air_id_ever[i].us);
    }
    if (g_stalls_armed && n < len - 20)
    {
        n += snprintf(buf + n, (size_t)(len - n), ", stalls=%d", g_stalls_armed);
    }
    if (g_n_air_id_ever == 0 && !g_stalls_armed && !g_air_time_set
        && n < len - 12)
    {
        snprintf(buf + n, (size_t)(len - n), ", defaults");
    }
}

/*
 * THE AIR TIME THIS HARNESS GIVES EVERY FRAME, 200 us whatever its length:
 * twai_fake_set_air_time_us(), set at every ft_reset(). A deliberate
 * simplification, commented so it is not rediscovered as a bug.
 *
 * Real air time on this bench is ~16 us per byte, measured per DLC by
 * e4_rig_calibration.py's airtime phase (IXXAT readings tracked unstuffed
 * theory to +4 us, in exact 16 us steps), and the fake's own default is each
 * frame's unstuffed length. So a DLC-6 0x051 really is shorter than a DLC-8
 * diag page, and this harness does not represent that: every timing in the
 * cases was written against 200 us.
 *
 * WHAT IT COSTS: the model will not make a longer frame likelier to be caught
 * mid-transmission than a short one. Anything turning on that -- the
 * TRANSMITTING-versus-AWAITING distinction an abort depends on (item 4), and
 * so the shape of a preemption -- is not exercised faithfully by the default.
 * ft_set_air_time_id() gives one ID its own air time when a case needs it;
 * case 28 drives 400 ms on the five diag IDs for exactly that reason.
 */
void ft_set_air_time(int64_t us)
{
    g_air_time = us;
    g_air_time_set = us;
    twai_fake_set_air_time_us(us);
}
void ft_stall_next(int n) { twai_fake_stall_next(n); g_stalls_armed += n; }
void ft_stall_id(uint32_t id, int n)
{
    twai_fake_stall_id(id, n);
    g_stalls_armed += n;
}

void ft_set_air_time_id(uint32_t id, int64_t us)
{
    note_air_id(id, us);
    twai_fake_set_air_time_id(id, us);
}

/*
 * COUNTS BY ID, KEPT AS THE LOGS GROW. The step hook asks for two of these at
 * every step boundary, and the fake answers by scanning its whole log. Its logs
 * never stop recording, so over the long arm's 706,283 steps those scans made
 * case 33 four times slower (30 s to 118 s of wall clock) and ran it past its
 * budget under ASan. The fake's logs only grow between resets, so each count
 * here reads only the entries added since it last looked; a log shorter than
 * what was read means a reset, and the count starts again. An ID beyond the
 * table is answered by the fake's own scan.
 */
#define IDCOUNTMAX 32
typedef struct {
    int      read;              /* log entries counted so far */
    int      n;
    uint32_t id[IDCOUNTMAX];
    int      count[IDCOUNTMAX];
} idcount_t;
static idcount_t g_sent_ids, g_wire_ids;

static int idcount(idcount_t *c, int total, const ft_frame_t *(*at)(int),
                   int (*scan)(uint32_t), uint32_t id)
{
    if (total < c->read)
    {
        memset(c, 0, sizeof(*c));
    }
    for (; c->read < total; c->read++)
    {
        const uint32_t x = at(c->read)->id;
        int k = 0;
        while (k < c->n && c->id[k] != x) { k++; }
        if (k == c->n)
        {
            if (c->n == IDCOUNTMAX) { continue; }
            c->id[k] = x;
            c->count[k] = 0;
            c->n++;
        }
        c->count[k]++;
    }
    for (int k = 0; k < c->n; k++)
    {
        if (c->id[k] == id) { return c->count[k]; }
    }
    return (c->n == IDCOUNTMAX) ? scan(id) : 0;
}

int ft_sent_count_id(uint32_t id)
{
    return idcount(&g_sent_ids, twai_fake_sent_count(), twai_fake_sent,
                   twai_fake_sent_count_id, id);
}
int ft_wire_count_id(uint32_t id)
{
    return idcount(&g_wire_ids, twai_fake_wire_count(), twai_fake_wire,
                   twai_fake_wire_count_id, id);
}
int ft_max_inflight(void) { return twai_fake_max_inflight(); }
/* ESP_FAIL, the value the old fake refused with. */
void ft_refuse_next(int n) { twai_fake_refuse(TWAI_FAKE_ANY_ID, n, ESP_FAIL); }
void ft_refuse_id(uint32_t id, int n) { twai_fake_refuse(id, n, ESP_FAIL); }
void ft_fail_next(void) { twai_fake_fail(TWAI_FAKE_ANY_ID, 1); }
void ft_rx_error_next(int n) { twai_fake_rx_error_next(n); }

/* The register model (T8) is the fake's: twai_port's C3 file calls into it. */
int ft_ll_aborts(void)            { return twai_fake_ll_aborts(); }
int ft_ll_aborts_while_tx(void)   { return twai_fake_ll_aborts_while_tx(); }
int ft_ll_removed(void)           { return twai_fake_ll_removed(); }

/* ------------------------------------------------- the ping-pong scheduler */

static pthread_mutex_t g_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  g_cv = PTHREAD_COND_INITIALIZER;
static int  g_turn;                 /* 0 = test runs, 1 = worker runs */
/*
 * THE FURTHEST THE CLOCK MAY RUN INSIDE THE TURN THE TEST GRANTED.
 *
 * A blocked twai_receive() used to advance g_now to its OWN deadline --
 * 200 ms for the worker -- on any turn where no frame was waiting, so one
 * empty turn jumped virtual time 200 ms however little the test had given
 * it. Set by ft_run() on every hand-over; read only by the blocked receive.
 */
static int64_t g_turn_grant;
static bool g_worker_started, g_worker_stop;
static pthread_t g_worker;
static TaskFunction_t g_worker_fn;
static void *g_worker_arg;

static void yield_to_test(void)
{
    g_turn = 0;
    pthread_cond_broadcast(&g_cv);
    while (g_turn != 1 && !g_worker_stop)
    {
        pthread_cond_wait(&g_cv, &g_m);
    }
}

static void *worker_entry(void *arg)
{
    pthread_mutex_lock(&g_m);
    while (g_turn != 1) pthread_cond_wait(&g_cv, &g_m);
    pthread_mutex_unlock(&g_m);
    g_worker_fn(arg);
    return NULL;
}

/*
 * AUTO-START, for the auto-arm build only (spec 3.2, "arming comes after it"):
 * gen_inhibit_init() creates the worker and then waits in the auto-arm for it
 * to apply the arm, so the worker has to be running by then. Everywhere else a
 * case starts it with ft_start_worker() after init.
 */
static bool g_autostart_worker;
void ft_autostart_worker(bool on) { g_autostart_worker = on; }

int xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack,
                void *arg, uint32_t prio, TaskHandle_t *out)
{
    (void)name; (void)stack; (void)prio;
    g_worker_fn = fn;
    g_worker_arg = arg;
    if (out) *out = (TaskHandle_t)1;
    if (g_autostart_worker)
    {
        ft_start_worker();
    }
    return pdTRUE;
}

/*
 * DISTINCT HANDLES PER THREAD, and this was a real mock-fidelity bug that
 * case 10 caught.
 *
 * Returning one handle for both threads made the TEST thread look like the
 * worker, so gen_inhibit_quiesce()'s "called from the worker itself, return
 * immediately" guard fired on every call. The spec 8 handshake never ran, the
 * quiesce reason was never set, and case 10 failed -- reporting a firmware
 * defect that was actually a defect in the model. A mock that cannot tell two
 * tasks apart cannot test code whose correctness turns on which task is
 * calling.
 */
/*
 * WHICH OTHER TASK the test thread is. Spec 8.1 tells the OTA handler's own
 * teardown (row 4 handed it the bus) from anybody else's by the task, so a case
 * needs two non-worker identities: 2 is the default, and ft_as_task(3) makes
 * the test thread a different task until ft_as_task(0). Cleared by ft_reset().
 */
static int g_as_task;
void ft_as_task(int handle) { g_as_task = handle; }

TaskHandle_t xTaskGetCurrentTaskHandle(void)
{
    if (g_worker_started && pthread_equal(pthread_self(), g_worker))
    {
        return (TaskHandle_t)1;         /* the worker */
    }
    if (g_as_task > 2)
    {
        return (TaskHandle_t)(intptr_t)g_as_task;
    }
    return (TaskHandle_t)2;             /* anybody else */
}

void ft_start_worker(void)
{
    /*
     * Clear the STOP flag here, not in ft_reset(). A previous case's
     * ft_stop_worker() leaves it set, and a fresh worker that inherits it
     * exits at its first yield -- after which ft_run() waits for a thread that
     * is already gone and the whole suite hangs on case 2 with case 1 green.
     * That is exactly what happened the first time this ran.
     */
    pthread_mutex_lock(&g_m);
    g_worker_stop = false;
    g_turn = 0;
    pthread_mutex_unlock(&g_m);
    g_worker_started = true;
    pthread_create(&g_worker, NULL, worker_entry, g_worker_arg);
}

void ft_stop_worker(void)
{
    if (!g_worker_started) return;
    g_worker_started = false;
    pthread_mutex_lock(&g_m);
    g_worker_stop = true;
    g_turn = 1;
    pthread_cond_broadcast(&g_cv);
    pthread_mutex_unlock(&g_m);
    /*
     * NO pthread_cancel(), which hung the suite (2026-10-20, WSL gcc 9.4 /
     * glibc 2.31). Cancelled inside yield_to_test()'s pthread_cond_wait(),
     * the worker re-acquires g_m before it exits and nothing releases it, so
     * the next case's ft_start_worker() blocks on g_m for ever. Whether the
     * cancel lands there or after the wake is scheduling, which is why the
     * hang moved between cases from run to run. The cancel is not needed:
     * the worker is always parked in yield_to_test(), whose callers
     * (twai_receive(), vTaskDelay()) pthread_exit() on g_worker_stop.
     * The cancel was also the only way out of vTaskDelay()'s no-worker
     * branch, which a stopping worker can reach once g_worker_started is
     * clear; that branch now checks g_worker_stop as well.
     */
    pthread_join(g_worker, NULL);
}

/*
 * Give the worker the CPU until it blocks again, letting virtual time run
 * forward by at most `us`. The worker blocks in twai_receive() or vTaskDelay();
 * both of those advance the clock themselves and then yield, so one call here
 * is one pass round the worker loop.
 */
/*
 * HOLD THE WORKER OFF: while set, ft_run() lets virtual time pass without giving
 * the worker a turn, as a worker that cannot get the CPU would look to everyone
 * else. For spec 3's 1 s bound on a mode change: a request the worker does not
 * take within the second must be withdrawn and never applied.
 */
static bool g_hold_worker;
void ft_hold_worker(bool hold) { g_hold_worker = hold; }

int64_t ft_run(int64_t us)
{
    const int64_t deadline = g_now + us;
    if (g_hold_worker)
    {
        if (g_now < deadline) g_now = deadline;
        twai_fake_poll();
        step();
        return g_now;
    }
    /*
     * Record the grant before the worker can look at it. A blocked receive may
     * advance the clock no further than this; see g_turn_grant.
     */
    g_turn_grant = deadline;
    pthread_mutex_lock(&g_m);
    g_turn = 1;
    pthread_cond_broadcast(&g_cv);
    while (g_turn != 0)
    {
        pthread_cond_wait(&g_cv, &g_m);
    }
    pthread_mutex_unlock(&g_m);
    /*
     * `us` is how much virtual time PASSES, not a ceiling on it.
     *
     * The first version capped downward and never advanced, so when a frame
     * was always waiting the worker never blocked, g_now never moved, and no
     * queued frame ever completed -- every inhibit was still outstanding when
     * the next 0x051 arrived and case 3 reported a false TX_LATE. The failure
     * looked exactly like the firmware bug the case was written to catch,
     * which is the trap: a model that is wrong in the same direction as the
     * defect will "confirm" it.
     *
     * If the worker blocked on a timeout it may already be past the deadline;
     * time never runs backwards.
     */
    if (g_now < deadline) g_now = deadline;
    twai_fake_poll();
    step();
    return g_now;
}

/* ---------------------------------------------- the fake, wired to this -- */

/*
 * A BLOCKED twai_receive() IS A TURN BACK TO THE TEST: the fake's wait hook,
 * called while the worker waits for a frame. A real blocked receive stays in
 * the driver until a frame arrives or the timeout expires, and nothing a
 * lower-priority task does shortens that; the fake keeps the worker inside
 * the call for that whole span (twai_fake_in_receive()), which is what makes
 * ft_unsafe_teardowns() meaningful. With twai_fake_set_receive_yields_first()
 * the worker takes one frame per turn the test grants, waiting or not.
 *
 * Each call first lets the clock reach the receive's deadline -- BUT NO
 * FURTHER THAN THE TURN THE TEST GRANTED. An empty turn that jumped to the
 * receive's own deadline (GEN_INHIBIT_RX_TIMEOUT_MS, 200 ms) would put a
 * replay's next ~470 frames in the past and deliver them as one clump; that
 * tripped 7 on skips the truck cannot produce, until 2026-10. The timeout
 * still happens: granted time accumulates turn by turn until the deadline.
 * The advance comes before the yield so a frame whose air time elapsed during
 * the block is complete when the test next looks, as ft_run() would make it.
 *
 * Only the worker blocks. From any other thread the fake's wait gives up at
 * once, as a zero wait does.
 */
static bool on_worker(void)
{
    return g_worker_started && pthread_equal(pthread_self(), g_worker);
}

static bool wait_turn(int64_t deadline_us, void *ctx)
{
    (void)ctx;
    if (!on_worker())
    {
        return false;
    }
    {
        const int64_t to = (deadline_us < g_turn_grant) ? deadline_us : g_turn_grant;
        if (to > g_now)
        {
            g_now = to;
            twai_fake_poll();
            step();
        }
    }
    pthread_mutex_lock(&g_m);
    yield_to_test();
    pthread_mutex_unlock(&g_m);
    if (g_worker_stop)
    {
        pthread_exit(NULL);
    }
    return true;
}

/* The wire invariant, on every call and on every frame the driver accepted. */
static void on_transmit(const twai_message_t *m, void *ctx)
{
    (void)ctx;
    check_wire(m);
}

static void on_accepted(const twai_message_t *m, void *ctx)
{
    (void)ctx;
    check_wire_accepted(m);
}

/*
 * The controller's cumulative error-frame count (spec 7 trip 6), as a target
 * this harness tops the driver's count up to. The fake restarts the count at
 * every install, as the driver does (its object is calloc'd), so the target is
 * reapplied after each one; and a case can stage it before setup(), which is
 * why ft_reset() keeps it, as it keeps the reset reason. The fake can only add
 * bus errors, each with its BUS_ERROR alert, so a target below a live
 * driver's count is a case asking for the impossible, and stops the run.
 */
static uint32_t g_bus_errors_target;

static void apply_bus_errors(void)
{
    twai_status_info_t st;
    if (!twai_fake_installed())
    {
        return;
    }
    twai_fake_driver_status(&st);
    if (g_bus_errors_target > st.bus_error_count)
    {
        twai_fake_bus_error(g_bus_errors_target - st.bus_error_count);
    }
    else if (g_bus_errors_target < st.bus_error_count)
    {
        printf("HARNESS: ft_set_bus_errors(%u) with the installed driver at %u; "
               "the fake's count only rises. Stage it before the install.\n",
               (unsigned)g_bus_errors_target, (unsigned)st.bus_error_count);
        abort();
    }
}

void ft_set_bus_errors(uint32_t n)
{
    g_bus_errors_target = n;
    apply_bus_errors();
}

/*
 * WHICH THREAD INSTALLED THE DRIVER (spec 3, "The worker makes every mode
 * change": on c6fdab5 an arm installed it from the caller's task). Seen at the
 * call itself, through the linker's --wrap (Makefile), because the fake's
 * install log has times and modes but no threads, and the thread is the
 * harness's scheduler's to know.
 */
esp_err_t __real_twai_driver_install(const twai_general_config_t *g,
                                     const twai_timing_config_t *t,
                                     const twai_filter_config_t *f);

esp_err_t __wrap_twai_driver_install(const twai_general_config_t *g,
                                     const twai_timing_config_t *t,
                                     const twai_filter_config_t *f)
{
    const esp_err_t err = __real_twai_driver_install(g, t, f);
    if (err == ESP_OK)
    {
        if (!on_worker())
        {
            g_install_off_worker++;
        }
        apply_bus_errors();
    }
    return err;
}

bool ft_foreign_transmit(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    /*
     * Not checked against the wire invariant (the fake calls neither hook for
     * it): this models a transmit from ANOTHER task through can_send(), which
     * is another component's frame. Since 2026-10-16 spec 3.2 disables every
     * other caller that changes bus state, but can_send()'s refusal while the
     * inhibitor owns the bus still stands, and this is what a case needs to
     * show that refusal at work or what it costs when it is missing.
     */
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = id;
    m.data_length_code = dlc > 8 ? 8 : dlc;
    memcpy(m.data, data, m.data_length_code);
    return twai_fake_foreign_transmit(&m) == ESP_OK;
}

void ft_deliver(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    deliv_note(id);
    twai_fake_deliver_std(id, data, dlc);
}

/*
 * WHICH THREAD IS DELAYING MATTERS, and getting it wrong deadlocks instantly.
 *
 * From the WORKER this is a blocking call: advance time and hand control back
 * to the test, exactly as twai_receive() does.
 *
 * From the TEST thread it is not. gen_inhibit_quiesce() delays in a loop
 * waiting for the worker to confirm it is parked -- that is the spec 8
 * handshake -- and it is called from whichever task is tearing the bus down,
 * which here is the test. If this yielded there it would wait for a worker
 * that nothing can schedule. So from the test it means "let the worker run for
 * this long", which is what a real preemptive scheduler would do.
 */
void vTaskDelay(TickType_t ticks)
{
    /*
     * NO WORKER YET, so this is the test thread and there is nobody to yield to:
     * time passes and that is all. Since 2026-10-16 a mode change waits for the
     * worker (spec 3); yielding here would wait for a thread that does not exist.
     *
     * A STOPPED WORKER CAN ALSO LAND HERE: ft_stop_worker() clears
     * g_worker_started before the worker has left. Without the check it would
     * spin in the OFF branch's sleep for ever, and since 2026-10-20 there is
     * no pthread_cancel() behind it (see ft_stop_worker()), so g_worker_stop
     * has to be the way out on this branch too.
     */
    if (!g_worker_started)
    {
        if (g_worker_stop && pthread_equal(pthread_self(), g_worker))
        {
            pthread_exit(NULL);
        }
        g_now += (int64_t)ticks * 1000;
        twai_fake_poll();
        return;
    }
    if (g_worker_started && !pthread_equal(pthread_self(), g_worker))
    {
        ft_run((int64_t)ticks * 1000);
        return;
    }
    g_now += (int64_t)ticks * 1000;
    twai_fake_poll();
    pthread_mutex_lock(&g_m);
    yield_to_test();
    pthread_mutex_unlock(&g_m);
    if (g_worker_stop) pthread_exit(NULL);
}

/*
 * TASK NOTIFICATIONS, for spec 3's wake on a mode change (user, 2026-10-10).
 *
 * WHAT THIS MODELS, AND IT IS LESS THAN THE FEATURE. The property the firmware
 * depends on is that a notification LATCHES: gen_inhibit_set_mode() may run
 * before the worker has executed a single instruction -- it does on an
 * auto-arm boot -- and the wake must still end the worker's first sleep. That
 * is modelled: a pending notification makes the take return at once, with no
 * virtual time passing at all.
 *
 * WHAT IT DOES NOT MODEL: a notification arriving PART WAY THROUGH a sleep and
 * cutting it short. It cannot be modelled here and it must not be claimed.
 * This harness single-steps -- the worker runs only when the test yields to it
 * -- so the test thread can only call gen_inhibit_set_mode() while the worker
 * is already parked at the yield inside its sleep, after vTaskDelay has
 * advanced the clock by the whole interval. And ft_run() then drags the clock
 * to its own deadline regardless. The shortened sleep has no observable
 * consequence in this model, and no shim case may be read as evidence for it:
 * on the device it is the difference between going live behind 120 queued
 * frames and going live behind none. What covers it is the bench, replaying a
 * full bus across every entry path into INHIBIT.
 *
 * So the counters below exist to check the one thing that IS checkable: that
 * the wake was sent, and sent to the worker.
 */
static uint32_t g_notify;               /* pending notifications for the worker */
static int      g_wakes_sent;
static int      g_wakes_misdirected;    /* sent to something that is not the worker */
static int      g_wakes_taken_pending;  /* takes that returned on a latched wake */

int ft_wakes_sent(void)         { return g_wakes_sent; }
int ft_wakes_misdirected(void)  { return g_wakes_misdirected; }
int ft_wakes_taken_pending(void) { return g_wakes_taken_pending; }

int xTaskNotifyGive(TaskHandle_t task)
{
    g_wakes_sent++;
    /*
     * The handle the firmware holds must be the WORKER's. xTaskCreate() hands
     * out (TaskHandle_t)1 for it, and xTaskGetCurrentTaskHandle() answers 2
     * for anyone else -- so a firmware that captured the wrong handle (the
     * caller's, say, or NULL) is caught here rather than silently waking
     * nobody.
     */
    if (task != (TaskHandle_t)1)
    {
        g_wakes_misdirected++;
        return pdTRUE;
    }
    g_notify++;
    return pdTRUE;
}

uint32_t ulTaskNotifyTake(int clear_on_exit, TickType_t ticks)
{
    if (g_notify != 0)
    {
        /*
         * THE LATCHED CASE, and no time passes. This is the half of spec 3
         * that says the wake must survive arriving before the worker blocks.
         */
        const uint32_t v = g_notify;
        g_notify = clear_on_exit ? 0 : g_notify - 1;
        g_wakes_taken_pending++;
        return v;
    }
    vTaskDelay(ticks);
    if (g_notify != 0)
    {
        const uint32_t v = g_notify;
        g_notify = clear_on_exit ? 0 : g_notify - 1;
        return v;
    }
    return 0;
}

const char *esp_err_to_name(esp_err_t e)
{
    static char buf[32];
    switch (e)
    {
    case ESP_OK:                 return "ESP_OK";
    case ESP_FAIL:               return "ESP_FAIL";
    case ESP_ERR_TIMEOUT:        return "ESP_ERR_TIMEOUT";
    case ESP_ERR_INVALID_STATE:  return "ESP_ERR_INVALID_STATE";
    case ESP_ERR_INVALID_ARG:    return "ESP_ERR_INVALID_ARG";
    case ESP_ERR_NOT_FINISHED:   return "ESP_ERR_NOT_FINISHED";
    case ESP_ERR_NOT_ALLOWED:    return "ESP_ERR_NOT_ALLOWED";
    default: break;
    }
    snprintf(buf, sizeof(buf), "err 0x%x", (unsigned)e);
    return buf;
}

void ft_reset(void)
{
    g_now = 0;
    g_turn_grant = 0;
    g_nlog = 0;
    g_air_time_set = 0;
    g_stalls_armed = 0;
    g_n_air_id_ever = 0;
    g_air_time = SHIM_AIR_TIME_US;
    g_install_off_worker = 0;
    g_hold_worker = false;
    g_as_task = 0;
    /*
     * Cleared here, unlike the reset reason and the error count: nothing sets
     * a notification before setup(), and a wake left over from the previous
     * case would shorten this one's first sleep.
     */
    g_notify = 0;
    g_wakes_sent = g_wakes_misdirected = g_wakes_taken_pending = 0;
    g_ndeliv = 0;
    memset(&g_sent_ids, 0, sizeof(g_sent_ids));
    memset(&g_wire_ids, 0, sizeof(g_wire_ids));
    g_nwv = 0;
    g_nallow = 0;
    g_allow_why[0] = 0;

    /*
     * THE FAKE, back to power-on and wired to this harness: its clock is
     * g_now, a blocked call is a turn back to the test, one frame per worker
     * turn, the wire invariant on every transmit, and 200 us of air time.
     * Ticks are 1 ms, as the mock's pdMS_TO_TICKS has them. Recovery is never
     * started on port 0 (spec 7), so the fake's automatic recovery is moot.
     */
    twai_fake_reset();
    twai_fake_set_clock(clock_now, NULL);
    twai_fake_set_tick_us(1000);
    twai_fake_set_wait_hook(wait_turn, NULL);
    twai_fake_set_receive_yields_first(true);
    twai_fake_set_tx_hook(on_transmit, NULL);
    twai_fake_set_tx_accepted_hook(on_accepted, NULL);
    twai_fake_set_air_time_us(SHIM_AIR_TIME_US);
}
