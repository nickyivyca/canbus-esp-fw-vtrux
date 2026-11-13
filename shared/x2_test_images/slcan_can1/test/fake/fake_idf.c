/* Host fakes for slcan_can1's app test: see fake_idf.h. */
#include "fake_idf.h"

#include <string.h>

fake_t fake;

struct can_port_ops {
    int unused;
};
const can_port_ops_t twai_port_ops = {0};

void fake_reset(void)
{
    memset(&fake, 0, sizeof fake);
    fake.board_has_twai = true;
    fake.registered_port = -1;
}

void fake_host_sends(const char *s)
{
    size_t n = strlen(s);
    if (fake.in_pos == fake.in_len) fake.in_pos = fake.in_len = 0;
    if (fake.in_len + n > sizeof fake.in) n = sizeof fake.in - fake.in_len;
    memcpy(fake.in + fake.in_len, s, n);
    fake.in_len += n;
}

const char *fake_host_reads(void)
{
    fake.out[fake.out_len < sizeof fake.out ? fake.out_len : sizeof fake.out - 1] = '\0';
    return fake.out;
}

void fake_host_clear(void)
{
    fake.out_len = 0;
    fake.out[0] = '\0';
}

/* ---- freertos ---- */
BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char *name,
                                   uint32_t stack, void *arg, unsigned prio,
                                   TaskHandle_t *out, BaseType_t core)
{
    (void)fn; (void)name; (void)stack; (void)arg; (void)prio; (void)out; (void)core;
    fake.tasks++;
    return 1;
}

/* ---- USB ---- */
esp_err_t usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t *cfg)
{
    fake.usb_installs++;
    fake.usb_config = *cfg;
    return ESP_OK;
}

int usb_serial_jtag_read_bytes(void *buf, uint32_t length, TickType_t ticks_to_wait)
{
    (void)ticks_to_wait;
    size_t n = fake.in_len - fake.in_pos;
    if (n > length) n = length;
    memcpy(buf, fake.in + fake.in_pos, n);
    fake.in_pos += n;
    return (int)n;
}

int usb_serial_jtag_write_bytes(const void *src, size_t size, TickType_t ticks_to_wait)
{
    (void)ticks_to_wait;
    size_t room = sizeof fake.out - 1 - fake.out_len;
    if (size > room) size = room;
    memcpy(fake.out + fake.out_len, src, size);
    fake.out_len += size;
    return (int)size;
}

/* ---- the board ---- */
esp_err_t board_twai_pins(int port, board_twai_pins_t *out)
{
    if (port != 0 || !fake.board_has_twai) return ESP_ERR_NOT_FOUND;
    out->tx = 7;
    out->rx = 6;
    return ESP_OK;
}

/* ---- twai_port ---- */
void twai_port_config_defaults(twai_port_config_t *cfg)
{
    memset(cfg, 0, sizeof *cfg);
    cfg->board_port = 0;
    cfg->timing = FAKE_TIMING_500K;
    cfg->mode = CAN_PORT_MODE_NORMAL;
    cfg->filter.acceptance_mask = 0xFFFFFFFFu;
    cfg->filter.single_filter = true;
    cfg->rx_queue_len = 64;
    cfg->tx_queue_len = 32;
    cfg->recovery = TWAI_PORT_RECOVERY_NEVER;
    cfg->drop_rtr = false;
    cfg->on_install_fail = TWAI_PORT_INSTALL_FAIL_ABORT;
}

esp_err_t twai_port_setup(const twai_port_config_t *cfg)
{
    if (fake.up) return ESP_ERR_INVALID_STATE;
    fake.setups++;
    fake.config = *cfg;
    fake.mode = cfg->mode;
    return ESP_OK;
}

static void bring_up(void)
{
    fake.up = true;
    if (fake.ups < (int)(sizeof fake.up_mode / sizeof fake.up_mode[0])) {
        fake.up_mode[fake.ups] = fake.mode;
    }
    fake.ups++;
}

esp_err_t twai_port_teardown(void)
{
    fake.teardowns++;
    fake.up = false;
    return ESP_OK;
}

/* T4: recorded if down; if up, a teardown and a bring-up in the new mode. */
esp_err_t twai_port_set_mode(can_port_mode_t mode)
{
    if (mode == fake.mode) return ESP_OK;
    if (fake.up) {
        twai_port_teardown();
        fake.mode = mode;
        bring_up();
    } else {
        fake.mode = mode;
    }
    return ESP_OK;
}

/* ---- can_port, index 0 backed by the fake twai_port ---- */
esp_err_t can_port_register(int port, const can_port_ops_t *ops, void *ctx)
{
    (void)ctx;
    fake.registers++;
    fake.registered_port = port;
    fake.registered_ops = ops;
    return ESP_OK;
}

static bool backed(int port)
{
    return port == 0 && fake.registered_port == 0 &&
           fake.registered_ops == &twai_port_ops && fake.setups > 0;
}

esp_err_t can_port_up(int port)
{
    if (!backed(port)) return ESP_ERR_INVALID_STATE;
    if (!fake.up) bring_up();
    return ESP_OK;
}

esp_err_t can_port_receive(int port, twai_message_t *out, TickType_t wait)
{
    (void)wait;
    if (!backed(port) || !fake.up || fake.rx_pending == 0) return ESP_ERR_TIMEOUT;
    memset(out, 0, sizeof *out);
    out->identifier = 0x123;
    out->data_length_code = 0;
    fake.rx_pending--;
    fake.rx_taken++;
    return ESP_OK;
}

esp_err_t can_port_send(int port, const twai_message_t *m, TickType_t wait)
{
    (void)wait;
    if (!backed(port) || !fake.up || fake.mode != CAN_PORT_MODE_NORMAL) {
        fake.violations++;
        fake.status.tx_refused++;
        return ESP_ERR_INVALID_STATE;
    }
    if (fake.queue_full) {
        fake.status.tx_dropped++;
        return ESP_FAIL;
    }
    if (fake.sent < FAKE_MAX_FRAMES) fake.sent_frames[fake.sent] = *m;
    fake.sent++;
    fake.status.tx_frames++;
    return ESP_OK;
}

void can_port_service(int port)
{
    (void)port;
    fake.services++;
}

esp_err_t can_port_status(int port, can_port_status_t *out)
{
    if (!backed(port)) return ESP_ERR_INVALID_STATE;
    fake.status_reads++;
    *out = fake.status;
    return ESP_OK;
}
