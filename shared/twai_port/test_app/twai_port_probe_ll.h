/*
 * twai_port_probe_ll -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T13).
 *
 * The register layer of the probe's no-acknowledge modes (M1-M3), one file per
 * chip, chosen as T8's register file is chosen (CMakeLists.txt, IDF_TARGET):
 *
 *   twai_port_probe_ll_esp32c3.c     the C3, checked against ESP-IDF v5.4.2
 *   twai_port_probe_ll_unsupported.c any other chip: unsupported, touches
 *                                    nothing
 *
 * The modes' logic (twai_port_unacked.c) reads the controller only through
 * these calls, so the modes are the same on every chip and a chip gets them by
 * getting a file here. The S3's comes with the charge interposer's T19 work.
 *
 * Why a file of its own and not more calls in twai_port_ll_<chip>.c: that file
 * is in every image, release images included, and its abort is confined to
 * the abort-if-awaiting (gen-inhibit spec 12.4). The abort here is issued in
 * any state, so it is compiled only under TWAI_PORT_TXABORT_PROBE, as the rest
 * of the probe is.
 */
#ifndef TWAI_PORT_PROBE_LL_H
#define TWAI_PORT_PROBE_LL_H

#include <stdbool.h>
#include <stdint.h>

/*
 * The status bits the modes read, chip-neutral. The values are the bit
 * positions of the SJA1000-style status register, so the `st` numbers in an
 * M1-M3 answer read the same as `st_before` in the older phases' answers.
 */
#define TXP_ST_TBS  (1u << 2)   /* transmit buffer free */
#define TXP_ST_TCS  (1u << 3)   /* the last transmission completed */
#define TXP_ST_RS   (1u << 4)   /* receiving */
#define TXP_ST_TS   (1u << 5)   /* transmitting */
#define TXP_ST_ES   (1u << 6)   /* an error counter at or above the warning limit */
#define TXP_ST_BS   (1u << 7)   /* bus-off */
#define TXP_ST_ALL  (TXP_ST_TBS | TXP_ST_TCS | TXP_ST_RS | TXP_ST_TS \
                     | TXP_ST_ES | TXP_ST_BS)

/* True on a chip with a register file here; false: the modes refuse. */
bool txp_ll_supported(void);

/* The status register, as TXP_ST_* bits only. 0 on an unsupported chip. */
uint32_t txp_ll_status(void);

/* The transmit error counter. 0 on an unsupported chip. */
uint32_t txp_ll_tec(void);

typedef enum {
    TXP_ABORT_ALWAYS = 0,   /* issue the command whatever the status reads */
    TXP_ABORT_IF_TS0,       /* only if the buffer holds a frame (TBS = 0) and
                             * the controller is not transmitting (TS = 0) */
} txp_abort_when_t;

typedef struct {
    uint32_t st_before;     /* TXP_ST_* read just before the decision */
    uint32_t st_after;      /* ... just after the command, or the decision */
    bool     issued;        /* the abort command was written */
} txp_abort_t;

/*
 * Read the status, issue the abort command if `when` says so, and read the
 * status again, in one critical section, so the status the decision rests on
 * is the status the command met, give or take the cycles of one write. On an
 * unsupported chip: nothing issued, both reads 0.
 */
void txp_ll_abort(txp_abort_when_t when, txp_abort_t *out);

#endif /* TWAI_PORT_PROBE_LL_H */
