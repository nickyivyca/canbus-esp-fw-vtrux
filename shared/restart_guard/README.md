# shared/restart_guard/

Takes the CAN ports off the bus at every software restart, a crash
included, before the CPU resets, as one ESP-IDF component,
`vtrux/restart_guard` (`SPEC.md` G1). It is made at gen-inhibit's port step
1c from gen-inhibit's `gi_restart_quiesce.c`; the charge interposer adopts
it in its stage (b). Each port is stopped by its own driver, through
`shared/can_port` C7, and put into standby through the board
(`shared/boards` B5). Its `test_app/` holds the restart test (G5).

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

A project takes it from git at its pinned `shared/main` tag, with
`vtrux/can_port` and its board. Depending on the component is enough: it
sets its own link-time wrap (G2).

## Files

| File | What |
|---|---|
| `include/restart_guard.h`, `restart_guard.c` | the guard (G2, G3): `__wrap_esp_restart_noos()`, which with interrupts off runs, for each CAN port the board has in port order, `can_port`'s restart stop (C7) then `board_can_standby(port, true)`, then writes the record (G4) and calls the real `esp_restart_noos()`; and the record's reader calls, for the test app. The record is gen-inhibit's `gi_restart_record_t` at the move, as `restart_guard_record_t` |
| `test_app/` | G5: the restart test, moved from gen-inhibit's `gi_restart_test.c` (`restart_guard_test.c`, `include/restart_guard_test.h`): `GET` and `POST /gi_quiesce`, the snapshot before `app_main` and the pre-install one, `restart_guard_test_pre_install()`, which the project calls just before its first driver install, and `restart_guard_test_register()`, from its URI hook. Compiled in only by `RESTART_GUARD_TEST`. It reads the standby pin from `board_pins.h` (`shared/boards` B6) |
| `CMakeLists.txt`, `idf_component.yml` | the component: the link-time wrap it sets itself (G2), the test app by its switch, and the project's one board component, found as `vtrux/twai_port` finds it |
| `unit/` | the guard's unit tests, their stand-ins and their mutants (`unit/README.md`) |

The build-switch table and the list of files that use pins,
`vtrux_checks.json`, are the tester's, and arrive from it.

## Who owns which file (SPEC G6)

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, G1 to G6, and why each requirement is written as it is | the reviewers, as above |
| the guard's code and its unit tests | G1 to G4 | the implementor of the project that needs the change |
| `test_app/` | G5: the restart test, compiled in only by `RESTART_GUARD_TEST` | the implementor of the project that needs the change |
| `unit/mutate.py` | the unit tests' mutants: one behaviour of the guard broken at a time, each of which the unit tests must fail | the implementor, as the unit tests |
| `VERIFICATION.md` | test evidence | the tester |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20) and its list of files that use pins (R30) | the tester of the project that needs the change |
