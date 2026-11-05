# charge-interposer/

The Vtrux charge interposer: firmware for the Autosport Labs ESP32-CAN-X2
(ESP32-S3), built on ESP-IDF v5.4.2 with arduino-esp32 3.2.1 as a component
since port step 2a (user, 2026-11-03), on branch
`charge-interposer/main`. It sits between the Bel Fuse charger and the rest
of the powertrain bus. The simulation bench is described in `SIMULATOR.md`.

This file is the project's file map: who owns each file. It belongs to the
charge-interposer reviewer, who decides ownership when it is unclear and
records it here (the repo's `AGENTS.md`, "Who owns what"). Paths starting
`$REVERSE_IT_DIR` are in reverse-it and not yet moved.

## Spec and paperwork, in reverse-it

| File | What | Owner |
|---|---|---|
| `$REVERSE_IT_DIR/projects/vtrux/notes/charge-interposer-spec.md` | The spec: the source of truth for behaviour | Reviewer |
| `$REVERSE_IT_DIR/projects/vtrux/notes/charge-interposer/charge-interposer-review.md` | The review tracker | Reviewer |
| `$REVERSE_IT_DIR/projects/vtrux/notes/plans/can-bus-fault-injection-test.md` | Fault-injection test plan (user, 2026-10-12) | Reviewer |
| `$REVERSE_IT_DIR/projects/vtrux/notes/charge-interposer-test-evidence.md` | Verification | Tester |
| `$REVERSE_IT_DIR/projects/vtrux/notes/interposer-firmware-bringup.md` | The implementor's working log | Implementor |
| `$REVERSE_IT_DIR/projects/vtrux/notes/plans/interposer-outstanding-work.md`, `interposer-verification-round-2.md` | Two earlier plans (reviewer, 2026-10-10) | Implementor |
| `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/interposer-firmware/` | The trace / golden fixtures, `l3_stimulus.txt`, the run folders, the scripts the tester wrote there that were not moved (reviewer, 2026-10-08), and that folder's `README.md` (reviewer, 2026-10-07). The 39 scripts the suites run moved into `bench_drivers/`, `harness_checks/` and `rebuild_capture_traces.py` here (port step 0, reviewer 2026-10-26); their SeaDrive copies are pointers until nothing reads them | Tester |

## This folder

| File | What | Owner |
|---|---|---|
| `README.md` | This file | Reviewer |
| `diag/interposer_diag_schema.md`, `diag/vtrux-interposer-diag.dbc` | The diag schema and its DBC, from schema 4: published interfaces, so spec (user, 2026-10-19). The reverse-it copies stay until every reader moves, then become pointers | Reviewer |
| `machine.py` | The state machine: pure, integer arithmetic, time passed in. The reference implementation the board ports | Implementor |
| `test_machine.py` | Unit tests for the core's decisions | Implementor |
| `firmware/main/` | The board firmware, the IDF project's `main` component: `machine.py` ported to C++, the two CAN port drivers, `board_ports.h` (each port's pins from the board layer), the variants' mains, its `CMakeLists.txt` (each variant's sources and switches) and `idf_component.yml` (the dependencies) | Implementor |
| `firmware/CMakeLists.txt` | The ESP-IDF project and its variant option `INTP_VARIANT` (port step 2a) | Implementor |
| `firmware/dependencies.lock` | The resolved components | Implementor |
| `firmware/sdkconfig` | The committed configuration every variant builds from | Implementor |
| `firmware/vtrux_build.json` | The project's file for the shared build tooling | Implementor |
| `firmware/shared_pins.json` | The shared-code pins (shared/tools R23), owned per entry: `shared/tools` the implementor's, the harness's shared entries the tester's; moved only on a reviewer batch (reviewer, 2026-11-03) | Per entry |
| `firmware/README.md` | The firmware's README | Implementor |
| `vtrux_tools.py` | The launcher: starts the pinned shared/tools (`release`, `image`, `digest`) and makes dev builds (`build <variant>`); refuses a shell exported against another `IDF_TOOLS_PATH` (release route B, user 2026-11-03) | Implementor |
| `test_vtrux_tools.py` | Its self-test, with `--can-fail`; not evidence | Implementor |
| `host_unit/` | Unit tests of the core's internals, with its `README.md` (moved from `firmware/test/` at port step 2a) | Implementor |
| `.gitattributes`, `.gitignore` | Line endings and ignores for the folder | Implementor |
| `SIMULATOR.md` | The simulation bench write-up | Implementor |
| `requirements.txt` | Python dependencies of the sims and the harness | Tester |
| `protocol.py` | The `0x18EFC000` page map and DBC-backed encode/decode for the BMS and charger frames | Tester |
| `pack.py` | 116-cell LFP pack model, fitted to three real captures | Tester |
| `bus.py` | Transport factory (virtual `udp_multicast` or a real dongle), `EchoSuppressor` and the segment-isolation guard | Tester |
| `paths.py` | Resolves the data outside this repo from `REVERSE_IT_DIR`, refusing when it is unset; gives `code_dir()` (refuses an `INTERPOSER_CODE` naming another tree), the pinned canre, and `import_canbench()` (the pinned canbench, through `CAN_TOOLS_DIR` and `can_tools_pin.py`; nothing on `sys.path`) | Tester |
| `paths_test.py` | Offline test for `paths.py`, with a `--can-fail` mode | Tester |
| `pins.json` | The harness pin file: canre at the pinned public-repo commit (port step 0, reviewer 2026-10-26) and can-tools at the pinned commit (user, 2026-10-30: each project owns its can-tools pin) | Tester |
| `canre_pin.py`, `canre_pin_test.py` | Install and check the pinned canre (`scratch/canre/<commit>`; `CANRE_DIR` on madhouse), and its test | Tester |
| `can_tools_pin.py`, `can_tools_pin_test.py` | Install and check the pinned can-tools `canbench/` (`scratch/can-tools/<commit>`, from the `CAN_TOOLS_DIR` clone), and its test | Tester |
| `scratch_dir.py`, `scratch_dir_test.py` | The harness's throwaway run folders under `scratch/charge-interposer-harness/<tool>/`, each with an owner file, pruned when finished and removed read-only files and long paths included (AGENTS.md, "Removing scratch"); its `sweep` reports old folders before any `--apply`; and its test | Tester |
| `scratch_dir_check.py` | Runs each harness entry point that makes run folders with a read-only git tree planted in them, and with the read-only handler taken out, and checks nothing is left or the leftover is reported, judged after the process exits; `--self-check` proves it on removal in a `finally`, at exit, and none | Tester |
| `vehicle_sim.py` | VCU, A123 BMS and the VCM's evap flag (0x649), as a model or a capture replay | Tester |
| `charger_sim.py` | Bel Fuse charger/inverter, with fault injection | Tester |
| `interposer_sim.py` | The board as a process: I/O only; the logic is `machine.py` | Tester |
| `replay.py` | Replays real captures, offline into the core or onto a live segment | Tester |
| `sims_test.py` | Offline test for the simulators' pure parts | Tester |
| `stop_all.sh` | Stops a bench run's simulator processes; Linux only | Tester |
| `run_scenario.py` | Runs the live three-way bench through named scenarios and checks the outcome | Tester |
| `board_reset_test.py` | Harness test: `run_scenario.py`'s `SerialTap` board reset and the start order (reviewer, 2026-10-14) | Tester |
| `regress.py` | Offline regression against the three real charge captures | Tester |
| `regress_offline_test.py` | Offline test for `regress.py`'s machinery | Tester |
| `test_signals.py` | Cross-checks `machine.py`'s bit extraction against cantools | Tester |
| `test_trips.py` | Spec 6: one test per fault source | Tester |
| `test_invariants.py` | Specs 4, 4.1 and 8.1: the rules with no golden behind them | Tester |
| `test_released_into_hold.py` | Harness test: spec 5.2's release-into-HOLD scoring | Tester |
| `test_repeat_bursts.py` | Harness test: spec 4.1's repeat check | Tester |
| `test_scorer_wiring.py` | Harness test: each scenario asks for the checks the spec requires | Tester |
| `test_hold_wire.py` | Harness test: a board run's spec 9 hold judged on the wire, and the vehicle stimulus premise (reviewer, 2026-10-15) | Tester |
| `build_lookup.py` | Which image the board is running: resolves `0x7F7`'s build id against the candidate images' own app descriptors, by ELF hash (no manifest from port step 2a). gen-inhibit imports two names from it (below) | Tester |
| `build_lookup_offline_test.py` | Offline test for `build_lookup.py` | Tester |
| `capture_pins.py` | Spec 9.1: hashes held in this repo that pin every capture-derived harness input (tester, 2026-10-08) | Tester |
| `capture_pins_test.py` | Tests for `capture_pins.py` | Tester |
| `rebuild_capture_traces.py` | Rebuilds the capture-derived traces against their pins (spec 9.1). Moved from reverse-it (port step 0, reviewer 2026-10-26) | Tester |
| `bus_gs_usb_test.py` | Test for `bus.open_gs_usb`, the charger segment's adapter, and `bus.open_bus` on canbench's `Adapter` detect results; no hardware; with a `--can-fail` mode | Tester |
| `diag_cadence.py` | Spec 8.2 on the wire: the status-frame cadence and the immediate `0x7F4` | Tester |
| `diag_cadence_test.py` | Test for `diag_cadence.py`; no hardware | Tester |
| `bench_suite.py`, `bench_suite_test.py`, `bench_identify.py` | The chain runner's three suites, their test and its identify hook (reviewer, 2026-10-19) | Tester |
| `accept_adapter.py`, `accept_adapter_test.py` | The project's adapter for the acceptance runner (shared/bench ACCEPT-SPEC A15), and its test (reviewer, 2026-11-01) | Tester |
| `accept_smoke.toml` | The adapter's smoke list: a test fixture, not an acceptance list; its records are never evidence (reviewer, 2026-11-02) | Tester |
| `accept_build_smoke.toml` | The adapter's build smoke list: one `idf-build` build of a variant through the runner; a test fixture, not an acceptance list; its records are never evidence (reviewer, 2026-11-04) | Tester |
| `scratch_run.sh`, `scratch_run_test.py` | The harness's bash scripts' run folders, as `scratch_dir.py` gives the Python ones, and its test (reviewer, 2026-11-02) | Tester |
| `twai_fake_option_test.py` | Test of `host_bridge`'s `TWAI_FAKE` option and its can-fail (reviewer, 2026-11-02) | Tester |
| `shared_pin.py`, `shared_pin_test.py` | The harness's shared code from the read-only `../shared-<tag>/` checkout, checked against `firmware/shared_pins.json`, and its test (port step 2a) | Tester |
| `firmware/vtrux_checks.json` | The release profile and the build-switch table (shared/tools R12, R18, R20, R29) | Tester |
| `bench_wican.py`, `bench_wican_test.py` | The WiCAN as a board of the bench suite, read and rebooted but never flashed, and its test (reviewer, 2026-10-24) | Tester |
| `bench_drivers/` | Whole directory: the bench suite's hardware drivers and helpers, `reset_check`, `keepalive_probe`, and `script_can_fail` with its test. Moved from reverse-it (port step 0, reviewer 2026-10-26); `README.md` inside | Tester |
| `harness_checks/` | Whole directory: the harness suite's mutants, can-fails and controls. Moved from reverse-it (port step 0, reviewer 2026-10-26); `README.md` inside | Tester |
| `fw_harness/host_diff/` | The reference-vs-port differential | Tester |
| `fw_harness/host_bridge/` | Whole directory, fakes included | Tester |
| `fw_harness/hw_mechanics.py` | Says whether a change touches one of spec 10's hardware mechanics, so it needs a bench run (spec 12) | Tester |
| `hw_mechanics_test.py` | Test of `fw_harness/hw_mechanics.py`, with `--can-fail` | Tester |
| `fw_harness/refusal_pairs.py` | shared/tools R20's refusal pairs for the interposer: each test-only rule passes on the truck stand-in and fails on an image built with its switch on | Tester |
| `fw_harness/refusal_pairs_runs.py` | Makes the stand-in, R19, fixture and dev builds, then runs the pairs (the bridge-release fixture: a local commit it makes, never on a branch; reviewer, 2026-11-03) | Tester |
| `fw_harness/idf_build_shell.py` | The acceptance runner's "idf" shell: exports the interposer's ESP-IDF from the machine's settings, runs the adapter's `idf-build` target, refuses a checkout not clean before and fails a build that changed a tracked file (`dependencies.lock`, shared/tools R33); Windows only (reviewer, 2026-11-04) | Tester |
| `fw_harness/idf_build_shell_test.py` | Test of `idf_build_shell.py` on a stand-in export, with `--can-fail` | Tester |
| `refusal_pairs_test.py` | Test of `refusal_pairs.py`'s judging and the runner's fixture check, with `--can-fail` | Tester |
| `fw_harness/c12_bench/` | Whole directory: the standalone ESP-IDF C12 bench Target, not a product image (reviewer, 2026-10-13) | Tester |
| `fw_harness/vport_bench/` | Whole directory: the standalone ESP-IDF vehicle-port bench image for spec section 9, TWAI only, never on the truck (reviewer, 2026-10-13) | Tester |
| `fw_harness/README.md` | The harness's own checks and can-fails, and the detail of the harness files one level up | Tester |
## Shared code

`shared/bench/` (the chain-runner core, package `vtrux_bench`) was written by
the charge-interposer tester. The gen-inhibit tester owns gen-inhibit's suite
definition, judges and flash kinds on top of it. Each project takes shared
code from a read-only sibling checkout, `../shared-<tag>/`, at a pinned tag,
checked against a committed pin file (user, 2026-10-23). Until it moves to
that, gen-inhibit installs the core at a pinned commit, and this branch still
carries its own copy of `shared/`, which its first re-pin removes. **One
exception:** `shared/bench/tests/layout_hash_crosscheck.py` is owned by the
gen-inhibit tester (both reviewers, 2026-10-22). It is that tester's
independent `layout_hash`, worth something only while it stays independent of
`decode.py`'s author, the charge-interposer tester, so only the gen-inhibit
tester changes its code. The charge-interposer tester may commit those changes
on the shared branch, and keeps `test_crosscheck` in `test_decode.py` as part
of `decode.py`'s tests. This paragraph stays here until
`shared/bench/README.md` records the change rule.

## Cross-project dependency

gen-inhibit's custody check (`tools/gen_inhibit/bench/x2_flash_custody.py`
and its test, on `gen-inhibit/main`) imports `build_lookup.py` through
`INTERPOSER_CODE` and uses exactly two names from it: `elf_sha256_of_bin` (a
`.bin` path in, the ELF SHA-256 hex string out) and the `APP_DESC_MAGIC`
constant. A change to either name's signature, result or value goes to the
gen-inhibit reviewer before it lands (reviewer, 2026-10-08).

The other way round, `bench_wican.py` reads from gen-inhibit, read-only,
never copying anything onto this branch (both reviewers, 2026-10-23/24):
- the key `gi_images` in gen-inhibit's local chain settings
  (`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/chain_settings_<host>.json`),
  and in `<gi_images>/build-tester-man/` exactly one file matching
  `wican-fw_obd_*.bin`, the glob of gen-inhibit's `image_bin()`;
- that image's identity through `elf_sha256_of_bin` above (the app
  descriptor's ELF SHA-256 at 0xB0);
- `tools/gen_inhibit/bench/wican_reboot.py`, its options `--label`, `--out`,
  `--known`, `--first`, `--want` and `--want-unknown`, and
  `page_state.read_page(timeout)`, from a detached worktree at a pinned
  `gen-inhibit/main` commit (`interposer-tester-gi-tools-<commit>`), named in
  the tester's local settings as `gi_tools`.

A change to any of these on either side goes to both reviewers first.
gen-inhibit keeps the same list in `tools/gen_inhibit/README.md`, under
"Cross-project dependencies".
