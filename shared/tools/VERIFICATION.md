# Shared build tooling: verification evidence

Test evidence that the tooling in `shared/tools/` meets each requirement in
`SPEC.md`. Why each requirement is written as it is: `VALIDATION.md`.

Each entry names the requirements it covers (R1 to R23), the commit of the
tooling it tested, and its run files under `$REVERSE_IT_DIR`, with each
file's hash where a verdict rests on it (port plan goal 20). Entries arrive
as commits on the tester's branch, and the reviewer pushes them.

## Library side: R1, R2, R7, R15 (C6f part A, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `736a1c7`. The library ran
from `shared/tools/checks` at `ebfe34d`, whose `vtrux_build/` tree (`707f59c`) is
the same as `736a1c7`'s. **Checks:** `verification/verify_image.py` and
`verification/verify_digest.py` at `ebfe34d`, written from SPEC and ESP-IDF's
published image format, not from `vtrux_build`'s source. Each one's `--self-test`
catches every one of its broken fakes (8 and 12) and passes its faithful one.
**Host:** NICKY-XPS, Python 3.14.6, 2026-10-24. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`; `nicky_xps/SHA256SUMS` hashes every output).

| Req | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| R2 | PASS | Synthetic images: the code hash and its blanked ranges match the tester's reading of R2. Version, date, time and the ELF hash each keep it. Project name, IDF version and one code byte each change it. A three-segment image passes. Truncated, padded, bad-checksum, bad-SHA and non-images are refused (`ImageError`). Reading leaves the file's bytes and mtime unchanged. Real images: the six clean `repro_a24de99` builds, from NICKY-XPS and madhouse-debian at two clone paths each, with and without ccache, give one code hash, `8f02c415...a37969`, the tester's reading for each. The dirty control gives another. | `verify_image_a24de99.txt` (`f9cd33fc...4c8bb`), `verify_image_a24de99_elf.txt` (`6ad5023c...89b64`) |
| R1 | PASS | Synthetic: the image hash is the embedded ELF SHA-256 field, and changes with it. Real: on the same seven images, read where their `.elf` files are kept (each `.bin` byte-identical to the SeaDrive copy), the image hash is the sha256 of the ELF built with it. | `verify_image_a24de99_elf.txt` (`6ad5023c...89b64`), `elf_copies_match.txt` (`fd4bb835...05b60`) |
| R15 (with R3) | PASS, library side | `code_hash(path)` and `AppImage.read(path).code_hash` give the tester's R2 code hash on synthetic and real images. Logging it is the bench toolkit's half, not tested here. | `verify_image_a24de99.txt` |
| R7 | PASS, partial (completed against `e9a03fd` in the follow-up entry below) | Same digest for: a copy elsewhere, an mtime change, `*.md` anywhere, a root `.gitignore`, empty folders, a `.git` above it on a branch, on a tag or absent. A different digest for: a content byte, a rename, a move, an empty file, `__pycache__`, CRLF, `README.md.txt`, a dotfile, a removed file, swapped contents, the path/content boundary. `component_files` lists exactly what R7 covers. `shared/tools` at `736a1c7`, checked out and exported by `git archive`: one digest, `daf1ffa9...79fea0`, 22 files. **Not tested** until the reviewer rules: `.gitignore` below the root, `.markdown`, `*.md` case, and the path separator across host OSes. | `verify_digest_736a1c7.txt` (`5d862596...cced`) |
| R4 | not yet verified | Waits on gen-inhibit's port step 0c, which retires the name stamp. | |

## Real release builds: R5, R8-R11, R13, R14 (with R3), R16, R22 (C6f part A, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `736a1c7`. Every batch ran with
`vtrux_build/` tree `707f59c`, the same as `736a1c7`'s, from `shared/tools/checks`
at `cbb49af`, `4c4686d` or `b4ce55d`. Those commits differ only in the tester's
`verification/` folder. **Checks:** `verification/release_cases.py`, whose
`--self-test` catches every broken record and second-build input. **Project:** a
throwaway clone of gen-inhibit `d2e0bb5` with fixture commits:
- `vtrux_build.json`, reproducible build on, sdkconfig as the reconfigure writes it;
- a branch whose build deletes its own `.bin`;
- a branch with a shared component by `override_path`;
- for R22, IDF's `hello_world` on esp32c3 as a second project.

Records go to a stand-in `REVERSE_IT_DIR`. **Hosts:** NICKY-XPS and madhouse-debian:
IDF v5.4.1, toolchain esp-14.2.0_20241119 (gcc 14.2.0) on both. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`; `xps_release_1/SHA256SUMS`, sha256 `e04670fb123f...`, hashes every
NICKY-XPS file; `madhouse_release/`).

| Req | Result | What was tested |
|---|---|---|
| R8 | PASS | Refused: an untracked file or a modified tracked file at the start; an untracked file made during the reconfigure, with nothing compiled; an untracked file or a new commit made mid-build, refused at the end. No record in any. |
| R9 | PASS | A release build with `IDF_CCACHE_ENABLE=1` from the export has `CCACHE_ENABLE` off in every CMakeCache and no `ccache` in any `.ninja` file (both build paths ran). `CMAKE_C_COMPILER_LAUNCHER` and `CMAKE_CXX_COMPILER_LAUNCHER=ccache`, taken by CMake, are refused. |
| R10 | PASS | Refused: the overwrite setting set (to `0`); a managed component file changed; its `.component_hash` changed; a file its `CHECKSUMS.json` does not list. After a release build, each `.component_hash` is the lock's. |
| R11 | PASS | The recorded `.bin` is the one `project_description.json` names. A build that deletes that file is refused. |
| R13 | PASS | `REVERSE_IT_DIR` unset: refused, with no `build/` made. A release build writes one record named by its image hash, with the `.bin` and `.elf`, judged field by field against what the tester read itself, on both hosts. A second build of the same image exits 0 and leaves the record folder's bytes and mtimes unchanged. |
| R16 (`override_path`) | PASS | A shared component taken by `override_path` (the lock shows a local source) is refused. |
| R22 | PASS, one question open (ruled; PASS against `e9a03fd` in the follow-up entry below) | A second project builds and records with no tooling change. The same project with no component manifest, so no `dependencies.lock`, is refused after the reconfigure. Whether SPEC allows that is with the reviewer. |
| R5 | PASS, except one clause not met | Same commit, another branch and clone path: the same image hash. Same files on NICKY-XPS and madhouse-debian: the same code hash, `a7e2d28084f3122c...`, with different image hashes. **Not met, cause step 0c:** identical files at another commit give another code hash. They differ only in `project_name`, gen-inhibit's name stamp `wican-fw_obd_<commit>_man`, outside R2's blanks. |
| R14 (with R3) | not met, cause step 0c | A bench build in a modified tree builds without the release script's refusals. Its code hash is not the release build's, for the same reason: `project_name` carries `-dirty`. |
| R6 | not tested on its own | No case builds an image whose name or version string disagrees with its tree. The records name images by image hash, not by name. |

## Follow-ups: R1, R7, R8, R9, R10, R22 (C6f part A, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `e9a03fd` (`vtrux_build/` tree
`39a9d5f`), merged into `shared/tools/checks` as `4ac8b76`. **SPEC:** shared/main
`40ddee3`, with the user's rulings of 2026-10-24 on R7 (`904b98a`) and R10 (`0d2431b`).
**Checks:** `verification/` at `4ac8b76`: `verify_digest.py`, whose `--self-test` catches
all 16 of its broken fakes, and `verify_image.py`. Also `release_cases.py` at `4ac8b76`,
and at `8fcb098` for the second `r9_masquerade` run; both commits' `--self-test` catch
every broken input. **Hosts:** NICKY-XPS (Python 3.14.6; IDF v5.4.1 with its Python
3.11.9) and, for R7, madhouse-debian (Python 3.13.5). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, its sections from "R7 across host OSes" on).

| Req | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| R7 | PASS | The ruled edges: a `.gitignore` in a subfolder gives the same digest. `notes.markdown`, `README.MD` and `sub/x.Md` each give a different one. `component_files` counts `x.markdown` and `sub/deep/NOTES.MD` and leaves out `sub/.gitignore`. All of the first entry's R7 cases pass again. `shared/tools` at `e9a03fd` gives `772c2493...1e0e08` over 22 files, from the checkout and from the `git archive` export, on NICKY-XPS and on madhouse-debian, each run checking the other host's digest. | `r7_cross_host/SHA256SUMS` (`a62c6d75...5a8256`), which hashes each host's self-test and library run |
| R1 | PASS | The three distinct `repro_a24de99` images, the NICKY-XPS clean (`x`), madhouse-debian clean (`m`) and dirty control (`mctl`), are now kept in the run folder with their `.elf` files. On each, the image hash is the sha256 of the `.elf` beside it. | `a24de99_elf/SHA256SUMS` (`25a33893...bf3ec6`), `a24de99_elf/verify_image_elf_copied.txt` (`16209318...3365a8`) |
| R8 | PASS | Refused at the start, with no record: a new file staged but not committed; a tracked file deleted. Refused after the reconfigure, with nothing compiled and no record: a commit whose committed sdkconfig the reconfigure rewrites (`git status` shows `M sdkconfig` after it). | `xps_release_2/SHA256SUMS` (`ee2bab74...35c6c4`) |
| R9 | **FAIL** | **Observed:** a copy of IDF's `ccache.exe` (4.10.2), named `riscv32-esp-elf-gcc.exe` and `riscv32-esp-elf-g++.exe` and first on PATH, is the C compiler CMake chose (`CMakeCCompiler.cmake`). The release build exits 0 and prints `RELEASE BUILD RECORDED`, and ccache's own statistics show 1290 compiles through its cache. **SPEC R9:** the build runs with ccache off whatever the environment sets, and a build whose configuration still has it on is refused. NICKY-XPS only: madhouse-debian has no ccache. | `xps_release_2/case_r9_masquerade.log` (`16da99da...7d442d00`), in `xps_release_2/SHA256SUMS` |
| R10 | PASS | A project with no manifest and no `dependencies.lock`, which downloads nothing: the reconfigure runs, no lock exists after it, and the build is refused with nothing compiled and no record. | `xps_release_2/SHA256SUMS` |
| R22 | PASS | A second project (IDF's `hello_world`, `vtrux-mini-test`) builds and records with no tooling change, judged field by field. Without a manifest it is refused (R10, above), as SPEC R10 now requires. | `xps_release_2/SHA256SUMS` |
| R13 | PASS | `a_release` again, from an empty record folder: one record, `91acd964...`, judged field by field. | `xps_release_2/SHA256SUMS` |

## R17 scope (C6f, 2026-10-24)

**Tooling tested:** builds made by `shared/tools/build-tooling` at `e9a03fd`
(`vtrux_build/` tree `39a9d5f`, the same in every commit below). **SPEC:** R17 in
shared/main `5ec6f53`, with the user's rulings of 2026-10-24: IDF is `IDF_PATH` plus the
chosen toolchain's install folder and `CMAKE_ROOT`, both from the build's own record; a
managed component is at the lock's version when its own manifest `version` is the lock's;
a bench build may read the `shared/` folder of any worktree of this repo or of a
`../shared-<tag>/` checkout. **Check:** `vtrux_checks/scope.py` (`python -B -m
vtrux_checks scope --build <dir> --release|--bench`) at `0b77a84`, written from SPEC
R17. Its 11 unit tests in `vtrux_checks/tests/` show each rule failing on a planted input
and passing on a faithful one, and three mutants of its rules each fail them. They pass on
Python 3.14.6 and 3.11.9 (NICKY-XPS) and 3.13.5 (madhouse-debian). **Cases:**
`verification/scope_cases.py` at `0b77a84`, and at `9332101` for the second run of the two
override cases; its `--self-test` catches every broken input. Each case confirms its trigger
without the check's reader (`ninja -t deps` or `build.ninja`). **Hosts:** NICKY-XPS (IDF
v5.4.1, its Python 3.11.9) and madhouse-debian. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R17 scope check").

| Case | Result | What was tested |
|---|---|---|
| Clean release build | PASS | The fixture's release build (5721 files read, app and bootloader): exit 0 with `--release` and `--bench`. |
| Managed component version | PASS | mdns's manifest `version` changed from the lock's 1.4.0: exit 1 both ways, naming `espressif__mdns`. |
| Managed component not in the lock | PASS | An extra folder in `managed_components/` the lock does not list: exit 1 both ways, naming it. |
| File outside the scope | PASS | A bench build whose `main.c` includes a header from a folder beside the project: exit 1 both ways, naming the header. |
| `shared/` in a bench build | PASS | A shared component taken by `override_path` from a `../shared-<tag>/shared/` folder: exit 0 with `--bench`, exit 1 with `--release` naming its source. |
| A folder that is not `shared/` | PASS | The same component from another folder: exit 1 both ways, naming its source. |
| Across host OSes | PASS | madhouse-debian's own release build of the fixture, with the system CMake 3.31's `CMAKE_ROOT`: exit 0 both ways (5725 files). |

Run files (sha256): `xps_scope_r17/SHA256SUMS` (`f26ce864...42a7`), which hashes every
NICKY-XPS log and `scope_results.json`; `r17_linux/scope_a_release.txt` (madhouse-debian)
(`5c531355...2129c5`).

## R9, R13 fixes, and R17's shared/ roots (C6f, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `8efba37` (`vtrux_build/` tree `3a42bbd`):
`8624d11` for R9 and `8efba37` for R13. It is merged into `shared/tools/checks` as `4467812`.
**SPEC:** shared/main `5ec6f53`: R9, and R13's lock-hash bullet ("taken with its line endings
normalised to LF, so one lock gives one hash on any host and in any checkout"). **Checks:**
`verification/release_cases.py` at `adab508`, and at `fbcd9ca` and `92c4434` for the later runs
of `r13_lock_crlf`; each commit's `--self-test` catches every broken input. The record judge
compares the record's `dependencies_lock_sha256_lf` with the tester's own LF-normalised hash
of the lock on disk. Its self-test catches a record carrying only the old field name, and a
mutant of the judge without the normalisation fails it. **Hosts:** NICKY-XPS (IDF v5.4.1, its
Python 3.11.9) and, for `r13_lock_crlf`, madhouse-debian (Python 3.13.5). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R9 and R13 fixes" and its subsections).

| Req | Result | What was tested | Run file (sha256) |
|---|---|---|---|
| R9 | PASS (was FAIL against `e9a03fd`) | ccache's own `ccache.exe` named `riscv32-esp-elf-gcc.exe` and `riscv32-esp-elf-g++.exe`, first on PATH and chosen by CMake, as a copy and as a hard link: each is refused right after the reconfigure, with nothing compiled and no record. `CMAKE_C_COMPILER_LAUNCHER` and `CMAKE_CXX_COMPILER_LAUNCHER=ccache` are refused. A clean release build passes and is recorded. | `xps_release_3/SHA256SUMS` (`5bbfd1ae...f595d3`) |
| R13 | PASS | `a_release`: one record, judged field by field, its lock hash the tester's LF hash of the lock (`17f7c654...`). A second build leaves the record folder unchanged. `r13_lock_crlf`: the R22 project with its committed lock on disk as CRLF and then as LF, each clean to git and built into an empty record folder. Both records give the committed lock's sha256 `03bbd78d...`, on NICKY-XPS and on madhouse-debian. | `xps_release_3/SHA256SUMS`, `r13_linux/SHA256SUMS` (`2f7d4d29...db29f9a`) |
| R22 | PASS | The second project builds and records with no tooling change, judged field by field. | `xps_release_3/SHA256SUMS` |

**How the lock reaches CRLF.** The Component Manager writes `dependencies.lock` only when its
content changes, and on Windows it writes CRLF. git calls a file committed LF "modified" when
its size on disk differs from the size the index cached, so a lock git last added as CRLF is
clean, and a CRLF rewrite after checkout is not. That was observed on git 2.52.0.windows.1 and
2.47.3, and it is how the first batch's CRLF lock passed R8. Evidence: `crlf_lock/SHA256SUMS`
(`0ecb4140...cb3b`). `r13_lock_crlf` reaches the clean CRLF state the same way.

**R17 follow-up.** At the reviewer's note on `0b77a84`, `vtrux_checks/scope.py` (`e469205`)
looks for `../shared-<tag>/` beside the worktree root (`git rev-parse --show-toplevel`), not
beside the project root, which may be a subfolder of the worktree. Its new unit test (12 in
all, on Python 3.14.6 and 3.11.9) puts a project in a subfolder: it finds the checkout beside
the worktree and not a `shared-*` folder beside the project, and reverting the fix fails it.
The real-build cases in the R17 entry are unaffected: there the project root is the worktree
root.

## R18 options (C6f, 2026-10-24)

**SPEC:** R18 in shared/main `b5d5477`, with the user's readings of 2026-10-24: the
options are `config/sdkconfig.json` in every CMake build folder; every option the
profile names has the profile's value, "unset" meaning absent or off, and the rest are
free; a check on finished release builds, not a refusal. **Check:**
`vtrux_checks/options.py` (`python -B -m vtrux_checks options --build <dir>`) at
`632962f`, written from SPEC R18. Its 16 unit tests in `vtrux_checks/tests/` show each rule
failing on a planted input and passing on a faithful one, and nine mutants of the check each
fail them. They pass on Python 3.14.6 and 3.11.9. **Profile:** gen-inhibit's
`vtrux_checks.json` at `gen-inhibit/tester/release-profile` `b565924`: its spec 12.3 item 3
settings 1 to 5 (rollback on; secure boot, flash encryption, with
`CONFIG_FLASH_ENCRYPTION_ENABLED` too, and virtual eFuses off;
`CONFIG_TWAI_ISR_IN_IRAM` unset; the two watchdog settings section 9.1 records), plus R12's
`CONFIG_APP_REPRODUCIBLE_BUILD` on. Each value cites its spec line. **Builds:** release
builds by `shared/tools/build-tooling` at `30279ba` (`vtrux_build/` tree `7a9eaf6`) of the
fixture of the earlier entries (gen-inhibit `d2e0bb5`), each with one `sdkconfig` change
committed on top. **Cases:** `verification/options_cases.py` at `632962f`, whose
`--self-test` catches every broken input. The failures each case expects, folder by folder,
come from each build folder's `config/sdkconfig.h`, not from the check's reader. **Host:**
NICKY-XPS (IDF v5.4.1, its Python 3.11.9). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R18 options check"); `xps_options_r18/SHA256SUMS`
(`f87d4860...fbdf10`) hashes every log and `options_results.json`.

| Case | Result | What was tested |
|---|---|---|
| Profile met | PASS | The fixture (reproducible build on): exit 0, with the app's and the bootloader's options read. |
| Bootloader's options missing | PASS | The same build with `build/bootloader/config/sdkconfig.json` moved aside: exit 1, cannot read, naming `build/bootloader`. |
| A set option off | PASS | `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` off: exit 1, naming it in `build` and `build/bootloader`, and nothing else. |
| An unset option on | PASS | `CONFIG_ESP_TASK_WDT_PANIC` on: exit 1, naming it in both folders, and nothing else. |
| An option the profile does not name | PASS | `CONFIG_LOG_COLORS` off: exit 0. |
| R12's option off | PASS | `CONFIG_APP_REPRODUCIBLE_BUILD` off: exit 1, naming it in both folders, and nothing else. |

| Req | Result | What was tested |
|---|---|---|
| R18, gen-inhibit's release builds | **not met, cause step 0b** | The `CONFIG_APP_REPRODUCIBLE_BUILD` off case is gen-inhibit `d2e0bb5` as committed: its tree differs from that commit only by `vtrux_build.json`, with `sdkconfig` and `sdkconfig.old` the same. Its release build fails R18 on `CONFIG_APP_REPRODUCIBLE_BUILD` alone, in both folders. Every other option the profile names is at its value. `gen-inhibit/main` `b1bd01e` differs from `d2e0bb5` only in `AGENTS.md`. Port step 0b turns the option on. |

## R8's sdkconfig clause (C6f, 2026-10-24)

**Tooling tested:** `shared/tools/build-tooling` at `a43c6e8` (`vtrux_build/` tree
`c5e5024`), merged into `shared/tools/checks` as `547ee26`. **SPEC:** R8 in shared/main
`c1a502c`: a release build is refused unless the `sdkconfig` it used
(`project_description.json`'s `config_file`) is committed in the project, tracked by git and
not ignored. **Checks:** `verification/release_cases.py` at `7384214`, and at `2cf217f` for
the second run of the generated-file case; each commit's `--self-test` catches every broken
input. The self-test checks the new helpers on a small repo, and four mutants of them each
fail it. The release command takes no `-D`, so a case that moves `SDKCONFIG` sets it in the
top-level `CMakeLists.txt` before `project.cmake` is included, a form VALIDATION R8 lists.
Each case confirms from the build that `config_file` names the file it planted, and asks git
itself whether that file is tracked and ignored. **Project:** the fixture of the earlier
entries (gen-inhibit `d2e0bb5`), with each case's change committed on top. **Host:**
NICKY-XPS (IDF v5.4.1, its Python 3.11.9). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R8's sdkconfig clause"); `xps_r8_sdkconfig/SHA256SUMS`
(`297484c7...c3f7ff`) hashes every log and `results.json`.

| Case | Result | What was tested |
|---|---|---|
| The default `sdkconfig` | PASS | The fixture's release build: one record, `91acd964...`, judged field by field. |
| Ignored by `.gitignore` | PASS | The root `sdkconfig`, still tracked, matched by a committed `.gitignore` line: refused, no record. |
| Ignored by `.git/info/exclude` | PASS | The same, matched by `.git/info/exclude`: refused, no record. |
| Untracked | PASS | The root `sdkconfig` out of the index and ignored, still on disk: refused, no record. |
| Untracked, written by the reconfigure | PASS | `SDKCONFIG` at an ignored `sdkconfig.gen` in the project root that the reconfigure writes: refused, no record. The first run was VOID, a fault in the case: it named a file in a folder that did not exist, and the reconfigure failed before the check. |
| Outside the project | PASS | `SDKCONFIG` at a copy in a folder beside the project whose name starts like the project's: refused, no record. |
| Committed elsewhere in the project | PASS | `SDKCONFIG` at a committed `configs/sdkconfig.release` with `CONFIG_LOG_COLORS` off: built and recorded, and `CONFIG_LOG_COLORS` is absent from the build's `sdkconfig.h`, so the build used that file. |

**Not tested:** a symbolic link or junction inside the project pointing outside it (SPEC says
"in the project"; the build message says the tooling resolves links, which no case here shows).

## R20 build switches (C6f, 2026-10-24)

**SPEC:** R20 in shared/main `d4c16e9`, with the readings of 2026-10-24 (the user's and the
gen-inhibit reviewer's, Q1 to Q12): the source files are the C and C++ files the build's own
record says it read from the project root, minus `build/` and `managed_components/`, plus the
CMake files it read from the repo; the scan rules carry over from gen-inhibit's spec 12.3
item 3 and its `build_switches.py`; refusal rows are declarative image or ELF rules where they
can be, otherwise a named row in the project's harness, read from the rows file it writes
beside the build; a managed component carrying a table has it applied, and a
`shared_components` entry with none fails. Also the user's option (b): a compile definition a
project's CMake sets is not listed unless the project's own compiled source tests it. And the
reviewer's reading (a): a refusal row may refuse more images than its own switch's.
**Check:** `vtrux_checks/switches.py` (`python -B -m vtrux_checks switches --build <dir>`) at
`shared/tools/switches` `5c43ada`, written from SPEC R20. Its 42 unit tests show each scan
rule, the table reader, each refusal rule, the rows file and the judge failing on a planted
input and passing on a faithful one. They pass on Python 3.14.6 and 3.11.9, with the ELF reader
matching `riscv32-esp-elf-nm` on a real ELF, and 26 mutants of the check each fail them.
`48a13f0` removed the check's empty hook for extra scanners, per option (b), and added the test
for compile definitions set by CMake. No scanner was ever registered, so no result changes.
**Builds:** release builds by `vtrux_build/` tree `c5e5024` (build-tooling `a43c6e8`) of
gen-inhibit at `gen-inhibit/tester/switch-table` `ef18e9a`, whose `vtrux_checks.json` holds its
table and whose `build_output.py` writes the rows file. The switch-on builds are bench builds
(`idf.py -D`), since a release build cannot turn a switch on. **Cases:**
`verification/switches_cases.py` at `5c43ada`. Its `--self-test` catches every broken input,
and 13 mutants of its judge each fail it. Each case confirms its trigger without the check's
reader: the deps log, `CMakeCache.txt`, the rows file. **Host:** NICKY-XPS (IDF v5.4.1, its
Python 3.11.9). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R20 build-switch check"); `xps_switches_r20/SHA256SUMS`
(`abc53df2...a6c2d1`) hashes every log, `switches_results.json` and `tests_5c43ada.txt`.

| Case | Result | What was tested |
|---|---|---|
| Every symbol listed | PASS | gen-inhibit's release build, its own RealDash row passing: exit 0, nothing named. |
| A stale entry | PASS | The same build, the table with an entry nothing tests: exit 1, naming it stale. |
| A managed component's table | PASS | A table planted in `managed_components/espressif__mdns/` listing all but one symbol the build tests there, plus one it does not: exit 1, naming both under that component. |
| A shared component without a table | PASS | `vtrux_build.json` naming `espressif/mdns` as shared, with no table there: exit 1, naming it. |
| Planted symbols | PASS | A commit testing a new symbol in compiled C, in an uncompiled header, and as a build option in a `CMakeLists.txt`, plus a variable the file sets before testing it: exit 1, naming the C one and the CMake option only. |

| Refusal row | Shown failing (switch on) | Shown passing (switch off) |
|---|---|---|
| `GI_INSTRUMENT_RXQ`, `GI_INSTRUMENT_TXABORT`, `GI_INSTRUMENT_ABORTWIN`, `OTA_HEALTH_FAULT_INJECT`, `GI_TEST_RESTART_QUIESCE` (test-only) | PASS: each switch on alone, exit 1, naming its own refusal only | PASS: the release build names none |
| `WICAN_FEATURE_MQTT`, `WICAN_FEATURE_ELM327` (stock off) | PASS: each on alone, exit 1, naming its own refusal, and RealDash's (below) | PASS |
| `WICAN_FEATURE_REALDASH` (stock off; a project row) | PASS: on alone, exit 1, naming its own refusal only | PASS: the row passes in the rows file, and the check names nothing |
| `WICAN_FEATURE_AUTOPID` (stock off) | PASS: on with MQTT and ELM327, which it needs, exit 1, naming all three, and RealDash's. A control with only MQTT and ELM327 on names those two and RealDash's, never AutoPID's | PASS |

**Observation: RealDash's row refuses the MQTT and ELM327 images too.** Its only rule is the
project row "no boot-time bus enable in app_main". The gen-inhibit reviewer gives the cause: that
row is a property of the image, that no stock feature enabling the bus at boot is present, and
MQTT and ELM327 both enable the bus at boot. By reading (a) that is allowed, so the switch-on
cases accept extra names as refusals and never as a pass. The first run (on `3cab845`, in
`run1_3cab845/`, `SHA256SUMS` `be26f7b2...89dd02`) judged them against the exact set, and those
four cases failed on that extra name alone. Its `component_table` case was VOID, a fault in the
case, fixed in `5c43ada`.
