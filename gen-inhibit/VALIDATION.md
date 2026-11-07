# Generator inhibitor (WiCAN): validation evidence

Why each requirement in `SPEC.md` is written as it is: the measurements,
readings, corrections and user rulings behind it. Test evidence, that a
commit meets the spec, is in `VERIFICATION.md`. Entries are keyed by
`SPEC.md`'s section numbers.

Moved from the spec in reverse-it on 2026-11-07, as a move only (review
tracker C6-C6c): the text below is the spec's own, taken out of the
requirements and grouped under the requirement it explains.

**Where things are.** `notes/` below is
`$REVERSE_IT_DIR/projects/vtrux/notes/`. The review tracker is
`notes/gen-inhibit-review-2026-09-22.md`; review item ids (A1, B3, ...)
are its sections. The working log, `notes/gen-inhibit-wican-firmware.md`,
is a record: the narrative of how the early results were obtained. The
measured constraints and the laptop tool's lessons are in
`notes/gen-inhibit-truck-tests.md`. Commits named below are of this
project's branch (`gen-inhibit/main`, or `gen-inhibit/rollback-fix` before
it was renamed 2026-10-12) at the time.

**Status** of each entry is *validated* or *basis pending*, with what is
missing.

## The document

- **History.** Revised 2026-09-24 from the external review (the tracker):
  the PASSIVE mode, the contactor-gated SoC rule, the explicit arm-gate
  signal list, stale-signal aborts, the on-wire transmit-failure
  definition, the no-other-transmitter rule, and the emulation and test
  requirements. Each changed passage named its review item, kept here.
  Converted from the working log.
- `plans/gen-inhibit-esp-firmware.md`, the pre-spec findings document, was
  deleted 2026-09-17 as implemented or skipped. Its still-live parts were
  folded into the spec first: the rig-fidelity requirements and the
  measured-stimulus proposal into section 12.2, the key-off-versus-we-broke-it
  reporting lesson into section 7, and its two unanswered questions into
  section 14. Everything else it held is either satisfied by the spec or
  recoverable from `notes/gen-inhibit-truck-tests.md`, which was its own
  stated source.
- **Moved into git** 2026-11-07 (user, tracker C6, C6a', C6b, C6c, and the
  row "The spec move into git, how"). Cleaned up first, per C6: the
  device's id and the pointer to the backup of its configuration, which
  holds credentials, are not carried into git; both stay in SeaDrive.

### The firmware base
- **Basis:** v4.21 exists and was rejected on 2026-09-19, because it fixes
  none of the defects in section 9, leaves ~46 KB of OTA slot, and resets
  device configuration on update. The branch was renamed
  `gen-inhibit/main` 2026-10-12 (user); it was `gen-inhibit/rollback-fix`.
- **Status:** validated.

## 1. Purpose

- **Basis:** the laptop implementation proved the inhibit on the truck on
  2026-09-06: against a commanded 1736 counts the inverter's feedback never
  exceeded 18, the engine turned 0.0 rpm, and it started within seconds of
  a scheduled disarm.
- Goal 4 is the reason the firmware work started with rollback rather than
  with the inhibitor.
- **Status:** validated.

## 2. Target, and why recovery dominates the design

- **The board on hand** reported `fw_version 4.13` and
  `hw_version WiCAN-OBD` when the spec was written (its device id is kept
  in SeaDrive). The C3 was chosen deliberately: dedicating the cheaper
  board to one fixed job frees the Pro for general work.
- **Why USB is the net.** MeatPi document no factory reset and no safe mode
  for the non-Pro board, and the product has no exposed USB port. On that
  reading an image that boots but fails to bring up its network stack is
  unrecoverable without opening the case, and bootloader rollback is the
  only automatic way back. That reading held until 2026-09-10, when the
  case was opened and a USB port was found: the board has one, only no
  cable was shipped.
- **Why rollback is kept:** recovering a silent device costs a drive home
  and a bench session, and until someone notices, the inhibit is not
  running.
- **Why a USB-flashed image is not armed for rollback:** USB is the net for
  USB-flashed builds, and arming rollback on them would risk a boot loop.
- **Not carried into git** (C6): the recovery inventory's row for the
  device's configuration and status backup, which holds WiFi and MQTT
  credentials. It stays in the reverse-it spec's history.
- **Status:** validated.

### 2.1 Power on the truck
- **Basis:** added 2026-10-12 (user). USB not connected on the truck, and
  the DC-DC holding 12 V at about 14 V with HV up: user, 2026-10-09.
- **Status:** validated.

## 3. Modes and ownership of the bus

### The worker's priority
- **Basis:** left as is (user, 2026-10-10): on the bench INHIBIT dropped no
  frames under a WiFi flood, on entry into live traffic, or over 300 s.
- **Status:** validated.

### The mode table and RESPOND
- The `0x7F0` probe's layout is published in the schema doc (user,
  2026-10-12).
- **`offset_us` above 4000 is rejected** because a probe scheduled beyond
  the shortest observed VCM inter-frame gap (4.69 ms) would land after the
  next genuine frame and make the measurement meaningless.
- **Arming brings the bus up itself:** this removed the original soft-lock
  where arming an idle device left the worker busy-waiting on
  `ESP_ERR_INVALID_STATE` at priority 18 and starved httpd.
- **Status:** validated.

### A mode change takes effect at once
- **Basis:** added 2026-10-10 (user). Before this, the idle worker checked
  for a mode change every 50 ms, and the driver started receiving before
  the worker looked. About 120 frames arrived for the 64-deep queue on
  every re-arm from OBSERVE, and on 1 in 5 entries from OFF (review
  tracker, re-arm stale frames).
- **Status:** validated.

### The worker makes every mode change
- **Basis:** added 2026-10-16 (user). Why: on `c6fdab5` the setter ran in
  the httpd task and reset the scheduler and the core while the
  higher-priority worker could wake on a frame part-way through, and the
  quiesce wrote the mode and its flags from the caller's task the same
  way. The 1 s rule (user, 2026-10-16) replaced "The setter waits at most
  1 s".
- **Status:** validated.

### No restart leaves the controller on the bus
- **Basis:** added 2026-10-13 (user). On the ESP32-C3 a software restart
  resets only the CPU, and a running controller keeps going at half its
  clock until the bootloader restores it. On the bench without this, each
  live restart put 107-139 error frames on the bus, destroyed replayed
  frames and drove the replaying adapter bus-off (2026-10-12). With it, on
  the trial build `089cd21`, 6 of 6 live restarts (3 reboots, 3 crashes)
  put 0 (2026-10-13).
- **Context, a known limit:** resets that bypass software (watchdog final
  stages, brown-out, power loss) are read as resetting the controller too,
  which is not measured. What they put on the bus is measured with the
  cold power-on (13).
- **Status:** validated.

### OBSERVE is silent in hardware
- **Basis:** rewritten 2026-09-24 (review B1). The earlier text described
  an application-silent OBSERVE with diag on top, which the firmware
  stopped doing on 2026-09-19.
- **Why standby is not a silent tap:** on the MCP2561/2 the STBY pin
  disables the receiver too.
- **Status:** validated.

### 3.1 PASSIVE
- **Basis:** added 2026-09-24 (review B1). There was no mode that watches
  the truck and still reports its reasoning on the wire: OBSERVE is
  deliberately mute, and RESPOND emits a `0x7F0` after every `0x051`.
  PASSIVE fills that gap.
- **The would-transmit count resets on every arm:** added 2026-10-12
  (user); it had counted since boot across every arm.
- **Status:** validated.

### 3.2 No other transmitter
- **Basis:** added 2026-09-24 (review A3). The hazard this closes,
  measured in the code 2026-09-22: an SLCAN `C` (close) called
  `can_disable()`, which quiesced the worker and forced mode OFF with no
  event and no diag, disarming an auto-armed truck unit for the rest of the
  power cycle; common SLCAN clients send `C` when they open a session.
  Loopback (`Y`) also made client-sent frames self-received by the worker,
  which would have answered an injected `0x051` as if it were the VCM's.
- **The SLCAN switch:** added 2026-10-29 (user).
- **Only `gen_inhibit` and the OTA handler change bus state, in every
  mode:** added 2026-10-16 (user). The other callers, on the 2026-10-16
  audit of `65e9446`: the boot-time bus enables for the RealDash, ELM327,
  AutoPID and MQTT protocols, MQTT's `can_enable()` before a transmit, and
  the ELM327 `ATSP` protocol switch and its receive-queue flush (which ran
  even while armed). The build switches: added 2026-10-16 (user).
- **The abort probe's exemption:** `twai_port`'s test app from port step
  1c; before it, `gi_txabort_probe.c`.
- **Arming after the boot-time configuration:** added 2026-10-16 (user).
  On `65e9446` an auto-arm build armed before it and installed with the
  stock defaults (500K, not listen-only), which match this device's
  configuration only by coincidence.
- **Refusals counted:** changed 2026-10-12 (user). It previously said
  "logged", but every log is muted after boot, so nothing recorded a
  refusal.
- **Status:** validated.

### 3.3 Hooks into the shared stock code
- **Basis:** added 2026-10-29 (user), port step 1a, moving the stock code
  into `shared/wican`; the move changed none of the rules the hooks carry
  out, and the refusal bodies were unchanged by it. The CAN port: port
  step 1b (user, 2026-10-31), including the bring-up failure rule.
- **Status:** validated.

## 4. The inhibit frame

- **Basis:** the encoding is settled by three independent sources: the DBC
  (`vtrux-powertrain-experimental.dbc`, in reverse-it's
  `projects/vtrux/`), the truck-validated `inhibit.py` in
  `payload_mode=mirror`, and 18k real frames decoded with cantools
  (`notes/artifacts/gen-inhibit/zero_torque_confirm.py`).
- **Stealing the counter:** the truck experiment confirmed the mechanism
  works whatever the GENE MCU's exact rule.
- **Status:** validated.

### 4.1 B0 is held at the engine-off state

- **Changed 2026-09-19.** B0 was previously mirrored from the VCM, which
  is what the truck test used. It is now held at `0x08` for the duration of
  the inhibit. B3/B4 are still mirrored.

B0 is not in the DBC and is not a confirmed signal, so it was surveyed across
every log in `projects/vtrux/logs/` before changing anything
(`notes/artifacts/gen-inhibit/gen_state_byte.py`; counts in
`gen_state_byte_b0scan.txt`, the analysis below in
`gen_state_byte_outliers.txt`).

**B0 takes at least three values, not the two previously recorded:**

| B0 | frames | logs | commanded torque | HCU/inverter state (`0x053`) |
|---|---|---|---|---|
| `0x10` | 5,566,745 | 67 | zero in 100 % | **SHTDWN**, 99.0 % |
| `0x08` | 2,685,364 | 69 | zero in >99.99 % | OPERATIONAL, 96.6 % |
| `0x0B` | 1,402,293 | 24 | nonzero in ~99.5 % | OPERATIONAL, 100 % |

`0x10` dominates charging and export sessions and maps to the shutdown state of
the `0x053` state machine (INIT / STBY / ACTIVATION / DEACTIVATION /
OPERATIONAL / FAILURE / SHTDWN / WAIT4COOLING / PWR_OFF_RDY). That
correspondence was checked on a log where the GENE family is present and
talking, because a capture taken with the generator-inverter fuse pulled shows
no GENE traffic for an unrelated reason and would have produced a false
conclusion.

`0x053` separates `0x10` from the rest but **does not separate `0x08` from
`0x0B`** -- both read OPERATIONAL. The difference between them is inside that
state, and it is visible in power rather than in any status field.

**Why holding `0x08` is specified.** Three measurements together:

1. **The VCM never sustains `0x0B` with zero torque.** Across the logs
   examined, the longest such run is **0.19 s** -- it is a transition the
   command passes through, not a state it rests in. The `(0x08, nonzero)`
   outliers are the same thing in the other direction, a handful of frames out
   of hundreds of thousands.
2. **The only sustained `(0x0B, zero torque)` anywhere in the corpus is our own
   inhibit.** In `truck_real3` it ran for **58.7 s** across 4,123 frames, all
   of them ours.
3. **The inverter draws power throughout it.** `gen_power` on `0x598` over that
   same window:

| window | gen_power |
|---|---|
| before arming, B0 `0x08`, engine off | median **-0.01 kW** |
| during the inhibit, B0 `0x0B` mirrored | median **-0.11 kW**, excursions to **-2.89 kW** |
| after disarm, engine running | median +1.93 kW |

Mirroring `0x0B` therefore leaves the inverter energised and consuming roughly
ten times the standing draw of the true engine-off state, for as long as the
inhibit is held, with multi-kilowatt excursions. Commanding zero torque in a
state whose meaning is "generator active" is not the same as the generator
being off, and the power figure is the evidence that it is not.

Holding `0x08` asks for the state the truck itself uses whenever the engine is
off and no torque is wanted, which is what the inhibit is trying to imitate.

**What this does not have behind it.** The truck test that produced
`INHIBIT HELD` mirrored B0. Holding `0x08` is a change to the one configuration
proven on the vehicle.

**Two risks remain and neither can be closed on a bench**, because the bench
has no VCM and no inverter:

1. whether a real VCM raises a fault when commanded to the engine-off state
   while it is mid-crank-request;
2. whether the stolen counter still causes the VCM's own frame to be rejected
   when B0 is held rather than mirrored. What the VCM *does* about it is a
   truck question.

- **Status:** validated for holding `0x08` over mirroring; the two risks
  above are open and are truck questions.

## 5. Transmit strategy and timing

### Reactive trail and the deadline
- **Basis:** the reactive trail matches the truck-validated tool. The
  4.69 ms is the minimum VCM `0x051` inter-arrival measured on the truck
  (range 4.69-16.4 ms, median 9.4-10.5 ms, counter `+1` 100 % of the time,
  unchanged across an engine start).
- **Status:** validated.

### The IRAM interrupt option
- **Basis:** measured with the flash and IRAM builds, the flash build's
  tail sat inside the IRAM build's own spread: the busy-WiFi tail is
  scheduling contention with WiFi/lwIP, which an IRAM ISR does not
  address. It *would* matter while flash is written with the cache off,
  which is why section 9.3 refuses OTA while armed instead.
- **Status:** validated.

### The design case and the armed rate
- **Basis:** the design case is the user's, 2026-09-26. ~2386 frames/s is
  from a full-bus truck capture, 2026-09-26, so section 9.3's ~2250 is
  about 6 % low rather than high.
- **Status:** validated.

### The hazards 5.1 fixes
- **Receive loss.** The driver's default RX queue is 5 frames deep behind an
  accept-all filter. At truck load (~2250 frames/s, section 9.3) that is
  ~2.2 ms, against a measured worst WiFi-preemption delay of 2.39 ms, the
  figure `WORST_PREEMPT_US` stands for. A dropped `0x051` is an unanswered
  one: the inverter acts on the VCM's torque for that slot.
- **Transmit ordering.** The controller has one TX buffer and the driver
  queues first-in-first-out, so a diag frame (the lowest priority on the
  bus) sitting in the buffer waiting for an idle bus delays an inhibit
  `0x051` queued behind it.
- **The E4 load model confirmed both hazards** (2026-09-25): at the measured
  worst preemption the 5-deep receive queue loses about one frame -- roughly
  one time in twenty the VCM's `0x051` -- and a diag page queued ahead of an
  inhibit delays it (`tx_behind` = 2). An intermittent unanswered frame is
  the kind that gets attributed to anything else.
- **Status:** validated.

### 5.1 The fix
- **Basis:** specified 2026-09-26 (user).
- **Item 1, the queue depth.** Changed 2026-09-26 (user): it was "at least
  32". 32 met the 3x margin against 2.39 ms, but a device-side stall of at
  least 6.8 ms was later seen under a WiFi flood (full-bus truck replay,
  2026-09-26, on `debfeca`). That is outside the design case, and 64 costs
  about 1 KB of RAM. This is the remedy that makes the margin positive
  rather than smaller: WiFi/lwIP preemption blocks the *worker task*, not
  the TWAI interrupt, which keeps moving frames from the controller into
  this queue, so the queue is the buffer that has to cover preemption x
  frame rate. Corrected 2026-09-27 (user): the device detects lateness
  itself only when the receive queue is empty at that moment.
- **Item 2, no filter narrowing.** Narrowing cannot close the hazard on its
  own -- the IDs that remain still fill a 5-deep queue in ~2.5 ms -- the
  C3's single/dual filter masks cannot express the core's ID set exactly,
  and a mask that drops one of those IDs fails silently (a device that
  never goes live, or trips stale).
- **Item 3, no flash write while owned.** `CONFIG_TWAI_ISR_IN_IRAM` stays
  unset (section 5), so while the flash cache is disabled for a write the
  TWAI interrupt cannot run and the controller's small hardware FIFO -- a
  few frames, ~2 ms at truck load -- is the only buffer; a deeper software
  queue does not help. Section 9.3 already refuses OTA while armed for this
  reason. OTA is not the only flash writer: audited 2026-09-26, the HTTP
  handlers `store_config`, `store_canflt`, `store_auto_data`,
  `store_car_data` and `upload_car_data` all write SPIFFS files.
- **Item 4, diag placement.** Changed 2026-09-26 (user). This item
  previously added "and if no inhibit completion has been observed for 1 s
  the normal cadence resumes". **That 1 s fallback is withdrawn**, and the
  mode condition and the clearing rule replace it. The reasoning, because
  the fallback was not pointless when it was written:

  - It was load-bearing for a **defect**, not for the case it names. The
    implementation tested liveness and the key with no mode term, and
    `inhibit_live` is set in PASSIVE exactly as in INHIBIT (3.1), so switching
    a live INHIBIT to PASSIVE left the device waiting for a completion that
    could never arrive. The 1 s fallback was the only thing that rescued it.
    Naming the mode fixes the cause instead.
  - What remained of its job was to stop a **stale** completion timestamp being
    read as recent -- stale because the record was set in one place and never
    cleared, so a device went live again carrying a timestamp from before a
    trip or a key cycle. Clearing the record addresses that directly, and says
    what it means.
  - Without it, the worst case is a diag hold from going live to the first
    completion: one `0x051` gap, 4.69-16.4 ms, or up to about half a second if
    the device goes live and the bus then stops, bounded by the trip that
    follows. Not silence.

  **The argument for keeping it, recorded because it is the one that carries
  weight.** Its value lay exactly in the case where the reachability reasoning
  above is wrong, and this component has produced two failures of that shape in
  one week (four bus-blind corpus scans agreeing with each other; two sessions
  "independently replicating" a witness artefact they had both inherited). It
  was withdrawn on the ground that the failure it guarded is now bounded at one
  `0x051` gap rather than being unbounded silence -- not on the ground that the
  state is provably unreachable.

  Amended 2026-09-27 (user): placement is no longer the safety mechanism.
  A diag page queued right after a completion still reached the wire after
  the VCM's next command once in a 300 s full-bus replay (it waited ~10.5 ms
  for arbitration; 5.2 below). The TWAI controller has a single transmit
  buffer fed first-in-first-out, so a low-priority frame waiting in it holds
  back a high-priority one behind it, however it was placed.
- **Status:** validated.

### 5.2 Transmit scheduler
- **Basis:** specified 2026-09-27 (user).

**Why.** A 300 s full-bus replay on the shipping build (WiFi idle) produced
one inhibit frame on the wire *after* the VCM's next command, and a TX_LATE
abort. The raw witness log shows our diag page `0x7F3` going out 0.17 ms
ahead of it, having waited ~10.5 ms for arbitration since the completion it
was queued after (715 diag pages over the arm: p50 1.1, p99 2.7, max
10.5 ms). The TWAI controller has **one transmit buffer**, fed
first-in-first-out by the driver, so a lowest-priority frame waiting in it
blocks a highest-priority frame behind it. The bus would let the inhibit win;
the controller never offers it. A lowest-priority frame's wait for an idle
bus has no hard bound. The bench replay made this instance likelier than the
truck would (section 12.2: batched timestamps squeeze VCM gaps and saturate
the bus in patches), but the mechanism needs neither.

The user rejected removing diag from the bus while inhibiting: *"the whole
point of the status messages is that they are around all the time. even if we
do not have it yet now, we may in the future want to have functionality
logged during inhibit. So we should structure such that this is easier."*
And the constraint on the design: *"must be architected so that it can be
rigorously emulated on the bench. (and the same cases need to pass on
hardware after emulated)"*.

- **Item 1:** only `twai_port`'s send calls the TWAI transmit API since
  port step 1b (user, 2026-10-31).
- **Item 4, the 1 ms bound:** user, 2026-09-27. *Why a loop and not one
  abort (measured 2026-09-27, item 9).* An abort that lands while our
  frame is in its arbitration field, where the frame then loses, **has no
  effect**. The frame stays in the buffer and keeps contending, with no
  completion event, only ARB_LOST. Under contention a single abort did this
  to 7 of 78 frames. Once the frame has lost, TS reads 0 until the next
  start of frame, and an abort then is the awaiting-arbitration case, which
  worked every time.
- **Item 5:** measured, 169 of 169 aborted frames that never reached the
  wire were reported as a successful transmission. The deadline is the
  user's, 2026-09-27. A late frame counts as a skip: user, 2026-09-27,
  replacing "trips at once". Stand-downs are not skips: user, 2026-10-16.
- **Item 6:** the counts are not in a diag page (section 10, user,
  2026-09-29). The balance identity was added 2026-09-29 (user).
  - `carried_in`: without it, a carried frame's completion landed in the
    new arm with nothing queued against it. This happened on 3 bench arms
    (+1 each).
  - `held`: corrected 2026-09-29 (user). It first read "the frame in the
    controller right now"; a bench reading with `held` 1 and the driver's
    `msgs_to_tx` 0 would have read one short under that wording. Without
    it, that was 4 bench arms at -1, and it is the one gap "telemetry is
    never lost silently" cannot leave open.
  - `withdrawn`: added 2026-09-29 (user). The equality was changed
    2026-10-16 (user); it read `withdrawn + dropped = skipped`. The first
    version of the balance identity omitted `withdrawn`, and so did not
    balance on any reading with a withdrawn skip.
  - **A stand-down withdrawal ends the wait:** on `c65c45e` it did not: an
    M-mode release 260 us after a `0x051` left the pages silent until the
    re-arm (bench B20 `liveflag-mmode`).
  - **A re-arm while a withdrawal is still running:** added 2026-10-16
    (user). Without this the new arm books the frame `withdrawn` when the
    loop removes it, with nothing on the right, and its equality reads one
    over for the rest of the arm. It is reachable only by timing: a re-arm
    within about 0.4 ms of the withdrawal starting (implementor's reading
    of `c6fdab5`).
  - **The longest hold and wait exclude a carried-in frame:** user,
    2026-09-29. Booking it into the new arm's longest hold produced figures
    of 142-310 s on 90 s arms.
- **Item 9:** the two requirements on the model were added 2026-09-27
  (user). Measured on the bench C3 (provenance in `VERIFICATION.md`):

  | Abort issued when | Trials | Result |
  |---|---|---|
  | awaiting arbitration (TBS = 0, TS = 0) | 162 | removed every time, never on the wire; buffer free in 6-21 us; reported TX_IDLE + TX_SUCCESS |
  | transmitting, idle bus (TS = 1) | 37 | a no-op: the frame completed on the wire |
  | TS = 1 under contention, single abort | 8 | 7 **no effect**: frame still in the buffer, ARB_LOST only, no completion. 1 removed, reported TX_IDLE + TX_SUCCESS + ARB_LOST |
  | the item 4 loop under contention | 78 | 78 freed, none on the wire, 1-2 abort commands, 5/6/36 us (min/median/max) from the first abort to a free buffer |
  | empty buffer (control) | 5 | no TX alert |
  | no abort (a genuine completion) | 10 | on the wire, buffer free in 242/249/253 us, TX_IDLE + TX_SUCCESS |

  The abort has been issued by `twai_port`'s abort-if-awaiting since port
  step 1b.
- **Item 10:** both halves replay recorded truck traffic (user,
  2026-10-02). *Why the existing harnesses did not do it:* the replay
  harness compiled only the core, so replayed traffic never reached the
  scheduler. A synthetic long run (fixed 10 ms commands) misses the real
  command timing, the real arm and key transitions, and the dense traffic
  that wakes the worker on the truck. The whole capture in the default
  suite, and the emulation half's acceptance list: user, 2026-10-03. The
  replay reader is where the `83dbec2` shortfall came from; the mock clock
  was not the cause and is not to be rebuilt (user, 2026-10-03).
  Emulation replays traffic for its stimulus, not for bus load: user,
  2026-10-03.
- **Status:** validated.

<!-- Batch 2 of 5 ends here: sections 6-16 follow in later batches. -->
