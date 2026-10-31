/*
 * vtrux/twai_port, T8 on a chip with no measured register file (the S3
 * among them): the transmit buffer's state and the abort report themselves
 * unsupported and touch no register. A chip gets its own file,
 * twai_port_ll_<chip>.c, once the measurement gen-inhibit spec 5.2 item 9
 * made on the C3 has been made on it (T8; user, 2026-10-31).
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
