# gen-inhibit/

The Vtrux generator inhibitor: firmware for the MeatPi WiCAN OBD (ESP32-C3),
built with ESP-IDF on the WiCAN codebase, on branch `gen-inhibit/main`. This
folder is the project's own; its spec, `VALIDATION.md` and `VERIFICATION.md`
can move here later.

This file is the project's file map: who owns each file. It belongs to the
gen-inhibit reviewer, who decides ownership when it is unclear and records it
here (`AGENTS.md`, "Who owns what"). Paths starting `$REVERSE_IT_DIR` are in
reverse-it and not yet moved.

## Reviewer

- The spec: `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`.
- Published interfaces, which are spec: the diag schema
  `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/wican_diag_schema.md`
  and its DBC `$REVERSE_IT_DIR/projects/vtrux/vtrux-wican-diag.dbc`.
- The review tracker:
  `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-review-2026-09-22.md`.
- Validation: `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-firmware.md`,
  frozen; the spec cites it, and no one adds to it (reviewer, 2026-10-10).
- This file.

## Implementor: product code

- The WiCAN tree on `gen-inhibit/main`, except the tester's `test/` folders
  and the tester's files in `tools/gen_inhibit/` listed below.
- Unit tests: `test/gen_inhibit_sched/`, `test/gen_inhibit_shim/`.
- The Python reference in `tools/gen_inhibit/`: `sync.py`, `inhibit.py`,
  `models.py`, `test_core.py`, `vehicle_runner.py`, `README.md`,
  `HANDOFF.md`, `requirements.txt`.
- `tools/prod_build.py` and its tests `tools/test_prod_build_fetch.py`
  (reviewer, 2026-10-17), `tools/test_prod_build_image.py` and
  `tools/test_prod_build_image_mutants.py` (reviewer, 2026-10-21);
  `tools/README.md`.

## Tester: harness and verification

- Verification: `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-test-evidence.md`.
  Some of its entries are also measurements that a requirement's validation
  cites as its basis (for example section 13's cross-host build comparison,
  cited by `shared/tools/VALIDATION.md` R2).
- In `tools/gen_inhibit/`: `bench_vehicle.py`, `live_bench.py`, `gi_vbus.py`,
  `test_gi_vbus.py`, `test_publish_scan.py`, `replay_inhibit.py`,
  `bench_flash.py`, `bench_flash_guard.py`, `bench_ota_guard.py` (reviewer,
  2026-10-08), `bench_flash_wire.py`, `test_bench_flash_wire.py`,
  `test_bench_flash_wire_mutants.py`, `test_bench_flash_outcome.py`,
  `test_bench_flash_outcome_mutants.py` (reviewer, 2026-10-17);
  `test_bench_flash_preflight.py` and `test_bench_guards_page.py` with their
  `_mutants` files, and `test_publish_scan_mutants.py`; the chain suite
  `suite_gen_inhibit.py`, `suite_gen_inhibit.settings.example.json`,
  `suite_judges.py`, `bench_compare.py` and their `test_suite_*` and
  `test_bench_compare*` files (reviewer, 2026-10-23).
- `tools/gen_inhibit/bench/`: the bench harnesses, judges, runners and rig
  scripts with their tests and mutant runners, moved in from reverse-it by
  `ad0cab3` (port plan goal 20; reviewer, 2026-10-22). This includes the X2
  flash custody check, `x2_flash_custody.py`, with
  `test_x2_flash_custody.py` and `test_x2_flash_custody_mutants.py`.
- `tools/gen_inhibit/x2_jammer/`: the X2 error-frame jammer, test equipment
  and never product code (since 2026-10-08; moved in by `ad0cab3`).
- The host suite `test/gen_inhibit_host/` (runner, scenarios, golden traces)
  and the build checks `test/build_checks/` (user, 2026-10-07).
- `test/gen_inhibit_spec_shim/`: the spec 12.4 requirement tests on the
  mock-HAL shim. They build against the implementor's `test/gen_inhibit_shim/`
  fakes and add their own fault behaviour on top, never by editing those fakes
  (reviewer, 2026-10-20: a test that judges product code is not written by the
  implementor).
- In reverse-it, `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/`:
  what stays out of git, as `tools/gen_inhibit/bench/README.md` lists it (run
  folders and fixtures, the jammer's builds and their `manifest.json`, the X2
  flash dumps in `board-backups/`, which never enter git, one-off checks and
  corpus investigations), and that folder's index `README.md` (reviewer,
  2026-10-10).

`tools/wican-log-viewer/` is upstream WiCAN's.

## Shared code

`shared/bench/` (the chain-runner core, package `vtrux_bench`) was written by
the charge-interposer tester. The gen-inhibit tester owns gen-inhibit's suite
definition, judges and flash kinds on top of it, and installs the core at a
pinned commit. **One exception:** `shared/bench/tests/layout_hash_crosscheck.py`
is owned by the gen-inhibit tester (both reviewers, 2026-10-22). It is that
tester's independent `layout_hash`, worth something only while it stays
independent of `decode.py`'s author, so only the gen-inhibit tester changes its
code; the charge-interposer tester may commit those changes on the shared
branch. These notes move to `shared/bench/README.md` once it exists.

## Cross-project dependency

gen-inhibit's custody check, `tools/gen_inhibit/bench/x2_flash_custody.py` and
its test `test_x2_flash_custody.py`, imports the charge-interposer tester's
`charge-interposer/build_lookup.py` through `INTERPOSER_CODE` and uses exactly
two names from it: `elf_sha256_of_bin` (a `.bin` path in, the ELF SHA-256 hex
string out) and the `APP_DESC_MAGIC` constant. It confirms the X2's interposer
image by ELF hash after the jammer is restored, and reads gen-inhibit's own
jammer manifest,
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/builds/manifest.json`,
not the interposer's. A change to either name goes to the gen-inhibit reviewer
before it lands (reviewer, 2026-10-08).
