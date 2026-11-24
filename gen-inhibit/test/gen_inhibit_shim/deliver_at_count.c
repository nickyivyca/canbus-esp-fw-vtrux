/*
 * A FRAME THAT ARRIVES BETWEEN THE COMPLETION READ AND THE COUNT (spec 7 trip 7,
 * user 2026-11-23: the count is taken after the completion is seen, so it also
 * counts such a frame). In virtual time nothing arrives between two driver calls
 * in one worker turn, so the harness puts it there: once armed, the first
 * twai_port_status() that finds no frame of ours pending (dev_outstanding(), the
 * scheduler's completion read) marks the completion seen, and the next
 * can_msgs_to_rx() (the scheduler's count, dev_rx_waiting()) delivers the frame
 * first. A count read before the completion read finds the mark unset.
 *
 * --wrap, as for twai_driver_install (Makefile). The port's own calls to the
 * status read are inside twai_port.c and are not wrapped; the firmware's are.
 *
 * A FILE OF ITS OWN, linked only by this folder's Makefile, because the
 * tester's spec shim (gen_inhibit_spec_shim/build.sh) compiles shim_harness.c
 * without these --wrap flags, and the __real_ references would not link there.
 *
 * THE STATUS PAGE'S OWN READ DOES NOT COUNT. The cases' step hooks read the
 * page at every step boundary, and the page reads the driver's status: that
 * read found the controller free first and let a count taken BEFORE the
 * completion read deliver the frame too, so the count-order mutant survived.
 * Reads made inside gen_inhibit_get_stats_json() are ignored here.
 */
#include "shim_harness.h"

#include "twai_port.h"
#include "can.h"

#include <string.h>

static bool          g_dac_armed;
static bool          g_dac_free;
static int           g_dac_fired;
static int           g_dac_in_page;
static uint32_t      g_dac_id;
static uint8_t       g_dac_data[8];
static uint8_t       g_dac_dlc;

esp_err_t __real_twai_port_status(can_port_status_t *out);
uint32_t  __real_can_msgs_to_rx(void);
int       __real_gen_inhibit_get_stats_json(char *buf, int buflen);

esp_err_t __wrap_twai_port_status(can_port_status_t *out);
uint32_t  __wrap_can_msgs_to_rx(void);
int       __wrap_gen_inhibit_get_stats_json(char *buf, int buflen);

int __wrap_gen_inhibit_get_stats_json(char *buf, int buflen)
{
    g_dac_in_page++;
    const int n = __real_gen_inhibit_get_stats_json(buf, buflen);
    g_dac_in_page--;
    return n;
}

esp_err_t __wrap_twai_port_status(can_port_status_t *out)
{
    const esp_err_t err = __real_twai_port_status(out);
    if (g_dac_armed && g_dac_in_page == 0 && err == ESP_OK
        && out->state != CAN_PORT_STATE_DOWN && out->tx_pending == 0)
    {
        g_dac_free = true;
    }
    return err;
}

uint32_t __wrap_can_msgs_to_rx(void)
{
    if (g_dac_armed && g_dac_free && g_dac_in_page == 0)
    {
        g_dac_armed = false;
        g_dac_free = false;
        g_dac_fired++;
        ft_deliver(g_dac_id, g_dac_data, g_dac_dlc);
    }
    return __real_can_msgs_to_rx();
}

void ft_deliver_at_count(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    g_dac_id = id;
    g_dac_dlc = dlc > 8 ? 8 : dlc;
    memset(g_dac_data, 0, sizeof(g_dac_data));
    memcpy(g_dac_data, data, g_dac_dlc);
    g_dac_free = false;
    g_dac_armed = true;
}

int ft_delivered_at_count(void)
{
    return g_dac_fired;
}

void ft_deliver_at_count_reset(void)
{
    g_dac_armed = false;
    g_dac_free = false;
    g_dac_fired = 0;
}
