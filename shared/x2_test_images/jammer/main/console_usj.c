/*
 * console_usj.c -- the jammer's console (console_usj.h). TEST EQUIPMENT,
 * NOT FOR VEHICLE (x2_test_images SPEC T8).
 */
#include "console_usj.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "freertos/semphr.h"

#define WRITE_TIMEOUT_MS 20

static SemaphoreHandle_t s_lock;

esp_err_t jcon_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;
    usb_serial_jtag_driver_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.tx_buffer_size = 2048;
    cfg.rx_buffer_size = 512;
    return usb_serial_jtag_driver_install(&cfg);
}

void jcon_line(const char *text)
{
    char line[CONSOLE_LINE_MAX];
    const size_t room = sizeof line - 2;    /* "\r\n" */
    size_t n = strlen(CONSOLE_PREFIX);
    memcpy(line, CONSOLE_PREFIX, n);
    size_t t = strlen(text);
    if (t > room - n) t = room - n;
    for (size_t i = 0; i < t; i++) {
        char ch = text[i];
        /* A line end inside the text would start a line without the
         * prefix. */
        line[n++] = (ch == '\r' || ch == '\n') ? ' ' : ch;
    }
    line[n++] = '\r';
    line[n++] = '\n';
    if (!s_lock) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    usb_serial_jtag_write_bytes(line, n, pdMS_TO_TICKS(WRITE_TIMEOUT_MS));
    xSemaphoreGive(s_lock);
}

void jcon_linef(const char *fmt, ...)
{
    char text[CONSOLE_LINE_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    jcon_line(text);
}

int jcon_read(uint8_t *buf, size_t n, TickType_t wait)
{
    return usb_serial_jtag_read_bytes(buf, (uint32_t)n, wait);
}
