# Generator inhibitor (WiCAN): specification

**Status:** the source of truth for the WiCAN generator inhibitor's
behaviour. Moved into git from
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md` on
2026-11-07 (user, review tracker C6-C6c), as a move only: the requirements
were not changed, and the measurements, readings and history behind them
moved to `VALIDATION.md`. The section numbers, and the items, rows and
trips within them, are those of the reverse-it spec, so every existing
citation still resolves.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside
  this file, keyed by the same section numbers.
- **Test evidence:** `VERIFICATION.md`, the tester's.
- **Open findings and rulings:** the review tracker,
  `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-review-2026-09-22.md`.

**What this document is for.** It is the translation layer between the human
and the agent sessions. The human reviews and edits *this*, not the code. An
agent session working on the inhibitor is doing one of two things:
implementing this spec, or auditing the implementation (`gen_inhibit.c`,
the shared stock WiCAN code at the version this project pins,
`shared/wican`, with its `ota_health.c` and config server, the shared CAN
port, `shared/can_port` and `shared/twai_port`, the hooks section 3.3 lists, the bench harnesses, the diag DBC) against it. Any session that finds code, a test, a note or a measurement
that contradicts this spec -- whether or not it is working on that area --
must fix it if the fix is unambiguous, and in every case bring it to the
user's attention. Changes to *behaviour* are made here first and then in the
code, never the other way round.

**This document never says a requirement is implemented.** Sections 1 through
12 state required behaviour in the present tense, and that is all they state.
A reader must not be able to infer from this document that any given
requirement is already in the firmware, because a session that reads
"implemented" will stop checking, and the whole point of an audit is to find
the requirement that quietly is not. Whether the code does what is written
here is answered by reading the code, never by reading this file.

The one permitted exception is the reverse: a requirement that is **known not
to be implemented yet** is called out as such, so nobody is misled into
thinking the truck is ready. Section 13 is where those live. When such an item
is implemented, its note is **deleted**, not replaced with a note saying it is
done.

The device: a MeatPi **WiCAN OBD** (ESP32-C3) on the Vtrux powertrain bus,
powered from a relay on the Via harness (section 2.1).
**It is deliberately not a permanent modification.** It is removable at any
time by unplugging it, with no other change to the truck, so the vehicle can
be handed to someone else carrying no modifications at all. Nothing in this
spec may assume the device is present, and the truck must behave normally
without it. Firmware is `wican-fw` v4.13 plus the components described here,
on branch `gen-inhibit/main`, based on upstream tag **v4.13**. Staying on
that base is a decision, not inertia.

## 1. Purpose

The truck's VCM starts the range-extender engine by commanding torque to the
GENE inverter on `0x051`. There is **no conventional starter motor** -- the
generator inverter is the only cranking path -- so overriding that command with
zero torque prevents the engine from starting at all.

The firmware must:

1. **Hold the inhibit whenever it is armed.** Every genuine VCM `0x051` gets an
   answer; a frame left unanswered is one the inverter acts on.
2. **Command nothing but zero torque.** Commanding a non-zero value must be
   structurally impossible, not merely discouraged.
3. **Stop inhibiting when the engine is legitimately wanted**, and otherwise
   perturb the bus as little as the job allows.
4. **Stay recoverable.** A firmware change must never be able to strand the
   device, and a device that has failed must be able to say so.

Goals 1-3 are the inhibit itself (sections 3-7). Goal 4 is why sections 8 and 9
exist at all.

## 2. Target, and why recovery dominates the design

Standard WiCAN OBD, not the Pro: ESP32-C3, 4 MB flash, `ota_0`/`ota_1` of
1740 K each.

`esptool` over USB reaches the C3 ROM download mode regardless of app state.
That, together with the device being unpluggable at any time, means a bricked
image is always recoverable by carrying the board to a bench:

| channel | status |
|---|---|
| USB serial | **the guaranteed net.** Independent of app state. |
| OTA bootloader rollback | a convenience on top, armed by section 9.1 |
| OTA upload | the working path for iteration (section 9.2) |

**Rollback still earns its place even though bricking is survivable.** What it
protects against is not an unrecoverable board but a *silent* one: a device
that boots, fails to bring up the channel that would report or replace it, and
sits on the truck appearing to work.

A USB-flashed image lands `VALID`, not `PENDING_VERIFY`, so the health gate's
rollback arm does not apply to it. That is intended.

**Discipline while iterating.** OTA only -- it cannot touch the
bootloader or partition table. The partition table stays byte-identical to
stock or the restore image stops matching. **Never burn eFuses:**
`CONFIG_SECURE_BOOT` and `CONFIG_SECURE_FLASH_ENC_ENABLED` are unset and
`CONFIG_EFUSE_VIRTUAL` is off, so an `espefuse` mistake is permanent and is the
only true brick.

Recovery inventory, in SeaDrive (`wican-config/` is the folder beside
reverse-it in the same library):

| Item | Location |
|---|---|
| Stock v4.13 OTA image | `wican-config/restore-v4.13/wican-fw_obd_v413.bin` (sha256 `f1f78c64...`) |
| Stock v4.13 full flash set | `...v413.zip` -- bootloader, partition table, ota_data, app |
| WiCAN full flash backup | `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/board-backups/` (sha256 `c6513d99...`) |
| CAN-X2 flash backup | same folder (sha256 `a6b3eb0f...`) |

### 2.1 Power on the truck

This is the one place the spec states how the device is powered. Other
sections refer here.

- **Supply.** 12 V on OBD pin 16, ground on pins 4-5, switched by a relay on
  the Via harness. The device sits in the exact physical location of the
  Bluetooth CAN dongle EPRI used to have.
- **The relay** is driven by an auxiliary power line that turns on when the
  truck is unlocked. So the device is powered while the truck is **awake** and
  unpowered while it is **asleep**.
- **USB is not connected on the truck.** 12 V is the only supply. With HV
  up, the DC-DC holds 12 V at about 14 V.
- **Consequences**, each stated where it applies:
  - every wake is a cold 12 V power-on, and an auto-arm build boots straight
    into INHIBIT (section 7, auto-arm);
  - the latching releases clear only on power loss or a key-on edge (sections
    6 and 7);
  - the device has no sleep logic of its own, because the relay does that job
    (section 8);
  - nothing may assume the device is present (preamble).

## 3. Modes and ownership of the bus

`gen_inhibit` runs its own task at priority 18 -- above `wican-fw`'s CAN tasks
at 5, below the WiFi driver at 23 -- doing a blocking `twai_receive()` woken
directly by the driver ISR. It does **not** use the stock `can_rx_task` path,
whose non-blocking drain plus `vTaskDelay(1 ms)` would inject up to 1 ms of
latency: most of the guard band the inhibitor has to fit inside. `can_rx_task`
stands down while `gen_inhibit_owns_bus()` says so (the shared stock code's
`owns_bus()`, section 3.3) rather than racing for frames.
It shares priority 18 with lwIP's TCP/IP task (`LWIP_TCPIP_TASK_PRIO`) on the
single core, so the two take turns when both are ready. That is left as is;
the cost is that HTTP can stall while the worker is busy.

| Mode | Value | Behaviour |
|---|---|---|
| `OFF` | 0 | idle; `wican-fw` owns the bus as usual |
| `OBSERVE` | 1 | timestamp `0x051` arrivals; controller in **listen-only** -- nothing at all on the wire, no ACK, no diag |
| `RESPOND` | 2 | also emit a `0x7F0` probe at a fixed offset after each `0x051`. Its layout is published in the schema doc, "`0x7F0`: the RESPOND timing probe" |
| `INHIBIT` | 3 | transmit the zero-torque `0x051` of section 4 on each RX |
| `PASSIVE` | 4 | run the complete INHIBIT decision path; transmit no `0x051`; broadcast diag reporting what INHIBIT would be doing (section 3.1) |

**RESPOND transmits the probe ID, never `0x051`.** It is a timing
probe, not a command: nothing on the truck consumes that ID, so a probe emitted
at the wrong instant cannot be mistaken for a torque command. `offset_us` is
rejected above 4000.

Any non-OFF mode owns the bus. Arming brings the bus up itself (`can_enable()`,
refusing to arm if the driver will not start).

**A mode change takes effect at once.** The request wakes the worker, so it
never finishes a sleep in its old mode while the driver is already receiving
into the queue.

**The worker makes every mode change.** The HTTP
setter and the boot auto-arm only post the request and wake the worker. The
worker applies it at the top of its loop, outside `twai_receive()` and between
frames. It does the driver teardown and install, the scheduler re-arm and the
core's mode, in that order, and returns success or the refusal reason. The
setter replies with that result. No other task writes the worker's state, and
there is no exception. A request not taken within 1 s of the setter's call is
withdrawn and refused with a reason, and never applied later. One the worker
has already taken is finished and its result reported. On a live bus
the change takes effect at the next frame. On a quiet bus it can take up to
the receive timeout (200 ms), because a wake cannot interrupt a blocked
receive. The worker's states and every transition between them are the
table in section 8.1; the only other caller allowed to take the bus is the
OTA handler, through the same mailbox (3.2, 8.1).

**No restart leaves the controller on the bus.** Every software restart, a
crash included, takes the CAN controller off the
bus before the CPU resets: the controller goes into reset mode and the
transceiver into standby, and both stay that way until the driver is
installed again. This is done at the point every software restart passes
through, after the last code that could skip it.

**OBSERVE is silent in hardware.** Arming OBSERVE puts the TWAI
controller into `TWAI_MODE_LISTEN_ONLY` itself, tearing down and re-installing
the driver if it is up in another mode, and refuses to arm if it cannot.
Listen-only means the WiCAN does not ACK, cannot emit error frames and cannot
transmit, so **no diag frames go out in OBSERVE** -- a capture that contains
`0x7F1`-`0x7F3` during OBSERVE means the tap was not passive. Leaving OBSERVE
restores the configured `can_mode`, and only undoes the forcing OBSERVE
applied. Transceiver standby does not serve as a silent tap.

### 3.1 PASSIVE -- a dry run of INHIBIT, with telemetry

PASSIVE watches the truck and still reports its reasoning on the wire. It is
also the mode for dry-running a new build on the truck before arming it and
for measuring behaviour on the loaded bus (section 12).

PASSIVE must:

1. **Run exactly the INHIBIT decision path** -- the section 7 arm gate, the
   section 6 releases, the 6.3 suppression, the section 7 trips and the 7.1
   key rules -- on the same inputs, with the same timing, producing the same
   state, the same latches and the same events. The only permitted difference
   is at the moment of transmission.
2. **Never transmit `0x051`.** At every point where INHIBIT would transmit an
   inhibit frame, PASSIVE counts a **would-transmit** instead and transmits
   nothing. This must be structural (the same property as section 1 goal 2),
   not a flag checked late. The count resets on every arm, as `tx_ok` does.
3. **Broadcast the section 10 diag pages**, reporting the state it *would* be
   in if it were transmitting: would-be-live (`inhibit_live`), the arm block,
   every latch and its reason, key state, and the would-transmit count. A log
   of a PASSIVE drive must read as a dry run of an INHIBIT drive, and a reader
   must be able to tell PASSIVE from INHIBIT, and a would-transmit from a real
   transmit, from the frames alone.

The trips that are about *our own* transmission have no PASSIVE counterpart
and are simply never reached: a failed transmit cannot happen when nothing is
sent. Everything else, including the error-frame rate trip, runs as in
INHIBIT.

PASSIVE is **not** silent: the controller is in normal mode, so it ACKs, can
emit error frames, and transmits diag. Use OBSERVE when nothing at all may
reach the wire.

### 3.2 No other transmitter

Section 1 goal 2 -- non-zero torque must be structurally impossible -- has to
hold for the **whole firmware**, not only for `gen_inhibit`. Stock
`wican-fw` keeps several client paths that can put arbitrary frames on the
bus and open or close it:

- the SLCAN command parser, reachable from **three** channels: the TCP port
  (when `protocol` is `slcan`, which is the device's configuration), UART, and
  the web UI **websocket, whatever the protocol setting** (the stock
  start-up's send task, `DEV_WIFI_WS`);
- the ELM327 command path, including `ATSP`, which calls `can_disable()` /
  `can_enable()`;
- the MQTT transmit path.

Required:

- **The SLCAN command parser is disabled on every channel.** No client can
  transmit a frame, open or close the bus, change silent/loopback mode or the
  bitrate through it. Receive-only monitoring may remain. In the shared
  stock code this is its SLCAN command switch, `WICAN_SLCAN_COMMANDS`
  (shared/wican W6), off in this product's build, so no call to the parser
  is compiled.
- **While `gen_inhibit` owns the bus (any non-OFF mode), no path other than
  `gen_inhibit` may transmit** -- `can_send()` refuses -- and no client path
  may change bus state.
- **In every mode, OFF included, only `gen_inhibit` and the OTA handler may
  change bus state**: bring the bus up or down,
  flush its receive queue, or change its settings. Every other caller is
  disabled. The RealDash, ELM327, AutoPID and MQTT protocols and MQTT's CAN
  logging and transmit are given up; none is used on this
  device's configuration (SLCAN, MQTT off). **They are turned off by build
  switch, one per feature**: `WICAN_FEATURE_MQTT`,
  `WICAN_FEATURE_ELM327`, `WICAN_FEATURE_AUTOPID` and
  `WICAN_FEATURE_REALDASH`, each tested in the stock code where its feature
  lives, on by default, and off in this product's build, where the feature's
  code is not compiled at all. They are the shared stock code's switches
  (shared/wican W7), on there, so another ESP project can turn them on. The OTA handler takes the bus
  only through the worker (8.1 rows 4 and 8). **Exempt:** the instrument-only
  abort probe (`twai_port`'s test app, its T13), which is never in a flight
  build, is refused by the integrity check, and refuses while armed.
- **The boot-time configuration stays, and arming comes after it.** The
  configured bitrate and `can_mode` that
  boot stores are what `gen_inhibit` installs with and restores on leaving
  OBSERVE (section 3). They change nothing while the bus is up and are not
  bus-state changes under the rule above. An auto-arm build arms only after
  they are applied.
- A refused attempt is counted, never silently dropped: the status page
  carries `refused_slcan` and `refused_can_send`, both since boot (section
  11).

### 3.3 Hooks into the shared stock code

The stock WiCAN code is a shared component, `shared/wican`, which this
project takes at a pinned tag. Every rule in 3.2, 5.1 item 3, 8.1 and 9.3
that the stock code carries out, it carries out through these hooks:

- **Start-up** (shared/wican W4): `wican_init()`, then `gen_inhibit_init()`
  with the configured bitrate and CAN mode, then `wican_start()`, so arming
  still comes after the boot-time configuration (3.2).
- **Bus-owner callbacks** (shared/wican W5):
  - `owns_bus()` is `gen_inhibit_owns_bus()`: true in every non-OFF mode;
  - `ota_begin()` is the worker's update request (8.1 rows 4 and 8, 9.3):
    go ahead from IDLE, refused while armed, no answer within 1 s;
  - `ota_failed()` returns the worker to IDLE (8.1 row 11);
  - `command_refused()` counts `refused_slcan` (3.2, section 11);
  - the refusal bodies are this project's.
- **Project URIs** (shared/wican W8): `/gen_inhibit` and `/gen_inhibit_set`
  at every server start; the abort probe in its instrument build; the
  restart-test URIs in the restart-test build, at the first start only. The
  extra handler slots are the restart-test URIs' count in that build, none
  otherwise.
- **Switches:** `WICAN_SLCAN_COMMANDS` and the four feature switches off
  (3.2).
- **The CAN port:** the stock code's CAN
  functions are `can_port`'s on port 0 (shared/wican W3), backed by
  `twai_port` (`shared/can_port`, `shared/twai_port`). gen-inhibit sets
  port 0 up with: the board's port 0; the configured bitrate; listen-only
  from the install for OBSERVE (3, 13 item 1) and normal otherwise;
  accept-all, forced by its install hook while it owns the bus (5.1 item
  2); an RX queue of at least 64 (5.1 item 1); no bus-off recovery (7);
  an install failure reported, not a restart; and its pre-teardown hook,
  the worker quiesce (8). When any step of its own bring-up fails (install,
  start or alerts), the arm or mode change is refused with the reason (8.1
  rows 2 and 5; 12.4) and the bus stays down until the next enable. None of
  these steps fails because of the bus; only memory exhaustion or a
  firmware bug fails them, and tests guard against the bug. The stock
  enable restarts on a failure, as it always has (`can_port` C3).

## 4. The inhibit frame

`0x51 VCM_GenControl_0051` is DLC 6; `gen_torque_cmd` is 16-bit LE at B1-B2
with offset -32768, so commanded zero torque is raw `0x8000` = bytes `00 80`.
`gen_rpm_ref` is 16-bit LE at B3-B4. B0 (state) and B5 (counter) are not in the
DBC. The DBC, `vtrux-powertrain-experimental.dbc`, is the source of truth for
the encoding.

Rebuilt from each received VCM frame:

| Byte | Value |
|---|---|
| DLC | 6 |
| B0 | **`0x08`**, the engine-off state -- held, not mirrored (see 4.1) |
| B1 | `0x00` |
| B2 | `0x80` (together: `gen_torque_cmd` = 0) |
| B3 | mirror VCM (`gen_rpm_ref` LO) |
| B4 | mirror VCM (`gen_rpm_ref` HI) |
| B5 | `(VCM_last_counter + 1) & 0x0F` -- steal the next counter |

**Stealing the next counter is the point.** A counter-validating GENE MCU then
sees the VCM's genuine same-counter frame as a duplicate and rejects it, so
every accepted torque command is our zero.

**Mirror B3/B4** (the RPM reference), and **hold B0 at `0x08`** (section
4.1). **Only zero torque is ever commanded**; the firmware has no
equivalent of the laptop tool's bench `--target`.

### 4.1 B0 is held at the engine-off state

B0 is **held at `0x08`** for the duration of the inhibit. B3/B4 are
mirrored. **`0x10` must never be transmitted:** its meaning is a shutdown
command and nothing here needs it.

Holding `0x08` is a change to the one configuration proven on the vehicle,
and two risks remain that no bench can close (`VALIDATION.md` 4.1).

## 5. Transmit strategy and timing

**Reactive trail.** Transmit immediately on each `0x051` RX, with
no forward prediction. This makes the arm
interlock structural: a reactive trail transmits only in response to a received
`0x051`, so it cannot transmit into a dead or unknown bus. No TX without a live
RX.

The deadline is the VCM's *next* frame, and the number that matters is the
**4.69 ms** minimum inter-arrival measured on the truck.

Decisions:

- **A plain high-priority task is sufficient**, because the worker's overhead
  is far inside the 4.69 ms guard band (section 12 measures it).
- **The IRAM interrupt option stays at the stock setting.**
  `CONFIG_TWAI_ISR_IN_IRAM` is not set.
- **Trail timing avoids the same-ID collision** a lead or simultaneous
  strategy would risk.

**Design case: normal truck operation.** The worst-case
preemption this section designs for, `WORST_PREEMPT_US` = 2.39 ms in the E4
model, is what the truck normally sees: no heavy WiFi traffic to the device
while it is inhibiting.

**~2386 frames/s is the rate the armed condition carries.**

#### The hazards 5.1 fixes

Receive loss behind the driver's default 5-deep RX queue, and transmit
ordering behind the controller's single TX buffer (`VALIDATION.md` 5).

Required: **nothing else the device transmits or receives may cause an inhibit
frame to be lost or to reach the wire after the VCM's next `0x051`.** That it
holds is a section 12 test on a full-replay bus.

#### 5.1 The fix

1. **Receive queue depth.** Whenever the TWAI driver is installed, its
   software RX queue holds **at least 64 frames** -- ~27 ms of truck traffic
   at ~2386 frames/s, about 11x the design-case worst preemption (2.39 ms).
   A deeper queue turns loss into lateness. Lateness past the VCM's next
   `0x051` is handled by section 7 trip 7, as a skip. The device detects it
   itself only when the receive queue is empty at that
   moment. Lateness caused by a receive backlog is exactly the case it can
   miss. Those verdicts are counted as "on time, unverified" (trip 7,
   section 11), and the bench witness is the check for them.
2. **No acceptance-filter narrowing.** The controller keeps accept-all.
   Revisit only together with a test proving every ID the core reads is
   delivered.
3. **No flash write while `gen_inhibit` owns the bus** (any non-OFF mode).
   **Every flash-writing path must be refused while owned**, the 9.3
   way (HTTP 403 naming the reason, and a log line); the implementation audit
   must confirm there are no others. Writes during boot, before the inhibitor
   can arm, are allowed. The WiFi driver's storage stays RAM-only
   (`esp_wifi_set_storage(WIFI_STORAGE_RAM)`, `wifi_network.c`), so it writes
   no NVS -- asserted, not assumed. The IRAM decision stands only with this
   refusal in place.
4. **Diag is scheduled around the inhibit, not by the clock.** While inhibit
   frames are being transmitted -- **mode INHIBIT**, live, key on, and not
   suppressed by 6.3 -- a diag page that falls due is queued only
   **immediately after an inhibit frame has been observed complete** -- the
   moment the VCM's next `0x051` is furthest away (>= 4.69 ms), giving the
   lowest-priority frame on the bus several ms to find an idle slot. At any
   other moment it waits for the next completion. When no inhibit frames are
   being transmitted -- not live, key off, PASSIVE, RESPOND -- diag keeps its
   normal cadence, so a device that has stopped transmitting still reports.
   **The device's record of the last completion is cleared whenever it stops
   being able to transmit** (disarm, a section 7 abort, or a change to a mode
   that does not transmit), so a device that has just gone live has no
   completion on record and takes the normal cadence until its first one. The
   existing rule that no diag is queued while an inhibit is outstanding
   (section 10) is kept.

   **Placement is not the safety mechanism.** **Section 5.2's transmit
   scheduler is the guarantee.** Placement right after a completion is kept
   as the scheduler's default slot for telemetry, because it makes an abort
   rarely necessary.
5. **Proof, as a margin and not as one clean scenario.** The E4 load model
   (section 12.3) must show, with this fix: no frame lost and none late at
   the truck rate with the measured worst preemption; `tx_behind` = 0 while
   live; and a **preemption sweep** reporting the shortest preemption that
   loses a frame, which must be **at least 3x the design-case worst
   preemption (>= 7.2 ms against 2.39 ms)**. The design case is normal truck
   operation, with no heavy WiFi to the device while inhibiting (section 5).
   The full-replay bench run and the direct backlog measurement (section
   12.4) then measure it on hardware.

#### 5.2 Transmit scheduler

**Requirements.**

1. **One transmit owner.** Every frame the device transmits goes through
   one scheduler: inhibit frames, the RESPOND probe, and telemetry (the diag
   pages of section 10 and any later logging). Nothing else transmits while
   the device owns the bus: the stock send is refused (3.2; can_port C4),
   and only `twai_port`'s send calls the TWAI transmit API (twai_port T6).
2. **Priority classes: inhibit > probe > telemetry.** Within a class, first
   in first out. Telemetry is a class, not a list of pages: a later logging
   feature adds frames to it through the same interface and inherits every
   guarantee below without touching the inhibit path.
3. **At most one frame handed to the driver at a time.** The driver's own
   software queue therefore never holds a telemetry frame ahead of an
   inhibit. The scheduler hands over the next frame only when the previous
   one has completed or been aborted.
4. **Preemption by abort, and recheck.** When an inhibit frame is due and
   the driver holds a telemetry frame, the scheduler aborts it with the
   controller's abort-transmission command, **then rechecks the buffer**
   until it is free. While the controller is not transmitting (TS = 0), it
   re-issues the abort; while it is transmitting (TS = 1), it waits. The
   loop is bounded at **1 ms**. The aborted telemetry
   frame is requeued at the head of the telemetry class. A frame that is
   really on the wire cannot be aborted (the controller finishes it), and the
   inhibit waits for it. **The guarantee: telemetry never delays an inhibit
   frame by more than one telemetry frame already on the wire** (~276 us at
   DLC 8, 500 kbit/s, worst-case stuffing).

   **If the bound is reached,** the inhibit frame stays queued at the head of
   its class and the scheduler keeps trying until the deadline in item 5.
5. **Completion accounting survives the abort.** The driver reports an
   aborted frame as a **successful transmission**: TX_SUCCESS with TX_IDLE,
   and msgs_to_tx falling to 0. So the scheduler's own
   record of which frame it aborted is the **only** way to tell an abort from
   a completion. It must never credit that completion to an inhibit frame.
   Order: abort and recheck (item 4) until the buffer is free and
   msgs_to_tx is 0, then hand over the inhibit. Section 7 trip 7 is judged on
   the inhibit's own completion only.

   **The inhibit's deadline is the VCM's next `0x051`.**
   An inhibit frame that is late but still ahead of the VCM's next command
   is sent: it still wins the counter race. Once the VCM's next `0x051` has
   been received, the old frame is too late. If it has not reached the wire
   by then, the scheduler **withdraws it** (the item 4 loop, if it is already
   in the buffer) and it becomes a **skipped frame**: it is never sent, it is
   counted, and the device answers the new command normally. For that one
   cycle the generator inverter acts on the VCM's real command. Skips are
   reported on the status page and in a diag page (section 10), and they
   feed trip 7 (3 within 1 s). A frame that went out late, or may have,
   counts as a skip too. It
   was either seen to complete only after the VCM's next `0x051` was
   dequeued, or it was still in the buffer at the deadline. The generator
   inverter has already acted on the VCM's command with that counter and
   ignores it. See section 7 trip 7, including what the device can and
   cannot see of lateness.

   **An answer withdrawn because the inhibit stopped being live is not a
   skip.** A section 6 release or a section 7 trip that
   lands while an answer is queued or still in the controller withdraws it
   on purpose; nothing failed, and it cannot feed trip 7. It is counted as a
   **stand-down** (item 6), never as a skip, whether or not it reached the
   wire. A skip means only that a live inhibit missed a cycle.
6. **Telemetry is never lost silently.** It may be delayed, aborted and
   requeued, or dropped when its queue is full. Every one of those is
   counted, per class: queued, sent, aborted, requeued, dropped, and the
   longest wait. The counts are on the status page (section 11), and not in
   a diag page (section 10). Placement of telemetry right after an inhibit
   completion (5.1 item 4) stays as the default slot; it is an optimisation
   that makes aborts rare, not the guarantee.

   **The counts must balance on every reading, per class and per arm:**

   `queued + carried_in = sent + dropped + withdrawn + depth + held`

   A frame that is aborted and requeued goes back into `depth`, so
   `aborted` and `requeued` are not terms. A refused frame stays queued.
   Three terms are new:

   - **`carried_in`**: a frame the controller was still holding when the
     device was re-armed. Re-arming keeps that frame, deliberately, because
     the hardware may really still hold it. The per-arm counts are cleared,
     so without this term its completion lands in the new arm with nothing
     queued against it.
   - **`held`**: 0 or 1, with its class. It is a frame handed to the driver
     whose departure has not yet been credited as sent. That covers a frame
     still in the controller, and also one that has left it but that the
     worker has not yet seen go, because completions are noticed only on a
     worker pass. Without it, a reading taken while a frame is in flight
     shows one frame short.
   - **`withdrawn`**: inhibit frames that the
     scheduler gives up on at the deadline and will never send. There are
     three routes: purged from the queue at the VCM's next `0x051`,
     withdrawn when the inhibit stops being live, and given up in the
     controller by the item 4 loop. The last route includes a frame that
     **may have reached the wire late**: once the loop is running, the
     device cannot tell a completion from an abort, so that frame is
     counted here and as a late skip, never as `sent`. So `withdrawn` does
     not mean "never on the wire". It is always 0 for the other classes.
     Every skip is either `withdrawn` or, from a full queue, `dropped`, and
     so is every **stand-down** (item 5), so
     `withdrawn + dropped = skipped + standdown` (inhibit class) **once the
     item 4 loop has finished**. `standdown` counts the answers
     withdrawn by the second route, from the queue or from the controller.
     **A stand-down withdrawal ends the device's wait for that answer's
     completion.** Nothing the device sends afterwards, diag pages included,
     may wait on it. The skip is counted at the deadline, but a frame
     given up in the controller is booked `withdrawn` only when the loop
     removes it. Until then it is still in `held`, which keeps the balance
     identity closed while this equality reads one short. A check of the
     equality must not be read mid-withdrawal.
     **A re-arm while a withdrawal is still running:** if a re-arm lands
     while the item 4 loop is still removing an
     inhibit the old arm gave up (as a skip or a stand-down), the new arm
     counts that answer as `standdown` at the re-arm, as it counts the
     carried frame in `carried_in`. Each arm's equality then holds on its
     own.

   **The longest hold and the longest wait both exclude a carried-in
   frame.** Its hold began in an earlier arm, so the new arm cannot know how
   long it really was. A carried-in frame that is aborted
   and requeued keeps its old-arm queue time, so the same applies to its
   next wait. It is counted in `carried_in` and nowhere
   else. It is not clamped to the re-arm time either, because that would
   be a measurement the device did not make.
7. **The section 10 rule is subsumed.** "No diag while an inhibit is
   outstanding" becomes a property of the scheduler: nothing is handed to
   the driver while an inhibit is outstanding.
8. **Architected for emulation.** The scheduler is platform-free logic,
   compiled unchanged on the host, driven by a virtual clock, behind a narrow
   hardware interface: *submit a frame*, *abort if awaiting arbitration*,
   *buffer state* (empty / awaiting arbitration / transmitting), and
   *completion events* (completed, aborted, failed). Everything that decides
   *what* goes next and *when* lives on the host-testable side. Only the
   calls into the TWAI driver and the abort register live on the device
   side.
9. **The hardware's behaviour is measured before it is modelled.** Before
   the scheduler is built on it, a bench test on the C3 establishes:
   - that an abort removes a frame awaiting arbitration;
   - that an abort during transmission is a no-op and the frame completes;
   - what the driver then reports (TX alerts, `msgs_to_tx`, its internal
     count);
   - how long the buffer takes to become free after an abort.

   The abort command is a private HAL call (`twai_ll_set_cmd_abort_tx`,
   below the public driver API; `twai_clear_transmit_queue` does not touch
   the buffer). It is issued by `twai_port`'s
   abort-if-awaiting (twai_port T8). Its interaction with the driver's
   interrupt handling is exactly what this test must pin. The emulator's model of the buffer and
   the abort is written from these measurements and checked against them.

   Two requirements on that model:
   - **It models the driver's reporting, not only the buffer.** An aborted
     frame is reported as transmission-complete, so the mock driver's model
     of an abort must reproduce its effect on the TX alert bits and on
     `msgs_to_tx` as measured, not just remove the frame from the queue.
     Otherwise the emulator cannot exercise item 5.
   - **It states what it is calibrated against.** Abort behaviour comes from
     the direct measurement above. Frame delays and arbitration waits taken
     from bench replays describe a bus burstier than the truck's (section
     12.2: batched corpus timestamps), so they are stress cases, not truck
     rates, and are labelled as such wherever the model uses them.

   **The emulator's model of an abort** (the mock driver and the E4 load
   model) must reproduce, from the bench C3's measurement (`VALIDATION.md`
   5.2 item 9):
   - awaiting arbitration: the frame is removed, TX_IDLE + TX_SUCCESS, and
     msgs_to_tx falls to 0;
   - transmitting: any of three outcomes:
     - the frame completes on the wire;
     - it is removed and reported as a success;
     - the abort has no effect and the frame keeps contending, with ARB_LOST
       only;
   - empty buffer: no alert.

   A model that knows only the documented no-op would pass cases the
   hardware fails.
10. **One case catalogue, two runners.** Each scheduler case is defined once
    -- stimulus and pass criteria -- and run first in emulation (the mock-HAL
    build and the E4 load model), then on the bench against hardware, with
    the same pass criteria read from the witness and the device. A case
    that exists only in emulation, or only on the bench, does not count
    toward acceptance. The minimum catalogue:
    - telemetry waiting in the buffer when a VCM command arrives: aborted,
      the inhibit on time, the telemetry requeued and later sent;
    - telemetry already transmitting when a VCM command arrives: not
      aborted, the inhibit delayed by at most that frame;
    - sustained bus saturation: no inhibit late, telemetry delayed and
      counted, never silently lost. **"No inhibit late" is judged as
      `ontime + ontime_unverified == queued`,** with the bench witness
      consulted for every unverified one: an unverified completion is one
      the device could not confirm against an empty receive queue, not a
      late frame, so `ontime == queued` is not the criterion;
    - telemetry queue overflow: drops counted;
    - an abort racing a completion: no double credit, no inhibit credited
      with a telemetry completion;
    - an abort that lands in our telemetry frame's arbitration field and has
      no effect (item 9): the item 4 loop frees the buffer within its bound,
      and the inhibit is on time;
    - the inhibit cannot get out before the VCM's next command: it is
      withdrawn, never reaches the wire late, and is counted as a skip; the
      next command is answered normally;
    - 3 skips within 1 s: trip 7; 2 skips within 1 s: no trip;
    - the inhibit is still in the transmit buffer at the deadline, or its
      completion is seen only after the next `0x051` is dequeued: counted as
      a skip, not an immediate trip (trip 7, as amended);
    - the full-replay long arm (section 12.4): no inhibit after the VCM's
      next command, `tx_queued_behind` 0, telemetry present throughout.
      **Both halves replay recorded truck traffic.**
      - **Emulation:** a real capture through the full shipped firmware,
        with every ID at its recorded timing: the core, the 5.2 scheduler
        and the device HAL over the mock driver. Use the same capture the
        bench long arm uses (`scottsvalley_armable_300s.log`).
      - **Telemetry "throughout" is judged per window,** never as a total.
      - **The whole capture runs:** all 300 s, 706,283 frames, in the
        default suite, not behind a flag. It is also the
        only emulation case that
        reaches the verified-on-time path: sparse synthetic traffic has every
        completion observed together with the next command.
      - The synthetic long run stays as an additional case.
      - **The emulation half is accepted when all of these hold:**
        - the full capture replays with every ID;
        - commands lost inside the mock: 0;
        - the verified-on-time path is reached in proportion comparable to
          the bench's (8,976 of 8,983 in the case 3 arm on `f977d81`);
        - every existing case still passes;
        - the latest mutation rounds still catch everything they caught.
      - **The replay reader delivers every frame of a timestamp burst** (the
        capture has bursts of up to 12 frames on one timestamp). The mock
        clock is not to be rebuilt.
      - **A blocked receive's 200 ms timeout must not push the replay late:** a
        backlog that then drains with no time passing produces skips the truck
        cannot.

    **Emulation replays recorded traffic for its stimulus, not for bus
    load.**
    - **What a replay gives:** real command timing, real arrival ordering,
      real arm and key transitions, and the receive-side pressure of dense
      traffic.
    - **What it does not give: any bus load.** The mock has no shared
      medium. It completes one of our frames a fixed air time after that
      frame reaches the head of our own transmit queue
      (`controller_advance()` in `fake_twai`), and a frame delivered to the
      receive path consumes nothing a transmit needs.
    - **Load enters emulation only as a calibrated dial** (the contention
      flag and air-time overrides), and the bench is what calibrates it.
    - **So a load-dependent criterion requires a bench run.** An emulation
      pass is not evidence for it, however faithful the stimulus.

<!-- Batch 2 of 5 ends here: sections 6-16 follow in later batches. -->
