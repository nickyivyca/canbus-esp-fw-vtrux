/*
 * gi_restart_quiesce -- every software restart takes the CAN controller off the
 * bus first (spec 3, "No restart leaves the controller on the bus", added
 * 2026-10-13, user).
 *
 * In every build. There is no switch: the step is the behaviour. What a test
 * build adds -- a reader for the record below, a crash trigger and a snapshot
 * taken before app_main -- lives in gi_restart_test.c, which is not in the
 * component unless GI_TEST_RESTART_QUIESCE is set (main/CMakeLists.txt).
 */
#ifndef GI_RESTART_QUIESCE_H
#define GI_RESTART_QUIESCE_H

#include <stdint.h>
#include <stdbool.h>

/*
 * THE RECORD OF THE LAST RESTART, written by the hook in every build, so that a
 * test build runs exactly the hook a prod build runs. Only a test build reads
 * it. RTC no-init memory: it survives a software restart and a panic, and the
 * magic and checksum tell it from the garbage a power-on leaves there.
 */
typedef struct {
    uint32_t magic;
    uint32_t entries;       /* hook entries; keeps counting across restarts */
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
    uint32_t did_stdby;     /* CAN_STDBY was driven to standby */
    uint32_t check;
} gi_restart_record_t;

#define GI_RESTART_RECORD_MAGIC 0x51E5CE0Du

/* Is the record in RTC memory one the hook wrote (or a test build initialised)? */
bool gi_restart_record_valid(void);

/* A copy of it, whether valid or not; check gi_restart_record_valid() first. */
void gi_restart_record_get(gi_restart_record_t *out);

/*
 * Make the record valid with a count of 0. Called only by a test build, at
 * start-up, when it found no valid record, so that the first restart after a
 * power-on reads entries 1.
 */
void gi_restart_record_init(void);

#endif /* GI_RESTART_QUIESCE_H */
