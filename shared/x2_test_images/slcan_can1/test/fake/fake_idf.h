/*
 * Host fakes of what ../../main/app_main.c calls: ESP-IDF's USB
 * Serial/JTAG driver and FreeRTOS's task call, the board's TWAI pins, and
 * the shared vtrux/can_port and vtrux/twai_port, as far as the app uses
 * them. The prototypes are the real headers' (ESP-IDF v5.4.2; shared/main
 * 2026.11.12.2); the types carry only the fields the app touches. The real
 * build is what checks the app against the real headers.
 *
 * The fake of the port models what T8a is about, and records every call:
 * whether the port is up, the mode it came up in, and every frame offered
 * to it. A frame offered while the port is down or listen-only is refused,
 * as the real port refuses it, and counted as a T8a violation: the app must
 * never offer one (slcan1_proto.h).
 */
#ifndef FAKE_IDF_H
#define FAKE_IDF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- esp_err.h ---- */
typedef int esp_err_t;
#define ESP_OK                  0
#define ESP_FAIL                (-1)
#define ESP_ERR_INVALID_ARG     0x102
#define ESP_ERR_INVALID_STATE   0x103
#define ESP_ERR_NOT_FOUND       0x105
#define ESP_ERR_TIMEOUT         0x107

/* ---- freertos ---- */
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef void (*TaskFunction_t)(void *);
typedef void *TaskHandle_t;
#define portMAX_DELAY ((TickType_t)0xffffffffUL)
BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char *name,
                                   uint32_t stack, void *arg, unsigned prio,
                                   TaskHandle_t *out, BaseType_t core);

/* ---- driver/usb_serial_jtag.h ---- */
typedef struct {
    uint32_t tx_buffer_size;
    uint32_t rx_buffer_size;
} usb_serial_jtag_driver_config_t;
esp_err_t usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t *cfg);
int usb_serial_jtag_read_bytes(void *buf, uint32_t length, TickType_t ticks_to_wait);
int usb_serial_jtag_write_bytes(const void *src, size_t size, TickType_t ticks_to_wait);

/* ---- board.h ---- */
typedef int gpio_num_t;
typedef struct {
    gpio_num_t tx;
    gpio_num_t rx;
} board_twai_pins_t;
esp_err_t board_twai_pins(int port, board_twai_pins_t *out);

/* ---- driver/twai.h, as can_port.h brings it in ---- */
typedef struct {
    uint32_t extd: 1;
    uint32_t rtr: 1;
    uint32_t ss: 1;
    uint32_t self: 1;
    uint32_t dlc_non_comp: 1;
    uint32_t identifier;
    uint8_t data_length_code;
    uint8_t data[8];
} twai_message_t;

/* ---- can_port.h ---- */
typedef enum {
    CAN_PORT_MODE_NORMAL = 0,
    CAN_PORT_MODE_LISTEN_ONLY,
    CAN_PORT_MODE_NO_ACK_SELF_TEST,
} can_port_mode_t;

typedef struct {
    bool bus_off;
    uint32_t rx_frames;
    uint32_t tx_frames;
    uint32_t rx_lost;
    uint32_t tx_dropped;
    uint32_t tx_refused;
    uint32_t bus_errors;
} can_port_status_t;

typedef struct can_port_ops can_port_ops_t;

esp_err_t can_port_register(int port, const can_port_ops_t *ops, void *ctx);
esp_err_t can_port_up(int port);
esp_err_t can_port_receive(int port, twai_message_t *out, TickType_t wait);
esp_err_t can_port_send(int port, const twai_message_t *m, TickType_t wait);
void can_port_service(int port);
esp_err_t can_port_status(int port, can_port_status_t *out);

/* ---- twai_port.h ---- */
typedef enum {
    TWAI_PORT_RECOVERY_NEVER = 0,
    TWAI_PORT_RECOVERY_AUTO,
} twai_port_recovery_t;

typedef enum {
    TWAI_PORT_INSTALL_FAIL_ABORT = 0,
    TWAI_PORT_INSTALL_FAIL_REPORT,
} twai_port_install_fail_t;

typedef struct {
    uint32_t acceptance_code;
    uint32_t acceptance_mask;
    bool single_filter;
} twai_port_filter_t;

typedef struct {
    int board_port;
    int timing;                 /* twai_timing_config_t; the defaults' 500 kbit/s */
    can_port_mode_t mode;
    twai_port_filter_t filter;
    bool single_shot;
    uint32_t rx_queue_len;
    uint32_t tx_queue_len;
    twai_port_recovery_t recovery;
    bool drop_rtr;
    twai_port_install_fail_t on_install_fail;
    void (*pre_teardown)(void *ctx);
    void (*install_hook)(void *ctx, twai_port_filter_t *filter);
    void *hook_ctx;
} twai_port_config_t;

void twai_port_config_defaults(twai_port_config_t *cfg);
esp_err_t twai_port_setup(const twai_port_config_t *cfg);
esp_err_t twai_port_teardown(void);
esp_err_t twai_port_set_mode(can_port_mode_t mode);
extern const can_port_ops_t twai_port_ops;

/* ---- the fake's own state, for the tests ---- */
#define FAKE_TIMING_500K 500000
#define FAKE_MAX_FRAMES 64
#define FAKE_IO_MAX 4096

typedef struct {
    /* the board */
    bool board_has_twai;
    /* the port */
    int setups;
    twai_port_config_t config;          /* as twai_port_setup() took it */
    int registers;
    int registered_port;
    const can_port_ops_t *registered_ops;
    bool up;
    can_port_mode_t mode;               /* the mode recorded for the next bring-up */
    int ups;
    can_port_mode_t up_mode[16];        /* the mode of each bring-up */
    int teardowns;
    int services;
    int status_reads;
    can_port_status_t status;           /* what can_port_status() gives */
    /* frames */
    int sent;                           /* queued with the port up, normal */
    twai_message_t sent_frames[FAKE_MAX_FRAMES];
    int violations;                     /* frames offered down or listen-only */
    bool queue_full;                    /* refuse sends as a full queue does */
    int rx_pending;                     /* frames the port holds for receive */
    int rx_taken;
    /* the USB link */
    int usb_installs;
    usb_serial_jtag_driver_config_t usb_config;
    uint8_t in[FAKE_IO_MAX];
    size_t in_len, in_pos;
    char out[FAKE_IO_MAX];
    size_t out_len;
    int tasks;
} fake_t;

extern fake_t fake;

/* A fresh fake, as after a hardware reset: the port down, nothing queued,
 * the board with its TWAI port. */
void fake_reset(void);
/* Bytes for the image to read from the host. */
void fake_host_sends(const char *s);
/* Take what the image wrote to the host, NUL-terminated. */
const char *fake_host_reads(void);
void fake_host_clear(void);

#endif
