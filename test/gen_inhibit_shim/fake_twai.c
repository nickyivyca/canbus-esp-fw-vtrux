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
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

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
static bool     g_fail_next;
static bool     g_installed, g_running;
static bool     g_in_receive;       /* a thread is blocked inside the driver */
static int      g_unsafe_teardowns;
static int      g_installed_mode = -1;
static bool     g_fail_alerts_cfg;

int  ft_unsafe_teardowns(void) { return g_unsafe_teardowns; }
int  ft_installed_mode(void)   { return g_installed_mode; }
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
void ft_refuse_next(int n) { g_refuse_n = n; }
void ft_fail_next(void) { g_fail_next = true; }
void ft_rx_error_next(int n) { g_rx_err_n = n; }

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
static bool           g_rx_have;
static twai_message_t g_rx_msg;

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
    (void)t; (void)f;
    /*
     * Record the MODE. Spec 3 requires OBSERVE to be hardware listen-only, and
     * the TWAI API exposes no read-back of the controller's mode -- so the only
     * thing that can be checked anywhere is the value handed to install, which
     * is exactly what gen_inhibit.c's own comment says it is confirming.
     */
    g_installed_mode = g ? (int)g->mode : -1;
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
esp_err_t twai_clear_receive_queue(void) { g_rx_have = false; return ESP_OK; }

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
    status->msgs_to_rx = g_rx_have ? 1u : 0u;
    return ESP_OK;
}

static bool enqueue(uint32_t id, const uint8_t *data, uint8_t dlc, bool foreign)
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
    if (g_stall_n > 0) { g_stall_n--; f->t_done = -2; }
    if (g_stall_id_n > 0 && id == g_stall_id) { g_stall_id_n--; f->t_done = -2; }
    if (g_fail_next)   { g_fail_next = false; f->failed = true; }
    if (g_nsent < SENTMAX) g_sent[g_nsent++] = *f;
    return true;
}

esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks)
{
    (void)ticks;
    if (!g_running) return ESP_ERR_INVALID_STATE;
    if (g_refuse_n > 0) { g_refuse_n--; return ESP_FAIL; }
    if (!enqueue(message->identifier, message->data,
                 message->data_length_code, false))
    {
        return ESP_FAIL;
    }
    return ESP_OK;
}

bool ft_foreign_transmit(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    return enqueue(id, data, dlc, true);
}

void ft_deliver(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    memset(&g_rx_msg, 0, sizeof(g_rx_msg));
    g_rx_msg.identifier = id;
    g_rx_msg.data_length_code = dlc;
    memcpy(g_rx_msg.data, data, dlc > 8 ? 8 : dlc);
    g_rx_have = true;
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
        if (g_rx_have)
        {
            *message = g_rx_msg;
            g_rx_have = false;
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
    g_stall_n = g_refuse_n = g_rx_err_n = 0;
    g_stall_id_n = 0;
    g_in_receive = false;
    g_unsafe_teardowns = 0;
    g_installed_mode = -1;
    g_fail_alerts_cfg = false;
    g_fail_next = false;
    g_rx_have = false;
}
