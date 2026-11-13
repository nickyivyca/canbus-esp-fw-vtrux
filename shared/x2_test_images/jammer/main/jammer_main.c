/*
 * jammer_main.c -- the X2 jammer. TEST EQUIPMENT, NEVER ON THE TRUCK: NOT
 * FOR VEHICLE (x2_test_images SPEC T3a, T8).
 *
 * Puts short dominant pulses on the X2's CAN1 by driving its transceiver's
 * TXD from the RMT (components/jam_pulse), so the bench can make error
 * frames on demand. It never sends a CAN frame: CAN1's TWAI driver is not
 * in the image, so its controller is never started, and the MCP2515 (CAN2)
 * is reset into configuration mode at boot, where it neither transmits nor
 * ACKs; its CANSTAT is in the banner (SPEC T8).
 *
 * Start-up order: CAN1's TXD driven recessive first, before anything that
 * can take time; then the MCP2515's reset; then the console; then the
 * pulse engine and the line task; the banner last.
 *
 * The board's ports come from the board layer (vtrux/x2, board.h); no pin
 * is named here.
 */
#include <string.h>

#include "board.h"
#include "esp_app_desc.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "console_usj.h"
#include "jam_pulse.h"
#include "jammer_main.h"
#include "mcp2515_reset.h"

#define CAN1_PORT 0     /* the board's TWAI port: jammed through its TXD */
#define CAN2_PORT 1     /* the board's SPI CAN port: the MCP2515 */

static gpio_num_t s_tx = GPIO_NUM_NC;
static esp_err_t s_mcp_err = ESP_FAIL;
static uint8_t s_canstat, s_canctrl;
static char s_buf[JAMMER_LINE_MAX];
static size_t s_len;

static void on_reply(const char *line, void *ctx)
{
    (void)ctx;
    jcon_line(line);
}

void jammer_banner(void)
{
    const esp_app_desc_t *d = esp_app_get_description();
    char elf[65];
    esp_app_get_elf_sha256(elf, sizeof elf);
    /* The PlatformIO jammer's banner lines, in its order, with the build's
     * identity (SPEC T10) where its source digest was. */
    jcon_linef("%s -- TEST EQUIPMENT, NEVER ON THE TRUCK", d->project_name);
    jcon_linef("build version=%s elf=%s idf=%s", d->version, elf, d->idf_ver);
    jcon_linef("tx=GPIO%d(CAN1/TWAI) twai=never_started stops=%s "
               "reset_reason=%d uptime_us=%lld",
               (int)s_tx, jam_stops_text(), (int)esp_reset_reason(),
               (long long)esp_timer_get_time());
    if (s_mcp_err == ESP_OK) {
        jcon_linef("mcp2515 canstat=0x%02X canctrl=0x%02X config_mode=%s",
                   s_canstat, s_canctrl,
                   (s_canstat & MCP2515_OPMOD_MASK) == MCP2515_OPMOD_CONFIG
                       ? "yes" : "NO");
    } else {
        jcon_linef("mcp2515 reset_failed err=0x%x config_mode=NO",
                   (unsigned)s_mcp_err);
    }
    char lim[JAM_REPLY_MAX];
    jam_limits_text(lim, sizeof lim);
    jcon_line(lim);
}

void jammer_handle_line(const char *line)
{
    const char *s = line;
    while (*s == ' ' || *s == '\t') s++;
    if (!strncmp(s, "ID", 2) && (s[2] == 0 || s[2] == ' ' || s[2] == '\t')) {
        jammer_banner();
        return;
    }
    if (jam_line(line) == JAM_LINE_NOT_MINE) jcon_line("ERR unknown_command");
}

void jammer_feed(const uint8_t *buf, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        char ch = (char)buf[i];
        if (ch == '\r') continue;
        if (ch == '\n') {
            s_buf[s_len] = 0;
            jammer_handle_line(s_buf);
            s_len = 0;
        } else if (s_len < sizeof s_buf - 1) {
            s_buf[s_len++] = ch;
        }
    }
}

static void line_task(void *arg)
{
    (void)arg;
    uint8_t buf[64];
    for (;;) {
        int n = jcon_read(buf, sizeof buf, portMAX_DELAY);
        if (n > 0) jammer_feed(buf, (size_t)n);
    }
}

void app_main(void)
{
    board_twai_pins_t can1;
    ESP_ERROR_CHECK(board_twai_pins(CAN1_PORT, &can1));
    s_tx = can1.tx;
    jam_drive_recessive(s_tx);      /* first */

    s_mcp_err = mcp2515_reset_config(CAN2_PORT, &s_canstat, &s_canctrl);

    ESP_ERROR_CHECK(jcon_init());

    jam_config_t jc;
    memset(&jc, 0, sizeof jc);
    jc.tx_gpio = s_tx;
    jc.reply = on_reply;
    jc.ctx = NULL;
    jc.core = 1;
    jc.priority = 10;
    ESP_ERROR_CHECK(jam_init(&jc));
    xTaskCreatePinnedToCore(line_task, "jam_line", 4096, NULL, 5, NULL, 0);

    vTaskDelay(pdMS_TO_TICKS(300));
    jammer_banner();
}
