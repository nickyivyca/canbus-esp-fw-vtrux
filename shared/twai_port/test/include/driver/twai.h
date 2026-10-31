/*
 * driver/twai.h for host builds on the twai_port fake (SPEC.md T10).
 *
 * Written from ESP-IDF v5.4.1's own headers, not from any code that calls
 * them: components/driver/twai/include/driver/twai.h and
 * components/hal/include/hal/twai_types.h. The types, field order, alert
 * values and default initialisers are theirs; the timing macros are the
 * CONFIG_XTAL_FREQ == 40 set, which is the C3's and the S3's. Only the
 * handle-less API is declared: the _v2 calls and twai_handle_t are left
 * out, because a call the fake does not implement must fail to link rather
 * than pass.
 *
 * It includes "esp_err.h", "freertos/FreeRTOS.h" and "driver/gpio.h", as
 * the real one does; a project's own host stand-ins for those come first
 * on its include path, and ../host/ has minimal ones for a build with none.
 */
#ifndef TWAI_FAKE_DRIVER_TWAI_H
#define TWAI_FAKE_DRIVER_TWAI_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "driver/gpio.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- hal/twai_types.h ---------------------------------------------------- */

#define TWAI_EXTD_ID_MASK               0x1FFFFFFF
#define TWAI_STD_ID_MASK                0x7FF
#define TWAI_FRAME_MAX_DLC              8
#define TWAI_ERR_PASS_THRESH            128

#define TWAI_MSG_FLAG_NONE              0x00
#define TWAI_MSG_FLAG_EXTD              0x01
#define TWAI_MSG_FLAG_RTR               0x02
#define TWAI_MSG_FLAG_SS                0x04
#define TWAI_MSG_FLAG_SELF              0x08
#define TWAI_MSG_FLAG_DLC_NON_COMP      0x10

/* SOC_TWAI_BRP_MIN and SOC_TWAI_BRP_MAX, the same on the C3 and the S3. */
#define TWAI_BRP_MIN                    2
#define TWAI_BRP_MAX                    16384

/* soc/clk_tree_defs.h: both chips' default is APB, 80 MHz. */
typedef int twai_clock_source_t;
#define TWAI_CLK_SRC_APB                4
#define TWAI_CLK_SRC_DEFAULT            TWAI_CLK_SRC_APB

#define TWAI_TIMING_CONFIG_1KBITS()     {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 20000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_5KBITS()     {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 100000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_10KBITS()    {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 200000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_12_5KBITS()  {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 312500, .brp = 0, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_16KBITS()    {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 400000, .brp = 0, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_20KBITS()    {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 400000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_25KBITS()    {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 625000, .brp = 0, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_50KBITS()    {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 1000000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_100KBITS()   {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 2000000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_125KBITS()   {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 2500000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_250KBITS()   {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 5000000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_500KBITS()   {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 10000000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_800KBITS()   {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 20000000, .brp = 0, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false}
#define TWAI_TIMING_CONFIG_1MBITS()     {.clk_src = TWAI_CLK_SRC_DEFAULT, .quanta_resolution_hz = 20000000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}

#define TWAI_FILTER_CONFIG_ACCEPT_ALL() {.acceptance_code = 0, .acceptance_mask = 0xFFFFFFFF, .single_filter = true}

typedef enum {
    TWAI_MODE_NORMAL,
    TWAI_MODE_NO_ACK,
    TWAI_MODE_LISTEN_ONLY,
} twai_mode_t;

typedef struct {
    union {
        struct {
            uint32_t extd: 1;
            uint32_t rtr: 1;
            uint32_t ss: 1;
            uint32_t self: 1;
            uint32_t dlc_non_comp: 1;
            uint32_t reserved: 27;
        };
        uint32_t flags;
    };
    uint32_t identifier;
    uint8_t data_length_code;
    uint8_t data[TWAI_FRAME_MAX_DLC];
} twai_message_t;

typedef struct {
    twai_clock_source_t clk_src;
    uint32_t quanta_resolution_hz;
    uint32_t brp;
    uint8_t tseg_1;
    uint8_t tseg_2;
    uint8_t sjw;
    bool triple_sampling;
} twai_timing_config_t;

typedef struct {
    uint32_t acceptance_code;
    uint32_t acceptance_mask;
    bool single_filter;
} twai_filter_config_t;

/* ---- driver/twai.h ------------------------------------------------------- */

#ifndef ESP_INTR_FLAG_LEVEL1
#define ESP_INTR_FLAG_LEVEL1            (1 << 1)
#endif

#define TWAI_GENERAL_CONFIG_DEFAULT_V2(controller_num, tx_io_num, rx_io_num, op_mode) {.controller_id = controller_num, \
        .mode = op_mode, .tx_io = tx_io_num, .rx_io = rx_io_num,                    \
        .clkout_io = TWAI_IO_UNUSED, .bus_off_io = TWAI_IO_UNUSED,                  \
        .tx_queue_len = 5, .rx_queue_len = 5,                                       \
        .alerts_enabled = TWAI_ALERT_NONE,  .clkout_divider = 0,                    \
        .intr_flags = ESP_INTR_FLAG_LEVEL1, .general_flags = {0}}
#define TWAI_GENERAL_CONFIG_DEFAULT(tx_io_num, rx_io_num, op_mode) TWAI_GENERAL_CONFIG_DEFAULT_V2(0, tx_io_num, rx_io_num, op_mode)

#define TWAI_ALERT_TX_IDLE                  0x00000001
#define TWAI_ALERT_TX_SUCCESS               0x00000002
#define TWAI_ALERT_RX_DATA                  0x00000004
#define TWAI_ALERT_BELOW_ERR_WARN           0x00000008
#define TWAI_ALERT_ERR_ACTIVE               0x00000010
#define TWAI_ALERT_RECOVERY_IN_PROGRESS     0x00000020
#define TWAI_ALERT_BUS_RECOVERED            0x00000040
#define TWAI_ALERT_ARB_LOST                 0x00000080
#define TWAI_ALERT_ABOVE_ERR_WARN           0x00000100
#define TWAI_ALERT_BUS_ERROR                0x00000200
#define TWAI_ALERT_TX_FAILED                0x00000400
#define TWAI_ALERT_RX_QUEUE_FULL            0x00000800
#define TWAI_ALERT_ERR_PASS                 0x00001000
#define TWAI_ALERT_BUS_OFF                  0x00002000
#define TWAI_ALERT_RX_FIFO_OVERRUN          0x00004000
#define TWAI_ALERT_TX_RETRIED               0x00008000
#define TWAI_ALERT_PERIPH_RESET             0x00010000
#define TWAI_ALERT_ALL                      0x0001FFFF
#define TWAI_ALERT_NONE                     0x00000000
#define TWAI_ALERT_AND_LOG                  0x00020000

#define TWAI_IO_UNUSED                      ((gpio_num_t) -1)

typedef enum {
    TWAI_STATE_STOPPED,
    TWAI_STATE_RUNNING,
    TWAI_STATE_BUS_OFF,
    TWAI_STATE_RECOVERING,
} twai_state_t;

typedef struct {
    int controller_id;
    twai_mode_t mode;
    gpio_num_t tx_io;
    gpio_num_t rx_io;
    gpio_num_t clkout_io;
    gpio_num_t bus_off_io;
    uint32_t tx_queue_len;
    uint32_t rx_queue_len;
    uint32_t alerts_enabled;
    uint32_t clkout_divider;
    int intr_flags;
    struct {
        uint32_t sleep_allow_pd;
    } general_flags;
} twai_general_config_t;

typedef struct {
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

esp_err_t twai_driver_install(const twai_general_config_t *g_config, const twai_timing_config_t *t_config, const twai_filter_config_t *f_config);
esp_err_t twai_driver_uninstall(void);
esp_err_t twai_start(void);
esp_err_t twai_stop(void);
esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks_to_wait);
esp_err_t twai_receive(twai_message_t *message, TickType_t ticks_to_wait);
esp_err_t twai_read_alerts(uint32_t *alerts, TickType_t ticks_to_wait);
esp_err_t twai_reconfigure_alerts(uint32_t alerts_enabled, uint32_t *current_alerts);
esp_err_t twai_initiate_recovery(void);
esp_err_t twai_get_status_info(twai_status_info_t *status_info);
esp_err_t twai_clear_transmit_queue(void);
esp_err_t twai_clear_receive_queue(void);

#ifdef __cplusplus
}
#endif

#endif /* TWAI_FAKE_DRIVER_TWAI_H */
