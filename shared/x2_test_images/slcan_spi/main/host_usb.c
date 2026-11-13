/*
 * host_usb.c -- slcan_spi's host transport (host_usb.h). NOT FOR VEHICLE
 * (x2_test_images SPEC T14).
 */
#include "host_usb.h"

#include "driver/usb_serial_jtag.h"

esp_err_t host_usb_init(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .tx_buffer_size = 4096,
        .rx_buffer_size = 4096,
    };
    return usb_serial_jtag_driver_install(&cfg);
}

int host_usb_read(uint8_t *buf, size_t n, TickType_t wait)
{
    return usb_serial_jtag_read_bytes(buf, n, wait);
}

int host_usb_write(const void *buf, size_t n, TickType_t wait)
{
    return usb_serial_jtag_write_bytes(buf, n, wait);
}
