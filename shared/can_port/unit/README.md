# shared/can_port/unit/

can_port's unit tests (`SPEC.md` C6: the implementor's): `can_port.c` and
the stock calls of `can_stock.c`, compiled on the host against a scripted
backend, an ops table whose every operation returns what the case set and
is recorded; and the restart stop of `can_port_restart.c` (C7), against a
scripted board and TWAI restart stop, built twice, with and without the TWAI
driver. Self-checks, not verification.

    make -C shared/can_port/unit run      # from the repo root; `asan` for the sanitiser build

The binaries go to `scratch/can_port-unit/` at the worktree's root.

| File | What |
|---|---|
| `test_can_port.c` | the cases: registration, a port with no backend, the C2 operations passed through with their context, the stock enable (the rate-code table, the mode from `can_set_silent()`, the filter, then the bring-up and the flush), the setters refused while on, a failed configure or bring-up restarting (C3), the reporting form of the stock enable (`can_port_stock_enable()`: the error back, the bus left off, no restart), the stock disable through the backend's teardown even when never enabled (C5) and a refused one keeping the bus on, the stock send and receive on port 0, the owner's refusal (C4: every refusal counted, the log at most once in 5 s, the owner's own path untouched), and the queries. It includes the component's `.c` files so each case can reset their statics, and supplies the event group, timer and clock. |
| `test_can_port_restart.c` | the restart stop's cases (C7): a TWAI port to `twai_port`'s stop with its report and its answer, an SPI port not supported, a port the board does not have or a NULL report refused, and the port's kind deciding rather than its index; no backend is registered. Built as `test_can_port_restart_twai` (`CAN_PORT_HAVE_TWAI_PORT=1`, as a build with `vtrux/twai_port`) and `test_can_port_restart_none` (`=0`: a TWAI port not supported, the report cleared) |
| `mock/` | host stand-ins for FreeRTOS's event groups, timers and tasks, and `esp_timer.h`. The driver types, `esp_err.h`, `esp_log.h`, `esp_attr.h` and `board.h` are `shared/twai_port/unit/mock/`'s. |
| `Makefile` | the build |
| `mutate.py` | the mutants: each breaks one behaviour of `can_port.c`, `can_stock.c` or `can_port_restart.c` in a copy, and the run passes only if the unmutated build passes and every mutant builds and fails all three binaries' runs; a mutant that does not build, or whose text does not occur once, fails the run. The owner's refusal (C4) is among them: it was gen-inhibit's `main/can.c` until its port step 1b, where gen-inhibit's shim mutated it. `python3 shared/can_port/unit/mutate.py [--only N,N...]` from the repo root, in Linux or WSL; its copies go in its own folder under `scratch/can_port-unit-mutants/`, removed when it ends. |
