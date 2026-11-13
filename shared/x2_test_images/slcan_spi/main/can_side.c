/*
 * can_side.c -- slcan_spi's CAN side (can_side.h). NOT FOR VEHICLE
 * (x2_test_images SPEC T14).
 *
 * One task owns the MCP2515: every SPI transfer happens in it. The host
 * side asks it to open or close through a command and a semaphore, and
 * queues frames to send. Frames go out one at a time through transmit
 * buffer 0, so they reach the bus in the order the host sent them.
 * Receive buffers 0 and 1 run with rollover; after reading buffer 0 the
 * flags are read again, so a frame that rolled into buffer 1 meanwhile is
 * read before a newer one in buffer 0, and the host gets them in bus
 * order.
 */
#include "can_side.h"

#include <string.h>
#include "driver/gpio.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mcp2515.h"

/* 500 kbit/s from the 16 MHz crystal (datasheet section 5): TQ = 2 x
 * (BRP + 1) / 16 MHz = 125 ns with BRP 0; 16 TQ a bit: sync 1, PRSEG 6,
 * PHSEG1 7, PHSEG2 2, so the sample point is at 14/16 = 87.5 %; SJW 2,
 * one sample per bit. */
#define CNF1_500K   0x40    /* SJW 2 TQ, BRP 0 */
#define CNF2_500K   0xB5    /* BTLMODE, one sample, PHSEG1 7 TQ, PRSEG 6 TQ */
#define CNF3_500K   0x01    /* PHSEG2 2 TQ */

/* Rollover from buffer 0 into 1, filters off: every frame received. */
#define RXB0CTRL_ANY_ROLLOVER 0x64
#define RXB1CTRL_ANY          0x60

#define TXQ_LEN     512

#define N_INT       (1u << 0)
#define N_TXQ       (1u << 1)
#define N_CMD       (1u << 2)

enum { CMD_NONE, CMD_OPEN_NORMAL, CMD_OPEN_LISTEN, CMD_CLOSE };
enum { MODE_CLOSED, MODE_NORMAL, MODE_LISTEN };

static const can_side_cb_t *s_cb;
static QueueHandle_t s_txq;
static TaskHandle_t s_task;
static SemaphoreHandle_t s_cmd_done;
static volatile int s_cmd;
static volatile int s_cmd_result;
static volatile int s_mode = MODE_CLOSED;
static int s_tx_busy;
static char s_state = 'a';
static can_side_stats_t s_stats;

static void IRAM_ATTR int_isr(void *arg)
{
    BaseType_t hp = pdFALSE;
    xTaskNotifyFromISR(s_task, N_INT, eSetBits, &hp);
    portYIELD_FROM_ISR(hp);
}

/* Reset into configuration mode and set up the bit timing, buffers and
 * interrupts. Silent afterwards. */
static void chip_init(void)
{
    mcp2515_reset();
    mcp2515_write(MCP_CNF1, CNF1_500K);
    mcp2515_write(MCP_CNF2, CNF2_500K);
    mcp2515_write(MCP_CNF3, CNF3_500K);
    mcp2515_write(MCP_BFPCTRL, 0x00);
    mcp2515_write(MCP_TXRTSCTRL, 0x00);
    mcp2515_write(MCP_RXB0CTRL, RXB0CTRL_ANY_ROLLOVER);
    mcp2515_write(MCP_RXB1CTRL, RXB1CTRL_ANY);
    mcp2515_write(MCP_CANINTE, MCP_RX0IF | MCP_RX1IF | MCP_TX0IF | MCP_ERRIF);
    mcp2515_write(MCP_CANINTF, 0x00);
    mcp2515_write(MCP_EFLG, 0x00);
}

static void flush_txq(void)
{
    can_frame_t f;
    while (xQueueReceive(s_txq, &f, 0) == pdTRUE) s_stats.tx_flushed++;
}

static int do_open(int listen)
{
    if (s_mode != MODE_CLOSED) return -1;
    flush_txq();
    s_tx_busy = 0;
    mcp2515_write(MCP_CANINTF, 0x00);
    mcp2515_write(MCP_EFLG, 0x00);
    if (mcp2515_set_mode(listen ? MCP_MODE_LISTEN : MCP_MODE_NORMAL, 20) != ESP_OK) {
        chip_init();
        return -1;
    }
    s_state = 'a';
    s_mode = listen ? MODE_LISTEN : MODE_NORMAL;
    return 0;
}

static void do_close(void)
{
    s_mode = MODE_CLOSED;
    /* Abort what is pending; configuration mode is entered only once the
     * frame on the wire has ended, so nothing is cut short. Then reset. */
    mcp2515_modify(MCP_CANCTRL, MCP_CANCTRL_ABAT, MCP_CANCTRL_ABAT);
    (void)mcp2515_set_mode(MCP_MODE_CONFIG, 20);
    chip_init();
    flush_txq();
    s_tx_busy = 0;
}

static void check_state(uint8_t eflg)
{
    char st = (eflg & MCP_TXBO) ? 'b'
            : (eflg & (MCP_TXEP | MCP_RXEP)) ? 'p'
            : (eflg & MCP_EWARN) ? 'w' : 'a';
    if (st == s_state) return;
    s_state = st;
    uint8_t c[2];
    mcp2515_read_n(MCP_TEC, c, 2);          /* TEC, REC */
    if (s_cb->on_state) s_cb->on_state(st, c[1], c[0]);
}

static void receive(int buf)
{
    can_frame_t f;
    mcp2515_read_rx(buf, &f);
    s_stats.rx++;
    if (s_cb->on_frame) s_cb->on_frame(&f);
}

/* One pass over the flags. Returns nonzero if anything was pending. */
static int service(void)
{
    uint8_t r[2];
    mcp2515_read_n(MCP_CANINTF, r, 2);      /* CANINTF, EFLG */
    uint8_t intf = r[0], eflg = r[1];
    if (intf & MCP_RX0IF) {
        receive(0);
        if (!(intf & MCP_RX1IF)) {
            mcp2515_read_n(MCP_CANINTF, r, 2);
            intf |= r[0] & ~MCP_RX0IF;
            eflg |= r[1];
        }
    }
    if (intf & MCP_RX1IF) receive(1);

    uint8_t clr = intf & (MCP_TX0IF | MCP_ERRIF | MCP_WAKIF | MCP_MERRF);
    if (clr) mcp2515_modify(MCP_CANINTF, clr, 0x00);
    if (intf & MCP_TX0IF) {
        s_tx_busy = 0;
        s_stats.tx_ok++;
    }
    uint8_t ovr = eflg & (MCP_RX0OVR | MCP_RX1OVR);
    if (ovr) {
        mcp2515_modify(MCP_EFLG, ovr, 0x00);
        s_stats.rx_overrun++;
        if (s_cb->on_rx_overrun) s_cb->on_rx_overrun();
    }
    check_state(eflg);
    return intf != 0;
}

static void transmit(void)
{
    if (s_tx_busy || s_mode != MODE_NORMAL) return;
    can_frame_t f;
    if (xQueueReceive(s_txq, &f, 0) == pdTRUE) {
        mcp2515_send_tx0(&f);
        s_tx_busy = 1;
    }
}

static void task(void *arg)
{
    for (;;) {
        uint32_t bits = 0;
        xTaskNotifyWait(0, UINT32_MAX, &bits, pdMS_TO_TICKS(2));
        if (bits & N_CMD) {
            int cmd = s_cmd;
            s_cmd = CMD_NONE;
            if (cmd == CMD_CLOSE) {
                do_close();
                s_cmd_result = 0;
            } else if (cmd == CMD_OPEN_NORMAL || cmd == CMD_OPEN_LISTEN) {
                s_cmd_result = do_open(cmd == CMD_OPEN_LISTEN);
            }
            xSemaphoreGive(s_cmd_done);
        }
        if (s_mode == MODE_CLOSED) continue;
        /* Until INT goes high: every enabled flag handled. The bound keeps
         * a stuck INT from starving the rest of the task. */
        for (int i = 0; i < 64; i++) {
            int pending = service();
            transmit();
            if (!pending && mcp2515_int_level() != 0) break;
        }
    }
}

esp_err_t can_side_init(int port, const can_side_cb_t *cb, uint8_t *canstat)
{
    s_cb = cb;
    esp_err_t err = mcp2515_attach(port);
    if (err != ESP_OK) return err;
    chip_init();
    if (canstat) *canstat = mcp2515_read(MCP_CANSTAT);

    s_txq = xQueueCreate(TXQ_LEN, sizeof(can_frame_t));
    s_cmd_done = xSemaphoreCreateBinary();
    if (!s_txq || !s_cmd_done) return ESP_ERR_NO_MEM;
    if (xTaskCreatePinnedToCore(task, "can_side", 4096, NULL, 20, &s_task, 1) != pdPASS)
        return ESP_ERR_NO_MEM;
    err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
    return gpio_isr_handler_add(mcp2515_int_gpio(), int_isr, NULL);
}

static int command(int cmd)
{
    s_cmd = cmd;
    xTaskNotify(s_task, N_CMD, eSetBits);
    if (xSemaphoreTake(s_cmd_done, pdMS_TO_TICKS(500)) != pdTRUE) return -1;
    return s_cmd_result;
}

int can_side_open(bool listen_only)
{
    return command(listen_only ? CMD_OPEN_LISTEN : CMD_OPEN_NORMAL);
}

void can_side_close(void)
{
    (void)command(CMD_CLOSE);
}

int can_side_send(const can_frame_t *f)
{
    if (s_mode != MODE_NORMAL) return -1;
    if (xQueueSend(s_txq, f, 0) != pdTRUE) {
        s_stats.tx_full++;
        return -1;
    }
    s_stats.tx_queued++;
    xTaskNotify(s_task, N_TXQ, eSetBits);
    return 0;
}

void can_side_get_stats(can_side_stats_t *out)
{
    *out = s_stats;
}
