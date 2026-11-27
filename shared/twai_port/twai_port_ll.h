/*
 * vtrux/twai_port: the calls twai_port.c makes into the chip's register file,
 * twai_port_ll_<chip>.c, for the kept-driver switch (SPEC.md T14, T15).
 * Private to the component: not in include/.
 *
 * T8's buffer state and abort and T12's restart stop are public, in
 * twai_port.h. These are the switch's register steps. Each chip's file gives
 * all of them; on a chip where the switch is not measured,
 * twai_port_ll_switch_supported() is false, twai_port_setup() refuses the
 * switch, and the others are never called.
 */
#ifndef TWAI_PORT_LL_H
#define TWAI_PORT_LL_H

#include <stdbool.h>
#include <stdint.h>
#include "twai_port.h"

/*
 * T14: the switch is measured on this chip (T19 for the S3).
 *
 * TWAI_PORT_HOST_SWITCH, HOST BUILDS ONLY: set by the testers' host build on
 * the T10 fake (test/Makefile), it makes the S3 file report the switch as
 * supported, so the port-on-fake tests run the real switch code before the
 * S3 measurement. T8 and T12 stay as they are. No image may carry it: the
 * component's CMakeLists.txt stops an ESP-IDF build that sets it, and this
 * header, which twai_port.c and every register file include, stops with
 * #error where ESP_PLATFORM is defined (every ESP-IDF compile). The
 * build-switch row is the tester's (vtrux_checks.json).
 */
#if defined(TWAI_PORT_HOST_SWITCH) && defined(ESP_PLATFORM)
#error "TWAI_PORT_HOST_SWITCH is for host builds on the T10 fake only"
#endif
bool twai_port_ll_switch_supported(void);

/*
 * T8's buffer state and abort-if-awaiting for the switch's own use (T17),
 * whether or not the public twai_port_buf_state() and
 * twai_port_abort_if_awaiting() report themselves supported. Called only
 * where twai_port_ll_switch_supported() is true.
 */
twai_port_buf_t twai_port_ll_buf_state(void);
twai_port_buf_t twai_port_ll_abort_if_awaiting(void);

/* The status register as reset mode was entered, and the buffer state it
 * gives. */
typedef struct {
    uint32_t status;
    twai_port_buf_t buf;
} twai_port_ll_entry_t;

/*
 * Put the controller into reset mode, reading the status register just
 * before (T16's place: no wait for the gap, held by the user 2026-11-27).
 * Nothing transmits or receives until twai_port_ll_switch_leave().
 */
void twai_port_ll_switch_enter(twai_port_ll_entry_t *out);

/*
 * In reset mode: set listen-only or normal, write the receive error counter
 * (`rec`, 128 or 0, T15), read both counters back, and leave reset mode.
 */
void twai_port_ll_switch_leave(bool listen_only, uint32_t rec,
                               uint32_t *rec_back, uint32_t *tec_back);

/* T15: the two error counters, from the registers. */
void twai_port_ll_counters(uint32_t *tec, uint32_t *rec);

#endif
