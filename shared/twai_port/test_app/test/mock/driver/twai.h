/* Host stand-in, compile only (test_stack.py): ESP-IDF v5.4.2's
 * driver/twai.h, the types, fields, alert bits and calls the probe uses. */
#ifndef MOCK_DRIVER_TWAI_H
#define MOCK_DRIVER_TWAI_H
#include <stdint.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#define TWAI_ALERT_TX_IDLE      0x00000001
#define TWAI_ALERT_TX_SUCCESS   0x00000002
#define TWAI_ALERT_RX_DATA      0x00000004
#define TWAI_ALERT_ARB_LOST     0x00000800
#define TWAI_ALERT_BUS_ERROR    0x00000200
#define TWAI_ALERT_TX_FAILED    0x00000400
#define TWAI_ALERT_TX_RETRIED   0x00004000
#define TWAI_ALERT_ALL          0x0001FFFF

typedef struct {
    union {
        struct {
            uint32_t extd: 1, rtr: 1, ss: 1, self: 1, dlc_non_comp: 1,
                     reserved: 27;
        };
        uint32_t flags;
    };
    uint32_t identifier;
    uint8_t  data_length_code;
    uint8_t  data[8];
} twai_message_t;

typedef struct {
    int      state;
    uint32_t msgs_to_tx, msgs_to_rx;
    uint32_t tx_error_counter, rx_error_counter;
    uint32_t tx_failed_count, rx_missed_count, rx_overrun_count;
    uint32_t arb_lost_count, bus_error_count;
} twai_status_info_t;

esp_err_t twai_transmit(const twai_message_t *m, TickType_t ticks);
esp_err_t twai_get_status_info(twai_status_info_t *s);
esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks);
esp_err_t twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev);
esp_err_t twai_clear_transmit_queue(void);
#endif
