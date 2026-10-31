# shared/can_port/

The CAN port interface both projects use, as one ESP-IDF component,
`vtrux/can_port` (`SPEC.md` C1): a backend per port index, and the stock
WiCAN code's CAN calls on port 0 (C3). It replaces gen-inhibit's `can.c` API
at port step 1b, and the charge interposer's `CanPort` at its step 2b.

The spec and `VALIDATION.md` belong to the reviewers: a change is drafted by
the reviewer of the project that needs it and ruled by the user in that
reviewer's session. `VERIFICATION.md` is the tester's, written when there is
code to test.

## Files

| File | What |
|---|---|
| `include/can_port.h`, `can_port.c` | the interface: the backend table per port index (C1), the operations of C2 by port, the status and its counters, the owner hook of C4, the stock enable's reporting form (C3) |
| `include/can.h`, `can_stock.c` | the stock WiCAN code's CAN calls on port 0 (C3), with the stock rate-code table, the owner's refusal (C4) and the teardown through the backend (C5); the stock enable restarts on a failed bring-up, and `can_port_stock_enable()` (declared in `can_port.h`) is its reporting form, for a project's own bring-up; made from gen-inhibit's `can.c`. `can.h` was `vtrux/wican`'s until 1b |
| `can_port_priv.h` | between the two `.c` files: the stock calls' configure and flush, and the owner hook's two calls |
| `CMakeLists.txt`, `idf_component.yml` | the component |
| `unit/` | the unit tests and their scripted backend (`unit/README.md`) |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20) |

## Who owns which file (SPEC C6)

| File | What | Owner |
|---|---|---|
| `SPEC.md`, `VALIDATION.md` | the component's spec, C1 to C6, and why each requirement is written as it is | the reviewers, as above |
| the interface's code and its unit tests | C1 to C5 | the implementor of the project that needs the change |
| `VERIFICATION.md` | test evidence | the tester |
| `vtrux_checks.json` | the component's build-switch table (`shared/tools` R20) | the tester of the project that needs the change |
</content>
</invoke>
