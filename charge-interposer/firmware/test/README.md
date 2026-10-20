# firmware/test/

Tests that need the firmware source rather than the Python bench. The
behavioural tests of the core live one level up (`../../test_machine.py`,
`../../test_trips.py`, `../../test_invariants.py`) because they run against
`machine.py`; everything here is about the C++ port or the build.

**This file also carries the detail of the tester's harness files one level
up** (reviewer, 2026-10-15): `../../README.md` describes each in one line and
points here for the checks and their can-fails. See "Harness files in
`charge-interposer/`" at the end.

| file | what |
|---|---|
| `build_check.py` | Builds **every** PlatformIO environment and checks the truck image carries no serial output (spec 8.3). Review item C2b. Also compares every image's source digest **against the tree on disk** (added 2026-10-05) -- the earlier check only compared the six images with each other, which six images built from the same older tree pass unanimously. Also checks `src/machine.h`'s `DIAG_SCHEMA_VER` equals the `DiagSchemaVersion` of `../diag/vtrux-interposer-diag.dbc` (user ruling 2026-10-19; alone: `--schema`, no build) |
| `build_check_in_clone.py` | Runs `build_check.py` in a real git checkout (tester, 2026-10-19; user ruling): clones a bundle, checks out exactly the run's commit, and refuses (exit 2, nothing built) unless it is a clean git work tree whose source digest equals the shipped copy's. The emulated suite's build-check on madhouse goes through it, because the shipped copy has no `.git` and a build there gets no manifest row (run e7d4) |
| `build_check_in_clone_test.py` | Tests the harness: on a throwaway two-commit repo, the right commit and copy run a stub `build_check.py`; a plain copy as the bundle, an unknown commit, a short SHA, an existing destination, an older commit and a shipped copy with other sources are each refused. `--can-fail`: 3 mutants (the e7d4 plain copy, the bundle's HEAD unverified, sources not compared), each caught |
| `build_check_schema_test.py` | Tests the harness (tester, 2026-10-19): `build_check.check_schema_version` on copies of the real `machine.h` and diag DBC. The unedited pair passes; the constant or the attribute moved alone, the constant missing, written twice, as a `#define` too or as another type, and the attribute missing, a string or stated twice, each fail (the DBC is read as text: build_check runs under the PlatformIO venv's Python, which has no cantools). `--can-fail`: 3 mutants of the check, each caught |
| `build_check_links_test.py` | Tests the harness (tester, 2026-10-13): `build_check.check_build_links` (spec 9 C2b, b7dc12e3) must fail on one extra flag on either side of each link (witness vs bench, truck vs bench), a dropped flag, an extra unflag, and any other setting that reaches the image; a monitor setting must pass. In memory on the resolved config, and end to end on edited copies of `platformio.ini` resolved by `platformio project config` |
| `build_check_manifest_test.py` | Tests the harness (tester, 2026-10-07): `build_check.check_manifest` must find, for each image on disk, a manifest row with that name AND the ELF read from its bytes -- a row with the name but other bytes fails, as does an image with no application descriptor. Runs against a throwaway tree. |
| `digest_recheck_check.py` | Proves the source-digest re-check can fail. Test 1: `source_digest()` moves on a one-byte change to any hashed input and returns when it is reverted, and does *not* move on a file that is not hashed. Test 2: starts a real build, edits a hashed input while it runs, and requires the build to **fail** with `SOURCE DIGEST MISMATCH`; then requires an untouched build to pass. It deletes the environment's `.bin`/`.elf` first, because the post-action is attached to the image target and a `pio run` with nothing to do never reaches it -- the first version of this test omitted that and reported the check broken when the probe had simply never run |
| `host_diff/` | The Python/C++ differential: `machine.py` makes the golden, `machine.cpp` is replayed through `host_runner` and the two are diffed byte for byte |
| `c12_bench/` | A standalone ESP-IDF test image (no Arduino, no PlatformIO, no product code) that runs `host_bridge/conformance.cpp` on the X2's real MCP2515 over SPI and asks the host (SeaDrive `bench_c12.py`) for what it cannot do itself: inject a frame, report the wire, withhold ACK. Its transcript diffs line for line against the fake's. Tester, 2026-10-13; see its README |
| `vport_bench/` | A standalone ESP-IDF test image (no Arduino, no PlatformIO, no product code; **never on the truck**) for spec 9's "what the board can put on the vehicle segment by itself". It drives only the S3's TWAI controller (the vehicle port) on console commands: mode (listen-only at power-up), bit rate, one-shot sends, a flood, a stalled task, a held-dominant TXD, and status. The host (SeaDrive `bench_vport.py`) judges the wire with its own adapters. Tester, 2026-10-13; see its README |
| `host_bridge/` | Spec 9.1 L2: `main.cpp`, both ports and the core against fake chips (`mcp2515_fake_chip`, `twai_fake`, fake `Arduino.h` / `SPI.h` / `esp_app_desc.h` / `esp_log.h`), plus the MCP2515 conformance sequence (`conformance.cpp`) that the fake and the bench image both run. Tester-owned, whole directory; see its README |
| `host_unit/` | C++ unit tests for the firmware pieces pure enough to compile on a PC with no peripheral faked at all. Two of them: spec 8.2's `bridge_ok` interval rule, both `sample()` for a status frame and `peek()` for 8.3's serial line in listen-only, which must not consume an interval (`src/bridge_health.h`), and spec 2.2's `0x7F6` B7 counter (`src/charger_tx_failures.h`) |
| `hw_mechanics.py` | Reports which spec 10 hardware mechanics a change has touched, against a manifest recorded at the last truck flash. The written procedure in `../README.md` is the authority; this closes the one gap worth automating |

## `build_check.py`

```
py -3.11 charge-interposer/firmware/test/build_check.py
py -3.11 .../build_check.py --no-build      # check existing .pio images
```

Two checks. First, every `[env:...]` in `platformio.ini` links -- the list is
parsed from the file, not written here, because the defect this exists for is
precisely an environment nobody remembered. On 2026-09-29 `slcan_main.cpp`
was added without being excluded from the other environments'
`build_src_filter`, so `[env:esp32-can-x2]` -- the image that goes in the
truck -- failed to link with "multiple definition of setup()", and `selftest`
and `diag` with it, until 2026-10-03. Nothing caught it because the bench
only ever built what it was about to flash, and that was never the truck
build.

Second, the truck image contains none of `main.cpp`'s serial format strings,
**and the bench image contains all of them**. The positive control is not
decoration: "the string is not in the file" is also what a typo, a renamed
message or a path pointing at the wrong image produces, and without the
control those pass as success.

Third (spec 9 C2b, b7dc12e3, user 2026-10-13), the build links. The witness
environment is the bench one plus `-DINTP_ORDER_WITNESS=1`. The truck
environment is the bench one plus `-DCORE_DEBUG_LEVEL=0 -DINTP_NO_SERIAL=1`,
with `-DCORE_DEBUG_LEVEL=1` unflagged. Nothing else may differ. The check
compares the settings PlatformIO resolves (`platformio project config
--json-output`), so the tool that builds expands `extends` and `${...}`. It
compares every option except `extends` and the monitor / upload / debug /
test options, which never reach the image. `--links` runs this check alone,
without a build.

## `host_unit/`

```
wsl bash ".../firmware/test/host_unit/run.sh"
```

Builds and runs every `test_*.cpp` in the folder with plain `g++ -std=c++11`,
and needs no environment set (unlike `host_bridge/run.sh`, which wants
`VTRUX_DATA`). Both rules below were kept out of `main.cpp` so they could be
tested here; the rest of `main.cpp` needs the fake ports of spec 9.1 L2,
which live in `host_bridge/` rather than in this folder.

| test | covers |
|---|---|
| `test_bridge_health.cpp` | spec 8.2's `bridge_ok`: health **since the previous status frame**, not since boot. Both halves, because each is a different defect -- a failure inside one interval must clear the flag for **that frame**, and a clean interval afterwards must set it again. Shown failing against both: ignoring the vehicle-side input (the pre-2026-10-04 behaviour) fails 2 checks, and never advancing the baselines (the "since boot" defect of review item B-3) fails 7. 20 checks |
| `test_charger_tx_failures.cpp` | spec 2.2's `0x7F6` B7, "charger not acknowledging while expected": the SUM of the charger port's three disjoint tallies -- queue full, controller failure, and the 100 ms age-out that discards a frame at the head without ever loading it (user, 2026-10-13) -- accrued only while the VCU's flow bit is 1, with a delta that arrives outside a session DISCARDED rather than banked for the next one. 18 checks: one per tally, the three together in one pass, both sides of the gate, the mid-session boot of 2.2's last sentence, and the unsigned subtraction that makes a counter wrap harmless |

(Check counts measured by the tester, 2026-10-13, at 9c42964: 20 and 18,
all OK.)

**`test_charger_tx_failures.cpp` is not a smaller copy of `test_l2_bridge`'s
`tx-fail` arm.** That arm drives the same field end to end through
`main.cpp` and is much the stronger test of the wiring -- the frames in it
fail by aging out, so it reaches the 2026-10-13 clause too. What it cannot
discriminate is the shape of the number: its flow-1 assertion is
`gated > 0`, which an implementation that banks failures from outside the
session and adds them to the next one also satisfies, reporting a bigger
number rather than a wrong-looking one. The unit case "and they are NOT
banked: the session starts at 0 even though the tallies stand at 1,000" is
an equality. *The implementor reports (2026-10-13, not re-run by the
tester) that six mutations of the header were run against these cases and
none survived, the baseline-only-when-counting mutation failing exactly
that case first.*

## `host_diff/`

| file | what |
|---|---|
| `host_runner.cpp` | Drives `machine.cpp` over a trace, printing `make_golden.py`'s canonical format. `--diag` prints the four spec 8.2 status frames instead |
| `diag_stream.py` | The same status frames from `machine.py`. No golden: both sides are generated on the spot, so neither can go stale |
| `diff_all.sh` | Builds `host_runner`, checks every golden is current against `machine.py` and every capture-derived trace against its pin, diffs every pair, then the diagnostics, then the 8.3 events field by field. Three negative controls, each reporting COULD NOT RUN rather than passing when it cannot: one byte flipped in a golden; top of charge's b and c swapped in a copy of `machine.py`; and (2026-10-15) "override ended, transparent" in a copy of `machine.py`, which must read DIFFERS on `unit_charger_during_override` while the unmutated line matches. Run under WSL on Windows (its `python3` is 3.10 and runs `machine.py` unchanged), or on madhouse-debian |
| `make_golden.py` | Builds a trace/golden pair from a real capture |
| `make_synthetic.py` | Builds the `syn_*` pairs from generators, for the paths no capture reaches |
| `make_unit_traces.py` | Builds the `unit_*` pairs by recording what `test_trips.py` and `test_invariants.py` drive, so the unit tests reach `machine.cpp` and not only `machine.py`. From 2026-10-08 also the 58 `unit_def_*` traces: spec 6's contactor and charger-fault definitions, one input per trace (`test_trips.definition_cases`); and the 5 `unit_dense_*` traces, synthetic charges replayed with 2-10 ms ticks and injected page 03 (`test_invariants.densified`), so the port is held to the spec 4 hold of 03.02 / 03.07 and the 4.1 repeat spacing. From 2026-10-15 also `unit_charger_during_override` (`override_ended_traces`): armed, Low Power, OVERRIDE, then the VCU's page 00 with mode CHARGER and the flow bit on -- spec 6's EV_OVERRIDE_ENDED_BY_VCU, "override ended, armed", which no capture, scenario or other trace reaches; it refuses to record unless the bench is in OVERRIDE before and MONITOR after |
| `regen_golden.py` | Replays an EXISTING trace through `machine.py` and rewrites only the golden -- for when the spec changes. `--check` reports without writing; `--stdout` prints one golden instead of writing it |
| `diff_one.sh` | Replays ONE trace through `machine.cpp` and diffs it against `machine.py`, generating both sides on the spot. `run_scenario.py` calls it per scenario (spec 9.1 L1) |
| `check_port.py` | Compares every `Config` field, identifier, enum and bit extractor in `machine.cpp`/`.h` against `machine.py`. Needs no compiler |
*`golden/` was deleted on 2026-10-05 (user's call).* It had been stale since
2026-09-20 and read by nothing: `diff_all.sh` globs
`notes/artifacts/interposer-firmware/`, while both generators still defaulted
to `golden/`. That combination meant a regeneration updated a directory the
gate never looks at and the gate then passed on stale files -- which happened
on 2026-10-05 and briefly produced the conclusion that no trace produced a
repeat at all. **Both generators were repointed at the artifacts directory**
(`make_synthetic.py` and `make_golden.py`); leaving either default behind
would have recreated the dead directory the first time anyone ran it without
`--outdir`. All 44 files had live counterparts in the artifacts directory
before deletion.

The pairs themselves live in SeaDrive, in
`$VTRUX_DATA/notes/artifacts/interposer-firmware/` (see `../../paths.py`),
with their own README covering what each one exists for and the two
regeneration traps.

**The diagnostics half was added 2026-10-03.** The frame/state diff cannot
see `diagFrames()` at all -- the four status frames are packed by a separate
hand-written function in each core, and C1 listed the C++ one as untested at
any level. `--diag` and `diag_stream.py` compare the packings directly over
every trace. Demonstrated: moving the schema-3 charger-silence bit from 0x40
to 0x20 in `machine.cpp` is caught on `syn_charger_silence_keeps_safe`
(`...a0a3` against `...a0c3`), which is the only trace where the charger goes
quiet while the flow bit is 1.

## Spec 9.1 L1 -- the flight core in closed loop

Every scenario in `run_scenario.py` judges `machine.py`. The board runs
`machine.cpp`, and the two have been shown to differ: review item B-1 was
exactly that, and no capture-derived trace covered it.

So `interposer_sim --trace-out` records every frame and tick the core
processed, and `run_scenario` replays that through `diff_one.sh` at the end
of the run. Three things about it:

- **the recording is of what the core SAW.** The write sits inside the
  echo-suppression guard, so a mirror frame the core emitted and read back
  off its own segment is not recorded as an input. Replaying that would
  feed the core its own output and diverge for a reason that is not a port
  bug;
- **there is no golden on disk.** `interposer_sim`'s clock comes from wall
  time, so a recording is not reproducible between runs and a stored golden
  would be stale by construction. Both sides are generated at diff time.
  These traces therefore stay OUT of the maintained set; if one ever
  diverges, a minimised hash-pinned copy gets promoted into it by hand;
- **a replay that cannot run FAILS the scenario.** It needs a C++ compiler
  (WSL on Windows). "Could not check" and "checked and fine" must not read
  the same, so there is no silent skip -- `--no-l1` exists to say so
  deliberately.

The trace is deleted after a clean diff and **kept when it diverged**,
which is when someone needs it. A 9,000-simulated-second scenario records a
few hundred thousand lines, and nineteen of those would put hundreds of MB
into a synced folder for no lasting value.

**What the differential cannot see.** It proves the two cores agree, which
is not the same as either being right: a mistake both of them make is
invisible to it, and a regenerated golden turns a wrong behaviour into the
expected one. Review items B-1, B-6 and B-7a were all in both cores or in
neither trace, and all 24 pairs stayed byte-identical across every one of
those fixes. `../../test_invariants.py` is the complement -- rules with no
golden behind them.

## Harness files in `charge-interposer/`

The tester's files one level up -- every harness file in the tester's column
of `firmware/esp-development.md`'s component table -- the checks they make and
how each can fail.
Paths below are relative to `charge-interposer/`. Each test runs with
`py -3.14 <file>` (NICKY-XPS) or `python3 <file>` (madhouse-debian), with
`VTRUX_DATA` and `VTRUX_PUBLIC_REPO` set (`paths.py`), and exits 0 only if
every check passed.

| file | what |
|---|---|
| `run_scenario.py` | The closed-loop scenarios (spec 9) against `interposer_sim` or, with `--external-interposer`, the real board. A board run's own checks are listed below the table |
| `diag_cadence.py` | Spec 8.2 on the wire: the PCAN witness (`DiagWitness`, ACTIVE by default so it acknowledges on CAN1) and `score()`, which judges the 1 Hz cadence and the immediate 0x7F4 against the serial event lines, and spec 2.3's windows: a status frame is normal-mode within 2 s (+0.1 s slack) of a forwarded charger frame, the power-up round once before any, otherwise outside and a FAIL. `prechg_unjudged` (2026-10-15) counts the frames before the first charger frame as NOT JUDGED, never failed, when `run_scenario.pre_charger_premise` says the previous charger stopped too recently. `reset_round()` places a board reset's power-up round |
| `diag_cadence_test.py` | `diag_cadence`'s own test: each 8.2 rule driven to pass and fail on synthetic rows, the host mapping under 80 ppm adapter drift, 2.3's windows (a stray 0x7F4, a burst 2.15 s after the last charger frame, a second power-up round each FAIL; 2.05 s passes on the slack), the real diag DBC, a board reset mid-capture, and the pre-charger premise: the previous charger 1.9 s before the witness -> NOT JUDGED; 3 s, with a second round before the first charger frame -> FAIL |
| `board_reset_test.py` | What `run_scenario` does differently for a real board, against a fake pyserial: boot-mid-session's reset (a port that ignores it, never comes back, or uptime that cannot discriminate each FAIL); the start order and the acknowledging witness; the serial readline fix; the fault-vs-flow-drop order from the witness. From 2026-10-15: the teardown order (refused if the serial port closes before the sims stop), the 3 s rest, the pre-charger premise's file and wiring; no-TRIP to the earliest sim stop (a trip 300 ms after the stop, one before it, a=11 after vehicle_sim stopped and one 900 ms after each FAIL, 510 ms PASSES); process priority (a child left at NORMAL FAILS, read back at ABOVE_NORMAL PASSES; Windows only). Needs pyserial, so it runs on NICKY-XPS |
| `bus_gs_usb_test.py` | `bus.open_gs_usb` against a fake GsUsbBus: the Innomaker opened by index from `--channel` with no auto-detection, its TX echoes drained and dropped by default and kept on request, and a `recv()` that sees only echoes returning None rather than an echo or a hang |
| `test_trips.py` | Spec 6: one test per fault source, from MONITOR **and** from OVERRIDE, each driving exactly one source with every other input held healthy so it cannot pass on a different one, plus the control (a healthy bus never trips). The thresholds at their edges (3609/3610 mV, the 5 s debounce, 500 ms staleness per message, the 6 h cap from the override's start); the spec's non-trips (TYPE2, undervolt, temperature, `chg_max` 0); "nothing changed on the bus" byte by byte; the literal 20. Spec 6's definitions of 2026-10-07: every `bcm_mainc_stat` value alone (only 11 and 12 do not trip) and every `0x18FFD4C0` mux-3 flag alone (only the four named faults trip), recorded as `unit_def_*` traces for the port; MONITOR past 6 h does not trip. Can-fails: `test_thresholds_can_fail` runs each threshold test against a core built with a wrong threshold and requires it to fail; each definition case has its own. Its `Bench` driver is a copy of `test_machine.py`'s, because that file is the implementor's: a harness that imported it could change under the tester without notice |
| `test_invariants.py` | Specs 4, 4.1 and 8.1: rules with no golden behind them -- every frame forwarded exactly once, nothing rewritten outside an override, no repeat over 20 frames, no command of our own, never above the BMS's permission or the EVSE cap -- over every differential trace and randomised traffic. The permission and cap come from an `Oracle` that decodes the bus, never from the core's state; every override page 01 must equal the permission capped by the EVSE; repeats checked for direction and bytes (4.1), mirrors for direction and content (8.1). Spec 4's held 03.02 / 03.07 and 4.1's 50 +/- 10 ms repeat gaps are judged on a densified replay (2-10 ms ticks, injected page 03), since the recorded traces tick at 100 ms. The complement to the differential, which cannot see a mistake both cores make. `run_scenario.py` runs it over each scenario's trace. Can-fails: `test_checker_catches_each_mutation` -- 16 wrong cores each rejected for their own reason -- plus population guards and four mutations for the densified rules |
| `test_released_into_hold.py` | Tests the harness: `released_into_hold` (spec 5.2's release into the VCU's hold) scored against every saved acceptance log that declares it, then against one mutation of a real charger log per clause, each rejected for its own reason. **Why it exists:** rev 1 failed a correct `evap-override-above-ceiling` by latching the VCU's pre-override Low Power -- a check wrong in one scenario and right in eight. Since 976f23e it also reads the board's log form (step 4 item 6) |
| `test_scorer_wiring.py` | Tests the harness: every scenario asks for `diag_frames_ok()` and every termination scenario for `no_rewrite_after_release()` (spec 9); the burst length, firmware and schema versions are the spec's literals, not `machine.py`'s; the version is checked in every `0x7F7`; `check_invariants()` fails on a missing trace and passes a known-good one; the 16 A scenario no longer scores the charger sim's clamped current; the fault-vs-flow-drop order in both directions; spec 8.3's event keys (TRIP codes, the boot lock) |
| `test_repeat_bursts.py` | Tests the harness: spec 4.1's repeat check against every saved acceptance log that declares it, then six mutations. **The rev-1 lesson:** it demanded exactly 20 frames per REPEAT, which is unreachable on a correct core in `fault-during-override` -- a burst is 20 x 50 ms = 1,000 ms and the VCU drops flow REACT_HOLD_S = 1.0 s after the charger leaves state 12, so the count was jitter (13 at 1x, 19 at 5x, 7 on the board). The two mutations that carry the weight must still FAIL: a short burst with the VCU's close-out removed, and a truncated release burst the VCU said nothing near. An ABANDONED end passes only with a spec 6.1 boundary witnessed in its window (VCU STAND_BY / EXPORT in the vehicle log; a handle pull, replug or 20 s of charger silence in the charger log). Since 976f23e it reads the board's log form too |
| `vehicle_sim.py` | VCU + A123 BMS + the VCM's evap flag (0x649). `--mode model` (closed loop, `pack.py`) or `--mode replay` (a real capture). On a board run it also logs the status frames it sees and their 2.3 window |
| `charger_sim.py` | The Bel charger/inverter, with fault injection and EVSE / Low Power behaviour (it reports what it is commanded and delivers what P3a calibrated); `--sp-log` records every page-01 setpoint it RECEIVES on the host clock (`sp_record`), which a board run's spec 9 hold is judged from |
| `interposer_sim.py` | The board as a process: I/O only, all logic in `machine.py`; `--trace-out` records what the core saw, for spec 9.1 L1 |
| `pack.py` | 116-cell LFP pack model: OCV curve, IR drop, per-cell imbalance, passive balancing, the BMS charge-current taper and the fitted permission (P3); fitted to three real captures |
| `protocol.py` | The `0x18EFC000` page map plus DBC-backed encode/decode for the BMS (`epri-pt-bus.dbc`) and charger (`BelInverter-v2.dbc`) frames |
| `bus.py` | Transport factory (virtual `udp_multicast`, hop limit 0, or a named dongle -- never auto-detected), `EchoSuppressor`, the segment-isolation guard, and `open_gs_usb` (its test: `bus_gs_usb_test.py`) |
| `replay.py` | Replays real captures, offline into the core or onto a live segment, at any speed |
| `paths.py` | Where the data not in this repo lives (captures, DBCs, fixtures, `canre`), resolved from `VTRUX_DATA`; raises `PathsError` naming the variable rather than returning a wrong path, so a missing setting fails at the first call instead of producing an empty result. **Not every suite needs it** -- measured 2026-10-08 with it unset: `regress.py --all`, `test_invariants.py`, `test_released_into_hold.py`, `test_repeat_bursts.py` and `test_scorer_wiring.py` fail; `test_machine.py`, `test_signals.py`, `test_trips.py`, `paths_test.py`, `sims_test.py` and `run_scenario.py --list` do not. Under WSL pass it with `WSLENV=VTRUX_DATA/p` |
| `paths_test.py` | `paths.py`'s test: every resolver, and the refusal when `VTRUX_DATA` is unset. Needs nothing set itself |
| `stop_all.sh` | Stops every simulator process of a bench run. Read `firmware/esp-development.md` first: killing processes on Windows does not work the way this script's Linux recipe suggests |
| `sims_test.py` | The simulators' pure parts (`vehicle_sim.py`, `charger_sim.py`, `pack.py`), no bus, no processes |
| `regress.py` | Offline regression against the three real charge captures |
| `regress_offline_test.py` | `regress.py`'s own machinery, with no captures, so it runs anywhere |
| `test_signals.py` | Cross-checks `machine.py`'s hand-rolled bit extraction against cantools over randomised frames |
| `build_lookup.py` | Which image is the board running: `0x7F7` decoded with cantools, the 4-byte build id resolved against `firmware/builds/manifest.json`; the identity is the ELF hash, and `elf_sha256_of_bin()` reads it out of an image file. Refuses with a distinct reason each (unknown id, no `0x7F7`, an ambiguous prefix, a missing or empty manifest); `require_witness()` guards arms meaningful only on the instrumented build. **Imported, never copied** -- also by gen-inhibit's `x2_flash_custody.py` (its `elf_sha256_of_bin` and `APP_DESC_MAGIC`), so a change to either goes to the gen-inhibit reviewer before it lands |
| `build_lookup_offline_test.py` | `build_lookup.py`'s test: every reason code, no bus and no board; it imports the helper rather than re-deriving it (a test that re-derives the logic it checks agrees only with itself) |
| `capture_pins.py` | Spec 9.1 pinned stimulus: every capture-derived input -- the three real-capture differential traces, the two L3 stimulus files and `regress.py`'s captures -- is pinned by a hash HELD IN THIS REPO, never read from the input it covers (a hash inside its own file moves when a changed parser regenerates both). A trace neither pinned nor in a synthetic family fails, so a new capture-derived trace cannot join the suite unpinned |
| `capture_pins_test.py` | `capture_pins.py`'s test, and most of its cases must FAIL: a changed byte, a missing file, an unpinned capture-derived trace, and a stimulus regenerated together with its own header hash. Needs no `VTRUX_DATA` |
| `test_hold_wire.py` | A board run's spec 9 hold judged on the wire, and the vehicle stimulus premise, on synthetic witness rows and `--sp-log` receipts; each can-fail is one change to a record that passes. The hold below the table; the premise: a gap covered by a vehicle_sim stall -> VOID; a stale-BMS trip with no gap, a stall over 500 ms with the trip suppressed, a trip 300 ms into a gap, a gap no stall covers -> FAIL; the trailing gap (2026-10-15): BMS frames stopping 510 ms before a TRIP a=10 -> OK, "the bench's"; 300 ms -> FAIL; a stop in a session with no trip -> FAIL |
| `bench_suite.py` | The interposer's suites for the shared chain runner (`shared/bench/`, `vtrux_bench.run`; tester, 2026-10-18): `emulated` (no hardware: builds, host bridge / unit / diff, the logic tests, the 27 closed-loop sims at 1x, the capture-trace rebuild), `bench` (hardware only, the lock from start to finish; refuses unless the newest emulated run is on the same clean commit and matched its reference) and `harness` (the harness tests, mutants and can-fails, run when the harness changes). Its judges read each check's output: `host_bridge_judge` gives one result per host-bridge test and per L2 case (a case that opened and never reported is an ERROR; fewer than 35 L2 cases FAILS), `scenario_judge` the final `-> PASS`, `corrupt_pin_judge` `monitor_6h_diff.py --corrupt-pin`'s documented outcome (both pin checks FAIL, exit 1, both replays IDENTICAL), `c12_judge` the C12 transcript (a DATASHEET line differing outside C9 FAILS). Known FAILs and skips name their spec item or ruling. The core's own tests (`shared/bench/tests/`, each with a can-fail) cover the runner; `bench_suite_test.py` covers this file |
| `bench_suite_test.py` | `bench_suite.py`'s test: each judge driven to every verdict it gives, on output in the format its script prints (taken from the 2026-10-18 trial runs): a FAIL beside a VOID, `-> PASS` with a non-zero exit, the last verdict line, a DATASHEET line inside and outside C9's block, an L2 case opened and never reported, a host-bridge test with no line, fewer than 35 L2 cases. The corrupt-pin judge on one pin missed, exit 0, and a replay that differs. And `bench_identify`: the product's serial lines recognised on any line (the two HELLO? replies of the 2026-10-18 00:30 trial, which the first pattern missed), and a product reply never followed by the SLCAN probe. And the suites' shape: bench requires emulated; no lock or image outside bench; no mutation, can-fail or self-test in emulated or bench (`mcp-reset-canfail`, which needs the board, named as the one exception); no wall-clock check in harness; no check in two suites; every known FAIL names a check of its own suite; every skip names its spec item or ruling. `--can-fail` runs each test against a mutant (a judge that passes everything, one that ignores the exit code, bench without requires, a sim in harness, ...) and requires it to fail |
| `bench_identify.py` | What image the X2 is actually running, for the chain runner before and after each flash: the build id from `0x7F7` on one acknowledged power-up round, else `@HELLO <name> ... elf_sha256=` (the ESP-IDF bench images), else the SLCAN probe (`V` / `N`, "slcan:V0101:NF001"). It never sends `HELLO?` or `V` to an image showing product serial lines, and it raises if the boot capture cannot run, which the core reads as "unknown": no flash, the bench stops |

**A board run's checks in `run_scenario.py`** (step 4, 2026-10-14 and -15):
- **The start and the teardown.** charger_sim first, vehicle_sim 1.5 s
  later, the PCAN witness opened ACTIVE before either (a scored premise).
  After vehicle_sim exits: a 1.5 s settle with everything recording (the
  board's end-of-run trip stays on the witness), then the sims, then the
  witness, then the serial port -- whose close resets the board -- then a
  3.0 s rest. Before 2026-10-15 the serial port closed first and the
  rebooted board heard the charger's tail (item 6 R2, R4).
- **The pre-charger premise.** charger_sim's exit time (host perf clock) is
  written to `board_last_charger_host.txt` in the run folder; the next run
  reads it as its witness opens, and under 2.5 s the window before its first
  charger frame is NOT JUDGED under 2.3 (`pre_charger_premise`).
- **No-TRIP ends at the earliest sim stop**, the first sim's last frame on
  the witness (`earliest_sim_stop`, `no_trip_to_first_stop`). A TRIP before it
  FAILS. A trip after it is judged against spec 6: the stopped sim's stale
  code (10 BMS, 11 charger) 470-750 ms after its last frame, on the TRIP
  line's host stamp, else FAIL.
- **The stimulus premises.** `charger_stimulus`: no forwarded 0x18FFD4C0 gap
  over 400 ms in a session unless charger_sim's own stalls cover it and
  nothing was lost. `vehicle_stimulus`: the same for 0x410-0x440 against
  vehicle_sim's stalls, with spec 6 held regardless (a=10 not before 500 ms
  of wire gap, and on time once it is); each id's trailing gap, to the trip
  or the record's end, counts as the bench's and is held to spec 6.
- **Process priority.** run_scenario (whose threads are the serial tap and
  the witness) and every sim at ABOVE_NORMAL on Windows, read back with
  GetPriorityClass as a premise line (`priority_premise`).
- **Spec 9's hold on the wire** (`judge_hold_on_wire`), for scenarios that
  declare `hold_follows_loads`. HOLD spans from the witness's 0x7F4, bounded
  inward (the unjudged ms at each end printed); the setpoints charger_sim
  RECEIVED (`--sp-log`); chg_max, pack current and the SoC-100 anchor from
  the witness's 0x410 / 0x420; the EVSE cap from `test_invariants.Oracle`.
  The entry (2026-10-15, spec 5.2): the last receipt before the release must
  exist and be >= 6.10 A, and the first HOLD receipt must be that setpoint,
  the Oracle's cap at that receipt (a clamp), or one 0.1 A step from either,
  else UNRESOLVED; clause 2 judges that move with the cadence timed from the
  release, so an entry clamp is counted and the first step must come
  >= 0.28 s after the release. u, the spread of (receipt - the witness VCU
  page 01 it rode), pairs each receipt with the NEAREST page 01 within
  +/-150 ms whose bytes 0-1 and 4-7 match it (two USB adapters can stamp a
  receipt ~1.6 ms before its own frame), in float ms, with unpaired receipts
  counted in the verdict. A spacing below 0.28 s - u FAILS; one in
  [0.28 s - u, 0.28 s) is UNRESOLVED unless its VCU page 01s are >= 0.28 s
  apart. A record without the override's setpoint, or without a setpoint
  change after each injected load step, is UNRESOLVED, never PASS.
  `test_hold_wire.py`'s can-fails: a 0 A setpoint, 0.27 s steps (0.28 s
  passes), pack current above chg_max past the grace, no settle in band, the
  setpoint lines removed, a 275 ms spacing inside u; and from 2026-10-15 a
  Low Power release (6.10 -> 6.00 A clamp at +46 ms -> 5.90 A at +345 ms)
  PASSES with 1 clamp, the same with the cap at 6.10 A FAILS (a step 46 ms
  after the release), a first step 200 ms after the release FAILS, a step
  from the cap on the clamp frame is timed as the step (at entry: FAIL), no
  receipt before HOLD, a 2.0 A override and a 5.0 A first receipt are
  UNRESOLVED, a receipt 1 ms before its frame pairs with it, one whose
  time-nearest page 01 differs in byte 1 stays unpaired and is reported, and
  -1.6 / +15.3 ms gives u = 16.9 ms.
