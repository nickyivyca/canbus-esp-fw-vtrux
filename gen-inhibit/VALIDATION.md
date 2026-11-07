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

## 6. Conditions that disable the inhibit

### 6.1 M mode engaged
- **Basis:** M mode is hill climbing (`notes/evap-cycle-analysis.md`). The
  value map was confirmed 2026-07-26 against `generator-on-then-off.log`
  independently of this work. `0x1F5` served only as a time-alignment
  corroboration.
- **Status:** validated.

### 6.2 Battery SoC below 21 %
- **The threshold:** decided 2026-09-19. The cost of 21 % is giving up
  inhibiting in the 20-21 % band; the benefit is that there is no window in
  which we are blocking a start the pack genuinely needs.
- **The source:** `0x411` measured 16.9-20.0 Hz on four clean captures
  (review B4). `0x596 bms_soc` reads 0 for its first samples at startup.

**`0x411` is wrong for about a second every time the BMS wakes** (review A1,
2026-09-24). The previous text here called it "valid during startup"; that is
withdrawn. A corpus scan
(`notes/artifacts/gen-inhibit/soc_wake_transient_scan.py`, 114 wakes in 83
captures where the powertrain bus was seen before `0x411` appeared, rig
sessions excluded) found:

- 113 of 114 wakes open on a wrong value and then step to the real one; 112
  read **low**, by a median 13.8 % and up to 20.9 %;
- the correction comes 0.32-1.15 s after the wake, 105 of them at ~1.0 s;
- the wake value is not the value before the BMS slept (0 of 40), and a raw 0
  never appears -- so the earlier "0 is the startup sentinel" framing does not
  describe what the BMS actually does.

It did happen in `vtrux_20260513_174225_T4`: 18.77 % for 1.04 s against a
real 24.41 %, and the device latched `low_soc` for the rest of the drive.

- **The marker's go-live test:** clarified 2026-09-25 (user): the earlier
  wording omitted the current-reading test that section 7 states.

Why the contactor rule is correct and costs nothing: **the engine is started
by the HV generator inverter, so no start is possible before the contactors
close** -- there is nothing to inhibit until then. And in all 103 scanned
wakes where the contactors closed, SoC had already corrected *before* the
first closed state (0 after; smallest margin 0.07 s, typically 0.13 s). The
correction is not caused by the contactors -- it lands at ~1.03 s even where
they never close, and in the longer charging close sequence -- so gating on
the contactors makes the wake reading unusable by construction rather than by
a timing window, and does not depend on the BMS's ~1 s behaviour staying the
same (`soc_wake_vs_contactor.py`).

- **The debounce:** decided 2026-09-25, the value the firmware already used,
  previously stated only in code. Because the release latches for the rest
  of the run, one spurious sample costs the whole drive's inhibiting.

**The truck's own low-battery trigger is 20.0 % on the high-resolution
value.** Measured 2026-09-19 across the whole corpus
(`notes/artifacts/gen-inhibit/engine_start_soc.py` and its output files):
173 engine starts in 140 sessions, each classified by what was true at the
moment of the start. The three causes separate cleanly and do not overlap:

| start cause | n | SoC min | median | max |
|---|---|---|---|---|
| M mode engaged | 103 | 37.91 % | 54.84 % | 93.27 % |
| evap cycle active | 33 | 77.20 % | 79.20 % | 81.02 % |
| neither -- i.e. low battery | 16 | 19.41 % | 20.44 % | 21.00 % |

The three bands do not overlap and there are no stragglers: every low-battery
start in the corpus lies between 19.41 % and 21.00 %, and every evap start
between 77.20 % and 81.02 %. (Classification is on a window rather than the
instant. `shift_lever_pos` can read Drive for a single sample in the middle of
an M-mode drive, which on the first pass misfiled one 46.56 % start as
low-battery.)

Isolating the **entries into charge-sustain** -- a start where the engine had
been off for the previous 300 s and the pack had coasted down through the band
from above 21.5 %, with no evap cycle and not in M -- leaves only two events in
the whole corpus, and both fire at the same place:

| session | SoC at start | lowest SoC in the next 15 min |
|---|---|---|
| `vtrux_20260618_002023` | **20.02 %** | 19.90 % |
| `vtruxcurrentlimitingtest` | **20.23 %** | 20.04 % |

So the truck defends a **20.0 % floor**, starting the engine a fraction of a
percent above it. The wider 19.41-21.00 spread in the table above mixes those
entries with *restarts under sustained load*, which are a different thing: in
one heavy session the engine restarted at 19.09, 19.39, 19.60, 19.67 and
19.96 % while SoC fell to 16.85 % with the engine running, because the
generator could not keep up with demand. Restart-under-load SoC is not a
constant and should not be read as a threshold.

**Why `0x411` and not `0x596`.** `bms_soc` (0x596) is a **floor** of the
high-resolution value, not a round: measured over 15,280 paired samples,
`floor(hires)` matches in 100.0 % of cases and `round(hires)` in only 46.7 %
(hires 12.68 reads 12). A reading of N therefore means the real SoC is
anywhere in [N, N+1). Testing `0x596 <= 20` would fire when the real value
crosses **21.00 %**, a full percentage point early -- in the one case where
it is measurable, 21 s before the truck itself acted. The DBC comment
calling 0x596 "integer-rounded" was wrong and was corrected on 2026-09-19.

**This is why the threshold is 21 % and not 20 %.** At 20 % the inhibitor
would release at the same instant the truck asks for the engine, or a fraction
of a percent after it, leaving a window in which we block a start the pack
genuinely needs. At 21 % the release happens first, with about a percentage
point of margin, and the truck never has to contend with us on the way down.

- **Status:** validated.

### 6.3 The VCM commands generator shutdown
- **Basis:** reviewed and accepted 2026-09-19. The original request was for
  `0x10` to join the latching release list; the Park measurement changed it
  to a non-latching suppression, and that shape is confirmed. `0x10` maps
  to **SHTDWN** on the `0x053` state machine, 99.0 % (4.1).

**Why it must not latch, unlike 6.1 and 6.2.** `0x10` is a *parked* state, not
a statement that the engine is wanted. Measured across four logs covering
**40,697 frames** of `0x10`:

| | |
|---|---|
| frames with the shifter in Park | 40,697 of 40,697, **100 %** |
| frames with the vehicle moving (`trac_rpm` > 50) | **0** |
| shortest / longest episode | 0.8 s / 561 s |
| time since the generator last ran, at entry | 298 s, 381 s, 675 s, or never |

It never occurs while driving, and it does not follow a generator shutdown --
where the generator had run at all, it was five to eleven minutes earlier.
What it looks like is the VCM powering the drive system down while parked and
waking it again, which is why episodes recur: up to four in a single capture.

Two consequences:

1. **Latching would be wrong.** The truck parks, cycles through `0x10`, and
   then drives again in the same power cycle -- `vtrux_partstruck_driveaftercharge`
   does exactly that three times. A latching release would stand the inhibitor
   down for the whole subsequent drive, which is the drive it exists for.
2. **Suppressing is free.** During `0x10` the truck is parked with the drive
   system shut down, so there is no start to inhibit and nothing is given up by
   staying quiet.

It also removes the need for any startup guard. `0x10` is the normal state at
key-on -- most logs that contain it begin in it and leave for `0x08` within
about a second -- and a non-latching suppression handles that case by simply
waiting, with no special rule.

- **Status:** validated.

### 6.4 Reporting
- **The suppression in the flags:** a value in `disable_reason` that
  appeared and vanished as the truck parked and woke would make a log
  harder to read, not easier: a reader seeing it would not be able to tell
  a current suppression from a past one.
- **The live flag clears with a latched release:** added 2026-09-19 after
  the host suite found the flag frozen at `true` for the rest of a run once
  `m_mode` or `low_soc` latched. The live flag is not merely reported -- it
  is one of the terms that authorises transmission -- so leaving it set
  means a control flag reads "authorised to transmit" while transmission is
  forbidden, and the only thing preventing a transmit is the
  latched-disable term sitting beside it in the same condition. That is a
  one-term margin and it depends on the order of a boolean chain.
- **The bench's wake-reading case:** added 2026-09-24.
- **Status:** validated.

## 7. Arming

### The interlocks and freshness
- **Basis:** the interlocks come from the laptop tool, where each fired or
  would have during real truck testing. Rewritten 2026-09-24 (review B2,
  B3, C1, C2) as an explicit list, because the earlier bullets said "refuse
  to arm on stale bus data" while the firmware deliberately permits the GENE
  family's absence at arm time -- a decision that until then lived only in a
  code comment.
- **The windows:** decided 2026-09-25. Measured cadences (review B3/B4, four
  clean captures): `0x051`, `0x639`, `0x054`, `0x471` ~100 Hz; `0x411` and
  `0x440` ~17-20 Hz (`0x440` worst in-presence gap 102 ms); `0x592` 10 Hz;
  `0x617` **4 Hz, worst gap 308 ms**.

**Why `0x617` has its own window.** With one 0.5 s window the slowest signal
sets the device's tolerance of a brief whole-bus dropout: `0x617` is absent
for the dropout plus up to 250 ms more, so any dropout over ~0.25-0.5 s
(depending on its phase) latched the inhibit off until the next key cycle,
and was reported as "0x617 stale" rather than as the link. At 1.0 s (four
periods, >3x the worst measured gap) a whole-bus dropout of up to ~0.5 s
rides through -- bounded by `0x051`'s own 0.5 s window -- and a longer one is
reported as trip 2, because `0x051` now expires first. The cost is up to
0.5 s longer before a VCM that has genuinely gone silent is noticed through
`0x617`; in that case the truck stops anyway, and `0x051`'s own window
catches a silent VCM first.

- **Status:** validated.

### The arm gate
- **Condition 9:** added 2026-10-10 (user). The first frames on the wire of
  a device that went live with a backlog carried stale counters, and the
  device scored them as on time (trip 7, "What the device can see of
  lateness"). On the bench this happened in 10 of 10 re-arms from OBSERVE
  and in 1 of 3 auto-arm boots. Spec 3's wake on a mode change removes
  those causes. On the truck the condition delays going live by the time
  the queue takes to drain, a few milliseconds.
- **The GENE family not required:** measured across bus wakes in the
  corpus: `0x051` appears within ~0.17 s of the bus waking, the GENE family
  ~28 s later or not at all. Requiring it would put the inhibit live ~28 s
  after key-on at best and never at worst, and its silence is positive
  evidence of an unpowered inverter -- which cannot crank. This was bug 1 of
  2026-09-19. The VCM never satisfies condition 6 while the generator runs
  (21,925 real frames at >= 300 rpm, none with the engine-off null and zero
  torque).
- **Condition 5 and resets:** review A2, accepted 2026-09-24.
- **Status:** validated.

### The trips
- **Trip 3:** firing on never-seen was bug 2 of 2026-09-19.
- **Trip 4:** from the laptop tool (`vehicle_runner.py --start-abort-rpm`).
- **Trip 5:** review B3, decided 2026-09-24: option c. The user's reasoning
  for why a latched abort is the right severity: if the VCM or BMS genuinely
  goes offline the truck stops, or the inhibitor has lost the truck; either
  way there is nothing to protect by continuing. Measured: `0x411` stops
  0.26 s after the key read 0. The GENE family rule: decided 2026-09-25.
  Without it the reason depended on which of the two frames happened to
  arrive last: the two identical end-of-drive events in
  `replay-rekey-long` reported one each.
- **Trip 7:** review C1. The previous implementation counted "accepted into
  the driver's queue" as success. Amended 2026-09-27 (user), with the
  section 5.2 transmit scheduler. A frame that went out late is a skip:
  user, 2026-09-27, replacing "trips at once" written earlier the same day.
  The user's reasoning: the generator inverter has already acted on the
  VCM's command carrying that counter, so it ignores the late frame. The
  effect is the same as a skip, one real VCM command acted on. "What the
  device can see of lateness": clarified 2026-09-27 (user).
- **Trip 8:** review C2b. It was previously counted as `tx_fail` with no
  abort and no event.
- **The evidence rule:** decided 2026-09-25. Found by the implementing
  session on 2026-09-24 with `0x617` (4 Hz, then on a 0.5 s window,
  stopping up to 250 ms before `0x051`), where a pulled connector would
  read "VCM fault flag lost". `0x617`'s 1.0 s window removes that
  particular case, but the same race exists for any signal whose window can
  expire before `0x051`'s -- `0x471`, `0x054`, `0x639`, `0x411`, `0x440` --
  so the rule stays. The answered `0x051`: user, 2026-10-16; bench B20 on
  `c65c45e`: in all seven stale-signal cases the device answered that frame
  and none after it. At the end of a drive `0x051` continues for a median
  ~77 s after the BMS stops.
- **Trip 7's controller-reported form:** added 2026-10-14 (user). The
  ESP32-C3's TWAI driver (ESP-IDF v5.4.1 and v5.4.2) reports a frame as
  failed only when its transmit buffer frees without a completed
  transmission. An abort frees it as a success, and a bus-off frees it with
  no report.
- **Bus-off:** added 2026-10-14 (user). Why: a fault in the inhibitor leaves
  the generator working as it would without one, and a device kept off a
  faulty bus adds nothing to the fault.
- **Self-disarm:** review C2a, documenting existing behaviour.
- **Status:** validated.

### Loss of the CAN link
- **Basis:** changed 2026-09-19. Framed as link loss because that is the
  realistic cause -- a pulled connector or a dropped transceiver takes
  every signal at once, and losing the VCU's and the BCM's data together
  has no other plausible explanation.

**It latches deliberately.** The tempting alternative is to stand down and
resume when the bus returns, on the grounds that absence is not a fault. That
is rejected: if the link is *intermittent*, resuming is the wrong response.
This device does not merely observe the bus -- it steals the VCM's rolling
counter and transmits a real `0x051` -- so coming back automatically would mean
doing that repeatedly across a connection already shown to be unsound, and
each resume would re-enter through a gate a flapping bus can satisfy. Latching
turns an intermittent link into one clean stand-down instead of an oscillation.

**Only once live:** a trip that ran on the path *to* going live would latch
an auto-arm build off during the ordinary pre-key-on wait, every drive,
before it had ever inhibited anything.

**The dead-man timer.** Every hazard the timer was still covering reduces to
"the signals that would release us stopped arriving": losing the whole link
is this trip, and losing one module's signals while the link stays up -- the
BMS's `0x411`/`0x440` are a different transmitter from the VCM's `0x051`,
and the BMS LV-connector disconnect test (`projects/vtrux/README.md`, "PT Bus
Disconnect Test Evidence", T2) shows `0x411` dropping while the VCM keeps
broadcasting -- is trip 5. (The previous text said there was "no plausible
way" to lose `0x411` on a healthy link; review B3 corrected that.) The
release-latching conditions of section 6 handle the cases where the truck
legitimately wants the engine; these handle the cases where we can no longer
see what it wants. A timer on top would only add a way for the inhibit to
stop mid-drive for no observed reason.

- **Status:** validated.

### The error-frame trip
- **Basis:** the running-generator bus produces 0.07-0.21 error frames/s
  with the generator running, bursting to 4 in any 10 s, and a background
  measured with the engine off reads ~0/s and is useless. Aborting on the
  first error frame ended three consecutive armed runs. The threshold is
  2.5x headroom over the worst observed burst of 4 in 10 s, while a genuine
  same-ID collision at 100 Hz exceeds it within a second. It is stated in
  the spec so a change to either number is a spec change, and so a
  build-output check has a stated value to compare against (a mutation
  shrinking the window to 1 s once went undetected by every test).
- **How the window moves:** decided 2026-10-08 (user). On `aaab89a`, which
  re-based its count every 10 s, 10 error frames at 1 s spacing tripped
  from only about 1 start phase in 10 (review tracker, trip 6).
- **The recorded time:** added 2026-10-20 (user). Found in B6 session 1
  (`VERIFICATION.md`, lead 1.5 arm 2): ten error frames 9.9999 s apart on
  the bus were recorded 10.0029 s apart, and the trip came at the 13th.
- **Only a recent burst:** user, 2026-10-08. As committed in `477171a`, any
  burst since key-on tripped at every later live check, which made trip 6 a
  latch until key-on.
- **Checked before going live:** added 2026-10-10 (user). Before this, the
  first check came after the first answer, so one inhibit frame went out
  before the latch (bench `trip6_rearm_check` A3 on `fb61892`; shim case 38).
- **Status:** validated.

### 7.1 Key state gates transmission, and a key-on clears every latch
- **Basis:** decided by the user 2026-09-20. Section 7 asks that the device
  be able to tell "the inverter went away because the key was turned off"
  from "because we broke it". This is that distinction, and it turned out to
  be the whole of finding 3: the section 7 inverter-lost trip was measured
  to fire on the **normal end of every drive**, not on any fault. `0x051`
  outlives the key by a median 77 s.
- **The contactor gate through a key-off:** amended 2026-09-24 (review A1,
  B3). Nothing is lost: the engine cannot be cranked before the contactors
  close.
- **Why `0x592`:** the VCM and the HCU are the same module under two names,
  so this arrives from the **same transmitter as `0x051`**. Key state can
  therefore never be less *available* than the ~100 Hz anchor the whole
  design already depends on (though it is sent at 10 Hz, not 100 -- same
  liveness, not the same rate), and losing it means losing the VCM, which is
  the link-loss trip's territory rather than a new failure mode.
- **Context, a known limit: the signal name is not confirmed.**
  `epri-pt-bus.dbc` carries `IgnitionKeyState` with the comment *"not in
  plist sampling"* -- the name was inferred from the decompile, not read
  from the EPRI parameter list. What is measured is the behaviour in the
  table below. If the bit turns out to mean "powertrain awake" rather than
  "key", the gate still works and the numbers still hold, but the section's
  wording would need revisiting.

**What the measurements say.** Epoch-aware over 1943 captures -- see
`notes/artifacts/gen-inhibit/epoch_aware_rerun.py` and
`keyoff_spindown_check.py`:

| | |
|---|---|
| `0x592` present in PT epochs | **1045 / 1045** |
| VCM commands the generator (`B0` = `0x0B`) while the key reads 0 | **0 samples** |
| key-off leads the inverter going silent by | min **+0.034 s**, median **+0.302 s**, **zero negatives** |
| key-offs that would trip section 7 as it stood | **100 / 100** |
| longest *confirmed* inverter silence | **35.43 s** |
| generator turning while the key reads 0 | all **spin-down**, within 0.36 s of a key-off |

The zero in the second row is what makes rule 1 safe: there is no instant in
the corpus where the inhibit is needed and the key reads off. The last row is
the engine coasting to a stop -- confirmed independently by the ECM on
GMLAN `0x0C9`, whose `Engine_Run_Active` drops within 0.01 s of the key-off
and whose `Engine_Speed` falls below 50 rpm within 0.52-0.75 s.

**Two earlier claims are withdrawn, and neither should be reintroduced.** A
bus-blind measurement reported 23 samples of the VCM commanding the generator
with the key reading 0, and a 50-91 ms window in which the VCM asserted before
the key bit published. Both were artifacts of merging logger epochs -- one
`.log` can hold several disjoint recording windows in which the same wire
carries a different channel number. A transmit-gate clause of the form
*"or `B0` = `0x0B`"* was proposed to close that window; there is no window, and
it is withdrawn.

- **The trip needs no key awareness:** the key-ON inverter loss is the case
  worth catching, and the only non-key-off population in the corpus
  (`vtrux_20260714_112312_T2`, five events, with the amber warning lamp
  lit).

> **What those five events are** (corrected 2026-09-26, review decision 2).
> With the key on throughout, the BMS opens the main contactors at 20.1 s
> (`0x440` 12 -> 14 `ALL_OPEN_SHUTDOWN`); the inverter then drops out and
> returns five times (~1.1 s on, 0.7 s off) until the key-off at 28.3 s. They
> are the inverter falling offline *after HV was dropped mid-key-on* -- not a
> start-up event and not a permanently absent inverter. (A permanently absent
> one, fuse pulled, is never heard, so trip 3 cannot fire on it by design, and
> without it nothing can crank.) The device goes live **before** HV drops and
> stays live through it, so the trip fires on real traffic:
> `replay-shutdown-at-keyon` (the whole 0-225 s of the capture) goes live at
> 16.2 s with the contactors closed, trips inverter lost at 21.0 s, latched,
> and is cleared by the next key cycle at 49.1 s.

- **The fixtures:** `replay-rekey-short`'s source was replaced 2026-10-31
  (user): the old one, `vtrux_20260403_194203_T2`, is a bench replay whose
  three key-cycles were the replayer looping one.
  `replay-inverter-lost-keyon` was changed 2026-09-26 (decision 2); it was
  "must still trip". `replay-shutdown-at-keyon` was added 2026-09-26
  (decision 2).
- **No fixture from a whole multi-epoch file:** `20260403_194203_T2` has an
  epoch break at 176.44-184.22 s -- a 7.7 s total-bus dropout -- which
  replayed whole would inject a bus-loss the capture never contained. (That
  capture is a bench replay, not truck data, and its dropout is the
  replayer's; the rule stands for any capture. Corrected 2026-10-31, user.)
  Two of the three fixtures originally proposed were sourced from
  cross-epoch artifacts.
- **Status:** validated, but for the arm gate's torque sign, which the spec
  records as not yet resolved.

#### Five decisions the rules above did not settle
- **Basis:** all five were put to the user and approved.
- **1.** This is the one structural change and rule 2 is unimplementable
  without it: the worker's OFF branch in `gen_inhibit.c` never called
  `twai_receive()` -- it parked, delayed 50 ms and looped -- so a device
  that had aborted could not hear the key come back, ever. Before this, an
  abort stopped diag, and a latched device and a switched-off device were
  indistinguishable in a log -- both silent.
- **2.** The probe and the diag pages are IDs nothing on the truck consumes,
  and silencing the diag heartbeat at key-off would remove the telemetry
  exactly when the end-of-drive behaviour this rule exists for is happening.
- **3.** On the truck the cost is not a hypothesis -- `0x592` was present in
  1045 of 1045 powertrain epochs.
- **4.** This is what keeps the bus-loss latch meaningful: a key-off takes
  `0x592` with it, but so does a pulled connector, and if staleness counted
  as a key-off then every flapping link would clear the very latch that
  exists because it is flapping.
- **5.** The justification is that a key cycle is the reboot these latches
  were always documented to wait for -- section 7's own text says "cleared
  only by a reboot, which the relay dropping at truck sleep provides", and a
  re-key quick enough that the relay never opens is that event with the
  power never interrupted. Neither release survives on evidence either: M
  mode must be re-selected before `disable_monitor()` can see it again, and
  a low SoC re-latches after five valid `0x411` frames. Clearing them is a
  re-evaluation, not an override.
- **`fb_ever`:** the inverter powers up ~28 s after the bus does, so
  re-entering the gate with it still set would abort on
  `GI_ABORT_INVERTER_LOST` the instant the inhibit went live -- bug 2 of
  2026-09-19 reintroduced through a different door.
- **Status:** validated.

#### `0x592` cadence
The first replay built under 7.1 showed the transmit gate flapping, so the
signal's actual cadence was measured per (epoch, powertrain channel) --
`notes/artifacts/gen-inhibit/key_cadence.py`:

| | |
|---|---|
| `0x592` rate on every clean epoch | **9.97-9.99 Hz** |
| worst gap | **0.150 s** (`vtrux_20260323_220148_T0`) |
| gaps exceeding `cfg.fresh_us` (0.5 s) | **zero** |

Over three times inside the window. (The worst gap read 0.117 s until
2026-10-31, when the tester's re-measurement without the bench replays gave
0.150 s, the same under the old and the new bus resolver; user,
2026-10-31.)

The flapping was one capture, `vtrux_20260403_194203_T2`, where `0x592` reads
5.5 Hz and `0x051` reads 59.6 Hz instead of 100 -- both down by the same
fraction, so frames were being lost, not the truck changing its rate. It is
a bench replay, not truck data: the `:vtrux` app recording a desktop
replayer looping `generator-on-then-off.log`, and the frames were lost where
the replayer's transmit queue overflowed (corrected 2026-10-31, user; it
was read as a logger on the truck dropping frames).

- **Status:** validated.

<!-- Batch 3 of 5 ends here: sections 8-16 follow in later batches. -->
