/*
 * twai_port_t19_ll -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T19).
 * Private to test_app/.
 *
 * The register reads and writes of the T19 app's own commands (twai_port_t19.c)
 * that neither T8's file nor the probe's register layer gives: the whole
 * status and mode registers, both error counters and the bus timing for
 * `regs` and `info`, and, for `resetprobe` (the tester's check of what reset
 * mode does to a frame left in the transmit buffer, apart from the port's
 * switch), reset mode entered at a chosen point, a mode set, reset mode left,
 * and an abort. S3 only: twai_port_t19_ll_esp32s3.c, compiled only in the T19
 * build (CMakeLists.txt), never in T8's file (charge-interposer reviewer,
 * 2026-11-27).
 */
#ifndef TWAI_PORT_T19_LL_H
#define TWAI_PORT_T19_LL_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t status;    /* the status register, every bit as the chip gives it */
    uint32_t mode;      /* the mode register: RM bit 0, LOM bit 1, STM bit 2 */
    uint32_t tec, rec;
    uint32_t btr0, btr1;    /* the bus timing registers */
} t19_regs_t;

/* The TWAI clock is on and the peripheral is not held in reset (T12's
 * bits): only then are the controller's registers read. */
bool t19_ll_clock_on(void);

/* Read them all; changes nothing. Only with t19_ll_clock_on(). */
void t19_ll_regs(t19_regs_t *out);

/* The status register, as the chip gives it. */
uint32_t t19_ll_status(void);

/*
 * Enter reset mode in one critical section with a status read just before
 * and just after. With `if_ts0`, only if the buffer holds a frame (TBS = 0)
 * and the controller is not transmitting (TS = 0); the return says whether it
 * was entered.
 */
bool t19_ll_enter_reset(bool if_ts0, uint32_t *st_before, uint32_t *st_after);

/* In reset mode: set listen-only or normal, then leave reset mode. */
void t19_ll_leave_reset(bool listen_only);

/* The abort command, with the status read just before and after it. */
void t19_ll_abort(uint32_t *st_before, uint32_t *st_after);

#define T19_ST_TBS  (1u << 2)
#define T19_ST_TCS  (1u << 3)
#define T19_ST_RS   (1u << 4)
#define T19_ST_TS   (1u << 5)
#define T19_MODE_RM  (1u << 0)
#define T19_MODE_LOM (1u << 1)
#define T19_MODE_STM (1u << 2)

#endif /* TWAI_PORT_T19_LL_H */
