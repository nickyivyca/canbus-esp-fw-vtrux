/*
 * vtrux/restart_guard: every software restart, a crash included, takes the
 * CAN ports off the bus before the CPU resets (SPEC.md G2).
 *
 * A project gets the guard by depending on the component, with no switch and
 * no call: the component sets its own link-time wrap of esp_restart_noos()
 * (CMakeLists.txt). What a test build adds -- a reader for the record below,
 * a crash trigger and the snapshots -- is the test app, compiled in only by
 * the component's switch RESTART_GUARD_TEST (test_app/, G5).
 */
#ifndef RESTART_GUARD_H
#define RESTART_GUARD_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * THE RECORD OF THE LAST RESTART (G4), written by the guard in every build,
 * so that a test build runs exactly the guard a release build runs. Only a
 * test build reads it. RTC no-init memory: it survives a software restart and
 * a panic, and the magic and checksum tell it from the garbage a power-on
 * leaves there.
 *
 * THE LAYOUT AND MAGIC ARE gen-inhibit's gi_restart_record_t's at the move
 * (user, 2026-11-01), so a record left by an image from before the move reads
 * the same after it. The TWAI fields are the TWAI port's stop report
 * (twai_port T12, through can_port C7). did_stdby is one bit per port; on a
 * one-port board it reads 0 or 1, as it did.
 */
typedef struct {
    uint32_t magic;
    uint32_t entries;       /* guard entries; keeps counting across restarts */
    uint32_t twai_clk;      /* TWAI bus clock enabled at entry */
    uint32_t twai_rst;      /* TWAI held in peripheral reset at entry */
    uint32_t in_reset;      /* controller already in reset mode at entry */
    uint32_t st_entry;      /* status register at entry */
    uint32_t tec;
    uint32_t rec;
    uint32_t did_twai;      /* the controller was put in reset mode */
    uint32_t st_reset;      /* status register as reset mode was entered */
    uint32_t wait_us;       /* time spent waiting for the bus between frames */
    uint32_t timed_out;     /* the bus was not between frames within 1 ms */
    uint32_t did_stdby;     /* bit p: port p's transceiver was put into standby */
    uint32_t aborts;        /* frames aborted while still awaiting the bus */
    uint32_t check;
} restart_guard_record_t;

#define RESTART_GUARD_RECORD_MAGIC 0x51E5CE0Du

/* Is the record in RTC memory one the guard wrote (or a test build initialised)? */
bool restart_guard_record_valid(void);

/* A copy of it, whether valid or not; check restart_guard_record_valid() first. */
void restart_guard_record_get(restart_guard_record_t *out);

/*
 * Make the record valid with a count of 0. Called only by a test build, at
 * start-up, when it found no valid record, so that the first restart after a
 * power-on reads entries 1.
 */
void restart_guard_record_init(void);

#ifdef __cplusplus
}
#endif

#endif /* RESTART_GUARD_H */
