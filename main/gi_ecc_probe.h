/*
 * gi_ecc_probe -- MEASUREMENT BUILD ONLY. Never flash it to the truck.
 *
 * B6 (review tracker, ruled 2026-10-20, user: investigate first). One jammer
 * pulse is sometimes counted as 10-12 bus errors, and the device record cannot
 * say why. This build records, for every bus-error interrupt the TWAI driver
 * counts, what the controller captured about it, and for every worker loop on
 * which the counter moved, what trip 6 booked. It changes no decision: trip
 * logic, timing and counting are the shipping build's.
 *
 * The source is not in the component unless the build defines
 * GI_INSTRUMENT_ECC -- see main/CMakeLists.txt -- for the reason
 * gi_txabort_probe.c is kept out: a source that is not in the build cannot be
 * linked by accident. tools/prod_build.py refuses a build directory configured
 * with the flag and an image carrying these symbols.
 *
 *   idf.py -B build-ecc -DGI_INSTRUMENT_ECC=1 build      (image suffix _ecc)
 *
 * WHERE A BUS ERROR IS COUNTED (ESP-IDF v5.4.1). The product never sees one
 * directly: it reads twai_get_status_info().bus_error_count once per worker
 * loop and books the delta (gen_inhibit_core.c, err_history_tick). The driver
 * increments that counter once per interrupt invocation whose decoded events
 * carry TWAI_HAL_EVENT_BUS_ERR (driver/twai/twai.c, twai_intr_handler_main),
 * and the HAL raises that event from the controller's bus error interrupt,
 * BEI (hal/twai_hal_iram.c, twai_hal_decode_interrupt). BEI is re-armed only
 * by reading the error code capture register (ECC), and the HAL reads it --
 * to release it, discarding the value -- inside twai_hal_get_events(), before
 * the driver sees the event. No alert is involved: the product enables only
 * TX_SUCCESS and TX_FAILED, and TWAI_ALERT_BUS_ERROR is not among them.
 *
 * HOW ECC IS CAPTURED WITHOUT LOSING EVENTS. The link wraps
 * twai_hal_get_events() (-Wl,--wrap, the way gi_restart_quiesce.c wraps
 * esp_restart_noos). The wrapper calls the real function first, unchanged, and
 * only then, when the returned events carry TWAI_HAL_EVENT_BUS_ERR, reads ECC,
 * TEC, REC and the status register into one ring entry. So:
 *
 *   - One entry per driver count, by construction: the wrapper runs inside the
 *     same interrupt invocation and the same driver critical section as the
 *     bus_error_count++ it precedes. The page reports both totals.
 *   - The HAL's own read-to-release happens exactly as in the shipping build.
 *     Reading ECC BEFORE it would release the capture early, let a second
 *     error re-raise BEI into the same pending bit, and so change the count
 *     being measured. Reading after it is a read of a register already
 *     released, which re-arms nothing.
 *   - ASSUMED, NOT SHOWN: that ECC still holds the captured value after the
 *     HAL's read, until the next bus error is captured. The register
 *     description in this IDF says nothing either way. If every entry reads
 *     the same value, the assumption is wrong and the codes mean nothing --
 *     the bench run is what shows which.
 *   - A KNOWN RACE, for the caveat list (reviewer, 2026-10-20). The HAL's read
 *     releases the capture, so a NEW error captured in the ~1 us before this
 *     probe's read is logged under the EARLIER interrupt's row, in place of
 *     the code the HAL released; the new error then raises its own interrupt
 *     and row, which reads its own code. At 500 kbit/s the window is under one
 *     bit time, so it is rare at the jammer's pulse spacing, but a burst of
 *     10-12 counts from one pulse is exactly where errors come close enough
 *     together for it to happen. Two adjacent rows with the same ecc inside a
 *     burst may be this.
 *   - The ring overwrites its oldest entry, and the page says how many were
 *     overwritten, so a lost entry is never silent.
 *
 * THE INTERFACE: GET /gi_ecc, JSON, read-only. Nothing is sent on CAN.
 */
#ifndef GI_ECC_PROBE_H
#define GI_ECC_PROBE_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_http_server.h"
#include "gen_inhibit_core.h"

/* One extra URI; config_server.c sizes its handler table by it. */
#define GI_ECC_URI_COUNT 1

esp_err_t gi_ecc_register(httpd_handle_t server);

/*
 * Called by the worker right after gi_tick(), with the err_last and
 * have_err_last the core held BEFORE the tick. Records an entry only when the
 * driver's counter moved since the last call, so a quiet bus costs one compare.
 */
void gi_ecc_note_tick(const gi_bus_t *bus, bool had_last_before,
                      uint32_t last_before, const gi_state_t *st,
                      const gi_events_t *ev);

#endif /* GI_ECC_PROBE_H */
