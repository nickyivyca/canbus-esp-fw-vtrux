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

**Captures and DBCs are reached through two harness settings.**
`VTRUX_DATA` points at `reverse-it`'s `projects/vtrux` (the captures and
`canre`); `VTRUX_PUBLIC_REPO` points at a clone of the public
`nickyivyca/canbus-reveng-vtrux-coda` for the vehicle DBCs, defaulting to
`../canbus-reveng-vtrux-coda` from the repo root. Both are the tester's.
They are described in
`projects/vtrux/notes/plans/charge-interposer-acceptance-round-1.md` and
move into the harness docs when the tester's path-fix commit lands.

**Until that commit lands, not every command below runs from this clone.**
The paths in them have been rewritten to this location, but `protocol.py`,
`replay.py`, `bus.py` and `regress.py` still reach for the DBCs, `canre`
and the captures by their old relative paths. Fixing those is a tester
commit, not an implementor one.

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
| `vehicle_sim.py` | VCU + A123 BMS + the VCM's evap flag (0x649). `--mode model` (closed loop) or `--mode replay` (real capture). |
| `charger_sim.py` | Bel Fuse charger/inverter, including fault injection. |
| `interposer_sim.py` | The board, as a process. I/O only -- all logic is in `machine.py`. |
| `replay.py` | Replays real captures: offline into the core, or onto a live segment, at any speed. |
| `run_scenario.py` | Runs the live three-way bench through named scenarios and checks the outcome. |
| `regress.py` | Offline regression against the three real charge captures. |
| `build_lookup.py` | **Which firmware image is the board actually running?** The identity is the ELF hash (reviewer, 2026-10-07): an OK result carries EVERY manifest row matching it, and `elf_sha256_of_bin()` reads the hash out of an image file so what is flashed is identified by its bytes, never its name. Reads `0x7F7` off the wire, decodes it with `cantools`, and resolves the 4-byte build id against `firmware/builds/manifest.json`. Imported by bench harnesses, never copied. Refuses rather than guessing, with a distinct reason for each failure: unknown id, no `0x7F7` frames at all, an ambiguous prefix, and a missing or empty manifest -- four different problems that must not collapse into one. `require_witness()` is the guard for arms that are only meaningful on the instrumented build. |
| `build_lookup_offline_test.py` | Offline test for the above: every reason code, no bus and no board. Modelled on the gen-inhibit `autoarm_bit_offline_test.py`, and it IMPORTS the helper rather than reimplementing it -- a test that re-derives the logic it checks agrees only with itself. `py -3.14 charge-interposer/build_lookup_offline_test.py` |
| `test_signals.py` | Cross-checks `machine.py`'s hand-rolled bit extraction against cantools over randomised frames. |
| `test_machine.py` | Unit tests for the core's decisions: accept/override/guard-band, the startup transient, release, trips, TYPE2 vs TYPE3, staleness, byte-exact idle forwarding. |
| `test_trips.py` | One test per fault source in spec 6, from MONITOR **and** from OVERRIDE, each driving exactly one source with every other input held healthy so it cannot pass on a different one. Plus the control: a healthy bus must not trip. Rev 2 (tester, 2026-10-07): the thresholds at their edges (3609/3610 mV, the 5 s debounce, 500 ms staleness per message, the 6 h cap counted from the override's start), the spec's non-trips (TYPE2, undervolt, temperature, `chg_max` 0), "nothing changed on the bus" judged byte by byte, the literal 20, and `test_thresholds_can_fail` against cores built with wrong thresholds. Carries its own copy of the `Bench` driver, since `test_machine.py` is the implementor's. **Rev 3 (tester, 2026-10-08):** spec 6's definitions of 2026-10-07 -- every `bcm_mainc_stat` value alone (only 11 and 12 do not trip) and every `0x18FFD4C0` mux-3 flag alone (only the four named faults trip), from MONITOR and OVERRIDE, recorded as `unit_def_*` traces for the port; MONITOR past 6 h does not trip; each with a can-fail test. |
| `test_invariants.py` | Rules with no golden behind them -- every frame forwarded exactly once, nothing rewritten outside an override, no repeat over 20 frames, no command of our own, never above the BMS's permission or the EVSE cap -- checked over every differential trace and over randomised traffic. The complement to the differential, which cannot see a mistake both cores make. Rev 2 (tester, 2026-10-07): the permission and cap come from an `Oracle` that decodes the bus, not from the core's own state; every override page 01 must equal the permission capped by the EVSE; repeats are checked for direction and bytes (4.1); mirrors for direction and content (8.1); `test_checker_catches_each_mutation` proves 16 wrong cores are each rejected for their own reason. `run_scenario.py` runs it over every scenario's trace. **Rev 3 (tester, 2026-10-08):** spec 4's held 03.02 / 03.07 during an override and spec 4.1's 50 +/- 10 ms repeat gaps, judged on a densified replay of the traces (2-10 ms ticks, injected page 03) because the traces as recorded tick every 100 ms and carry no 03.02 / 03.07 in an override; population guards and four mutations. |
| `test_released_into_hold.py` | Tests the HARNESS, not the core: `released_into_hold` scored against every saved acceptance log that declares it, then against one mutation of a real charger log per clause, each of which must be rejected for its own reason. Written after rev 1 failed a correct `evap-override-above-ceiling` by latching the VCU's pre-override Low Power -- a check that was wrong in one scenario and right in eight. |
| `test_scorer_wiring.py` | Tests the HARNESS: the scorer changes of 2026-10-07 (tester). Every scenario asks for `diag_frames_ok()` and every termination scenario for `no_rewrite_after_release()` (spec 9); the burst length, firmware and schema versions are the spec's literals, not `machine.py`'s; the version is checked in every `0x7F7`; `check_invariants()` -- `test_invariants.py` run over each scenario's recorded trace -- fails on a missing trace and passes a known-good one; the 16 A scenario no longer scores the charger sim's clamped current. |
| `test_repeat_bursts.py` | Also tests the HARNESS: the spec 4.1 repeat check, against every saved acceptance log that declares it and then six mutations. Rev 1 demanded exactly 20 frames per REPEAT, which is unreachable on a correct core in `fault-during-override` -- a burst is 20 x 50 ms = 1,000 ms and the VCU drops flow REACT_HOLD_S = 1.0 s after the charger leaves state 12, so the count is jitter (13 at 1x, 19 at 5x, 7 on the board). The two mutations that carry the weight are the ones that must still FAIL: a short burst with the VCU's close-out removed, and a truncated release burst the VCU said nothing near. **Tester, 2026-10-08:** an ABANDONED end now passes only with a spec 6.1 boundary witnessed in its window (VCU STAND_BY / EXPORT in the vehicle log, a handle pull or replug or 20 s of charger silence in the charger log); `evaluate()` passes the charger log to the scorer for this. |
| `firmware/` | **The real board.** ESP32-CAN-X2 firmware: `machine.py` ported to C++ plus the two CAN port drivers. See `firmware/README.md`. The port is verified against this folder's Python by a differential trace test; `machine.py` remains the reference implementation. Stage 0 board self-test passed on hardware 2026-09-10 (18 checks / 0 failed); Stages 1-2, which need dongles, are not run. |

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
2026-09-19 neither does the interposer: a release is transparency plus one
repeat of the VCU's standing Low Power command (spec 4.1, 5.2 -- the VCU
sends page 00 only on change, so the charger would otherwise never hear it),
after which the VCU's hold reaches the charger and lasts until the handle is
pulled. At a handle pull the simulated BMS drops HVIL 30 ms after the VCU's
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
| `evap-bypass` | **Control.** Evap ceiling with the state machine disabled: the truck stops at 80 % and sits in the hold until the handle is pulled, exactly as it does today. Pair this with `evap-override`. |
| `evap-override` | The whole point: override at 80 %, charge continues under the BMS's own permission, we go transparent when that permission collapses, the VCU's hold reaches the charger, the handle pull closes out. Checks that nothing is rewritten after the release. |
| `evap-override-above-ceiling` | Plugged in at 83 % with evap pending (the truck's 83 % start, spec 7): CHARGER with a 0 A setpoint, Low Power seconds later. Must arm without a net charging current, wait out the 75 s arm delay untouched, then override and run to the top. |
| `evap-override-early-bms` | The BMS calls the pack full early (`--bms-taper-shift-mv 50`): the release must land at *its* point, ~3.54 V. |
| `evap-override-imbalanced` | The parts-truck pack under our override: release on the BMS with the weak cell still short of the knee, checked as spread and vmin **at the release** (spec 9). They used to be read at the flow drop -- the end of the session, minutes of hold later, with the pack relaxing throughout -- so they described something else. The core reports vmin alongside vmax in its top-of-charge event for this. |
| `evap-override-16a` | The same on a 16 A EVSE (26 % pilot). Must never command past the 8 A DC ceiling. |
| `evap-override-unplug` | Handle pulled mid-override. No trip, no release: the VCU's STAND_BY drops us to PASSTHROUGH. |
| `evap-override-bms-fault` | `cell_overvolt` asserted mid-override: trip straight to SAFE (transparent), the VCU's hold continues, the handle pull clears it. |
| `normal-full` | Balanced pack to 100 %. The stop is genuine (evap flag clear), so not a byte is touched; the hold ends at the handle pull. |
| `imbalanced-full` | The parts-truck case, one persistently low cell. The BMS drives the taper; the long CV balancing tail passes through untouched. |
| `balance-hold` | The mode-3 hold, ended by a handle pull ~3000 s in, as every captured hold was. Untouched throughout. |
| `charger-fault-stop` | The corpus's majority ending: a charger internal fault mid-charge, no override. Trip from MONITOR to SAFE, the VCU closes out, SAFE clears. |
| `fault-during-override` | An `inverterFault` mid-override. Trip to SAFE, nothing originated, then the VCU's close-out. |
| `hard-ceiling-failsafe` | **Negative test.** Release disabled AND the BMS's charge-current permission pinned at 300 A (`vehicle_sim --bms-stuck-chg-max`), leaving only the 3.610 V cell ceiling; the trip is transparency, so the VCU's own hold follows and the handle pull ends it. While the BMS behaves, `bcm_chg_max` collapsing already releases, so the ceiling only matters if the BMS limit signal itself fails. |

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
shell running your own command. `stop_all.sh` matches by path fragment, filters
on `/proc/<pid>/comm`, and verifies afterwards.

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
| `test_machine.py` | 58/58 | core decisions incl. J1772 caps (17 when this table was written) |
| `test_trips.py` | PASS | 14 fault sources, each from MONITOR and from OVERRIDE |
| `test_invariants.py` | PASS | 24 traces + randomised traffic, no golden |
| `test_signals.py` | PASS | bit extraction matches cantools |
| `test_released_into_hold.py` | PASS | 5 saved logs + 6 mutations + 2 clock cases, rev 2 |
| `test_repeat_bursts.py` | PASS | 7 saved logs + 6 mutations + 2 window cases |

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
