# firmware/ -- the interposer board

Firmware for the Autosport Labs **ESP32-CAN-X2** (ESP32-S3-WROOM-1-N8R8),
sitting between the Bel Fuse charger and the rest of the Vtrux powertrain bus.

```
 vehicle segment ==[ CAN1 / TWAI ]== InterposerCore ==[ CAN2 / MCP2515 ]== charger
```

`machine.cpp` is a port of the parent folder's `machine.py`, which stays the
reference implementation. Everything else here exists to feed it frames. What
the core is required to do is specified in
**`projects/vtrux/notes/charge-interposer-spec.md`**; this README is the board, the
build and the bring-up.

Paths beginning `projects/vtrux/` are in the `reverse-it` project on
SeaDrive, not in this repository. The spec, the bring-up log and the
bench artifacts stayed there when this folder moved into
`charge-interposer/`; see `../README.md`.

> **Status:** Stage 0 (2026-09-10), Stage 1 and Stage 2 have all passed on
> hardware; the running log of every result is
> `projects/vtrux/notes/interposer-firmware-bringup.md`. The logic is separately
> verified against the Python reference over real captures (below).

## Layout

| Path | What |
|------|------|
| `platformio.ini` | Build config. Arduino framework, `esp32-s3-devkitc-1` board (there is no PlatformIO definition for the CAN-X2). Six environments: `esp32-can-x2` (the bench bridge), **`truck`** (the same with serial compiled out, spec 8.3), **`esp32-can-x2-witness`** (the bridge plus completion-order instrumentation), `selftest`, `diag`, `slcan`. **Each one must exclude every other env's `*_main.cpp`** -- `slcan_main.cpp` was added on 2026-09-29 without updating the others, and `esp32-can-x2` did not link at all from then until 2026-10-03. Nothing caught it because nothing built it in between. |
| `src/machine.h` / `.cpp` | **The state machine.** Pure: no Arduino, no ESP-IDF, integer-only, time as a parameter. The only file whose behaviour matters. |
| `src/can_port.h` | `CanPort` interface + a fixed-capacity `FrameRing`. Separates "queued" from "on the wire" deliberately. |
| `src/port_twai.h` / `.cpp` | CAN1, the vehicle segment, on the S3's built-in controller. `setMode()` is spec 2.3's listen-only switch, and it is a driver teardown and reinstall because TWAI fixes the mode at install: it can be refused, and it discards the driver's queues. |
| `src/port_mcp2515.h` / `.cpp` | CAN2, the charger segment. Hand-written; see below for why. |
| `src/bridge_health.h` | Spec 8.2's `bridge_ok`: health **since the previous status frame**, not since boot. `sample()` answers and moves the baselines, for a frame; `peek()` answers without moving them, for 8.3's serial line in listen-only where no frame goes out. Pure and header-only, kept out of `main.cpp` so `test/host_unit/` can compile it. |
| `src/charger_tx_failures.h` | Spec 2.2's `0x7F6` B7 counter: the sum of the charger port's three disjoint tallies (queue full, controller failure, the 100 ms age-out), accrued only while the VCU's flow bit is 1, with a delta that arrives outside a session DISCARDED rather than banked for the next one. Lifted out of `main.cpp` on 2026-10-13 for the same reason as `bridge_health.h`: the rule is the whole of it, and nothing could test it where it was. |
| `src/main.cpp` | Wiring only. Moves frames, reports losses. Owns spec 2.3's two caller duties: the one round of status frames at power-up, and applying the core's `vehicleListenOnly()` to the port BEFORE dispatching that pass's frames. |
| `src/slcan_main.cpp` | **Bench only, and not part of the bridge.** Turns the board into a plain SLCAN transmitter on CAN1 so the generator-inhibit bench has a SECOND transmitter on the powertrain segment. Frames queued in one adapter do not arbitrate against each other, so filler from the bench's PCAN delays the replay's `0x051` whatever its CAN priority; from this board's own controller arbitration places it, `0x7E0` loses to every truck id, and it cannot delay a command. Silent until it receives an `O`. Read its header before flashing it -- it lists the protocol subset and four deliberate refusals. |
| `src/selftest_main.cpp` | Stage 0. Cross-port identity, DLC and throughput over the loopback jumpers. |
| `src/diag_main.cpp` | Stage 0a. Controller internal loopback + physical-layer probes. Flash this when Stage 0 fails and the cause could be either wiring or driver. |
| `datasheets/` | **Not in this repo.** The vendor data sheets (MCP2515 DS20001801J) were deliberately left out of the move and stay in the reverse-it project, at `projects/vtrux/tools/interposer/firmware/datasheets/`. Findings taken from them need both text extraction and the rendered page -- see that folder's `README.md`. |
| `test/host_diff/make_golden.py` | Freezes what `machine.py` does over a real capture into a trace + golden pair. |
| `test/host_diff/host_runner.cpp` | Drives `machine.cpp` over the same trace, same output format. |
| `test/host_diff/check_port.py` | Cross-checks every constant against `machine.py`. Needs no compiler. |
| `test/host_unit/` | C++ unit tests for the pure headers above -- plain `g++`, no peripheral fakes. `run.sh` builds and runs every `test_*.cpp`; see that folder and `test/README.md`. |

## Build

Six environments.

| Env | Image tag | What it is |
|---|---|---|
| `esp32-can-x2` | `bridge` | The bridge, with serial. What the bench runs. |
| `truck` | `truck` | The same image with serial compiled out (spec 8.3). What goes in the vehicle. |
| `esp32-can-x2-witness` | `witness` | **Bench only.** The bridge plus the completion-order witness. Keeps serial, because dumping the witness is the point. |
| `selftest` | `selftest` | Stage 0 board self-test (needs jumpers). |
| `diag` | `diag` | Stage 0a fault-isolation build. |
| `slcan` | `slcan` | Bench-only SLCAN transmitter for the generator-inhibit rig (see `src/slcan_main.cpp`). |

**Flashing `slcan` or `esp32-can-x2-witness` replaces the bridge**; restore it
with `-e esp32-can-x2 -t upload`, and there is a byte-exact image of the flash
as it was before `slcan` first went on in
`projects/vtrux/notes/artifacts/interposer-firmware/flash-backup/`.

**The build is reproducible on one host OS** (spec 8.2, 2026-10-08).
`reproducible.py` is a `pre:` extra_script that fixes `SOURCE_DATE_EPOCH` to a
constant -- not the commit time, which would make the image depend on when the
tree was committed -- and adds `-ffile-prefix-map` for the project and package
directories, in both path spellings and on both SCons environments, since the
Arduino framework's own sources compile in the default one. `platformio.ini`
pins the platform to an immutable release-asset URL for the same reason. Read
that file's header before changing any of it: a prefix map that matches
nothing fails in a way that looks exactly like success, because the build
still works and only the embedded strings differ. Across host OSes the flashed
code is the same and the id is not, which is expected rather than a defect --
the measurement is in `reproducible.py`.

**No image is called `firmware.bin`.** Each environment writes
`interposer_<src_digest>_<tag>.bin`, so six images cannot be told apart only
by their parent directory -- the same collision the generator-inhibit project
fixed by tagging its auto-arm build, and a worse one here, because a board
carrying measurement instrumentation must not be confusable with the bench
bridge. Every build also records itself in
[`builds/manifest.json`](builds/README.md), which maps the four bytes the
board reports in `0x7F7` back to an image. `build_name.py` and `manifest.py`
do this; `build_identity.py` holds the table both they and `test/build_check.py`
read.

> **USE `py -3.14 -m platformio`, NOT the `pio` on PATH** (NICKY-XPS,
> 2026-09-29, interpreter updated 2026-10-04). The `pio` on PATH lives under
> **Python 3.9** (`/c/Program Files/Python39/Scripts/pio`, Core 6.1.18), and
> the bundled `tool-esptoolpy`
> now annotates `_verbosity: str | None`, which Python evaluates at runtime and
> which 3.9 cannot parse. **Every environment fails, the bridge included**, and
> the failure names `bootloader.bin` and a `TypeError` rather than anything about
> Python versions -- the C++ compiles fine first, which makes it read like a
> toolchain fault. Worth knowing because "rebuild from source" is this board's
> documented restore path and it was quietly unavailable.
>
> **Core 6.2.0 is installed under both `py -3.14` and `py -3.11`** (both
> verified 2026-10-04), so no install is needed either way. `py -3.14` is the
> machine's primary interpreter since 2026-10-04
> (`python-executables-reference.md`) and is what `build_check.py` uses to
> build every environment in `platformio.ini` -- six of them, a list it reads
> from the file rather than keeping its own copy; **`py -3.11` still works** and is kept because
> the ESP-IDF venv was built from it. The version on PATH is the only one
> that does not work.

`[common]` sets `-DARDUINO_USB_CDC_ON_BOOT=1`, which is not optional. The
`esp32-s3-devkitc-1` board definition sets `ARDUINO_USB_MODE=1` but leaves CDC
on boot at 0, which makes Arduino `Serial` UART0 on GPIO43/44 -- pins with
nothing attached on this board. Without the flag every `Serial.printf` is lost
and the board looks like it crashed on boot.

```bash
py -3.14 -m platformio run -d charge-interposer/firmware     # all
py -3.14 -m platformio run -d charge-interposer/firmware -e esp32-can-x2 -t upload
py -3.14 -m platformio run -d charge-interposer/firmware -e slcan -t upload
py -3.14 -m platformio run -d charge-interposer/firmware -e truck   # no serial
# BENCH ONLY -- replaces the bridge with the instrumented build:
py -3.14 -m platformio run -d charge-interposer/firmware -e esp32-can-x2-witness -t upload
py -3.14 -m platformio device monitor -b 115200
```

| Env | RAM | Flash |
|-----|-----|-------|
| `esp32-can-x2` | 7.6 % (24,972 B) | 10.7 % (357,710 B) |
| `selftest` | 7.1 % (23,388 B) | 10.6 % (354,102 B) |
| `slcan` | 6.4 % (20,860 B) | 10.1 % (337,158 B) |

**After flashing, confirm what is actually on the board rather than assuming.**
`0x7F7` B0-B3 carries the first four bytes of the image's ELF SHA-256, and
`../build_lookup.py` resolves that against the manifest. It refuses rather
than guesses, with a separate reason for an unknown id, no `0x7F7` frames at
all, an ambiguous prefix, and a missing manifest. Any bench arm that needs the
witness image should call `require_witness()` instead of trusting that the
right thing was flashed.

## Two bench hazards that produce confident wrong answers

Both found on 2026-10-05, both silent, and both of a kind that makes a
measurement look clean while describing something else.

**CLOSING THE USB SERIAL PORT RESETS THE BOARD.** DTR/RTS is the reset line.
A script that opens the port once per condition therefore reboots the board
*between* conditions, changing uptime, queue state and error history along
with whatever you meant to vary. Worse, it fails silently: the `LOSS` line is
printed only when something has actually DROPPED or a port is bus-off, so a
freshly booted board prints no counters at all, and a condition can yield zero
samples that read as zero activity. **Any arm that compares counters across
conditions must hold ONE serial session open for all of them.**

**A SEGMENT WITH NO ACKNOWLEDGER MAKES ITS OWN ERRORS.** The board emits four
diagnostic pages a second. With nothing on the vehicle segment to ACK them,
each retries until abandoned:

| | vehicle bus errors/s | txDropped/s |
|---|---|---|
| nothing else active on the segment | **3,649.1** | 4.0 |
| one adapter present in normal mode | **0.0** | 0.0 |

912 bus errors per dropped frame. So **every arm must keep an acknowledger
present and unchanged across all of its conditions** — otherwise the
difference between two readings includes a change in who was acknowledging,
and an error rate that is a property of the bench gets reported as a property
of the firmware. A listen-only reading is only about the segment if the set of
acknowledging nodes does not change.

Two corollaries worth stating separately:

- **`veh(rx= tx= ...)` on the `LOSS` line is `rxDropped`/`txDropped`, not
  received and transmitted counts.** Every field on a LOSS line is a loss, so
  `rx=0` is the *healthy* value. Misreading it as "received nothing" produced a
  confident claim that the vehicle port was deaf.
- **A board that has stopped transmitting also reports zero errors.** The two
  are indistinguishable in the board's own counters, so a zero needs a second
  instrument on the wire to mean anything.
  `projects/vtrux/notes/artifacts/interposer-firmware/bench_vehicle_errors.py`
  refuses to give a verdict unless its acknowledger actually heard the board.

## Bringing it up on hardware

Three stages, cheapest first. **Stage 0 and 0a passed 2026-09-10**; Stages 1 and
2 are not run, and both need dongles.

> **Resolve the board from its MAC before every flash, never from a port
> number written down earlier.** Two ESP32s enumerate here and both report
> `VID:PID=303A:1001`, so nothing in the description tells them apart. The
> interposer is the S3 `3C:0F:02:F0:4D:30`; `D4:F9:8D:1D:0D:74` is the C3
> WiCAN and belongs to another session -- flashing it is the one
> unrecoverable mistake on this bench. On `NICKY-XPS` it is **S3=COM3,
> WiCAN=COM4**, per the measured map of 2026-09-19 and re-confirmed
> 2026-10-04; COM7/COM8 in older notes is the **laptop's** mapping, not a
> superseded one for this machine. Numbers are per-machine, so resolve
> from the MAC rather than carrying one across.
>
> ```bash
> py -3.14 -c "import serial.tools.list_ports as l; [print(p.device, p.serial_number) for p in l.comports()]"
> ```
>
> `serial_number` is the MAC and reading it does not open the port.

### Stage 0 -- board self-test, no dongles

Jumper **CAN1H<->CAN2H** and **CAN1L<->CAN2L** (the vendor's `ping_pong`
wiring), nothing else connected. Termination works out by accident: 120 ohm on
each port in parallel is the 60 ohm a segment wants.

```bash
py -3.14 -m platformio run -d charge-interposer/firmware -e selftest -t upload
py -3.14 projects/vtrux/notes/artifacts/interposer-firmware/capture_serial.py \
    --port COM3 --out stage0.txt --until "checks,"
```

Use `capture_serial.py` rather than `pio device monitor`. The whole test runs
inside `setup()` and is over in 27 s, so a monitor attached after the upload
misses it; the script opens the port first and only then pulses reset.

> **Never flash the bridge env with those jumpers fitted.** It would forward
> CAN1 -> CAN2 straight back onto its own wire and storm.

This is the step that matters most right now, because **the two port drivers are
the only completely unexercised code here**. It checks identity in both
directions (standard, extended, `0x1FFFFFFF`, DLC 0-8 -- the MCP2515 ID
encode/decode is hand-written and is the most likely place to be wrong), then
ramps throughput at 200 / 864 / 1500 / 2250 / 3000 fps each way and reports
sent / rejected / received / lost plus `send()` cost in microseconds.

The `send()` figure is the direct test of the claim that motivated the custom
driver: coryjfowler's blocking `sendMsg()` costs ~300-350 us per call at
500 kbps. This one should be the SPI load time and nothing more.

#### Stage 0 result, 2026-09-10 -- 18 checks, 0 failed

Full capture in `projects/vtrux/notes/artifacts/interposer-firmware/stage0_selftest.txt`.

Identity passed in both directions for every probe, including the two most
likely to expose a hand-written driver: `0x1FFFFFFF` (max extended ID) and
DLC 0. All DLCs 0-8 round-tripped intact both ways.

| Target fps | Actual | Sent | Rejected | Received | Lost |
|---|---|---|---|---|---|
| 200 | 200 | 400 | 0 | 400 | 0 |
| 864 (charging) | 864 | 1728 | 0 | 1728 | 0 |
| 1500 | 1502 | 3000 | 0 | 3000 | 0 |
| 2250 (generator) | 2253 | 4500 | 0 | 4500 | 0 |
| 3000 | 3004 | 6000 | 0 | 6000 | 0 |

Identical in both directions. Final counters: both ports `rx=15644 tx=15644`,
`rxDrop=0 txDrop=0 err=0 busOff=0`. MCP rings peaked at 0 of 64 and the TWAI RX
queue at **1** of 64 -- so the depth-64 choice is untested by this run, which
never stalls the way a flash erase would. It is not evidence the default of 5
would have sufficed.

`send()` cost, the number the custom driver exists for:

| | avg | max |
|---|---|---|
| TWAI | 4-6 us | 24 us |
| MCP2515 | 1 us | 12 us |

Against coryjfowler's ~300-350 us of blocking per call, that is the claim in the
design rationale confirmed on silicon rather than inferred.

### Stage 0a -- controller and physical-layer diagnostic

```bash
py -3.14 -m platformio run -d charge-interposer/firmware -e diag -t upload
```

Flash this when Stage 0 fails. On a two-node bus a single break makes **both**
directions fail identically, because each node needs the other to ACK before
either reports success -- so a plain cross-port failure cannot tell a dead
driver from a loose wire. This separates them:

- **Phase 1** puts each controller in its own internal loopback (MCP2515
  `REQOP=010`; TWAI `NO_ACK` + self-reception). No wire is involved at all, so a
  pass here means the driver is sound and the fault is physical.
- **Phase 2** tests the wire. Raw GPIO on CAN1 TXD/RXD checks that transceiver
  drives and echoes; sampling CAN1 RXD while the MCP2515 retries a frame forever
  checks that bits physically arrive from CAN2; and a `NO_ACK` transmitter
  paired with a listen-only receiver gets frames across in one direction with
  nothing ACKing on the bus.

Result 2026-09-10: 18 checks, 0 failed, in
`projects/vtrux/notes/artifacts/interposer-firmware/stage0a_diag.txt`. The bit-level
sample showed 8689 transitions in 50 ms, and the one-way frame test delivered
10 of 10.

### Stage 1 -- two dongles, transparency

**Passed 2026-09-11.** One dongle per segment. On `L-EEHTRBPN2` that is a PEAK
**PCAN-USB FD** (`PCAN_USBBUS1`) and a **Kvaser Leaf Light v2** (channel 0) --
different drivers, so they coexist. An earlier version of this file named the
Innomaker `gs_usb`; that dongle is not on this bench. Both are auto-detected by
`bus.py`, which already carries the pcan bus-off `auto_reset` handling.

Driven by `projects/vtrux/notes/artifacts/interposer-firmware/stage1_transparency.py`,
which has four phases:

| Phase | What it does |
|---|---|
| `probe` | Dongle-only link check. Safe with the Stage 0 jumpers fitted, and run **before** flashing the bridge: if the dongles hear each other with the board not bridging, the segments are shorted together and the bridge image would storm. |
| `xparent` | The transparency test. Corpus sweeps ID 0x000/0x7FF/0x1FFFFFFF and DLC 0-8, compares every field, both directions. |
| `order` | Frame ordering in isolation, with switches to hold identifier and DLC constant independently. |
| `regress` | Standing same-identifier ordering check. **Run after any change to a port driver.** |

Check termination with a meter here rather than assuming: board 120 ohm plus
whatever each dongle fits.

#### Result

Delivery is clean. 88,000 frames each way in one uninterrupted session, zero
lost, byte-identical, and the board's own `v2c`/`c2v` counters reconciled
**exactly** with the externally sent count on every run.

| Offered fps | Delivered (both directions) |
|---|---|
| 100 / 200 / 500 / 1000 | 100 % |
| 2250 (measured real bus load) | 100 % |
| 3000 | 100 % |
| 4000 | 100 % |

The `LOSS` line was independently validated by overloading a deliberately
throughput-limited build: it reported `chg tx=808 bridge=808` against an
external count of exactly 808 missing frames. **The drop counters can be
trusted.**

The TWAI RX queue peaked at **1** of 64 even at 5000 fps, so depth-64 remains
unproven, exactly as after Stage 0.

#### Frame ordering: a bounded, deliberate exception

The MCP2515 egress direction transposes frames of **different
identifiers**.

**"ALWAYS BY EXACTLY ONE POSITION" WAS WRONG** (corrected 2026-10-04).
That row read `always +/-1, never 2`, and it was a generalisation from
synthetic bench traffic. On 10 s of real truck traffic the host L2 model
moved **469 of 22,117 frames by more than one position, the worst by
36**.

That count MOVES with the driver, by a few frames, and the live source is
`test_l2_bridge[replay]`, which prints it. On this capture: 460 before the
same-identifier hold, 456 with the hold (it removes the 2 same-identifier
transpositions and 4 frames with them), 469 once `drainTx()` stopped
refilling a buffer the completion loop had not yet resolved -- that one
makes a buffer wait up to a pass longer, which moves a few more frames
across identifiers. The worst displacement is 36 throughout, and the
per-identifier order is 0 out of order for all three. The mechanism below says why a bound of one was never justified: a
frame in the lowest-numbered buffer is not overtaken once, it can be
overtaken repeatedly while the other two are refilled.

| | |
|---|---|
| At 2250 fps, synthetic | ~1 in 10,000 frames |
| At 4000 fps, synthetic | ~13 in 10,000 frames |
| Displacement, synthetic | +/-1 |
| Displacement, real traffic (host model) | 469 of 22,117 beyond +/-1, max 36 (moves a few frames with the driver -- see below) |
| Same identifier | guaranteed by the hold (spec 2), **not yet implemented on silicon** -- see below |
| TWAI egress direction | never |
| Uniform DLC | never reproduced; frame-length variation is required |

**The cross-identifier exception is left in place on purpose.** Receivers
demultiplex by identifier, so two *different* identifiers arriving a position
apart is indistinguishable from ordinary bus arbitration and changes nothing.

**The same-identifier case is a different matter, and this paragraph used to
get it backwards** (corrected 2026-10-04). It said the one place order carries
meaning -- `0x18EFC000`, which is paged (`pageKey()` in `machine.h`) -- was
"precisely the case that is preserved", meaning preserved *by the chip*. It is
not. p15 s3.2 selects by TXP then highest buffer number, with no reference to
the identifier, so two frames of one identifier resident at once can leave in
either order. Same-identifier order is preserved by the **driver's hold**
(spec 2): at most one hardware buffer may carry a given identifier, and a
successor waits at the head of the ring until its predecessor's outcome is
known.

That hold is implemented and passes on the host model. **It has not been run
on silicon**, so nothing here is yet bench evidence. The `regress` phase and
`test_sameid_hold` exist to keep it that way once it has been.

Serialising `drainTx()` to a single TX buffer does eliminate the transposition,
and was tested: it costs the throughput ceiling, dropping 0.2 % at 4000 fps and
20 % at 5000 fps against a real bus load of 2250 fps. That trade was rejected.
**Unsourced: no artifact or run log found, 2026-10-04.** Those two figures
appear nowhere in the project but this sentence, and the stimulus behind them
(DLC distribution, identifier count, service period) is not recorded, so the
run cannot be reconstructed. Both rates do imply the same delivered ceiling of
about 4000 fps. The host model puts a single-buffer `drainTx()` at 3396 fps
with DLC 8 and 4465 fps with DLC 4, bracketing that, and identifier count makes
no difference -- consistent with the figures, but not a validation of them
(`projects/vtrux/notes/artifacts/interposer-firmware/sameid-hold-model/`).

**The same-identifier row is not evidence yet** (corrected 2026-10-04,
spec 2). It read "never -- 30,000 frames, zero swaps". The archive holds one
run, `projects/vtrux/notes/artifacts/interposer-firmware/stage1_sameid_regress.txt`:
**10,000 frames in each direction, not 30,000**, and only the
vehicle-to-charger direction is the one that transposes, so 10,000 bear on
the question. The DLC *was* varied -- `phase_regress` does that
unconditionally and says why -- so that objection does not apply. But the
**offered rate is not recorded anywhere**, and `--rate` defaults to 200 fps,
far below any rate at which the effect has been seen. At 2,250 fps the
different-identifier rate of ~1 in 10,000 predicts about one swap in 10,000
frames, so zero separates nothing.

Spec 2 now states this as unconfirmed, and spec 12 entry 2 carries the bench
re-run that would settle it. **That arm was re-specified on 2026-10-04**: it
previously read "same identifier, 4,000 fps, at least 50,000 frames, rate
recorded", and 4,000 fps was not reachable -- with the hold no service period
reaches it for a single identifier, and even with the hold removed the modelled
wire tops out near 4,050 fps for DLC 8, so the arm sat within about 1 % of
capacity before the interposer's own diagnostic frames. As approved it is now:
one identifier **that the core does not read**, a sequence number in every
payload at **DLC 3 or more** so the counter fits, offered in back-to-back
bursts of 20 (the shape of the VCU's `0x18EFC000` page burst) at a mean of
2,000 fps, at least 50,000 frames, with the offered rate, burst shape and DLC
recorded. The burst shape is **confirmed on the vehicle side** and recorded
with the result, not taken from what the stimulus tool intended to send. It
passes on zero out of sequence and zero dropped on the charger side, with the
board's completion-order witness agreeing. Both ordering phases now print their full stimulus so this
cannot recur.

**The arm was modelled before being booked, and it can fail** -- an acceptance
check that passes either way is not a check. Over 50,000 frames at a 60 us
service period, the host model gives 5,000 inversions without the hold and 0
with it, both with nothing dropped. Two details that matter for running it:
the burst shape is load-bearing (the same rate offered *steadily* gives 0
inversions even without the hold, so a run whose bursts were smoothed would be
the check that cannot fail -- which is why the shape is confirmed from the
wire), and the sequence counter must fit the DLC -- 50,000 frames needs three
payload bytes, so DLC 3 is the floor. Raw output:
`projects/vtrux/notes/artifacts/interposer-firmware/sameid-hold-model/`.

**THE MECHANISM IS ESTABLISHED** (2026-10-04; this section previously said
it was not). DS20001801J p15 s3.2, confirmed in the extracted text and on
the rendered page: transmit priority inside the chip "is independent from,
and not necessarily related to, any prioritization implicit in the message
arbitration scheme built into the CAN protocol". Selection among the chip's
own buffers is TXP first, then **highest buffer number** on a tie. The
driver never writes TXP, so every buffer sits at 00 and the tie-break
always decides. Identifiers arbitrate against *other nodes* once a buffer
is already driving SOF -- a different mechanism at a different boundary,
and conflating the two is what made the behaviour look identifier-
dependent.

This also explains the displacement tail that "always one position" missed:
the tie-break is static, so a frame in TXB0 can lose *every* comparison
while TXB1 and TXB2 are refilled around it.

**SPEC 2 NOW FORBIDS SAME-IDENTIFIER REORDERING OUTRIGHT** (user,
2026-10-04), rather than relying on the chip to preserve it -- the host
model showed one adjacent `0x3FE` swap in 10 s of truck traffic, so the
reliance was unfounded. The driver must guarantee the chip never holds two
frames of one identifier at once: a frame whose identifier is in any
transmit buffer stays at the head of the queue and nothing behind it is
loaded until that buffer is free, with a buffer being given up on counting
as holding its identifier until the outcome is known. **Not yet
implemented** as of this edit; the unmodified baseline has been captured
first so the cost can be measured against it.

The historical note below is kept because the reasoning in it was wrong in
an instructive way. The
generator-inhibit review session proposes the mechanism concretely
(2026-10-04): `drainTx()` loads TXB0, TXB1, TXB2 in ring order and never
writes TXP, so with equal priority the higher-numbered buffer transmits
first, and whether TXB0 has already started depends on frame length -- which
is why uniform DLC never reproduced it. A second bench arm tests that by
giving the buffers strictly descending TXP in load order. Recorded as
observed, not explained.

**Bridge latency is unmeasured.** Two dongles with independent clocks cannot
give a trustworthy one-way number. Only delivery and ordering were measured.

### Stage 2 -- the full three-way bench

```
vehicle_sim --[Kvaser]--> CAN1 | BOARD | CAN2 <--[Innomaker]-- charger_sim
     \_____________ physics link (virtual, localhost) _____________/
```

`run_scenario.py --external-interposer` sets up both segments and the scenario
but does not spawn `interposer_sim.py`, leaving the middle to the board:

```bash
py -3.14 charge-interposer/run_scenario.py evap-override \
    --external-interposer --serial-port COM3 \
    --vehicle-transport kvaser --charger-transport gs_usb
```

It captures the board's serial output into `<scenario>.interposer.log`, so the
existing `ev()` / `no_ev()` / `final_state()` expectations evaluate against real
hardware unchanged -- `machine.cpp`'s `eventName()` returns the same strings
`machine.py` logs.

**That claim was false for every event that carried a value, until
2026-10-13.** `eventName()` returns a static string and cannot interpolate,
so where `machine.py` wrote the value into its prose the two cores could not
match -- and nothing compared them, so they drifted. `boot-mid-session`
passed on the sim and failed at its first bench run on the board for exactly
this: the model wrote "already %d min old", the board "already running".
`top of charge reached` had drifted further still, the model's prose being
vmax, vmin, chg_max in amps against the board's fields vmax, chg_max in
centiamps, vmin.

The rule now (reviewer's ruling, 2026-10-13): **an event's name is fixed
text and its values go in `a`/`b`/`c`, in the board's order and the board's
units.** `machine.py`'s `_log` takes the fields, `machine.events` entries
carry them as `.a`/`.b`/`.c` while staying three-element tuples for their
existing readers, and `test_machine.py` pins the fields of the events it
reaches. Comparing the two cores' fields on the same input belongs to the
reference-vs-port differential, not here.

**`--time-scale` is forced to 1.0**, and the flag says so rather than warning.
The board runs on its own `millis()` and cannot be sped up: at scale 20 the sims
reach the evap ceiling in 4.5 s of wall time while the board is still 55 s short
of its 60 s arm delay, so it correctly refuses to override and the scenario
"fails" for a reason that has nothing to do with the firmware. Real-time runs
cost ~2-3 minutes each, which is what the synthetic scenarios suggest (override
at 90 s, done by 150 s). Long-run charge regression stays offline in
`regress.py`.

## Test

Two levels. Both were run on 2026-09-09 and both passed.

**1. Constants cross-check** -- no compiler needed, catches the port bug that is
both most likely and most silent (a mistyped threshold compiles fine and just
charges to the wrong voltage):

```bash
py -3.12 charge-interposer/firmware/test/host_diff/check_port.py
```

> 21 config fields, 22 identifiers, 2 payloads, 15 extractors -- all agree.

**2. Differential trace test** -- the real verification. Runs both cores over
the same real capture and diffs every emitted frame and state transition:

```bash
# generate trace + golden from the Python reference
py -3.12 .../make_golden.py <capture.log> --name NAME --bus 3 \
    --outdir projects/vtrux/notes/artifacts/interposer-firmware

# build and diff (needs g++; on L-EEHTRBPN2 use WSL)
g++ -std=c++11 -O2 -I src -o /tmp/hr test/host_diff/host_runner.cpp src/machine.cpp
/tmp/hr <NAME>.trace | diff - <NAME>.golden
```

**3. Synthetic scenarios** -- `make_synthetic.py`, same trace/golden format.

`syn_repeat_superseded` (added 2026-10-05) is the only trace that drives spec 4.1's superseding clause -- the VCU's own page 00 landing inside a repeat and ending it. Before it, all 24 traces that produce a repeat let it run to its full 20 frames, so the branch the clause is written around had no trace at all, while the real bench hits it every time in `fault-during-override`. The frame is placed at a fixed 500 ms into the burst, so the achieved count is a deterministic 9 and a golden can hold it; on the bench the burst and the VCU's reaction are both 1,000 ms and the count is jitter.

`syn_repeat_replaced` (added 2026-10-05) drives the paragraph added to spec 4.1 that same day: a repeat starting while another is still being sent replaces it, the earlier one stops there, and the frames it already sent stand. The base is `syn_above_ceiling_start`, because that override is decided **from a tick** -- the 75 s arm delay elapsing with no page-00 frame on the boundary -- and a frame-decided override opens no burst for anything to interrupt. A charger inverter fault 500 ms into the resulting CHARGER repeat then starts the Low Power one (spec 6), giving `REPEAT ended: 9 of 20, replaced by a new repeat` followed immediately by the new `REPEAT`, and a final `synth` of 29 = 9 + 20. **The arm delay runs from charge establishment, not from the VCU's command**, so the fault is timed off 75.1 s and not off the VCU's Low Power at 3 s; timing it off the command put the fault 2.4 s after the burst had already completed and produced two COMPLETE repeats and no replacement at all. As with the superseded trace, 500 ms rather than the 1,000 ms boundary is deliberate: at the boundary the burst's own end and the replacement are a dead heat and the count becomes jitter.

`host_runner.cpp --events` dumps the core's event log as `X <t_ms> <a> <b> <c> <name>` instead of its emissions. It is NOT part of the L1 gate: the two cores word their logs differently by design, so only the numbers are comparable. It exists so `EV_REPEAT_END` can be checked against `machine.py`'s -- both report 9 of 20, superseded, at t=100500 on the trace above.
No real capture reaches a release, because in reality the charger shut down
at the ceiling and the capture ended. So the release (transparency, spec
5.2), every trip path, the arm-delay wait and the above-ceiling start were
untested by the captures. These drive them deliberately; `synth` counts the
repeats of the VCU's standing page-00 command (spec 4.1), the only frames
the core originates -- 20 per release, trip-during-override or
override-from-a-tick, and 0 everywhere else.

```bash
py -3.12 .../make_synthetic.py --outdir projects/vtrux/notes/artifacts/interposer-firmware
```

**4. Regenerating a golden after a spec change** -- `regen_golden.py`.
Replays an EXISTING `.trace` through `machine.py` and rewrites only the
`.golden`. Needed when behaviour changes and the capture-derived goldens have
to be refreshed: the original capture may not be on this machine, and
re-deriving the trace would change the input as well as the expected output,
which would hide a port bug instead of exposing one. `--check` reports what
would change without writing.

```bash
py -3.14 .../regen_golden.py <...>/interposer-firmware/*.trace --check
```

## Diagnostic instrumentation (compiled out of every image)

Two measurement facilities live in `port_mcp2515.{h,cpp}` behind compile-time
gates. **No PlatformIO environment defines either**, so none of the five
firmware images contains any of it; `test/build_check.py` asserts that, with a
positive control that fails if the macro it looks for has been renamed away.
Only the host test runner defines them, one test each.

| Gate | Test | What it records |
|---|---|---|
| `INTP_TX_TRACE` | `test_tx_stall_trace` | Per `drainTx()` pass: what each of the three hardware buffers was found doing, ring occupancy, frames loaded and aged. 65,536 passes x 16 bytes, static. |
| `INTP_ORDER_WITNESS` | `test_order_witness` | Per completion: the load sequence number, buffer, (id, ext), pass number and outcome. `INTP_WITNESS_N` entries (512 by default) x 16 bytes, static. |

The **completion-order witness** exists because spec 12 entry 2 reads frame
order from a sequence number in the payload on the charger side, and that can
only say what *arrived*. A dongle that drops or reorders on capture is
indistinguishable, from the charger side alone, from the driver doing it --
the confusion that cost a day on the L3 shortfall, where four separate
"firmware failures" were all undrained receive buffers in the measuring
equipment. The witness is the board's own view, so the two can be compared.

Three properties it is built around, each with its own check:

- **It stops when full and never wraps.** A ring would discard the start of
  the run, which for an ordering question is the part that matters, and the
  result would read as a complete short witness rather than a truncated long
  one. `witnessFull()` and the gap between `witnessCount()` and
  `witnessCompletions()` say how much was not stored.
- **Multi-completion passes are counted, not resolved.** When two buffers are
  found complete in one pass the driver cannot know which reached the wire
  first; it sees both only after the fact. Those passes are counted in
  `witnessMultiPasses()` and their entries share a pass number, so the
  ambiguity is visible instead of being silently replaced by the loop's
  iteration order.
- **It keeps its own copy of (id, ext), taken at load time**, rather than
  reading the same-identifier hold's `tx_held_id_`. Using the hold's
  bookkeeping to witness whether the hold worked would let a bug in that
  bookkeeping answer for itself.

It does not perturb what it measures: `test_l2_bridge[replay]` built with and
without `INTP_ORDER_WITNESS` produces **byte-identical output** -- 22,316
delivered on the charger wire either way.

### The hold's deferral count, in the same dump

The dump also publishes **the same-identifier hold's deferral count**, which
is what makes spec 12 entry 2's arm scoreable: the arm passes only with that
count above zero. An in-order result with no deferral did not exercise the
hold at all, whatever its ordering says -- and on 2026-10-05 the then-approved
arm turned out to be exactly that once the stimulus was modelled physically.
The line reads

```
  HOLD: deferrals(passes)=1234
```

and when the count is zero it says so in words, because a reader scanning the
dump for a failure would otherwise take the clean order record below it at
face value.

**It counts PASSES, not frames** -- section F item 27 of the review tracker
asked which, and this is the answer. `drainTx()` re-tests the blocked head on
every pass and the driver's own comment puts that at about six passes per
frame at truck rate, so the figure is a yes/no instrument and not a rate:
nothing should be divided by it. `txFramesDeferred()` is the per-frame
companion -- frames deferred at least once, incremented when the frame
finally loads -- and it is deliberately **not** in the dump, so the published
number is the one the arm is scored on rather than one that invites
arithmetic.

**The counter is in every build; only the dump is gated.** `tx_hold_defer_`
is four bytes and one unconditional increment inside `drainTx()`, outside
both `INTP_TX_TRACE` and `INTP_ORDER_WITNESS`, so the bridge and truck images
count deferrals as well and simply never print them. `witnessDump()` is what
`INTP_ORDER_WITNESS` compiles in, which is why spec 12 entry 2 reads the count
from the witness build.

## The truck build (spec 8.3)

`[env:truck]` is `[env:esp32-can-x2]` with `-DINTP_NO_SERIAL=1`. Every print
in `main.cpp` goes through `INTP_PRINTF` / `INTP_PRINT` / `INTP_PRINTLN`,
which compile to nothing under that flag, and the `diag` line's whole
formatting block is `#ifdef`-ed out with it. The calls are removed at compile
time rather than guarded at run time, so there is no CDC write left for an
attached-but-not-reading USB host to block the bridge loop on.

The bench and bring-up builds keep their prints and bound the block instead:
`INTP_SERIAL_BEGIN` sets the CDC TX timeout to `INTP_CDC_TX_TIMEOUT_MS`,
1 ms, so output a host is not reading is dropped rather than held (spec 8.3,
user 2026-10-14). `diag`, `selftest` and `slcan` set the same value beside
their own `Serial.begin()`.

**The setting is 1 and 8.3's bound is 2 ms** (`6505ded27e460190`), and they
are different numbers. The macro is what `setTxTimeoutMs()` gets, and in
that loop the timeout doubles as the retry counter, so 1 buys exactly one
pass; 2 ms is what the spec allows the episode that one pass produces, after
E10 measured it at more than a millisecond (below). **Do not raise the macro
to 2 to match the bound** -- nothing asks for a second pass. **And 1 and not
0**: 0 makes the counter wrap on a `uint32_t` so the write never returns at
all -- the comment at `INTP_CDC_TX_TIMEOUT_MS` in `main.cpp` has the
mechanism and the measurement it came from.

**And the TX ring is sized, which the 1 ms timeout made necessary** (spec
8.3, reviewer 2026-10-14, found on the bench in step 4 item 2). 8.3 lets
output be dropped for a host that is **not** reading; a host that **is**
reading must get every line, and it was not: 42 of 1,250 lines were cut
mid-line, tail and newline missing, with `run_scenario`'s tap reading
throughout. 33 inside the first line of the 5 s status block, 223-257 B from
its start, and 9 in the `charger tx:` line at 468-504 B. E8's `6b54c67d`,
before the 1 ms timeout, has **0 splices in 211 complete blocks**.

The cause is the ring, and the timeout is what made it fatal. `begin()`
creates the TX ring at 256 B (`HWCDC.cpp:315`) and a status block is
430-515 B, written in one pass while USB-Serial/JTAG drains 64 B per IN
token -- so the ring **must** fill part-way through however fast the host
reads. `HWCDC::write()` then finds no free space and calls
`xRingbufferSend()` for 0 bytes, which on a `RINGBUF_TYPE_BYTEBUF` returns
`pdTRUE` without copying (the same fact that makes a 0 ms timeout hang), so
the pass makes no progress, `tries` goes 1 -> 0, `connected` latches false
and the rest of the line goes to `flushTXBuffer()`. The 256 B and ~500 B
clusters are the ring filling once and twice. At the old 100 ms default that
same pass cost one `delay(1)` and the next pass made progress, which
**resets** `tries` (`HWCDC.cpp:473`) -- so a reading host never reached 0,
which is why E8 shows none of it.

`INTP_CDC_TX_RING_BYTES` is 2048, set with `setTxBufferSize()` **before**
`begin()`, which is what makes it stick: `begin()` creates a ring only
`if (tx_ring_buf == NULL)`, so an existing one is left alone and nothing
buffered is discarded, while calling it afterwards would delete the ring
`begin()` had just made. `diag`, `selftest` and `slcan` do the same beside
their own `Serial.begin()`; `slcan` already had `setRxBufferSize(8192)`
there, against the RX form of this same failure.

**Why 2048 and not the ~11 KB the worst case would need.** One pass fits
with room -- 515 B of status block, ~140 B of diag line, and an event line
is at most ~154 B. `MAX_EVENTS` is 64, so a pathological pass could still
overrun it and splice exactly as it does today; this bounds the realistic
pass and does not prove the pathological one. A ring large enough for 64
events would hold **minutes** of output for a host that had stopped reading,
and the tester's 8.3 method scores a stop only when a hole appears in the
board's own `t=` sequence -- so it would lengthen the buffer-fill times E10
measured and could stop a hole appearing at all. `tx_timeout_ms` is
unchanged at 1, so what E10 measured for a host that is not reading is
unchanged.

Verified from the images, not the source (2026-10-03):

| | `esp32-can-x2` | `truck` |
|---|---|---|
| `"Vtrux charge interposer"` | present | **absent** |
| `"diag st="` | present | **absent** |
| `"BRIDGE DOWN"` | present | **absent** |
| flash | 353,824 B | 350,528 B |

The spec 8.2 diagnostic CAN frames are unaffected BY THE SERIAL REMOVAL --
`0x7F4`-`0x7F7` are how the truck build is observed, and `intp_serial` reads
0 in it, which is what identifies a truck image on the wire.

**Spec 2.3 does affect when they arrive** (user, 2026-10-13): the vehicle
port is in listen-only whenever the charger has been quiet for 2 s, and the
board sends no diagnostic frames at all in that mode. So on a truck the spec
requires the four frames once at power-up and then only during a charge. A
log of a drive shows nothing from the board, and that is the board working.
Its counters are read over USB serial on a bring-up build instead (8.3, and
spec 10 item 7).

**And until 2026-10-14 serial did not deliver on that** (reviewer).
`sendDiag()` returned on `vehicle.listenOnly()` before it reached the print,
so a bench or bring-up build printed one `diag st=` line at boot and then
none -- listen-only being the normal state, every E9 log on `6cacaf88` shows
exactly that, and the promise in the paragraph above was not kept. The gate
now covers the SEND only: in listen-only no frame goes out and the 1 Hz line
is still built and printed. The immediate `0x7F4` of a state change has no
line of its own, so in listen-only it does nothing at all.

**One subtlety in the printed line's `bridge_ok` flag, and it is the kind
that would not show up in the output.** The flag describes the interval since
the previous status FRAME (8.2), and `BridgeHealth::sample()` moves its
baselines when it answers. A printed line therefore calls `peek()`, which
answers without moving them. Had it sampled, an overflow during listen-only
would have been reported on a serial line nobody may be reading and then been
**absent** from the first frame after the switch -- the flag would read ok and
nothing else anywhere would differ. What to expect when reading a bench log:
in listen-only the printed flag stays cleared on every line until a frame
actually carries it, rather than clearing once and going green.
`test/host_unit/test_bridge_health.cpp` pins both halves, and its mutation is
a `peek()` that advances.

**THE ROUND IS WAITED FOR, BECAUSE THE SWITCH USED TO THROW IT AWAY**
(spec 2.3 at 4f5168ae66fd2c62, user 2026-10-13). `sendDiag()` only QUEUES the
four frames, and `setMode()` stops the driver, which **discards its TX
queue**. Until 2026-10-14 the switch ran on the next line, so the round never
reached the wire: the L2 tests with the fake modelling airtime put 0 of 4 out,
and a bench boot with an acknowledging adapter put 1 of 4 -- the single frame
already in the hardware TX buffer, which could complete only because
something was there to ACK it.

`setup()` now waits for the round before switching: until all four are sent
or 50 ms from the first transmit, whichever comes first, and a frame not sent
by then is dropped. **A frame counts as sent when the CONTROLLER reports it
transmitted**, which on CAN means another node acknowledged it -- not when
`send()` returns true, which only means the driver queued it. The number is
read from the driver's own queue, `msgs_to_tx` and `tx_failed_count`, rather
than counted from `TWAI_ALERT_TX_SUCCESS`: the alert is a bit, not a counter,
so completions between polls collapse into one and the count would read low.
`msgs_to_tx` includes the frame in flight, and a frame leaves it by
succeeding, by failing, or by a bus-off recovery discarding it -- all three
are subtracted, so the arithmetic is exact whatever the alerts did and
**exact across a recovery inside the wait**. A discard appears in neither
`msgs_to_tx` nor `tx_failed_count`, so leaving that term out booked
discarded frames as sent, which was the one way this figure could
over-report. The count of
0-4 is on the `MODE` line and the boot line (8.3); the spec also wants it on
a status page once there is one, which is the Wi-Fi page after the move to
the WiCAN codebase and nothing here. No CAN diagnostic frame changes, so
`DIAG_FW_VER` stays 9.

**With nothing acknowledging, the 50 ms deadline is the only thing that ends
the wait.** Automatic retransmission means an unacknowledged frame raises no
`TWAI_ALERT_TX_FAILED` at all -- it simply retries -- so a quiet vehicle
segment spends the full 50 ms and reports `round=0/4`. That is the specified
outcome, not a fault.

The bring-up builds' 5 s status block carries a `MODE` line for this:

```
  MODE veh=listen-only core wants listen-only | switches=1 refused=0 suppressed=0 | boot=11410us switch=319/319us(last/max) drained=0
```

| Field | What it is |
|---|---|
| `veh=` / `core wants` | the PORT's actual mode and the CORE's wish, separately, so a refused switch is visible as the two disagreeing rather than as silence |
| `switches` / `refused` | completed and refused `setMode()` calls (it can be refused from RECOVERING) |
| `suppressed` | vehicle-bound frames `dispatch()` dropped because the port was in listen-only. Not booked as bridge failures |
| `boot=` | the power-up window: vehicle controller up in normal mode to the first switch into listen-only completing, the status round inside it |
| `switch=` | `setMode()`'s duration, last and worst; a mean would hide the one that matters |
| `round=` | how many of the power-up round of four the CONTROLLER reported transmitted, and how long the wait took. `0/4` on a segment with no acknowledger, where the wait runs the full 50 ms |
| `drained=` | vehicle frames found in the RX queue at a switch and handed to the core before the teardown. **Zero is the normal case** -- the loop drains RX every iteration, so the queue is empty at a switch unless the loop had fallen behind. Non-zero means it had. It says nothing about where the drain is placed |

Measured on the bench 2026-10-13 (the loan entry in SeaDrive
`firmware/bench-holder.md` has the runs): the boot window was 11,409-11,424 us
over nine boots, with no traffic dependence, and `setMode()` 234-340 us over
nine switches in both directions.

**Those boot-window figures predate the round's wait.** They were measured
when the switch followed the round immediately, so they are the cost of
bringing both controllers up and nothing else. Re-measured with the wait in
place at E9 (2026-10-14, image `6cacaf88`; the tester's run folder is
`runs/bench_powerup_round_e9_20261014T0255/`): **12,154-12,218 us with
something acknowledging the round** over five boots and **61,413-61,428 us
with nothing acknowledging** over three, against a wait bounded at 50 ms.
`setMode()` came out 298-302 us and is unaffected by the wait, being the
teardown and reinstall alone.

**A boot with no host reading the USB port reported about 100 ms more than
that, and the cause was the CDC write rather than anything on the CAN side.**
Same run: 161,383 us with nothing acknowledging against 61,4xx, and 112,383
and 119,383 us with an acknowledger against 12,1xx-12,2xx. Arduino core
3.2.1's `HWCDC::write()` spends `tx_timeout_ms` passes of `delay(1)` on a TX
ring buffer that has stopped draining and only then latches its `connected`
flag false, so a host that is attached but not reading costs exactly one
100 ms stall per boot. The write it lands in is the `vehicle port ->
LISTEN-ONLY` line inside `applyVehicleMode()`, which is inside the measured
window -- and the published fields localise it without reading the source,
because `round=` and `switch=` are normal on those boots and `drained=0`,
which leaves that print as the only code in the span. Spec 8.3 now forbids
the wait and `INTP_CDC_TX_TIMEOUT_MS` sets the timeout to 1 ms on every
serial-enabled build -- the setting stays 1 although the bound is now 2 ms,
see above. **E10 settled it on silicon and the figures above do hold whether
or not a host is reading** (`charge-interposer-test-evidence.md`, 8.3).
Three sets, and they are not interchangeable:

| Boot | ACKed | No acknowledger |
|---|---|---|
| baseline: RTS reset, host reading | 12,163-12,238 us | 61,422-61,536 us |
| fix, host attached, buffer full, RTS reset | 12,209-12,216 us | 61,357-61,386 us |
| fix, no host at the boot (a COM3 close resets the board) | 13,386 us every boot | 62,386 us every boot |
| **control `6cacaf88`, same close-boots** | **112,382 us (+100,164)** | **161,383 us (+99,891)** |

6/6 PASS on both fix classes; the check fails the control on both, so it can
fail. The buffer-full RTS resets leave no residue at all.

**The residue is the close-boot class**: +1.148 ms over the baseline's max
and +1.223 ms over its min when ACKed, +0.850 ms over the max with no
acknowledger -- which is what moved 8.3's bound from 1 ms to 2. Read it as
an extra over a baseline that already contains an RTS reset with the host
reading, not as an isolated timing of the wait; and note it is more than the
single `delay(1)` accounts for on its own, which is not explained anywhere
and is inside the bound's margin. Bridging is bounded the same way: after
the buffer fills, a max excess of 0.755 ms over 185,854 gaps against a
2.131 ms limit, where the control shows one +99.284 ms at the first print
after the fill. The unexplained 111,388 us recorded on 2026-10-13 was this.

### Why a repeat ended -- the `c` field of `REPEAT ended`

Spec 8.3 requires the end line to carry the number of frames sent and the
reason. The line is 8.3's own `event a= b= c=`, so the reason is the code
below: `a` is the frames that went, `b` the number announced, `c` this.
**Published here because that is what makes a bare number satisfy "the
reason"** (reviewer, 2026-10-13).

| `c` | Name | What happened |
|---|---|---|
| 0 | `REPEAT_COMPLETE` | all announced frames went out |
| 1 | `REPEAT_SUPERSEDED` | spec 4.1: the VCU's own page 00 ended it |
| 2 | `REPEAT_RESET` | the session was torn down mid-burst |
| 3 | `REPEAT_REPLACED` | a newer repeat took over mid-burst |

**Appended, never inserted.** Saved logs and traces carry these numbers, so
renumbering silently re-reads every one of them -- which is why `REPLACED`,
added 2026-10-05, is 3 and not 2. The table is in `machine.h` as
`enum RepeatEnd` and in `machine.py` as the four `REPEAT_*` constants, and
both must agree on the numbers, not merely on the order.

### An event's text must be true in every state it can fire from

`EV_FLOW_DROP` printed `VCU dropped the flow bit: override ended,
transparent`. It fires on `prev_flow && !vcu_flow_ && active()`, and
`active()` is **MONITOR, OVERRIDE and HOLD** -- so it claimed an override
had ended from two states where there was none to end. The scenario that
found it, `charger-fault-stop-flow-first`, never overrode at all (reviewer,
2026-10-14, item 6 scenario 2). It now reads `VCU dropped the flow bit:
session ending, transparent`; the `VCU dropped the flow bit` prefix is
preserved because the harness keys match on it.

**The audit this prompted, over every string in `eventName()`.** Checked by
asking, for each, which states it can be emitted from and whether its text
claims otherwise. Three texts name a state or a posture and are correct:
`EV_HOLD_ENDED_BY_VCU` is guarded by `state_ == S_HOLD` and
`EV_OVERRIDE_ENDED_BY_VCU` by `state_ == S_OVERRIDE`, so each names the
state it actually fires from; `EV_HANDLE_PULL` fires from all three
`active()` states but claims only its destination, TERMINATED, which is
transparent from each of them. `EV_BOOT_LOCKED` claims `PASSTHROUGH until
the next session boundary` and can fire only in PASSTHROUGH, because
`_maybe_arm` is gated on `pilot_seen` -- spec 3 (A3), a deliberate ordering
whose comment records the bug that forced it, where arming before the first
pilot value left the core in MONITOR. `EV_RELEASE` describes HOLD's
behaviour and is reached only from OVERRIDE. The rest -- `EV_ARMED`,
`EV_VCU_CHARGE_AGAIN`, `EV_SESSION_OVER`, the three `EV_WAIT_*`, the two
`EV_ACCEPT_*`, `EV_OVERRIDE`, `EV_TOP_OF_CHARGE`, `EV_TRIP`, `EV_REPEAT`,
`EV_REPEAT_END`, `EV_PILOT_BACK`, `EV_CHG_SILENCE` and `EV_VEHICLE_MODE` --
describe an input, a decision or a field and name no state.

**And one the audit raised and the reviewer then ruled on.**
`EV_OVERRIDE_ENDED_BY_VCU` ended with `transparent` and goes to **MONITOR**,
which forwards unmodified but is armed: it can trip, and the next Low Power
can override again. That is a claim about the destination rather than about
where the event fires from, so it is a different fault from the flow-drop
one above -- the text was true of no state at all there, whereas this was
true of the frames and wrong about the posture. **Ruled (reviewer,
2026-10-14): `transparent` in this core names TERMINATED and PASSTHROUGH,
the postures that neither rewrite nor arm, so a line reaching MONITOR must
not claim it.** It now reads `override ended, armed`, in both cores.

### Why it tripped -- the `a` field of `TRIP`

`TRIP` is spec 8.3's ordinary `event a= b= c=` line, so the reason is the
code below and the board prints no reason text at all: `a` is this, `b` is
the stale BMS identifier on `BMS frames stale` and 0 on every other trip,
`c` is unused. **Published here for the same reason as the table above -- it
is what makes a bare number satisfy "the reason"** (reviewer, 2026-10-14).

Until 2026-10-14 the board printed `TRIP: charger fault` instead, from
2026-09-11 when the reference logged the text too. Both had moved to the
shared form at `7078b5e` / `817f7cf` and this line had not, so the harness
key stopped matching and **every board scenario that tripped failed while
the trip itself was correct** -- found on the bench in step 4 item 2.

| `a` | Name | What tripped it |
|---|---|---|
| 0 | `TRIP_NONE` | no trip; what `diag trip=` reads when the latch is clear |
| 1 | `TRIP_VMAX_HARD` | vmax over the hard ceiling |
| 2 | `TRIP_CELL_OVERVOLT` | BMS `cell_overvolt` |
| 3 | `TRIP_CELL_UNDERVOLT_RETIRED` | **never emitted** -- see below |
| 4 | `TRIP_BMS_EPO` | BMS EPO |
| 5 | `TRIP_BMS_ALARM_TYPE3` | BMS alarm TYPE3 |
| 6 | `TRIP_HVIL_OPEN` | HVIL open |
| 7 | `TRIP_CONTACTOR` | contactor not in a closed state |
| 8 | `TRIP_CHARGER_FAULT` | charger fault |
| 9 | `TRIP_CHG_STATE` | charger out of state 12 |
| 10 | `TRIP_BMS_STALE` | BMS frames stale; the id is in `b` |
| 11 | `TRIP_CHG_STALE` | charger frames stale |
| 12 | `TRIP_OVERRIDE_CAP` | override exceeded the session cap |
| 255 | -- | the reference's fallback for a trip text it cannot map; the board has no such case |

**Slot 3 is retired in place, not removed.** The `cell_undervolt` trip went
at B-7a (2026-10-03) and its number stayed, so every trip above it keeps the
value it already had in saved logs and traces. A decoder that meets a 3 is
reading a pre-B-7a log.

**What is pinned here and what is not.** The NUMBERS are `enum TripReason`
in `machine.h`, matched position for position against `machine.py`'s
`TRIP_KEYS` by `test/host_diff/check_port.py` -- that check is what stops
this table's codes and the wire drifting apart, and it is the only one. The
NAMES in this table are pinned by nothing, so they are documentation and
must be read as such.

`machine.cpp` used to carry the same names a third time, in `tripName()`.
Once the `TRIP` line stopped printing them nothing called it, nothing linked
it -- the strings were absent from every image at `91b360b2b0ef` -- and no
check ever covered it, so it was deleted on 2026-10-14. Two homes for these
names is the intended number: the numbering in the enum, which is checked,
and the prose here, which is not.

## Charger-segment transmit behaviour (spec 2.2)

With nothing acknowledging on the charger segment the MCP2515 retries
forever, so all three hardware buffers stay pinned and the 64-slot ring
fills behind them within ~30 ms. A charger coming back then received a burst
of up to 66 frames describing a charge that had already moved on, and
`send()` stamped every queued frame with timestamp 0, so none could be aged
out.

Now (`port_mcp2515.cpp`):

- every queued frame is stamped with the current `service()` tick;
- a frame older than `TX_MAX_AGE_MS` (100 ms, two periods of the 20 Hz
  command page) is **dropped rather than sent**, counted in `txAged()`;
- a hardware buffer holding a frame queued longer ago than that has its
  `TXREQ` cleared, so the controller abandons it, counted in `txFailed()`;
- **the 100 ms is one budget across both stages, counted from when the
  frame was queued** (user, 2026-10-15). The buffer's deadline is the
  frame's queue stamp plus the bound, carried over from the ring at load
  (`tx_queued_at_`), so the ring wait and the buffer wait cannot be spent
  one after the other. Until then each stage had its own 100 ms and they
  added up: on the bench in step 4 item 4 (`88566aee`, nothing
  acknowledging and then the charger returning) a frame reached the
  charger 188 ms after it was queued, about 100 ms in the ring behind the
  previous frame's retries and about 88 ms in the buffer, each stage
  inside its own limit at the time. A frame loaded at 99 ms of age now
  gets about 1 ms in the buffer; one loaded fresh still gets 100;
- the stamp is the `t_ms` the port was last given, so it is at most one
  loop pass old -- and `setup()` takes one service pass of its own before
  `applyVehicleMode()`, because that drain reaches `charger.send()` while
  the ageing clock would otherwise still read 0. A frame stamped 0 is
  older than the bound the moment the clock catches up, so without that
  pass the boot drain would discard the frames it exists to save. Reading
  `millis()` in `send()` instead would make the stamp exact at the cost of
  comparing two clocks; the port keeps one, the caller's, and the
  staleness can only age a frame out early, which is the safe direction
  for a rule that exists to stop late delivery (reviewer, 2026-10-15);
- retries are deliberately left **on**. One-shot mode would also discard a
  frame that merely lost arbitration, which on a healthy segment should be
  retried. Aborting on age keeps normal retries and still bounds staleness.

`intp_tx_fail` counts each failure **once**. It previously summed the ports'
own `txDropped()` *and* `main.cpp`'s `bridge_drops`, which `dispatch()`
increments for exactly the sends that already incremented the port counter --
so every failure was counted twice and the field read its 255 ceiling before
every bench run.

### Results (2026-10-03, later) -- 22 of 22 **byte-identical**

`syn_plug_out_then_pilot_reset` is the new pair, and it exists because the
differential was blind to a defect both cores shared.

`_session_boundary` logged only through `_goto`, so a `clear_safe` boundary
arriving while the core was already in PASSTHROUGH performed its whole reset
-- `chg_seen_charging`, `boot_locked`, `max_avail`, `learned_ilim` -- and
left no record of it. That is the ORDINARY ordering at a handle pull: the
charger reports the plug out and the VCU closes out first, and the pilot
timer only returns to 0 afterwards. Spec 6.1's most consequential boundary
was the one you could not see.

Nothing caught it. The unit suite reached that boundary only from SAFE,
which takes the state-changing path; the differential agreed because both
cores had the same omission, and no trace drove the case. The new pair
drives it, and also sends the VCU's real 20-frame page-00 burst rather than
a single frame, so the one-line-per-boundary collapse is compared too.

The boundary's RESET stays level-triggered and still runs on every frame of
that burst. `maybeArm()` does not look at the VCU's mode, so it is the
repeated clearing of `chg_seen_charging` that stops the core arming while
the VCU is commanding STAND_BY -- edge-triggering the whole boundary would
have changed behaviour, not just output. Only the log is an edge.

### Results (2026-10-03) -- 21 of 21 in the maintained set **byte-identical**

The spec 6 plug-out rule (2026-10-03) ends the override on the
`BELINV_vehicleConnected` 1 -> 0 edge. It fires on real truck frames:
`above83` goes TERMINATED at t=57902, **923 ms** before its flow drop at
t=58825, inside the corpus's 0.8-1.1 s band.

**`shutdownSource` 11 is deliberately not a trigger**, and the reason is
worth keeping. It was one in the first implementation, and `charging45`
showed why that is wrong: the original Bel unit latches 11 as a "last stop"
reason, **209 frames over 83 s, every one of them with the plug still
connected**. Triggering on it left the core transparent for the rest of the
session and swallowed the charger fault at t=178138
(`030101ff3b240000`, inverterFault). `charging45` keeps its `S 178138 SAFE`
under the flag-only rule, and `syn_source_11_plug_in` is the synthetic
case that holds that line. Measured population:
`projects/vtrux/notes/artifacts/interposer-review-2026-09-22/handle_pull_trigger_population.txt`.

### The earlier result (2026-09-30) -- 19 of 19 byte-identical

Re-run after the review's section A landed in the spec (2026-09-30). Two
capture goldens changed, both of them the intended behaviour change rather
than a port difference:

- `charging45` **lost** its `TERMINATED` at t=28661. That is the VCU's own
  startup transient, 3.2 s into MONITOR and well inside the 75 s arm delay;
  the old code accepted it and latched TERMINATED for the session before
  re-arming at t=92306. Spec 3 (A7) now waits instead, so the core simply
  stays MONITOR. The session still ends identically: SAFE at t=178138,
  PASSTHROUGH at t=179469.
- `above83` **gained** a `TERMINATED` at t=58825, where the VCU drops the
  flow bit (`00 00 03`). Spec 6 (A5) ends the override there instead of
  setting a teardown flag. Its STAND_BY at t=59077 is unchanged.

A third golden changed later the same day, when the boot fallback of spec 3
was fixed to not depend on frame order (the core now waits for the first
`0x18FFD8C0` value before arming):

- `evap80` **no longer overrides at all**. Its trace was rebuilt from the
  source capture to include the pilot timer, and the first value in the
  window reads **115 minutes**, so the core correctly stays in PASSTHROUGH
  for a session that was already running when the window opens. It is now a
  real-capture test of the boot fallback rather than of the override.

**Consequence worth knowing before trusting this suite: no capture-derived
case covers the override any more.** Only the synthetic scenarios reach
OVERRIDE. On the truck the evap ceiling arrives about two hours into a
charge, so any window containing it opens with a high pilot value unless it
starts at the session start -- which for `evap80` would mean a 7600 s trace
of roughly 4.3 M lines instead of 600 s and 339 k. `above83` cannot fill the
gap either: its whole session is 62 s, shorter than the 75 s arm delay.
**Real-frame override coverage lives in `regress.py`, not here**, and needs
no committed trace: `regress.py evap_80pct` (the case name is positional;
there is no `--case` flag) replays the whole 9,221 s session from plug-in,
where the pilot timer reads 0, so the core joins the session and overrides
the ceiling at t=7427.0 s (18,753 frames modified, verified 2026-09-30 --
it is 18,735 since the plug-out rule landed, see the `evap_80pct` row in
`../README.md`). Read the two together -- the differential proves the
C++ port matches `machine.py`, and `regress.py` proves the decision is right
on the truck's own bytes. Decided with the review session 2026-09-30.

**`known-divergence/` is gone as of 2026-10-03, and the set is 24 pairs.**
It held `syn_charger_silence_keeps_safe` and `syn_charger_stop_source_3`,
which reached the 19-vs-20 repeat-frame divergence of review item B-1:
`machine.py`'s `tick()` discarded `_trip()`'s return value, and `_trip()`
emits the burst's first frame itself, so that frame was built, counted in
`synth` and dropped. Fixed in both directions -- the fix is in `machine.py`,
the goldens were regenerated from it, and both pairs now diff identical.

### The older result (2026-09-17) -- all ten byte-identical

`test/host_diff/diff_all.sh` builds the runner and diffs every pair (on
L-EEHTRBPN2 run it under WSL with `MSYS_NO_PATHCONV=1`). The goldens now
include the diagnostic mirror frames (spec 8.1), so both real captures were
regenerated on 2026-09-17.

| Case | Covers |
|------|--------|
| `charging45` | arm, charger-fault trip, session-over reset |
| `evap80` | **the boot fallback on real frames** (since 2026-09-30): the window opens 115 min into the session, so the core stays PASSTHROUGH and rewrites nothing. It used to be the override case; see the note above the table |
| `above83` | the 83 % start on the truck's own frames: arm without a net charging current, the hold recognised, the arm-delay wait, nothing modified before the real STAND_BY |
| `syn_override_release` | override -> BMS permission collapses at 3593 mV / 2.8 A -> **release = transparency + 20 repeats of the VCU's `00 01 03`** (synth=20) -> the VCU's setpoint passes untouched -> its STAND_BY -> PASSTHROUGH |
| `syn_trip_during_override` | cell_overvolt mid-override -> SAFE, transparent, the VCU's Low Power repeated (synth=20) |
| `syn_charger_fault` | inverter fault mid-override -> SAFE, transparent, the VCU's Low Power repeated (synth=20) |
| `syn_stale_bms` | BMS goes quiet -> staleness trip raised from `tick()` |
| `syn_flow_drop_ends_override` | spec 6 (A5): the flow drop ends the override; vmax 3700 mV and HVIL open *after* it cannot trip, and nothing is rewritten from there |
| `syn_arm_delay_no_latch` | spec 3 (A7): a Low Power 10 s into the charge latches nothing; the accept happens at t=75100 when the delay expires, not at t=10000 |
| `syn_boot_mid_session` | spec 3 (A3): pilot timer reads 5 min at power-up -> PASSTHROUGH throughout, no arm, no override |
| `syn_pilot_restart_clears_safe` | spec 6.1: hard-ceiling trip -> SAFE, then the pilot timer steps 1 -> 0 -> SAFE cleared, re-armed, and the second session overrides normally. The "failed at one handle, moved to another" case |
| `syn_charger_handle_pull` | spec 6 (2026-10-03): the charger drops `vehicleConnected` 1 s before the VCU's flow drop; HVIL then opens 20 ms BEFORE the drop. TERMINATED at the charger's report, no trip, synth 0 |
| `syn_source_11_plug_in` | spec 9 (2026-10-03): `shutdownSource` latched at 11 with the plug still in -- the original Bel unit's behaviour. The core keeps watching, and the charger fault that follows still trips (synth 20). This is the case `charging45` lost when source 11 was a trigger |
| `syn_boot_lock_arm_first` | spec 3 (A3): the charge is observable for 3 s before the first pilot frame, which then reads 5. The core must never arm. This is the ORDER the truck actually uses -- 0x18FFD4C0 at 10 Hz against 0x18FFD8C0 at 1 Hz -- and the defect it guards was invisible to a test that sent the pilot frame first |
| `syn_evap_clear_accept` | Low Power at 90 % with the evap flag clear -> TERMINATED, not OVERRIDE |
| `syn_soc_below_gate` | Low Power with the flag set at 70 % -> TERMINATED (below the 78 % gate) |
| `syn_startup_transient` | the transient with the flag up at 83 %: Low Power 3 s in, CHARGER at 66 s -> waited out, nothing touched, still armed |
| `syn_above_ceiling_start` | the same hold kept past 75 s -> overridden from a tick at the delay, the VCU's Low Power repeated as CHARGER (synth=20), setpoint rewritten to the pilot cap, STAND_BY -> PASSTHROUGH |
| `syn_balance_hold` | genuine stop (flag clear), 60 s hold, flow 0 + STAND_BY, nothing touched |
| `syn_direct_stop` | flow 0 in CHARGER mode, STAND_BY 1 s later, nothing touched |
| `syn_hard_ceiling_trip` | the BMS never tapers (`chg_max` pinned at 300 A) and vmax climbs to 3610 mV -> the hard ceiling trips, immediately and with no debounce. The backstop of spec 5, and the one path nothing else here reaches: the next highest vmax in the set is 3593 mV. Until it existed the C++ ceiling branch had never been compared against machine.py at all |
| `syn_hold_cap_clamp` | spec 5.2 at e5d62c90: override on the 6.10 A floor at SoC 98.5 %, release on the leader into HOLD at 6.10 A, then the EVSE cap drops below the hold setpoint TWICE -- to 6.00 A (what a real charger reports in Low Power) and later to 2.00 A -- and back to 6.00 A. The cap is a clamp, not a 0.1 A walk. No other trace ever puts the cap under the setpoint, so a port that walked down to it, or ignored it, agreed with machine.py by default |
| `syn_repeat_replaced` | spec 4.1: a repeat starting over one still in flight REPLACES it. Based on the above-ceiling start, because only a TICK-decided override opens a burst a later event can interrupt; the fault lands at a fixed 500 ms into the burst, not on its 1,000 ms boundary, so the frame count is deterministic rather than scheduling jitter |
| `syn_repeat_superseded` | spec 4.1: the VCU's own page 00 lands inside the repeat and ends it. Every other trace lets the burst run its full 20 frames, so the superseding clause had no trace at all -- the bench hits it every time and this corpus never did |
| `syn_charger_silence_keeps_safe` | spec 6.1: 20 s of charger silence resets the session but NOT SAFE. The 500 ms staleness row has already tripped on the same silence, and silence continuing must not undo the latch it set |
| `syn_charger_stop_source_3` | spec 6: only source 11 is a handle pull. Source 3 is the charger stopping itself -- 24 endings in the corpus, connected flags staying up -- and must reach the 5 s state debounce and trip there, not read as an unplug |
| `syn_plug_out_then_pilot_reset` | spec 6.1: the boundary that arrives when the session is ALREADY over. The charger reports the plug out, the VCU follows with STAND_BY, and the pilot timer returns to 0 only afterwards, so the boundary must be logged in PASSTHROUGH. `syn_pilot_restart_clears_safe` reaches the same boundary from SAFE, which takes the state-changing path, so until this trace existed the differential never ran a clear_safe boundary that changes no state |

The Python side's own suites also pass: `test_machine.py` 89 tests,
`test_signals.py` over 2000 randomised frames per message. (The count is
refreshed when this file is touched, not on every batch -- `test_machine.py`'s
own run prints the current one.)

Build footprint: **RAM 7.6 % (24,972 B), Flash 10.7 % (357,710 B)**.
`pio run` leaves ~23 MB in `.pio/`, which is regenerable -- delete it if the
SeaDrive sync cost matters.

Ticks are written into the trace rather than re-derived by each runner, so a
divergence can only be a real difference in the state machine and not a
difference in how the two harnesses decided when to tick.

The trace/golden pairs live in `projects/vtrux/notes/artifacts/interposer-firmware/`
and are committed, so the diff is reproducible without re-parsing the source
captures. `evap80` came from a 1.8 GB capture via the same pre-filter `regress.py`
uses; the 245 MB intermediate is **not** kept. To rebuild it:

```bash
bash projects/vtrux/notes/artifacts/charge_cmd_filter.sh \
     <tmp>/filt_evap80.log \
     projects/vtrux/logs/vtruxchargeafterexportchargetest-80pctcv.log
# the filter gained 0x18FFD8C0 on 2026-09-30; without it the rebuilt
# trace has no pilot frames and the core cannot arm at all
py -3.12 .../make_golden.py <tmp>/filt_evap80.log --name evap80 \
     --start 7000 --end 7600 --bus 3 --outdir <artifacts>
```

The window is chosen around the low-power command at t=7426.95 s, with ~427 s of
lead-in so the core arms and clears `arm_delay_ms` before the decision. Pass
`--bus` as an integer -- it is compared against `CANFrame.bus`, and a string
silently matches nothing.

## Design decisions worth knowing

**The vehicle segment gets TWAI; the charger segment gets the MCP2515.** Not
arbitrary. Measured on this truck's powertrain bus:

| | frames/s | bus load @ 500 kbps |
|---|---|---|
| charging | 864 | ~21 % |
| generator running | 2250 | ~50 % |

while the charger segment carries only what the Bel emits (~55 fps) plus the
20 Hz command frame. The heavy side belongs on the hardware FIFO.

**The MCP2515 driver is hand-written rather than coryjfowler/MCP_CAN_lib.**
That library is otherwise the right choice -- `MCP_CAN(SPIClass*, cs)`,
`MCP_STDEXT`, extended IDs both directions -- but `MCP_CAN::sendMsg()`
busy-waits for the frame to complete *on the wire*, polling TXREQ up to
`TIMEOUTVALUE` (2500 us, `mcp_can_dfs.h:47`), then returns `CAN_SENDMSGTIMEOUT`
and **silently drops the frame**. That is ~300-350 us of blocking per send at
500 kbps, all three TX buffers wasted, and frame loss on arbitration
contention. For a transparent bridge that is a correctness bug. This driver
loads a free TX buffer, sets TXREQ, and returns.

**TWAI RX queue depth is 64, not the driver default of 5.** A 20 ms
non-IRAM stall -- which `CONFIG_SPI_FLASH_ERASE_YIELD_DURATION_MS` can produce
during a flash erase -- is ~45 frames at 2250 fps.

**Nothing is filtered in hardware.** Both ports accept everything. A bridge
cannot forward what it filtered, and we do not yet know the full set of IDs the
Bel actually cares about.

### Every event line carries `a= b= c=`, zeros included

Spec 8.3's event line is `[t] STATE event a= b= c=`. Until 2026-10-14
`reportEvents()` printed the three fields only `if (e.a || e.b || e.c)`, so
an event with nothing to report printed a bare name and fell out of the
form -- the line that found it was
`[604437] TERMINATED   VCU dropped the flow bit: ...`, with no fields at all
(found on the bench, item 6 scenario 2).

The reference has no such rule and never did: `machine.py`'s `_log` defaults
`a`, `b` and `c` to 0 and `Event.fields()` returns all three
unconditionally. So the suppression made the port's OUTPUT disagree with the
reference's DATA for exactly those events that have nothing to say, which is
also why nothing caught it -- `event_stream.py` compares the three numbers
against `host_runner --events`, and both sides had them; only the printed
line dropped them.

### An edge must not live in a buffer someone else empties

Spec 6.1's session boundary resets the session on **every** frame that shows
one, level-triggered on purpose, and only the log line is an edge: the VCU
sends its page-00 changes as a 20-frame burst, so without a collapse one
session end reads as twenty. The collapse used to ask whether the last
RECORDED EVENT was the same boundary -- and `reportEvents()` prints the
event buffer and empties it every pass, so on the board the buffer was
always empty when the question was asked. **It answered "no" every time and
the board printed `session over a=2` twenty times, ~50 ms apart** (found on
the bench, step 4 item 2, 2026-10-14).

**Why the differential could not see it, corrected 2026-10-14.** The first
account of this said the reference never empties its buffer, so `machine.py`
collapsed correctly and the two cores agreed. The agreement is real and the
reason given for it was wrong, which matters because the wrong reason is
reassuring and the right one is not.

`test/host_diff/event_stream.py` drains the reference's log after every
input line -- `del core.events[:]` at its line 60 -- precisely as
`host_runner` drains `machine.cpp`'s every pass, so that the two sides are
comparable. Measured on the pre-fix reference over a 20-frame STAND_BY
burst: **1 line undrained, 20 lines drained.** So in the differential BOTH
cores emitted twenty and agreed. The reference was not correct there; it was
broken in exactly the same way, because the harness had put it in the
board's condition.

**A differential can only see asymmetry, and the draining that makes the
comparison possible is what made this defect symmetric.** That is the thing
worth remembering: the defect was invisible to the strongest check in the
project not despite that check's design but because of it. `test_machine.py`
drains neither side, which is why the reference looked correct there and why
the unit test for this had to call `clear_events()` explicitly to say
anything at all.

So the edge is a member on both sides now (`last_boundary_valid_` /
`_last_boundary`), set where a boundary line is logged and cleared by any
other event, which is the pattern `wait_reason_` already used. A member
cannot be emptied by a consumer. `boundaryLogs()` counts the lines so the
behaviour can be read **across** `clearEvents()`, where the buffer shows
nothing, and `main.cpp` exposes it as `intpBoundaryLogs()` for the hosted L2
test. `test_machine.py` drives the reference through `clear_events()` -- a
mirror of the board's call added for this -- so the board's condition is now
something a test can express rather than something only hardware saw.

**Unifying the two cores took a second change.** `machine.cpp` compared the
event code, `a` and `b`; `machine.py` compared the event NAME alone. A run
of same-named boundaries carrying different values would therefore have
collapsed in the reference and not on the board. No trace in the corpus
contains that case -- the differential would have failed if one did -- so
the port's reading was kept as the more discriminating of the two.

## What this firmware deliberately does not do

- **It cannot take itself off the bus.** Both MCP2562 transceivers are
  hardwired always-on: pin 8 (STBY) has no GPIO on this board, confirmed from
  the Arduino variant `aslcanx2` and the CircuitPython board definition. Adding
  that interlock is a solder mod, not a firmware change.
- **No OTA.** If it is added, note the rollback trap the generator-inhibit work
  found in stock wican-fw: marking an image valid before the recovery channel
  exists defeats the point of rollback.
- **No stealth telemetry masking.** `mask_charger_telemetry` exists in `Config`
  and defaults off, exactly as in `machine.py`.

## Classifying a change against spec 10

Spec 10 lists what only the truck or the bench can answer. **A change that
touches any of it needs a bench or truck run, whatever the host tests
show** -- a green emulation run is not evidence about a thing emulation
cannot reach. Nothing classified a change against that list until
2026-10-04; this is the procedure, and spec 12 carried the gap.

### The procedure (this is the authority)

Before a build goes to the truck, walk the change through each item and
record the answer in the flash record -- "touches X", or "touches none".

| # | spec 10 item | touched when the change involves |
|---|---|---|
| 1 | flashing and boot | `platformio.ini`, partition or sdkconfig settings, anything in `setup()` |
| 2 | the real transceivers and the controllers' own behaviour | either port driver, `can_port.h`, bitrate, filters, bus-off or recovery handling |
| 3 | throughput at real bus load | the bridge loop's drain bounds, the tick or diagnostic cadence, anything that adds work per frame |
| 4 | reset and brownout | persistence, boot-time state, power handling |
| 5 | the board offline (item 6 in spec 10) | anything that changes what a stalled or crashed board leaves on the bus |
| 6 | the charger acknowledging while silent (item 7) | the transmit path, the 100 ms age-out, the spec 2.2 counters |

**When in doubt, it is touched.** The asymmetry is not close: a needless
bench run costs an afternoon, and a missed one puts an unverified change on
a vehicle.

Two cases that look like exceptions and are not. A change confined to
`machine.cpp` still reaches item 3 if it adds per-frame work. A change to a
*test* touches nothing -- but a change to a **fake** can move a result that
an acceptance decision rests on, which is why the fakes carry their own
assumed-versus-measured list (spec 9.1).

### The mechanical aid

`test/hw_mechanics.py` watches what is a hardware mechanic **by
construction**, in three ways:

- **whole files**: both port drivers, `port_*.h`, `can_port.h`,
  `platformio.ini`;
- **`setup()`'s body**, extracted by brace matching and hashed with the
  `INTP_PRINT*` lines removed. The boot path is a mechanic entire -- init
  order, the controller begin calls, the B-7d failed-start branch -- so a
  reordered `begin()` is reported while a reworded message is not;
- **named settings by value**, not by file hash: the pin assignments, the
  bitrate, the loop timings and the per-iteration drain bounds in
  `main.cpp`.

```
py -3.14 charge-interposer/firmware/test/hw_mechanics.py
py -3.14 .../hw_mechanics.py --record 2026-10-04-truck    # at each truck flash
py -3.14 .../hw_mechanics.py --list                       # what is watched, and why
```

This folder is not under git, so the baseline is a manifest written at each
truck flash and kept in
`projects/vtrux/notes/artifacts/interposer-firmware/flash-manifests/`. Manifests
are a record of what was flashed: the script refuses to overwrite one.

**It does not replace the procedure and cannot.** A change anywhere can
alter timing, and only a person walking the table can say so. What it makes
impossible is the one failure worth automating -- *the driver changed and
nobody noticed* -- which is silent, and which concerns files a change is
rarely about.

Three properties it was built to have, each verified by changing the thing
and watching the output:

- a port-driver edit is reported, and the report clears when it is reverted;
- a **pin assignment** change in `main.cpp` is reported, while a
  logging-only edit to the same file is not -- the settings are watched by
  value, so hashing the whole file (which would flag every edit, and so be
  ignored within a week) was not needed;
- bringing SPI up **after** the TWAI controller instead of before is
  reported, while rewording a message inside `setup()` is not -- and
  deleting a whole branch that contained only a message still is, because
  its braces go with it;
- a mapped entry that can no longer be found is a **failure**, not a skip.
  Rename `MCP_CS`, or `setup()` itself, and the script exits 2 saying that
  mechanic is no longer being watched. Without that, a rename would quietly
  drop an item from the map and the script would report "nothing touched"
  forever after.

## Before this goes in a truck

`projects/vtrux/notes/plans/can-bus-fault-injection-test.md` is on the books and not
yet run. Every claim we hold about what a fault here costs is inference from
datasheets, not measurement on this vehicle.
