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

## 6. Conditions that disable the inhibit

**Two** conditions release the inhibit, and both **latch until a key-on
(7.1) or a reboot** once fired. A third condition (6.3) merely suppresses transmission
while it is present and does not latch -- the distinction is that the first two
mean *the engine is legitimately wanted*, while the third means *the truck is
parked and already shut down*.

For the two latching conditions, the inhibit must never silently resume once it
has stood down -- not when the condition goes away, and not when the
measurement that caused it recovers. A key-off -> key-on transition clears
it (7.1 rule 2 and decision 5), and so does the reboot when the device loses
power as the truck sleeps (section 2.1). Nothing else clears it.

Every condition is evaluated on each received frame. For the latching pair the
transmit path is gated on the latched state, not the live one; for 6.3 it is
gated on the live state, which is the whole difference.

### 6.1 M mode engaged

In M mode the driver is manually commanding the engine (hill climbing), so
the engine is wanted.

Source: `0x639 VCM_ShiftPos`, signal `shift_lever_pos`, `B6 bits[6:4]`, DLC 8, on
the **PT bus** the WiCAN taps. `shift_lever_pos == 4` disables. The value map is
the DBC's: 0 Park, 1 Reverse, 2 Neutral, 3 Drive, 4 Manual generator mode.

**The firmware must not reference `0x1F5`.** It lives on the OBD/chassis
bus, never on the PT bus the WiCAN taps.

### 6.2 Battery SoC below 21 %

**The inhibit is released the moment `bms_soc_hires` goes below 21.00 %,
and the release latches.** Below that point the pack is close enough to needing
the generator that the truck's own judgement takes precedence over ours.

**The latch is unconditional on SoC recovering.** If SoC falls to 19 %, the
engine runs, and SoC climbs back to 22 %, the inhibit does **not** resume. Once
released for low SoC it stays released, exactly as the M-mode release does
(section 6.1), and clears only as section 6 describes: on a key-on (7.1)
or a reboot.

**21 % is deliberately a full percentage point above the truck's own 20.0 %
trigger**, so the inhibitor gets out of the way *before* the VCM asks for the
engine rather than at the same instant.

Source: `0x411 bms_soc_hires`, 14-bit, 0.01 %, ~20 Hz. **Use `0x411`, never
`0x596`, for the comparison**: `0x596 bms_soc` is a floor of the
high-resolution value, not a round, and is not used.

**`0x411` is wrong for about a second every time the BMS wakes**, so a wake
reading is a false low-SoC release waiting to happen.

**The rule: SoC is only evaluated after the main contactors have closed.**

- The device keeps a **SoC-valid marker**. It is **set** when `0x440`
  `bcm_mainc_stat` reads a closed state (11 `MAIN_PN_CLOSED_DRIVE` or 12
  `MAIN_P_CLOSED_CHARGE`, `epri-pt-bus.dbc`, decoded per the DBC). It is
  **cleared** whenever `0x411` or `0x440` stops being fresh (the BMS has gone
  away, so its next reading is a wake reading), on every key-on latch clear
  (7.1), and at boot.
- A `0x411` reading counts toward the low-SoC release only if it was received
  while the marker is set. Readings received while it is clear are ignored and
  do not count toward the debounce (and the debounce count restarts when the
  marker clears).
- The inhibit may not go live until the marker is set, at least one `0x411`
  has been received since it was set, **and `0x440` currently reads closed**
  (section 7 condition 2). The marker alone is not enough: it deliberately
  survives the contactors opening while the BMS stays awake, and a gate
  evaluated then (for example an arm during a key-off) must not go live with
  the contactors open.
- Once set, the marker is not cleared by the contactors merely opening while
  the BMS stays awake: no wake has happened, so the readings stay valid.

**Debounce is still required, and the latch makes it matter more.** Never
release on a single sub-threshold sample: the reading must persist for **5
consecutive valid `0x411` readings** (~0.25 s at ~20 Hz), and a
reading at or above the threshold restarts the count. A raw 0 never counts.

### 6.3 The VCM commands generator shutdown (`0x051` B0 = `0x10`)

**Do not transmit while B0 reads `0x10`, and resume when the VCM returns to an
operating state. This one does not latch.** It is a suppression, not a release,
and it is deliberately the odd one out in this section.

`0x10` is the VCM commanding the generator subsystem into shutdown. The VCU is
already asserting a shutdown of its own, so there is nothing to add and no
reason to hold the bus against it. It is a *parked* state, not a statement
that the engine is wanted, so latching would be wrong, and suppressing is
free. A non-latching suppression also handles `0x10` at key-on by simply
waiting, with no startup guard.

The inhibitor must **never transmit `0x10` itself** (section 4.1). Suppressing
on it is the opposite of asserting it.

### 6.4 Reporting

`/gen_inhibit` reports `disabled`, `disable_reason` and `soc_x100`; the same
facts go out on `0x7F1` (section 10).

**The 6.3 suppression belongs in the flags, not in `disable_reason`.** That
field names a latched release. The live state needs a flag bit instead, so
the two are distinguishable at a
glance: `diag_flags` bit4 `shutdown_suppressed` on `0x7F1`. The layout's
source of truth is `gen-inhibit/diag/wican_diag_schema.md` (section 10).

**A latched release is a stand-down, so the section 7 live flag must clear
with it.** The flag must
be cleared where the release latches, and `arm_block` must say the device is
disabled rather than reporting a stale gate reason.

**What the bench must demonstrate** for these conditions, and the shape the
harness takes
(`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/wican_disable_bench.py`):

- an SoC=0 startup sentinel, repeated, does **not** release, and transmission
  keeps climbing through it;
- a sustained SoC below 21.00 % latches `low_soc` and freezes transmission;
- **SoC recovering does not un-latch it**: drive SoC below 21 %, confirm the
  release, then take SoC back up to 22 % and confirm transmission stays frozen
  and `disable_reason` still reads `low_soc`. This is the case the latch exists
  for and it must be tested explicitly, not assumed;
- the boundary behaves: just above 21.00 % keeps inhibiting, just below
  releases;
- `shift_lever_pos` = 4 latches `m_mode`, stays latched through a return to
  Drive, and clears only on a key-on (7.1) or a reboot;
- **the shutdown suppression does not latch** (section 6.3): with B0 held at
  `0x10`, transmission stops; when B0 returns to `0x08`, transmission resumes
  and no latched reason is set. Run it twice in one power cycle, because the
  failure this guards against is a latch that survives into the next drive.
- **the live flag clears when a release latches**: with the inhibit live,
  drive `m_mode` (and separately `low_soc`) and confirm `inhibit_live` goes
  false, `diag_flags` bit5 clears, and `arm_block` reports the disable rather
  than a stale gate reason. Re-arming while latched must not set it again.
- **the BMS wake reading does not release** (6.2): a wake
  whose first `0x411` readings are below 21 % and which correct above it
  before the contactors close must not latch `low_soc`; a genuinely low SoC
  received after the close must. `0x411` before the close is ignored, and the
  inhibit never goes live before the close. `replay-rekey-long` is the
  real-traffic case: under this rule it must **not** latch `low_soc` at
  202.47 s, and its golden and the host README's description of it as a low
  pack change accordingly.

## 7. Arming

**Auto-arm is build-time.** `GEN_INHIBIT_AUTOARM_MODE` in
`gen_inhibit.c` defaults to `GEN_INHIBIT_OFF`: boot idle, arm by hand via
`POST /gen_inhibit_set`, which is the bench and truck-bring-up setting. For
deployment it is set to `GEN_INHIBIT_INHIBIT`: the device powers up at
truck wake (section 2.1), it boots and arms itself, with no human in the
loop. Auto-arm is safe at any setting because the trail is reactive.

**Arming logs loudly**, and mode 3 is distinct from OBSERVE/RESPOND so the
real-ID transmit is never reached by accident.

**Freshness** below means received within the signal's freshness window of
now, never a last-known value, and never-seen is never fresh. The window is
`fresh_us` (**0.5 s**) for every signal **except `0x617`, which gets 1.0 s**.

#### The arm gate -- every condition must hold for the inhibit to go live

1. `0x051` fresh.
2. **Main contactors closed**: `0x440` `bcm_mainc_stat` fresh and reading 11
   or 12, **and at least one `0x411` received since** (section 6.2).
3. `0x617` fresh, and B7 not `0xCA` (VCM fault active).
4. `0x639` fresh (so the M-mode release is evaluating a real reading).
5. Generator not running: if `0x054` is fresh, `GENE_RotSpd` below 300 rpm.
6. The VCM is not asking for the engine: `gen_rpm_ref` reads its engine-off
   null (< 0) and `gen_torque_cmd` is zero (7.1 records an unresolved
   refinement on the torque sign).
7. Not in the 6.3 shutdown suppression.
8. No section 6 release and no section 7 abort latched.
9. **The receive queue is drained**: the frame the worker has just taken was
   the last one waiting. A device that goes live
   with frames still queued answers commands the VCM has already passed.
   This condition is the backstop for a backlog from any cause at that
   moment, such as WiFi or lwIP preemption. It is checked only for going
   live. A backlog that builds once live is handled by trip 7 and the
   unverified count. A device that never drains, for
   example under heavy WiFi load, does not go live, and `arm_block` says why.

**The GENE family (`0x054`, `0x471`) is deliberately not required to be
present.** Taking over a *loaded* generator (the load-dump hazard) is
covered by condition 6.

**Condition 5 is load-bearing for resets.** Every latch is in RAM, and any
reset -- panic, brownout, config save, a
connector carrying both power and CAN -- clears them, after which the
auto-arm build re-arms on boot. That is acceptable **only because the gate
will not take over an engine that is already running.** Any change to the
gate must preserve that for a mid-drive reset with the engine turning,
including the arrival order where `0x051` is seen before `0x054`
(`arm-gate-order-cmd-first`), where conditions 5 and 6 together are what
hold.

#### The trips -- each ends a live inhibit and latches (cleared by a key-on, 7.1, or an explicit re-arm)

1. **VCM fault active**: `0x617` B7 = `0xCA`.
2. **Link loss**: `0x051` not fresh (below).
3. **Inverter lost**: `0x471` not fresh, **once it has been heard in this arm
   cycle**, and subject to the evidence rule below. "Stops" presupposes it was
   going; never-seen is not stopped.
4. **Engine turning**: `GENE_RotSpd` >= 300 rpm sustained for 0.3 s. It must
   be debounced, because a momentary crank blip is exactly what a *working*
   inhibit produces.
5. **Stale interlock signal.** Any
   of `0x411`, `0x440`, `0x617`, `0x639` not fresh, and `0x054` not fresh once
   heard in this arm cycle (the same once-heard rule as trip 3). `0x411` and
   `0x440` stop shortly after a key-off, so this trip fires at the end of
   every drive *after* the key
   has read off, and the next key-on clears it -- the same shape as trip 3.
   Tests must pin that ordering. Subject to the evidence rule below.
   **When the GENE family goes quiet together** -- `0x054` and `0x471` both
   stale -- it is reported as **trip 3, inverter lost**, not as a stale
   `0x054`. A stale
   `0x054` is reported as trip 5 only while `0x471` is still fresh.
6. **Error-frame rate** exceeded (below).
7. **Failed transmit.** A frame counts as transmitted only when
   the controller reports it **completed on the wire**. A frame that could
   not be queued, that the controller reports as failed, or that has not
   completed by the time the VCM's next `0x051` is received, is a failed
   transmit -- the last because a frame landing after the VCM's next one
   loses the counter race, so the VCM's torque is what the inverter acted
   on. It is unambiguously ours and distinct from a bus error frame.

   With the section 5.2 transmit scheduler, a
   frame that has not completed by the VCM's next `0x051` does not trip
   on its own:
   - **If it never reached the wire,** the scheduler withdraws it. It becomes
     a **skipped frame** (5.2 item 5): not sent, counted and reported.
     **3 skipped frames within 1 s trip 7.** A stuck transmit path therefore
     cannot pass for a working inhibit.
   - **A frame that went out late, or may have, is also a skip.** This
     covers two cases:
     - its completion was seen only after the VCM's next `0x051` had been
       dequeued;
     - it was still in the transmit buffer at the deadline, where the device
       cannot tell a late completion from a successful abort, because both
       are reported as TX_SUCCESS.

     The 3-in-1 s window still ends the inhibit if the transmitter is
     persistently late.
   - **A frame the controller reports as failed, or the driver refuses,**
     still trips at once.

   The completion test is the inhibit's own completion, never a TX_SUCCESS
   that may belong to an aborted telemetry frame (5.2 item 5).

   **What the device can see of lateness.** The
   device judges the deadline when its worker **dequeues** the VCM's next
   `0x051`, not when that frame arrives, because the driver gives no
   receive timestamp. So its own detection of a late frame is exact only
   when the receive queue is empty at the moment it sees the inhibit
   complete. With frames waiting, the next command may already have arrived,
   and a frame that was physically late is scored as on time. Such verdicts
   are counted separately as **on time, unverified**, with the largest
   backlog seen, on the status page (section 11). The bench witness, which
   sees both frames on the wire, is the check for them. The device never
   receives its own frames, so its counter check (`ctr_bad`) cannot see our
   transmit timing at all. Condition 9 of the gate means the device never
   goes live with a backlog, so this applies only to a backlog that builds
   once live.
8. **Short VCM frame.** A `0x051` received with DLC < 6 while
   live cannot be answered -- there is no counter to steal. It aborts.

Every trip names itself in the JSON and on the wire (section 10), and the
state must preserve the distinction between "the truck went away" and "we
broke it" (below).

**The evidence rule for trips 3 and 5.** A single
signal going stale counts as trip 3 or 5 **only if a `0x051` has been
received after that signal's freshness window expired** -- i.e. the bus was
demonstrably alive at the moment the missing signal should have arrived.
Without it, a whole-bus loss is reported as whichever signal's window expires
first rather than as link loss.
Consequences:

- **Whole-bus loss is reported as trip 2**, as intended: once `0x051` stops it
  can never arrive after another signal's expiry.
- **A single signal lost on a live bus still trips**, on the next `0x051`
  after its window expires -- at most one `0x051` gap (~10-47 ms) later.
  **That `0x051` may itself be answered; the inhibit stops from the next
  one.** One more answered command after a signal has been
  missing for its whole window (0.5 s, or 1.0 s for `0x617`) changes nothing
  the trip protects.
- **End of drive is unchanged**: the BMS stops while `0x051` continues, so
  trip 5 still fires after the key reads 0.
- No new tunable for the rule itself; it uses each signal's own window.

This rule is about **reporting, not safety**: every trip ends the inhibit
and latches identically. It matters because the abort reason goes on the
wire (section 10) and a mid-drive link loss -- a pulled or failing connector,
contactors closed -- must read as the link, not as one ECU. At the end of a
drive the contactors are already open when the bus stops, so there it makes
no difference.

**On this controller trip 7's controller-reported form does not occur.**
Inhibit frames go out with automatic
retransmission. The device's reader of that report stays, as defence in
depth, covered by the host
mock-HAL test. When the controller is driven to bus-off, the live inhibit is
ended by trip 6, or by trip 2 if the bus is lost first.

**A bus-off ends the device's part until a re-arm or a reboot.** The device
does not start bus-off recovery. A controller
that has gone bus-off stays off the bus, transmitting, receiving and
acknowledging nothing, so the device sees no key-on and does not re-arm
itself. The inhibit stays off until a re-arm or a reboot; on the truck, the
relay drop at truck sleep reboots it.

**Self-disarm on receive errors.** A run of 20 consecutive hard (non-timeout)
receive errors means
the driver has gone out from under the worker. The device sets mode OFF
rather than keep retrying at priority 18. This is a disarm, not a trip: in OFF
the worker no longer receives, so no key-on can clear it; recovery is a re-arm
or a reboot. Because OFF also stops the diag heartbeat, it must be **reported
in the JSON with its reason** so it is never confused with a disarm someone
asked for.

**Loss of the CAN link latches the inhibit off.**
`0x051` at ~100 Hz is the anchor: if it stops arriving for longer than the
freshness window *while the inhibit is live*, the device treats the powertrain
connection as lost and releases, latched until a key-on (7.1), an explicit
re-arm or a reboot, as every trip is. It is framed as **link
loss**, not as individual signals going offline.

**It latches deliberately**: it does not stand down and resume when the bus
returns.

**It applies only once live.** Before the inhibit goes live an absent bus is
"not ready", not a fault: the powertrain bus is legitimately silent until
key-on, and the GENE family for tens of seconds after it.

**This replaces the dead-man timer**, which is removed.

**Error-frame handling must be rate-based**, calibrated to the
running-generator regime.
**The threshold:** trip at **10 or more error frames within a 10 s window**.
**How the window moves:**
- **It slides.** The trip fires when any 10 error frames fall within 10 s of
  each other, wherever they fall in time (subject to the recency rule
  below), and the count is checked on every
  new error frame. A window of fixed 10 s blocks does not comply: it splits a
  burst that straddles a block edge.
- **Exactly 10.0 s counts**, in the times the device records (next bullet).
  The 1st and 10th error frames may be up to and including 10 s apart, as
  "within" reads. Trip 7's 1 s window is strict; this one is not.
- **An error frame's time is when the device records it.** The device reads
  its bus-error count once per worker
  pass (8.1: after each received frame, or after the 200 ms receive timeout)
  and gives each new error frame that pass's time. A recorded time therefore
  trails the error frame on the bus by up to the gap to the next pass. On a
  busy bus that is about one frame interval; on a quiet one it is up to
  200 ms. A burst whose 1st and 10th error frames are within 0.25 s of 10 s
  apart on the bus may or may not trip at its 10th error frame, and either
  result complies. A test that judges the 10 s edge takes the span from the
  device's recorded times. A test that judges from the bus keeps the span at
  least 0.25 s away from 10 s.
- **Only a key-on (7.1) clears the error history.** Arming, an explicit
  re-arm and going live do not. The error rate is a property of the bus, and
  a history cleared on re-arm would let repeated re-arms hide a real rate. A
  re-arm still clears the trip's latch, as for every trip; only the history
  carries over.
- **Error frames are recorded whenever the device is armed**, not only while
  the inhibit is live. The trip is also evaluated **before going live**. Each
  time the arm gate passes, trip 6 is checked first, and if a recent
  qualifying burst exists it latches there. The device never goes live, and
  no inhibit frame is sent.
- **Only a recent burst trips.** A qualifying burst (10
  error frames within 10 s of each other) trips only while its newest error
  frame is no more than 10 s before the live check. A burst that has expired
  in that sense causes no trip: neither one that expired before going live,
  nor one carried across a re-arm. After a trip 6 latch, a re-arm can
  therefore go live once the bus has been free of a qualifying burst for 10 s.
  The two tests are separate on purpose: whether the burst is tight enough
  compares error frames with each other, and whether it is recent enough
  compares the newest one with the check, so the inclusive 10.0 s edge above
  stays exact. A burst that arrives while live trips when its 10th error
  frame is recorded.
- **Checked before going live.** The latch, its reason and its clearing are
  unchanged by this. Trip 6 is the only
  trip this affects: the gate already covers trips 1-5, and trips 7 and 8 are
  judged per frame.

And **echo detection must not key on payload content** -- the inhibit frame is
byte-identical to the VCM's next genuine frame by design, so a payload-keyed
test discards genuine frames; distinguish by time, or suppress echoes in the
controller.

**The states must preserve one distinction the laptop tool lost.** It could not
tell "the inverter went away because the key was turned off" from "because we
broke it", and reported the alarming reading. Whatever replaces it on the
WiCAN should be designed so that distinction survives into the disarm reason.

### 7.1 Key state gates transmission, and a key-on clears every latch

**The two rules.**

1. **Transmit only while the key reads on.**
2. **A key-off -> key-on transition clears any latch**, and the device
   re-enters the normal arm gate on the way back in.

**The device stays armed through the key-off.** Every arm-gate condition is
satisfied during a key-off -- `0x051` is fresh (it
outlives the key), the generator is stopped, `gen_rpm_ref` reads its
engine-off null (raw `0x7FFF`, which decodes to -1 and so passes the `>= 0`
test), and torque is zero. So the device arms and sits **armed but silent**.
The key gates transmission, not arming. On an ordinary re-key there is
therefore no gate re-entry at all; re-entry happens only where a latch was
actually set.

That is what makes the user's case work -- key off, the generator inverter
sleeps, the key comes back before the VCU has fully fallen asleep and cut the
12 V, and the device must neither latch out nor stop inhibiting.

The arm gate also requires the main contactors closed and a post-close SoC
reading (section 7 condition 2), which a key-off does not satisfy. So a
device that is *already live* stays live and silent through a key-off, as
above; a device that is *not yet live*, or whose latch a key-on has just
cleared, waits in the gate for the contactors. At the end of a drive the BMS
goes quiet shortly after the key-off, which fires the section 7 stale-signal
trip (trip 5) after the key has already read 0; the next key-on clears it --
the same shape as the inverter-lost trip.

**Key state is `0x592` B0 bit 4** (`EPRI_HCU_Sensor_0592`, `IgnitionKeyState`).
Freshness applies: check it, do not carry a last-known value. No
transmit-gate clause of the form *"or `B0` = `0x0B`"* is added
(`VALIDATION.md` 7.1).

**The trip needs no key awareness of its own.** Key state gates transmission
and latch-clearing only. A key-off -> key-on transition cannot occur during a
key-ON inverter loss, so that latch correctly persists.
`replay-shutdown-at-keyon` is the real-traffic cover for the key-ON inverter
loss. The variant with the inverter dropping while HV stays up is covered
synthetically (`inverter-lost`), and no doctored capture is built for it.

**One consequence for the arm gate, not yet resolved.** `arm_gate_ok()`
refuses while `vcm_torque != 0` and while `vcm_rpm_ref >= 0`. But `B0` `0x0B`
spans two different states -- in one capture its torque ranges -1492 to +1708
-- and the **sign** separates them: positive is motoring, i.e. cranking a
stopped engine, which is exactly the case the inhibitor exists for; negative is
generating, i.e. a loaded spinning generator, which is the load-dump hazard the
interlock was written to prevent. The present test blocks both. This only bites
on the latch-clear path now that the device arms during key-off, but it should
be resolved rather than left.

**What must be replayed:**

| fixture | source | exercises |
|---|---|---|
| `replay-rekey-short` | `vtrux_crankover_bug_investigation` (2026-05-03), 0-240 s, its powertrain channel | three key-cycles, the key off for 23.5, 8.5 and 16.1 s, with the generator inverter (`0x054`) on the bus, so its silence at a key-off can count. Rules 1 and 2, repeated, so a latch shows on cycle 2. |
| `replay-rekey-long` | `vtrux_20260513_174225_T4`, single epoch | the longest **confirmed** silence, 35.43 s |
| `replay-inverter-lost-keyon` | `vtrux_20260714_112312_T2`, 20.5-28.2 s, single epoch | **must never go live**: the window starts after the contactors opened at 20.1 s, so HV is down throughout |
| `replay-shutdown-at-keyon` | `vtrux_20260714_112312_T2`, 0-225 s | **the key-ON inverter loss on real traffic**: live at 16.2 s, inverter lost at 21.0 s latched, cleared by the key cycle at 49.1 s |

**Do not build a fixture from a whole multi-epoch file**: replayed whole, an
epoch break injects a bus-loss the capture never contained.
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/bus_epochs.py`
reports the epoch structure of any capture and should be run before a
capture is used as a fixture.

A fixture names both its time window and its powertrain channel;
`from_capture.py --channel` enforces it.

#### Five decisions the rules above did not settle

**1. A section 7 abort latches in place instead of setting `mode = GI_OFF`.**
`inhibit_abort()` sets `abort_latched` and leaves
the mode alone. Nothing is less latched for it: the gate does not run while
the flag is set, `inhibit_live` stays false, and the transmit dispatch tests
both. The latch has the same two exits as the section 6 one it now sits beside
-- an explicit re-arm, or a key-on. A latched device **says so on the wire**
for as long as it stays latched: `gi_tick()` emits the diag heartbeat
whenever the mode is not OFF. **`diag_mode` therefore does not drop to 0 on
an abort**; read `diag_flags` bit7 instead.

**2. The key gates the real `0x051` only.** Not the `0x7F0` timing probe, not
the `0x7F1`-`0x7F3` diag pages.

**3. Never-seen reads key-OFF.** The literal rule, and the safe direction, but
it has a cost worth stating: a bus with no `0x592` produces a device that
arms, reports `inhibit_live`, and transmits nothing, **with no error
anywhere**. That is exactly what a partial bench harness presents.

**4. The edge runs on readings, not on freshness.** Only an actual 0 followed
by an actual 1 clears anything. A stale key gates transmission but clears no
latch.

**5. "Any latch" includes section 6.** M mode and low SoC clear on a key-on
too.

One thing that is **not** a decision but a requirement: the key-on clear
resets `fb_ever`.

##### `0x592` cadence: the 0.5 s freshness window is right, unchanged

**No new tunable, and `fresh_us` is not split.** `vtrux_20260403_194203_T2`,
a bench replay, is kept as the suite's frame-loss fixture, labelled as a
bench replay.

##### Reported fields

`diag_flags` **bit6 `key_on`** is the effective gate (value and freshness
together) and **bit7 `abort_latched`** says a section 7 trip is latched.
`gen-inhibit/diag/wican_diag_schema.md` is the source of truth;
`gen-inhibit/diag/vtrux-wican-diag.dbc` is in sync (section 10).

The `/gen_inhibit` JSON gains `abort_latched`, `key_on` and `key_fresh`. The
last two are reported apart deliberately: "the key is off" and "we have not
heard `0x592`" gate transmission identically and mean entirely different
things.

## 8. Concurrency: the bus can be torn down under the worker

The worker spends its life blocked in a receive. The port's teardown
uninstalls the driver, which frees the structures that blocked call sits on --
a use-after-free if it runs from another task. gen_inhibit's own disarm is
safe (only the worker tears the driver down, from its top-of-loop safe point),
but other subsystems call the stock `can_disable()` from their own tasks
knowing nothing about gen_inhibit.

**The callers.** Section 3.2 disables every caller
but `gen_inhibit` and the OTA handler, so the OTA handler is the only other
task that takes the bus, and it does so through the worker (8.1). The hook
below stays as a backstop for a caller that was missed.

**The quiesce handshake (backstop).** Every teardown of port 0 calls
`gen_inhibit_quiesce()` first, as gen-inhibit's pre-teardown hook (twai_port
T2 and T3; can_port C5). It posts a request to the worker's mailbox
(section 3, "The worker makes every mode change"), which the worker applies
itself (8.1 rows 9 and 13), and blocks until
the worker publishes that it is parked outside `twai_receive()` -- bounded by
the 200 ms receive timeout, waiting up to 1 s -- and only then does the caller
tear down. It is a no-op when called from the worker itself or when the worker
never started. Re-arming clears the quiesce and brings the bus back up.
**A quiesce that forces mode OFF must be reported** (JSON reason, log line).

**The bench must exercise this**, on the build that will fly: with traffic
flowing and the device armed in INHIBIT (and separately in PASSIVE), switch it
to OBSERVE and back through the HTTP setter, whose driver re-install the
worker makes itself (8.1 row 5). Required outcome:
no panic, the requested mode actually in effect afterwards (listen-only for
OBSERVE, proven per section 12), and a clean re-arm. The host mock-HAL
harness (section 12.3) must also drive a `can_disable()` from a second
thread against a blocked receive.

### 8.1 The worker state machine

**States.** IDLE: mode OFF, the driver is not `gen_inhibit`'s, and the worker
is outside it. ARMED(mode): OBSERVE, RESPOND, INHIBIT or PASSIVE; the driver
is installed by the worker and the worker receives. UPDATING: an OTA upload
holds the bus. The status page carries the state, why the worker last
switched itself off, and the violation count (row 13).

**Update point.** The top of each worker pass, and nowhere else: after each
received frame, after the 200 ms receive timeout, or on a wake while not
receiving. Other tasks only post requests (section 3: a mode request not
taken within 1 s of the call is refused and never applied). If more than one
is waiting at an update point they are taken in this order: a teardown by
another caller; an OTA starting or ending; a mode request; the core
switching itself off.

| # | From | Event | Exit actions | Entry actions / reply | To |
|---|---|---|---|---|---|
| 1 | IDLE | mode request, not OFF; install succeeds | -- | install the driver (listen-only for OBSERVE), check the controller is running, set its alerts, re-arm the scheduler, set the core's mode; reply OK | ARMED(m) |
| 2 | IDLE | mode request, not OFF; install fails | -- | reply with the refusal reason | IDLE |
| 3 | IDLE | mode request OFF | -- | reply OK | IDLE |
| 4 | IDLE | the OTA handler posts an update request | -- | confirm the worker is outside the driver; the handler then tears down | UPDATING |
| 5 | ARMED(m) | mode request, another non-OFF mode | withdraw pending answers (stand-downs, 5.2 item 5); reinstall the driver only if the new mode needs other driver settings (OBSERVE listen-only, the filter) | re-arm the scheduler, set the core's mode; reply OK. If the reinstall fails, reply with the reason and go to IDLE, because the old driver is already gone | ARMED(m2) |
| 6 | ARMED(m) | mode request OFF | withdraw pending answers; release the bus | reply OK | IDLE |
| 7 | ARMED(m) | the core switches itself off (receive errors, section 7) | withdraw pending answers; release the bus | report why | IDLE |
| 8 | ARMED(m) | the OTA handler posts an update request | -- | refused; the handler replies 403 (9.3) with nothing touched | ARMED(m) |
| 9 | ARMED(m) | **backstop**: another caller tears the bus down | withdraw pending answers; mode OFF | report the forced quiesce (JSON reason and log line), count a violation; confirm the worker is outside the driver before the caller goes on | IDLE |
| 10 | UPDATING | the upload succeeds | -- | restart; the rollback gate (9.1) takes over | (restart) |
| 11 | UPDATING | the upload fails: every error exit of the handler signals it | -- | nothing; the bus is already down | IDLE |
| 12 | UPDATING | mode request | -- | refused, "bus is down for a firmware update". Not expected: the web server runs one request at a time and the boot auto-arm cannot coincide with an upload | UPDATING |
| 13 | IDLE or UPDATING | another caller tears the bus down | -- | count a violation and log it, only if the caller is not the OTA handler and the bus was up; nothing is forced | unchanged |

**Not state changes.** Trips and latches (sections 6 and 7) end a live inhibit
inside ARMED; the worker stays ARMED until a mode request or row 7.

**The sleep state machine is removed.** Nothing is wanted from it here --
the relay is its power management (section 2.1). `can_disable()`,
`wifi_network_deinit()` and `esp_light_sleep_start()` are excised from it;
the task only samples battery voltage for `/check_status`.

`wifi_mode` stays `AP`, permanently.

## 9. Firmware integrity

### 9.1 The rollback gate

A freshly written image boots in `ESP_OTA_IMG_PENDING_VERIFY` and must call
`esp_ota_mark_app_valid_cancel_rollback()` to be kept
(`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`).

**The `ota_health` component defers the decision** until every
subsystem needed to *replace* the image has reported in, which is WiFi and the
HTTP server and only those:

| Bit | Subsystem | Reported from |
|---|---|---|
| `OTA_HEALTH_WIFI_AP` | SoftAP started | `wifi_network.c` (shared stock code) |
| `OTA_HEALTH_HTTPD` | OTA endpoint registered | `config_server.c`, both `httpd_start` sites (shared stock code) |
| `OTA_HEALTH_CAN` | reported for the boot log only, **not gated** | |

Gate outcomes:

- all gated bits + `PENDING_VERIFY` -> `esp_ota_mark_app_valid_cancel_rollback()`
- timeout + `PENDING_VERIFY` -> `esp_ota_mark_app_invalid_rollback_and_reboot()`
- timeout + already `VALID` -> **stay up degraded, do not reboot**

The third branch is required: with no rollback target, rebooting yields a boot
loop harder to recover from than a degraded but reachable device. Reports
arriving before the gate is armed must be buffered rather than dropped, so the
call order in `app_main` stays safe after someone reorders it.

Whether CAN works is judged after boot by the thing that uses it.

**How the gate is tested, and the rule that makes the test safe.** The
mechanism is exercised on a board with USB and a console rather than on the
WiCAN, using a two-slot app whose partition table copies the WiCAN's slot
sizes (`$REVERSE_IT_DIR/firmware/rollback_bench/`), and a build with a deliberately suppressed
subsystem. Required outcome: the faulty image diagnoses which bit is missing,
rolls back unattended, and the board comes up on the healthy slot as `VALID`.
It is also rehearsed on the WiCAN itself, which is a bring-up precondition.

**The injected fault must never be the recovery channel.** A board whose HTTP
server is suppressed is stranded if rollback fails; a board whose worker is
suppressed stays reflashable. On the WiCAN the injected fault masks a gated
subsystem's report, never the subsystem itself, so the recovery channel
stays up.

**No watchdog will rescue a hung app, so the gate cannot lean on one.**
The bootloader's 9 s RTC watchdog is *turned off immediately before the
user's main function*, because `CONFIG_BOOTLOADER_WDT_DISABLE_IN_USER_CODE`
is not set, so it covers boot only; and the task watchdog has
`CONFIG_ESP_TASK_WDT_PANIC` unset, so on timeout it prints a warning and a
backtrace and **continues running**. Changing either setting is a decision,
not a given: a watchdog that resets a transmitter attached to a live
powertrain bus has its own hazards.

### 9.2 The OTA upload path

OTA is a plain `multipart/form-data` POST of the image to the stock endpoint,
field name `ota_file`, as the device's own web UI form declares it. No paced or
otherwise special-cased uploader is required or wanted; a plain
`curl -F ota_file=@<image>` is the interface.

Three properties are required of whatever handler is resident:

1. **A read can never write past the end of the receive buffer.** The read
   target is a fixed buffer start, never a pointer that has been advanced past
   the multipart preamble.
2. **The image is written exactly once, in order, with nothing dropped or
   duplicated.** The write pointer is reset every iteration, not left parked
   at an offset from the first chunk.
3. **Timeout retries are bounded**, so a vanished client aborts the upload
   rather than pinning the single HTTP worker. The device's HTTP server is a
   recovery channel, and an upload that hangs it costs a power cycle.

**The handler is patched and stays patched.**

### 9.3 OTA is refused while armed

The handler returns **403 if `gen_inhibit_owns_bus()`**. The
worker makes that decision (8.1 row 8): the handler posts an update request
and replies 403 when the worker refuses it, with nothing touched. (OTA is one of
several flash writers; section 5.1 item 3 extends the same refusal to every
path that writes flash while the bus is owned.)
Reflashing under an armed transmitter is never wanted, and `can_disable()` at
the top of the handler would otherwise tear the TWAI driver down under the
worker (section 8). It also excludes the one case where an IRAM-resident ISR
would have mattered: a flash write with the instruction cache off.

### 9.4 Image budget

The image must leave enough headroom in its OTA slot that adding to the
inhibitor never forces a choice between a feature and the ability to flash.

BLE is not used by this device. Its removal is held until
after the shared-code port, and until then the build keeps
`CONFIG_BT_ENABLED=y`.

**Any strip is followed by the full regression set** before it is kept: a clean
boot, the health gate reaching its decision, the inhibitor bench, and an
end-to-end OTA. A strip that frees space and breaks the recovery channel is a
net loss.

## 10. Diagnostics

The WiCAN broadcasts its own state so any Android or laptop log of a run is
self-documenting: which build ran, what mode, whether and why the inhibit was
disabled, how much it transmitted. Five plain fixed-ID frames, no
multiplexing, a hand-maintained schema rather than a generator.

**Source of truth for the layout is `gen-inhibit/diag/wican_diag_schema.md`**
on `gen-inhibit/main` in the firmware repo; the decode DBC is
`gen-inhibit/diag/vtrux-wican-diag.dbc` beside it. This section states the
contract, not the byte offsets.

Five standard 11-bit IDs, DLC 8, round-robin at ~1 Hz each while armed:

| ID | name | carries |
|---|---|---|
| `0x7F1` | STATUS | schema_ver, fw_ver, mode, flags (disabled / bus_on (controller running, section 11; reads 1 on the wire in practice, since a controller that is not running sends nothing) / bus_ours / autoarm / shutdown_suppressed / inhibit_live / key_on / abort_latched -- bit layout in the schema doc), disable_reason, shift_pos, soc |
| `0x7F2` | COUNTERS | tx_ok (u32), tx_fail, ctr_bad, rx_errors |
| `0x7F3` | BUILD | build_id (the first four bytes of the image hash), fw_ver, schema_ver |
| `0x7F8` | STATUS2 (schema 4) | abort reason, arm block, flags2 (SoC-valid marker, tx pending, ...), would-transmit count, queued-behind count -- layout in the schema doc |
| `0x7F9` | SKIPS (schema 5) | skipped inhibit frames since arming, withdrawn and late (below) -- layout in the schema doc |

Five pages since schema 5 (the skips page, `0x7F9`, below). The pages go
round-robin at **240 ms**, so each page still repeats about every 1.2 s.
**Every page's repeat interval stays at ~1.2 s
whenever a page is added:** the round-robin step is 1.2 s divided by the
number of pages. A trip reason which clears quickly
must still get one turn on `0x7F8` before it clears, because the PASSIVE
dry run reads the reason from the log.

**Diag pages are telemetry-class frames of the section 5.2 transmit
scheduler.** They are sent all the time, including while
inhibiting, and can never delay an inhibit frame beyond 5.2's bound.

**Skipped inhibit frames get a diag page of their own** (5.2 item 5). It
carries the count since arming, split
into withdrawn and late. The page is allocated from `0x7F9` upward, with a
`diag_schema_ver` bump and the DBC in the same change.

**The rest of the scheduler's counters are on the status page only:** the
per-class telemetry counts, the balance terms of
5.2 item 6, the on-time counts and the abort-loop figures. They check the
scheduler on the bench. On the truck, lost or delayed telemetry already
shows in the CAN log as a gap in the diag pages themselves.

**`0x7F4`-`0x7F7` belong to the charge interposer**, which
transmits them on the same powertrain bus; the allocation table in the schema
doc is the authority for the `0x7Fx` range, and new WiCAN pages take the next
free ID from `0x7F9` upward. **That nothing on the truck transmits in
`0x7F0`-`0x7FF` is measured, not assumed** (`VALIDATION.md` 10).

**The pages also carry**, with the layout in the schema doc:

- `diag_mode` **4 = PASSIVE**.
- A **would-transmit** count for PASSIVE (section 3.1). A reader must be able
  to tell a PASSIVE would-transmit from an INHIBIT transmit from the frames
  alone -- either a separate field, or `tx_ok` redefined per mode with the
  mode alongside it; the schema doc decides and says which.
- **The abort reason on the wire.** `diag_flags` bit7 says *that* a section 7
  trip latched; with trips now numbering eight (section 7) the log must say
  *which*. The PASSIVE dry run depends on it.
- The SoC-valid marker of section 6.2, so a log shows when SoC started
  counting.

**Diag must not delay an inhibit frame** (section 5). The diag
pages are the lowest-priority frames on the bus and share the controller's
single TX buffer with the inhibit frame; scheduling them is part of meeting
section 5's requirement, and the full-replay test (section 12) measures it.

`0x7F0` is the RESPOND probe (section 3) and is not a diagnostic frame.
`0x7F1`-`0x7F3`, `0x7F8` and `0x7F9` sit in the 0x7Fx range nothing on the
truck consumes.

**The on-wire `diag_schema_ver` is the decoder's guard**: a decoder holding a
different DBC version flags the mismatch rather than trusting the fields. Bump
it on any layout change and update the DBC and the schema doc together. The
firmware, the DBC and the schema doc are hand-kept in sync.

**`0x7F3` names the binary.** `diag_build_id` is the first four bytes of the
running image's
image hash (shared/tools R1: the ELF SHA-256 ESP-IDF embeds in the app
descriptor), read from the descriptor at run time. It is packed as the
interposer's `0x7F7` build id, so both projects' diag pages carry it the same
way: a u32 with the hash's first byte most significant, sent little-endian, so
the DBC decodes it as an unsigned 32-bit value that, printed as eight hex
digits, reads as the hash's first four bytes in order. A build id is looked up
in the release build records and the bench run records, which name each image
by its full hash: a prefix that matches no record is unknown, and one that
matches two is ambiguous, never guessed. The build id only names one binary:
builds are compared by code hash (shared/tools R2, R3). `diag_fw_ver` is a behaviour/capability revision, with
its bump rule and table in the schema doc. It stays, by hand, until stage (b)
of the port retires it in the one diag layout both projects share.

Broadcast is best-effort and fails silently in listen-only, so a truly passive
tap emits none.

## 11. Control surface

```
GET  /gen_inhibit                      statistics and state as JSON
POST /gen_inhibit_set?mode=N&offset=N  0 off, 1 observe, 2 respond, 3 inhibit, 4 passive
```

The JSON carries the would-transmit count (PASSIVE), the SoC-valid
marker (6.2), the reason for any mode-OFF the device imposed on itself
(receive-error self-disarm, forced quiesce -- sections 7 and 8), and the
freshness of each interlock signal. Error responses from the setter must name
the actual failure (a bus that would not come up is not "bad offset").

The setter is **POST so a browser prefetch cannot start the device
transmitting**. `/gen_inhibit` reports `bus_on`, `bus_ours`,
`rx_errors`, `tx_ok`, `tx_fail`, `ctr_bad`, `disabled`, `disable_reason` and
`soc_x100`, so bus state is visible rather than inferred, and the counters
confirm the device is actually seeing traffic.

Two histograms are reported: `rx_gap` (`0x051` inter-arrival as the ESP32 sees
it) and `response` (RX to probe TX), with bucket edges
50/100/200/400/800/1600/3200/6400/12800 us. The B5 rolling counter is tracked
as a cheap check that no frames are being dropped.

**Every received `0x051` updates the counter check and `rx_gap`, including
the one that triggers a section 7 abort.** The abort is still attributed
to the frame that triggered it. `ctr_bad` counts real missed counter steps
only.

**The TWAI driver's own counters are reported**,
as a `drv` object: `rx_missed` (a frame dropped because the driver's
software receive queue was full), `rx_overrun` (dropped because the
controller's hardware receive FIFO overran), `arb_lost`, `bus_error`,
`tx_failed`, and the current `msgs_to_rx` and `msgs_to_tx`, read from
`twai_get_status_info()` once per page request, never per received frame.
They are cumulative since the driver was installed, so per-run figures come
from differencing. `drv` is `null` while the driver is not installed. Together
with `ctr_bad` they separate a real receive loss, and which buffer lost it,
from a counting artifact.

**The controller's own error state is reported too.** The `drv` object also
carries `state` (the `twai_state_t`, as a
string: `stopped`, `running`, `bus_off`, `recovering`), `tec` (transmit
error counter) and `rec` (receive error counter), from the same
`twai_get_status_info()` read. Nothing else on the page stands in for
them: `bus_on` (below) says only whether the controller is running, and
`bus_error` counts error *events* without saying whether any of them moved
the controller towards bus-off. Bus-off keys on TEC, which
rises only on transmit errors, so `bus_error` alone cannot distinguish a
device being driven towards bus-off from one merely receiving a corrupted
bus. Unlike the cumulative counters beside them these three are
**instantaneous**, so they are read directly and never differenced across a
run.

**`bus_on` reports whether the controller is on the bus**, not whether the
driver is installed.
- It is true only when `twai_get_status_info()` reports
  `TWAI_STATE_RUNNING`. It is false when the controller is stopped, bus-off
  or recovering, and false when the driver is not installed.
- "Driver installed" stays readable as `drv` being non-null, so nothing is
  lost.
- The `bus_on` flag bit on `0x7F1` (section 10) has the same meaning.
- No driver read is added for it: the page uses the read made when it is
  built, and the `0x7F1` bit uses the per-loop read section 7's error-rate
  trip already makes. A per-dequeue read of the driver status perturbed
  transmit completion.
- Only the reported value changes. Mode switching still keys on the driver
  being installed.

**Transmit scheduler counters** (section 5.2). The
status page carries the scheduler's per-class counters from 5.2 item 6. It
also carries **skipped inhibit frames**: the count since arming, and the
count in the current 1 s trip-7 window (5.2 item 5, section 7 trip 7). It
also carries the on-time counts, **verified** (the receive queue was empty)
and **unverified** (frames were waiting), with the largest backlog seen for
an unverified one (trip 7, "What the device can see of lateness"). They
are reported separately and never summed. **All of these are on the status
page, one page for everything.** This includes the
balance terms `carried_in`, `held` and `withdrawn`, and the stand-down count
`standdown` (5.2 items 5 and 6). **Only the skip counts
also go on the CAN bus**, in their own diag page (section 10).

In RESPOND the on-time counts and the largest unverified backlog are booked
for **probe** completions, against the same deadline (the VCM's next
`0x051`), so RESPOND reports the receive backlog behind its timing figures.
In every other
mode they count inhibit completions only, and in OBSERVE and PASSIVE they read
0.

**Uptime and reset reason are reported.**
- `/gen_inhibit` carries `uptime_ms`: the time since this boot as a whole
  number of milliseconds, from `esp_timer_get_time()`. It only rises while the
  device stays up and starts again from zero after any reset.
  - So a reading lower than the previous one is a reboot, seen directly.
  - That needs no request-stall threshold and no USB console.
- It also carries `reset_reason`: the cause of the reset that started this
  boot, from `esp_reset_reason()`, read once at boot.
  - It is reported as the enum name without its `ESP_RST_` prefix (`POWERON`,
    `PANIC`, `INT_WDT`, `BROWNOUT`, and so on).
  - A value the firmware does not recognise is reported as its number, as a
    string (`"17"`), so the field is always a string.
- Both are on the status page only. Neither goes on the CAN bus, so no diag
  page changes and neither `diag_schema_ver` nor `diag_fw_ver` is bumped.
- Both are part of the page's one snapshot (below).

**A reboot is confirmed by uptime, not by the reply.** `/system_reboot`
replies before it reboots and only schedules the
reboot, so the reply says nothing about whether the device restarted. A reboot
is confirmed by `uptime_ms` falling and `reset_reason` reading a software
restart (`SW`).

**The page names the binary it runs.**
`/gen_inhibit` carries `elf_sha256`, the SHA-256 that ESP-IDF embeds in the
running image's app descriptor, in hex. It is on the page only, not on CAN.

**Trip 6's error history is reported.**
`/gen_inhibit` carries:
- `err_n`: the number of error frames recorded in the 10 s before the reading.
  `err_n` counts at most 16, the depth of the device's error history, so 16
  means 16 or more;
- `err_age_ms`: the time since the newest recorded error frame, or `null` if
  none has been recorded since key-on;
- `err_burst`: whether a recent qualifying burst exists at the reading. This
  is section 7's trip 6 test, evaluated at the time of the reading, so it shows
  ahead of time that going live would latch trip 6. `err_n` and `err_age_ms`
  alone do not decide it: a burst that began 15 s ago and ended 5 s ago trips,
  while most of it falls outside the last 10 s.

These are on the status page only, and part of its one snapshot (below).

**Refused transmits are counted.** The status page
carries `refused_slcan` and `refused_can_send`, each the number of attempts
refused under section 3.2 since boot. On the status page only.

**A status reading is one snapshot.** The status
page is built by the web server task while the CAN worker updates the
counters at a higher priority. Every counter on the page must come from a
single consistent copy. The copy is taken in one short critical section
against the worker, and the page is built from that copy, never from the
live counters. The critical section must stay short enough not to move
any 5.2 timing bound. "One snapshot" covers every value the page shows,
not only the scheduler's.

## 12. Bench requirements

Stated as requirements on the setup, not as a description of one. The exact
adapters change; what the bench has to be able to do does not. The current
hardware and its quirks belong in the working log and in reverse-it's
`tools-reference.md`, not here.

The bench is a single terminated CAN segment carrying the WiCAN under test and
at least one **USB-to-CAN interface driving that segment** from a host, at the
truck's bit rate. Minimum capability:

- **Replay a recorded VCM stream onto the segment** so the device under test
  sees real `0x051` traffic and answers it.
- **Replay the full powertrain bus**: every ID
  of a real single-epoch capture at its recorded timing, ~2250 frames/s, not
  a `0x051`-only stream. Section 5's loss and ordering requirements are only
  testable this way.
- **Witness the segment independently of the device under test.** This is not
  optional and not a convenience. An instrument cannot be trusted to report a
  fault it is itself causing. In practice this means a second interface, or a
  separate logger, that is neither the transmitter nor the device under test.
- **Timestamp at the wire, in hardware.** Host-side receive times on a USB
  adapter show transfer batching, which will be mistaken for device jitter.

**Full-bus arms are run with the charge interposer's presence controlled**,
ideally both present and absent in one session so the comparison is not
across two bench states.

### 12.1 Reading the result

The device reports its own RX-to-TX figures and the witness reports what
reached the wire. **They must agree, and the way they agree is the check.**
The witness sits above the device's own figure by about one frame's air time,
because the device timestamps when it queues the frame and the witness sees it
after it has been transmitted. A constant one-frame offset across every arm of
a sweep means neither instrument is lying about the distribution; a varying or
absent offset means one of them is, and the run is void.

**What the device's own figure measures.** `response` is RX to the
device *observing completion* -- the driver reporting nothing of ours left to
transmit, seen at the next loop iteration. The offset to the witness is
therefore not a clean one-frame constant: expect the witness at or slightly
*before* the device's figure. Do not compare a sweep against the
2026-09-20/22 numbers without allowing for this (`VALIDATION.md` 12.1); it
will look like a regression and is not one.
RESPOND's probe is still counted at queueing, so probe timing stays
comparable with the earlier record. "Queueing" means **handing the probe to
the driver**. It is not the moment the probe enters the
scheduler's own queue. A probe that waits behind a telemetry frame shows that
wait in its figure. One RESPOND baseline is re-measured on the bench after the
switchover, to show comparability rather than assume it.

Every arm must report the rolling-counter check (`ctr_bad`), which must be
zero. Under load the device is allowed to be **late but never lossy**: the
driver's receive queue (5.1 item 1) absorbs preemption, and a missed counter
step is a real defect where a delayed frame is not.

Two more requirements on how runs are conducted:

- **A load arm alongside the idle arm.** Hammering the HTTP server for the
  duration of a run is an adversarial load rather than a field one, and exists
  to bound the tail, not to represent normal operation.
- **Each disable condition tested from a fresh boot**, because the disable
  latches until a key-on (7.1) or a reboot (section 6).

### 12.2 Fidelity the simple bench does not have

**Every capture in the corpus has batched timestamps**, so a replay "at
recorded timing" sends each batch back to back. That saturates the bench bus
in patches and squeezes VCM `0x051` gaps below the truck's 4.69 ms floor.
Replay results are harsher than the truck: conservative for receive backlog,
pessimistic for low-priority transmit timing such as diag. The truck's real
burst structure and VCM gap distribution under load cannot be measured from
the corpus; that needs a new capture with a logger that timestamps at the
wire in hardware.

**A replay on a clean fixed-period grid is not the truck.** The truck's
`0x051` arrives every 4.69 to 16.4 ms and is jittery. Reactive trail is
cadence-independent, so the present margin holds regardless, but a perfect-grid
replay flatters any strategy that is *not* purely reactive.

The items below therefore become requirements the moment lead or bracket
placement is prototyped (section 14.3), and are otherwise deferred:

- **Two replay modes.** *Verbatim*, replaying recorded inter-frame deltas for
  maximum fidelity; and *parametric*, sampling from the measured distribution
  so it is sweepable. Verbatim confirms survival of one recorded day;
  parametric finds where it breaks.
- **A frame-acceptance model for the inverter** -- accept-newest,
  counter-validating, or per-frame. Without one the bench cannot score whether
  an inhibit actually worked.
- **The supporting signals** the section 7 arm gate and trips read:
  `0x051`, `0x440`, `0x411`, `0x617`, `0x639`, `0x592`, and `0x054`/`0x471`
  where the scenario has an inverter awake. The section 7 list is the
  authority; this bullet only restates it.
- **A scripted start** at a configurable offset, using the measured cadence:
  bus-up to the VCM's crank command ~6.3 s, each attempt 1.09 s then
  abandoned, retry every 9.59 s with zero variance and no escalation or
  backoff, and 0.24 s to start once allowed.
- **Background error frames at a configurable rate.** This is the one fact
  that invalidated the laptop tool's original abort logic (section 7).

**On rig timing.** A general-purpose host adds its own transmit scheduling
jitter, of the order of a few milliseconds, which left uncorrected contaminates
the quantity under test and makes a mishandled jitter indistinguishable from a
manufactured one. A second microcontroller running a truck emulator avoids it
entirely.

**Proposal, not a requirement** (2026-09-06, untested and uncosted): drive the
transmitter from an *intended* schedule and let the hardware-timestamping
witness record what actually landed, turning rig timing from an open-loop
requirement into a measured covariate. Two caveats shape it. **The hazard is
the floor, not the spread** -- what a trailing frame must survive is the VCM's
next frame arriving early, and host scheduling jitter predominantly *delays*
frames, so incidental jitter under-produces the hazard that matters while
over-producing one the truck does not have. And **uncontrolled jitter cannot be
swept or repeated**. Unverified and worth checking early: whether a
transmitting USB adapter batches outbound frames, collapsing gaps rather than
widening them.

### 12.3 Emulation: what must be provable without hardware

The goal is that functional code changes that do not change hardware
mechanics can be flashed on the truck after only an emulation run
(`VALIDATION.md` 12.3, the user's words).

So the emulation suite is a requirement, not a convenience, and it has five
parts:

1. **Mock-HAL host build.** `gen_inhibit.c` with the real `twai_port` and
   `can_port`, compiled on the host against the shared fake of the TWAI
   driver (twai_port T10) and fakes of FreeRTOS and `esp_timer`, so the shim -- mode changes, bus bring-up and listen-only
   forcing, transmit dispatch and its outcome, the receive-error path, the
   quiesce handshake from a second thread, the JSON -- is exercised, not only
   the core. It must support **fault injection**: transmit refused / failed /
   not completed before the next `0x051`, receive errors, queue full, a
   `can_disable()` racing a blocked receive. This is what covers trip 7's
   failed-transmit forms; the controller-reported form cannot be produced on
   this controller (7). Share the Python side
   (replay, witness, trace format, invariant checker) with the charge
   interposer's tooling where practical. The driver-level fake is shared;
   each project keeps its own logic-level fakes.
2. **Golden-independent invariants**, checked on every trace from every
   scenario and replay, and over randomised frame sequences. Goldens are a
   regression record and cannot catch a rule that is wrong in the golden too.
   For every emitted inhibit frame: ID `0x051`, DLC 6, B0 `0x08`, B1-B2 `00 80`,
   B5 = the triggering VCM counter + 1; at most one per received `0x051`; key
   on and fresh; no section 6 or 7 latch; not suppressed; live; SoC-valid
   marker set. Never an inhibit frame in OBSERVE, RESPOND or PASSIVE. Latches
   clear only on a key 0 -> 1 reading or an explicit re-arm. **PASSIVE is
   differential against INHIBIT**: every scenario run in both, and the PASSIVE
   trace must equal the INHIBIT trace with each inhibit transmit replaced by a
   would-transmit.
3. **Build-output checks**, run on the image to be flashed:
   `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`; secure boot and flash
   encryption unset; `CONFIG_EFUSE_VIRTUAL` off; `CONFIG_TWAI_ISR_IN_IRAM`
   unset; watchdog settings as section 9.1 records; the console on the
   USB-Serial/JTAG, as stock, with no secondary console; Bluetooth on until its held removal (9.4);
   `CONFIG_FREERTOS_HZ` 1000, a 1 ms tick, which this spec's timings and their bench evidence
   assume (each project sets its own, shared/tools R27);
   log levels as shared/tools R28 sets them for every release build (ESP-IDF
   none, bootloader error); test-only
   builds keep info, so the rollback rehearsal reads its log lines;
   partition table byte-identical to stock; image size against the
   1,781,760-byte slot; `DIAG_FW_VERSION`, the schema doc and the DBC
   consistent -- diag frames emitted by the host build decoded with cantools
   against `vtrux-wican-diag.dbc`.

   **Prod builds are release builds of the shared build tooling.** A prod
   build is any image meant for the truck. It is built
   only by the shared tooling's release build, at the version this project
   pins (shared/tools `SPEC.md`, section 3). That build refuses a worktree
   with uncommitted changes, reconfigures before building, checks the
   downloaded components against their pinned versions, and writes one
   record named by the image hash. Identity does not depend on the branch
   (port plan goal 11).

   An image with no such record is not a prod build. The build-output checks
   report it as NO REFERENCE ("not a prod build"), not as a failure, and the
   truck pre-flash checklist refuses it. A build that runs on a clean tree,
   straight after a reconfigure, cannot carry a stale name or stale code.
   Bench and development builds are exempt. Their results carry to a prod
   build by code hash (shared/tools R3).

   **During the shared-code port this is the intent, not yet the practice.**
   No prod build is made while the port is under way;
   the shared tooling's release build serves the port's own comparisons. A
   known anomaly, such as a check a release build does not meet yet, is
   carried forward through the port without a review cycle of its own,
   provided the port plan or the review tracker names the step that fixes
   it. The time goes to reaching that step instead.

   **The image's version and name are ESP-IDF's own, and are labels.** The
   app descriptor's
   version is the form shared/tools R4 sets: ESP-IDF's default, from `git
   describe` at configure, with no override in this project. `project_name`
   is fixed for each build configuration and carries no commit, so the
   commit enters the image once (R4). Its variant suffixes stay: `_man` or
   `_auto` for the arm state, `_flt` for the fault-injection build and
   `_rt` for the restart test, which the build-output checks read. Both are
   labels: nothing checks them against the tree
   (shared/tools R6), and the image is named by its image hash. A release
   build runs on a clean tree straight after a reconfigure, so its version is
   its commit. A bench or development build's version can be stale after an
   incremental build; that no longer matters, because nothing reads it as
   identity.

   **Every build-time switch is classified, and an unclassified one fails the
   build checks.** Every preprocessor symbol the
   product source tests, other than ESP-IDF's `CONFIG_*` and header guards,
   and every build option tested by a CMake file the build reads from the
   repo, taken from the build's own record of the files it read, not from a
   list kept by hand, is listed as a shipping variant (for example
   auto-arm), test-only, a stock feature off in this product, or not a
   switch -- a default value, a value the build injects or a library macro --
   each with its reason. **A stock feature off in this product** is one of
   the section 3.2 feature switches: on in the shared code, off here. It gets
   the same image-refusal row as a test-only switch, shown failing with the
   switch on and passing with it off. It has no test trigger, so it is not in
   the trigger table of the 12.4 row "test-only features are inert in the
   build that will fly". They stay CMake build options, never ESP-IDF
   configuration options, for every project and shared firmware component
   (shared/tools R20). Each switch lives in the CMake
   files of the project or shared component whose code it gates (shared/tools
   R20): gen-inhibit's own switches stay in its CMake files, and the four
   section 3.2 stock-feature switches are in `shared/wican`, where
   gen-inhibit sets them off, and the SLCAN command switch is a stock feature
   off in this product like them, with the same image-refusal row. A build
   option is a variable that can be set
   from outside the file that tests it: the CMake cache, a `-D`, or the
   environment. A variable the file sets itself before testing it, and
   CMake's built-in variables, are not build options. A test-only switch has
   a row that refuses an image containing
   its feature. The row judges the image and the ELF, never the flag, so a
   switch set to off but not wired to its code is still caught. Each row is
   shown failing on an image built with the switch on and passing on one
   built without it. This list is also what keeps the trigger table of the
   12.4 row "test-only features are inert in the build that will fly"
   complete.

4. **A bus-load model** in the host runner: full captures at recorded timing
   through a model of the RX queue at the firmware's depth and the
   single-buffer FIFO TX with diag interleaved, reporting dropped `0x051` and inhibit frames landing
   after the VCM's next one. It is a model, calibrated by the section 12
   full-replay bench run, not a substitute for it.

5. **Transmit scheduler emulation** (section 5.2). The mock-HAL
   build and the E4 load model run the real scheduler code against a model of
   the single transmit buffer that distinguishes *awaiting arbitration* from
   *transmitting*, loses arbitration to higher-priority traffic, and
   implements abort as measured on the C3 (5.2 item 9). Every case in 5.2
   item 10's catalogue runs here first, with the same pass criteria it must
   then meet on hardware.

**What stays hardware-only**, so a change can be classified: TWAI peripheral
behaviour (ACK, listen-only, error counters and error frames, bus-off), real
timing and preemption under WiFi load, the transceiver, the MeatPi
bootloader's rollback, and the OTA handler against a real client. A change
that touches none of these -- the core, or shim logic the mock-HAL covers --
may be flashed to the truck on a green emulation run. A change that touches
any of them needs the matching hardware test below.

### 12.4 Required tests

Each is required on the build that will fly unless marked host-only. The
section 6.4 list still stands and these are in addition to or sharpen it.
Each row keeps its ruling's date in the row, as the test list's own record.

| test | where | from |
|---|---|---|
| SoC recovering to 22 % after a `low_soc` latch stays latched | host + bench | 6.4, D1 |
| 21.00 % boundary: 2100 keeps inhibiting, 2099 releases (after debounce) | host + bench | 6.4, D2 |
| 0x10 suppression, two episodes in one power cycle | host + bench | 6.4, D3 |
| BMS wake reading does not release; `0x411` before contactor close ignored; never live before close; `replay-rekey-long` does not latch | host + bench | 6.2, A1 |
| every section 7 trip, including the stale-signal trip for each signal, the short-DLC trip and the end-of-drive ordering (key reads 0 before the stale trip) | host (all) + bench (all a bench can produce) | 7, B3, C2b |
| gate refuses with the SoC-valid marker set but `0x440` currently reading open (e.g. 13/14 during a key-off with the BMS awake) | host + bench | 6.2, 7 cond. 2 |
| evidence rule: a whole-bus stop reports trip 2 "bus lost" -- never trip 3 or 5 -- including with `0x617`'s last frame 250 ms before `0x051`'s; a single signal stopping on a live bus trips 3/5 within one `0x051` gap of its expiry | host (+ replay trailing silences) | 7 |
| whole-bus dropout: ~0.45 s rides through with no abort whatever `0x617`'s phase; ~0.6 s latches and reports trip 2, not a stale `0x617` | host | 7 (`0x617` window) |
| **a bus with no other node** (added 2026-10-20, user): with the inhibit live, every other node stops transmitting and acknowledging, and the witness stays in listen-only mode. Required: the witness sees the device's frame repeat unacknowledged (otherwise the run is void); the device sends no `0x051` after its answer to the last VCM command; it latches within 0.7 s of that command (`0x051`'s 0.5 s window plus up to one 200 ms receive timeout; corrected 2026-10-20, user); when the nodes return without a key-on it stays latched and its diag pages resume; a key-on then clears the latch (7.1). Recorded, not judged: which trip latched (2 or 6) and when; the device's TEC, REC and driver state throughout; whether it reached bus-off. Part of the before-port suite on `e3d9437`, then of every full bench run, including each build during the port (user, 2026-10-20) | bench | 5, 7 trips 2 and 6, 7.1 |
| **a bus with no other node, emulated** (added 2026-10-20, user): with the inhibit live, the fake driver stops delivering frames, the device's own frames stay unacknowledged (never complete, error-passive, a bus-error alert per retry), and in a second case the controller goes bus-off. Required: no `0x051` after its answer to the last VCM command; the trips are evaluated once per received frame or per 200 ms receive timeout, and at those points the latch and its reason follow spec 7 for the injected timing: trip 6 if its threshold is met at an earlier check than the one that finds `0x051` stale, otherwise trip 2 (0.5-0.7 s after the last `0x051`; corrected 2026-10-20, user); a stuck diag frame does not block the worker, the status page or the latch; when traffic returns without a key-on the device stays latched and the stuck frame completes; a key-on then clears the latch (7.1); in the bus-off case the device starts no recovery and stays off until a re-arm or reboot (7). Once the bench row has measured the real alert pattern, the fake replays that pattern | host (shim fault injection) | 5, 7 trips 2 and 6, 7.1, 12.3 item 1 |
| GENE family quiet together (`0x054` and `0x471`) reports trip 3 regardless of which frame arrived last; `0x054` alone stale with `0x471` fresh reports trip 5 | host | 7 trips 3, 5 |
| SoC debounce: 4 consecutive sub-threshold readings do not release, the 5th does, and one reading at/above the threshold restarts the count | host | 6.2 |
| error-frame rate trip. One way to test it, not the only one: error frames from a spare adapter at the wrong bit rate, in three regimes (error-active, error-passive, and at the trip threshold), with the device's `state`, TEC and REC, its `bus_error` count and the witness's error-frame count read throughout. The regimes may be split across methods (*user, 2026-10-12*). On the 2026-10-10 bench the wrong bit rate reached only error-passive; error-active and at-threshold were taken with the X2 GPIO jammer (2026-10-09, `VERIFICATION.md` 7) | bench | 7, D4, 14 q8 |
| **no error frames at a restart** (3; added 2026-10-13, user): a `/system_reboot` and a forced crash, each while live in INHIBIT under replayed traffic, put 0 error frames on the bus per an independent witness, and the restart record shows the step ran. The crash trigger exists only in a test build of the same commit, never in a prod build | bench | 3 |
| failed transmit, all three forms (not queued, reported failed, late past the next `0x051`) | host mock-HAL | 7, C1 |
| receive-error self-disarm is reported | host mock-HAL | 7, C2a |
| quiesce: INHIBIT and PASSIVE -> OBSERVE -> back, under traffic | bench + host mock-HAL | 8, D5 |
| OTA refused (403) while armed in any non-OFF mode; accepted when OFF; `offset > 4000` rejected; arm refused when the bus will not come up | bench (OTA) + host mock-HAL (rest) | 3, 9.3, D6 |
| OBSERVE is listen-only **in hardware**: with the WiCAN as the only node able to ACK, the transmitter sees ACK errors; with the WiCAN in RESPOND, it does not. `tx_ok=0` is not evidence | bench (framework rework) | 3, D7 |
| PASSIVE: never a `0x051` from the device (witness), diag present, would-transmit count matches the VCM frames the witness saw while would-be-live, every release and trip reported as in INHIBIT | host differential + bench | 3.1, B1 |
| no other transmitter: SLCAN commands on TCP, UART and websocket do nothing to the bus; `can_send()` refused while owned | host mock-HAL + bench | 3.2, A3 |
| full-replay bus: no dropped `0x051` answer and no inhibit frame after the VCM's next, in normal operation (no heavy WiFi to the device while inhibiting -- the design case, user 2026-09-26), diag active; counted and timed by the witness, which must tell the device's inhibit frames from the VCM's | bench | 5, 12, A4 |
| receive backlog measured directly: a measurement-only build (never flashed to the truck) records the driver's RX backlog at each dequeue (`msgs_to_rx` + 1) and reports the high-water and how often it is large; on a full-replay bus in the design case it confirms or revises `WORST_PREEMPT_US` (2.39 ms). | bench (measurement build) | 5, 5.1 items 1 and 5 |
| RX queue depth >= 64 whenever the driver is installed (E1 reads the depth the driver was installed with; E3 or a source check pins it) | host mock-HAL | 5.1 item 1 |
| **abort behaviour of the C3 TWAI**, measured before the scheduler is built on it: abort removes a frame awaiting arbitration; abort during transmission is a no-op and the frame completes; what the driver reports afterwards (alerts, `msgs_to_tx`, internal count); time to free the buffer. The emulator's abort model is checked against these numbers | bench | 5.2 item 9 |
| **transmit scheduler case catalogue** (5.2 item 10), every case run in emulation (mock-HAL + E4) and then on the bench against hardware with identical pass criteria | host mock-HAL + E4, then bench | 5.2 |
| **abort-and-recheck loop** (5.2 item 4): frees the buffer within 1 ms under contention, including when the first abort has no effect; the emulator's abort model reproduces item 9's three TS = 1 outcomes. | bench + host mock-HAL | 5.2 items 4, 9 |
| **skipped inhibit frames** (5.2 item 5, section 7 trip 7): a frame not on the wire by the VCM's next `0x051` is withdrawn and never sent late; skips are counted on the status page and in a diag page; 3 within 1 s trip 7 and 2 do not (strictly less than 1 s); a frame that went out late, or may have (still in the buffer at the deadline), counts as a skip; TX_FAILED or a driver refusal trips at once; on-time verdicts with a non-empty receive queue are counted as unverified; the bench witness confirms no frame went out late undetected. Bench method (user, 2026-10-04): the stimulus sends VCM N and N+1 back to back, then ~2 ms of filler below `0x051`, so our answer to N loses arbitration and is withdrawn. With no filler it goes out late and must count as a late skip. Three events within 1 s must trip 7 (reason 15); two, or a third at ~1.1 s, must not. The witness classifies each event by wire order, and the device's counts are judged against it on totals: device late >= witness late, and device withdrawn <= witness never on the wire. A frame still in the controller at the deadline is booked late (trip 7), so the device may book as late a frame the witness saw never reach the wire. *(Changed 2026-10-12, user.)* These strict checks verify the implementation tightly; they are not a claim that the truck needs that exact threshold | host (golden) + E1, then bench | 5.2 item 5, 7 trip 7, 10, 11 |
| **no stale answers on going live** (added 2026-10-10, user): the first inhibit frame after going live answers the VCM's newest `0x051`, never one it has already passed. Run under full-bus replay on every entry into INHIBIT: from OFF, OBSERVE, RESPOND and PASSIVE, and from an auto-arm boot. The witness shows no device `0x051` with a counter the VCM had already passed | host (a case with frames waiting at go-live) + bench | 3 (mode change), 7 condition 9 |
| **the private TWAI abort is confined**: `twai_ll_set_cmd_abort_tx` appears at exactly one site in the shipping source, `twai_port`'s abort-if-awaiting (twai_port T8), where the status read and the abort run in one critical section, and gen-inhibit reaches it only through the scheduler's device HAL. `twai_transmit` appears only in `twai_port`'s send (user, 2026-09-27; port step 1b, 2026-10-31) | E3 (source rows) | 5.2 items 1, 4, 9 |
| **per-class counts balance on every reading** (user, 2026-09-29): `queued + carried_in = sent + dropped + withdrawn + depth + held` for every class, including a reading after a withdrawn skip. It must hold with a frame in the controller at the reading, and across a re-arm with a frame still held. A carried-in frame's completion never sets the new arm's longest hold. The bench check applies the same identity to every arm's end-of-arm status | host (sched + E1) + bench (every E4 arm) | 5.2 item 6, section 11 |
| **a stand-down with an answer pending** (added 2026-10-16, user): an answer queued or still in the controller when a section 6 release or a section 7 trip lands is counted as `standdown`, never as a skip; skips unchanged; diag pages on cadence afterwards (section 10); the balance identity holds. The latch is phase-locked on a `0x051` so the answer is pending. Fails on `c65c45e` | host (sched + shim) + bench (`liveflag-mmode-hold`) | 5.2 items 5 and 6, 10, 11 |
| **the worker makes every mode change** (added 2026-10-16, user): a second thread issues mode changes against a live replay, and every arm's balance holds; on the bench, repeated 3->3 and 3->4->3 changes on a live bus, each reply correct, the balance and the diag cadence held; a request not taken within 1 s of the setter's call is refused and never applied, and one already taken is finished and reported; a re-arm while an old-arm withdrawal is still running counts it as `standdown` in the new arm; no task but the worker writes the mode (added 2026-10-16, user) | host (second thread + sched) + bench | 3, 5.2 item 6, 8 |
| **the worker state machine** (added 2026-10-16, user): every row of the 8.1 table, each from its own starting state, including the refusals (rows 2, 8, 12); an OTA while armed is refused by the worker with nothing touched; an OTA that fails returns the worker to IDLE and a later arm works; a teardown by another caller while armed (the backstop) leaves the worker OFF, reported and counted, and one while IDLE or by the OTA handler counts nothing; an auto-arm build installs with the configured bitrate and `can_mode`; no disabled caller (3.2) can change bus state in any mode | host (shim, a second thread) + bench (OTA while armed and while OFF; a failed upload) + build check (3.2 callers) | 3, 3.2, 8.1, 9.3 |
| **a status reading is one snapshot**: the page is built from a copy taken in one critical section against the worker, never from the live counters | E1 (the page is built during worker activity and every reading balances) | section 11 |
| **every case whose stimulus comes from a capture is pinned to it** (user, 2026-10-02; widened 2026-10-03 from "every golden"): each golden records a checksum of the scenario it was made from, and a case with no golden (a replay-driven shim case) records it against the case. The suite FAILS when a regenerated stimulus does not match, and when a golden has no scenario. Comment lines are checked separately and only give a notice. Replay scenarios are rebuilt from captures through the project parser and are not in git. On 2026-09-27 a parser fix changed six of them silently, and the goldens stopped reproducing from the repo while a stale local copy kept passing | host (`run_tests.py`, E1) | 12.3 item 2 |
| **every emulation case declares its transmit-timing configuration** (user, 2026-10-03): the contention flag, air-time overrides and stall settings are reported with the case. A claim about contention can then be read beside the setting that produced it, and a load-dependent case cannot pass unnoticed because a setting was left at its default | host (E1 + sched) | 5.2 item 10 |
| a TX_LATE abort updates the counter check and `rx_gap` for the frame that triggered it: `ctr_bad` stays 0 and `rx_gap` records no doubled gap when no `0x051` was missed | host (golden) | 11, 7 trip 7 |
| E4 with the fix: 0 lost / 0 late at the truck rate and worst measured preemption; `tx_behind` = 0 while live; preemption sweep reports first loss at >= 7.2 ms | host (E4) | 5.1 items 1, 4, 5 |
| diag scheduling: while transmitting inhibits (mode INHIBIT, live, key on, not suppressed), every diag page is queued right after an inhibit completion; normal cadence when not transmitting inhibits (including PASSIVE after a live INHIBIT); the completion record is cleared whenever the device stops transmitting, so the first page after going live again is not held by a stale completion (the 1 s fallback was withdrawn 2026-09-26) | host (E1 + E4) | 5.1 item 4 |
| flash writes refused while owned: `store_config`, `store_canflt`, `store_auto_data`, `store_car_data`, `upload_car_data` and OTA each return 403 in every non-OFF mode and succeed when OFF; WiFi storage is RAM | bench (+ a source/E3 check for the WiFi storage call) | 5.1 item 3, 9.3 |
| **auto-arm build**: a test that runs only when `GEN_INHIBIT_AUTOARM_MODE` is built in, covering power-on straight into the gate, waiting for the contactors, going live, and the boot-to-live time; and a reset asserted on the WiCAN in the middle of a log replay with the generator running comes up armed and never goes live (condition 5), with the witness showing no `0x051` beyond what the replay sent; and a **restart timed to a key-on** (*added 2026-10-10, user*): a replayed capture starting at its key-on edge, with the bus silent before it, and a commanded restart (`/system_reboot`) landing at a set of offsets around that edge: before, at and after it. Measure key-on to live, and confirm the first device `0x051` answers the VCM's newest command (condition 9). Use a capture from the 7.7 s cluster of the deadline sweep (`VERIFICATION.md` 7), where the VCM asks for the engine soonest | bench | 7, D8 |
| golden-independent invariants over every trace and a randomised sequence | host | 12.3, D9 |
| build-output checks | host (build) | 12.3 |
| **the page names the binary** (11; added 2026-10-14, user; changed at port step 0c, user, 2026-10-25): `elf_sha256` on the page equals the value in the flashed image file's descriptor, and its first four bytes equal `0x7F3`'s build id | bench | 11 |
| **`0x7F3` carries the build id** (10; port step 0c, user, 2026-10-25): on the bench, `diag_build_id` decoded from `0x7F3` with the schema 8 DBC, printed as eight hex digits, equals the first four bytes of the flashed image's hash and of the page's `elf_sha256`; on the host, the encoder packs a known hash into B0-B3 in the interposer's `0x7F7` byte order | bench; host (shim) | 10 |
| **test-only features are inert in the build that will fly** (added 2026-10-13, user): for every test-only build switch, its trigger is sent to the flight image exactly as a test run sends it, and the effect it exists to produce is shown absent on the bus and on the device: no restart, no transmitted frame, no rollback, no extra page field. The same probe is shown to produce the effect on a build with the switch on, so a probe that reaches nothing can't pass. The triggers come from one table that the test rigs also use | bench | 12.3 |

## 13. Known not to be implemented

This is the one place the spec talks about implementation status, and it does
so in one direction only: these requirements are **known to be missing**. When
one is implemented, delete its entry -- do not replace it with a note saying it
is done.

**Known not implemented, from the section 12.4 audit of 2026-10-04**
(reviewing session, user-approved). Tracker:
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-review-2026-09-22.md`.
Delete each line as it is implemented.

- **Bench, an existing harness lacks the case:**
  - SoC back at 22 % stays latched; the 21.00 % boundary; two 0x10
    episodes in one power cycle; the live flag clearing on a latched
    release; the 6.2 BMS-wake and pre-close rows (6.4);
  - gate refusal with `0x440` reading open; trip 5 for each signal;
    trip 8 (7).
- **Truck, at bring-up:**
  - only what a restart skips: the 12 V supply ramp and any start-up that
    runs only at power-on. In this build, image validation runs on every
    boot, since no skip-validate option is set in `sdkconfig`. So on the
    truck, measure power-on to live once and compare it with the bench figure.
- **Not written** (adopted 2026-10-04, user):
  - a WiCAN truck bring-up procedure.

**Known to differ from spec 7, trip 6's count** (*added 2026-10-21, user;
tracker B6*). Trip 6 counts the controller's bus-error interrupts, and these
do not always match error frames on the bus:

- **One error frame can count as several.** On the bench (B6, jammer pulses
  1 s apart), 7 of 1,891 pulses were counted 7-12 times each, against one
  error frame on the witness: 4 of 679 at 10-12 on builds before the port,
  and 3 of 1,212 at 7-8 on the measurement build. So trip 6 can latch after
  1-2 real error frames, and the device then stands down until a key-on.
- **An error frame can go uncounted:** 1 of 1,212 on the measurement build.
  That delays trip 6 by one error frame.
- Why the controller sometimes counts one error frame several times is not
  shown (tracker B6 hypothesis).
- No change is made until realistic wire faults have been characterised
  (14 q12). That decides between counting each error frame once, raising the
  threshold, or leaving it. Delete this entry when trip 6's count or
  threshold is changed for it.

**Mode 3 is bench-only until the hardware test suite has been run against the
build that will fly.** Transmitting a real `0x051` onto a live powertrain bus
waits on that, not on this list. *Clarified 2026-10-04 (user):*

- **Which build:** the latest HEAD at the time of the flash, as a prod
  build (12.3 item 3).
- **What carries over:** a build whose firmware is code-identical to a
  tested one carries that build's results. The suite may be re-run as a
  formality, but does not have to be. Code-identical means the same code
  hash (shared/tools R2).
- **What does not:** any other change restarts the suite, per 12.3.
- **Arming:** truck bring-up starts in manual mode, and the auto-arm
  build is fully tested before it is deployed.

**The gate is NOT cleared.** The user's decision, 2026-09-20: *"will do some
more re review on this before we do bring up on the truck"*, and that review
gets a **fresh session** rather than a continuation of the work that produced
the results -- deliberately, so it is read by someone with no stake in them.
Meeting the literal condition above is deliberately not treated as
sufficient -- a gate whose condition an agent may evaluate and then clear
for itself is not a gate, and this one guards transmitting a real `0x051`
onto a live powertrain bus.

**To confirm at truck bring-up.** None is expected to be a problem; each is
cheap to observe on the first runs.

- **The VCM does not fault on a blocked start.** A crank that doesn't
  catch is routine for it. In 8 natural failed starts in the corpus and 6
  blocked attempts on 2026-09-06, it retried on the same ~9.5 s cadence
  with `0x617` normal every time. Bring-up confirms the same with B0 held
  at `0x08` (4.1), which those 6 attempts did not have because they
  mirrored B0.
- **The ECM does not care.** A blocked start gives it less to see than a
  natural failed start: the engine never turns, so there is no crank
  signal. A DTC read before and after the first blocked starts confirms
  nothing is stored (14 q7).
- **The inverter acts on our frame with B0 held.** The counter steal is
  required of the device (section 4) and tested on the bench; the truck
  shows whether the inverter takes ours over
  the VCM's. It is observable directly: the engine either cranks or does
  not.

Open, but not a bring-up risk: where the background error frames originate
(14 q6).

Verification against this spec is recorded in `VERIFICATION.md`, and open
findings in the review tracker, not here. That is also where anything found
to CONTRADICT this spec is recorded -- the host test suite is verification
work, so its findings are spec findings. Section 13 stays a list of things
not built; a thing that is built but wrong belongs in the tracker and, once
resolved, in the relevant section here.

Nothing is outstanding on power or voltage: the sleep state machine is gone,
so there is no voltage-driven bus teardown or light-sleep to reason about on
the vehicle supply, and battery voltage is sampled for telemetry only.

## 14. Open questions

Nothing here binds an implementation.

1. **Gating GPIO6 (transceiver STBY) on lock freshness.** The pin is
   already wired (the board's `board_pins.h`, shared/boards wican_v3), set high -- standby, transmitter off -- at
   boot and driven low only on-bus, and on reset it goes high-Z with the
   transceiver's pull-up returning it to standby, so a crashed MCU stops
   transmitting with no software involved. **But the transceiver's
   permanent-dominant protection is structurally blind to the failure that
   matters here**: per MCP2561/2 DS20005167C, `tPDTR` is typ 100 ns, so any
   recessive pulse resets the dominant timer and a transmitter emitting
   well-formed frames never trips the 1.25 ms detection. A transmitter that
   loses phase lock but keeps sending well-formed zero-torque `0x051` is
   therefore invisible to it, and unlike noise such a frame is a plausible
   command. The interlock must be the sync core dropping STBY itself.
   *Hypothesis, not validated:* the safe state on loss of lock is not
   transmitting at all rather than holding the last good value, and transmit
   permission should be the same computed fact as lock freshness rather than a
   separate interpretation of a phase estimate. `tWAKE` (standby to normal, max
   40 us) is ~0.85 % of the 4.69 ms guard band, which argues for holding STBY
   low across a run of good slots rather than cycling per frame.
2. **WiFi during active inhibit.** Raised 2026-09-10. It pairs naturally
   with refusing OTA while armed -- both narrow what can perturb a live
   transmitter -- but the original motivation (the `esp_wifi_stop()`/`start()`
   reconnect path) is already gone in AP mode, which does not reconnect. So it
   is belt-and-suspenders isolation rather than a known hazard, and it trades
   away live read-back during a run.
3. **Lead or bracket placement.** The device's measured latency
   (`VERIFICATION.md`, section 5) may make
   forward placement feasible where the laptop's 1-4 ms latency refused it on
   every run. Trail already works, so this is an improvement, not a
   requirement; prototype and measure before adopting.
4. **Stripping `mqtt`, `elm327` and `autopid`.** Small flash (mqtt
   ~14.5 KB) and the deepest `config_server.c` coupling, so lower value and
   higher risk than BLE. It would remove the last uncoordinated `can_disable()`
   callers. (2026-09-24: section 3.2 now bars those callers functionally while
   the bus is owned, so this is back to being a flash-space question.)
5. **Whether the trail deadline should be sized from 4.69 ms rather than the
   nominal 10 ms period.** Unresolved from the laptop work; no evidence
   yet links our transmissions to error frames, and the passive error rate was
   *higher* than the armed one. The device's measured trail
   (`VERIFICATION.md`, section 5) makes this close to moot for the firmware.
6. **Where the background error frames originate.** Unlocalised; they
   appear only with the generator running.
7. **What the ECM stores after a blocked start.** Not yet read. The MIL
   did not illuminate and the VCM never raised a fault, but DTCs have not been
   pulled; `$REVERSE_IT_DIR/projects/vtrux/tools/vtrux_dtc_interrogation.py`
   exists.
8. **Can an ESP32's TWAI usefully inject bus error frames?** Needed for
   the configurable error-frame background in section 12.2. Unknown; it may
   need a third node or a different approach. Worth checking early, because it
   shapes what a higher-fidelity rig can be.
9. **What `0x08` versus `0x0B` actually switches inside the inverter.**
   Both read OPERATIONAL on `0x053`, so the status field does not distinguish
   them, yet they differ by roughly 10x in standing power draw (section 4.1).
   Knowing the mechanism would say whether holding `0x08` fully de-energises
   the inverter or merely reduces the draw.
10. **Whether to change either watchdog setting** so that a hung app resets
   itself (section 9.1). Neither currently does. The counter-argument is that
   an unattended reset of a device transmitting to a live powertrain bus is its
   own hazard.
11. **Is a measured-stimulus host rig good enough to defer emulator
   firmware?** The proposal at the end of section 12.2. It turns on whether a
   transmitting USB adapter batches outbound frames, and on whether an intended
   schedule plus a few milliseconds of host noise reproduces the 4.69 ms floor
   that actually matters.
12. **How do wire faults the truck can actually have present to the
   controller?** (*added 2026-10-21, user*) The B6 jammer's pulse is the full
   short case. Whether realistic faults also make the controller count one
   error frame as several, or produce one count per error frame, decides
   whether trip 6 counts each error frame once, its threshold rises, or
   neither (13, trip 6's count). Related: where the background error frames
   originate (q6).

## 15. Toolchain

**Paths differ per machine and are never written here.** Look them up, in
reverse-it:

| What | Where |
|---|---|
| ESP-IDF and tools install paths, per machine | `$REVERSE_IT_DIR/tools-reference.md`, "ESP-IDF"; this repo's `local-paths.md` |
| Python executable, per machine | `$REVERSE_IT_DIR/python-executables-reference.md`; this repo's `local-paths.md` |
| Build invocation and Windows gotchas | `$REVERSE_IT_DIR/firmware/README.md` |

Two constraints that are *not* machine-specific and do belong here:

- **ESP-IDF v5.4.2**, the charge interposer's version, from port step 1d;
  v5.4.1 before it, matching `wican-fw`'s
  `dependencies.lock`. The project's component manifest names it exactly
  (`shared/tools` R33). Changing the IDF version changes the bootloader,
  which is the thing rollback depends on (section 9.1) and the thing OTA
  cannot replace, so a version change updates the bootloader with the app,
  flashed by USB.
- **Native `idf.py`, not PlatformIO.** The stock `sdkconfig` and partition CSV
  are used as-is. PlatformIO regenerates `sdkconfig` from `platformio.ini`,
  which is exactly where the partition table could silently drift from stock --
  and a drifted partition table means the restore image in section 2 no longer
  matches the device.

## 16. Related

In this folder: `VALIDATION.md` (why each requirement is written as it is),
`VERIFICATION.md` (test results, current runs only; moved from reverse-it's
`gen-inhibit-test-evidence.md` 2026-11-07) with its hash manifest
`VERIFICATION-manifest.txt`, and the file map `README.md`. `notes/` below is
`$REVERSE_IT_DIR/projects/vtrux/notes/`.

| file | what |
|---|---|
| `notes/gen-inhibit-review-2026-09-22.md` | the 2026-09-22/24 external review: every finding, the user's decision, and where it landed in this spec |
| `notes/gen-inhibit-wican-handoff.md` | handoff to the session implementing section 13 on hardware |
| `notes/gen-inhibit-wican-firmware.md` | the working log: how each result was obtained (the results themselves are in `VERIFICATION.md`) |
| `notes/gen-inhibit-truck-tests.md` | the 2026-09-06 truck session that proved the inhibit |
| `gen-inhibit/tools/` | the laptop Python core and benches; `inhibit.py` is the validated reference |
| `gen-inhibit/diag/wican_diag_schema.md` (`notes/artifacts/gen-inhibit/wican_diag_schema.md` is the frozen schema 7 copy) | diagnostics layout, source of truth |
| `notes/artifacts/gen-inhibit/README.md` | every bench harness and run file |
| `notes/artifacts/gen-inhibit/rollback-test/ota_defect_writeup.md` | the OTA defect evidence |
| `$REVERSE_IT_DIR/firmware/README.md` | the shared ESP32 firmware tree and the rollback bench |
| `gen-inhibit/diag/vtrux-wican-diag.dbc` (`$REVERSE_IT_DIR/projects/vtrux/vtrux-wican-diag.dbc` is the frozen schema 7 copy) | decode DBC for `0x7F0`-`0x7F3`, `0x7F8` and `0x7F9` |
