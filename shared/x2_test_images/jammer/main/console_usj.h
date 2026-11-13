/*
 * console_usj.h -- the jammer's console on the X2's USB Serial/JTAG port.
 * TEST EQUIPMENT, NOT FOR VEHICLE (x2_test_images SPEC T8).
 *
 * The only way the image prints: ESP-IDF's own console is off and its log
 * level is none (sdkconfig.defaults), and every line written here starts
 * with CONSOLE_PREFIX.
 *
 * The prefix is the PlatformIO jammer's "JAMMER ", which the gen-inhibit
 * harness parses (x2_jammer.py's parse_line). NOT FOR VEHICLE is in the
 * image name and the boot banner (SPEC T8 as the user ruled on 2026-11-12,
 * in the gen-inhibit reviewer's session: no longer every console line).
 */
#ifndef CONSOLE_USJ_H
#define CONSOLE_USJ_H

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#define CONSOLE_PREFIX   "JAMMER "
#define CONSOLE_LINE_MAX 200    /* prefix, text and line end */

/* The names start jcon_: ESP-IDF's esp_vfs_console already has a
 * console_read() and console_init(). */
esp_err_t jcon_init(void);

/* One line: CONSOLE_PREFIX, text, "\r\n", written whole under a lock, with
 * a short timeout so an unread port cannot hold the caller. Text past the
 * line's room is cut, never wrapped onto a line without the prefix. */
void jcon_line(const char *text);
void jcon_linef(const char *fmt, ...);

/* Bytes from the host; returns the count (0 on timeout). */
int jcon_read(uint8_t *buf, size_t n, TickType_t wait);

#endif
