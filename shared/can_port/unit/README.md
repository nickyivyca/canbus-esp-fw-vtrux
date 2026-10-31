# shared/can_port/unit/

can_port's unit tests (`SPEC.md` C6: the implementor's): `can_port.c` and
the stock calls of `can_stock.c`, compiled on the host against a scripted
backend, an ops table whose every operation returns what the case set and
is recorded. Self-checks, not verification.

    make -C shared/can_port/unit run      # from the repo root; `asan` for the sanitiser build

The binary goes to `scratch/can_port-unit/` at the worktree's root.

| File | What |
|---|---|
| `test_can_port.c` | the cases: registration, a port with no backend, the C2 operations passed through with their context, the stock enable (the rate-code table, the mode from `can_set_silent()`, the filter, then the bring-up and the flush), the setters refused while on, a failed bring-up, the stock disable through the backend's teardown even when never enabled (C5) and a refused one keeping the bus on, the stock send and receive on port 0, the owner's refusal (C4: every refusal counted, the log at most once in 5 s, the owner's own path untouched), and the queries. It includes the component's `.c` files so each case can reset their statics, and supplies the event group, timer and clock. |
| `mock/` | host stand-ins for FreeRTOS's event groups, timers and tasks, and `esp_timer.h`. The driver types, `esp_err.h` and `esp_log.h` are `shared/twai_port/unit/mock/`'s. |
| `Makefile` | the build |
