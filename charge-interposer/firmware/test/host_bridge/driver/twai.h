// The ESP-IDF TWAI API, host side, so `port_twai.cpp` compiles UNMODIFIED.
//
// Types and semantics are taken from the real header shipped with the
// toolchain this firmware builds against:
//   ~/.platformio/packages/framework-arduinoespressif32-libs/esp32s3/
//       include/driver/twai/include/driver/twai.h    (dated 2025-06-29)
// and NOT from `port_twai.cpp`. A fake written from the driver agrees with
// the driver's misunderstandings -- the same rule the MCP2515 fake follows,
// and the reason that one found a real defect.
//
// Only what `port_twai.cpp` actually calls is declared. Adding an unused
// surface would be modelling guesswork nothing exercises.
//
// THE STATE MACHINE IS THE POINT. Every one of these calls documents
// ESP_ERR_INVALID_STATE for a specific wrong state, and the driver's
// recovery path depends on one detail that is easy to get backwards:
// `twai_initiate_recovery()` ends in **STOPPED**, not RUNNING. That is why
// `TwaiPort::service()` calls `twai_start()` when it sees TWAI_STATE_STOPPED.
// A fake that recovered straight to RUNNING would make that branch dead code
// and it would never be tested.

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_TIMEOUT 0x107

typedef int gpio_num_t;

// --- modes (twai_types.h) ------------------------------------------------
typedef enum {
  TWAI_MODE_NORMAL,
  TWAI_MODE_NO_ACK,
  TWAI_MODE_LISTEN_ONLY,
} twai_mode_t;

// --- driver states, verbatim from the header -----------------------------
typedef enum {
  TWAI_STATE_STOPPED,
  TWAI_STATE_RUNNING,
  TWAI_STATE_BUS_OFF,
  TWAI_STATE_RECOVERING,
} twai_state_t;

// --- alerts --------------------------------------------------------------
#define TWAI_ALERT_TX_IDLE 0x00000001
#define TWAI_ALERT_TX_SUCCESS 0x00000002
#define TWAI_ALERT_RX_DATA 0x00000004
#define TWAI_ALERT_BELOW_ERR_WARN 0x00000008
#define TWAI_ALERT_ERR_ACTIVE 0x00000010
#define TWAI_ALERT_RECOVERY_IN_PROGRESS 0x00000020
#define TWAI_ALERT_BUS_RECOVERED 0x00000040
#define TWAI_ALERT_ARB_LOST 0x00000080
#define TWAI_ALERT_ABOVE_ERR_WARN 0x00000100
#define TWAI_ALERT_BUS_ERROR 0x00000200
#define TWAI_ALERT_TX_FAILED 0x00000400
#define TWAI_ALERT_RX_QUEUE_FULL 0x00000800
#define TWAI_ALERT_ERR_PASS 0x00001000
#define TWAI_ALERT_BUS_OFF 0x00002000

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
} twai_general_config_t;

typedef struct {
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

typedef struct {
  // The real header packs these as a union of bitfields with a flags word;
  // the driver only ever sets the named members, so the named form is what
  // is modelled.
  uint32_t extd : 1;
  uint32_t rtr : 1;
  uint32_t ss : 1;
  uint32_t self : 1;
  uint32_t dlc_non_comp : 1;
  uint32_t reserved : 27;
  uint32_t identifier;
  uint8_t data_length_code;
  uint8_t data[8];
} twai_message_t;

// Field order and meaning verbatim from the header. rx_missed_count and
// rx_overrun_count are DIFFERENT losses -- a full RX queue versus an RX
// FIFO overrun -- and both are cumulative since install. The driver sums
// them, which is what `can_port.h`'s "frames lost, however lost" asks for.
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

#define TWAI_GENERAL_CONFIG_DEFAULT(tx, rx, op_mode)                      \
  {0, (op_mode), (tx), (rx), -1, -1, 5, 5, 0, 0, 0}

#define TWAI_TIMING_CONFIG_1MBITS() {4, 15, 4, 3, false}
#define TWAI_TIMING_CONFIG_500KBITS() {8, 15, 4, 3, false}
#define TWAI_TIMING_CONFIG_250KBITS() {16, 15, 4, 3, false}
#define TWAI_TIMING_CONFIG_125KBITS() {32, 15, 4, 3, false}
#define TWAI_FILTER_CONFIG_ACCEPT_ALL() {0, 0xFFFFFFFF, true}

esp_err_t twai_driver_install(const twai_general_config_t* g_config,
                              const twai_timing_config_t* t_config,
                              const twai_filter_config_t* f_config);
esp_err_t twai_driver_uninstall(void);
esp_err_t twai_start(void);
esp_err_t twai_stop(void);
esp_err_t twai_transmit(const twai_message_t* message, int ticks_to_wait);
esp_err_t twai_receive(twai_message_t* message, int ticks_to_wait);
esp_err_t twai_read_alerts(uint32_t* alerts, int ticks_to_wait);
esp_err_t twai_reconfigure_alerts(uint32_t alerts_enabled,
                                  uint32_t* current_alerts);
esp_err_t twai_get_status_info(twai_status_info_t* status_info);
esp_err_t twai_initiate_recovery(void);

#ifdef __cplusplus
}
#endif
