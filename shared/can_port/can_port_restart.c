/*
 * vtrux/can_port C7: the restart stop, for the restart path only
 * (shared/restart_guard G3), with interrupts already off.
 *
 * IT DISPATCHES BY THE BOARD, NOT BY THE REGISTRATION. A restart can come
 * before any backend has registered, from a crash in start-up or one of
 * call_start_cpu0's early-boot failures, and a controller left running by
 * the boot before is on the bus all the same. So the port's kind comes from
 * the board (shared/boards B5), and the call goes to that kind's driver.
 *
 * THE TWAI DRIVER IS FOUND BY THE BUILD (CMakeLists.txt): twai_port requires
 * can_port, so can_port cannot name it as a dependency of its own. When the
 * build has vtrux/twai_port, CMakeLists.txt links it in and defines
 * CAN_PORT_HAVE_TWAI_PORT=1; without it, a TWAI port answers "not
 * supported", as C7 says of a driver that is not in the build.
 *
 * IRAM, AND NOTHING THAT NEEDS THE CACHE: what it calls is in IRAM too (B5's
 * two board calls, twai_port T12), and there is no switch, whose jump table
 * would be in flash.
 */
#include <stddef.h>

#include "esp_attr.h"
#include "board.h"
#include "can_port.h"
#if CAN_PORT_HAVE_TWAI_PORT
#include "twai_port.h"
#endif

static IRAM_ATTR void report_clear(can_port_restart_report_t *r)
{
    r->clock_on        = false;
    r->held_in_reset   = false;
    r->was_in_reset    = false;
    r->status_at_entry = 0;
    r->tec             = 0;
    r->rec             = 0;
    r->status_at_reset = 0;
    r->wait_us         = 0;
    r->timed_out       = false;
    r->aborts          = 0;
    r->entered_reset   = false;
}

IRAM_ATTR esp_err_t can_port_restart_stop(int port, can_port_restart_report_t *out)
{
    if (out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    report_clear(out);
    if (port < 0 || port >= board_can_ports())
    {
        return ESP_ERR_INVALID_ARG;
    }
    const board_can_kind_t kind = board_can_kind(port);
#if CAN_PORT_HAVE_TWAI_PORT
    if (kind == BOARD_CAN_TWAI)
    {
        return twai_port_restart_stop(out);
    }
#endif
    /* An SPI port has no restart stop until mcp2515_port (stage (b)). */
    (void)kind;
    return ESP_ERR_NOT_SUPPORTED;
}
