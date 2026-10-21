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
 * HOW ECC IS CAPTURED WITHOUT CHANGING THE COUNT. The link wraps
 * twai_hal_get_events() (-Wl,--wrap, the way gi_restart_quiesce.c wraps
 * esp_restart_noos), and the wrapper is a copy of the v5.4.1 function that
 * keeps the value of the HAL's own ECC read instead of discarding it. So:
 *
 *   - One entry per driver count, by construction: the wrapper runs inside the
 *     same interrupt invocation and the same driver critical section as the
 *     bus_error_count++ it precedes. The page reports both totals.
 *   - The register reads are the shipping build's, in the same order, and no
 *     others. The HAL's ECC read is the only one that can see the capture:
 *     the ESP32-C3 TRM (v1.4, 31.4.3.7) says the register "will no longer
 *     record new error information until it is cleared (via a read from the
 *     CPU)", and it resets to 0. Reading ECC before the HAL would release the
 *     capture early and could change the count being measured; reading it
 *     after, as the first version of this probe did (8abf16b), reads the
 *     cleared register, and every row of B6 session 1 read 0x00 (2026-10-20).
 *   - The copy is pinned: another IDF version, or one of the errata options
 *     whose branches it leaves out, refuses to build (gi_ecc_probe.c).
 *   - Liveness is on the page: isr_distinct_ecc, and isr_undefined_seg, the
 *     rows whose segment code the TRM does not define. 0x00 is one of those.
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
