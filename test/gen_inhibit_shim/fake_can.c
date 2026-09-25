/*
 * fake_can -- wican-fw's can.c reduced to what the shim uses.
 *
 * NOT a copy of can.c. The real one drags in lwip, comm_server and the whole
 * client stack; compiling it would mean mocking half the firmware to test the
 * eight functions gen_inhibit.c actually calls. What is reproduced is the
 * BEHAVIOUR the shim depends on:
 *
 *   - can_enable() installs and starts the driver, honouring the silent flag;
 *   - can_disable() calls gen_inhibit_quiesce() FIRST and then uninstalls,
 *     which is the handshake spec 8 exists for -- the ordering is the whole
 *     point, so getting it wrong here would hide the bug it guards against;
 *   - can_send() refuses while gen_inhibit owns the bus (spec 3.2, review A3).
 *
 * can_send()'s refusal is reproduced rather than mocked away because E1 case 5
 * is about what happens when it is missing: a frame queued by another task
 * after the inhibit leaves msgs_to_tx above zero once ours has gone, which is
 * a false TX_LATE. A test can call ft_foreign_transmit() to bypass the refusal
 * and show that, or can_send() to show the refusal working.
 */
#include "can.h"
#include "fake_twai.h"
#include "gen_inhibit.h"

#include "driver/twai.h"
#include "esp_log.h"

#include <stdbool.h>

static bool g_enabled;
static uint8_t g_silent;
static uint8_t g_bitrate;

void can_init(uint8_t bitrate) { g_bitrate = bitrate; }

void can_enable(void)
{
    if (g_enabled) return;
    twai_driver_install(0, 0, 0);
    twai_start();
    g_enabled = true;
}

void can_disable(void)
{
    if (!g_enabled) return;
    /*
     * Spec 8: the worker must be provably outside the driver before it is
     * uninstalled, or it wakes in freed memory. The real can.c calls this
     * first too; reproducing the ORDER is the point of having it here.
     */
    gen_inhibit_quiesce();
    twai_stop();
    twai_driver_uninstall();
    g_enabled = false;
}

void can_set_silent(uint8_t flag) { g_silent = flag; }
uint8_t can_is_silent(void) { return g_silent; }
bool can_is_enabled(void) { return g_enabled; }
void can_set_bitrate(uint8_t rate) { g_bitrate = rate; }
uint8_t can_get_bitrate(void) { return g_bitrate; }
void can_flush_rx(void) { twai_clear_receive_queue(); }
uint32_t can_msgs_to_rx(void) { return 0; }
void can_set_loopback(uint8_t f) { (void)f; }
void can_set_auto_retransmit(uint8_t f) { (void)f; }
void can_set_filter(uint32_t f) { (void)f; }
void can_set_mask(uint32_t m) { (void)m; }

esp_err_t can_receive(twai_message_t *message, TickType_t ticks)
{
    return twai_receive(message, ticks);
}

esp_err_t can_send(twai_message_t *message, TickType_t ticks)
{
    /* Spec 3.2 (review A3). See can.c for the full reasoning. */
    if (gen_inhibit_owns_bus())
    {
        ESP_LOGW(TAG, "can_send refused: gen_inhibit owns the bus (spec 3.2)");
        return ESP_ERR_INVALID_STATE;
    }
    if (!g_enabled) return ESP_ERR_INVALID_STATE;
    return twai_transmit(message, ticks);
}
