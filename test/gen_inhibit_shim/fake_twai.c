/*
 * fake_twai -- see fake_twai.h for what this models and why.
 *
 * THE SCHEDULER. The worker runs on its own pthread because gen_inhibit.c's
 * worker is an infinite loop that cannot be called a step at a time. But the
 * two threads never run concurrently: every point where the worker would block
 * on the real device -- twai_receive() and vTaskDelay() -- hands control to
 * the test and waits to be woken. So exactly one thread is runnable at a time,
 * virtual time only moves when the test moves it, and two runs of the same
 * script produce the same trace.
 *
 * That is deliberate and it is also a limitation worth stating: this cannot
 * find a RACE. It finds attribution and sequencing bugs, which is what the
 * four shim defects of 2026-09-25 were. A race between the worker and the
 * httpd task is out of reach here and belongs on hardware.
 */
#include "fake_twai.h"

#include "driver/twai.h"
#include "esp_log.h"
#include "gen_inhibit_core.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

/*
 * A RECEIVE QUEUE, not a single slot.
 *
 * The slot was enough while only the test delivered frames, one at a time.
 * Honouring self-reception breaks that: a self frame completing while a
 * test-delivered frame is still pending would overwrite it, and the model
 * would then be wrong in the SAME DIRECTION as the defect -- swallowing
 * exactly the evidence of the loop it exists to expose. That failure has
 * already happened four times in this suite (see README, "the model wrong in
 * the same direction as the defect"), so it gets a queue.
 *
 * Depth 10 matches the driver config the firmware installs with. Overflow is
 * counted rather than ignored, because a full RX queue on the device drops
 * frames and that is a real behaviour, not a modelling artefact.
 */
#define RXMAX 10
static twai_message_t g_rx_q[RXMAX];
static int            g_rx_n;
static int            g_rx_dropped;
static int            g_self_received;
static int64_t        g_last_rx_time = -1;

#define DELIVMAX 16
static struct { uint32_t id; int n; } g_deliv[DELIVMAX];
static int g_ndeliv;

int ft_self_received(void) { return g_self_received; }
int64_t ft_last_rx_time(void) { return g_last_rx_time; }

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

static void rx_push(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    if (g_rx_n >= RXMAX) { g_rx_dropped++; return; }
    twai_message_t *m = &g_rx_q[g_rx_n++];
    memset(m, 0, sizeof(*m));
    m->identifier = id;
    m->data_length_code = dlc;
    memcpy(m->data, data, dlc > 8 ? 8 : dlc);
}

static bool rx_pop(twai_message_t *out)
{
    if (g_rx_n <= 0) return false;
    *out = g_rx_q[0];
    memmove(&g_rx_q[0], &g_rx_q[1], (size_t)(g_rx_n - 1) * sizeof(g_rx_q[0]));
    g_rx_n--;
    return true;
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

/* ------------------------------------------------------------- the FIFO -- */

#define QMAX   16
#define SENTMAX 4096

static ft_frame_t g_q[QMAX];        /* queued, not yet complete: FIFO */
static int        g_qn;

static ft_frame_t g_sent[SENTMAX];  /* every accepted queue attempt */
static int        g_nsent;
static ft_frame_t g_wire[SENTMAX];  /* every completion, in wire order */
static int        g_nwire;

static uint32_t g_alerts;           /* LATCHED, shared by every frame */
static uint32_t g_alerts_enabled;
static int64_t  g_air_time = 200;
static int64_t  g_head_started = -1;
static int64_t  g_last_done;   /* when the previous frame left the head */
static int      g_stall_n, g_refuse_n, g_rx_err_n;
static uint32_t g_stall_id;
static int      g_stall_id_n;
static uint32_t g_refuse_id;
static int      g_refuse_id_n;
static int      g_max_inflight;     /* item 3's high-water mark, ours only */
static bool     g_fail_next;
static bool     g_installed, g_running;
static bool     g_in_receive;       /* a thread is blocked inside the driver */
static int      g_unsafe_teardowns;
static int      g_installed_mode = -1;
static bool     g_fail_alerts_cfg;

/*
 * THE REST OF THE INSTALL CONFIG, recorded because spec 5.1 items 1 and 2 are
 * both statements about what twai_driver_install() was handed and nothing was
 * keeping it. The driver exposes no read-back for any of these, so the value
 * passed at install is the only thing any test can ever check -- the same
 * argument the mode above is recorded under.
 *
 * Until 2026-09-26 this function took `t` and `f` and threw them away, which is
 * why the reviewing session's Q1, Q2 and F1 mutations all survived E1: the queue
 * depth and the acceptance filter simply were not observable here.
 */
static int      g_install_n;
static uint32_t g_installed_rxq;
static uint32_t g_installed_acc_code, g_installed_acc_mask;
static int      g_installed_single_filter = -1;

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

int  ft_unsafe_teardowns(void) { return g_unsafe_teardowns; }
int  ft_installed_mode(void)   { return g_installed_mode; }
int      ft_install_count(void)          { return g_install_n; }
uint32_t ft_installed_rx_queue_len(void) { return g_installed_rxq; }
uint32_t ft_installed_acc_code(void)     { return g_installed_acc_code; }
uint32_t ft_installed_acc_mask(void)     { return g_installed_acc_mask; }
int      ft_installed_single_filter(void) { return g_installed_single_filter; }
void ft_fail_alerts_config(bool fail) { g_fail_alerts_cfg = fail; }

int              ft_sent_count(void) { return g_nsent; }
const ft_frame_t *ft_sent(int i) { return &g_sent[i]; }
int              ft_wire_count(void) { return g_nwire; }
const ft_frame_t *ft_wire(int i) { return &g_wire[i]; }

static void (*g_step_hook)(void);
void ft_set_step_hook(void (*fn)(void)) { g_step_hook = fn; }

/* Every step boundary, so an always-property is checked as one. */
static void step(void)
{
    if (g_step_hook) g_step_hook();
}

void ft_set_air_time(int64_t us) { g_air_time = us; }
void ft_stall_next(int n) { g_stall_n = n; }
void ft_stall_id(uint32_t id, int n) { g_stall_id = id; g_stall_id_n = n; }

#define AIRMAX 8
static struct { uint32_t id; int64_t us; } g_air_id[AIRMAX];
static int g_n_air_id;

void ft_set_air_time_id(uint32_t id, int64_t us)
{
    for (int i = 0; i < g_n_air_id; i++)
    {
        if (g_air_id[i].id == id) { g_air_id[i].us = us; return; }
    }
    if (g_n_air_id < AIRMAX)
    {
        g_air_id[g_n_air_id].id = id;
        g_air_id[g_n_air_id].us = us;
        g_n_air_id++;
    }
}

static int64_t air_time_for(uint32_t id)
{
    for (int i = 0; i < g_n_air_id; i++)
    {
        if (g_air_id[i].id == id) return g_air_id[i].us;
    }
    return g_air_time;
}

int ft_sent_count_id(uint32_t id)
{
    int n = 0;
    for (int i = 0; i < g_nsent; i++) if (g_sent[i].id == id) n++;
    return n;
}

int ft_wire_count_id(uint32_t id)
{
    int n = 0;
    for (int i = 0; i < g_nwire; i++) if (g_wire[i].id == id) n++;
    return n;
}
int ft_max_inflight(void) { return g_max_inflight; }
void ft_refuse_next(int n) { g_refuse_n = n; }
void ft_refuse_id(uint32_t id, int n) { g_refuse_id = id; g_refuse_id_n = n; }
void ft_fail_next(void) { g_fail_next = true; }
void ft_rx_error_next(int n) { g_rx_err_n = n; }

/* ------------------------------------- the private HAL the scheduler uses -- */

#include "hal/twai_ll.h"

static int g_ll_aborts;         /* abort commands issued */
static int g_ll_aborts_while_tx;/* ... while the head was transmitting */
static int g_ll_removed;        /* frames the abort took out of the queue */

/*
 * The status register, DERIVED FROM THE QUEUE and not tracked alongside it. Two
 * sources of truth for "is the buffer busy" is how a model starts disagreeing with
 * itself, and this one already answers that question through msgs_to_tx.
 *
 *   empty queue            TBS set              -- nothing to abort
 *   head stalled           TBS clear, TS clear  -- awaiting arbitration
 *   head not stalled       TBS clear, TS set    -- transmitting
 *
 * A stalled frame is fake_twai's model of a frame that will not get out, which is
 * what a busy bus looks like from here, so it IS the awaiting case.
 */
uint32_t twai_ll_get_status(twai_dev_t *hw)
{
    uint32_t st = 0;
    (void)hw;
    if (g_qn == 0)
    {
        return TWAI_LL_STATUS_TBS;
    }
    if (g_q[0].t_done != -2)
    {
        st |= TWAI_LL_STATUS_TS;
    }
    return st;
}

/*
 * The abort, as measured on the C3 (2026-09-27, artifacts/gen-inhibit/runs/
 * txabort_full.json):
 *
 *   awaiting     removed, NEVER on the wire -- and TX_SUCCESS is raised anyway,
 *                which is the whole reason spec 5.2 item 5 exists.
 *   transmitting a no-op. Counted, so a case can assert the scheduler did not
 *                issue one here: item 4 says it waits for TS to fall instead.
 *   empty        nothing, and no alert.
 */
void twai_ll_set_cmd_abort_tx(twai_dev_t *hw)
{
    (void)hw;
    g_ll_aborts++;
    if (g_qn == 0)
    {
        return;
    }
    if (g_q[0].t_done != -2)
    {
        g_ll_aborts_while_tx++;
        return;
    }
    /*
     * Remove the head. The wire log is deliberately NOT touched: the frame never
     * reached the bus, and the only thing the device can see is that msgs_to_tx
     * fell -- indistinguishable from a real completion.
     */
    for (int i = 1; i < g_qn; i++)
    {
        g_q[i - 1] = g_q[i];
    }
    g_qn--;
    g_head_started = -1;
    g_ll_removed++;
    g_alerts |= TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE;
}

int ft_ll_aborts(void)            { return g_ll_aborts; }
int ft_ll_aborts_while_tx(void)   { return g_ll_aborts_while_tx; }
int ft_ll_removed(void)           { return g_ll_removed; }

/*
 * Advance the controller to `t`: complete whatever is at the head of the queue
 * if its air time has elapsed, and start the next one. A stalled frame never
 * starts its clock, so it sits at the head and msgs_to_tx never falls -- which
 * is exactly what a busy bus looks like to the shim.
 */
static void controller_advance(int64_t t)
{
    while (g_qn > 0)
    {
        if (g_q[0].t_done == -2)        /* stalled: never completes */
        {
            return;
        }
        if (g_head_started < 0)
        {
            /*
             * WHEN THE FRAME REACHED THE HEAD, which is a property of the
             * queue and not of when this function happens to be called. An
             * empty queue starts transmitting at the instant the frame is
             * queued; otherwise it starts when the frame ahead completed.
             *
             * The first version used max(t_queued, g_now), and since the
             * caller advances g_now to the deadline BEFORE calling here, the
             * head's clock restarted on every call and no frame ever
             * completed. Every inhibit then looked outstanding when the next
             * 0x051 arrived -- a false TX_LATE, which is indistinguishable
             * from the firmware bug case 3 exists to catch.
             */
            g_head_started = g_q[0].t_queued > g_last_done
                           ? g_q[0].t_queued : g_last_done;
        }
        if (t < g_head_started + air_time_for(g_q[0].id))
        {
            return;
        }
        ft_frame_t done = g_q[0];
        done.t_done = g_head_started + air_time_for(g_q[0].id);
        if (done.failed)
        {
            g_alerts |= TWAI_ALERT_TX_FAILED;
        }
        else
        {
            g_alerts |= TWAI_ALERT_TX_SUCCESS;
        }
        g_last_done = done.t_done;
        if (g_nwire < SENTMAX) g_wire[g_nwire++] = done;

        /*
         * HONOUR self, so the loop shows up as behaviour and not only as a
         * flag the invariant objects to.
         *
         * A flag check alone would prove the firmware set a bit it should not
         * have. Delivering the frame back proves what that bit COSTS: the
         * worker receives its own 0x051, the core cannot distinguish it from
         * the VCM's, and it responds -- which queues another self frame, which
         * is received, which is responded to. The case watching ft_sent_count()
         * sees it run away. That is the difference between "this flag is wrong"
         * and "this is what happens on the truck".
         */
        if (!done.failed && done.self)
        {
            rx_push(done.id, done.data, done.dlc);
            g_self_received++;
        }
        memmove(&g_q[0], &g_q[1], (size_t)(g_qn - 1) * sizeof(g_q[0]));
        g_qn--;
        g_head_started = -1;
    }
}

/* ------------------------------------------------- the ping-pong scheduler */

static pthread_mutex_t g_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  g_cv = PTHREAD_COND_INITIALIZER;
static int  g_turn;                 /* 0 = test runs, 1 = worker runs */
static bool g_worker_started, g_worker_stop;
static pthread_t g_worker;
static TaskFunction_t g_worker_fn;
static void *g_worker_arg;

/* Rx mailbox: at most one frame waiting, posted by the test. */



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

int xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack,
                void *arg, uint32_t prio, TaskHandle_t *out)
{
    (void)name; (void)stack; (void)prio;
    g_worker_fn = fn;
    g_worker_arg = arg;
    if (out) *out = (TaskHandle_t)1;
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
TaskHandle_t xTaskGetCurrentTaskHandle(void)
{
    if (g_worker_started && pthread_equal(pthread_self(), g_worker))
    {
        return (TaskHandle_t)1;         /* the worker */
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
    pthread_cancel(g_worker);
    pthread_join(g_worker, NULL);
}

/*
 * Give the worker the CPU until it blocks again, letting virtual time run
 * forward by at most `us`. The worker blocks in twai_receive() or vTaskDelay();
 * both of those advance the clock themselves and then yield, so one call here
 * is one pass round the worker loop.
 */
int64_t ft_run(int64_t us)
{
    const int64_t deadline = g_now + us;
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
    controller_advance(g_now);
    step();
    return g_now;
}

/* ------------------------------------------------------------ the driver -- */

esp_err_t twai_driver_install(const twai_general_config_t *g,
                              const twai_timing_config_t *t,
                              const twai_filter_config_t *f)
{
    (void)t;
    /*
     * Record the MODE. Spec 3 requires OBSERVE to be hardware listen-only, and
     * the TWAI API exposes no read-back of the controller's mode -- so the only
     * thing that can be checked anywhere is the value handed to install, which
     * is exactly what gen_inhibit.c's own comment says it is confirming.
     */
    g_installed_mode = g ? (int)g->mode : -1;

    /* Spec 5.1 item 1 (queue depth) and item 2 (accept-all): same argument. */
    g_installed_rxq = g ? g->rx_queue_len : 0;
    g_installed_acc_code = f ? f->acceptance_code : 0;
    g_installed_acc_mask = f ? f->acceptance_mask : 0;
    g_installed_single_filter = f ? (int)f->single_filter : -1;

    g_install_n++;
    g_installed = true;
    return ESP_OK;
}

esp_err_t twai_driver_uninstall(void)
{
    /*
     * Spec 8. On the device this frees the memory a blocked twai_receive() is
     * using. The model cannot crash, so it counts instead -- see
     * ft_unsafe_teardowns().
     */
    if (g_in_receive) g_unsafe_teardowns++;
    g_installed = false;
    g_running = false;
    g_installed_mode = -1;
    return ESP_OK;
}

esp_err_t twai_start(void) { g_running = true; return ESP_OK; }
esp_err_t twai_stop(void)
{
    if (g_in_receive) g_unsafe_teardowns++;
    g_running = false;
    return ESP_OK;
}
esp_err_t twai_clear_receive_queue(void) { g_rx_n = 0; return ESP_OK; }

esp_err_t twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev)
{
    if (g_fail_alerts_cfg)
    {
        /*
         * A driver that will not enable the alerts. Without them every frame
         * looks outstanding for ever and aborts TX_LATE on the first VCM frame,
         * so spec 7 trip 7 requires the arm to be refused rather than attempted
         * blind.
         */
        return ESP_ERR_INVALID_STATE;
    }
    if (prev) *prev = g_alerts_enabled;
    g_alerts_enabled = alerts;
    return ESP_OK;
}

esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks)
{
    (void)ticks;
    const uint32_t v = g_alerts & g_alerts_enabled;
    if (v == 0)
    {
        return ESP_ERR_TIMEOUT;     /* nothing pending; not an error */
    }
    *alerts = v;
    g_alerts &= ~v;                 /* reading CLEARS, as the real one does */
    return ESP_OK;
}

esp_err_t twai_get_status_info(twai_status_info_t *status)
{
    memset(status, 0, sizeof(*status));
    status->state = g_running ? TWAI_STATE_RUNNING : TWAI_STATE_STOPPED;
    status->msgs_to_tx = (uint32_t)g_qn;
    status->msgs_to_rx = (uint32_t)g_rx_n;
    return ESP_OK;
}

static bool enqueue(uint32_t id, const uint8_t *data, uint8_t dlc, bool foreign,
                    const twai_message_t *m)
{
    if (g_qn >= QMAX) return false;
    ft_frame_t *f = &g_q[g_qn++];
    memset(f, 0, sizeof(*f));
    f->t_queued = g_now;
    f->t_done = -1;
    f->id = id;
    f->dlc = dlc;
    memcpy(f->data, data, dlc > 8 ? 8 : dlc);
    f->foreign = foreign;
    if (m != NULL)
    {
        f->extd = m->extd;
        f->self = m->self;
        f->rtr  = m->rtr;
        f->ss   = m->ss;
    }
    if (g_stall_n > 0) { g_stall_n--; f->t_done = -2; }
    if (g_stall_id_n > 0 && id == g_stall_id) { g_stall_id_n--; f->t_done = -2; }
    if (g_fail_next)   { g_fail_next = false; f->failed = true; }
    if (g_nsent < SENTMAX) g_sent[g_nsent++] = *f;

    /*
     * ITEM 3'S HIGH-WATER MARK, taken here because this is the only place a frame
     * enters the driver. Counted rather than inferred from g_qn so that a foreign
     * transmit sitting in the queue does not read as the scheduler having
     * double-submitted.
     */
    {
        int ours = 0;
        for (int i = 0; i < g_qn; i++) { if (!g_q[i].foreign) { ours++; } }
        if (ours > g_max_inflight) { g_max_inflight = ours; }
    }
    return true;
}

esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks)
{
    (void)ticks;

    /*
     * BEFORE the running check, deliberately. A frame the firmware tried to
     * send with extd set is wrong whether or not the driver happened to accept
     * it, and putting this after the early returns would let a case that
     * exercises the refusal path skip the invariant entirely.
     */
    check_wire(message);

    if (!g_running) return ESP_ERR_INVALID_STATE;

    /*
     * LISTEN-ONLY REFUSES TO TRANSMIT, which the model has to implement rather
     * than assume, because OBSERVE's silence rests on nothing else.
     *
     * ESP-IDF: in TWAI_MODE_LISTEN_ONLY the controller "will not influence the
     * bus", and transmission is not permitted. gen_inhibit.c builds a diag page
     * in OBSERVE anyway and lets the call fail -- the comment at
     * gen_inhibit.c:495 names that as the deliberate cost of a passive tap.
     *
     * Until this existed the fake enqueued whatever it was handed, and case 11
     * passed because the driver happened not to be running at the instant the
     * diag page was built. A silence produced by an accident of timing is not
     * the silence the firmware relies on, and it would have gone on passing if
     * the real mechanism were taken out.
     */
    if (g_installed_mode == TWAI_MODE_LISTEN_ONLY)
    {
        return ESP_ERR_NOT_SUPPORTED;
    }

    if (g_refuse_n > 0) { g_refuse_n--; return ESP_FAIL; }
    if (g_refuse_id_n > 0 && message->identifier == g_refuse_id)
    {
        g_refuse_id_n--;
        return ESP_FAIL;
    }
    if (!enqueue(message->identifier, message->data,
                 message->data_length_code, false, message))
    {
        return ESP_FAIL;
    }
    check_wire_accepted(message);
    return ESP_OK;
}

bool ft_foreign_transmit(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    /*
     * Not checked against the wire invariant: this models the SLCAN and MQTT
     * paths, which are another component's frames. Spec 3.2's objection to
     * them is that they exist at all while the inhibitor owns the bus, not
     * that they are malformed.
     */
    return enqueue(id, data, dlc, true, NULL);
}

void ft_deliver(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    deliv_note(id);
    rx_push(id, data, dlc);
}

esp_err_t twai_receive(twai_message_t *message, TickType_t ticks)
{
    /*
     * A REAL BLOCKED RECEIVE, and the first version was not one.
     *
     * It yielded once, then returned as soon as it got the CPU back -- so the
     * worker left the driver on any test step. On the device it stays inside
     * twai_receive() until a frame arrives or the timeout expires, and nothing
     * a lower-priority task does shortens that: it is blocked on the driver's
     * queue, not on a delay.
     *
     * That difference hid a whole class of bug. Removing the spec 8 quiesce
     * wait left the suite green, because can_disable()'s own short vTaskDelay
     * let the model's worker slip out of the driver and park, so the teardown
     * never saw anyone inside. The real worker would still have been blocked
     * there, which is precisely the use-after-free the handshake prevents.
     *
     * So: stay in, yielding repeatedly, until a frame is posted or the deadline
     * passes. g_in_receive is true for that whole span, which is what makes
     * ft_unsafe_teardowns() meaningful.
     */
    const int64_t deadline = g_now + (int64_t)ticks * 1000;

    for (;;)
    {
        g_in_receive = true;
        pthread_mutex_lock(&g_m);
        yield_to_test();
        pthread_mutex_unlock(&g_m);
        if (g_worker_stop) { g_in_receive = false; pthread_exit(NULL); }

        if (!g_installed)
        {
            /*
             * The driver went away underneath us. On the device this is the
             * ESP_ERR_INVALID_STATE the worker's error path is written for.
             */
            g_in_receive = false;
            return ESP_ERR_INVALID_STATE;
        }
        if (g_rx_err_n > 0)
        {
            g_rx_err_n--;
            g_in_receive = false;
            return ESP_FAIL;
        }
        if (rx_pop(message))
        {
            g_last_rx_time = g_now;
            g_in_receive = false;
            return ESP_OK;
        }
        if (g_now >= deadline)
        {
            g_in_receive = false;
            controller_advance(g_now);
            step();
            return ESP_ERR_TIMEOUT;
        }
        /* Still blocked. Let the clock reach the deadline if nothing else does. */
        if (g_now < deadline)
        {
            g_now = deadline;
            controller_advance(g_now);
            step();
        }
    }
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
    if (g_worker_started && !pthread_equal(pthread_self(), g_worker))
    {
        ft_run((int64_t)ticks * 1000);
        return;
    }
    g_now += (int64_t)ticks * 1000;
    controller_advance(g_now);
    pthread_mutex_lock(&g_m);
    yield_to_test();
    pthread_mutex_unlock(&g_m);
    if (g_worker_stop) pthread_exit(NULL);
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
    default: break;
    }
    snprintf(buf, sizeof(buf), "err 0x%x", (unsigned)e);
    return buf;
}

void ft_reset(void)
{
    g_now = 0;
    g_qn = g_nsent = g_nwire = g_nlog = 0;
    g_alerts = 0;
    g_alerts_enabled = 0;
    g_air_time = 200;
    g_n_air_id = 0;
    g_head_started = -1;
    g_last_done = 0;
    g_ll_aborts = 0;
    g_ll_aborts_while_tx = 0;
    g_ll_removed = 0;
    g_stall_n = g_refuse_n = g_rx_err_n = 0;
    g_stall_id_n = 0;
    g_refuse_id = 0;
    g_refuse_id_n = 0;
    g_max_inflight = 0;
    g_in_receive = false;
    g_unsafe_teardowns = 0;
    g_installed_mode = -1;
    g_install_n = 0;
    g_installed_rxq = 0;
    g_installed_acc_code = g_installed_acc_mask = 0;
    g_installed_single_filter = -1;
    g_fail_alerts_cfg = false;
    g_fail_next = false;
    g_rx_n = g_rx_dropped = 0;
    g_self_received = 0;
    g_last_rx_time = -1;
    g_ndeliv = 0;
    g_nwv = 0;
    g_nallow = 0;
    g_allow_why[0] = 0;
}
