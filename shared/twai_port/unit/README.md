# shared/twai_port/unit/

The port's unit tests (`SPEC.md` T11: the implementor's): `twai_port.c` with
the C3 register file, `twai_port_ll_esp32c3.c`, and with the S3 register
file, `twai_port_ll_esp32s3.c`, compiled on the host against a scripted stub
of the driver API. The S3 build is made three times: with the T19
measurement switch `TWAI_PORT_S3_PROBE`, where the S3 file runs; with the
testers' host-only `TWAI_PORT_HOST_SWITCH`, where the switch runs and T8 and
T12 report themselves unsupported; and with neither, where every S3 call
must report itself unsupported. Self-checks, not verification: acceptance
runs on the testers' T10 fake in `test/`.

    make -C shared/twai_port/unit run      # from the repo root; `asan` for the sanitiser build

The binaries go to `scratch/twai_port-unit/` at the worktree's root.

| File | What |
|---|---|
| `test_twai_port.c` | the C3 cases: the install and its hook, each failed step (report and abort), the teardown from each state, its refusal while recovering and a refused stop or uninstall leaving the transceiver out of standby, the queues it counts, mode changes and reconfigures, send and its counts (`tx_dropped`, `tx_refused`, single-shot, self-reception), receive and the RTR drop within the caller's wait, the counts across reinstalls, bus-off once per episode, the alerts and the held transmit failure, automatic recovery and the receive losses of its restart, the flush and the frames it discards, the C3's buffer state and abort, the C3's restart stop (T12: nothing touched with the clock off or the peripheral held in reset, reset mode at once between frames, the 2 us wait for another node's frame, the 1 ms bound and reset mode on its time-out, the abort on every poll while ours awaits, and the report's every field), and the kept-driver switch refused on the C3 (T14). It includes the port's `.c` files so each case can reset their statics. |
| `test_twai_port_s3.c` | the S3 cases. With the switch: the kept-driver switch (T14: installed in normal mode and switched to listen-only before the transceiver leaves standby, sends refused in listen-only, refused while not running, no-ACK self-test still T4's; T15: REC 128 and 0 written in reset mode and read back, the counters from the registers; T16's place: reset mode with no wait before it and the status reported; T17: the held frames handed back oldest first with their tags, counted once, the queue cleared, the head frame aborted after the switch, its failure and event not counted again, an earlier failure kept, the driver left holding a frame reported, the record wrapping; T18: the bus-error event taken by the switch's poll passed up once), the S3's buffer state and abort, and the S3's restart stop (the other core stalled first, and not again when already stalled, then T12's steps), T19's stall record (the stall-code fields read at entry, written only after T12's steps, at each return after the stall, refused when its check fails, cleared once read) and R20's mark `twai_port_s3_probe_build`. With the host switch: the same switch cases, T8 and T12 unsupported, and R20's mark `twai_port_host_switch_build`. With neither: the switch refused, and T8 and T12 unsupported with nothing touched. |
| `stub.c`, `stub.h` | the scripted stub: every driver, board and tick call returns what the case set and is recorded in order. It models no controller behaviour: no states, no alerts of its own, no arbitration (README.md's `unit/` row, gen-inhibit reviewer, 2026-10-31). |
| `mock/` | host stand-ins for the ESP-IDF 5.4.1 and 5.4.2 headers the port includes (byte-identical in the two) (`driver/twai.h` with the real field names and alert values, `hal/twai_ll.h` with a status register the case sets, the restart stop's reset mode and counters, and the switch's mode and REC writes, `soc/soc.h` and `soc/system_reg.h` with the clock and reset bits the case sets, `soc/rtc_cntl_reg.h` with the S3's stall-code registers, `esp_cpu.h` with the core id and the recorded stall, `esp_rom_sys.h` with the scripted 2 us wait, `esp_err.h`, `esp_log.h`, `esp_attr.h`, FreeRTOS, `board.h`). `shared/can_port/unit/` and `shared/restart_guard/unit/` build against the same ones. |
| `Makefile` | the build |
| `mutate.py` | the mutants: each breaks one behaviour of `twai_port.c`, `twai_port_ll_esp32c3.c` or `twai_port_ll_esp32s3.c` in a copy, and the run passes only if the unmutated build passes and every mutant builds and fails one of the four binaries; a mutant that does not build, or whose text does not occur once, fails the run. `python3 shared/twai_port/unit/mutate.py [--only N,N...]` from the repo root, in Linux or WSL; its copies go in its own folder under `scratch/twai_port-unit-mutants/`, removed when it ends. |
