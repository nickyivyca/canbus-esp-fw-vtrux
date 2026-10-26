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
#include "driver/gpio.h"

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

/*
 * The config types and macros, so the REAL main/can.c compiles unmodified.
 * Field names and the shape of the DEFAULT macros follow ESP-IDF 5.4.1; the
 * values are not used by the model, only accepted.
 */
#define TWAI_IO_UNUSED          ((gpio_num_t)-1)
#define TWAI_ALERT_NONE         0x00000000
#define ESP_INTR_FLAG_LEVEL1    (1 << 1)

typedef enum
{
    TWAI_MODE_NORMAL,
    TWAI_MODE_NO_ACK,
    TWAI_MODE_LISTEN_ONLY,
} twai_mode_t;

typedef struct
{
    twai_mode_t mode;
    int tx_io;
    int rx_io;
    int clkout_io;
    int bus_off_io;
    uint32_t tx_queue_len;
    uint32_t rx_queue_len;
    uint32_t alerts_enabled;
    uint32_t clkout_divider;
    int intr_flags;
} twai_general_config_t;

typedef struct
{
    uint32_t brp;
    uint8_t tseg_1;
    uint8_t tseg_2;
    uint8_t sjw;
    bool triple_sampling;
} twai_timing_config_t;

typedef struct
{
    uint32_t acceptance_code;
    uint32_t acceptance_mask;
    bool single_filter;
} twai_filter_config_t;

#define TWAI_GENERAL_CONFIG_DEFAULT(tx, rx, op) {                                 .mode = op, .tx_io = tx, .rx_io = rx,                                         .clkout_io = TWAI_IO_UNUSED, .bus_off_io = TWAI_IO_UNUSED,                    .tx_queue_len = 5, .rx_queue_len = 5,                                         .alerts_enabled = TWAI_ALERT_NONE, .clkout_divider = 0,                       .intr_flags = ESP_INTR_FLAG_LEVEL1 }

#define TWAI_FILTER_CONFIG_ACCEPT_ALL() {                                         .acceptance_code = 0, .acceptance_mask = 0xFFFFFFFF, .single_filter = true }

esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks);
esp_err_t twai_receive(twai_message_t *message, TickType_t ticks);
esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks);
esp_err_t twai_reconfigure_alerts(uint32_t alerts, uint32_t *prev);
esp_err_t twai_get_status_info(twai_status_info_t *status);
esp_err_t twai_start(void);
esp_err_t twai_stop(void);
esp_err_t twai_driver_install(const twai_general_config_t *g,
                              const twai_timing_config_t *t,
                              const twai_filter_config_t *f);
esp_err_t twai_driver_uninstall(void);
esp_err_t twai_clear_receive_queue(void);

#endif /* MOCK_DRIVER_TWAI_H */
