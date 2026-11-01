# shared/restart_guard/test_app/

The restart test (`../SPEC.md` G5), compiled into a project's image only
when the build sets the component's switch `RESTART_GUARD_TEST`, and never
into a release image (`shared/tools` R20). Moved at port step 1c from
gen-inhibit's `gi_restart_test.c` and `.h`, with the URIs, the page's fields
and the record unchanged.

| File | What |
|---|---|
| `restart_guard_test.c` | `GET /gi_quiesce` (the record of the last restart, the controller and the standby pin before `app_main` and before the first driver install, the reset reason, the version and the OTA partitions) and `POST /gi_quiesce?crash=illegal\|abort` (answer, then panic 300 ms later from a task of its own). It reads the standby pin from the board's `board_pins.h` (`shared/boards` B6) |
| `include/restart_guard_test.h` | what the project calls: `restart_guard_test_register()` from its URI hook (`shared/wican` W8), with `RESTART_GUARD_TEST_URI_COUNT` handler slots, and `restart_guard_test_pre_install()` just before its first driver install |
