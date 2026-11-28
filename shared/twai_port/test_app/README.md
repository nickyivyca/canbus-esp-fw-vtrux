# shared/twai_port/test_app/

The transmit-abort probe (`../SPEC.md` T13), compiled into a project's
image only when the build sets the component's switch
`TWAI_PORT_TXABORT_PROBE`, and never into a release image (`shared/tools`
R20). Moved at port step 1c from gen-inhibit's `gi_txabort_probe.c` and
`.h`, with its URI, its parameters and its answer unchanged.

| File | What |
|---|---|
| `twai_port_txabort_probe.c` | the probe's parse, run and answer, with no transport: what the chip's abort command does to a frame awaiting arbitration, to one being transmitted and to an empty buffer, what the driver reports afterwards, and how long the buffer takes to come free (gen-inhibit spec 5.2 item 9). It writes the abort command register directly, so it is a measurement build's only. Phases `m1`, `m2` and `m3` go to `twai_port_unacked.c` |
| `twai_port_txabort_run.h` | private: `twai_port_txabort_run()`, which takes the arguments through a getter, gives the answer through an emitter, and returns the URI's status code with the refusal's body. The URI and the X2's serial commands (T19) both call it. It was split from the URI's handler for T19 (charge-interposer reviewer, 2026-11-27); the arguments, the refusals and the answer did not change |
| `twai_port_txabort_http.c` | `POST /gi_txabort`: the query string's own check, the HTTP getter and emitter, the refusals as HTTP errors, and `twai_port_txabort_register()` |
| `include/twai_port_txabort_probe.h` | what the project calls: `twai_port_txabort_register()`, from its URI hook (`shared/wican` W8), with its check of whether it owns the bus, while which the probe refuses, and the body of that refusal |
| `twai_port_unacked.c`, `twai_port_unacked.h` | the no-acknowledge modes, m1-m3 (below): their arguments, trials and answer, chip-neutral |
| `twai_port_unacked_esp.c` | what m1-m3 call on the target: the driver, the CPU cycle counter and `esp_timer` |
| `twai_port_probe_ll.h` | m1-m3's register layer: the status bits, the TEC, and the abort command with the status read before and after it in one critical section |
| `twai_port_probe_ll_esp32c3.c` | that layer on the C3, checked against ESP-IDF v5.4.2 |
| `twai_port_probe_ll_esp32s3.c` | that layer on the S3 (T19), checked against ESP-IDF v5.4.2, whose S3 calls and status bits are the C3's. Its critical section masks the driver's interrupt only on the core that installed the driver, so the T19 app runs the modes on that core |
| `twai_port_probe_ll_unsupported.c` | that layer on any other chip: m1-m3 answer 501 "not supported" and touch no register |
| `include/twai_port_t19.h`, `twai_port_t19.c` | T19, the S3 app: serial commands over the probe, the kept-driver switch, reset mode and the restart stop, one JSON line per answer (below). Compiled only with `TWAI_PORT_TXABORT_PROBE` and `TWAI_PORT_S3_PROBE` on the S3 |
| `twai_port_t19_parse.c`, `twai_port_t19_parse.h` | private: the app's command line, chip-neutral: split, arguments, numbers, JSON strings |
| `twai_port_t19_ll.h`, `twai_port_t19_ll_esp32s3.c` | private: the app's own register reads and writes on the S3, checked against ESP-IDF v5.4.2: the whole status and mode registers, both error counters, the bus timing, and reset mode entered and left, a mode set and an abort for `resetprobe` |
| `test/` | the host tests of m1-m3, of the register files and of the T19 command line, and their mutants (`test/README.md`) |

The register layer is a file per chip, chosen by `IDF_TARGET` as T8's
register file is, but it is not in T8's file: that file is in every image,
and its only abort is the abort-if-awaiting (gen-inhibit spec 12.4). The
abort here is issued in any state, so it is compiled only with the probe.

## The no-acknowledge modes: m1, m2 and m3

These measure the controller on a bus where **no node acknowledges**
(T13, added 2026-11-27, gen-inhibit tracker F5). Each attempt then ends in
an ACK error and an error frame, and the controller sends the frame again
by itself. The questions are:
- what TS reads between those attempts, in the error-active and the
  error-passive state;
- what the abort command does during an attempt and in the idle between
  attempts.

The bus must have no other node that acknowledges, in any mode. The older
phases need the opposite, an acknowledging node.

    POST /gi_txabort?phase=m1|m2|m3[&<argument>=<value>...]

### Arguments

Every argument is optional. An argument that is too long, not a whole
number, out of range, or not used by the phase is refused with 400. The
older phases' arguments (`gate`, `retry`, `d0`, `d1`, `step`) are refused
too.

| Argument | Phases | Range | Default | What |
|---|---|---|---|---|
| `label` | all | up to 31 characters, no `"`, `\` or control character | empty | echoed in the answer |
| `reps` | all | 1-8 | 1 | trials in this call |
| `settle` | all | 1-1000 ms | 20 | the pause after each trial |
| `fresh` | all | 0, 1 | 1 | 1: before each trial, the driver is uninstalled and installed again, so the trial starts error-active with TEC 0. 0: the trial starts with the TEC the last one left |
| `irq` | all | on, off | on | off: interrupts are masked while the trace is taken, from just after the send to the end of the trace (at most `window_us` plus `bound_us` plus `trail_us`). The driver's ISR then runs only after it |
| `sample_us` | all | 0-100 | 1 | the sampling period in microseconds. 0: as fast as the loop runs |
| `window_us` | all | 100-50000 | 10000 | m1: how long the trace is, from the send. m2, m3: the bound on the wait for the chosen attempt or time |
| `trail_us` | all | 0-5000 | 500 | the trace goes on this long after the buffer comes free |
| `wait_att` | m2, m3 | 1-255 | 1 if `wait_us` is not given | the attempt the abort is aimed at, counted by TS rises from the send |
| `wait_us` | m2, m3 | 0 to `window_us` minus 1 | not given | the time from the send the abort is aimed at. Give `wait_att` or `wait_us`, not both |
| `bound_us` | m2, m3 | 10-20000 | 2000 | after the abort, how long the trace waits for the buffer to come free |
| `delay_us` | m3 | 0-5000 | 100 | from the TS rise to the abort |
| `hold_ms` | all | 0-2000 | 0 | the hold before the cleanup (step 9). 0: no hold |

### What each phase does

Each trial:
1. With `fresh=1`, the driver is installed again and every alert is
   enabled. The receive queue and the alerts are cleared.
2. The trial is skipped if the buffer is not free (TBS = 0) or the driver
   holds a frame.
3. One frame is sent with `twai_transmit()`: ID 0x7FE, DLC 8, bytes 2-3
   `AB 7E`, single-shot off, so the controller resends it after each error.
   Times are from the moment `twai_transmit()` returns.
4. The trace starts. Each sample reads the status register and the TEC. A
   change of either is recorded with its time.
5. The phase's own steps:
   - **m1:** the trace runs for `window_us`.
   - **m2:** the trace runs until the chosen attempt starts (`wait_att`:
     its TS rise) or the chosen time (`wait_us`). Then every read is made
     with the abort command in one critical section: the first read with
     TS = 0 and TBS = 0 issues the abort.
   - **m3:** the trace runs until the chosen attempt's TS rise
     (`wait_att`), or the first TS rise at or after `wait_us`. The abort
     is issued `delay_us` after that rise, whatever the status reads.
6. m2 and m3: after the abort, the trace runs until TBS = 1 or for
   `bound_us`.
7. The trace runs `trail_us` more after the buffer comes free.
8. The driver's alerts and counters are read.
9. With `hold_ms` above 0, the hold. The driver stays installed and
   running, with interrupts on. The probe issues no abort, clears nothing
   and records no change for at least `hold_ms`. Then it reads the status,
   the TEC, the driver's alerts and the driver's view. The hold runs
   whether or not the trace saw the buffer come free.
10. If the buffer is still full, the cleanup clears the driver's queue,
    then issues the abort at each read with TS = 0, for at most 20 ms,
    until TBS = 1. This is not part of the measurement. It is counted
    (`cleanup_aborts`) and timed (`cleanup_ns`).

The hold is for a bench check that brings back an acknowledging node
after the trace (gen-inhibit reviewer, 2026-11-27). Without it the cleanup
removes any frame the abort left in the buffer, so a frame that should not
go out could never be seen going out:
- `TWAI_ALERT_TX_SUCCESS` in `alerts_hold` shows that a frame went out in
  the hold.
- m1 with a hold is the positive control. Its frame was never aborted, so
  it must go out when the node returns.
- `cleared` `hold` means the trace ended with the buffer full and TBS read
  1 at the hold's end.
- `cleanup` 1 after a hold means the frame was still in the buffer at the
  hold's end.

The call answers only after its last trial. With `hold_ms=2000` and
`reps=8` that is more than 16 s.

m2 and m3 read the driver's alerts and counters once before the abort, at
the point the wait is met (m3: at the TS rise). That read pauses the
trace for `snap_ns`; the next sample's gap shows it. With `irq=off` that
read is not made: the ISR has not run yet, so there is nothing to split.

### Timing

The probe uses two clocks:
- **The CPU cycle counter** (`esp_cpu_get_cycle_count()`) times the busy
  parts, where the CPU never idles: the trace and everything in it
  (`tx_call_ns`, `max_gap_ns`, `end_ns`, `clear_ns`, `met_ns`, `anchor_ns`,
  `abort_ns`, `free_after_abort_ns`, `snap_ns`, the events' times) and the
  cleanup (`cleanup_ns`). These are reported in nanoseconds, converted with
  the CPU frequency read at the start of the call (`cpu_hz`). `cpu_hz_end`
  is read at the end. The head's `timing` names this clock.
- **`esp_timer`** times every span with a block in it: the hold
  (`hold_ns`, from microseconds), and the call (`run_us`, `uptime_us`).
  The cycle counter stops while the CPU idles in a block: on the C3 a
  2000 ms hold read 98.5 ms in cycles (gen-inhibit `VERIFICATION.md` 12.4,
  "The rerun on the stack fix", 2026-11-27).

The clock check, `clk_cyc` and `clk_us`, gives the same span on both
clocks: from just before the send to the trace's end, the trail included.
Nothing in it blocks, so the two agree unless the CPU frequency changed.
It does not cover the hold or the cleanup. Both are 0 for a skipped trial.

A change is timed at the sample that saw it, so it happened between that
sample and the one before. With `sample_us=1` that is within about 1 us,
unless the loop was held up: by the driver's ISR when `irq=on`, or by the
read before the abort. Each gap between two samples longer than
`sample_us` plus 2 us (`gap_us`) is counted in `gaps`, the longest is
`max_gap_ns`, and the change recorded after such a gap has its `gap` flag
set. An edge pair, such as a short TS = 0, can be missed whole inside a
gap.

### The answer

One JSON object, sent in chunks. The head:

| Field | What |
|---|---|
| `probe`, `git`, `phase`, `label` | `"gi_txabort"`, the app version, `m1`/`m2`/`m3`, the label |
| `id`, `dlc`, `tag`, `single_shot` | the frame: `"0x7FE"`, 8, `"AB7E trial"`, false |
| `reps` ... `hold_ms` | the arguments as run; -1 for one the phase does not use |
| `timing`, `cpu_hz`, `cpu_hz_end`, `gap_us` | `"cpu cycle counter"`, the CPU frequency at the start and the end, the gap mark |
| `st_bits` | the status bits by name: `tbs` 4, `tcs` 8, `rs` 16, `ts` 32, `es` 64, `bs` 128. These are the bit positions the older phases' status words use |
| `events_max`, `events_used` | the change records: 2048 for the whole call, and how many were used |
| `n`, `run_us`, `uptime_us` | trials, the call's length, the uptime at the answer |
| `trials` | one object per trial |
| `stack_free_min` | after `trials`, the last field: the calling task's least free stack in bytes, from the task's start to just before the last chunk is sent (FreeRTOS's high-water mark). On gen-inhibit that task is the httpd task, with a 5120-byte stack |

Every buffer on the probe's call path is static, not on the stack, because
the httpd task's stack is small: the URI handler's query and refusal
message, the run's arguments, refusal text and lines, and the answer's.
The older phases run in their own function, so their frame is not under
m1-m3. On gen-inhibit (gen-inhibit `VERIFICATION.md` 12.4):
- at shared/main 3bb6c945 the answer's buffers were on the stack, and
  every m1-m3 call overflowed it and panicked at its answer (2026-11-28);
- at 9d1dc134 the handler's and the run's were, and `stack_free_min`
  read 44-108 bytes, with 10 of 140 calls ending in a restart
  (2026-11-27).

`test/test_stack.py` holds the frames of the handler, the run and
`twai_port_unacked.c`, and the deepest call path from the handler, to a
budget on the host.

Each trial (times in ns from the send; -1: never, or not used by the
phase):

| Field | What |
|---|---|
| `t`, `skip`, `reinstalled` | the trial's number; why it was skipped, or empty; whether the driver was installed again |
| `st_pre`, `tec_pre`, `drv_pre` | the status and the TEC before the send, and the driver's view (below) |
| `tx_call_ns` | how long `twai_transmit()` took. The first attempt may start inside it |
| `samples`, `max_gap_ns`, `gaps`, `ev_lost` | samples taken, the longest gap, gaps over the mark, changes lost because the 2048 records were used up |
| `attempts` | TS rises seen. A rise inside a gap is not seen |
| `end_ns` | the trace's last sample |
| `cleared`, `clear_ns` | how the buffer came free: `tbs_tcs` (in the trace, TCS = 1), `tbs` (in the trace, TCS = 0), `hold` (in the hold: TBS = 1 at its end), `cleanup` (by the cleanup), `never`; and the first sample with TBS = 1 (-1 when not in the trace) |
| `wait_timeout`, `freed_before` | m2, m3: the wait ran past `window_us`, so no abort; the buffer came free before the abort, so no abort |
| `met_ns`, `anchor_ns` | m2, m3: when the wait was met; m3: the TS rise the delay counts from |
| `abort_ns`, `ab_issued`, `ab_st_before`, `ab_st_after`, `ab_tec`, `ab_attempt` | the abort: its time, whether the command was written, the status just before and just after it in the same critical section, the TEC, and the attempts seen up to it |
| `free_after_abort_ns` | from the abort to TBS = 1 |
| `alerts_before`, `drv_mid`, `snap_ns` | m2, m3: the driver's alerts and view read before the abort, and how long that read took; `null` and -1 with `irq=off` |
| `alerts_after`, `drv_after` | the driver's alerts and view after the trace: m1 the whole window, m2 and m3 the abort and after |
| `hold_ns`, `hold_st`, `hold_tec`, `hold_tbs`, `hold_tcs`, `alerts_hold`, `drv_hold` | the hold: its length (`esp_timer`, to 1 us); at its end the status, the TEC, TBS and TCS (1 or 0), the driver's alerts in the hold and the driver's view. -1 and `null` with no hold |
| `cleanup`, `cleanup_ns`, `cleanup_aborts`, `alerts_cleanup` | the cleanup: whether it ran, its time to TBS = 1 (-1: never), abort commands issued, the alerts after it |
| `st_end`, `drv_end`, `free_end` | the status, the driver's view and TBS at the end of the trial |
| `clk_cyc`, `clk_us` | the clock check (Timing): from just before the send to the trace's end, in cycles and in `esp_timer` microseconds; 0 for a skipped trial |
| `events` | the changes, each `[t_ns, st, tec, gap]`: the time, the status bits (`st_bits`), the TEC (capped at 255), and 1 if a gap came before it. The first sample is always recorded |

The driver's view, `drv_*`: `state` (`twai_state_t`), `mtx` (frames the
driver holds to send), `tec`, `rec`, and the counters `tx_failed`,
`bus_error` and `arb_lost`, as `twai_get_status_info()` gives them. The
counters are totals, not deltas. Alerts are `TWAI_ALERT_*` bit masks.

### Refusals

| Status | When |
|---|---|
| 400 | an argument is unusable, or the project owns the bus, or the bus is listen-only |
| 501 | the chip has no probe register file (any chip but the C3 and the S3) |
| 500 | the bus would not come up, or the alerts would not enable |

## The T19 app on the S3: serial commands

`SPEC.md` T19: the S3 measurement of T8, T12, T14-T18 and T13's
no-acknowledge modes, started by serial commands, since the X2 has no URI
hook. The project (the charge interposer's `s3probe` variant) passes each
line to `twai_port_t19_line()` and sends the answer. It runs every command on
one task, so the driver is installed, and its interrupt runs, on that task's
core. The commands and fields were set with the interposer tester
(2026-11-27).

The bus is can_port's port 0, driven by the stock CAN calls as the probe
drives it: twai_port with the kept-driver switch (T14), 500 kbit/s, accept
all, RX queue 64, TX queue 32. Nothing is installed at boot. Each command
brings the bus up in normal mode when it needs it. `resetprobe` takes it
down at its end.

**A mode change with the bus up is `can_port_set_mode(0, ...)`**, can_port's
"set the mode" (can_port C2). It reaches `twai_port_set_mode()` through the
backend table, as a project's own switch does. The stock `can_set_silent()`
is ignored while the bus is on, so the app never switches through it. At
tag 2026.11.27.6 it did, and no switch ran (`../VERIFICATION.md`, T19 run
`bench_t19_1d4221c9`). The mode the app reads is the port's,
`twai_port_mode()`.

### The line and the answer

    <command> [<key>=<value> ...]

Spaces separate the words. A line holds at most 255 characters and 16
arguments. A token with no `=`, an empty key, a key the command does not
take, a value that does not fit and a number out of range are each refused
with 400. Every answer is one JSON line:

    {"cmd":"<the line>","core":N,"t0_us":T,"status":200, ... ,"t1_us":T}

`core` is the core the command ran on, and `t0_us` and `t1_us` are
`esp_timer` at its start and end. **The answer is sent in pieces as the
command runs:** its start (`cmd`, `core`, `t0_us`) goes out before the
command's work, and the rest after it. So a reader must read to the newline,
however long the command takes: `switchloop` is silent for `k` x
`period_ms` after its start. A refusal has `"status"` 400, 500 or 501
and `"error"`. Statuses and times are numbers; status registers are the
chip's bits, as in the probe's `st_*` fields (TBS 4, TCS 8, RS 16, TS 32,
ES 64, BS 128).

### The commands

| Command | Arguments | What it does and answers |
|---|---|---|
| `info` | none | `build` (the app version), `idf`, `port_core` (the core the driver was installed on, -1 while not installed), `installed`, `mode`, `reset_reason`; with the clock on: `btr0`, `btr1` and their fields `brp`, `sjw`, `tseg1`, `tseg2`, `sam`, and `tec`, `rec`. `clock` false: the controller's registers were not read |
| `regs` | none | changes nothing. `st` and its bits `tbs`, `tcs`, `rs`, `ts`, `es`, `bs`; `mode_reg` and `rm`, `lom`, `stm`; `tec`, `rec`; `drv` the driver's `state`, `mtx` (msgs_to_tx) and `mrx` (msgs_to_rx), or null when not installed |
| `txabort` | the URI's query arguments, as key=value | the probe, every phase (abort, control, noabort, m1-m3), with the URI's arguments, refusals (status and body) and answer, which is in `"answer"`. The project owns nothing, so the probe never refuses for that |
| `switch` | `to=lom\|normal`, `n=0..32` (default 0) | one kept-driver switch, through `can_port_set_mode(0, ...)`. With `n`, that many frames are queued first with `twai_port_send_tagged()`: ID 0x7FD, data `i 0 AB 7D`, tag 0x7100 + i. Answers `queued`, `queue_refused`, `switch_us` (the whole switch), `set_mode` (what the call returned, an `esp_err_t`), `mode` (the port's after it), `report` (every field of `twai_port_last_switch()`), `hook` (the T17 hook's frames in order, each `[id, tag]`), `hook_lost` (beyond 64), `st_after`, `mtx_after`, `tx_discarded_switch`, `tx_failed`. The port gives no time per step inside the switch: `report.abort_wait_us` is the only inner time |
| `switchloop` | `k=1..200` (10), `period_ms=1..10000` (100) | from normal, `k` switches, listen-only then normal by turns, one every `period_ms`, receiving between them. `switches`: one `[t_us, to_lom, switch_us, result, held, rec_read_back, status_at_reset, head_aborted, driver_tx_stuck]` per switch, `t_us` from the start. A call that ran no switch (the report's `seq` did not move) gives zeros, with the call's return as `result`. `rx_windows`: frames received in each of the `k` + 1 windows. Then the port's deltas over the run: `rx_frames`, `rx_lost`, `rx_lost_queue_full`, `rx_lost_overrun`, `rx_lost_restart`, `bus_errors`, `tx_discarded_switch`. An odd `k` is switched back to normal at the end |
| `resetprobe` | `at=ts0\|ts1` (ts0), `att=1..255` (1), `delay_us=0..5000` (0, ts1 only), `leave=normal\|lom` (normal), `wait_us=0..100000` (1000), `abort=0\|1` (0, lom only), `window_us=100..50000` (10000) | reset mode on a frame left in the buffer, apart from the port's switch. Sends one frame (ID 0x7FC, data `0 0 AB 7C`) on a bus where no node acknowledges, counts attempts by TS rises, and enters reset mode: `ts0` at the first read with TS = 0 and TBS = 0 from attempt `att`, in one critical section; `ts1` `delay_us` after attempt `att`'s TS rise. In reset mode: `st_reset`, `mtx_reset`. Then the mode is set and reset mode is left; with `abort=1`, an abort (`ab_st_before`, `ab_st_after`). After `wait_us`: `st_wait`, `mtx_wait`, `tx_alerts` (success, failed, both, none) and `alerts_wait`. Then a second frame (data `1 0 AB 7B`) with `twai_transmit()` and a 10 ms wait: `second_tx_err` and `second` within 10 ms, `alerts_second`. Also `attempts`, `entered`, `freed_before`, `wait_timeout`, `enter_us`, `st_before` and `st_after_enter`. The bus is taken down at the end (`cleanup`) |
| `restart` | `how=restart\|panic`, `load=idle\|noack\|queued\|flood\|core` (idle), `n=1..32` (queued only, 4), `period_us=50..100000` (core only, 1000) | brings the bus up in normal mode and sets the load: `noack` one frame (ID 0x7FA) for a bus where no node acknowledges, `queued` `n` frames, `flood` nothing (the bench floods), `core` a task pinned to the other core sending a frame (ID 0x7FB) every `period_us`, running 50 ms first. Answers `how`, `load`, `port_core` and `restart_at_us` (`esp_timer` just before the call), sends the line, then calls `esp_restart()` or `abort()` (a panic) |
| `g4` | `clear=0\|1` (0) | after a restart: `reset_reason`; `g4`, the guard's record (`shared/restart_guard` G4) as the project gives it, or null; `stall`, the S3 stop's record of the other core (`valid`, `core`, `other`, `options0`, `sw_cpu_stall`, `stalled_at_entry`, `stall_called`, `entered_reset`; twai_port_ll.h). `clear=1` clears the stall record after the answer |
