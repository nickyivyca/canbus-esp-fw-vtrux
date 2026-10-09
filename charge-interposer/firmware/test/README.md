# firmware/test/

Tests that need the firmware source rather than the Python bench. The
behavioural tests of the core live one level up (`../../test_machine.py`,
`../../test_trips.py`, `../../test_invariants.py`) because they run against
`machine.py`; everything here is about the C++ port or the build.

| file | what |
|---|---|
| `build_check.py` | Builds **every** PlatformIO environment and checks the truck image carries no serial output (spec 8.3). Review item C2b. Also compares every image's source digest **against the tree on disk** (added 2026-10-05) -- the earlier check only compared the six images with each other, which six images built from the same older tree pass unanimously |
| `build_check_manifest_test.py` | Tests the harness (tester, 2026-10-07): `build_check.check_manifest` must find, for each image on disk, a manifest row with that name AND the ELF read from its bytes -- a row with the name but other bytes fails, as does an image with no application descriptor. Runs against a throwaway tree. |
| `digest_recheck_check.py` | Proves the source-digest re-check can fail. Test 1: `source_digest()` moves on a one-byte change to any hashed input and returns when it is reverted, and does *not* move on a file that is not hashed. Test 2: starts a real build, edits a hashed input while it runs, and requires the build to **fail** with `SOURCE DIGEST MISMATCH`; then requires an untouched build to pass. It deletes the environment's `.bin`/`.elf` first, because the post-action is attached to the image target and a `pio run` with nothing to do never reaches it -- the first version of this test omitted that and reported the check broken when the probe had simply never run |
| `host_diff/` | The Python/C++ differential: `machine.py` makes the golden, `machine.cpp` is replayed through `host_runner` and the two are diffed byte for byte |
| `host_unit/` | C++ unit tests for firmware logic pure enough to compile on a PC without faking a peripheral. Today: spec 8.2's `bridge_ok` interval rule |
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

## `host_unit/`

```
wsl bash ".../firmware/test/host_unit/run.sh"
```

Builds and runs every `test_*.cpp` in the folder with plain `g++ -std=c++11`.
The one test so far covers `src/bridge_health.h`, which is spec 8.2's
`bridge_ok` rule: health **since the previous status frame**, not since
boot. That rule was kept out of `main.cpp` precisely so it could be tested
here -- the rest of `main.cpp` needs the fake ports of spec 9.1 L2, which
will live beside this rather than in it.

Both halves of the claim are tested, because each is a different defect: a
failure inside one interval must clear the flag for **that frame**, and a
clean interval afterwards must set it again. Shown failing against both --
ignoring the vehicle-side input (the pre-2026-10-04 behaviour) fails 2
checks, and never advancing the baselines (the "since boot" defect of
review item B-3) fails 7.

## `host_diff/`

| file | what |
|---|---|
| `host_runner.cpp` | Drives `machine.cpp` over a trace, printing `make_golden.py`'s canonical format. `--diag` prints the four spec 8.2 status frames instead |
| `diag_stream.py` | The same status frames from `machine.py`. No golden: both sides are generated on the spot, so neither can go stale |
| `diff_all.sh` | Builds `host_runner`, diffs every pair, then diffs the diagnostics. Run under WSL on Windows (its `python3` is 3.10 and runs `machine.py` unchanged) |
| `make_golden.py` | Builds a trace/golden pair from a real capture |
| `make_synthetic.py` | Builds the `syn_*` pairs from generators, for the paths no capture reaches |
| `make_unit_traces.py` | Builds the `unit_*` pairs by recording what `test_trips.py` and `test_invariants.py` drive, so the unit tests reach `machine.cpp` and not only `machine.py`. From 2026-10-08 also the 58 `unit_def_*` traces: spec 6's contactor and charger-fault definitions, one input per trace (`test_trips.definition_cases`); and the 5 `unit_dense_*` traces, synthetic charges replayed with 2-10 ms ticks and injected page 03 (`test_invariants.densified`), so the port is held to the spec 4 hold of 03.02 / 03.07 and the 4.1 repeat spacing |
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
