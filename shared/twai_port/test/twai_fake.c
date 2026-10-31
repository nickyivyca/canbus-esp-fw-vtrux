/*
 * twai_fake -- see twai_fake.h for what this models and why, and README.md
 * for what each behaviour rests on.
 *
 * Every driver entry point below follows ESP-IDF v5.4.1's
 * components/driver/twai/twai.c (cited as "twai.c" with its line numbers).
 * Where gen-inhibit's fake_twai.c and the interposer's twai_fake.cpp did
 * something different, the source decides, and README.md lists the change.
 */
#include "twai_fake.h"
#include "hal/twai_ll.h"

#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------- frames -- */

typedef struct {
    twai_fake_frame_t f;
    int  sent_idx;          /* its entry in the sent log, or -1 */
    bool stalled;           /* never wins arbitration */
    bool fail;              /* completes failed (fail rule) */
} qframe_t;

typedef struct {
    twai_fake_frame_t *v;
    int n, cap;
} flog_t;

static void flog_push(flog_t *l, const twai_fake_frame_t *f)
{
    if (l->n == l->cap)
    {
        int cap = l->cap ? l->cap * 2 : 256;
        twai_fake_frame_t *v = (twai_fake_frame_t *)realloc(l->v, (size_t)cap * sizeof(*v));
        if (v == NULL) { abort(); }
        l->v = v;
        l->cap = cap;
    }
    l->v[l->n++] = *f;
}

/* A FIFO of frames, sized at install as the driver's queues are. */
typedef struct {
    qframe_t *v;
    int n, cap;
} fifo_t;

static void fifo_size(fifo_t *q, int cap)
{
    int alloc = cap > 0 ? cap : 1;
    qframe_t *v = (qframe_t *)realloc(q->v, (size_t)alloc * sizeof(*v));
    if (v == NULL) { abort(); }
    q->v = v;
    q->cap = cap;
    q->n = 0;
}

static void fifo_push(fifo_t *q, const qframe_t *f) { q->v[q->n++] = *f; }

static qframe_t fifo_pop(fifo_t *q)
{
    qframe_t f = q->v[0];
    memmove(&q->v[0], &q->v[1], (size_t)(q->n - 1) * sizeof(q->v[0]));
    q->n--;
    return f;
}

/* Test knobs that pick frames: by ID or any, for n frames or for ever. */
#define RULEMAX 16
typedef struct { uint32_t id; int n; esp_err_t rc; } rule_t;
typedef struct { rule_t r[RULEMAX]; int n; } rules_t;

static void rule_add(rules_t *rs, uint32_t id, int n, esp_err_t rc)
{
    if (n == 0 || rs->n >= RULEMAX) { return; }
    rs->r[rs->n].id = id;
    rs->r[rs->n].n = n;
    rs->r[rs->n].rc = rc;
    rs->n++;
}

/* The first live rule matching `id`, consumed; NULL if none. */
static const rule_t *rule_take(rules_t *rs, uint32_t id)
{
    for (int i = 0; i < rs->n; i++)
    {
        rule_t *r = &rs->r[i];
        if (r->n == 0) { continue; }
        if (r->id != TWAI_FAKE_ANY_ID && r->id != id) { continue; }
        if (r->n > 0) { r->n--; }
        return r;
    }
    return NULL;
}

/* -------------------------------------------------------------- state -- */

/* The wiring, kept across twai_fake_reset(). */
static twai_fake_clock_fn g_clock;
static void              *g_clock_ctx;
static int64_t            g_own_now;
static int64_t            g_tick_us = 1000;
static twai_fake_wait_fn  g_wait;
static void              *g_wait_ctx;
static twai_fake_tx_fn    g_tx_hook, g_tx_acc_hook;
static void              *g_tx_hook_ctx, *g_tx_acc_hook_ctx;
static twai_fake_chip_t   g_chip = TWAI_FAKE_CHIP_C3;
static bool               g_yield_first;

/* The driver object: what twai_driver_install() allocates, zeroed. */
static bool         g_installed;
static twai_state_t g_state;
static twai_mode_t  g_mode;
static twai_general_config_t g_gcfg;
static twai_timing_config_t  g_tcfg;
static twai_filter_config_t  g_fcfg;
static uint32_t     g_brp;
static uint32_t     g_alerts_enabled, g_alerts_triggered;
static bool         g_alert_sem;            /* the driver's binary semaphore */
static uint32_t     g_rx_missed, g_rx_overrun, g_tx_failed, g_bus_errors;
static uint32_t     g_tec, g_rec;

/* The controller: one transmit buffer, the TX queue behind it, the RX queue. */
static bool     g_buf_full;
static qframe_t g_buf;
static int64_t  g_buf_start;    /* when the frame in the buffer started */
static fifo_t   g_txq, g_rxq;

/* Knobs. */
static twai_fake_tx_timing_t g_timing;
static int64_t  g_air_us;
#define AIRMAX 16
static struct { uint32_t id; int64_t us; } g_air_id[AIRMAX];
static int      g_n_air_id;
static bool     g_acked;
static rules_t  g_stall, g_fail, g_refuse;
static bool     g_inflight_fails;
static int      g_busoff_after, g_busoff_count;
static bool     g_auto_recovery;
static int64_t  g_recovery_done;
static int      g_fail_install;            /* installs left to fail; < 0 all */
static bool     g_fail_start, g_fail_reconf;
static esp_err_t g_fail_install_rc, g_fail_start_rc;
static int      g_rx_err_n;
static bool     g_misreport;

/* Logs and counts. */
static flog_t   g_sent, g_wire;
static int      g_max_inflight;
static uint32_t g_transmit_calls, g_lo_attempts, g_refused_rule, g_failed_rule;
static uint32_t g_wrong_state;
static uint32_t g_disc_stop, g_cut_stop, g_disc_uninstall, g_disc_recovery;
static uint32_t g_disc_start, g_disc_clear, g_failed_busoff;
static uint32_t *g_drop_ids;
static int      g_n_drop, g_cap_drop;
static uint32_t g_rx_not_rx, g_rx_filtered, g_rx_disc, g_rx_handed, g_self_rx;
static int64_t  g_last_rx = -1;
static uint32_t g_tot_missed, g_tot_overrun, g_tot_failed;
static bool     g_in_receive;
static int      g_unsafe;
static int      g_install_n;
#define INSTLOGMAX 256
static twai_mode_t g_inst_mode[INSTLOGMAX];
static int64_t  g_inst_time[INSTLOGMAX];
static int      g_n_inst;
static int      g_ll_aborts, g_ll_aborts_tx, g_ll_removed, g_ll_unmeasured;

/* ---------------------------------------------------------------- time -- */

int64_t twai_fake_now(void)
{
    return g_clock ? g_clock(g_clock_ctx) : g_own_now;
}

static void catch_up(void);

void twai_fake_advance(int64_t us)
{
    if (g_clock == NULL && us > 0) { g_own_now += us; }
    catch_up();
}

void twai_fake_poll(void) { catch_up(); }

void twai_fake_set_clock(twai_fake_clock_fn fn, void *ctx)
{
    g_clock = fn;
    g_clock_ctx = ctx;
}

void twai_fake_set_tick_us(int64_t us) { g_tick_us = us > 0 ? us : 1000; }

static int64_t deadline_for(TickType_t ticks)
{
    if (ticks == portMAX_DELAY) { return INT64_MAX; }
    return twai_fake_now() + (int64_t)ticks * g_tick_us;
}

/* -------------------------------------------------------------- alerts -- */

/*
 * twai.c:111, twai_alert_handler(): an alert is latched only if it is
 * enabled WHEN IT OCCURS, and then gives the semaphore twai_read_alerts()
 * waits on. One enabled later never shows an event from before.
 */
static void raise_alert(uint32_t a)
{
    if (g_alerts_enabled & a)
    {
        g_alerts_triggered |= a;
        g_alert_sem = true;
    }
}

/* ----------------------------------------------------------- air time -- */

int64_t twai_fake_frame_us(uint32_t dlc, bool extd, bool rtr, int64_t bit_ns)
{
    const int64_t bits = (extd ? 67 : 47) + (rtr ? 0 : 8 * (int64_t)(dlc > 8 ? 8 : dlc));
    return (bits * bit_ns + 999) / 1000;
}

int64_t twai_fake_bit_ns(void)
{
    if (!g_installed) { return 0; }
    /* 80 MHz APB: one quantum is brp x 12.5 ns, a bit 1 + tseg_1 + tseg_2. */
    return (int64_t)g_brp * (1 + g_tcfg.tseg_1 + g_tcfg.tseg_2) * 25 / 2;
}

static int64_t air_for(const twai_fake_frame_t *f)
{
    for (int i = 0; i < g_n_air_id; i++)
    {
        if (g_air_id[i].id == f->id) { return g_air_id[i].us; }
    }
    if (g_air_us > 0) { return g_air_us; }
    return twai_fake_frame_us(f->dlc, f->extd, f->rtr, twai_fake_bit_ns());
}

/* ---------------------------------------------------------- receiving -- */

/*
 * The acceptance filter in single-filter mode (ESP32-C3 TRM, TWAI chapter,
 * "Acceptance filter"; the S3's is the same): a standard frame's ID in bits
 * 31-21, RTR in bit 20 and data bytes 0 and 1 in bits 15-0; an extended
 * frame's ID in bits 31-3 and RTR in bit 2. A mask bit of 1 is don't-care.
 * Dual-filter mode is not modelled: every frame passes.
 */
static bool filter_passes(const twai_fake_frame_t *f)
{
    uint32_t v, care;
    if (!g_fcfg.single_filter) { return true; }
    if (f->extd)
    {
        v = ((f->id & TWAI_EXTD_ID_MASK) << 3) | (f->rtr ? 0x4u : 0u);
        care = 0xFFFFFFFCu;
    }
    else
    {
        v = ((f->id & TWAI_STD_ID_MASK) << 21) | (f->rtr ? 0x100000u : 0u);
        care = 0xFFF00000u;
        if (!f->rtr && f->dlc >= 1) { v |= (uint32_t)f->data[0] << 8; care |= 0xFF00u; }
        if (!f->rtr && f->dlc >= 2) { v |= f->data[1]; care |= 0xFFu; }
    }
    return ((v ^ g_fcfg.acceptance_code) & ~g_fcfg.acceptance_mask & care) == 0;
}

/* twai.c:131: a frame the controller received goes to the RX queue, or is
 * lost to a full one. Nothing is received unless running. */
static void rx_receive(const twai_fake_frame_t *f)
{
    qframe_t q;
    if (!g_installed || g_state != TWAI_STATE_RUNNING)
    {
        g_rx_not_rx++;
        return;
    }
    if (!filter_passes(f))
    {
        g_rx_filtered++;
        return;
    }
    if (g_rxq.n >= g_rxq.cap)
    {
        g_rx_missed++;
        g_tot_missed++;
        raise_alert(TWAI_ALERT_RX_QUEUE_FULL);
        return;
    }
    memset(&q, 0, sizeof(q));
    q.f = *f;
    q.sent_idx = -1;
    fifo_push(&g_rxq, &q);
    raise_alert(TWAI_ALERT_RX_DATA);
}

/* --------------------------------------------------------- transmitting -- */

static void drop_id(uint32_t id)
{
    if (g_n_drop == g_cap_drop)
    {
        int cap = g_cap_drop ? g_cap_drop * 2 : 64;
        uint32_t *v = (uint32_t *)realloc(g_drop_ids, (size_t)cap * sizeof(*v));
        if (v == NULL) { abort(); }
        g_drop_ids = v;
        g_cap_drop = cap;
    }
    g_drop_ids[g_n_drop++] = id;
}

static void load_next(int64_t start)
{
    if (g_txq.n > 0)
    {
        g_buf = fifo_pop(&g_txq);
        g_buf_full = true;
        g_buf_start = start;
    }
    else
    {
        g_buf_full = false;
        raise_alert(TWAI_ALERT_TX_IDLE);
    }
}

static void enter_bus_off(void);

/* twai.c:180, twai_handle_tx_buffer_frame(): the frame in the buffer is
 * done, successfully or not; the next one is loaded, or TX_IDLE raised. */
static void complete_buf(int64_t done, bool failed)
{
    qframe_t q = g_buf;
    q.f.t_done = done;
    q.f.failed = failed;
    if (q.sent_idx >= 0)
    {
        g_sent.v[q.sent_idx].t_done = done;
        g_sent.v[q.sent_idx].failed = failed;
    }
    g_buf_full = false;
    if (failed)
    {
        g_tx_failed++;
        g_tot_failed++;
        raise_alert(TWAI_ALERT_TX_FAILED);
    }
    else
    {
        flog_push(&g_wire, &q.f);
        raise_alert(TWAI_ALERT_TX_SUCCESS);
        /* Self-reception: the controller receives its own frame. */
        if (q.f.self)
        {
            g_self_rx++;
            rx_receive(&q.f);
        }
    }
    load_next(done);
    if (!failed && g_busoff_after > 0 && ++g_busoff_count >= g_busoff_after)
    {
        g_busoff_after = 0;
        enter_bus_off();
    }
}

/* Can the frame in the buffer finish? A stalled frame never starts; in
 * normal mode with nobody ACKing, a frame is retransmitted for ever unless
 * single-shot, when its one attempt fails. */
static bool buf_can_finish(bool *failed)
{
    *failed = g_buf.fail;
    if (g_buf.stalled) { return false; }
    if (g_mode == TWAI_MODE_NORMAL && !g_acked)
    {
        if (!g_buf.f.ss) { return false; }
        *failed = true;
    }
    return true;
}

/* Run the transmit side up to `t` (TIMED), or as far as it can go now
 * (INSTANT, and HELD's flush). */
static void tx_advance(int64_t t, bool ignore_air)
{
    while (g_buf_full && g_installed && g_state == TWAI_STATE_RUNNING)
    {
        bool failed;
        int64_t done;
        if (!buf_can_finish(&failed)) { return; }
        done = ignore_air ? t : g_buf_start + air_for(&g_buf.f);
        if (!ignore_air && t < done) { return; }
        complete_buf(done, failed);
    }
}

static void complete_recovery(void)
{
    if (g_state != TWAI_STATE_RECOVERING) { return; }
    g_state = TWAI_STATE_STOPPED;           /* twai.c:242 */
    raise_alert(TWAI_ALERT_BUS_RECOVERED);
}

static void catch_up(void)
{
    const int64_t t = twai_fake_now();
    if (g_state == TWAI_STATE_RECOVERING && g_auto_recovery && t >= g_recovery_done)
    {
        complete_recovery();
    }
    if (g_timing == TWAI_FAKE_TX_TIMED) { tx_advance(t, false); }
}

/* twai.c:238: bus-off. The state and the alert; queues untouched. */
static void enter_bus_off(void)
{
    if (!g_installed || g_state != TWAI_STATE_RUNNING) { return; }
    g_state = TWAI_STATE_BUS_OFF;
    raise_alert(TWAI_ALERT_BUS_OFF);
    if (g_inflight_fails && g_buf_full)
    {
        /* Branch a: a TX-buffer-free event for the halted frame books it
         * failed and takes it out of the count (twai.c:186-191). */
        complete_buf(twai_fake_now(), true);
        g_failed_busoff++;
    }
}

/* Throw away every frame in the controller, counting them in `*count`. */
static void discard_tx(uint32_t *count, bool record_ids, bool cut_head)
{
    if (g_buf_full)
    {
        if (record_ids) { drop_id(g_buf.f.id); }
        if (cut_head) { g_cut_stop++; } else { (*count)++; }
        g_buf_full = false;
    }
    while (g_txq.n > 0)
    {
        qframe_t q = fifo_pop(&g_txq);
        if (record_ids) { drop_id(q.f.id); }
        (*count)++;
    }
}

static void discard_rx(void)
{
    g_rx_disc += (uint32_t)g_rxq.n;
    g_rxq.n = 0;
}

/* --------------------------------------------------------- the driver -- */

static esp_err_t no_driver(void)
{
    /* twai.c's handle-less calls pass g_twai_objs[0], NULL with no driver,
     * and TWAI_CHECK(handle != NULL, ESP_ERR_INVALID_ARG) refuses it. */
    g_wrong_state++;
    return ESP_ERR_INVALID_ARG;
}

static esp_err_t wrong_state(void)
{
    g_wrong_state++;
    return ESP_ERR_INVALID_STATE;
}

esp_err_t twai_driver_install(const twai_general_config_t *g,
                              const twai_timing_config_t *t,
                              const twai_filter_config_t *f)
{
    uint32_t brp;
    catch_up();
    /* twai.c:436-467 and :541 */
    if (g == NULL || t == NULL || f == NULL) { return ESP_ERR_INVALID_ARG; }
    if (g->controller_id != 0) { return ESP_ERR_INVALID_ARG; }
    if (g->rx_queue_len == 0) { return ESP_ERR_INVALID_ARG; }
    if (g_installed) { return wrong_state(); }
    brp = t->brp;
    if (t->quanta_resolution_hz)
    {
        if (80000000u % t->quanta_resolution_hz != 0) { return ESP_ERR_INVALID_ARG; }
        brp = 80000000u / t->quanta_resolution_hz;
    }
    if (brp < TWAI_BRP_MIN || brp > TWAI_BRP_MAX || (brp & 1u)) { return ESP_ERR_INVALID_ARG; }
    if (g_fail_install != 0)
    {
        if (g_fail_install > 0) { g_fail_install--; }
        return g_fail_install_rc;
    }

    /* A fresh driver object (twai.c:358, calloc): every count zero. */
    g_installed = true;
    g_state = TWAI_STATE_STOPPED;
    g_mode = g->mode;
    g_gcfg = *g;
    g_tcfg = *t;
    g_fcfg = *f;
    g_brp = brp;
    g_alerts_enabled = g->alerts_enabled;
    g_alerts_triggered = 0;
    g_alert_sem = false;
    g_rx_missed = g_rx_overrun = g_tx_failed = g_bus_errors = 0;
    g_buf_full = false;
    fifo_size(&g_txq, (int)g->tx_queue_len);
    fifo_size(&g_rxq, (int)g->rx_queue_len);
    g_install_n++;
    if (g_n_inst < INSTLOGMAX)
    {
        g_inst_mode[g_n_inst] = g->mode;
        g_inst_time[g_n_inst] = twai_fake_now();
        g_n_inst++;
    }
    return ESP_OK;
}

esp_err_t twai_driver_uninstall(void)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    /* twai.c:558 */
    if (g_state != TWAI_STATE_STOPPED && g_state != TWAI_STATE_BUS_OFF)
    {
        return wrong_state();
    }
    if (g_in_receive) { g_unsafe++; }
    discard_tx(&g_disc_uninstall, true, false);
    discard_rx();
    g_installed = false;
    g_state = TWAI_STATE_STOPPED;
    return ESP_OK;
}

esp_err_t twai_start(void)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (g_fail_start)
    {
        g_fail_start = false;
        return g_fail_start_rc;
    }
    if (g_state != TWAI_STATE_STOPPED) { return wrong_state(); }
    /* twai.c:605-611: both queues reset. */
    discard_rx();
    discard_tx(&g_disc_start, false, false);
    g_state = TWAI_STATE_RUNNING;
    return ESP_OK;
}

esp_err_t twai_stop(void)
{
    bool cut;
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (g_state != TWAI_STATE_RUNNING) { return wrong_state(); }
    if (g_in_receive) { g_unsafe++; }
    /* twai.c:637-643: the controller to reset mode, the TX queue reset. A
     * frame already on the wire is cut off mid-frame. The RX queue stays. */
    cut = g_timing == TWAI_FAKE_TX_TIMED && g_buf_full && !g_buf.stalled
          && twai_fake_now() >= g_buf_start;
    discard_tx(&g_disc_stop, true, cut);
    g_state = TWAI_STATE_STOPPED;
    return ESP_OK;
}

static void note_inflight(void)
{
    int ours = (g_buf_full && !g_buf.f.foreign) ? 1 : 0;
    for (int i = 0; i < g_txq.n; i++)
    {
        if (!g_txq.v[i].f.foreign) { ours++; }
    }
    if (ours > g_max_inflight) { g_max_inflight = ours; }
}

static esp_err_t transmit(const twai_message_t *m, TickType_t ticks, bool foreign)
{
    qframe_t q;
    const rule_t *r;
    g_transmit_calls++;
    if (!foreign && g_tx_hook) { g_tx_hook(m, g_tx_hook_ctx); }
    catch_up();
    if (!g_installed) { return no_driver(); }
    /* twai.c:660-673: the arguments, then listen-only, then the state. */
    if (m == NULL) { return ESP_ERR_INVALID_ARG; }
    if (m->data_length_code > TWAI_FRAME_MAX_DLC && !m->dlc_non_comp) { return ESP_ERR_INVALID_ARG; }
    if (g_mode == TWAI_MODE_LISTEN_ONLY)
    {
        g_lo_attempts++;
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (g_state != TWAI_STATE_RUNNING) { return wrong_state(); }
    r = rule_take(&g_refuse, m->identifier);
    if (r != NULL)
    {
        g_refused_rule++;
        return r->rc;
    }

    memset(&q, 0, sizeof(q));
    q.f.t_queued = twai_fake_now();
    q.f.t_done = -1;
    q.f.id = m->identifier;
    q.f.dlc = m->data_length_code > 8 ? 8 : m->data_length_code;
    memcpy(q.f.data, m->data, q.f.dlc);
    q.f.extd = m->extd != 0;
    q.f.rtr = m->rtr != 0;
    q.f.ss = m->ss != 0;
    q.f.self = m->self != 0;
    q.f.foreign = foreign;

    if (g_buf_full)
    {
        /* twai.c:688-718: into the queue, waiting for space. */
        if (g_txq.cap == 0) { return ESP_FAIL; }
        if (g_txq.n >= g_txq.cap)
        {
            const int64_t deadline = deadline_for(ticks);
            for (;;)
            {
                if (ticks == 0 || g_wait == NULL || twai_fake_now() >= deadline
                    || !g_wait(deadline, g_wait_ctx))
                {
                    return ESP_ERR_TIMEOUT;
                }
                catch_up();
                if (!g_installed || g_state != TWAI_STATE_RUNNING) { return wrong_state(); }
                if (g_txq.n < g_txq.cap || !g_buf_full) { break; }
            }
        }
    }

    q.stalled = rule_take(&g_stall, q.f.id) != NULL;
    q.fail = rule_take(&g_fail, q.f.id) != NULL;
    if (q.fail) { g_failed_rule++; }
    q.sent_idx = g_sent.n;
    flog_push(&g_sent, &q.f);
    if (!g_buf_full)
    {
        /* twai.c:680: nothing waiting, so straight into the buffer. */
        g_buf = q;
        g_buf_full = true;
        g_buf_start = q.f.t_queued;
    }
    else
    {
        fifo_push(&g_txq, &q);
    }
    note_inflight();
    if (!foreign && g_tx_acc_hook) { g_tx_acc_hook(m, g_tx_acc_hook_ctx); }
    if (g_timing == TWAI_FAKE_TX_INSTANT) { tx_advance(twai_fake_now(), true); }
    return ESP_OK;
}

esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks_to_wait)
{
    return transmit(message, ticks_to_wait, false);
}

esp_err_t twai_fake_foreign_transmit(const twai_message_t *m)
{
    return transmit(m, 0, true);
}

esp_err_t twai_receive(twai_message_t *message, TickType_t ticks_to_wait)
{
    const int64_t deadline = deadline_for(ticks_to_wait);
    bool yielded = false;
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (message == NULL) { return ESP_ERR_INVALID_ARG; }
    for (;;)
    {
        bool ok = true;
        /* twai.c:738: a receive waits on the queue in any state. */
        if ((g_yield_first && !yielded) || g_rxq.n == 0)
        {
            if (ticks_to_wait == 0 || g_wait == NULL || twai_fake_now() >= deadline)
            {
                if (g_rxq.n == 0) { return ESP_ERR_TIMEOUT; }
            }
            else
            {
                g_in_receive = true;
                ok = g_wait(deadline, g_wait_ctx);
                g_in_receive = false;
                yielded = true;
                catch_up();
                /* The driver went away underneath the call: on the device
                 * a use-after-free, counted at the teardown. */
                if (!g_installed) { return ESP_ERR_INVALID_STATE; }
            }
        }
        if (g_rx_err_n > 0)
        {
            g_rx_err_n--;
            return ESP_FAIL;
        }
        if (g_rxq.n > 0)
        {
            const qframe_t q = fifo_pop(&g_rxq);
            memset(message, 0, sizeof(*message));
            message->identifier = q.f.id;
            message->extd = q.f.extd;
            message->rtr = q.f.rtr;
            message->data_length_code = q.f.dlc;
            memcpy(message->data, q.f.data, q.f.dlc);
            g_rx_handed++;
            g_last_rx = twai_fake_now();
            return ESP_OK;
        }
        if (!ok || twai_fake_now() >= deadline) { return ESP_ERR_TIMEOUT; }
    }
}

esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks_to_wait)
{
    const int64_t deadline = deadline_for(ticks_to_wait);
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (alerts == NULL) { return ESP_ERR_INVALID_ARG; }
    for (;;)
    {
        /* twai.c:766-775 */
        if (g_alert_sem)
        {
            g_alert_sem = false;
            *alerts = g_alerts_triggered;
            g_alerts_triggered = 0;
            return ESP_OK;
        }
        if (ticks_to_wait == 0 || g_wait == NULL || twai_fake_now() >= deadline
            || !g_wait(deadline, g_wait_ctx))
        {
            *alerts = 0;
            return ESP_ERR_TIMEOUT;
        }
        catch_up();
        if (!g_installed) { return ESP_ERR_INVALID_STATE; }
    }
}

esp_err_t twai_reconfigure_alerts(uint32_t alerts_enabled, uint32_t *current_alerts)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (g_fail_reconf) { return ESP_ERR_INVALID_STATE; }
    /* twai.c:790-797: hands back the alerts triggered and not yet read, and
     * clears them; the semaphore is left as it was. */
    if (current_alerts != NULL) { *current_alerts = g_alerts_triggered; }
    g_alerts_triggered = 0;
    g_alerts_enabled = alerts_enabled;
    return ESP_OK;
}

esp_err_t twai_initiate_recovery(void)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (g_state != TWAI_STATE_BUS_OFF) { return wrong_state(); }
    /* twai.c:820-828: the TX queue and the count reset, recovery started. */
    discard_tx(&g_disc_recovery, false, false);
    g_state = TWAI_STATE_RECOVERING;
    g_recovery_done = twai_fake_now() + (1408 * twai_fake_bit_ns() + 999) / 1000;
    return ESP_OK;
}

static void fill_status(twai_status_info_t *s)
{
    memset(s, 0, sizeof(*s));
    if (!g_installed) { return; }
    /* twai.c:848-863 */
    if (g_mode != TWAI_MODE_LISTEN_ONLY)
    {
        s->tx_error_counter = g_tec;
        s->rx_error_counter = g_rec;
    }
    s->msgs_to_tx = g_misreport ? 0u : (uint32_t)((g_buf_full ? 1 : 0) + g_txq.n);
    s->msgs_to_rx = (uint32_t)g_rxq.n;
    s->tx_failed_count = g_tx_failed;
    s->rx_missed_count = g_rx_missed;
    s->rx_overrun_count = g_rx_overrun;
    s->bus_error_count = g_bus_errors;
    s->state = g_state;
}

esp_err_t twai_get_status_info(twai_status_info_t *s)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (s == NULL) { return ESP_ERR_INVALID_ARG; }
    fill_status(s);
    return ESP_OK;
}

void twai_fake_driver_status(twai_status_info_t *s)
{
    catch_up();
    fill_status(s);
}

esp_err_t twai_clear_transmit_queue(void)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    if (g_txq.cap == 0) { return ESP_ERR_NOT_SUPPORTED; }
    /* twai.c:884-885: the queue, not the frame in the buffer. */
    g_disc_clear += (uint32_t)g_txq.n;
    g_txq.n = 0;
    return ESP_OK;
}

esp_err_t twai_clear_receive_queue(void)
{
    catch_up();
    if (!g_installed) { return no_driver(); }
    discard_rx();
    return ESP_OK;
}

/* ------------------------------------------------- the register model -- */

/*
 * The transmit buffer's status register, DERIVED from the buffer, never
 * tracked beside it (gen-inhibit's fake_twai.c, which found two sources of
 * truth disagreeing):
 *
 *   buffer empty                        TBS set
 *   frame that has not won arbitration  TBS clear, TS clear  (awaiting)
 *   frame on the wire                   TBS clear, TS set    (transmitting)
 *
 * The abort, as measured on the C3 (2026-09-27, reverse-it
 * projects/vtrux/notes/artifacts/gen-inhibit/runs/txabort_full.json):
 * awaiting, the frame is removed and never reaches the wire, and the
 * driver reports TX_SUCCESS anyway; transmitting, a no-op; empty, nothing.
 */
static char g_hw_token;

twai_dev_t *twai_fake_ll_hw(int controller_id)
{
    return controller_id == 0 ? (twai_dev_t *)(void *)&g_hw_token : (twai_dev_t *)0;
}

static bool buf_transmitting(void)
{
    return g_buf_full && !g_buf.stalled && g_installed
           && g_state == TWAI_STATE_RUNNING && twai_fake_now() >= g_buf_start;
}

uint32_t twai_ll_get_status(twai_dev_t *hw)
{
    if (g_chip != TWAI_FAKE_CHIP_C3) { g_ll_unmeasured++; }
    catch_up();
    if (hw != twai_fake_ll_hw(0)) { return 0; }
    if (!g_buf_full) { return TWAI_LL_STATUS_TBS; }
    return buf_transmitting() ? (uint32_t)TWAI_LL_STATUS_TS : 0u;
}

void twai_ll_set_cmd_abort_tx(twai_dev_t *hw)
{
    if (g_chip != TWAI_FAKE_CHIP_C3) { g_ll_unmeasured++; }
    catch_up();
    if (hw != twai_fake_ll_hw(0)) { return; }
    g_ll_aborts++;
    if (!g_buf_full) { return; }
    if (buf_transmitting())
    {
        g_ll_aborts_tx++;
        return;
    }
    /* Removed: the sent log keeps t_done -1, the wire log is not touched,
     * and the ISR handles it as a completion (twai.c:183-206). */
    g_buf_full = false;
    g_ll_removed++;
    raise_alert(TWAI_ALERT_TX_SUCCESS);
    load_next(twai_fake_now());
}

/* ------------------------------------------------------------ the knobs -- */

void twai_fake_set_wait_hook(twai_fake_wait_fn fn, void *ctx) { g_wait = fn; g_wait_ctx = ctx; }
void twai_fake_set_tx_hook(twai_fake_tx_fn fn, void *ctx) { g_tx_hook = fn; g_tx_hook_ctx = ctx; }
void twai_fake_set_tx_accepted_hook(twai_fake_tx_fn fn, void *ctx) { g_tx_acc_hook = fn; g_tx_acc_hook_ctx = ctx; }
void twai_fake_set_chip(twai_fake_chip_t chip) { g_chip = chip; }
void twai_fake_set_receive_yields_first(bool on) { g_yield_first = on; }

void twai_fake_set_tx_timing(twai_fake_tx_timing_t t)
{
    catch_up();
    g_timing = t;
    catch_up();
}

void twai_fake_flush_tx(void) { tx_advance(twai_fake_now(), true); }

void twai_fake_set_air_time_us(int64_t us) { catch_up(); g_air_us = us; }

void twai_fake_set_air_time_id(uint32_t id, int64_t us)
{
    catch_up();
    for (int i = 0; i < g_n_air_id; i++)
    {
        if (g_air_id[i].id == id)
        {
            g_air_id[i].us = us;
            if (us == 0) { g_air_id[i] = g_air_id[--g_n_air_id]; }
            return;
        }
    }
    if (us != 0 && g_n_air_id < AIRMAX)
    {
        g_air_id[g_n_air_id].id = id;
        g_air_id[g_n_air_id].us = us;
        g_n_air_id++;
    }
}

void twai_fake_set_acked(bool acked)
{
    catch_up();
    /* An ACKer arriving: the attempt it ACKs starts now. */
    if (acked && !g_acked && g_buf_full) { g_buf_start = twai_fake_now(); }
    g_acked = acked;
    if (g_timing == TWAI_FAKE_TX_INSTANT) { tx_advance(twai_fake_now(), true); }
}

void twai_fake_stall_next(int n) { rule_add(&g_stall, TWAI_FAKE_ANY_ID, n, ESP_OK); }
void twai_fake_stall_id(uint32_t id, int n) { rule_add(&g_stall, id, n, ESP_OK); }
void twai_fake_fail(uint32_t id, int n) { rule_add(&g_fail, id, n, ESP_OK); }
void twai_fake_clear_fail_rules(void) { g_fail.n = 0; }
void twai_fake_refuse(uint32_t id, int n, esp_err_t rc) { rule_add(&g_refuse, id, n, rc); }
void twai_fake_clear_refuse_rules(void) { g_refuse.n = 0; }

void twai_fake_deliver(const twai_message_t *m)
{
    twai_fake_frame_t f;
    catch_up();
    memset(&f, 0, sizeof(f));
    f.t_queued = twai_fake_now();
    f.t_done = f.t_queued;
    f.id = m->identifier;
    f.dlc = m->data_length_code > 8 ? 8 : m->data_length_code;
    memcpy(f.data, m->data, f.dlc);
    f.extd = m->extd != 0;
    f.rtr = m->rtr != 0;
    rx_receive(&f);
}

void twai_fake_deliver_std(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = id;
    m.data_length_code = dlc > 8 ? 8 : dlc;
    if (data != NULL) { memcpy(m.data, data, m.data_length_code); }
    twai_fake_deliver(&m);
}

void twai_fake_rx_overrun(uint32_t n)
{
    catch_up();
    if (n == 0 || !g_installed || g_state != TWAI_STATE_RUNNING) { return; }
    g_rx_overrun += n;
    g_tot_overrun += n;
    raise_alert(TWAI_ALERT_RX_FIFO_OVERRUN);
}

void twai_fake_bus_error(uint32_t n)
{
    catch_up();
    if (n == 0 || !g_installed) { return; }
    g_bus_errors += n;
    raise_alert(TWAI_ALERT_BUS_ERROR);
}

void twai_fake_set_error_counters(uint32_t tec, uint32_t rec) { g_tec = tec; g_rec = rec; }

void twai_fake_bus_off(void)
{
    catch_up();
    enter_bus_off();
}

void twai_fake_set_busoff_inflight_fails(bool on) { g_inflight_fails = on; }

void twai_fake_bus_off_after_sent(int n)
{
    catch_up();
    g_busoff_after = n > 0 ? n : 0;
    g_busoff_count = 0;
}

void twai_fake_set_auto_recovery(bool on) { catch_up(); g_auto_recovery = on; catch_up(); }

void twai_fake_complete_recovery(void)
{
    catch_up();
    complete_recovery();
}

void twai_fake_fail_install_next(esp_err_t rc) { twai_fake_fail_installs(1, rc); }
void twai_fake_fail_installs(int n, esp_err_t rc) { g_fail_install = n; g_fail_install_rc = rc; }
void twai_fake_fail_start_next(esp_err_t rc) { g_fail_start = true; g_fail_start_rc = rc; }
void twai_fake_fail_reconfigure_alerts(bool fail) { g_fail_reconf = fail; }
void twai_fake_rx_error_next(int n) { g_rx_err_n = n; }
void twai_fake_misreport_msgs_to_tx(bool on) { g_misreport = on; }

/* ---------------------------------------------------------- inspection -- */

twai_state_t twai_fake_state(void) { catch_up(); return g_state; }
bool         twai_fake_installed(void) { return g_installed; }
twai_mode_t  twai_fake_mode(void) { return g_mode; }
uint32_t     twai_fake_alerts_enabled(void) { return g_alerts_enabled; }
const twai_general_config_t *twai_fake_installed_general(void) { return &g_gcfg; }
const twai_timing_config_t  *twai_fake_installed_timing(void) { return &g_tcfg; }
const twai_filter_config_t  *twai_fake_installed_filter(void) { return &g_fcfg; }
uint32_t twai_fake_installed_brp(void) { return g_brp; }
int      twai_fake_install_count(void) { return g_install_n; }
int          twai_fake_install_log_count(void) { return g_n_inst; }
twai_mode_t  twai_fake_install_log_mode(int i) { return g_inst_mode[i]; }
int64_t      twai_fake_install_log_time(int i) { return g_inst_time[i]; }

int twai_fake_sent_count(void) { catch_up(); return g_sent.n; }
const twai_fake_frame_t *twai_fake_sent(int i) { return &g_sent.v[i]; }
int twai_fake_wire_count(void) { catch_up(); return g_wire.n; }
const twai_fake_frame_t *twai_fake_wire(int i) { return &g_wire.v[i]; }

static int count_id(const flog_t *l, uint32_t id)
{
    int n = 0;
    for (int i = 0; i < l->n; i++) { if (l->v[i].id == id) { n++; } }
    return n;
}

int twai_fake_sent_count_id(uint32_t id) { catch_up(); return count_id(&g_sent, id); }
int twai_fake_wire_count_id(uint32_t id) { catch_up(); return count_id(&g_wire, id); }

int      twai_fake_tx_pending(void) { catch_up(); return (g_buf_full ? 1 : 0) + g_txq.n; }
int      twai_fake_max_inflight(void) { return g_max_inflight; }
int      twai_fake_rx_pending(void) { catch_up(); return g_rxq.n; }
uint32_t twai_fake_transmit_calls(void) { return g_transmit_calls; }
uint32_t twai_fake_listen_only_tx_attempts(void) { return g_lo_attempts; }
uint32_t twai_fake_refused_by_rule(void) { return g_refused_rule; }
uint32_t twai_fake_failed_by_rule(void) { return g_failed_rule; }
uint32_t twai_fake_wrong_state_calls(void) { return g_wrong_state; }
uint32_t twai_fake_tx_discarded_by_stop(void) { return g_disc_stop; }
uint32_t twai_fake_tx_cut_by_stop(void) { return g_cut_stop; }
uint32_t twai_fake_tx_discarded_by_uninstall(void) { return g_disc_uninstall; }
uint32_t twai_fake_tx_discarded_by_recovery(void) { return g_disc_recovery; }
uint32_t twai_fake_tx_discarded_by_start(void) { return g_disc_start; }
uint32_t twai_fake_tx_discarded_by_clear(void) { return g_disc_clear; }
uint32_t twai_fake_tx_failed_at_busoff(void) { return g_failed_busoff; }
int      twai_fake_dropped_at_stop_count(void) { return g_n_drop; }
uint32_t twai_fake_dropped_at_stop_id(int i) { return g_drop_ids[i]; }
uint32_t twai_fake_rx_not_received(void) { return g_rx_not_rx; }
uint32_t twai_fake_rx_filtered(void) { return g_rx_filtered; }
uint32_t twai_fake_rx_discarded(void) { return g_rx_disc; }
uint32_t twai_fake_rx_handed_out(void) { return g_rx_handed; }
uint32_t twai_fake_self_received(void) { return g_self_rx; }
int64_t  twai_fake_last_rx_time(void) { return g_last_rx; }
uint32_t twai_fake_total_rx_missed(void) { return g_tot_missed; }
uint32_t twai_fake_total_rx_overrun(void) { return g_tot_overrun; }
uint32_t twai_fake_total_tx_failed(void) { return g_tot_failed; }
bool     twai_fake_in_receive(void) { return g_in_receive; }
int      twai_fake_unsafe_teardowns(void) { return g_unsafe; }
int      twai_fake_ll_aborts(void) { return g_ll_aborts; }
int      twai_fake_ll_aborts_while_tx(void) { return g_ll_aborts_tx; }
int      twai_fake_ll_removed(void) { return g_ll_removed; }
int      twai_fake_ll_unmeasured_calls(void) { return g_ll_unmeasured; }

/* --------------------------------------------------------------- reset -- */

void twai_fake_reset(void)
{
    g_own_now = 0;
    g_installed = false;
    g_state = TWAI_STATE_STOPPED;
    g_mode = TWAI_MODE_NORMAL;
    memset(&g_gcfg, 0, sizeof(g_gcfg));
    memset(&g_tcfg, 0, sizeof(g_tcfg));
    memset(&g_fcfg, 0, sizeof(g_fcfg));
    g_brp = 0;
    g_alerts_enabled = g_alerts_triggered = 0;
    g_alert_sem = false;
    g_rx_missed = g_rx_overrun = g_tx_failed = g_bus_errors = 0;
    g_tec = g_rec = 0;
    g_buf_full = false;
    g_buf_start = 0;
    g_txq.n = g_rxq.n = 0;

    g_timing = TWAI_FAKE_TX_TIMED;
    g_air_us = 0;
    g_n_air_id = 0;
    g_acked = true;
    g_stall.n = g_fail.n = g_refuse.n = 0;
    g_inflight_fails = false;
    g_busoff_after = g_busoff_count = 0;
    g_auto_recovery = true;
    g_recovery_done = 0;
    g_fail_install = 0;
    g_fail_start = g_fail_reconf = false;
    g_rx_err_n = 0;
    g_misreport = false;

    g_sent.n = g_wire.n = 0;
    g_max_inflight = 0;
    g_transmit_calls = g_lo_attempts = g_refused_rule = g_failed_rule = 0;
    g_wrong_state = 0;
    g_disc_stop = g_cut_stop = g_disc_uninstall = g_disc_recovery = 0;
    g_disc_start = g_disc_clear = g_failed_busoff = 0;
    g_n_drop = 0;
    g_rx_not_rx = g_rx_filtered = g_rx_disc = g_rx_handed = g_self_rx = 0;
    g_last_rx = -1;
    g_tot_missed = g_tot_overrun = g_tot_failed = 0;
    g_in_receive = false;
    g_unsafe = 0;
    g_install_n = 0;
    g_n_inst = 0;
    g_ll_aborts = g_ll_aborts_tx = g_ll_removed = g_ll_unmeasured = 0;
}

/* --------------------------------------------------- what this rests on -- */

static const twai_fake_assumption_t k_assumptions[] = {
    {TWAI_FAKE_SOURCE, "twai.c:433-543, twai_driver_install",
     "the argument checks (controller 0, rx_queue_len > 0, an even brp of 2 to "
     "16384 or a quanta resolution dividing 80 MHz), ESP_ERR_INVALID_STATE if "
     "installed, and a fresh calloc'd object: every count zero, STOPPED"},
    {TWAI_FAKE_SOURCE, "twai.c:545-623, uninstall and start",
     "uninstall only from STOPPED or BUS_OFF; start only from STOPPED, and it "
     "resets both queues"},
    {TWAI_FAKE_SOURCE, "twai.c:625-648, twai_stop",
     "only from RUNNING; the TX queue reset, the frame on the wire cut off; "
     "the RX queue left readable"},
    {TWAI_FAKE_SOURCE, "twai.c:541, :547, :596 ... TWAI_CHECK(handle != NULL)",
     "with no driver every call returns ESP_ERR_INVALID_ARG, though the header "
     "documents ESP_ERR_INVALID_STATE for 'not installed'"},
    {TWAI_FAKE_SOURCE, "twai.c:656-721, twai_transmit",
     "arguments, then listen-only (ESP_ERR_NOT_SUPPORTED), then RUNNING; a frame "
     "goes straight to the buffer when nothing is waiting, else to the queue, "
     "so msgs_to_tx reaches tx_queue_len + 1; a full queue waits, then "
     "ESP_ERR_TIMEOUT; with no queue, ESP_FAIL"},
    {TWAI_FAKE_SOURCE, "twai.c:729-755, twai_receive",
     "no state check: frames received before a stop or a bus-off stay readable"},
    {TWAI_FAKE_SOURCE, "twai.c:111-129, twai_alert_handler",
     "an alert is latched only if enabled when it occurs; read_alerts returns "
     "and clears them all; reconfigure_alerts hands back the triggered ones and "
     "clears them"},
    {TWAI_FAKE_SOURCE, "twai.c:131-207, the RX and TX buffer handlers",
     "a full RX queue loses the frame (rx_missed_count, RX_QUEUE_FULL); a "
     "completed frame raises TX_SUCCESS or TX_FAILED (tx_failed_count), then "
     "the next is loaded or TX_IDLE raised"},
    {TWAI_FAKE_SOURCE, "twai.c:238-245, :808-838",
     "bus-off sets the state and the alert and touches no queue; recovery is "
     "allowed only from bus-off, resets the TX queue, and ends in STOPPED with "
     "BUS_RECOVERED"},
    {TWAI_FAKE_SOURCE, "driver/twai.h, twai_initiate_recovery",
     "recovery waits for 128 occurrences of the bus-free signal: 1408 bit times"},
    {TWAI_FAKE_SOURCE, "twai.c:848-855, twai_get_status_info",
     "the error counters read 0 in listen-only"},
    {TWAI_FAKE_SOURCE, "hal/esp32c3 and esp32s3 twai_ll.h; soc_caps.h; Kconfig",
     "the C3 and the S3 have the same TWAI registers (only the clock enable's "
     "field names differ), the same capabilities, and the same errata options "
     "(none of the four ESP32-only ones; LISTEN_ONLY_DOM on both)"},
    {TWAI_FAKE_MEASURED, "gen-inhibit runs/txabort_full.json (2026-09-27), C3",
     "the abort removes an awaiting frame without putting it on the wire and the "
     "driver reports TX_SUCCESS; on a transmitting frame it does nothing"},
    {TWAI_FAKE_ASSUMED, "the S3: no measurement",
     "the S3's abort and buffer state are modelled as the C3's (the registers are "
     "the same), but every twai_ll call there is counted as unmeasured"},
    {TWAI_FAKE_ASSUMED, "frame length arithmetic; interposer bench boot 2026-10-13",
     "air time is the unstuffed frame length at the installed bit timing, one "
     "frame at a time, back to back: a lower bound"},
    {TWAI_FAKE_ASSUMED, "normal-mode CAN retransmission",
     "with nobody ACKing, a normal-mode frame is retransmitted for ever and never "
     "completes; single-shot, it fails once; no-ACK mode needs no ACK"},
    {TWAI_FAKE_ASSUMED, "not settled from IDF source (twai_hal_iram.c)",
     "the frame in the buffer at bus-off stays counted and recovery discards it; "
     "twai_fake_set_busoff_inflight_fails() gives the other branch"},
    {TWAI_FAKE_ASSUMED, "ESP32-C3 TRM, acceptance filter, single-filter mode",
     "the filter's bit layout; data bytes a frame does not carry match anything"},
    {TWAI_FAKE_NOT_MODELLED, "nothing here raises them",
     "arbitration, error frames, the error counters climbing, error passive and "
     "warning alerts, RECOVERY_IN_PROGRESS, ARB_LOST, dual-filter mode, the "
     "ESP32-only errata workarounds, and the DLC > 8 non-compliant frames"},
};

const twai_fake_assumption_t *twai_fake_assumptions(int *count)
{
    if (count != NULL) { *count = (int)(sizeof(k_assumptions) / sizeof(k_assumptions[0])); }
    return k_assumptions;
}
