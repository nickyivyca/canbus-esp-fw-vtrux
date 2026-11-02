# shared/restart_guard/unit/

The guard's unit tests (`SPEC.md` G6: the implementor's): `restart_guard.c`
compiled on the host against a scripted board, a scripted `can_port` restart
stop (C7) and a real `esp_restart_noos()` that jumps back to the case.
Self-checks, not verification. The test app (G5) is not built here: it is
the HTTP pages and the crash, which need a device.

    make -C shared/restart_guard/unit run      # from the repo root; `asan` for the sanitiser build

The binary goes to `scratch/restart_guard-unit/` at the worktree's root.

| File | What |
|---|---|
| `test_restart_guard.c` | the cases: the record's layout word by word and its magic (gen-inhibit's `gi_restart_record_t`, G4) and its checksum against gen-inhibit's own; on one TWAI port, interrupts off first, the stop, then standby, then the real restart, with the stop's report in the record, and a clocked port held in reset; the entry count across restarts, and from 1 after a power-on or a bad checksum; the test build's record init and copy; a stop that answers "not supported"; every port in port order with standby one bit per port; the TWAI fields only from the TWAI port's stop; a board with no CAN ports. It includes `restart_guard.c` so each case can reach the record. |
| `mock/` | host stand-ins for `sdkconfig.h` (a RISC-V target) and `riscv/rv_utils.h`. `esp_err.h`, `esp_attr.h`, `board.h` and the driver types under `can_port.h` are `shared/twai_port/unit/mock/`'s. |
| `Makefile` | the build |
| `mutate.py` | the mutants: each breaks one behaviour of `restart_guard.c` in a copy, and the run passes only if the unmutated build passes and every mutant builds and fails; a mutant that does not build, or whose text does not occur once, fails the run. `python3 shared/restart_guard/unit/mutate.py [--only N,N...]` from the repo root, in Linux or WSL; its copies go in its own folder under `scratch/restart_guard-unit-mutants/`, removed when it ends. |
