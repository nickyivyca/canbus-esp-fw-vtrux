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

<!-- Batch 1 of 5 ends here: sections 5-16 follow in later batches. -->
