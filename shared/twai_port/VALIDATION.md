# Shared TWAI port: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that
the component meets them, is in `VERIFICATION.md`, when there is code to
test. Each entry has a **Basis**, and a **Status**: *validated*, or *basis
pending*, saying what is missing.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
("Layer 2: shared components", `twai_port` and the host fake; goal 15).
gen-inhibit's spec is
`gen-inhibit/SPEC.md` on `gen-inhibit/main` (moved from reverse-it 2026-11-07). The
readings of gen-inhibit's code are of `gen-inhibit/main` at `4020bc8`
(`gen-inhibit/firmware/main/can.c` and `gen_inhibit.c`, both last changed
at `c48e05b`); the readings of the interposer's are of
`charge-interposer/main` at `5426202` (`firmware/src/port_twai.cpp` and
`.h`, `can_port.h`, `firmware/test/host_bridge/twai_fake.cpp`).

**The contract.** T3 to T9 are the contract both reviewers framed on
2026-10-17 (port plan, `twai_port`), which the user ruled on 2026-10-31, in
the gen-inhibit reviewer's session: as written, its "left to each project"
list as the project's parameters (T2), its "the same for both projects"
list fixed, and two additions from the charge-interposer reviewer (T7).
The user's question, whether more of it should be selectable: the fixed
items are correctness rules, which a project could only choose against.

**Readings are not verification.** `port_twai.cpp` already does much of
T3 to T7. That is a reading of what the interposer's port does, not
evidence that this component does it: the interposer's `VERIFICATION.md`
entries for that port do not carry over (AGENTS.md, "Spec, validation and
verification"; charge-interposer reviewer, 2026-10-31).

### T1. One component
- **Basis:** port plan "Layer 2", `twai_port`, gen-inhibit stage (a), from
  `can.c` plus the `twai_ll` calls moved out of `gen_inhibit.c`; goal 15
  (user, 2026-10-17): shared components use ESP-IDF APIs only, and the
  interposer's TWAI driver adopts `twai_port` in its stage (a), its step 2b.
- **Reading:** `port_twai.cpp` includes `Arduino.h` but calls only the IDF
  TWAI API (charge-interposer reviewer, 2026-10-17), so its adoption is a
  merge with `can.c` and a change from C++ to C.
- **Status:** validated.

### T2. What each project chooses
- **Basis:** the contract's "left to each project" list, ruled as the
  project's parameters (user, 2026-10-31). The board port index:
  `shared/boards` B5, which `can.c` already follows (`CAN_BOARD_PORT`,
  `board_twai_pins()`, `board_can_standby()`).
- **The install hook** is the plan's "the filter override ... become[s] a
  callback": `can_enable()` forces accept-all while gen-inhibit owns the bus
  (gen-inhibit spec 5.1 item 2), and that check is gen-inhibit's, so it
  stays the project's.
- **The two projects' choices today:** gen-inhibit installs listen-only for
  OBSERVE and normal otherwise (`gen_inhibit.c` `apply_mode()`), never
  recovers from bus-off (gen-inhibit spec 7; nothing calls
  `twai_initiate_recovery()`), restarts on an install failure
  (`ESP_ERROR_CHECK` in `can_enable()`), and quiesces its worker before a
  teardown (`gen_inhibit_quiesce()` at the top of `can_disable()`, spec 8).
  The interposer installs normal, recovers automatically
  (`TwaiPort::pollAlerts()`), and reports an install failure
  (`TwaiPort::begin()` returns false).
- **Three modes** (user, 2026-10-31, on the gen-inhibit implementor's
  question): the IDF 5.4 TWAI driver has three, normal, no-ACK and
  listen-only, on the C3 and the S3 alike, and a frame is received back by
  its sender when the send sets the self-reception flag. The chip's loopback
  is the no-ACK mode with that flag on every send, which is what the
  interposer's `setSelfTest()` does (`port_twai.cpp`). A separate loopback
  mode would have nothing under it on either chip. The stock
  `can_set_loopback()` only records a flag that `can.c` never applies, and
  keeps doing so (`can_port` C3).
- **Status:** validated.

### T3. Teardown
- **Basis:** the contract: teardown checks the controller state, refusing
  only while it is recovering and skipping the stop in bus-off; every
  return value is checked.
- **Reading:** `can_disable()` ignores the results of `twai_stop()` and
  `twai_driver_uninstall()` and marks the bus off regardless. In bus-off
  the stop is refused and the uninstall allowed; while recovering both are
  refused, which would leave the driver installed and reported off.
  gen-inhibit never recovers, so recovering does not occur there today
  (`gen_inhibit.c`, `controller_running()`'s comment: recovery must not be
  added before this path is revisited). `TwaiPort::end()` does what T3
  says.
- **Status:** validated.

### T4. Mode changes and reinstalls
- **Basis:** the contract: the mode flag is set only after teardown
  succeeds; a failed reinstall reports which step failed and leaves the
  driver uninstalled; a reconfigure that changes nothing does nothing.
- **Reading:** `TwaiPort::setMode()` sets its flag only after `end()`
  succeeds and returns at once when the mode is unchanged. gen-inhibit's
  `apply_mode()` relies on the same order: `can_set_silent()` takes effect
  only with the bus off, so the teardown comes first.
- **The queues at a teardown** (charge-interposer reviewer, 2026-10-31;
  user, 2026-10-31): a reinstall discards both driver queues. The
  interposer's spec 2.3 records that a switch to listen-only discards,
  uncounted, the frames queued toward the vehicle. Reading the depths
  before the uninstall, as T7 does before bus-off recovery, counts them,
  and gives the stage (b) switch counting a starting point.
- **Status:** validated.

### T5. The one alert reader
- **Basis:** the contract: the port is the only alert reader and passes
  events up, with a public alert poll that can run before the project's
  loop starts (the interposer's spec 2.3 power-up round waits in
  `setup()`).
- **Reading:** `twai_read_alerts` consumes what it returns, so two readers
  take events from each other (`port_twai.h`, `pollAlerts()`). Today
  gen-inhibit reads `TWAI_ALERT_TX_FAILED` in `sched_advance()` for spec 7
  trip 7's "controller reported failed" form, and clears a stale one before
  queueing an inhibit; the port's held "transmit failed" event carries
  both uses.
- **Alerts from every install** (user, 2026-10-31): today `can.c` installs
  with no alerts and gen-inhibit enables TX success and failure only when
  it arms, so the stock path runs without them. The port needs its alerts
  from every install to count bus-off entries and losses (T7) whoever is
  using the port. It changes nothing on the bus.
- **Nothing counts events** (charge-interposer reviewer, 2026-10-31; user,
  2026-10-31): an alert is a bit, and completions between two polls
  collapse into one. The interposer's spec 2.3 power-up count is exact only
  from the queue depth, the `tx_failed` delta and the frames bus-off
  recovery discards (`port_twai.h`, `pollAlerts()`); its host fake
  collapses `TX_SUCCESS` per pass, so the collapse is reproducible.
- **Status:** validated.

### T6. Every send goes through the port
- **Basis:** the contract: every send goes through the port.
- **Reading:** today gen-inhibit's scheduler HAL calls `twai_transmit`
  itself with a zero wait (`dev_submit()`, "THE ONLY twai_transmit IN THIS
  COMPONENT", gen-inhibit spec 5.2 item 1), and the stock `can_send()`
  calls it behind the owner refusal. The interposer's `send()` counts a
  full queue as `tx_dropped_`.
- **`tx_refused`** (user, 2026-10-31, on the gen-inhibit implementor's
  question): `can_port` C2 counts every send that is not queued, and a send
  can be refused with the queue not full (the port down, bus-off, an
  invalid frame). Counting those apart keeps `tx_dropped` the queue-full
  count the interposer reads it as.
- **Status:** validated.

### T7. Counters and status
- **Basis:** the contract's counters (loss counts carried across a
  reinstall as deltas, bus-off counted once per episode, `txPending`,
  `txFailedCount`, `txDiscardedBusOff` read before recovery, the RX
  high-water mark, one status read); the charge-interposer reviewer's two
  additions (2026-10-31), ruled with the contract: the receive-loss total
  and `tx_dropped` apart from `tx_failed`. The interposer's spec 8.2
  `bridge_ok` is fed from the receive-loss total and from the bus-off
  entry count, so a loss booked elsewhere, or a bus-off begun and ended
  between two reads, would pass it silently (`can_port.h`).
- **Flushed frames are counted** (charge-interposer reviewer, 2026-10-31;
  user, 2026-10-31): `can.c`'s `can_flush_rx()` cleared the receive queue
  uncounted, and the stock enable flushes after every bring-up. Counted
  apart from the losses, every frame received is in some count. Frames a
  restart after bus-off recovery discards (IDF 5.4.1 `twai_start_v2()`
  resets the receive queue) were received and never delivered, so they are
  losses under "however they were lost".
- **Per-install figures:** gen-inhibit spec 11's status fields are read per
  install; showing cumulative counts would be a gen-inhibit stage (b) spec
  change (port plan), so gen-inhibit computes them from the counters at its
  own install.
- **Reading:** the driver's `rx_missed_count` and `rx_overrun_count` are
  per-frame totals since install, reset by a reinstall; `port_twai.cpp`
  accumulates their delta for that reason.
- **Status:** validated.

### T8. Transmit abort and buffer state
- **Basis:** port plan: register-level code (abort, status read) is one
  file per chip that names the IDF version it was checked against, and only
  the C3 file is measured. gen-inhibit spec 5.2 item 4 and its test (the
  abort's measured behaviour), spec 3 and 12.4's confinement row (the
  restart step's abort goes through the same site).
- **Reading:** `dev_abort_if_awaiting()` reads the status and issues
  `twai_ll_set_cmd_abort_tx` in one critical section, never reads the
  interrupt register (reading it clears interrupts the driver needs), and
  is in IRAM because `gen_inhibit_restart_abort_tx()` may run from a panic.
- **Other chips** (charge-interposer reviewer, 2026-10-31; user,
  2026-10-31): the interposer adopts the port on the S3 at its step 2b,
  where nothing is measured, and does not use the abort. Reporting it
  unsupported there, rather than shipping an unmeasured file, keeps the
  port from claiming behaviour nobody has checked.
- **Status:** validated for the C3; unsupported elsewhere by design.

### T9. Rules
- **Basis:** the contract: every wait inside the port is bounded within
  gen-inhibit's 1 s refusal and 200 ms quiet-bus limits; it takes no lock
  that waits on another task, starts no task and calls the project only
  through declared hooks. gen-inhibit spec 8: its worker's receive wait is
  200 ms and a quiesce waits up to 1 s.
- **Status:** validated.

### T10. One host fake
- **Basis:** port plan, "One host fake, at the IDF driver level" (user,
  2026-10-17): the two driver-level fakes merge into one in `twai_port`'s
  `test/` folder, both projects' host builds compile the real `twai_port`
  on it, each project keeps its logic-level fakes, the merge needs a clock
  adapter in place of `millis()` and per-chip behaviour where the C3 and S3
  differ.
- **Status:** validated.

### T11. Ownership
- **Basis:** the fake is the testers' and the port's unit tests the
  implementors' (user, 2026-10-31, in the charge-interposer reviewer's
  session and in the gen-inhibit reviewer's): the fake encodes what the
  controller does, so its owner defines what correct means for the port,
  and an implementor owning both the port and its fake would let a shared
  misreading of the hardware pass the host tests. This differs from
  gen-inhibit's `fake_twai.c`, the implementor's until this step.
- **Status:** validated.

### T12. The restart stop
- **Basis:** gen-inhibit spec 3, "No restart leaves the controller on the
  bus" (user, 2026-10-13), and the restart guard's steps
  (`shared/restart_guard` G3). Each port's driver stops its own controller,
  reached through `can_port` C7 (user, 2026-11-01), so the TWAI register
  code stays in this component's file per chip with T8's.
- **Reading** of `gi_restart_quiesce.c` at `6e56289`, whose steps this is,
  unchanged: the wait of at most 1 ms in 2 us steps for Transmit Status and
  Receive Status both 0, the abort-if-awaiting on every poll (only an
  awaiting frame, the only state in which the abort was measured to act,
  gen-inhibit spec 5.2 item 4), then reset mode even if the wait ran out;
  nothing done while the TWAI clock is off or the peripheral is held in
  reset, the state after a power-on or an uninstall. It reads the two bits
  rather than the driver's state, because a CPU reset from a live state
  leaves the controller clocked and running with no driver (gen-inhibit
  reviewer's reading of the trial, 2026-10-13).
- **Other chips:** as T8: nothing is measured on the S3, so the stop
  reports "not supported" there rather than running an unmeasured
  sequence. The interposer measures it in its stage (b), gap fix 3.
- **Two cores** (user, 2026-11-27, in the charge-interposer reviewer's
  session, on the charge-interposer implementor's question): the steps
  assume that with interrupts off nothing else loads a frame, which holds
  on the single-core C3. On the X2 the S3 runs both cores, and the stop
  turns interrupts off only on its own core. The TWAI interrupt, or a task
  on the other core, can then load the next queued frame during the wait,
  and the reset cuts it on the bus. ESP-IDF's `esp_restart_noos` stalls
  the other core itself a little later, so stalling it first changes
  nothing else. The stop takes no lock, so a stalled core cannot block it.
- **Status:** validated for the C3; unsupported elsewhere by design.

### T13. The test app
- **Basis:** port plan "Layer 2": a test app for each shared component,
  among them `gi_txabort_probe.c` for the C3 register file, whose results
  are evidence for that component version, citable by both projects.
  gen-inhibit spec 5.2 item 9 and 12.4's "abort behaviour of the C3 TWAI"
  row: the measurement the scheduler's abort model rests on.
- **Built into a project's image, interface unchanged** (user, 2026-11-01):
  as `shared/restart_guard` G5. The ownership check is a hook because the
  probe's refusal while armed is gen-inhibit's rule (spec 3.2, "Exempt"),
  and the shared probe cannot call gen-inhibit.
- **A bus where no node acknowledges** (user, 2026-11-27, in the
  gen-inhibit reviewer's session, gen-inhibit tracker F5): gen-inhibit's
  emulated test of a withdrawn inhibit frame assumes that TS reads 0 in
  the idle between automatic resends (6 us error active, 22 us error
  passive, from CAN bit timing). Nobody has measured this on the C3. The
  earlier measurement covered TS falling when a frame loses arbitration,
  which is a different event. The fix for F5 depends on it: if TS stays
  at 1 across resends, an abort issued only while TS = 0 never removes the
  frame. The user ruled that these measurements come before the fix is
  chosen.
- **Status:** validated.

### Stage (b): T14 to T19
- **Basis for all six:** the charge-interposer review tracker
  (`$REVERSE_IT_DIR/projects/vtrux/notes/charge-interposer/charge-interposer-review.md`),
  row "Vehicle frames lost at the vehicle port's mode switch", its
  requirements (a) to (f) (charge-interposer implementor, 2026-10-16, from
  IDF v5.4.1; user, 2026-10-16 and 2026-10-22), and row "A software restart
  leaves the controllers on their buses"; the interposer spec, 2.3 ("A
  switch loses no frame", "No restart leaves the vehicle-side controller on
  its bus") and 8.2 (`bridge_ok`); port plan, Layer 2, `twai_port` "Stage
  (b)". The user ruled on 2026-11-07, in the charge-interposer reviewer's
  session, that the loss is fixed at stage (b) in `twai_port`. The user
  approved the text on 2026-11-27, in the same session, after the
  gen-inhibit reviewer's read.
- **Two switch paths** (user, 2026-11-27, in the gen-inhibit reviewer's
  session): T14 and T4's teardown both stay in the port. gen-inhibit stays
  on the teardown switch.

### T14. The kept-driver switch
- **Basis:** the interposer's bench lost 1 or 2 vehicle-side frames at every
  switch of the vehicle port, in both directions: 44 over 41 switches at
  2,000 fps, and 0 of 171,810 in a control with no switches (charge-interposer
  tester, 2026-10-15). The switch was a full teardown and reinstall, the only
  route the public TWAI API offers, and it left the controller deaf for
  longer than the bus's idle gap. Requirement (c): the driver's mode is
  fixed at install (`twai.c:481`) and gates `twai_transmit` (`:666`), so a
  port installed in listen-only could never send.
- **Status:** validated.

### T15. The error counters at a switch
- **Basis:** requirement (a): the errata workaround's REC = 128 is set only
  in `twai_hal_start` (`twai_hal.c:76-85`), and `twai_hal_stop` (`:98`) sets
  it back to 0. Without the write, a controller switched through the
  registers is listen-only and error-active. Requirement (d):
  `twai_get_status_info` reads both counters as 0 while the driver believes
  it is in listen-only (`:848`). The register read after the driver's own
  listen-only start: REC 128 in 18 of 18 reads (charge-interposer tester,
  vport_bench `f404111`, 2026-10-16).
- **The last sentence** (gen-inhibit reviewer, 2026-11-27): limited to a
  port that chose T14, so gen-inhibit's counters are unchanged.
- **Status:** validated.

### T16. The switch's placement in the inter-frame gap
- **Basis:** requirement (b): `twai_ll_set_mode` needs reset mode
  (`twai_ll.h:202`), which halts a frame in transmission and misses one
  arriving, so the switch is placed in the gap, found and not assumed.
- **Held** (user, 2026-11-27): the drafted text waited for TS = 0 and RS = 0,
  bounded at 1 ms, and switched anyway when the wait ran out. If TS stays
  at 1 between unacknowledged resends, every such wait runs out. A faulty
  or full bus can also stop the gap from coming. The user ruled that the
  bound and the action on timeout wait for T13's measurements on a bus
  where no node acknowledges, on the C3 and on the S3.
- **Status:** basis pending: T13's no-acknowledge measurements on the C3
  and the S3 (T19).

### T17. Frames held at a switch to listen-only
- **Basis:** requirement (e) (user, 2026-10-16): every bridged frame the
  driver still holds at a switch to listen-only is booked toward the
  interposer spec 8.2's "bridged frame that failed to reach the vehicle",
  which also meets 2.3's "either sent or counted". With nothing on the
  vehicle segment acknowledging, the head frame retries forever and up to
  32 frames queue behind it. The teardown switch then discarded them
  uncounted (charge-interposer implementor, 2026-10-16). The interposer's
  host_bridge checks `bridge-ok-veh-failed` and `bridge-ok-switch-discard`
  are known FAILs on the baseline that the ported image must pass.
- **Counted once:** an aborted frame raises a transmit failure, so the
  switch's count and `tx_failed` would otherwise both book it.
- **Status:** validated.

### T18. The bus-error count is exact up to the switch
- **Basis:** requirement (f) (user, 2026-10-22, from the interposer's
  powerup-round-noack run): the vehicle-side error count books every bus
  error up to the switch, including one raised after the loop's last alert
  read. On the kept-driver switch the latched alert survives, since nothing
  uninstalls.
- **Limited to T14** (gen-inhibit reviewer, 2026-11-27): the teardown path
  is unchanged.
- **Status:** validated.

### T19. T8 and T12 on the S3
- **Basis:** T8 and T12 report "not supported" on any chip with no measured
  file, so `shared/restart_guard` does nothing on the X2, and the
  interposer spec 2.3 restart requirement is unmet there. The implementor's
  reading of IDF v5.4.1 (2026-10-16, not measured): on the S3 a software
  restart leaves TWAI on the bus at half rate. T12's validation names this
  measurement as the interposer's stage (b) work.
- **A serial command:** the X2 runs no WiCAN web server, so `shared/wican`
  W8's URI hook does not exist there.
- **The no-acknowledge modes** (charge-interposer reviewer, 2026-11-27, on
  the gen-inhibit reviewer's question): T16's wait and T17's abort depend
  on what TS reads between resends on the S3.
- **Status:** validated.
