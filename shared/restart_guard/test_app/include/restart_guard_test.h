/*
 * restart_guard_test -- TEST BUILD ONLY. Never in a release build
 * (vtrux/restart_guard SPEC.md G5, shared/tools R20). Moved at port step 1c
 * from gen-inhibit's main/gi_restart_test.h, with its URIs, its page's fields
 * and the record unchanged (user, 2026-11-01), so the bench reads them as
 * before.
 *
 * What a restart test needs that a release build must not carry: a reader for
 * the record the guard writes in every build (restart_guard.h), a way to crash
 * the device on request, and a view of the controller and the transceiver
 * from before app_main up to the first driver install.
 *
 * The source is not in the component unless the build sets the component's
 * switch RESTART_GUARD_TEST -- see the component's CMakeLists.txt -- because a
 * source that is not in the build cannot be linked by accident. The shared
 * build tooling's build-switch check (shared/tools SPEC R20, rows in
 * vtrux_checks.json) refuses a release image that carries these symbols.
 *
 *   idf.py -B build-restart-test -DRESTART_GUARD_TEST=1 build
 *
 * THE INTERFACE:
 *
 *   GET  /gi_quiesce                      the restart record, the snapshots,
 *                                         the reset reason, the versions and
 *                                         the OTA partitions
 *   POST /gi_quiesce?crash=illegal|abort  answer 200, then panic 300 ms later
 *                                         from a task of its own, in any mode
 */
#ifndef RESTART_GUARD_TEST_H
#define RESTART_GUARD_TEST_H

#include "esp_err.h"
#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

/* How many URI handlers restart_guard_test_register() adds: GET and POST. */
#define RESTART_GUARD_TEST_URI_COUNT 2

/* Register the two URIs, from the project's URI hook (shared/wican W8). */
esp_err_t restart_guard_test_register(httpd_handle_t server);

/*
 * The pre-install snapshot. The project calls it immediately before its
 * first driver install of this boot, in a test build only. Records the
 * controller and the standby pin as they stood just before the first install
 * after boot -- the far end of "both stay that way until the driver is
 * installed again" (gen-inhibit spec 3). Later calls are not recorded.
 */
void restart_guard_test_pre_install(void);

#ifdef __cplusplus
}
#endif

#endif /* RESTART_GUARD_TEST_H */
