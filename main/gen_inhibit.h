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
#include "driver/twai.h"    /* twai_state_t, for drv_state_name() below */

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
 * 4096 SINCE 2026-10-10, sized from the bound rather than chosen. 3072 was NOT
 * BIG ENOUGH and had not been since the balance terms landed; the bound that
 * was meant to catch it was only looking at part of the page.
 *
 * The history, because each step was reactive and the pattern is the point.
 * 1400 cut the scheduler block off (defect D10). 2048 cut a long arm's page
 * mid-number, and because config_server.c truncates instead of overrunning,
 * the device answered HTTP 200 with a body that would not parse -- which read
 * as "the device did not answer" and voided two 90 s bench arms. 3072 was set
 * on 2026-09-29 from a worst case of 2507, computed by widening every integer
 * to a full u32.
 *
 * THAT WORST CASE WAS INCOMPLETE IN THREE WAYS, found by the gen-inhibit tester
 * on 2026-10-10 (runs/case26_worst_case_ddddfea_20261010.txt) and now computed
 * inside shim case 26:
 *
 *   - the 20 histogram bucket counts are integers inside ARRAYS, and the
 *     widening only matched integers following a ':' (+178 at u32 width);
 *   - the text fields were left at whatever the shim's own page happened to
 *     carry, which is empty or short in every one of them, while the tables
 *     behind abort_reason and arm_block can return 60 characters and self_off
 *     52 (+190);
 *   - `false` is a byte longer than `true` (+11).
 *
 * With those, the page's worst case is 3201 against a 3072 cap: over by 129,
 * and over since the 5.2 item 6 balance terms were added. Nothing had failed on
 * the bench because reaching it needs a long arm AND a latched abort AND
 * five-figure counters at the same moment.
 *
 * 4096 leaves 894 bytes of headroom over that -- roughly a dozen more u32
 * fields at full width, or one more 60-character text field with room to
 * spare. THE COST IS STATIC RAM AND NOT STACK: both page buffers are
 * function-local statics in config_server.c (:1622, :1673), so this is
 * 2 x 1024 bytes of .bss and nothing on the httpd task's stack. Measured:
 * .bss 41,824 -> 43,872 bytes, DRAM 43.29% -> 43.93% used on esp32c3.
 *
 * Anything appended to gen_inhibit_get_stats_json() has to be checked against
 * this, and shim case 26 does it: it renders the page, then widens every
 * integer to its type's full width, every text field to the longest its own
 * table can return, and every boolean to `false`. It asks the tables rather
 * than copying their strings, so a longer name added later is inside the bound
 * from the moment it exists.
 */
#define GI_STATUS_PAGE_CAP 4096

int gen_inhibit_get_stats_json(char *buf, int buflen);

/*
 * The buffer reset_reason_name() is given, and therefore the widest string
 * spec 11's `reset_reason` field can carry: GI_RESET_REASON_LEN - 1.
 *
 * Longest name is 10 ("PWR_GLITCH", "CPU_LOCKUP") and the number fallback is at
 * most 11 ("-2147483648"), so 12 would do; 16 leaves room for a longer name
 * arriving from ESP-IDF without that becoming a truncation.
 *
 * HERE RATHER THAN IN THE .c SO THE BOUND CAN BE ASKED FOR, not copied. Shim
 * case 26 has to know the longest value every text field on the page can take,
 * and a case that hard-codes 10 for "PWR_GLITCH" is a second copy of a table
 * that will not be updated with the first. The four core name tables are
 * reachable through gi_abort_name() and friends; these two were not.
 */
#define GI_RESET_REASON_LEN 16

/*
 * The driver-state name the page's `drv.state` carries. Exported for the same
 * reason: case 26 asks it for its longest value.
 */
const char *drv_state_name(twai_state_t s);

/* Discard all accumulated samples. */
void gen_inhibit_reset_stats(void);

#ifdef __cplusplus
}
#endif

#endif /* GEN_INHIBIT_H */
