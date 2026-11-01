/*
 * vtrux/twai_port, T8 and T12 on a chip with no measured register file (the
 * S3 among them): the transmit buffer's state, the abort and the restart stop
 * report themselves unsupported and touch no register. A chip gets its own
 * file, twai_port_ll_<chip>.c, once the measurement gen-inhibit spec 5.2 item
 * 9 made on the C3 has been made on it (T8; user, 2026-10-31).
 */
#include "twai_port.h"
#include "esp_attr.h"

twai_port_buf_t twai_port_buf_state(void)
{
    return TWAI_PORT_BUF_UNSUPPORTED;
}

IRAM_ATTR twai_port_buf_t twai_port_abort_if_awaiting(void)
{
    return TWAI_PORT_BUF_UNSUPPORTED;
}

IRAM_ATTR esp_err_t twai_port_restart_stop(can_port_restart_report_t *out)
{
    (void)out;
    return ESP_ERR_NOT_SUPPORTED;
}
