/*
 * jam_internal.h -- jam_pulse's internals, shared by its two files and the
 * host tests (jammer/test/). TEST EQUIPMENT, NOT FOR VEHICLE. Not part of
 * the component's interface: no caller outside jam_pulse includes it.
 */
#ifndef JAM_INTERNAL_H
#define JAM_INTERNAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"

#if defined(JAM_TEST_STOP_A_ONLY) && defined(JAM_TEST_STOP_B_ONLY)
#error "JAM_TEST_STOP_A_ONLY and JAM_TEST_STOP_B_ONLY exclude each other"
#endif

typedef enum { JAM_K_TRAIN, JAM_K_PULSES } jam_kind_t;

typedef struct {
    jam_kind_t kind;
    uint32_t   w_us, p_us, ms, n, interval_ms;
} jam_cmd_t;

typedef enum {
    JP_EMPTY,       /* nothing on the line: no reply */
    JP_NOT_MINE,    /* unknown first word */
    JP_STOP,
    JP_STATUS,
    JP_REFUSED,     /* note holds the ERR reply; nothing is transmitted */
    JP_CMD,         /* *out is a command; note holds a CAPPED line or "" */
} jp_result_t;

/* jam_parse.c: pure, no hardware. busy is whether a command is running;
 * the order of the refusals is the PlatformIO jammer's (arguments, busy,
 * then ranges). note is at least JAM_REPLY_MAX long. */
jp_result_t jam_parse(const char *line, bool busy, jam_cmd_t *out,
                      char *note, size_t note_len);

/* The deadline a command runs to, in ms from its start. */
uint32_t jam_cmd_ms(const jam_cmd_t *c);

/* jam_engine.c: the jam task's two halves, so the host tests can run one
 * command to its end. */
bool jam_engine_take(jam_cmd_t *c, TickType_t wait);
void jam_engine_run(const jam_cmd_t *c);

#endif
