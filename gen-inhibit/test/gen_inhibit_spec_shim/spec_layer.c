/*
 * spec_layer.c -- see spec_layer.h. Gen-inhibit tester, 2026-10-20; onto
 * twai_port's host fake at port step 1b, 2026-10-31.
 *
 * Linked with -Wl,--wrap= for twai_receive, twai_get_status_info,
 * twai_read_alerts, twai_reconfigure_alerts and twai_initiate_recovery, so the
 * product's calls (can_port, twai_port, main/gen_inhibit.c) reach these first
 * and the shared fake (shared/twai_port/test/twai_fake.c) second. Calls inside
 * twai_fake.c itself are not wrapped, which is what is wanted: the layer sits
 * between the product and the driver model, as the controller's view of the
 * bus would. twai_driver_install is shim_harness.c's wrap, so the layer sees
 * an install by the fake's count instead (twai_fake_install_count).
 */
#include "spec_layer.h"

#include "shim_harness.h"
#include "driver/twai.h"

#include <string.h>

/* The alert bits the layer causes: the fake raises BUS_ERROR (each error) and
 * BUS_OFF itself, the layer the two TEC thresholds. */
#define SL_INJECTED (SL_ALERT_BUS_ERROR | SL_ALERT_ABOVE_ERR_WARN | SL_ALERT_ERR_PASS | SL_ALERT_BUS_OFF)

static struct
{
    bool     silent, bus_off;
    int64_t  retry_us;
    int64_t  retry_t0;          /* -1: no frame waiting yet in this silence */
    int64_t  tenth_ref, tenth_us; /* tenth_us < 0: retry_us is fixed */
    int64_t  passive_us;        /* > 0: the period once TEC is 128 (error passive) */
    int64_t  last_err_t;        /* the last error's time in this wait (retry_t0 before the first) */
    int      retries_this_wait; /* errors injected since retry_t0 */
    uint32_t base_errors;       /* bus_error_count before this silence */
    uint32_t tec;
    uint32_t pending, delivered;
    int      installs;          /* the fake's install count when last looked */
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
esp_err_t __real_twai_initiate_recovery(void);

/* TEC as the status reports it: the fake's own counters, which nothing else
 * moves (REC stays 0 with nothing received). */
static void set_tec(uint32_t tec)
{
    L.tec = tec;
    twai_fake_set_error_counters(tec, 0);
}

/* An alert the layer raises, latched only if enabled when it occurs, as the
 * driver's are (the fake's raise_alert, twai.c:111). */
static void raise(uint32_t a)
{
    L.pending |= a & twai_fake_alerts_enabled();
}

static void inject_error(int64_t t)
{
    if (L.n_err < SL_MAX_ERR) L.err_t[L.n_err] = t;
    L.n_err++;
    ft_set_bus_errors(L.base_errors + (uint32_t)L.n_err);  /* the fake's count and its BUS_ERROR alert */
    if (L.tec < 128)
    {
        const uint32_t before = L.tec;
        set_tec(L.tec + 8);
        if (before < 96 && L.tec >= 96) raise(SL_ALERT_ABOVE_ERR_WARN);
        if (L.tec >= 128) raise(SL_ALERT_ERR_PASS);
    }
}

/* An install ends a bus-off: the driver can only be installed again after an
 * uninstall, which is allowed in bus-off (IDF 5.4.1, twai.c:558). */
static void see_installs(void)
{
    const int n = twai_fake_install_count();
    if (n == L.installs) return;
    L.installs = n;
    if (L.bus_off)
    {
        L.bus_off = false;
        set_tec(L.silent ? L.tec : 0);
    }
}

void sl_step(void)
{
    see_installs();
    if (L.silent && !L.bus_off)
    {
        twai_status_info_t s;
        twai_fake_driver_status(&s);    /* not a driver call: none counted against the product */
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
            L.last_err_t = now;
        }
        if (L.retry_t0 >= 0)
        {
            /* every retry whose time has come, at its own time, not at now; the
             * period is passive_us once TEC is 128, when that is set */
            for (;;)
            {
                const int64_t gap = (L.passive_us > 0 && L.tec >= 128) ? L.passive_us : L.retry_us;
                if (L.last_err_t + gap > now) break;
                L.last_err_t += gap;
                L.retries_this_wait++;
                inject_error(L.last_err_t);
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
    L.installs = twai_fake_install_count();
    ft_set_bus_errors(0);
    set_tec(0);
    ft_set_step_hook(sl_step);
}

void sl_silence(int64_t retry_us)
{
    twai_status_info_t s;
    twai_fake_driver_status(&s);
    L.base_errors = s.bus_error_count - (uint32_t)L.n_err;
    L.silent = true;
    L.retry_us = retry_us;
    L.retry_t0 = -1;
    L.passive_us = 0;
    L.tenth_us = -1;
    twai_fake_set_acked(false);
}

void sl_silence_tenth_at(int64_t t_ref, int64_t tenth_us)
{
    sl_silence(1000);
    L.tenth_ref = t_ref;
    L.tenth_us = tenth_us;
}

void sl_silence_measured(int64_t active_us, int64_t passive_us)
{
    sl_silence(active_us);
    L.passive_us = passive_us;
}

int64_t sl_retry_us(void) { return L.retry_us; }

void sl_bus_off(void)
{
    sl_step();                  /* the errors up to now, then no more */
    L.bus_off = true;
    set_tec(256);
    ft_bus_off();               /* the fake's state and its BUS_OFF alert */
}

void sl_nodes_back(void)
{
    sl_step();
    L.silent = false;
    L.retry_t0 = -1;
    if (!L.bus_off) set_tec(0);
    twai_fake_set_acked(true);
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
    return __real_twai_get_status_info(s);
}

esp_err_t __wrap_twai_read_alerts(uint32_t *alerts, TickType_t ticks)
{
    sl_step();
    uint32_t a = 0;
    const uint32_t mine = L.pending;
    /* an alert of the layer's is pending: the driver's semaphore is given, so
     * the read does not wait */
    const esp_err_t r = __real_twai_read_alerts(&a, mine ? 0 : ticks);
    L.pending = 0;
    if (r == ESP_OK || mine)
    {
        *alerts = (r == ESP_OK ? a : 0) | mine;
        L.delivered |= *alerts & SL_INJECTED;
        return ESP_OK;
    }
    return r;
}

/* The driver hands back the alerts triggered and not yet read, and clears
 * them (twai.c:790-797); the layer's with them. */
esp_err_t __wrap_twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev)
{
    const esp_err_t r = __real_twai_reconfigure_alerts(alerts, prev);
    if (r == ESP_OK)
    {
        if (prev) *prev |= L.pending;
        L.pending = 0;
    }
    return r;
}

/* Counted, then the fake's own (spec 7: the device starts no bus-off
 * recovery, so a healthy run never gets here). */
esp_err_t __wrap_twai_initiate_recovery(void)
{
    L.recovery_calls++;
    return __real_twai_initiate_recovery();
}

/* ------------------------------------------------------------ readers -- */

int            sl_rx_count(void)          { return L.n_rx < SL_MAX_RX ? L.n_rx : SL_MAX_RX; }
const sl_rx_t *sl_rx(int i)               { return &L.rx[i]; }
int            sl_err_count(void)         { return L.n_err; }
int64_t        sl_err_time(int i)         { return i < SL_MAX_ERR ? L.err_t[i] : -1; }
uint32_t       sl_tec(void)               { return L.tec; }
int            sl_recovery_calls(void)    { return L.recovery_calls; }
uint32_t       sl_alerts_enabled(void)    { return twai_fake_alerts_enabled(); }
uint32_t       sl_alerts_delivered(void)  { return L.delivered; }
