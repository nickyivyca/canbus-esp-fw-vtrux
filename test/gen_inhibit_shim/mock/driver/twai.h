/*
 * Mock driver/twai.h for the E1 host build. Field names and semantics follow
 * ESP-IDF 5.4.1's real header, because the shim is written against that and a
 * mock that renamed anything would be testing a different program.
 *
 * The two semantics that matter here, both copied from the real docs:
 *
 *   TWAI_ALERT_TX_SUCCESS  "The previous transmission was successful" -- note
 *                          PREVIOUS, with no indication of WHICH. It is a
 *                          latched bit shared by every frame, and
 *                          twai_read_alerts() clears what it returns.
 *   msgs_to_tx             "Number of messages queued for transmission or
 *                          awaiting transmission completion".
 *
 * TWAI_ALERT_TX_FAILED is documented "for single shot transmission"; these
 * frames go out with ss = 0, so it is effectively unreachable in normal mode
 * and the model only raises it when a test asks.
 */
#ifndef MOCK_DRIVER_TWAI_H
#define MOCK_DRIVER_TWAI_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#define TWAI_ALERT_TX_IDLE      0x00000001
#define TWAI_ALERT_TX_SUCCESS   0x00000002
#define TWAI_ALERT_RX_DATA      0x00000004
#define TWAI_ALERT_TX_FAILED    0x00000400

typedef enum
{
    TWAI_STATE_STOPPED,
    TWAI_STATE_RUNNING,
    TWAI_STATE_BUS_OFF,
    TWAI_STATE_RECOVERING,
} twai_state_t;

typedef struct
{
    struct
    {
        uint32_t extd : 1;
        uint32_t rtr : 1;
        uint32_t ss : 1;
        uint32_t self : 1;
        uint32_t dlc_non_comp : 1;
        uint32_t reserved : 27;
    };
    uint32_t identifier;
    uint8_t  data_length_code;
    uint8_t  data[8];
} twai_message_t;

typedef struct
{
    twai_state_t state;
    uint32_t msgs_to_tx;
    uint32_t msgs_to_rx;
    uint32_t tx_error_counter;
    uint32_t rx_error_counter;
    uint32_t tx_failed_count;
    uint32_t rx_missed_count;
    uint32_t rx_overrun_count;
    uint32_t arb_lost_count;
    uint32_t bus_error_count;
} twai_status_info_t;

esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks);
esp_err_t twai_receive(twai_message_t *message, TickType_t ticks);
esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks);
esp_err_t twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev);
esp_err_t twai_get_status_info(twai_status_info_t *status);
esp_err_t twai_start(void);
esp_err_t twai_stop(void);
esp_err_t twai_driver_install(const void *g, const void *t, const void *f);
esp_err_t twai_driver_uninstall(void);
esp_err_t twai_clear_receive_queue(void);

#endif /* MOCK_DRIVER_TWAI_H */
