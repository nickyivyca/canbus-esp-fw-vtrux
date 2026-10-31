/* The scripted stub; see stub.h. */
#include "stub.h"

#include <stdio.h>
#include <string.h>

stub_t stub;
twai_dev_t stub_twai_dev;
int stub_log_e, stub_log_w;

static void call(const char *name)
{
    if (stub.n_calls < STUB_MAX_CALLS)
    {
        stub.calls[stub.n_calls++] = name;
    }
}

void stub_reset(void)
{
    memset(&stub, 0, sizeof(stub));
    memset(&stub_twai_dev, 0, sizeof(stub_twai_dev));
    stub_log_e = stub_log_w = 0;
    stub.pins.tx = 5;
    stub.pins.rx = 4;
    stub.status[0].state = TWAI_STATE_RUNNING;
    stub.n_status = 1;
}

void stub_status(int n, const twai_status_info_t *seq)
{
    for (int i = 0; i < n && i < STUB_MAX_STATUS; i++)
    {
        stub.status[i] = seq[i];
        stub.status_ret[i] = ESP_OK;
    }
    stub.n_status = n;
    stub.status_i = 0;
}

const char *stub_calls(void)
{
    static char buf[1024];
    size_t n = 0;
    buf[0] = '\0';
    for (int i = 0; i < stub.n_calls && n < sizeof(buf); i++)
    {
        n += (size_t)snprintf(buf + n, sizeof(buf) - n, "%s%s",
                              i ? " " : "", stub.calls[i]);
    }
    return buf;
}

int stub_count(const char *name)
{
    int c = 0;
    for (int i = 0; i < stub.n_calls; i++)
    {
        c += strcmp(stub.calls[i], name) == 0;
    }
    return c;
}

const char *esp_err_to_name(esp_err_t e)
{
    (void)e;
    return "err";
}

void stub_error_check_failed(esp_err_t e)
{
    call("ABORT");
    stub.abort_err = e;
    if (stub.abort_to != NULL)
    {
        longjmp(*stub.abort_to, 1);
    }
}

TickType_t xTaskGetTickCount(void)
{
    return stub.tick;
}

esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    call("pins");
    stub.board_port = port;
    *out = stub.pins;
    return stub.pins_ret;
}

esp_err_t board_can_standby(int port, bool on)
{
    (void)port;
    call(on ? "standby_on" : "standby_off");
    if (stub.n_standby < STUB_MAX_CALLS)
    {
        stub.standby[stub.n_standby++] = on;
    }
    return ESP_OK;
}

esp_err_t twai_driver_install(const twai_general_config_t *g,
                              const twai_timing_config_t *t,
                              const twai_filter_config_t *f)
{
    call("install");
    stub.g = *g;
    stub.t = *t;
    stub.f = *f;
    return stub.install_ret;
}

esp_err_t twai_driver_uninstall(void)
{
    call("uninstall");
    return stub.uninstall_ret;
}

esp_err_t twai_start(void)
{
    call("start");
    return stub.start_ret;
}

esp_err_t twai_stop(void)
{
    call("stop");
    return stub.stop_ret;
}

esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks_to_wait)
{
    call("transmit");
    stub.tx = *message;
    stub.tx_wait = ticks_to_wait;
    return stub.transmit_ret;
}

esp_err_t twai_receive(twai_message_t *message, TickType_t ticks_to_wait)
{
    call("receive");
    if (stub.rx_i < STUB_MAX_RX)
    {
        stub.rx_waits[stub.rx_i] = ticks_to_wait;
    }
    stub.tick += stub.rx_tick_step;
    if (stub.rx_i >= stub.n_rx)
    {
        return ESP_ERR_TIMEOUT;
    }
    const int i = stub.rx_i++;
    *message = stub.rx[i];
    return stub.rx_ret[i];
}

esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks_to_wait)
{
    (void)ticks_to_wait;
    call("read_alerts");
    *alerts = stub.alerts;
    stub.alerts = 0;
    return stub.read_alerts_ret;
}

esp_err_t twai_reconfigure_alerts(uint32_t alerts_enabled, uint32_t *current_alerts)
{
    (void)current_alerts;
    call("alerts");
    stub.alerts_enabled = alerts_enabled;
    return stub.alerts_ret;
}

esp_err_t twai_initiate_recovery(void)
{
    call("recovery");
    return stub.recovery_ret;
}

esp_err_t twai_get_status_info(twai_status_info_t *status_info)
{
    call("status");
    const int i = (stub.status_i < stub.n_status) ? stub.status_i++
                                                  : stub.n_status - 1;
    *status_info = stub.status[i];
    return stub.status_ret[i];
}

esp_err_t twai_clear_receive_queue(void)
{
    call("clear_rx");
    return stub.clear_rx_ret;
}
