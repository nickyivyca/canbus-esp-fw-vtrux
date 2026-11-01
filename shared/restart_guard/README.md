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

## Who owns which file (SPEC G6)

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, G1 to G6, and why each requirement is written as it is | the reviewers, as above |
| the guard's code and its unit tests | G1 to G4 | the implementor of the project that needs the change |
| `test_app/` | G5: the restart test, compiled in only by `RESTART_GUARD_TEST` | the implementor of the project that needs the change |
| `VERIFICATION.md` | test evidence | the tester |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20) and its list of files that use pins (R30) | the tester of the project that needs the change |

The files themselves arrive with the implementor's code; the table above
is filled in then.
