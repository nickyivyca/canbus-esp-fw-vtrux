# Shared TWAI port: specification

**Status:** approved by the user on 2026-10-31, in the gen-inhibit
reviewer's session (T2's three modes, T7's `tx_refused` and its count of
flushed frames the same day, on the implementor's and the charge-interposer
reviewer's questions), and drafted that day by the gen-inhibit reviewer for
gen-inhibit's port step 1b, after the charge-interposer reviewer's read. T12
and T13 approved by the user on 2026-11-01, there, and drafted that day by
the gen-inhibit reviewer for port step 1c. As
a shared component's spec this follows port plan goal 20: the reviewer whose
project needs a change drafts it, the user rules in that reviewer's session,
and the other project's reviewer reads it before it merges into
`shared/main`. T14, T15 and T17 to T19 approved by the user on 2026-11-27,
in the charge-interposer reviewer's session, and drafted that day by the
charge-interposer reviewer for the interposer's stage (b), after the
gen-inhibit reviewer's read. T16 is held (see T16).

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file.
- **Test evidence:** `VERIFICATION.md`, the tester's, when there is code to
  test.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (goal 15, and `twai_port` and the host fake under Layer 2 of "The
  split"), `shared/can_port/SPEC.md` and
  `shared/boards/SPEC.md`.

Requirements are numbered T1 to T19. The numbers are stable.

- **T1. One component.** `shared/twai_port/` is one ESP-IDF component,
  `vtrux/twai_port`, in C on ESP-IDF APIs only (port plan goal 15). It is
  the driver for the chip's built-in CAN controller (TWAI) behind `can_port`
  (`can_port` C1). It is made from gen-inhibit's `can.c` and the TWAI
  register code in `gen_inhibit.c` (the transmit abort and the buffer-state
  read), at the commit the move is made from. The interposer adopts it at
  its port step 2b, in place of `port_twai.cpp`.
- **T2. What each project chooses.** A project gives these when it sets up
  a port, and the port does what they say:
  - the board's CAN port index, from which the port takes its pins and
    standby line (`shared/boards` B5);
  - the bit timing;
  - the mode, and the mode it installs in: normal, listen-only, or no-ACK
    self-test (the chip's loopback: no ACK, and each frame sent is also
    received);
  - the acceptance filter and mask, and single-shot transmission;
  - the RX and TX queue depths;
  - bus-off recovery: never, or automatic;
  - whether RTR frames are dropped;
  - on an install failure: abort (restart), or report and stay uninstalled;
  - a pre-teardown hook, run before every teardown;
  - an install hook, run before every install, which may replace the
    acceptance filter for that install.
- **T3. Teardown.** It runs the pre-teardown hook, then reads the
  controller's state. While the controller is recovering from bus-off it
  refuses, reports the refusal, and the driver stays installed and is
  reported as installed. It stops the controller only from running, and
  uninstalls it from stopped or bus-off. Every return value is checked. The
  port reports itself down only once the uninstall has succeeded.
- **T4. Mode changes and reinstalls.** A mode change is a teardown (T3)
  then an install. The port records the new mode only once the teardown
  has succeeded, so a refused teardown leaves both the record and the
  controller in the old mode. A reconfigure that changes nothing does
  nothing. A failed install reports which step failed (install, start,
  alerts) and leaves the driver uninstalled, unless the project chose abort
  (T2). A teardown discards both driver queues, TX and RX; the port reads
  how many frames were in each just before the uninstall and adds them to
  T7's counters.
- **T5. The one alert reader.** The port is the only caller of
  `twai_read_alerts` and `twai_reconfigure_alerts`. It enables, from each
  install, the alerts its counters (T7) and its events need. It passes
  events up: transmit failed (held until the project takes it), transmit
  succeeded, bus error, entry into bus-off, receive queue full. Its alert
  poll is public and may be called before the project's service loop
  starts. An event is a bit, and one event may cover several occurrences
  between two polls, so nothing counts events: every count comes from T7.
- **T6. Every send goes through the port.** The port is the only caller of
  `twai_transmit`. A send waits at most the time its caller gives (zero:
  enqueue or fail). Every refused send is counted (T7): with the TX queue
  full as `tx_dropped`, for any other reason as `tx_refused`.
- **T7. Counters and status.** One status read gives the controller's
  state, the queue depths (frames to transmit, including the one in
  flight; frames waiting to be received), the error counters and these
  counters:
  - frames received and frames sent;
  - **frames lost on receive**, however they were lost: the driver's queue
    full, the controller's FIFO overrun, and any ring of the port's own.
    Per frame where the hardware gives it;
  - sends refused with the TX queue full (`tx_dropped`); sends refused for
    any other reason, such as the port down, bus-off or an invalid frame
    (`tx_refused`); both apart from transmissions the controller reports
    failed (`tx_failed`);
  - bus errors;
  - bus-off as a level and as a count of entries, counted once per episode;
  - frames queued for transmission that bus-off recovery discards, read
    before recovery starts;
  - frames in the TX and in the RX queue that a teardown discards (T4);
  - frames waiting to be received that a flush discards, read just before
    the flush: a deliberate discard, not a loss;
  - the RX queue's high-water mark.

  The counters carry across a reinstall as deltas, so they never go
  backwards. A project that reports per-install figures computes them from
  the counters at its own install.
- **T8. Transmit abort and buffer state.** The port gives the transmit
  buffer's state (empty, transmitting, awaiting arbitration) and an
  abort-if-awaiting: it reads the state and, only if awaiting, issues the
  abort command, in one critical section. Neither touches the interrupt
  register. The abort-if-awaiting is in IRAM and callable from a panic
  handler (the restart stop, T12, calls it). This code is
  register-level, in one file per chip that names the ESP-IDF version it was
  checked against; only the C3's is measured (gen-inhibit spec 5.2 item 4).
  On a chip with no measured file, such as the S3, the component still
  builds, and the buffer state and the abort report themselves unsupported
  until a measurement is made there.
- **T9. Rules.** Every wait inside the port is bounded: none outlasts 1 s
  (gen-inhibit's refusal limit), and a receive waits no longer than its
  caller gives (gen-inhibit's is its 200 ms quiet-bus wait). The port takes
  no lock that waits on another task, starts no task, and calls the project
  only through the hooks of T2.
- **T10. One host fake.** `twai_port/test/` holds one fake of the TWAI
  driver for host builds, merged from gen-inhibit's `fake_twai.c` (with its
  register fakes) and the interposer's `twai_fake.cpp` (with its S3
  calibration and recovery). It takes a clock adapter in place of Arduino
  `millis()`, and models the C3 and the S3 apart wherever they are found to
  differ. Both projects' host builds compile the real `twai_port` on it.
  Each project keeps its own logic-level fakes.
- **T11. Ownership** is per file in the component's `README.md`: the port's
  code and its unit tests are the implementors'; the fake of T10 is the
  testers' (user, 2026-10-31, in both reviewers' sessions). The test app of
  T13 is the implementors', as the port's code.
- **T12. The restart stop.** The port gives a call that takes the chip's
  TWAI controller off the bus from the restart path (`can_port` C7,
  `shared/restart_guard` G3), with interrupts already off:
  - if the TWAI clock is on and the peripheral is not held in reset, it
    waits, bounded at 1 ms in steps of 2 us, for the controller to be
    neither transmitting nor receiving, running the abort-if-awaiting of T8
    on every poll, and then puts the controller into reset mode, even if
    the wait ran out;
  - otherwise it touches nothing: the controller is then not on the bus.

  It reads the controller's registers, not the driver's state, so it acts
  whether or not the port was set up in this boot. Nothing takes the
  controller out of reset mode until the next install. It reports, for the
  guard's record (`shared/restart_guard` G4): the clock and reset bits,
  whether the controller was already in reset mode, the status register at
  entry and as reset mode was entered, the transmit and receive error
  counters, the time waited, whether the wait ran out, the frames aborted,
  and whether it entered reset mode. It is in IRAM, takes no lock,
  allocates nothing and logs nothing. It is register-level, in T8's file
  per chip; only the C3's is measured (gen-inhibit spec 3). On any other
  chip it reports "not supported" and touches nothing. On a chip with two
  cores, such as the S3, it first stalls the other core, before the steps
  above, so that nothing on that core loads a frame during the wait. The
  other core is not started again: the restart follows (added 2026-11-27,
  user).
- **T13. The test app.** `twai_port/test_app/` holds the transmit-abort
  probe, moved from gen-inhibit's `gi_txabort_probe.c`: a measurement of
  what the chip's abort command does to a frame awaiting arbitration, to
  one being transmitted and to an empty buffer, what the driver reports
  afterwards, and how long the buffer takes to come free (gen-inhibit spec
  5.2 item 9). It is compiled into a project's image only when the
  component's build switch `TWAI_PORT_TXABORT_PROBE` is set, and never into
  a release image (`shared/tools` R20). The project registers it from its
  URI hook (`shared/wican` W8), passing a check of whether the project owns
  the bus; the probe refuses while it does. Its URI, `POST /gi_txabort`, its
  parameters and its answer are unchanged from gen-inhibit's at the move
  (user, 2026-11-01). Its results are evidence for this component version,
  citable by both projects.

  It also measures the following on a bus where no node acknowledges, in
  the error-active and error-passive states (gen-inhibit tracker F5, added
  2026-11-27, user):
  - what the status register's TS bit reads between the controller's
    automatic resends;
  - what the abort command does to the frame during an attempt and in the
    idle between attempts.

- **T14. The kept-driver switch.** A project may choose it (T2) for a port
  that installs in normal mode. In place of T4's teardown and install, a
  change between normal and listen-only then keeps the driver installed,
  so no queue is discarded by the switch. The driver's mode is fixed at
  install and gates every send, so the port installs in normal mode and
  reaches listen-only only through the controller's registers. In
  listen-only the port's own flag refuses every send, counted as
  `tx_refused` (T6). A change to or from no-ACK self-test is still T4's.
  The switch is register-level code in T8's file per chip, and is
  available on a chip only once measured there. On any other chip, setting
  up a port with it fails as "not supported".
- **T15. The error counters at a switch.** The switch writes the receive
  error counter in reset mode: 128 on the way into listen-only, as the
  driver's listen-only errata workaround does at start, and 0 on the way
  out, as the driver does at stop. A listen-only controller is then
  error-passive and sends no active error flag. After each switch the port
  reads the register back and reports it. On a port that chose T14, T7's
  error counters come from the registers in every mode, not from the
  driver's values, which read 0 in listen-only.
- **T16. The switch's placement in the inter-frame gap.** Held (user,
  2026-11-27). It is written once T13's measurements on a bus where no node
  acknowledges have reported on the C3 and on the S3 (T19). The number is
  kept for it.
- **T17. Frames held at a switch to listen-only.** The frame in
  transmission and the frames queued behind it cannot be sent in
  listen-only. The switch removes them, and for each one the project can
  tell which frame it was: the frame itself, or a tag the project gave at
  the send. The port cannot tell a bridged frame from the project's own
  frames, so the project books them. T7 counts them as a new counter,
  frames discarded at a switch, apart from T4's teardown discards. Each
  frame is counted once: a frame the switch aborts is not also counted as
  `tx_failed`.
- **T18. The bus-error count is exact up to the switch.** Before a T14
  switch, the port polls its alerts once more after the controller stops
  sending, so T7's bus-error count includes every bus error up to the
  switch.
- **T19. T8 and T12 on the S3.** T8's file for the ESP32-S3 names the
  ESP-IDF version it was checked against (v5.4.2) and is measured on the
  S3 before it reports itself supported: the transmit abort and buffer
  state (T8), the restart stop (T12) and the kept-driver switch (T14-T18).
  It also runs T13's measurements on a bus where no node acknowledges,
  since T16's wait and T17's abort depend on what TS reads between
  automatic resends. The measurement is a test app as T13's, built for the
  X2 and started by a serial command, since the X2 has no URI hook. It is
  compiled in only when its build switch is set, and never into a release
  image (`shared/tools` R20). Its results are evidence for this component
  version.
