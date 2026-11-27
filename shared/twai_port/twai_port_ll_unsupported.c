/*
 * vtrux/twai_port, T8, T12 and T14 on a chip with no register file of its
 * own: the transmit buffer's state, the abort and the restart stop report
 * themselves unsupported and touch no register, and the kept-driver switch is
 * refused at set-up. A chip gets its own file, twai_port_ll_<chip>.c, once
 * the measurement gen-inhibit spec 5.2 item 9 made on the C3 has been made on
 * it (T8; user, 2026-10-31). The C3 and the S3 have theirs.
 */
#include "twai_port.h"
#include "twai_port_ll.h"
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

bool twai_port_ll_switch_supported(void)
{
    return false;
}

twai_port_buf_t twai_port_ll_buf_state(void)
{
    return TWAI_PORT_BUF_UNSUPPORTED;
}

twai_port_buf_t twai_port_ll_abort_if_awaiting(void)
{
    return TWAI_PORT_BUF_UNSUPPORTED;
}

void twai_port_ll_switch_enter(twai_port_ll_entry_t *out)
{
    out->status = 0;
    out->buf = TWAI_PORT_BUF_UNSUPPORTED;
}

void twai_port_ll_switch_leave(bool listen_only, uint32_t rec,
                               uint32_t *rec_back, uint32_t *tec_back)
{
    (void)listen_only;
    (void)rec;
    *rec_back = 0;
    *tec_back = 0;
}

void twai_port_ll_counters(uint32_t *tec, uint32_t *rec)
{
    *tec = 0;
    *rec = 0;
}

/* R20's build marks (twai_port_ll.h): no test-only switch here. */
void twai_port_ll_build_marks(void)
{
}
