# charge-interposer/

Three-way CAN bench for the Vtrux charge interposer: a board that sits between
the Bel Fuse charger and the rest of the powertrain bus so the truck can be
charged to 100 % even while the VCU's evap-purge routine wants to stop it at
80 %.

Same shape as the Coda DLCM flash bench
(`projects/coda/notes/dlcm/flash_sim/`): endpoint simulators talking to each
other over a localhost virtual bus, with the same code able to drive real
dongles later.

```
   vehicle_sim.py  ==[ vehicle segment ]==  interposer_sim.py  ==[ charger segment ]==  charger_sim.py
   VCU + A123 BMS       239.0.2.1:43213      the state machine      239.0.2.2:43214       Bel Fuse charger
         \________________________ HV cable / physics link ________________________/
                                    239.0.2.9:43215
```

The **physics link** is not a CAN bus. The charger and the pack are joined by
copper the interposer does not sit in, so the charger publishes the current it
is really delivering and the pack publishes its terminal voltage back. Without
it the bench has a hole: the pack would be charged *by a CAN telemetry frame*,
so the interposer could "charge the battery" by rewriting a status message, and
`--stealth` (which zeroes exactly that telemetry) would appear to stop the
charge dead.

When the real board exists, kill `interposer_sim.py` and wire the board in its
place; the other two do not change. Then swap `charger_sim.py` for the real
charger, then `vehicle_sim.py` for the real truck.

---

## Where this tree lives, and what not to build from it

This folder is `charge-interposer/` at the root of
`nickyivyca/canbus-esp-fw-vtrux`. It moved here on 2026-10-08 from
`projects/vtrux/tools/interposer/` in the `reverse-it` SeaDrive project;
that copy is frozen and is being replaced by a pointer.

**Never build or flash WiCAN firmware from this clone.** The WiCAN tree
around this folder is `gen-inhibit/rollback-fix` at `45b6871`, 92 commits
behind: diag schema 2, four versions behind the bench's 6, and no
transmit scheduler. (Verified in this clone: `GI_DIAG_SCHEMA_VER 2` at
`main/gen_inhibit_core.h:112` here, against 6 at :220 in
`~/Git/wican-fw-vtrux`.) Nothing here depends on it
and nothing here should be used to produce a WiCAN image. The gen-inhibit
component builds only from `~/Git/wican-fw-vtrux`.

**The spec and its paperwork did not move.** They stay in `reverse-it`
under `projects/vtrux/notes/`:

| What | Where, in `reverse-it` |
|---|---|
| Spec -- the source of truth for behaviour | `projects/vtrux/notes/charge-interposer-spec.md` |
| Review tracker | `projects/vtrux/notes/charge-interposer/charge-interposer-review.md` |
| Test evidence | `projects/vtrux/notes/charge-interposer-test-evidence.md` |
| Bench harnesses and captures | `projects/vtrux/notes/artifacts/interposer-firmware/` |

So every `projects/vtrux/...` path in this README is a path in
`reverse-it`, not in this repo. Relative links to them went stale the
moment this folder moved and have been rewritten to say so.

**Captures and DBCs are reached through two harness settings**, read by
`paths.py`. `REVERSE_IT_DIR` points at the root of the `reverse-it` project
folder (`canre`, and `projects/vtrux` below it with the captures); it
replaced `VTRUX_DATA`, which pointed at `projects/vtrux` (user,
2026-10-20). `VTRUX_PUBLIC_REPO` points at a clone of the public
`nickyivyca/canbus-reveng-vtrux-coda` for the vehicle DBCs, defaulting to
`../canbus-reveng-vtrux-coda` from the repo root. Both are the tester's,
and are described in `reverse-it`'s `tools-reference.md`.

---

## What the interposer does

Specified in **`projects/vtrux/notes/charge-interposer-spec.md`**, the single source of
truth for arming, the override, the release, the trips and the diagnostics.
This README covers only the bench. One design fact the bench code leans on
directly and is easy to get wrong: page 00 `cmd_Mode` 3 is a **hold**, not a
completion state -- the charger keeps delivering ~2.4 A in it, and 3 -> 1
restarts full current. The enum (0 Export, 1 Charger, 2 Stand-By, 3 Charger
Low Power, 4 Invalid) is confirmed from the EPRI binary and on the bus; see
`projects/vtrux/notes/epri-decompile.md`.

---

## Files

| File | What |
|------|------|
| `machine.py` | **The state machine.** Pure: no imports at all, integer arithmetic in fixed units, time passed in as a parameter. This is what ports to the micro; everything else exists to exercise it. |
| `protocol.py` | The `0x18EFC000` page map plus DBC-backed encode/decode for the BMS (`epri-pt-bus.dbc`) and charger (`BelInverter-v2.dbc`) frames. |
| `pack.py` | 116-cell LFP pack model: OCV curve, IR drop, per-cell imbalance, passive balancing, and the BMS charge-current taper. Fitted to three real captures. |
| `bus.py` | Transport factory (virtual `udp_multicast` or a real dongle brand) + `EchoSuppressor` + the segment-isolation guard. |
| `paths.py` | **Where the data that is not in this repo lives.** The captures, the DBCs, the harness fixtures and `canre` are in SeaDrive, outside this clone; this resolves them from `REVERSE_IT_DIR`, raising `PathsError` naming the variable rather than returning a wrong path, so a missing setting fails at the first call instead of producing an empty result set. Not every test needs it. Under WSL pass it with `WSLENV=REVERSE_IT_DIR/p`. |
| `paths_test.py` | Offline test for the above: every resolver, and the refusal when `REVERSE_IT_DIR` is unset. Needs nothing set itself. |
| `vehicle_sim.py` | VCU + A123 BMS + the VCM's evap flag (0x649). `--mode model` (closed loop) or `--mode replay` (real capture). |
| `charger_sim.py` | Bel Fuse charger/inverter, including fault injection. |
| `interposer_sim.py` | The board, as a process. I/O only -- all logic is in `machine.py`. |
| `replay.py` | Replays real captures: offline into the core, or onto a live segment, at any speed. |
| `run_scenario.py` | Runs the live three-way bench through named scenarios and checks the outcome. |
| `diag_cadence.py` | Spec 8.2 on the wire: the status-frame cadence and the immediate `0x7F4` on a state change, judged for a board run against a PCAN witness and the serial event lines. |
| `regress.py` | Offline regression against the three real charge captures. |
| `build_lookup.py` | **Which firmware image is the board actually running?** Reads `0x7F7` off the wire and resolves the 4-byte build id against `firmware/builds/manifest.json`; the identity is the ELF hash (reviewer, 2026-10-07), and it refuses rather than guessing. Imported by bench harnesses, never copied -- including gen-inhibit's `x2_flash_custody.py`, so a change to `elf_sha256_of_bin()` or `APP_DESC_MAGIC` goes to the gen-inhibit reviewer before it lands. |
| `build_lookup_offline_test.py` | Offline test for the above: no bus and no board. |
| `regress_offline_test.py` | Offline test for `regress.py`'s own machinery -- no captures, so it runs anywhere. |
| `sims_test.py` | Offline test for the simulators' pure parts (`vehicle_sim.py`, `charger_sim.py`, `pack.py`): no bus, no processes. |
| `bus_gs_usb_test.py` | Test for `bus.open_gs_usb`, the charger segment's Innomaker adapter: no hardware. |
| `stop_all.sh` | Stops every simulator process of a bench run, Linux only: it refuses (rc 2) anywhere else, since there it would match nothing and report "survivors: 0". `--self-test` kills a dummy under a `charge-interposer/` path and counts a live one as 1; `--can-fail` runs it on 4 mutants (the pre-2026-10-23 `tools/interposer`-only pattern among them). Read `firmware/esp-development.md` before running any of them. |
| `capture_pins.py` | **Spec 9.1 pinned stimulus.** Every capture-derived harness input is pinned by a hash HELD IN THIS REPO, never read from the input it covers: a hash stored inside its own file moves when a changed parser regenerates both. |
| `capture_pins_test.py` | Tests for the above. Needs no `REVERSE_IT_DIR`. |
| `requirements.txt` | `cantools` and `python-can`, the only dependencies outside the standard library. |
| `test_signals.py` | Cross-checks `machine.py`'s hand-rolled bit extraction against cantools over randomised frames. |
| `test_machine.py` | Unit tests for the core's decisions: accept/override/guard-band, the startup transient, release, trips, TYPE2 vs TYPE3, staleness, byte-exact idle forwarding. Also spec 2.3's vehicle-port mode -- the boot into listen-only, the source-address test with its two negatives and a control, an unread 0xC0 id, the 2 s revert at its boundary, and a session boundary that must NOT touch it -- and spec 5.2's HOLD -- the entry, both step directions, both band edges, the 0.28 s cadence (asserted at the boundary, with a 0.298 s page-01 stream that steps on every frame and a short gap that is still skipped) and the gate that makes the release itself count as a step, the leader cell's own step down and its veto on a step up, the EVSE cap as a clamp that does NOT wait for that gate, the mid-hold derate, the floor at 0 A, each exit, and the setpoint as the diagnostics report it -- and spec 3's arming, which takes a case per term of the condition: a re-arm after a TERMINATED and after a VCU-ended hold must each wait out a charger restart, a state 12 the charger has stopped refreshing must not arm, and neither must the 12 an ended session left behind. |
| `test_trips.py` | Spec 6: one test per fault source, from MONITOR and from OVERRIDE, plus the healthy-bus control. Carries its own copy of the `Bench` driver, since `test_machine.py` is the implementor's. |
| `test_invariants.py` | Specs 4, 4.1 and 8.1: the rules with no golden behind them, checked over every differential trace and over randomised traffic. The complement to the differential, which cannot see a mistake both cores make. |
| `test_released_into_hold.py` | Tests the HARNESS, not the core: scores spec 5.2's release-into-HOLD against the saved acceptance logs and against mutations of a real charger log. |
| `test_scorer_wiring.py` | Tests the HARNESS: that each scenario asks for the checks the spec requires, and that the literals come from the spec rather than from `machine.py`. |
| `test_repeat_bursts.py` | Tests the HARNESS: spec 4.1's repeat check, against the saved acceptance logs and mutations of them. |
| `test_hold_wire.py` | Tests the HARNESS for board runs (reviewer, 2026-10-15): spec 9's hold judged on the wire, from what `charger_sim` received against the PCAN witness, and the vehicle stimulus premise behind spec 6's trip. |
| `board_reset_test.py` | Tests the HARNESS: `run_scenario.py`'s `SerialTap` board reset and the bench's start order (reviewer, 2026-10-14). No hardware. |
| `diag_cadence_test.py` | Test for `diag_cadence.py`: no hardware. |
| `firmware/` | **The real board.** ESP32-CAN-X2 firmware: `machine.py` ported to C++ plus the two CAN port drivers. See `firmware/README.md`. The port is verified against this folder's Python by a differential trace test; `machine.py` remains the reference implementation. Stage 0 board self-test passed on hardware 2026-09-10 (18 checks / 0 failed); Stages 1-2, which need dongles, are not run. |

The harness's own checks and can-fails are not described here, so that the
description changes where the harness does (reviewer, 2026-10-15). They are in
`firmware/test/README.md`, which is the tester's, under "Harness files in
`charge-interposer/`".

---

## Quick start

```bash
# the fast tests -- run these after every edit  (~10 s, no CAN, no captures)
python3.13 charge-interposer/test_machine.py
python3.13 charge-interposer/test_signals.py
python3.13 charge-interposer/test_trips.py
python3.13 charge-interposer/test_invariants.py
python3.13 charge-interposer/test_released_into_hold.py
python3.13 charge-interposer/test_repeat_bursts.py
python3.13 charge-interposer/test_hold_wire.py

# offline regression against the real truck captures  (~4 min, no CAN)
python3.13 charge-interposer/regress.py --all

# the live three-way bench
python3.13 charge-interposer/run_scenario.py --list
python3.13 charge-interposer/run_scenario.py evap-override

# the same bench with the REAL BOARD in the middle (Stage 2)
py -3.12 charge-interposer/run_scenario.py evap-override     --external-interposer --serial-port COM7     --vehicle-transport kvaser --charger-transport pcan
```

### Running against the real board

`--external-interposer` forces `--time-scale 1.0`, because the board runs on its
own `millis()` and cannot be sped up. Two consequences that are easy to get
wrong:

- **Runs take roughly 20x longer than the scenario timeouts assume**, since
  every timeout was written against the default scale of 20. `--timeout-scale`
  therefore defaults to **20** under `--external-interposer`. Expect an hour or
  more per scenario: `evap-override` took 88 minutes to charge 79 % -> 100 % in
  real time. Set `--timeout-scale` explicitly to cut a run short.
- **An interrupted run can strand a simulator that is still transmitting.**
  Windows has no SIGINT for another process, so children are started in their
  own process group and interrupted with `CTRL_BREAK_EVENT`; before that fix the
  cleanup path raised and left `charger_sim` alive. After any run that did not
  exit cleanly, check for survivors -- and note that a process search for
  `_sim.py` will match its own command line unless ` -c ` is excluded.

Or drive the three processes by hand, in three terminals:

```bash
python3.13 .../charger_sim.py    --port 43214 --time-scale 20
python3.13 .../interposer_sim.py --vehicle-port 43213 --charger-port 43214 --time-scale 20
python3.13 .../vehicle_sim.py    --port 43213 --time-scale 20 --evap --soc 79 --stop-on-done
```

Start the charger and interposer first. `--time-scale` must match across all
three.

---

## Replaying real captures

Three ways, cheapest first.

**Offline** -- `regress.py`, or `replay.replay_offline()`. Feeds a capture
straight into the core with no CAN and no timing. Deterministic, ~20-80 s for a
multi-GB session, and the only mode that can assert byte-exactness. This is the
right tool for regression and for anything that needs to run fast.

**Half-replay** -- `vehicle_sim.py --mode replay --log <capture> --speed 25`
puts the captured VCU + BMS frames on the vehicle segment while the interposer
and `charger_sim.py` run live. The state machine sees genuine truck traffic.
`--speed 0` runs as fast as the bus will take it.

**Tap** -- `replay.replay_to_bus()` with both ID sets pushes the whole original
conversation onto one segment for a listener.

Bus selection is by **content**, never by channel number: the powertrain bus is
found by its anchor IDs (`0x051`, the BMS `0x4xx` cluster, the J1939 charger
family), because channel numbers vary per capture with dongle insertion order.

Large captures are filtered first by
`projects/vtrux/notes/artifacts/charge_cmd_filter.sh`, which strips a multi-GB log down
to the charge-control IDs in seconds. `regress.py` does this automatically and
deletes the filtered copy afterwards unless `--keep-filtered`.

### What replay can and cannot show

Replay proves the *decision* is right. It cannot show what happens next: once
the interposer overrides the stop, the captured charger still shuts down,
because in reality it was never told to keep going. Everything above 80 % SoC in
evap mode is territory no capture contains -- the truck has never been allowed
to go there. That is what the closed-loop model is for.

---

## Scenarios

The simulated VCU never ends a session on its own (spec 7), and since
2026-09-19 neither does the interposer: a release hands page 00 back and
repeats the VCU's standing Low Power command once (spec 4.1, 5.2 -- the VCU
sends page 00 only on change, so the charger would otherwise never hear it),
after which the charger sits in Low Power until the handle is pulled.
**The hold after the release is ours (spec 5.2, user, 2026-10-08):** the
core enters HOLD and replaces the page-01 setpoint from there on, following
the pack current in 0.1 A steps, because the VCU's own hold puts 0.85-2.55 A
into the pack for the first hour and on the parts truck, whose loads need a
**setpoint** of ~2.9-3.0 A (user, 2026-10-10), that drains it. Setpoints, not
currents: by the efficiency fit a ~2.9 A setpoint delivers about 1.6 A.
Page 00 and every other frame are the VCU's
again from the release. At a handle pull the simulated BMS drops HVIL 30 ms after the VCU's
flow drop, as the truck does, and the core must not trip on it (spec 6). The truck's real endings come from `charger_sim
--unplug-at` (handle pull N s after charging starts, `shutdownSource` 11),
`--unplug-after-hold-s` (handle pull N s after the charger last entered mode 3
-- in an override scenario that is N s after the release) and `--fault-at`
(internal fault, source 3). The VCU reacts to the charger leaving state 12
with flow 0 then STAND_BY, the BMS follows with EPO and the contactors, so
every terminating scenario ends in PASSTHROUGH. The VCM's evap flag (0x649)
is broadcast by `vehicle_sim` exactly as the truck does: 1 for the whole
session under `--evap`, 0 otherwise -- it is the override gate (spec 3).
Default `--time-scale` is 5 (spec 9).

| Scenario | What it proves |
|---|---|
| `evap-bypass` | Control run. Evap ceiling, state machine disabled: the truck must stop at 80 % exactly as it does today and sit in the hold until the handle is pulled. |
| `evap-override` | The point of the whole exercise: evap ceiling overridden, charge continues to a real top of charge, the core releases into our hold when the leader cell reaches 3585 mV (spec 5.1), and the handle pull closes the session out. |
| `evap-override-above-ceiling` | Plugged in at 83 % with evap pending (spec 7, the 83 % start): the VCU commands CHARGER with a 0 A setpoint and Low Power seconds later. The core must arm without a net charging current, wait out the 75 s arm delay without touching the (possible) startup transient, then override at 75 s and run to a real top of charge. |
| `evap-override-high-cell` | Spec 9 / 5.1: one cell slightly high (+1.5 % of charge, on a 0.4 % family). The leader reaches 3585 mV before the BMS's SoC reads 100 %, so the release is on it, with no trip and the leader under 3610 mV through the hold. |
| `evap-override-high-cell-far` | Spec 9 / 5.1: one cell far out of family on the high side (+5 % of charge, on a 0.4 % family), which no truck has shown. The leader reaches 3585 mV at ~96 % SoC with the full override current flowing; the release is on it, before the BMS's SoC reads 100 %, with no trip and the leader under 3610 mV through the hold. |
| `evap-override-imbalanced` | The parts-truck pack under our override: 1.0 % spread with one cell 2.5 % low (out of family on the low side, spec 9). The release is on the leader (5.1) with the weak cell still behind, visible as the spread and the weakest cell AT THE RELEASE (the reported SoC is clamped and cannot show it), with vmax never past 3.610 V. |
| `evap-override-imbalanced-hold` | Spec 9, the hold after the release (P3): the imbalanced parts-truck charge continues past the release into our hold, with the parts truck's 12 V load (266 W). The simulation is first checked against the truck's nine full-charge holds (P3a); then the hold must keep the pack at or under bcm_chg_max (2 s grace after a load drop or a fall in chg_max), step 0.1 A at least 0.28 s apart and never to 0 A, and settle in 2.6-3.3 A within 2 min of chg_max reaching 0 A, until the first injected load step. The 12 V load drops 266 -> 136 W 300 s after chg_max first reaches 0 A and returns to 266 W at 600 s. |
| `evap-override-16a` | The override on a 16 A EVSE (26 % pilot). Must still work, and must never command past the EVSE's 8 A DC ceiling -- J1772 compliance is the interposer's responsibility once it takes over the setpoint. |
| `evap-override-unplug` | The handle is pulled mid-override -- the truck's real ending. The charger goes to 15 with shutdownSource 11, the VCU drops flow and sends STAND_BY, and the interposer must simply get out of the way: no trip, no release, PASSTHROUGH. |
| `evap-override-bms-fault` | The BMS asserts cell_overvolt mid-override (spec 6). Trip straight to SAFE, transparent: the VCU's hold reaches the charger, and the handle pull closes the session out, which clears SAFE. |
| `normal-full` | A balanced pack charging normally to 100 %. The stop is genuine (evap flag clear), so the interposer must not touch a single byte; the hold ends at the handle pull. |
| `imbalanced-full` | The parts-truck case: badly imbalanced pack with one low cell. The BMS drives the taper, so again no intervention -- and the long CV balancing tail must pass through untouched. |
| `balance-hold` | The balance hold, ended the way every captured hold ended: the handle is pulled. Mode 3 parks the charger at the VCU's ~2.4 A hold while the pack balances; nothing in the VCU ends it. The interposer must sit through all of it untouched, must not read the hold as an evap ceiling (the flag is clear), and must go transparent on the VCU's STAND_BY. |
| `charger-fault-stop` | The corpus's majority ending, now known for what it is: a charger internal fault (shutdownSource 3) mid-charge, with no override in play. The interposer trips from MONITOR to SAFE, the VCU drops flow with the mode left at CHARGER and sends STAND_BY, and that clears SAFE. |
| `charger-fault-stop-flow-first` | charger-fault-stop with the fault flag raised 0.5 s AFTER the state edge, so the VCU's flow drop (0.45 s, source 3) reaches the core first: the minority order (1 of 19 truck endings), forced. The core must go transparent on the flow drop, trip on nothing after it, and end in PASSTHROUGH at the STAND_BY. |
| `fault-during-override` | An inverterFault while we are driving the charge. Must trip to SAFE (transparent, nothing originated) and then obey the VCU's close-out. |
| `fault-during-override-flow-first` | fault-during-override with the fault flag raised 0.5 s AFTER the state edge, so the VCU's flow drop reaches the core first (the minority truck order, forced): transparent on the flow drop, no trip and no repeat after it, PASSTHROUGH at the STAND_BY. |
| `hard-ceiling-failsafe` | Negative test with BOTH normal stops disabled: the release threshold is disabled AND the BMS's charge-current permission is pinned at 300 A so it never tapers. Only the interposer's own 3.610 V cell ceiling is left to stop the override; the trip is transparency, so what follows is the VCU's own hold, ended by the handle pull. Note what this took to set up -- while the core behaves, the leader reaching 3585 mV already releases (spec 5.1), so the release is turned off. Must start BELOW the 80 % ceiling so the VCU's stop comes after a charge is established. |
| `restart-after-standby` | Spec 9 (A5): a charge that fails, then a NEW session on the same run, across the VCU's STAND_BY. The realistic case is a truck plugged into one handle where the charge fails, moved to another, and charged fine. The second session must arm and override normally rather than stay latched from the first -- before 6.1 the core cleared its session state only on STAND_BY and carried SAFE and the charger-reached-12 flag into the next session. |
| `restart-no-standby` | Spec 9 (A5): the same restart, but with NO VCU STAND_BY in between -- the boundary is the pilot timer stepping back to 0 on its own. 11 of the 49 terminations in the survey do not end on a STAND_BY, so this is the path that mattered: without the pilot-timer boundary the core would still be latched. |
| `boot-mid-session` | Spec 9 (A3): the board powers up into a session already running. The pilot timer reads well past 2 minutes from the first frame, so the core stays in PASSTHROUGH for the whole of it and never overrides -- a freshly booted core cannot know the VCU's standing page-00 command, because page 00 is sent only on change. |
| `charger-silence-after-trip` | Spec 9 (6.1): the charger goes off the bus during an override. Staleness trips at 500 ms, and 20 s later the silence boundary resets the working state -- but NOT SAFE. Silence must not undo the latch that silence set. |
| `hvil-before-flow-drop` | Spec 9 (2026-10-03): the handle is pulled and HVIL opens 20 ms BEFORE the VCU drops the flow bit, the ordering in 5 of the 13 source-11 pulls in the corpus. The charger's plug-out report has already made the core transparent about a second earlier, so the early HVIL must NOT trip. Under the teardown window this replaced it logged a false 'HVIL open' fault at every such unplug. |

Offline regression cases (`regress.py`): `charge_M1`, `evap_80pct`,
`partstruck`, `above_ceiling_start` -- see the table at the top for what each
one contains. `above_ceiling_start` is the 83 % start replayed from the
truck's own frames: the core must arm, recognise the hold with the gate
satisfied, wait out the arm delay, and modify nothing before the real
STAND_BY (the handle was pulled before 75 s).

---

## Things that bit us, so they do not bite you

**A virtual CAN bus is not automatically local.** `python-can`'s
`udp_multicast` defaults to `hop_limit=1`, and the kernel routes 239.0.0.0/8 out
the default interface -- so every simulated frame goes on the real LAN. On
2026-09-01 this bench pushed 66,450 pps / 13.6 MB/s onto the house network and
disrupted it. `bus.py` now forces `hop_limit=0` and **refuses to start** if the
socket's multicast TTL is not 0. Do not "fix" that guard by removing it, and do
not additionally pin `IP_MULTICAST_IF` to loopback -- python-can joins the group
with `INADDR_ANY`, so sending via a different interface means nothing is
received. See the "Simulators, Virtual CAN Buses" section of `AGENTS.md`.

**Use `stop_all.sh` to stop the bench, not `pkill -f`.** `run_scenario.py`
spawns children via `sys.executable`, so their command lines start with
`/usr/bin/python3.13` and an anchored `^python3\.13` pattern misses them --
orphans survive and keep transmitting. And `pkill -f interposer` also matches the
shell running your own command. `stop_all.sh` matches by path fragment
(`charge-interposer|tools/interposer`), filters on `/proc/<pid>/comm`, and
verifies afterwards. Until 2026-10-23 it matched only `tools/interposer`, the
path before the move, so in this repo it killed nothing and still reported
"survivors: 0"; `bash stop_all.sh --self-test` on the Linux host proves the
match before you rely on it.

**Two virtual segments must differ by PORT, not just by multicast group.**
Verified on this machine: two `udp_multicast` buses sharing a UDP port receive
each other's traffic even on different groups, because the socket binds to the
port and receives every group joined on it. With a shared port the charger hears
the VCU directly, the interposer is bypassed, and the charger sees both the
original and the rewritten command -- which shows up as its mode flapping, and
makes every result meaningless. `bus.assert_segments_isolated()` refuses to
start such a run.

**The core is clocked in SIMULATED time.** Every threshold in `machine.py` is in
vehicle time (a 60 s arming delay means 60 s of charging). Feeding it wall time
on an accelerated bench silently divides all of them by `--time-scale`.

**`--time-scale` 25 is the ceiling; 40 breaks.** Bus timing is expressed in
simulated time, so accelerating means proportionally more frames per wall
second. Measured on this machine: at 25x the interposer forwards 29,741 of
30,000 expected frames with zero saturation warnings, and scenarios run clean.
At 40x runs trip on `BMS frames stale` -- not a core defect but an artifact of
the 500 ms staleness window shrinking to 12.5 ms of wall time. Use 25x for the
live bench and the offline harness (`regress.py`) for anything faster.

(An earlier version of this note blamed saturation above 30x. That was really a
64-frame cap on how many frames the interposer would retire per loop pass, which
made it give up mid-burst and warn spuriously; the cap is now 512 and the true
limit is the staleness watchdog.)

**`bcm_mainc_stat` is 12 while charging, not 11.** `projects/vtrux/README.md` documents 11 as
the normal running state -- that is the *driving* steady state. Measured across
`vtrux_charge_M1` (8681 points) and the 80 % session (9180 points), charging
sits at 12. Both are accepted.

**`bcm_alarm` TYPE2 is not a fault.** It is a persistent latched state this pack
charges and drives under (see
`notes/specific-trucks/bcm-alarm-type2-investigation.md`). Tripping on it would
disable the board permanently on a truck that has it latched -- which is this
truck. Only TYPE3 trips.

**The charger blips out of state 12 for single frames during normal charging** --
including once at the 80 % cutover itself. A real fault latches; these do not.
Hence `chg_state_debounce_ms`.

**Arm on an observed charge, never on the VCU's command alone.** The command
leads reality by a long way (CHARGE at t=79.6 s with the contactors still open),
and may never be seen at all if the capture or the board starts mid-session.

---

## Current results

The table below is from the original vmax-based release and predates the
2026-09-16 spec; it is kept as the bench's baseline. Hardware results and
everything since live in `projects/vtrux/notes/interposer-firmware-bringup.md`.

All seven scenarios and all three offline regressions passed, on this machine, at
`--time-scale 25`.

| | result | key evidence |
|---|---|---|
| `regress.py charge_M1` | PASS | 4.06 M frames forwarded **byte-identical**. Since 2026-09-30 this is the real-capture case for the spec 3 boot fallback: the capture opens 99 minutes into the session, so the core stays PASSTHROUGH throughout and never reaches the accept. It used to recognise the genuine stop at vmax 3594 mV / chg_max 2.75 A, which is what its expectation asserted before the fallback existed. |
| `regress.py evap_80pct` | PASS | override fires at t=7427.0 s, `00 01 03` -> `00 01 01`, vmax 3340 mV, chg_max 300 A, 18,735 frames modified (re-measured 2026-10-03; it was 18,753 on 2026-09-30). The reduction is the spec 6 plug-out rule: the override now ends at t=9297.9650 s on the charger's `vehicleConnected` 1 -> 0 report instead of at the VCU's flow drop 1.1286 s later, and the 18 are rewrites the core no longer makes in that gap. Established by controlled replay -- disabling that rule alone restores 18,753 -- see `projects/vtrux/notes/artifacts/evap80_rewrite_delta.py` (regenerate the filtered capture first with `regress.py evap_80pct --keep-filtered`). **This is where real-frame override coverage lives** -- no capture-derived differential case reaches an override any more, see `firmware/README.md`. |
| `regress.py partstruck` | PASS | 16.3 M frames **byte-identical**; startup transient at t=31 s ignored; genuine stop accepted at vmax 3590 mV / chg_max 0 A |
| `evap-bypass` | PASS | SoC pinned at 82.3 % -- the truck's behaviour today |
| `evap-override` | PASS | override at 80 %, release at vmax 3595 mV, SoC 100 %, vmax peak 3.596 V |
| `evap-override-16a` | PASS | same, on a 16 A EVSE; **peak current exactly 8.0 A** |
| `normal-full` | PASS | genuine stop at vmax 3587 mV / chg_max 3.00 A, untouched |
| `imbalanced-full` | PASS | genuine stop, no intervention, CV tail passes through |
| `fault-during-override` | PASS | inverterFault -> stop burst emitted -> latched SAFE |
| `hard-ceiling-failsafe` | PASS | with a stuck BMS limit, trips at vmax 3610 mV; peak 3.62 V |

**The unit and harness suites are deliberately not in that table.** A pass
count is evidence, and evidence belongs in
`projects/vtrux/notes/charge-interposer-test-evidence.md` against a stated sha
(reviewer, 2026-10-12). The counts that used to sit here drifted instead:
three of the four were wrong by the time anyone checked, and two of those
counted suites the tester owns, so they went stale without this file being
edited at all. The suites are named under [Files](#files); the commands to run
them are under [Quick start](#quick-start).

One number worth knowing: in the hard-ceiling test vmax peaked at **3.62 V**,
20 mV past the 3.610 V trip. The BMS broadcasts at 10 Hz, so the ceiling is
detected up to one frame late and the pack overshoots by however much it climbs
in that window. Fine for LFP at these currents; it would not be if the trip
threshold were ever moved close to a genuinely damaging voltage.

## Status and caveats

`0x18EFC000` is now defined in `projects/vtrux/vtrux-powertrain-experimental.dbc` as
`VCU_ChargerCmd_18EFC000`. Page 00's `cmd_Enable`/`cmd_Mode` are declared as real signals
with a `VAL_` table — confirmed by two independent methods. **Every other page remains a
hypothesis** and is deliberately declared positionally only (one byte per multiplexer id, so
cantools does not raise on those frames) with the reasoning in `CM_` comments. Page 01's
setpoint reading is strong but single-method. Nothing here has been run against the real
charger or the real truck.

The pack model is **fitted, not derived** -- OCV curve, internal resistance,
balancing threshold and BMS taper all reproduce the observable behaviour of
three captures; they are not a cell datasheet. The balancing rule in particular
is a deliberate stand-in: the real BMS rule is unresolved in the project's own
notes, and `pack.py` bleeds on one rule and reports the count on another so that
both observables match. None of the interposer's decisions depend on it -- it
keys off `bcm_chg_max` and `bcm_cell_vmax`, which the model reproduces directly.

Known fidelity gap: the real charger held ~2.2 A indefinitely at the 80 %
ceiling while the pack voltage slowly fell (pack relaxation, which the model has
no term for). The modelled hold current decays where the real one persists. The
error is in the conservative direction.

`--stealth` (masking charger-to-vehicle telemetry while overriding) exists but
is **off by default and unverified**. We do not know whether the VCU misbehaves
when it sees current flowing after it commanded a stop. That is a question for
the bench and then the vehicle, not for a guess.

**Hardware failsafe, outside the state machine:** this file used to require
that the micro's watchdog fail the board to *electrical passthrough* rather
than to a dead bus -- a normally-closed bypass relay across the two CAN
segments, or store-and-forward with a hard forwarding deadline -- so that a
hung micro degraded to a plain wire.

**That is no longer a requirement. See spec 2.1** (`projects/vtrux/notes/charge-interposer-spec.md`),
which supersedes this paragraph: the specification governs the program while
it is running, the board does not degrade to a wire when it fails, and
nothing requires it to. The expected truck-level consequence of the board
going offline is that charging simply stops, which a bring-up test is to
confirm (spec 10). Recorded 2026-09-30 from review item A2; the requirement
was never carried into the spec and was never implemented.
