/*
 * spec_layer.c -- see spec_layer.h. Gen-inhibit tester, 2026-10-20.
 *
 * Linked with -Wl,--wrap= for twai_receive, twai_get_status_info,
 * twai_read_alerts, twai_reconfigure_alerts and twai_driver_install, so the
 * product's calls (main/can.c, main/gen_inhibit.c) reach these first and the
 * implementor's fake_twai.c second. Calls inside fake_twai.c itself are not
 * wrapped, which is what is wanted: the layer sits between the product and the
 * driver model, as the controller's view of the bus would.
 */
#include "spec_layer.h"

#include "fake_twai.h"
#include "driver/twai.h"

#include <string.h>

#define AIR_NEVER  ((int64_t)1 << 50)   /* a frame this long never completes */
#define AIR_NORMAL 200                  /* the fake's default, us */

static struct
{
    bool     silent, bus_off;
    int64_t  retry_us;
    int64_t  retry_t0;          /* -1: no frame waiting yet in this silence */
    int64_t  tenth_ref, tenth_us; /* tenth_us < 0: retry_us is fixed */
    int      retries_this_wait; /* errors injected since retry_t0 */
    uint32_t base_errors;       /* bus_error_count before this silence */
    uint32_t tec;
    uint32_t pending, enabled, delivered;
    int      recovery_calls;
    int      n_rx, n_err;
    sl_rx_t  rx[SL_MAX_RX];
    int64_t  err_t[SL_MAX_ERR];
    void   (*next)(void);
} L;

esp_err_t __real_twai_receive(twai_message_t *m, TickType_t ticks);
esp_err_t __real_twai_get_status_info(twai_status_info_t *s);
esp_err_t __real_twai_read_alerts(uint32_t *alerts, TickType_t ticks);
esp_err_t __real_twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev);
esp_err_t __real_twai_driver_install(const twai_general_config_t *g,
                                     const twai_timing_config_t *t,
                                     const twai_filter_config_t *f);

static void inject_error(int64_t t)
{
    if (L.n_err < SL_MAX_ERR) L.err_t[L.n_err] = t;
    L.n_err++;
    ft_set_bus_errors(L.base_errors + (uint32_t)L.n_err);
    L.pending |= SL_ALERT_BUS_ERROR;
    if (L.tec < 128)
    {
        const uint32_t before = L.tec;
        L.tec += 8;
        if (before < 96 && L.tec >= 96) L.pending |= SL_ALERT_ABOVE_ERR_WARN;
        if (L.tec >= 128) L.pending |= SL_ALERT_ERR_PASS;
    }
}

void sl_step(void)
{
    if (L.silent && !L.bus_off)
    {
        twai_status_info_t s;
        __real_twai_get_status_info(&s);
        const int64_t now = ft_now();
        if (L.retry_t0 < 0 && s.msgs_to_tx > 0)
        {
            L.retry_t0 = now;           /* a frame is waiting: it starts retrying */
            if (L.tenth_us >= 0)
            {
                L.retry_us = (L.tenth_ref + L.tenth_us - now) / 10;
                if (L.retry_us < 1) L.retry_us = 1;
            }
            L.retries_this_wait = 0;
        }
        if (L.retry_t0 >= 0)
        {
            /* every retry whose time has come, at its own time, not at now */
            while (L.retry_t0 + (int64_t)(L.retries_this_wait + 1) * L.retry_us <= now)
            {
                L.retries_this_wait++;
                inject_error(L.retry_t0 + (int64_t)L.retries_this_wait * L.retry_us);
            }
        }
    }
    if (L.next) L.next();
}

void sl_reset(void (*next)(void))
{
    memset(&L, 0, sizeof(L));
    L.retry_t0 = -1;
    L.next = next;
    ft_set_bus_errors(0);
    ft_set_step_hook(sl_step);
}

void sl_silence(int64_t retry_us)
{
    twai_status_info_t s;
    __real_twai_get_status_info(&s);
    L.base_errors = s.bus_error_count - (uint32_t)L.n_err;
    L.silent = true;
    L.retry_us = retry_us;
    L.retry_t0 = -1;
    L.tenth_us = -1;
    ft_set_air_time(AIR_NEVER);
}

void sl_silence_tenth_at(int64_t t_ref, int64_t tenth_us)
{
    sl_silence(1000);
    L.tenth_ref = t_ref;
    L.tenth_us = tenth_us;
}

int64_t sl_retry_us(void) { return L.retry_us; }

void sl_bus_off(void)
{
    sl_step();                  /* the errors up to now, then no more */
    L.bus_off = true;
    L.tec = 256;
    L.pending |= SL_ALERT_BUS_OFF;
    ft_bus_off();
}

void sl_nodes_back(void)
{
    sl_step();
    L.silent = false;
    L.retry_t0 = -1;
    ft_set_air_time(AIR_NORMAL);
}

/* ------------------------------------------------------------ the wraps -- */

esp_err_t __wrap_twai_receive(twai_message_t *m, TickType_t ticks)
{
    const esp_err_t r = __real_twai_receive(m, ticks);
    if (L.n_rx < SL_MAX_RX)
    {
        L.rx[L.n_rx].t = ft_now();
        L.rx[L.n_rx].ok = (r == ESP_OK);
        L.rx[L.n_rx].id = (r == ESP_OK) ? m->identifier : 0;
    }
    L.n_rx++;
    return r;
}

esp_err_t __wrap_twai_get_status_info(twai_status_info_t *s)
{
    sl_step();                  /* the bus as of this instant */
    const esp_err_t r = __real_twai_get_status_info(s);
    if (r == ESP_OK && (L.silent || L.bus_off))
    {
        s->tx_error_counter = L.tec;
        s->rx_error_counter = 0;
    }
    return r;
}

esp_err_t __wrap_twai_read_alerts(uint32_t *alerts, TickType_t ticks)
{
    sl_step();
    uint32_t a = 0;
    const esp_err_t r = __real_twai_read_alerts(&a, ticks);
    const uint32_t mine = L.pending & L.enabled;
    L.pending &= ~mine;
    L.delivered |= mine;
    if (r == ESP_OK || mine)
    {
        *alerts = (r == ESP_OK ? a : 0) | mine;
        return ESP_OK;
    }
    return r;
}

esp_err_t __wrap_twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev)
{
    const esp_err_t r = __real_twai_reconfigure_alerts(alerts, prev);
    if (r == ESP_OK) L.enabled = alerts;
    return r;
}

esp_err_t __wrap_twai_driver_install(const twai_general_config_t *g,
                                     const twai_timing_config_t *t,
                                     const twai_filter_config_t *f)
{
    const esp_err_t r = __real_twai_driver_install(g, t, f);
    if (r == ESP_OK && g) L.enabled = g->alerts_enabled;
    if (r == ESP_OK && L.bus_off)
    {
        /* an install ends a bus-off in the fake, as in IDF 5.4.1 */
        L.bus_off = false;
        L.tec = L.silent ? L.tec : 0;
    }
    return r;
}

/* Not in the implementor's mock: defined here so a call is counted, not a
 * link error. Spec 7: the device starts no bus-off recovery. */
esp_err_t twai_initiate_recovery(void)
{
    L.recovery_calls++;
    return ESP_OK;
}

/* ------------------------------------------------------------ readers -- */

int            sl_rx_count(void)          { return L.n_rx < SL_MAX_RX ? L.n_rx : SL_MAX_RX; }
const sl_rx_t *sl_rx(int i)               { return &L.rx[i]; }
int            sl_err_count(void)         { return L.n_err; }
int64_t        sl_err_time(int i)         { return i < SL_MAX_ERR ? L.err_t[i] : -1; }
uint32_t       sl_tec(void)               { return L.tec; }
int            sl_recovery_calls(void)    { return L.recovery_calls; }
uint32_t       sl_alerts_enabled(void)    { return L.enabled; }
uint32_t       sl_alerts_delivered(void)  { return L.delivered; }
