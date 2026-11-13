/*
 * fake_idf.h -- host fakes of the ESP-IDF, FreeRTOS and board interfaces the
 * jammer uses, on an emulated microsecond clock. TEST EQUIPMENT, NOT FOR
 * VEHICLE. The headers beside it (driver/gpio.h, esp_timer.h, ...) each just
 * include this one, so the jammer's sources compile unchanged against it.
 *
 * Only what the jammer calls, with the semantics it relies on. The model:
 *  - one emulated clock, fake_now_us; esp_timer_get_time() advances it by
 *    fake_time_step_us per call, so a busy-wait ends;
 *  - events at clock times: an RMT transmission's end (calls on_trans_done
 *    unless the channel was disabled first), the esp_timer's expiry, and
 *    lines the test schedules (fake_at()); time only moves in
 *    esp_timer_get_time(), xTaskNotifyWait() and vTaskDelay(), and due
 *    events fire as it moves, in time order;
 *  - the wire: each RMT symbol's level0 (inverted output, so internal 1 is
 *    dominant) is one pulse; pulses that start before a disable count.
 */
#ifndef FAKE_IDF_H
#define FAKE_IDF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- esp_err.h, esp_attr.h ---- */
typedef int esp_err_t;
#define ESP_OK                 0
#define ESP_FAIL               (-1)
#define ESP_ERR_NO_MEM         0x101
#define ESP_ERR_INVALID_ARG    0x102
#define ESP_ERR_INVALID_STATE  0x103
#define ESP_ERR_NOT_SUPPORTED  0x106
void fake_error_check(esp_err_t e, const char *expr);
#define ESP_ERROR_CHECK(x) fake_error_check((x), #x)
#define IRAM_ATTR

/* ---- driver/gpio.h ---- */
typedef int gpio_num_t;
#define GPIO_NUM_NC (-1)
typedef enum { GPIO_MODE_INPUT = 1, GPIO_MODE_OUTPUT = 2 } gpio_mode_t;
esp_err_t gpio_set_level(gpio_num_t gpio, uint32_t level);
esp_err_t gpio_set_direction(gpio_num_t gpio, gpio_mode_t mode);

/* ---- FreeRTOS ---- */
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef unsigned UBaseType_t;
#define pdTRUE  1
#define pdFALSE 0
#define pdPASS  1
#define portMAX_DELAY ((TickType_t)0xFFFFFFFFu)
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))      /* CONFIG_FREERTOS_HZ 1000 */
typedef struct fake_queue *QueueHandle_t;
typedef struct fake_mutex *SemaphoreHandle_t;
typedef struct fake_task *TaskHandle_t;
typedef void (*TaskFunction_t)(void *);
typedef enum { eSetBits = 1 } eNotifyAction;
SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t m, TickType_t wait);
BaseType_t xSemaphoreGive(SemaphoreHandle_t m);
QueueHandle_t xQueueCreate(UBaseType_t len, UBaseType_t item);
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);
BaseType_t xTaskNotify(TaskHandle_t t, uint32_t v, eNotifyAction a);
BaseType_t xTaskNotifyFromISR(TaskHandle_t t, uint32_t v, eNotifyAction a,
                              BaseType_t *woken);
BaseType_t xTaskNotifyWait(uint32_t clear_in, uint32_t clear_out,
                           uint32_t *value, TickType_t wait);
BaseType_t xTaskNotifyStateClear(TaskHandle_t t);
uint32_t ulTaskNotifyValueClear(TaskHandle_t t, uint32_t bits);
BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char *name,
                                   uint32_t stack, void *arg,
                                   UBaseType_t prio, TaskHandle_t *out,
                                   int core);
void vTaskDelay(TickType_t ticks);

/* ---- driver/rmt_tx.h, driver/rmt_encoder.h ---- */
typedef struct fake_rmt *rmt_channel_handle_t;
typedef struct fake_enc *rmt_encoder_handle_t;
typedef union {
    struct {
        uint16_t duration0 : 15;
        uint16_t level0 : 1;
        uint16_t duration1 : 15;
        uint16_t level1 : 1;
    };
    uint32_t val;
} rmt_symbol_word_t;
typedef enum { RMT_CLK_SRC_DEFAULT = 1 } rmt_clock_source_t;
typedef struct {
    gpio_num_t gpio_num;
    rmt_clock_source_t clk_src;
    uint32_t resolution_hz;
    size_t mem_block_symbols;
    size_t trans_queue_depth;
    int intr_priority;
    struct {
        uint32_t invert_out : 1;
        uint32_t with_dma : 1;
        uint32_t io_loop_back : 1;
        uint32_t io_od_mode : 1;
    } flags;
} rmt_tx_channel_config_t;
typedef struct { size_t num_symbols; } rmt_tx_done_event_data_t;
typedef bool (*rmt_tx_done_callback_t)(rmt_channel_handle_t,
                                       const rmt_tx_done_event_data_t *,
                                       void *);
typedef struct { rmt_tx_done_callback_t on_trans_done; } rmt_tx_event_callbacks_t;
typedef struct {
    int loop_count;
    struct {
        uint32_t eot_level : 1;
        uint32_t queue_nonblocking : 1;
    } flags;
} rmt_transmit_config_t;
typedef struct { int unused; } rmt_copy_encoder_config_t;
esp_err_t rmt_new_tx_channel(const rmt_tx_channel_config_t *cfg,
                             rmt_channel_handle_t *out);
esp_err_t rmt_tx_register_event_callbacks(rmt_channel_handle_t ch,
                                          const rmt_tx_event_callbacks_t *cbs,
                                          void *ctx);
esp_err_t rmt_enable(rmt_channel_handle_t ch);
esp_err_t rmt_disable(rmt_channel_handle_t ch);
esp_err_t rmt_del_channel(rmt_channel_handle_t ch);
esp_err_t rmt_new_copy_encoder(const rmt_copy_encoder_config_t *cfg,
                               rmt_encoder_handle_t *out);
esp_err_t rmt_encoder_reset(rmt_encoder_handle_t enc);
esp_err_t rmt_transmit(rmt_channel_handle_t ch, rmt_encoder_handle_t enc,
                       const void *data, size_t size,
                       const rmt_transmit_config_t *cfg);

/* ---- esp_timer.h ---- */
typedef struct fake_timer *esp_timer_handle_t;
typedef void (*esp_timer_cb_t)(void *);
typedef struct {
    esp_timer_cb_t callback;
    void *arg;
    int dispatch_method;
    const char *name;
    bool skip_unhandled_events;
} esp_timer_create_args_t;
esp_err_t esp_timer_create(const esp_timer_create_args_t *a,
                           esp_timer_handle_t *out);
esp_err_t esp_timer_start_once(esp_timer_handle_t t, uint64_t us);
esp_err_t esp_timer_stop(esp_timer_handle_t t);
int64_t esp_timer_get_time(void);

/* ---- driver/spi_master.h ---- */
typedef enum { SPI1_HOST = 0, SPI2_HOST = 1, SPI3_HOST = 2 } spi_host_device_t;
#define SPI_DMA_DISABLED 0
typedef struct {
    int mosi_io_num, miso_io_num, sclk_io_num, quadwp_io_num, quadhd_io_num;
    int max_transfer_sz;
} spi_bus_config_t;
typedef struct {
    uint8_t mode;
    int clock_speed_hz;
    int spics_io_num;
    int queue_size;
} spi_device_interface_config_t;
typedef struct fake_spidev *spi_device_handle_t;
typedef struct {
    size_t length;          /* bits */
    const void *tx_buffer;
    void *rx_buffer;
} spi_transaction_t;
esp_err_t spi_bus_initialize(spi_host_device_t host, const spi_bus_config_t *b,
                             int dma);
esp_err_t spi_bus_add_device(spi_host_device_t host,
                             const spi_device_interface_config_t *d,
                             spi_device_handle_t *out);
esp_err_t spi_device_polling_transmit(spi_device_handle_t d,
                                      spi_transaction_t *t);

/* ---- driver/usb_serial_jtag.h ---- */
typedef struct {
    uint32_t tx_buffer_size;
    uint32_t rx_buffer_size;
} usb_serial_jtag_driver_config_t;
esp_err_t usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t *c);
int usb_serial_jtag_read_bytes(void *buf, uint32_t n, TickType_t wait);
int usb_serial_jtag_write_bytes(const void *buf, size_t n, TickType_t wait);

/* ---- esp_app_desc.h, esp_system.h ---- */
typedef struct {
    char version[32];
    char project_name[32];
    char idf_ver[32];
} esp_app_desc_t;
const esp_app_desc_t *esp_app_get_description(void);
int esp_app_get_elf_sha256(char *dst, size_t size);
typedef enum { ESP_RST_POWERON = 1 } esp_reset_reason_t;
esp_reset_reason_t esp_reset_reason(void);

/* ---- board.h (vtrux/x2) ---- */
typedef struct { gpio_num_t tx, rx; } board_twai_pins_t;
typedef enum { BOARD_SPI_CAN_MCP2515 } board_spi_can_controller_t;
typedef struct {
    board_spi_can_controller_t controller;
    spi_host_device_t host;
    gpio_num_t cs, sck, miso, mosi, irq;
    uint32_t crystal_hz;
} board_spi_can_t;
esp_err_t board_twai_pins(int port, board_twai_pins_t *out);
esp_err_t board_spi_can(int port, board_spi_can_t *out);

/* ======== the test's side ======== */

/* Everything back to its start state. */
void fake_reset(void);

extern int64_t fake_now_us;
extern int64_t fake_time_step_us;   /* per esp_timer_get_time(); default 1 */

/* Injected faults. */
extern bool fake_timer_broken;      /* start_once succeeds, never fires */
extern int64_t fake_task_late_us;   /* each xTaskNotifyWait returns this late */

/* A call at an emulated time (e.g. a STOP line arriving). */
void fake_at(int64_t t_us, void (*fn)(void *), void *arg);

/* The call log: one entry per hardware-facing call, in order, e.g.
 * "gpio_set_level 7 1", "spi_bus_initialize", "usb_install",
 * "task jam", "delay 300". */
#define FAKE_LOG_MAX 4096
extern char fake_log[FAKE_LOG_MAX][64];
extern int fake_log_n;
int fake_log_find(const char *prefix, int from);    /* index or -1 */

/* The wire (CAN1's TXD, as the RMT drives it). */
extern long fake_wire_pulses;
extern int64_t fake_wire_first_us, fake_wire_last_us;  /* -1 if none */
extern uint32_t fake_wire_max_w_us, fake_wire_min_w_us;
extern int64_t fake_tx_end_max_us;  /* latest scheduled end of any batch */
extern int fake_rmt_violations;     /* level, eot, invert or overlap faults */
extern int fake_rmt_channels;       /* channels alive now */
extern int fake_rmt_transmits;
extern int fake_timer_creates, fake_timer_starts, fake_timer_fired;

/* The GPIO pin the jammer drives: its last level and direction. */
extern int fake_gpio_level[64];
extern int fake_gpio_output[64];

/* SPI: every transaction's bytes, and the MCP2515's model. */
#define FAKE_SPI_MAX 64
extern uint8_t fake_spi_tx[FAKE_SPI_MAX][8];
extern int fake_spi_len[FAKE_SPI_MAX];
extern int64_t fake_spi_t[FAKE_SPI_MAX];
extern int fake_spi_n;
extern int fake_spi_clock_hz, fake_spi_mode;
extern uint8_t fake_mcp_canstat_after_reset;  /* default 0x80 */
extern uint8_t fake_mcp_canctrl_after_reset;  /* default 0x87 */
extern esp_err_t fake_spi_bus_init_result;

/* The USB port: everything written, and bytes to read. */
#define FAKE_OUT_MAX 65536
extern char fake_out[FAKE_OUT_MAX];
extern size_t fake_out_n;

/* The app description esp_app_get_description() returns. */
extern esp_app_desc_t fake_app_desc;

/* The tasks created: name, function, priority, core. */
#define FAKE_TASKS_MAX 8
extern const char *fake_task_name[FAKE_TASKS_MAX];
extern TaskFunction_t fake_task_fn[FAKE_TASKS_MAX];
extern int fake_task_prio[FAKE_TASKS_MAX], fake_task_core[FAKE_TASKS_MAX];
extern int fake_tasks_n;

#endif
