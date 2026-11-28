# shared/twai_port/test_app/test/

Host tests of the probe's no-acknowledge modes, m1-m3, and of its register
files (`../../SPEC.md` T13; the implementor's, as the probe is, T11).
Self-checks, not verification: the tester measures m1-m3 on the bench.

    make -C shared/twai_port/test_app/test run      # from the repo root; `asan` for the sanitiser build
    python3 shared/twai_port/test_app/test/mutate.py [--only N,N...]

The binaries go to `scratch/twai_port-probe-test/` at the worktree's root.

| File | What |
|---|---|
| `test_unacked.c` | `twai_port_unacked.c` against a script: the register layer and the target calls are given by a timeline of unacknowledged attempts (TS = 1 for the attempt, TS = 0 for the idle, the TEC up 8 at each attempt's end until 128) and a rule for what an abort does (remove the frame if issued with TS = 0, or nothing). The clock moves only when the code reads it. Cases: the arguments and every refusal; m1's trace (every change and only changes, timed within a sample, the TEC with each, attempts counted, the first sample kept), TS stuck at 1, TEC-only changes, a frame that completes, a stall counted and flagged, the trail, the 2048 records running out; m2 in the error-active and the error-passive idle, by attempt and by time, TS never 0, freed before the abort, an abort with no effect; m3 by attempt and by time, delay 0, a delay that lands in the idle; `fresh`, the skips, `irq=off`, the unsupported chip; the hold (`hold_ms`; the script can bring back an acknowledging node at its start): after the trace's reads and before the cleanup and the settle, nothing sampled, recorded or aborted in it, interrupts on, its length and its end read, run also when the trace saw the buffer free, m1's frame going out in it, an aborted frame not going out, a frame the abort left going out, the cleanup after it when no node returns; the answer, its ns times, its nulls and a failed chunk |
| `test_probe_ll.c` | the C3 register file against `mock/` (each status bit, the TEC, the abort's two modes, the reads before and after the command, the command inside the critical section); the same cases for the S3 file (T19), built with `-DCHIP='"s3"'`; and, built with `-DPROBE_LL_UNSUPPORTED`, the unsupported file (refuses, touches nothing) |
| `mock/` | host stand-ins for ESP-IDF v5.4.2's esp32c3 `hal/twai_ll.h` (the status and TEC the case sets, the abort command counted; the esp32s3 header has the same calls and status bits) and `freertos/FreeRTOS.h` (the critical section counted) |
| `Makefile` | the four binaries: `test_unacked`, `test_probe_ll_c3`, `test_probe_ll_s3`, `test_probe_ll_unsupported` |
| `mutate.py` | the mutants: each breaks one behaviour of `twai_port_unacked.c` or a register file in a copy, and the run passes only if the unmutated build passes and every mutant builds and fails; a mutant that does not build, or whose text does not occur once, fails the run. Its copies go in its own folder under `scratch/twai_port-probe-mutants/`, removed when it ends |
