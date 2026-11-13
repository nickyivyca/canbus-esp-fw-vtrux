/*
 * host_usb.h -- slcan_spi's host transport: the ESP32-S3's USB Serial/JTAG
 * port, the X2's one USB port, which also flashes it. Kept apart from the
 * CAN side (x2_test_images SPEC T15). NOT FOR VEHICLE (SPEC T14).
 *
 * The ESP-IDF console is off in this image (sdkconfig: no console), so
 * nothing but this image's own lines reaches the port once it runs. The
 * ROM's boot lines, before the image starts, are the ROM's (README.md).
 */
#ifndef HOST_USB_H
#define HOST_USB_H

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

esp_err_t host_usb_init(void);
int host_usb_read(uint8_t *buf, size_t n, TickType_t wait);
int host_usb_write(const void *buf, size_t n, TickType_t wait);

#endif
