/*
 * gi_restart_test -- TEST BUILD ONLY. Never in a prod build (spec 12.4, "no
 * error frames at a restart", added 2026-10-13, user).
 *
 * What spec 12.4's row needs that a prod build must not carry: a reader for the
 * restart record the hook writes in every build (gi_restart_quiesce.h), a way
 * to crash the device on request, and a view of the controller and the
 * transceiver from before app_main up to the first driver install.
 *
 * The source is not in the component unless the build defines
 * GI_TEST_RESTART_QUIESCE -- see main/CMakeLists.txt -- for the reason
 * gi_txabort_probe.c is kept out: a source that is not in the build cannot be
 * linked by accident. The shared build tooling's build-switch check
 * (shared/tools SPEC R20, rows in vtrux_checks.json) refuses a release image
 * that carries these symbols.
 *
 *   idf.py -B build-restart-test -DGI_TEST_RESTART_QUIESCE=1 build
 *
 * THE INTERFACE:
 *
 *   GET  /gi_quiesce                      the restart record, the snapshots,
 *                                         the reset reason, the versions and
 *                                         the OTA partitions
 *   POST /gi_quiesce?crash=illegal|abort  answer 200, then panic 300 ms later
 *                                         from a task of its own, in any mode
 */
#ifndef GI_RESTART_TEST_H
#define GI_RESTART_TEST_H

#include "esp_err.h"
#include "esp_http_server.h"

/* How many URI handlers gi_restart_test_register() adds: GET and POST. */
#define GI_RESTART_TEST_URI_COUNT 2

esp_err_t gi_restart_test_register(httpd_handle_t server);

/*
 * Called by can_enable() immediately before twai_driver_install(), in a test
 * build only. Records the controller and CAN_STDBY as they stood just before
 * the first install after boot -- the far end of "both stay that way until the
 * driver is installed again" (spec 3). Later installs are not recorded.
 */
void gi_restart_test_pre_install(void);

#endif /* GI_RESTART_TEST_H */
