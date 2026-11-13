/*
 * fake_idf.c -- the host fakes of fake_idf.h, on an emulated clock.
 * TEST EQUIPMENT, NOT FOR VEHICLE.
 */
#include "fake_idf.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int64_t fake_now_us;
int64_t fake_time_step_us = 1;
bool fake_timer_broken;
int64_t fake_task_late_us;

char fake_log[FAKE_LOG_MAX][64];
int fake_log_n;

long fake_wire_pulses;
int64_t fake_wire_first_us = -1, fake_wire_last_us = -1;
uint32_t fake_wire_max_w_us, fake_wire_min_w_us = 0xFFFFFFFFu;
int64_t fake_tx_end_max_us = -1;
int fake_rmt_violations;
int fake_rmt_channels;
int fake_rmt_transmits;
int fake_timer_creates, fake_timer_starts, fake_timer_fired;

int fake_gpio_level[64];
int fake_gpio_output[64];

uint8_t fake_spi_tx[FAKE_SPI_MAX][8];
int fake_spi_len[FAKE_SPI_MAX];
int64_t fake_spi_t[FAKE_SPI_MAX];
int fake_spi_n;
int fake_spi_clock_hz = -1, fake_spi_mode = -1;
uint8_t fake_mcp_canstat_after_reset = 0x80;
uint8_t fake_mcp_canctrl_after_reset = 0x87;
esp_err_t fake_spi_bus_init_result = ESP_OK;
static int s_mcp_reset_done;

char fake_out[FAKE_OUT_MAX];
size_t fake_out_n;

esp_app_desc_t fake_app_desc;

const char *fake_task_name[FAKE_TASKS_MAX];
TaskFunction_t fake_task_fn[FAKE_TASKS_MAX];
int fake_task_prio[FAKE_TASKS_MAX], fake_task_core[FAKE_TASKS_MAX];
int fake_tasks_n;

/* ---- internal state ---- */

struct fake_rmt {
    rmt_tx_channel_config_t cfg;
    bool enabled;
    rmt_tx_done_callback_t on_done;
    void *ctx;
};
struct fake_enc { int resets; };
struct fake_timer {
    esp_timer_cb_t cb;
    void *arg;
    bool active;
    int64_t expiry;
};
struct fake_queue {
    size_t item;
    unsigned char buf[128];
    bool full;
};
struct fake_mutex { bool held; };
struct fake_task { int unused; };
struct fake_spidev { int unused; };

static struct fake_rmt *s_ch;           /* the one live channel, if any */
static struct {
    bool on;
    int64_t start, end;
    uint32_t w, period;
    long count;
} s_batch;
static struct fake_enc s_enc;
static struct fake_timer s_timer;
static struct fake_queue s_queues[4];
static int s_queues_n;
static struct fake_mutex s_mutexes[4];
static int s_mutexes_n;
static struct fake_task s_tasks[FAKE_TASKS_MAX];
static struct fake_spidev s_spidev;
static uint32_t s_bits;                 /* the jam task's notification */
/* Ten emulated minutes after fake_reset(): no command runs that long (the
 * cap is 30 s, the backstop 2 s more), so past it the code under test has
 * run away, and the test fails rather than hang. */
#define RUNAWAY_US (600LL * 1000000)
static int64_t s_runaway_at;
static int s_firing;                    /* inside an event's callback */

#define SCHED_MAX 16
static struct {
    int64_t t;
    void (*fn)(void *);
    void *arg;
    bool pending;
} s_sched[SCHED_MAX];

static void logf_(const char *fmt, ...)
{
    if (fake_log_n >= FAKE_LOG_MAX) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(fake_log[fake_log_n], sizeof fake_log[0], fmt, ap);
    va_end(ap);
    fake_log_n++;
}

int fake_log_find(const char *prefix, int from)
{
    size_t n = strlen(prefix);
    for (int i = from < 0 ? 0 : from; i < fake_log_n; i++)
        if (!strncmp(fake_log[i], prefix, n)) return i;
    return -1;
}

void fake_reset(void)
{
    fake_now_us = 1000000;      /* uptime 1 s, so t0 is never 0 */
    s_runaway_at = fake_now_us + RUNAWAY_US;
    fake_time_step_us = 1;
    fake_timer_broken = false;
    fake_task_late_us = 0;
    fake_log_n = 0;
    fake_wire_pulses = 0;
    fake_wire_first_us = fake_wire_last_us = -1;
    fake_wire_max_w_us = 0;
    fake_wire_min_w_us = 0xFFFFFFFFu;
    fake_tx_end_max_us = -1;
    fake_rmt_violations = 0;
    fake_rmt_channels = 0;
    fake_rmt_transmits = 0;
    fake_timer_creates = fake_timer_starts = fake_timer_fired = 0;
    memset(fake_gpio_level, -1, sizeof fake_gpio_level);
    memset(fake_gpio_output, 0, sizeof fake_gpio_output);
    fake_spi_n = 0;
    fake_spi_clock_hz = fake_spi_mode = -1;
    fake_mcp_canstat_after_reset = 0x80;
    fake_mcp_canctrl_after_reset = 0x87;
    fake_spi_bus_init_result = ESP_OK;
    s_mcp_reset_done = 0;
    fake_out_n = 0;
    fake_out[0] = 0;
    memset(&fake_app_desc, 0, sizeof fake_app_desc);
    strcpy(fake_app_desc.project_name, "vtrux_jammer_x2_NOT_FOR_VEHICLE");
    strcpy(fake_app_desc.version, "2026.11.12.2-1-gabcdef0");
    strcpy(fake_app_desc.idf_ver, "v5.4.2");
    fake_tasks_n = 0;
    s_ch = NULL;
    memset(&s_batch, 0, sizeof s_batch);
    memset(&s_timer, 0, sizeof s_timer);
    s_queues_n = 0;
    s_mutexes_n = 0;
    s_bits = 0;
    s_firing = 0;
    memset(s_sched, 0, sizeof s_sched);
}

void fake_error_check(esp_err_t e, const char *expr)
{
    if (e != ESP_OK) {
        fprintf(stderr, "ESP_ERROR_CHECK failed: %s = 0x%x\n", expr, e);
        abort();
    }
}

/* ---- the clock and its events ---- */

static void wire_add(int64_t cut)
{
    long emitted;
    if (cut >= s_batch.end) {
        emitted = s_batch.count;
    } else if (cut <= s_batch.start) {
        emitted = 0;
    } else {
        int64_t span = cut - s_batch.start;
        emitted = (long)((span + s_batch.period - 1) / s_batch.period);
        if (emitted > s_batch.count) emitted = s_batch.count;
    }
    if (emitted > 0) {
        if (fake_wire_first_us < 0) fake_wire_first_us = s_batch.start;
        fake_wire_last_us = s_batch.start
                            + (int64_t)(emitted - 1) * s_batch.period;
        fake_wire_pulses += emitted;
    }
    s_batch.on = false;
}

enum { EV_NONE, EV_BATCH, EV_TIMER, EV_SCHED };

static int next_event(int64_t *t, int *idx)
{
    int kind = EV_NONE;
    if (s_batch.on && s_ch && s_ch->enabled) {
        *t = s_batch.end;
        kind = EV_BATCH;
    }
    if (s_timer.active && !fake_timer_broken
            && (kind == EV_NONE || s_timer.expiry < *t)) {
        *t = s_timer.expiry;
        kind = EV_TIMER;
    }
    for (int i = 0; i < SCHED_MAX; i++) {
        if (s_sched[i].pending && (kind == EV_NONE || s_sched[i].t < *t)) {
            *t = s_sched[i].t;
            *idx = i;
            kind = EV_SCHED;
        }
    }
    return kind;
}

static void fire(int kind, int idx)
{
    s_firing++;
    if (kind == EV_BATCH) {
        wire_add(s_batch.end);
        if (s_ch->on_done) {
            rmt_tx_done_event_data_t ev = {1};
            s_ch->on_done(s_ch, &ev, s_ch->ctx);
        }
    } else if (kind == EV_TIMER) {
        s_timer.active = false;
        fake_timer_fired++;
        logf_("timer_fired");
        s_timer.cb(s_timer.arg);
    } else if (kind == EV_SCHED) {
        s_sched[idx].pending = false;
        s_sched[idx].fn(s_sched[idx].arg);
    }
    s_firing--;
}

static void run_until(int64_t t)
{
    if (t > s_runaway_at) {
        printf("FAIL: RUNAWAY -- emulated time passed %lld s; something "
               "never stopped\n", (long long)(RUNAWAY_US / 1000000));
        exit(3);
    }
    for (;;) {
        int64_t te = 0;
        int idx = -1;
        int kind = next_event(&te, &idx);
        if (kind == EV_NONE || te > t) break;
        if (te > fake_now_us) fake_now_us = te;
        fire(kind, idx);
    }
    if (t > fake_now_us) fake_now_us = t;
}

void fake_at(int64_t t_us, void (*fn)(void *), void *arg)
{
    for (int i = 0; i < SCHED_MAX; i++) {
        if (!s_sched[i].pending) {
            s_sched[i].t = t_us;
            s_sched[i].fn = fn;
            s_sched[i].arg = arg;
            s_sched[i].pending = true;
            return;
        }
    }
    fprintf(stderr, "fake_at: no room\n");
    abort();
}

int64_t esp_timer_get_time(void)
{
    if (!s_firing) run_until(fake_now_us + fake_time_step_us);
    return fake_now_us;
}

/* ---- GPIO ---- */

esp_err_t gpio_set_level(gpio_num_t gpio, uint32_t level)
{
    logf_("gpio_set_level %d %u", gpio, level);
    if (gpio >= 0 && gpio < 64) fake_gpio_level[gpio] = (int)level;
    return ESP_OK;
}

esp_err_t gpio_set_direction(gpio_num_t gpio, gpio_mode_t mode)
{
    logf_("gpio_set_direction %d %s", gpio,
          mode == GPIO_MODE_OUTPUT ? "out" : "in");
    if (gpio >= 0 && gpio < 64)
        fake_gpio_output[gpio] = mode == GPIO_MODE_OUTPUT;
    return ESP_OK;
}

/* ---- FreeRTOS ---- */

SemaphoreHandle_t xSemaphoreCreateMutex(void)
{
    struct fake_mutex *m = &s_mutexes[s_mutexes_n++];
    m->held = false;
    return m;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t m, TickType_t wait)
{
    if (m->held) {
        /* One thread: a held mutex would never be given back. */
        if (wait == portMAX_DELAY) fake_rmt_violations++;
        return pdFALSE;
    }
    m->held = true;
    return pdTRUE;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t m)
{
    m->held = false;
    return pdTRUE;
}

QueueHandle_t xQueueCreate(UBaseType_t len, UBaseType_t item)
{
    (void)len;
    struct fake_queue *q = &s_queues[s_queues_n++];
    q->item = item;
    q->full = false;
    return q;
}

BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{
    (void)wait;
    if (q->full) return pdFALSE;
    memcpy(q->buf, item, q->item);
    q->full = true;
    logf_("queue_send");
    return pdTRUE;
}

BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait)
{
    (void)wait;
    if (!q->full) return pdFALSE;
    memcpy(item, q->buf, q->item);
    q->full = false;
    return pdTRUE;
}

BaseType_t xTaskNotify(TaskHandle_t t, uint32_t v, eNotifyAction a)
{
    (void)t;
    (void)a;
    s_bits |= v;
    return pdPASS;
}

BaseType_t xTaskNotifyFromISR(TaskHandle_t t, uint32_t v, eNotifyAction a,
                              BaseType_t *woken)
{
    (void)t;
    (void)a;
    s_bits |= v;
    if (woken) *woken = pdTRUE;
    return pdPASS;
}

BaseType_t xTaskNotifyWait(uint32_t clear_in, uint32_t clear_out,
                           uint32_t *value, TickType_t wait)
{
    s_bits &= ~clear_in;
    if (!s_bits) {
        bool forever = wait == portMAX_DELAY;
        int64_t deadline = fake_now_us + (int64_t)wait * 1000;
        for (;;) {
            int64_t te = 0;
            int idx = -1;
            int kind = next_event(&te, &idx);
            if (kind == EV_NONE || (!forever && te > deadline)) {
                if (forever) fake_rmt_violations++;  /* would never wake */
                else run_until(deadline);
                break;
            }
            run_until(te);
            if (s_bits) break;
        }
    }
    if (fake_task_late_us) run_until(fake_now_us + fake_task_late_us);
    uint32_t got = s_bits;
    if (value) *value = got;
    s_bits &= ~clear_out;
    return got ? pdTRUE : pdFALSE;
}

BaseType_t xTaskNotifyStateClear(TaskHandle_t t)
{
    (void)t;
    s_bits = 0;
    return pdTRUE;
}

uint32_t ulTaskNotifyValueClear(TaskHandle_t t, uint32_t bits)
{
    (void)t;
    uint32_t old = s_bits;
    s_bits &= ~bits;
    return old;
}

BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char *name,
                                   uint32_t stack, void *arg,
                                   UBaseType_t prio, TaskHandle_t *out,
                                   int core)
{
    (void)stack;
    (void)arg;
    if (fake_tasks_n >= FAKE_TASKS_MAX) return pdFALSE;
    int i = fake_tasks_n++;
    fake_task_name[i] = name;
    fake_task_fn[i] = fn;
    fake_task_prio[i] = (int)prio;
    fake_task_core[i] = core;
    logf_("task %s", name);
    if (out) *out = &s_tasks[i];
    return pdPASS;
}

void vTaskDelay(TickType_t ticks)
{
    logf_("delay %u", ticks);
    run_until(fake_now_us + (int64_t)ticks * 1000);
}

/* ---- RMT ---- */

esp_err_t rmt_new_tx_channel(const rmt_tx_channel_config_t *cfg,
                             rmt_channel_handle_t *out)
{
    if (s_ch) fake_rmt_violations++;            /* a second live channel */
    if (!cfg->flags.invert_out || cfg->resolution_hz != 1000000)
        fake_rmt_violations++;
    struct fake_rmt *ch = calloc(1, sizeof *ch);
    ch->cfg = *cfg;
    /* The channel takes the pin through the GPIO matrix: the GPIO
     * driver's level no longer drives it. */
    if (cfg->gpio_num >= 0 && cfg->gpio_num < 64)
        fake_gpio_output[cfg->gpio_num] = 0;
    s_ch = ch;
    fake_rmt_channels++;
    logf_("rmt_new %d", cfg->gpio_num);
    *out = ch;
    return ESP_OK;
}

esp_err_t rmt_tx_register_event_callbacks(rmt_channel_handle_t ch,
                                          const rmt_tx_event_callbacks_t *cbs,
                                          void *ctx)
{
    ch->on_done = cbs->on_trans_done;
    ch->ctx = ctx;
    return ESP_OK;
}

esp_err_t rmt_enable(rmt_channel_handle_t ch)
{
    if (ch->enabled) return ESP_ERR_INVALID_STATE;
    ch->enabled = true;
    return ESP_OK;
}

esp_err_t rmt_disable(rmt_channel_handle_t ch)
{
    if (!ch->enabled) return ESP_ERR_INVALID_STATE;
    if (s_batch.on) wire_add(fake_now_us);
    ch->enabled = false;
    logf_("rmt_disable");
    return ESP_OK;
}

esp_err_t rmt_del_channel(rmt_channel_handle_t ch)
{
    if (ch->enabled) {
        fake_rmt_violations++;
        return ESP_ERR_INVALID_STATE;
    }
    if (s_ch == ch) s_ch = NULL;
    /* Deleting the channel leaves the pin an input (ESP-IDF disables its
     * output), held recessive only by the transceiver's pull-up until it is
     * driven again. */
    if (ch->cfg.gpio_num >= 0 && ch->cfg.gpio_num < 64)
        fake_gpio_output[ch->cfg.gpio_num] = 0;
    free(ch);
    fake_rmt_channels--;
    logf_("rmt_del");
    return ESP_OK;
}

esp_err_t rmt_new_copy_encoder(const rmt_copy_encoder_config_t *cfg,
                               rmt_encoder_handle_t *out)
{
    (void)cfg;
    *out = &s_enc;
    return ESP_OK;
}

esp_err_t rmt_encoder_reset(rmt_encoder_handle_t enc)
{
    enc->resets++;
    return ESP_OK;
}

esp_err_t rmt_transmit(rmt_channel_handle_t ch, rmt_encoder_handle_t enc,
                       const void *data, size_t size,
                       const rmt_transmit_config_t *cfg)
{
    (void)enc;
    if (!ch->enabled) return ESP_ERR_INVALID_STATE;
    if (size != sizeof(rmt_symbol_word_t)) fake_rmt_violations++;
    rmt_symbol_word_t sym;
    memcpy(&sym, data, sizeof sym);
    if (s_batch.on) fake_rmt_violations++;      /* overlapping batches */
    if (sym.level0 != 1 || sym.level1 != 0) fake_rmt_violations++;
    if (cfg->flags.eot_level != 0) fake_rmt_violations++;
    if (cfg->loop_count < 0) fake_rmt_violations++;  /* endless */
    fake_rmt_transmits++;
    s_batch.on = true;
    s_batch.start = fake_now_us;
    s_batch.w = sym.duration0;
    s_batch.period = (uint32_t)sym.duration0 + sym.duration1;
    s_batch.count = cfg->loop_count <= 0 ? 1 : cfg->loop_count;
    s_batch.end = s_batch.start + (int64_t)s_batch.count * s_batch.period;
    if (s_batch.end > fake_tx_end_max_us) fake_tx_end_max_us = s_batch.end;
    if (sym.duration0 > fake_wire_max_w_us) fake_wire_max_w_us = sym.duration0;
    if (sym.duration0 < fake_wire_min_w_us) fake_wire_min_w_us = sym.duration0;
    logf_("rmt_transmit %ld", s_batch.count);
    return ESP_OK;
}

/* ---- esp_timer ---- */

esp_err_t esp_timer_create(const esp_timer_create_args_t *a,
                           esp_timer_handle_t *out)
{
    fake_timer_creates++;
    s_timer.cb = a->callback;
    s_timer.arg = a->arg;
    s_timer.active = false;
    logf_("timer_create %s", a->name ? a->name : "");
    *out = &s_timer;
    return ESP_OK;
}

esp_err_t esp_timer_start_once(esp_timer_handle_t t, uint64_t us)
{
    if (t->active) return ESP_ERR_INVALID_STATE;
    fake_timer_starts++;
    t->active = true;
    t->expiry = fake_now_us + (int64_t)us;
    logf_("timer_start %llu", (unsigned long long)us);
    return ESP_OK;
}

esp_err_t esp_timer_stop(esp_timer_handle_t t)
{
    if (!t->active) return ESP_ERR_INVALID_STATE;
    t->active = false;
    return ESP_OK;
}

/* ---- SPI and the MCP2515 ---- */

esp_err_t spi_bus_initialize(spi_host_device_t host, const spi_bus_config_t *b,
                             int dma)
{
    logf_("spi_bus_initialize %d sck=%d miso=%d mosi=%d dma=%d", host,
          b->sclk_io_num, b->miso_io_num, b->mosi_io_num, dma);
    return fake_spi_bus_init_result;
}

esp_err_t spi_bus_add_device(spi_host_device_t host,
                             const spi_device_interface_config_t *d,
                             spi_device_handle_t *out)
{
    fake_spi_clock_hz = d->clock_speed_hz;
    fake_spi_mode = d->mode;
    logf_("spi_add_device %d cs=%d", host, d->spics_io_num);
    *out = &s_spidev;
    return ESP_OK;
}

esp_err_t spi_device_polling_transmit(spi_device_handle_t d,
                                      spi_transaction_t *t)
{
    (void)d;
    size_t nz = t->length / 8;
    int n = nz > 64 ? 64 : (int)nz;
    const uint8_t *tx = t->tx_buffer;
    uint8_t *rx = t->rx_buffer;
    if (fake_spi_n < FAKE_SPI_MAX) {
        int k = n < 8 ? n : 8;
        memcpy(fake_spi_tx[fake_spi_n], tx, (size_t)k);
        fake_spi_len[fake_spi_n] = n;
        fake_spi_t[fake_spi_n] = fake_now_us;
        fake_spi_n++;
    }
    logf_("spi %02x", n > 0 ? tx[0] : 0);
    if (rx) memset(rx, 0xFF, nz);
    if (n >= 1 && tx[0] == 0xC0) s_mcp_reset_done = 1;
    if (n >= 3 && tx[0] == 0x03 && rx) {
        uint8_t v = 0;
        if (tx[1] == 0x0E) v = s_mcp_reset_done ? fake_mcp_canstat_after_reset : 0x00;
        if (tx[1] == 0x0F) v = s_mcp_reset_done ? fake_mcp_canctrl_after_reset : 0x07;
        rx[2] = v;
    }
    return ESP_OK;
}

/* ---- USB Serial/JTAG ---- */

esp_err_t usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t *c)
{
    (void)c;
    logf_("usb_install");
    return ESP_OK;
}

int usb_serial_jtag_read_bytes(void *buf, uint32_t n, TickType_t wait)
{
    (void)buf;
    (void)n;
    (void)wait;
    return 0;
}

int usb_serial_jtag_write_bytes(const void *buf, size_t n, TickType_t wait)
{
    (void)wait;
    if (fake_out_n + n >= FAKE_OUT_MAX) n = FAKE_OUT_MAX - 1 - fake_out_n;
    memcpy(fake_out + fake_out_n, buf, n);
    fake_out_n += n;
    fake_out[fake_out_n] = 0;
    return (int)n;
}

/* ---- app description, reset reason, board ---- */

const esp_app_desc_t *esp_app_get_description(void)
{
    return &fake_app_desc;
}

int esp_app_get_elf_sha256(char *dst, size_t size)
{
    size_t n = size - 1 < 64 ? size - 1 : 64;
    memset(dst, 'e', n);
    dst[n] = 0;
    return (int)n;
}

esp_reset_reason_t esp_reset_reason(void)
{
    return ESP_RST_POWERON;
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    if (port != 0 || !out) return ESP_ERR_INVALID_ARG;
    out->tx = 7;
    out->rx = 6;
    return ESP_OK;
}

esp_err_t board_spi_can(int port, board_spi_can_t *out)
{
    if (port != 1 || !out) return ESP_ERR_INVALID_ARG;
    out->controller = BOARD_SPI_CAN_MCP2515;
    out->host = SPI3_HOST;
    out->cs = 10;
    out->sck = 12;
    out->miso = 13;
    out->mosi = 11;
    out->irq = 3;
    out->crystal_hz = 16000000;
    return ESP_OK;
}
