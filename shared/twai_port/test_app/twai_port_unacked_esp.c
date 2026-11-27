/*
 * twai_port_unacked on the target -- MEASUREMENT-ONLY (SPEC.md T13): the
 * txp_env_*() calls the no-acknowledge modes use for the driver and the
 * clocks (twai_port_unacked.h). Chip-neutral: the register reads are in
 * twai_port_probe_ll_<chip>.c.
 *
 * TIMING SOURCE: the CPU cycle counter, esp_cpu_get_cycle_count(), a CSR
 * read, cheap enough for a sample about every microsecond. It is converted
 * with esp_clk_cpu_freq(), read at the start and the end of each call; each
 * trial also reports its cycle count against esp_timer's microseconds, so a
 * frequency change inside a trial shows.
 */
#include "twai_port_unacked.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_cpu.h"
#include "esp_private/esp_clk.h"
#include "esp_timer.h"
#include "driver/twai.h"

#include "can.h"

/* The trial frame: the older phases' ID and tag (twai_port_txabort_probe.c). */
#define TXP_ID          0x7FE
#define TXP_DLC         8
#define TXP_TAG_HI      0xAB
#define TXP_TAG_LO      0x7E

static portMUX_TYPE s_irq_lock = portMUX_INITIALIZER_UNLOCKED;

uint32_t txp_env_cycles(void)
{
    return (uint32_t)esp_cpu_get_cycle_count();
}

uint32_t txp_env_cpu_hz(void)
{
    return (uint32_t)esp_clk_cpu_freq();
}

int64_t txp_env_timer_us(void)
{
    return esp_timer_get_time();
}

void txp_env_irq_off(void)
{
    taskENTER_CRITICAL(&s_irq_lock);
}

void txp_env_irq_on(void)
{
    taskEXIT_CRITICAL(&s_irq_lock);
}

/*
 * The driver down and up again: the install resets the controller's error
 * counters (twai_hal_init(), TWAI_HAL_INIT_TEC), so the trial starts
 * error-active with TEC 0. The port's install enables only its own alerts, so
 * every alert is enabled again after it, as the older phases do once.
 */
bool txp_env_reinstall(void)
{
    can_disable();
    can_set_silent(0);
    can_enable();
    if (!can_is_enabled() || can_is_silent())
    {
        return false;
    }
    return twai_reconfigure_alerts(TWAI_ALERT_ALL, NULL) == ESP_OK;
}

/*
 * twai_transmit() directly, as the older phases do, with single-shot off, so
 * the controller resends after each error by itself.
 */
bool txp_env_send(uint16_t trial)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = TXP_ID;
    m.data_length_code = TXP_DLC;
    m.ss = 0;
    m.self = 0;
    m.data[0] = (uint8_t)(trial & 0xFF);
    m.data[1] = (uint8_t)(trial >> 8);
    m.data[2] = TXP_TAG_HI;
    m.data[3] = TXP_TAG_LO;
    return twai_transmit(&m, 0) == ESP_OK;
}

uint32_t txp_env_alerts(void)
{
    uint32_t a = 0;
    (void)twai_read_alerts(&a, 0);   /* ESP_ERR_TIMEOUT with a = 0: none */
    return a;
}

bool txp_env_driver(txp_drv_t *out)
{
    twai_status_info_t s;
    if (twai_get_status_info(&s) != ESP_OK)
    {
        memset(out, 0, sizeof(*out));
        return false;
    }
    out->state = (uint32_t)s.state;
    out->msgs_to_tx = s.msgs_to_tx;
    out->tec = s.tx_error_counter;
    out->rec = s.rx_error_counter;
    out->tx_failed = s.tx_failed_count;
    out->bus_error = s.bus_error_count;
    out->arb_lost = s.arb_lost_count;
    return true;
}

void txp_env_clear_tx(void)
{
    (void)twai_clear_transmit_queue();
}

void txp_env_flush_rx(void)
{
    can_flush_rx();
}

void txp_env_settle(int ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

/*
 * The hold: blocks, so the driver's ISR and the IDLE task run. pdMS_TO_TICKS()
 * rounds down, so it waits tick by tick until esp_timer says ms have passed:
 * at least the hold asked for, never less. The caller times it in cycles.
 */
void txp_env_hold(int ms)
{
    const int64_t end = esp_timer_get_time() + (int64_t)ms * 1000;
    while (esp_timer_get_time() < end)
    {
        vTaskDelay(1);
    }
}
