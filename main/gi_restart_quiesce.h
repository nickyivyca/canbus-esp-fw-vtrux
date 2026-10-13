/*
 * gi_restart_quiesce -- TRIAL BUILD ONLY. Never in an image that flies.
 *
 * The user's trial of 2026-10-13, put by the reviewer: take the CAN controller
 * off the bus before every software restart, and show that it prevents the
 * error burst a restart puts on the bus while the device is live (evidence doc
 * 12.4, "Error frames at a WiCAN restart, characterised"). It is not in the
 * spec. It exists to find out whether it should be.
 *
 * It exists only when the build defines GI_TRIAL_RESTART_QUIESCE, and the
 * source is not added to the component otherwise -- see main/CMakeLists.txt,
 * for the same reason gi_txabort_probe.c is kept out: a source that is not in
 * the build cannot be linked by accident.
 *
 *   idf.py -B build-quiesce -DGI_TRIAL_RESTART_QUIESCE=1 build
 *
 * THE INTERFACE (the tester's rig takes these from the command line):
 *
 *   POST /gi_quiesce?hook=1|0      (also on|off)  set the switch, answer as GET
 *   POST /gi_quiesce?crash=illegal|abort          panic 300 ms after answering
 *   GET  /gi_quiesce                              the switch, the last restart's
 *                                                 record and the boot snapshot
 *   GET  /gen_inhibit  gains three top-level fields in this build only:
 *        "quiesce_hook"     true|false, the switch
 *        "quiesce_entries"  hook entries since the record was last cold-
 *                           initialised, or null; +1 per restart of any kind
 *                           that goes through esp_restart_noos()
 *        "ota_running"      the running app partition's label
 */
#ifndef GI_RESTART_QUIESCE_H
#define GI_RESTART_QUIESCE_H

#include <stddef.h>
#include "esp_err.h"
#include "esp_http_server.h"

/* How many URI handlers gi_quiesce_register() adds: GET and POST. */
#define GI_QUIESCE_URI_COUNT 2

esp_err_t gi_quiesce_register(httpd_handle_t server);

/*
 * Add the three /gen_inhibit fields above to a page gen_inhibit_get_stats_json()
 * has just written, before its closing brace. Returns the new content length.
 * A page that is truncated, or has no room, is returned unchanged: the fields
 * are then ABSENT, which the rig reads as "not as requested", rather than
 * present on a damaged document.
 */
int gi_quiesce_append_page(char *page, int n, size_t cap);

#endif /* GI_RESTART_QUIESCE_H */
