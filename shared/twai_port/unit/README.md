# shared/twai_port/unit/

The port's unit tests (`SPEC.md` T11: the implementor's): `twai_port.c` and
the C3 register file, `twai_port_ll_esp32c3.c`, compiled on the host
against a scripted stub of the driver API. Self-checks, not verification:
acceptance runs on the testers' T10 fake in `test/`.

    make -C shared/twai_port/unit run      # from the repo root; `asan` for the sanitiser build

The binary goes to `scratch/twai_port-unit/` at the worktree's root.

| File | What |
|---|---|
| `test_twai_port.c` | the cases: the install and its hook, each failed step (report and abort), the teardown from each state, its refusal while recovering and a refused stop or uninstall leaving the transceiver out of standby, the queues it counts, mode changes and reconfigures, send and its counts (`tx_dropped`, `tx_refused`, single-shot, self-reception), receive and the RTR drop within the caller's wait, the counts across reinstalls, bus-off once per episode, the alerts and the held transmit failure, automatic recovery, and the C3's buffer state and abort. It includes the port's `.c` files so each case can reset their statics. |
| `stub.c`, `stub.h` | the scripted stub: every driver, board and tick call returns what the case set and is recorded in order. It models no controller behaviour: no states, no alerts of its own, no arbitration (README.md's `unit/` row, gen-inhibit reviewer, 2026-10-31). |
| `mock/` | host stand-ins for the ESP-IDF 5.4.1 headers the port includes (`driver/twai.h` with the real field names and alert values, `hal/twai_ll.h` with a status register the case sets, `esp_err.h`, `esp_log.h`, `esp_attr.h`, FreeRTOS, `board.h`). `shared/can_port/unit/` builds against the same ones. |
| `Makefile` | the build |
