# E3 — build-output checks (spec 12.3 item 3 / 12.4 row 22)

These run on **the image that will be flashed**, not on the source. The WiCAN
OBD has **no external USB port** and no factory reset, so a bad image is
recoverable only by bootloader rollback or by opening the case -- the ESP32-C3's
native USB-serial/JTAG is on the board and reachable once it is off, which is how
the bench unit is flashed (COM4). Neither is a field
option on a dongle plugged into a truck, which is what makes every one of these
checks a guard against a way to lose a device. *Corrected 2026-09-27 (user): this
said "no USB port ... recoverable only by soldering to UART0 or by bootloader
rollback", which overstated it -- and a rationale that overstates its own stakes
is the kind that gets discounted when it matters.*

| File | What it checks |
|---|---|
| `build_output.py` | The whole of row 22: sdkconfig assertions, the embedded version, image size against the OTA slot, the partition table against stock, and the diag schema/DBC agreement. Also **refuses non-shipping builds**: the three measurement instruments, and (since 2026-10-04) the `OTA_HEALTH_FAULT_INJECT` rollback-rehearsal image, which rolls itself back 60 s after every boot, and (since 2026-10-13) the `GI_TEST_RESTART_QUIESCE` restart test image, which carries a crash trigger, and (since 2026-10-16) the **stock features off in this product** (MQTT, ELM327, AutoPID, RealDash; spec 3.2): each of the first three by strings in the image and symbols in the ELF, RealDash by `app_main`'s `can_enable` call sites (objdump), plus a row that only gen_inhibit and the OTA handler call `can_enable`/`can_disable` and that `can_flush_rx` and `can_send` are not in the image. Those have to be content checks, because two configurations of one commit share both the filename and the embedded version. |
| `config_vs_spec.py` + `config_probe.c` | `gi_config_defaults()` against the numbers the **spec** states, each row citing its section. |
| `elf_checks.py` | `slcan_parse_str` is not linked into the image. |
| `test_board_data.py` | Tests the WiCAN's board data for shared/tools R21 (`vtrux_checks.json` key `board`, the tester's; R21 readings, gen-inhibit reviewer 2026-10-24) against spec 12.3 item 3 and section 2: exactly `target`, `partition_table` and `flash_size` (R21 as worded in shared/tools a5e7c46, user 2026-10-24), the target the committed `sdkconfig`'s `CONFIG_IDF_TARGET`, the reference (`reference/stock-v4.13-partition-table.bin`) tracked by git, its smallest app partition the spec's 1,781,760-byte slot (R21 takes the slot from the table, so the data holds no number), and the flash size `4MB`, the spec's 4 MB and the committed `sdkconfig`'s `CONFIG_ESPTOOLPY_FLASHSIZE`. Each judgement is first shown failing on a planted input. Owned by the gen-inhibit tester. |
| `test_partition_row.py` | Tests `check_partitions()`: it reads the table from the IMAGE'S OWN build dir (as the sdkconfig rows do), so a `-B build-<x>` image is judged by its own table. A matching table passes, a one-byte change fails, a missing one fails naming that dir. Fails against the pre-2026-10-08 code, which read `build/` whatever image it was given. Owned by the gen-inhibit tester. |
| `test_prod_record.py` | Tests `check_prod_record()` against spec 12.3 item 3 (prod builds are the shared tooling's release builds, port step 0, user 2026-10-25): the record is found by the image hash read from the image, in `$REVERSE_IT_DIR/firmware/builds/gen-inhibit/` (shared/tools R13) or `--records`. No record, or only a retired `<image>.prod.json` beside the image, or no image hash, is NO REFERENCE ("not a prod build"), and so is an unset `REVERSE_IT_DIR` with no folder given. A record that cannot be read, or whose image hash, project or image size disagrees, or whose `.bin` beside it (the one it names, else `<image hash>.bin`) is missing or not this image byte for byte, is a FAIL. Fails against the pre-2026-10-07 behaviour, where a missing record was a FAIL. 19 mutants of the check, all caught. Owned by the gen-inhibit tester. |
| `test_fw_version_row.py` | Tests `check_fw_version()`: `DIAG_FW_VERSION` in `main/gen_inhibit.c` against the highest rev in the schema doc's rev table (row 22 item 10's other half; the host suite cannot see it, since every golden carries the default 1). A source bump the doc lacks, and a doc row the source never reached, each FAIL; no table or no define is NO REF. Owned by the gen-inhibit tester. |
| `test_reference_paths.py` / `test_reference_paths_mutants.py` | Tests where `build_output.py` gets the diag DBC and the schema doc: since 2026-10-24 (port plan toolkit item 10) from git, HEAD's copies of `--ref-repo` (default this repo), never the working tree and never `$REVERSE_IT_DIR`'s frozen copies, which the test plants with different contents. A modified, staged or deleted copy is a FAIL row, and the rows that read them NO REFERENCE; a working-tree edit git status does not show still reads HEAD's bytes; a repo without them, or a folder that is not a worktree, is NO REFERENCE naming `--ref-repo`; a DBC whose stated hash is not its own FAILS; the emitted-frames row decodes only with the schema set it read. Also checks that `build_output.py` names no machine's own path and builds no reverse-it path. Each case imports a copy afresh in a subprocess (`--target <copy>`). Its mutant runner: 6 mutants, each failing a check. Owned by the gen-inhibit tester. |
| `test_sources_row.py` | Tests `check_sources_older_than_image()`: a source newer than the image whose content (by `git hash-object`) equals the commit the image names is NO REFERENCE -- a branch switch, or an edit-build-revert, which mtimes cannot tell apart -- and a newer source with different content, or an image naming no readable commit, still FAILs. Fails against a comparison that always says same and one that always says different. Owned by the gen-inhibit tester. |
| `test_flt_row.py` | Tests the "project name is not a rehearsal build" row: `_flt` is refused as a `_`-delimited token anywhere in `project_name`, not only as the last suffix (a `_DIAG` after it hid it from the old `endswith`). Fails against the old predicate. Owned by the gen-inhibit tester. |
| `build_switches.py` | **Spec 12.3 item 3: every build-time switch is classified** (added 2026-10-13, user). It scans the files the build compiled (every file ninja's deps log, `ninja -t deps`, says a compile read, in the build directory and each CMake build folder below it, inside the repo, minus the build directory and `managed_components/`; **reading changed 2026-10-24**, gen-inhibit reviewer, Q12 of shared/tools R20: it was `main/` and `components/` walked whole) for every symbol an `#if`/`#ifdef`/`#ifndef`/`#elif` tests (`CONFIG_*` and header guards excluded), and every build option an `if()`/`elseif()`/`while()` tests in a CMake file the build reads from the repo. **The CMake files come from the build's own record** (spec, changed 2026-10-13, user): the inputs of `build.ninja`'s regeneration rule (`ninja -t query build.ninja`), keeping `CMakeLists.txt` and `*.cmake` inside the repo, minus the build directory and `managed_components/`. So it needs a built build directory (`--repo`, `--build`), prints what it scanned and excluded, and FAILS rather than falling back when the record is missing, empty, lacks the top-level `CMakeLists.txt`, or is older than a scanned file, or when the deps log names no compiled file. **A build option** is a variable settable from outside the file that tests it; a variable the file sets itself first, and CMake's built-ins (`cmake --help-variable-list` of the recorded cmake), are not. Read narrowly (reviewer, 2026-10-13): only a file-scope `set(VAR <value>)` with no CACHE/PARENT_SCOPE, before the test, from built-ins or the file's own variables, makes a variable the file's; `$ENV{X}` is always an option. It compares them against the table, which since 2026-10-24 is data in the repo root's `vtrux_checks.json` (key `build_switches`, shared with the shared/tools R20 check; the triggers are key `inert_triggers`): shipping variant, test-only, stock feature off in this product (since 2026-10-16), each of the last two with refusal rules that judge the image or the ELF (`bin_strings_absent`, `elf_symbols_absent`, `elf_symbol_prefixes_absent`, `project_name_token_absent`, or `project_row`, a row `build_output.py` writes), or not a switch (with a kind -- the spec's examples are default value, injected value and library macro -- and a reason). `build_output.py` reports an unlisted symbol, a stale entry and a malformed entry as three separate rows, evaluates every test-only switch's and stock feature's rules on the image, and writes `vtrux_project_rows.json` beside the image (its rows, bound to its image hash) for the shared check. A header guard is only the exact shape: the file's first line is `#ifndef X` and the next is `#define X` with no value. The table is also what the 12.4 "inert" row's trigger table must cover. Owned by the gen-inhibit tester. |
| `test_build_switches.py` | Tests the scan on a synthetic tree. Cases: a default value shaped like a guard; a guard after an `#include`; a guard symbol tested again later; `defined()`, `#elif`, continuations, comments and string literals in an `#if`; CMake keywords, comments, `${}` and `elseif`; the record (every input line, `|` and `||`; outputs ignored; `.cmake` files taken; non-CMake inputs, the build directory, `managed_components/` and files outside the repo not; each refusal of `read_record`/`check_record`); the compiled files from `ninja -t deps` text (VALID records only, build-relative paths resolved, the build directory, `managed_components/` and files outside the repo left out, a non-`.c` file a compile included taken, every CMake build folder asked, an unbuilt tree refused); the narrow build-option reading case by case (set before/after the test, under a condition, CACHE, PARENT_SCOPE, no value, unset, a set from an outside or environment value, `$ENV{}`, a quoted `${}`, built-ins exact and by placeholder, `<PROJECT-NAME>` only for the declared project, a function body, `string()`, `#` and parentheses inside quotes, bracket comments, `while()`). 66 mutants across this file, `build_output.py`'s row and `scan_scope_check.py`, all caught. Also tests the table rules, including that a "not a switch" kind is required but is not limited to the spec's three examples (reviewer, 2026-10-13), each refusal rule's form, and that the real table loads from `vtrux_checks.json` well-formed. 35 mutants, all caught. Owned by the gen-inhibit tester. |
| `scan_scope_check.py` / `test_scan_scope_check.py` | **The scan's CMake scope shown both ways on real builds** (spec 12.3 item 3; spec 13 "Checks, not built"). In a throwaway worktree of `--commit`: a baseline `idf.py build` and scan (a build, not only a reconfigure, since 2026-10-24: the C scan reads the deps log); then a NEW `cmake/gi_planted_option.cmake`, included from the top-level `CMakeLists.txt`, testing `GI_PLANTED_OPTION`, configured with `-DGI_PLANTED_OPTION=ON` -- the scan must FAIL naming it there; then the same file with `set(GI_PLANTED_OPTION OFF)` before the test, same `-D` -- the scan must PASS. Each configure is checked independently of the scan: the planted file prints a line when its test is true, which must appear in the planted step (the `-D` reaches it: an option) and not in the set-first step (the set wins: not one), and the planted file must be in the record in both. The test file covers every judgement, including that the planted texts scan as intended. Owned by the gen-inhibit tester. |
| `test_switch_rows.py` | Tests `build_output.py`'s switch rows: the restart-test refusal (each marker alone fails it, the `_rt` token), and each of the scan's three findings reaching its own row only. It also tests the refusal-rules check (each entry's table rules on the image: a project row this run did not produce, a string on the image, no image or no ELF as NO REF, an entry with no rules) and the `vtrux_project_rows.json` it writes (beside the image, its embedded ELF hash, NO REF written as FAIL), and (2026-10-16) the stock-feature rows on fixtures: objdump call-site parsing in both line shapes, each symbol and each image marker alone failing only its own row, app_main calling `can_enable`, and each disallowed caller or present function failing the bus-state row. Mutants for those: `build_checks_stock_mutants.py` in the analysis project's gen-inhibit artifacts. `--pair <on.bin> <off.bin> [--with-name]` runs the refusal rows on a real switch-on/switch-off pair, which is the 12.3 "shown failing ... and passing" proof. 14 mutants, all caught (on the a3394ca pair, with the `_rt` name row); two more dropped as equivalent. Owned by the gen-inhibit tester. |
| `test_refusal_pairs.py` | **Spec 12.3 item 3's proof for every test-only switch**: each switch's refusal rows FAIL on an image built with it on, and every refusal row PASSES on the image built without it. Instrument rows must also name the switch, so one instrument's marker can't stand in for another's. A missing ELF, a missing row, or a test-only switch with no ON image in the run fails the proof. `--off <bin> --on SWITCH=<bin> ... [--control SWITCH=<bin>]`. Since 2026-10-16 it covers the stock features too; AutoPID cannot be built without MQTT and ELM327, so its ON image is all three and its `--control` image is MQTT+ELM327, on which its rows must PASS (a switch that needs a control and has none fails the run). Run 2026-10-13 on the tester's six clean-worktree builds of `63eb50f`. Re-run 2026-10-16 on the tester's clean-worktree builds of `e3d9437` with the stock features: PASS, and 11 negative controls each caught on the check it targets (among them the product image given as AutoPID's control, and a switch-on image given as OFF). Negative controls, all caught: a wrong image for a switch, the default image as ON, an instrument image or the fault-injection image as OFF, a switch left out, an image without its ELF. Owned by the gen-inhibit tester. |
| `stale_name_check.py` | **Spec 12.4 "no stale image name" (12.3 item 3, "Every build names its image from the tree it compiled"; added 2026-10-13, user).** Drives real builds, so it runs where ESP-IDF is exported. In a throwaway detached worktree of `--commit` (with `--components` copied in; removed afterwards unless `--keep`), one build directory is rebuilt eleven times: clean `idf.py build`; a comment appended to `main/gen_inhibit.c` and `idf.py build`; reverted and `idf.py build`; the same edit and revert with bare `ninja -C build`; then a committed edit built with `ninja`, and another built with `idf.py build`; then, with `ninja`, an edit to `README.md`, its revert, and a `README.md`-only commit (the 12.3 text's "uncommitted changes" and "git state", reviewer 2026-10-13: nothing compiled changes there, so a build that does not re-run the naming writes no image and fails); and last, with `.git` moved aside and `GIT_CEILING_DIRECTORIES` set (git must be shown unable to answer first) plus a source edit, bare `ninja` must fail and write no image (12.3: "if the naming can't be re-run, the build fails"). Each step judges the one image that build wrote, by its filename, its `esp_app_desc_t` version (0x10) and its project_name (0x30): each must name HEAD (an abbreviation of its sha) and carry `-dirty` exactly when the step left the tree dirty, which `git status` must agree with. A failed build, no image, two images or no app descriptor fails the step. It must fail on a tree with the trap and pass on one without. Owned by the gen-inhibit tester. |
| `test_stale_name_check.py` | Tests `stale_name_check.py`'s judging without a build: version and filename parsing, the prefix and dirty match, which images count as written by a step, and every undetermined case failing, the step window (an image the previous step wrote just before is not this step's), and the no-git step (fails, no image, and git shown unable to answer). 22 mutants, all caught. Owned by the gen-inhibit tester. |
| `name_once_check.py` | **Spec 12.4 "the name enters the build once" (12.3 item 3; added 2026-10-13, user), the host (build) half.** Drives real builds where ESP-IDF is exported, in a throwaway local CLONE of the repo at `--commit` (a clone, so the tag it makes never reaches the repo's refs), removed afterwards. Steps: a clean `idf.py build` (the image's name, descriptor version and project_name name HEAD, by `stale_name_check.judge`; fnv1a32 of the version and of the name's describe part are recorded as the host cross-check for the bench's `0x7F3` read, and how many compile lines pass the name as a quoted value); a `README.md` edit and its revert, each built with `idf.py build` (an image under the new name, and no object whose source is under `main/` or `components/` recompiled -- object mtimes from `compile_commands.json`; ESP-IDF's own recompiles are recorded, not judged); a 38-character tag on HEAD (the build fails and writes no image, whichever guard fires -- the product's project-name guard refuses it before the version-field one can, reviewer 2026-10-13); and a minimal fixture project that includes the commit's `cmake/git_tree_name.cmake` and has no length guard of its own, tagged the same (the build fails, writes no image, and the CMake error names the version field). The `0x7F3` hash itself is read on the bench (`diag_build_page_check.py`; user, 2026-10-13). Owned by the gen-inhibit tester. |
| `test_name_once_check.py` | Tests `name_once_check.py`'s judging without a build: fnv1a32 against two hashes read off the wire earlier (`4a0a796`, `a71b90a`), `compile_commands.json` parsing, which objects count as recompiled (an mtime change, or newly present) and which are the product's (`main/`, `components/`, not a sibling that starts with `main`), and every judgement's failing cases, undetermined ones included, and that the name counted on compile lines is the quoted value, not a path that contains the sha. 21 mutants, all caught. Owned by the gen-inhibit tester. |
| `reference/` | Fixtures a check compares against — see below. |

## Running them

```bash
python3 build_output.py                  # newest build/wican-fw_obd_*.bin
python3 build_output.py --bin build/wican-fw_obd_abc1234.bin
python3 build_output.py --records <folder>   # release records; default $REVERSE_IT_DIR/firmware/builds/gen-inhibit
make config_probe && python3 config_vs_spec.py
python3 elf_checks.py
python3 test_prod_record.py             # tests the prod-record row itself
python3 test_partition_row.py           # tests the partition-table row itself
python3 test_flt_row.py                 # tests the rehearsal-name row itself
python3 test_fw_version_row.py          # tests the DIAG_FW_VERSION row itself
python3 test_reference_paths.py         # tests where the DBC and schema doc defaults come from
python3 test_sources_row.py             # tests the sources-older row itself
python3 build_switches.py [--repo DIR] [--build DIR]   # the switch scan, every finding (needs a built tree)
python3 test_build_switches.py          # tests the scan itself
python3 test_switch_rows.py [--pair <on.bin> <off.bin> [--with-name]]
python3 test_refusal_pairs.py --off <off.bin> --on SWITCH=<on.bin> ... [--control SWITCH=<img>]   # every test-only switch and stock feature, on and off
```

`build_output.py` reads the diag DBC and the schema doc from git
(`gen-inhibit/diag/`, since 2026-10-24): HEAD's copies of `--ref-repo`, default
this repo, after `gi_diag`'s checks of the DBC's history. A copy with
uncommitted changes is a FAIL row; a repo without them is reported as **NO
REFERENCE**, never as a pass. The emitted-frames row decodes the goldens' diag
lines through `gi_diag`, each trace on its own schema pages.

## Two rules these files follow

**A check with no reference says so.** Where a comparison needs something this
repo does not hold, the row prints `NO REF` and the summary counts it
separately. That is the same discipline `config_vs_spec.py` was corrected into
on 2026-09-25, when a row cited spec 6.2 for a number section 6.2 does not
state: *a table whose rows do not all mean the same thing is worse than no
table*, because the rows that do mean something stop being distinguishable.

**Only the enables count.** This build sets `CONFIG_SECURE_BOOT_V2_RSA_SUPPORTED`
and `CONFIG_SECURE_BOOT_V2_PREFERRED` — a capability and a preference, not the
feature. A check that tested those would fail a perfectly correct image. The
enables are `CONFIG_SECURE_BOOT` and `CONFIG_SECURE_FLASH_ENC_ENABLED`.

## The version comes from the image, never the filename

ESP-IDF fixes the output filename at **configure** time, so
`wican-fw_obd_37addf4-dirty.bin` can contain a build of something else
entirely. The authoritative string is in `esp_app_desc_t` at offset `0x20` of
the app image, and that is what the `-dirty` and git-sha checks read.

Getting the offsets wrong there produced the kind of wrong that looks like
data: the first version read `0x60` for `idf_ver` and reported
`idf='Sep 25 2026'`, which is the `date` field. The layout is `0x00` magic,
`0x04` secure_version, `0x08` reserv1[2], `0x10` version[32], `0x30`
project_name[32], `0x50` time[16], `0x60` date[16], `0x70` idf_ver[32].

## The checker was proved in both directions

A check nobody has seen fail is not a check, and that applies to these as much
as to the test suites. The `-dirty` row was **observed failing** on
`wican-fw_obd_bea8d51-dirty.bin` — a real artifact built from an uncommitted
tree — and then **observed passing** on `wican-fw_obd_8896311.bin`, built clean
from `8896311`. Two of the rows were over-claiming on that first run and were
corrected rather than blessed:

- the DBC row demanded every `GI_*_ID` including `GI_PROBE_ID`, and failed a
  correct DBC. `0x7F0` is the RESPOND mode's timing measurement, not a status
  page; row 22 says *diag frames*, and `vtrux-wican-diag.dbc` deliberately
  defines only the four pages.
- the project-name row demanded equality with `wican-fw`, which this build
  system never produces — it embeds the git describe.

## `reference/`

| File | Provenance |
|---|---|
| `stock-v4.13-partition-table.bin` | Extracted from `wican-config/restore-v4.13/wican-fw_obd_v413.zip` in the analysis project — the stock MeatPi v4.13 release. 3072 bytes, sha256 `98d193a613ff7e9d…`. This is what "byte-identical to stock" is measured against; a device flashed with a differing table loses its recovery layout. Also the WiCAN's reference table for shared/tools R21: `vtrux_checks.json`'s `board` names it, and R21's slot is its smallest app partition. |

## What row 22 still cannot check here

Nothing, as of 2026-09-26 — all nineteen rows report a real verdict against a
real reference. The remaining E3 gap is not in this directory: it is that these
checks are not yet wired into the release step, so they have to be run by hand
before a flash.
