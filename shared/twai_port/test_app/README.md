# shared/twai_port/test_app/

The transmit-abort probe (`../SPEC.md` T13), compiled into a project's
image only when the build sets the component's switch
`TWAI_PORT_TXABORT_PROBE`, and never into a release image (`shared/tools`
R20). Moved at port step 1c from gen-inhibit's `gi_txabort_probe.c` and
`.h`, with its URI, its parameters and its answer unchanged.

| File | What |
|---|---|
| `twai_port_txabort_probe.c` | `POST /gi_txabort`: what the chip's abort command does to a frame awaiting arbitration, to one being transmitted and to an empty buffer, what the driver reports afterwards, and how long the buffer takes to come free (gen-inhibit spec 5.2 item 9). It writes the abort command register directly, so it is a measurement build's only. Phases `m1`, `m2` and `m3` go to `twai_port_unacked.c` |
| `include/twai_port_txabort_probe.h` | what the project calls: `twai_port_txabort_register()`, from its URI hook (`shared/wican` W8), with its check of whether it owns the bus, while which the probe refuses, and the body of that refusal |
| `twai_port_unacked.c`, `twai_port_unacked.h` | the no-acknowledge modes, m1-m3 (below): their arguments, trials and answer, chip-neutral |
| `twai_port_unacked_esp.c` | what m1-m3 call on the target: the driver, the CPU cycle counter and `esp_timer` |
| `twai_port_probe_ll.h` | m1-m3's register layer: the status bits, the TEC, and the abort command with the status read before and after it in one critical section |
| `twai_port_probe_ll_esp32c3.c` | that layer on the C3, checked against ESP-IDF v5.4.2 |
| `twai_port_probe_ll_unsupported.c` | that layer on any other chip: m1-m3 answer 501 "not supported" and touch no register. The S3's file comes with the charge interposer's T19 work |
| `test/` | the host tests of m1-m3 and of the register files, and their mutants (`test/README.md`) |

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
9. If the buffer is still full, the cleanup clears the driver's queue, then
   issues the abort at each read with TS = 0, for at most 20 ms, until
   TBS = 1. This is not part of the measurement. It is counted
   (`cleanup_aborts`) and timed (`cleanup_ns`).

m2 and m3 read the driver's alerts and counters once before the abort, at
the point the wait is met (m3: at the TS rise). That read pauses the
trace for `snap_ns`; the next sample's gap shows it. With `irq=off` that
read is not made: the ISR has not run yet, so there is nothing to split.

### Timing

Times come from the CPU cycle counter (`esp_cpu_get_cycle_count()`) and
are reported in nanoseconds, converted with the CPU frequency read at the
start of the call (`cpu_hz`). `cpu_hz_end` is read at the end. Each trial
also gives its length in cycles (`clk_cyc`) and in `esp_timer`
microseconds (`clk_us`), so a frequency change shows.

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
| `reps` ... `delay_us` | the arguments as run; -1 for one the phase does not use |
| `timing`, `cpu_hz`, `cpu_hz_end`, `gap_us` | `"cpu cycle counter"`, the CPU frequency at the start and the end, the gap mark |
| `st_bits` | the status bits by name: `tbs` 4, `tcs` 8, `rs` 16, `ts` 32, `es` 64, `bs` 128. These are the bit positions the older phases' status words use |
| `events_max`, `events_used` | the change records: 2048 for the whole call, and how many were used |
| `n`, `run_us`, `uptime_us` | trials, the call's length, the uptime at the answer |
| `trials` | one object per trial |

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
| `cleared`, `clear_ns` | how the buffer came free: `tbs_tcs` (in the trace, TCS = 1), `tbs` (in the trace, TCS = 0), `cleanup` (by the cleanup), `never`; and the first sample with TBS = 1 |
| `wait_timeout`, `freed_before` | m2, m3: the wait ran past `window_us`, so no abort; the buffer came free before the abort, so no abort |
| `met_ns`, `anchor_ns` | m2, m3: when the wait was met; m3: the TS rise the delay counts from |
| `abort_ns`, `ab_issued`, `ab_st_before`, `ab_st_after`, `ab_tec`, `ab_attempt` | the abort: its time, whether the command was written, the status just before and just after it in the same critical section, the TEC, and the attempts seen up to it |
| `free_after_abort_ns` | from the abort to TBS = 1 |
| `alerts_before`, `drv_mid`, `snap_ns` | m2, m3: the driver's alerts and view read before the abort, and how long that read took; `null` and -1 with `irq=off` |
| `alerts_after`, `drv_after` | the driver's alerts and view after the trace: m1 the whole window, m2 and m3 the abort and after |
| `cleanup`, `cleanup_ns`, `cleanup_aborts`, `alerts_cleanup` | the cleanup: whether it ran, its time to TBS = 1 (-1: never), abort commands issued, the alerts after it |
| `st_end`, `drv_end`, `free_end` | the status, the driver's view and TBS at the end of the trial |
| `clk_cyc`, `clk_us` | the trial's length in cycles and in `esp_timer` microseconds |
| `events` | the changes, each `[t_ns, st, tec, gap]`: the time, the status bits (`st_bits`), the TEC (capped at 255), and 1 if a gap came before it. The first sample is always recorded |

The driver's view, `drv_*`: `state` (`twai_state_t`), `mtx` (frames the
driver holds to send), `tec`, `rec`, and the counters `tx_failed`,
`bus_error` and `arb_lost`, as `twai_get_status_info()` gives them. The
counters are totals, not deltas. Alerts are `TWAI_ALERT_*` bit masks.

### Refusals

| Status | When |
|---|---|
| 400 | an argument is unusable, or the project owns the bus, or the bus is listen-only |
| 501 | the chip has no probe register file (any chip but the C3 today) |
| 500 | the bus would not come up, or the alerts would not enable |
