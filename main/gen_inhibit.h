/*
 * gen_inhibit -- Vtrux generator-inhibit transmitter.
 *
 * INHIBIT (mode 3) transmits a real zero-torque 0x051 on the powertrain bus,
 * rebuilt from each received VCM frame and stealing its next rolling counter.
 * The measuring modes it grew out of are still here and still useful: OBSERVE
 * (1) times 0x051 arrivals, RESPOND (2) answers each one with a 0x7F0 probe
 * at a fixed offset, which is how the RX-to-TX trail is measured.
 *
 * The question those modes exist to answer, and that no capture could:
 * when a VCM 0x051 frame arrives, how long until a frame of ours is on the
 * wire, and how tightly is that bounded?
 *
 * Why existing data cannot answer it: BUSMASTER timestamps host-side in
 * batches (up to 96 frames sharing one 100 us tick), so recorded inter-frame
 * times are the logger's, not the bus's. projects/vtrux/tools/gen_inhibit/
 * therefore sweeps jitter as a parameter rather than assuming a value.
 *
 * Why this path and not wican-fw's: main.c's can_rx_task drains with a
 * NON-BLOCKING can_receive(..., 0) and then vTaskDelay(pdMS_TO_TICKS(1)).
 * That alone injects up to 1 ms of latency -- most of the guard band we are
 * trying to fit inside. This component takes the bus with a dedicated
 * high-priority task doing a blocking twai_receive(), which the driver ISR
 * wakes directly.
 *
 * SAFETY -- read before arming on a vehicle:
 *
 *   INHIBIT mode transmits a real 0x051 that the GENE inverter acts on. It is
 *   gated by the spec section 7 arm interlocks, the section 6 releases and the
 *   7.1 key rule, and mode 3 is deliberately a separate value from the
 *   measuring modes so the real-ID transmit cannot be reached by accident.
 *   Spec section 13 is the list of what is known to be missing; read it before
 *   deciding a build is fit for the truck.
 *
 *   RESPOND mode transmits GEN_INHIBIT_PROBE_ID (0x7F0), never 0x051. It is a
 *   timing probe, not a command: nothing on the truck consumes that ID, so a
 *   probe frame emitted at the wrong instant cannot be mistaken for a torque
 *   command.
 *
 *   OBSERVE mode never transmits. Note it cannot use transceiver standby to
 *   guarantee that: on MCP2561/2 the STBY pin disables the receiver as well,
 *   so standby is fully silent rather than listen-only. Set the device's
 *   can_mode to "silent" (TWAI_MODE_LISTEN_ONLY) for a hardware-backed
 *   guarantee.
 */
#ifndef GEN_INHIBIT_H
#define GEN_INHIBIT_H

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * The VCM's generator torque command: the ID this component both reads and,
 * in INHIBIT, transmits. Section 4 of the spec is the frame layout.
 */
#define GEN_INHIBIT_VCM_ID      0x051

/* Timing probe. Deliberately an ID nothing on the truck consumes. */
#define GEN_INHIBIT_PROBE_ID    0x7F0

typedef enum
{
    GEN_INHIBIT_OFF = 0,    /* component idle; wican-fw owns the bus as usual */
    GEN_INHIBIT_OBSERVE,    /* timestamp 0x051 arrivals; transmit nothing */
    GEN_INHIBIT_RESPOND,    /* also emit a probe frame (0x7F0) at a fixed offset */
    GEN_INHIBIT_INHIBIT,    /* THE REAL THING: on each 0x051 RX, transmit a
                             * zero-torque 0x051 with the stolen next counter.
                             * Transmits the real command ID, so everything in
                             * spec sections 6, 7 and 7.1 gates it. */
    GEN_INHIBIT_PASSIVE,    /* spec 3.1: the whole INHIBIT decision path, with
                             * no 0x051 on the wire. Counts a would-transmit
                             * where INHIBIT would transmit, and broadcasts
                             * diag reporting the state it WOULD be in, so a
                             * log of a PASSIVE drive reads as a dry run.
                             * NOT silent -- the controller ACKs and diag goes
                             * out; use OBSERVE when nothing may reach the
                             * wire at all. */
} gen_inhibit_mode_t;

/* Start the worker. Call once, after can_init(). Starts in OFF. */
void gen_inhibit_init(void);

/* Mode change. `offset_us` applies to RESPOND: delay from RX to probe TX. */
esp_err_t gen_inhibit_set_mode(gen_inhibit_mode_t mode, uint32_t offset_us);

/* True while this component owns the TWAI receive path, so wican-fw's
 * can_rx_task can stand down rather than race us for frames. */
bool gen_inhibit_owns_bus(void);

/*
 * Force the worker out of the TWAI driver and keep it out until re-armed.
 *
 * can_disable() uninstalls the driver; doing that while the worker is blocked
 * inside twai_receive() frees the driver under it. Any task that is about to
 * tear the bus down -- the sleep monitor, an SLCAN close, a protocol switch --
 * MUST call this first. It blocks until the worker is provably outside the
 * driver (bounded by the receive timeout). Safe to call from the worker itself
 * (returns immediately) and safe when the worker never started.
 */
void gen_inhibit_quiesce(void);

/* JSON stats into `buf`. Returns bytes written. */
/*
 * The buffer gen_inhibit_get_stats_json() must be given.
 *
 * DEFECT D10, 2026-09-27: the scheduler block (gs_json) is appended to the end of
 * the page and took it to 1761 bytes, while both HTTP handlers in
 * config_server.c passed 1400. The page was cut mid-token, so the per-class
 * counters spec 11 requires were missing AND the body was not valid JSON --
 * returned with a 200, which is why it went unnoticed.
 *
 * Here rather than at each call site because a length and a capacity kept in
 * separate files with nothing comparing them is what produced the defect. E1
 * case 26 asserts the page fits this, so appending to the page fails a test
 * rather than truncating it.
 */
/*
 * 3072 SINCE 2026-09-29, measured rather than chosen. The page with every counter
 * at 0 is 1830 bytes; the same page with every integer widened to a full u32 is
 * 2507. At 2048 a long arm's page was cut mid-number, and because
 * config_server.c truncates instead of overrunning, the device answered HTTP 200
 * with a body that would not parse -- which read as "the device did not answer"
 * and voided two 90 s bench arms.
 *
 * Anything appended to gen_inhibit_get_stats_json() has to be checked against
 * this, and shim case 26 does it: it widens every integer in the rendered page to
 * u32 and requires that to fit, so the bound is enforced without needing an arm
 * that reaches those values.
 */
#define GI_STATUS_PAGE_CAP 3072

int gen_inhibit_get_stats_json(char *buf, int buflen);

/* Discard all accumulated samples. */
void gen_inhibit_reset_stats(void);

#ifdef __cplusplus
}
#endif

#endif /* GEN_INHIBIT_H */
