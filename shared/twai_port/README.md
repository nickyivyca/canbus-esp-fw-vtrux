# shared/twai_port/

The driver for the chip's built-in CAN controller (TWAI), as one ESP-IDF
component, `vtrux/twai_port` (`SPEC.md` T1), behind `shared/can_port`. It is
made at gen-inhibit's port step 1b from gen-inhibit's `can.c` and the TWAI
register code in its `gen_inhibit.c`; the charge interposer adopts it at its
step 2b, in place of `port_twai.cpp`. Its `test/` folder holds the one host
fake of the TWAI driver both projects' host builds use (T10).

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

## Files

| File | What |
|---|---|
| `include/twai_port.h`, `twai_port.c` | the port: the project's choices (T2), install and teardown (T3, T4), mode changes, receive and the one send (T6), the one alert reader (T5), the counters and status (T7), and its `can_port` backend table, `twai_port_ops` |
| `twai_port_ll_esp32c3.c` | T8 on the C3: the buffer state and the abort-if-awaiting, register level, checked against ESP-IDF v5.4.1 |
| `twai_port_ll_unsupported.c` | T8 on any other chip: both report unsupported |
| `CMakeLists.txt`, `idf_component.yml` | the component: the register file by target, and the project's one board component, found as `vtrux/wican` finds it |
| `unit/` | the port's unit tests and their scripted stub (`unit/README.md`) |

A project takes it from git at its pinned `shared/main` tag, with
`vtrux/can_port` and its board, fills a `twai_port_config_t`, calls
`twai_port_setup()` and registers `twai_port_ops` with `can_port` for the
port index, all before `wican_init()`.

## Who owns which file (SPEC T11)

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, T1 to T11, and why each requirement is written as it is | the reviewers, as above |
| the port's code and its unit tests | T1 to T9 | the implementor of the project that needs the change |
| `unit/`'s scripted stub of the driver API | part of the unit tests: each case sets the driver's returns and checks the port's calls; it models no controller behaviour (no states, alerts or arbitration of its own) | the implementor, as the unit tests (gen-inhibit reviewer, 2026-10-31) |
| `test/`'s fake of the TWAI driver | T10: what the controller does, in its states, recovery and per-chip behaviour | the tester of the project that needs the change (user, 2026-10-31, in both reviewers' sessions) |
| `VERIFICATION.md` | test evidence | the tester |
</content>
</invoke>
