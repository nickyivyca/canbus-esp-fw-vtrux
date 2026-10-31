# Shared TWAI port: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that
the component meets them, is in `VERIFICATION.md`, when there is code to
test. Each entry has a **Basis**, and a **Status**: *complete*, or *basis
pending*, saying what is missing.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
("Layer 2: shared components", `twai_port` and the host fake; goal 15).
gen-inhibit's spec is
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`. The
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
- **Status:** complete.

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
- **Status:** complete.

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
- **Status:** complete.

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
- **Status:** complete.

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
- **Status:** complete.

### T6. Every send goes through the port
- **Basis:** the contract: every send goes through the port.
- **Reading:** today gen-inhibit's scheduler HAL calls `twai_transmit`
  itself with a zero wait (`dev_submit()`, "THE ONLY twai_transmit IN THIS
  COMPONENT", gen-inhibit spec 5.2 item 1), and the stock `can_send()`
  calls it behind the owner refusal. The interposer's `send()` counts a
  full queue as `tx_dropped_`.
- **Status:** complete.

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
- **Per-install figures:** gen-inhibit spec 11's status fields are read per
  install; showing cumulative counts would be a gen-inhibit stage (b) spec
  change (port plan), so gen-inhibit computes them from the counters at its
  own install.
- **Reading:** the driver's `rx_missed_count` and `rx_overrun_count` are
  per-frame totals since install, reset by a reinstall; `port_twai.cpp`
  accumulates their delta for that reason.
- **Status:** complete.

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
- **Status:** complete for the C3; unsupported elsewhere by design.

### T9. Rules
- **Basis:** the contract: every wait inside the port is bounded within
  gen-inhibit's 1 s refusal and 200 ms quiet-bus limits; it takes no lock
  that waits on another task, starts no task and calls the project only
  through declared hooks. gen-inhibit spec 8: its worker's receive wait is
  200 ms and a quiesce waits up to 1 s.
- **Status:** complete.

### T10. One host fake
- **Basis:** port plan, "One host fake, at the IDF driver level" (user,
  2026-10-17): the two driver-level fakes merge into one in `twai_port`'s
  `test/` folder, both projects' host builds compile the real `twai_port`
  on it, each project keeps its logic-level fakes, the merge needs a clock
  adapter in place of `millis()` and per-chip behaviour where the C3 and S3
  differ.
- **Status:** complete.

### T11. Ownership
- **Basis:** the fake is the testers' and the port's unit tests the
  implementors' (user, 2026-10-31, in the charge-interposer reviewer's
  session and in the gen-inhibit reviewer's): the fake encodes what the
  controller does, so its owner defines what correct means for the port,
  and an implementor owning both the port and its fake would let a shared
  misreading of the hardware pass the host tests. This differs from
  gen-inhibit's `fake_twai.c`, the implementor's until this step.
- **Status:** complete.
</content>
