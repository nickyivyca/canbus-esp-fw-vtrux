# Shared build tooling: verification evidence

Test evidence that the tooling in `shared/tools/` meets each requirement in
`SPEC.md`. Why each requirement is written as it is: `VALIDATION.md`.

Each entry names the requirements it covers (R1 to R30), the commit of the
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

## R19 reproducible (C6f, 2026-10-24)

**SPEC:** R19 in shared/main `d4c16e9` (merged as `4369101`): one clean commit, built twice on one
machine in two folders, each time by the release build script, gives the same image hash; a test
of the tooling, rerun when the build script, record writer, image library, ESP-IDF or its Python
environment, compiler, CMake or Ninja changes, and run once by each project when it first builds
with the tooling. With R9's fixed `SOURCE_DATE_EPOCH` (same commit). **Tooling tested:**
`shared/tools/build-tooling` at `3543457` (`vtrux_build/` tree `4e33d70`), merged into
`shared/tools/checks` as `4949fcd`. **Test:** `verification/repro_cases.py` at `8c58880`, written
from SPEC R19. It clones each commit into two new folders of different length and depth, runs
the release build in each with its own stand-in record folder, and reads each image hash three
ways (the record, `vtrux_build.image` and the tester's own reader), which must agree. A case is
VOID unless both clones are clean at the same commit with no build folder, and its trigger (the
reproducible-build setting in `sdkconfig.h`, the case's marker in the `.bin`) shows. Its
`--self-test` catches every broken input, and 11 mutants each fail it. **Rerun triggers
recorded:** ESP-IDF v5.4.1 with its Python 3.11.9 environment (`pip freeze` hash recorded),
esptool v4.12.0, riscv32-esp-elf-gcc 14.2.0 (esp-14.2.0_20241119), CMake 3.30.2, Ninja 1.12.1.
**Host:** NICKY-XPS. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R19 reproducible test, again"); `xps_repro_r19_3543457/SHA256SUMS`
(`cc4b2d26...04ab`) hashes every log, `repro_results.json` and `tests_4949fcd.txt`.

| Case | Result | What was tested |
|---|---|---|
| The fixture | PASS, met | gen-inhibit `d2e0bb5` with `vtrux_build.json` and reproducible build on: `91acd964...` both times. |
| The minimal project | PASS, met | hello_world for esp32c3, reproducible build on: `22638905...` both times. |
| `__DATE__` and `__TIME__` | PASS, met | The minimal project printing both: `7ff7a771...` both times, the marker string in both images. R9's fixed `SOURCE_DATE_EPOCH` is what this case needs. |
| A build-path define (control) | PASS, not met | The minimal project printing a define holding `CMAKE_BINARY_DIR`: two hashes, the images of different sizes. The test can fail. |

| Req | Result | What was tested |
|---|---|---|
| R19, gen-inhibit's own run | **FAIL** | Observed: gen-inhibit as committed (`d2e0bb5`, reproducible build off), plus `vtrux_build.json`, built twice gives `340767d6...` and `e1dc80c8...`. The two `.bin` files differ only in the descriptor's `elf_sha256` field: 0 bytes outside it and R2's blanks. Spec: the same image hash both times. `gen-inhibit/main` at `2a30345` has the same `sdkconfig`, the option still off. |

The earlier run of this test (tooling `a43c6e8`, in the run folder's section "R19 reproducible
test") predates the merged SPEC and the fixed `SOURCE_DATE_EPOCH`; it is not cited here.

## R9's fixed SOURCE_DATE_EPOCH and R24's environment (C6f, 2026-10-24)

**SPEC:** R9 and R24 in shared/main `d4c16e9`. R9: a release build runs with `SOURCE_DATE_EPOCH`
set to one fixed value, the same for every project and commit. R24: a release build runs ESP-IDF
in an environment the tooling builds from an empty one, passing through only the caller's
variables on its own list, adding its own settings; it prints the names it dropped, and the
record lists the names it passed. **Tooling tested:** `shared/tools/build-tooling` at `3543457`
(`vtrux_build/` tree `4e33d70`), merged into `shared/tools/checks` as `4949fcd`. **Checks:**
`verification/release_cases.py` at `1ea1495` (on `shared/tools/epoch-env`), written from SPEC,
not from the tooling's source or its pass lists. Its `--self-test` catches every broken input,
and 16 mutants of the R9 and R24 judges each fail it. **Project:** branches of the minimal project
of the earlier entries (`4100948`). **Hosts:** NICKY-XPS (IDF v5.4.1, its Python 3.11.9) and
madhouse-debian (IDF v5.4.1), the first release build there with the tooling's Linux list.
**Run files:** `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R9's fixed `SOURCE_DATE_EPOCH` and R24's environment");
`xps_epoch_env/SHA256SUMS` (`5e27354b...31cc8f`) and `r924_linux/SHA256SUMS` (`6d6fc85a...4dc0`)
hash every log and `results.json`.

| Req | Result | What was tested |
|---|---|---|
| R9, fixed `SOURCE_DATE_EPOCH` | PASS on both hosts | The project prints its `__DATE__` and `__TIME__`. A plain build with the caller's `SOURCE_DATE_EPOCH=12345` shows gcc's stamp for it, so the variable reaches the compiler. Release builds of commit A (caller sets none), commit B (caller 12345) and commit A as another project (caller 99999) each hold one stamp, `Jan  1 2026 00:00:00`, the same in all three and on both hosts, and no caller's. |
| R24, environment from an empty one | PASS on both hosts | The caller plants `EXTRA_CFLAGS`, `EXTRA_CPPFLAGS`, `CFLAGS` and `CPPFLAGS`, each carrying a define the project prints, and `R24_PLANTED`, which its configure prints. A plain build shows `EXTRA_CFLAGS`, `EXTRA_CPPFLAGS` and `R24_PLANTED` reaching a build; `CFLAGS` and `CPPFLAGS` reach none, so they are not judged. The release build builds, shows none of the three, prints every planted name among those it dropped, and its record's `environment_passed` lists none of them. It lists 8 names on NICKY-XPS and 4 on madhouse-debian, all set by the caller. |

The code hashes of each build agree across the two hosts.

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

## R21 board data (C6f, 2026-10-24)

**SPEC:** R21 in shared/main `9e0daa9`, the merge of `a5e7c46`, whose wording the check and
the board data cite. It adds the flash size, in both the app's and the bootloader's headers
(user, 2026-10-24, option B; the user confirmed that reading, through the gen-inhibit
reviewer). The gen-inhibit reviewer's readings of 2026-10-24 (Q1 to Q7), which `VALIDATION.md`
R21 records: the table compared is the build's own `partition-table.bin`, byte for byte; the reference is a `.bin` named by path in the board data, with no stored hash; the
slot is the smallest app partition of the reference table, and the image fits when it is no
larger; the tool prefix is a property of the checks, named from the board's `IDF_TARGET` by
ESP-IDF's own per-target files, and a build whose target is not the board's fails; a board used
by one project has its data inline in that project's `vtrux_checks.json`, while a shared
board's comes from a shared board component under `managed_components/`; release builds only,
and of the bootloader only its header is read, never its contents. The WiCAN's flash size,
4 MB, is from gen-inhibit spec section 2.
**Check:** `vtrux_checks/board.py` (`python -B -m vtrux_checks board --build <dir>`) at
`shared/tools/board-data` `6fc499b`, written from SPEC R21. The board data is `{"target",
"partition_table", "flash_size"}`. The tool prefix is the `_CMAKE_TOOLCHAIN_PREFIX` of the build's
ESP-IDF `tools/cmake/toolchain-<target>.cmake`. The flash size field (the high four bits of
header byte 3), its values and the header's magic byte come from that ESP-IDF's
`esp_app_format.h`. Its 32 unit tests show each rule failing on a planted input and passing on
a faithful one. They pass on Python 3.14.6 and 3.11.9, reading the real ESP-IDF v5.4.1 too
(`riscv32-esp-elf-`, `xtensa-esp32s3-elf-`, magic `0xE9`, 4MB = 2, 8MB = 3). 29 mutants of
the check and of the partition-table reader each fail them. One of the tests scans every
module in `vtrux_checks/` for a string constant naming a chip's tool and finds none. **The
named-board form is not built yet** (reading Q7 (a)): no board component exists until the
interposer's port step 2a, and the check refuses the form, naming that. `41bcc51` on the same
branch carries two follow-ups from the R20 review: `switches.py` closes `vtrux_build.json`, with a
test that no file is left open, and the README's switches row is back inside its table.
**Board data:** gen-inhibit `gen-inhibit/tester/board-data` `a46087a`, whose `vtrux_checks.json`
`board` is `esp32c3`, `test/build_checks/reference/stock-v4.13-partition-table.bin` and `4MB`.
Its `test/build_checks/test_board_data.py` (14 checks, each shown failing on a planted input,
and 5 mutants each fail it) checks that data against gen-inhibit's spec: the target is the
committed `sdkconfig`'s, the reference is tracked by git, its smallest app partition is spec
12.3 item 3's 1,781,760 bytes, and the flash size is section 2's 4 MB and the committed
`sdkconfig`'s.
**Builds:** release builds by `vtrux_build/` at `6fc499b` of `release_cases.py`'s fixture
(gen-inhibit `d2e0bb5` with its fixture commits), and of the minimal project set to esp32s3.
**Cases:** `verification/board_cases.py` at `6fc499b`. Its `--self-test` catches every broken
input, and 18 mutants of its readings and judges each fail it. Each case works out what the
check must name without the check's reader: the table bytes, the reference's app entries read
here, the flash size field of each header read here against its own table of the image format,
`sdkconfig.h`'s target and flash size, and the build's `monitor_toolprefix`. **Host:** NICKY-XPS
(IDF v5.4.1, its Python 3.11.9). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R21 board-data check"); `xps_board_r21b/SHA256SUMS`
(`23b8e046...d27214`) hashes every log, `board_results.json`, the board data copy and
`tests_6fc499b.txt`.

| Case | Result | What was tested |
|---|---|---|
| WiCAN, faithful | PASS | The fixture's release build with the WiCAN's board data: exit 0, the table identical, the image under the 1,781,760-byte slot, both headers 4MB, prefix `riscv32-esp-elf-`. |
| Another target | PASS | The same build, the board's target `esp32s3`: exit 1, naming the target only; the prefix printed is `xtensa-esp32s3-elf-`. |
| A smaller slot | PASS | The same build, the reference a copy of the stock table with `ota_0` one byte smaller than the image: exit 1, naming the slot, and the table, which then differs. |
| No reference file | PASS | The same build, the reference path naming no file: exit 1, cannot read. |
| A named board | PASS | The same build, `board` a name: exit 1, cannot read, "not built yet". |
| Table differs | PASS | A release build of a commit making `storage` 296K in the partition CSV: exit 1, naming the table only. |
| Flash size larger | PASS | A release build of a commit setting the committed `sdkconfig`'s flash size to 8MB, both headers then giving 8MB: exit 1, naming the flash size of the app's and the bootloader's header, and nothing else. |
| esp32s3, faithful | PASS | The minimal project set to esp32s3, its own table and flash size (2MB) as the board's: exit 0, prefix `xtensa-esp32s3-elf-`; R17's scope check passes on it too. |
| esp32s3 build, esp32c3 board | PASS | The same build, the board's target `esp32c3`: exit 1, naming the target only. |

**Not a case: a smaller flash size.** By `VALIDATION.md` R21 it fails the build itself, since
the partition table no longer fits.

**Also: R20's ELF reader on an Xtensa ELF** (asked for by the interposer reviewer). Its unit
tests had compared it with `riscv32-esp-elf-nm` only. `s3_elf_reader` runs `test_switches.py`'s
`test_as_nm` on the esp32s3 build's ELF against `xtensa-esp32s3-elf-nm`: PASS, the same
symbol set.

The first run of the cases, on `f05ad48` (before the flash size), is in `xps_board_r21/`
(`SHA256SUMS` `14986bd4...55eebc`). All 9 results passed there; that check is superseded by
`6fc499b`.

## R23, R16's tooling-pin clause and R13's tooling field (2026-10-25)

**SPEC:** shared/main `1224b24`: R23 (the tooling runs from `../shared-<tag>/` beside the
project's worktree root and refuses to start unless that checkout's R7 digest matches the
project's committed pin; a missing checkout is refused with the command that creates it), R16's
second clause (a release build is refused if the tooling's own pin names a commit rather than a
tag) and R13's last field (the record holds the tooling's pin: its tag, commit and digest).
**Tooling tested:** `shared/tools/pin` at `963290c`, merged into shared/main as `1224b24`
(`vtrux_build/` tree `37737eb`). It ran from checkouts of `shared/tools/pin-verify` `7d28045`,
whose `vtrux_build/` is the same; that commit differs from `1224b24` only in the tester's
`verification/`. **Checks:** `verification/release_cases.py` at `7d28045`, written from SPEC,
not from `pin.py`. Every case now runs the tooling from a pinned checkout: the fixture repo tags
the tooling's commit `2000.01.01` (a tag no real shared/main commit carries), every fixture
commit and the minimal project commit the matching `shared_pins.json`, and each project's
checkout is a `git worktree add --detach` of the fixture repo beside it. Its `--self-test`
catches every broken input, including the new pin verdict and printed-command reader, and 13
mutants of those and of the record judge's tooling fields each fail it. **Project:** the fixture
of the earlier entries (gen-inhibit `d2e0bb5` with its fixture commits, now carrying the pin).
**Host:** NICKY-XPS (IDF v5.4.1, its Python 3.11.9). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R23, R16's tooling pin and R13's tooling field"); `xps_pin_r23/SHA256SUMS`
(`e217acd8...35261`) hashes every log, `results.json` and `tests_7d28045.txt`.

The pin cases run on their own clone of the fixture. Each changes one thing from `r23_control`,
and each refusal is judged from outside: refused with `--check-only` and again as a full release
build, with no `build/` folder made (so it never started) and no record written. Each case first
confirms its own trigger, reading the checkout's digest, HEAD and tags itself.

| Case | Result | What was tested |
|---|---|---|
| `r23_control` | PASS | The checkout `../shared-2000.01.01/` beside the project, at the pin: `--check-only` exit 0. |
| `r23_md_edited` | PASS | The same, with the checkout's `README.md` changed: the digest is still the pin's (R7 leaves out `.md`), and `--check-only` exits 0. |
| `r23_missing` | PASS | No checkout beside the project; the tooling run from another checkout at the pin: refused. The refusal prints `git -C <project> worktree add --detach ../shared-2000.01.01 2000.01.01`; run from the project's root, it made the checkout at the pin's commit, and `--check-only` from it then exits 0. |
| `r23_wrong_copy` | PASS | The tooling run from a byte-for-byte copy of the checkout's `shared/tools` elsewhere (same digest), the checkout beside the project: refused. |
| `r23_wrong_other` | PASS | The tooling run from another real checkout at the pin (`p1`'s), not the one beside this project: refused. |
| `r23_digest_changed` | PASS | One tracked non-`.md` file in the checkout changed (`pyproject.toml`): refused. |
| `r23_digest_added` | PASS | One untracked non-`.md` file added to the checkout's `shared/tools`: refused. |
| `r23_head_moved` | PASS | The checkout's HEAD a commit beside the pin's that adds one file outside `shared/tools`, so the digest is the pin's: refused. |
| `r23_tag_other` | PASS | The pin's tag `2000.01.02` marks that other commit; the checkout at the pin's commit, digest the pin's: refused. |
| `r16_tag_absent` | PASS | The pin's tag `2000.01.03`, which no repository here has; the checkout at the pin's commit: refused. |
| `r16_tag_commit` | PASS | The pin's tag the commit's 40-hex id, which is also a real tag marking that commit, so absence is not why: refused. |
| `r16_tag_short` | PASS | The same with the 7-hex id: refused. |
| `r16_no_tag` | PASS | A pin with no tag: refused. |
| `r16_branch` | PASS | The pin's tag a branch name at the pin's commit, not a tag: refused. |

| Req | Result | What was tested |
|---|---|---|
| R13, the tooling field | PASS | Each record's tooling field (`a_release`, `r22_mini`, `r13_tooling_dirty`) against the committed pin's tag, the checkout's HEAD, the digest read here from the checkout and `git status` of the checkout's `shared/tools`: `2000.01.01`, `7d28045`, `e16aec27...`, dirty false. `r13_tooling_dirty` builds the minimal project with the checkout's `README.md` edited (the digest is unchanged, so the build runs): record `ae734ba4...`, dirty true. |
| R8, R10, R13 refusals from the checkout | PASS | `pre_unset_ri`, `pre_overwrite`, `pre_untracked`, `pre_modified`, `pre_staged`, `pre_deleted`: each refused as in the first entry, the tooling now from the pinned checkout. |
| R5, R16 (`override_path`), R22, R13 second build | PASS | `a_release` (record `6469e156...`), `a_again` (skipped, the record folder unchanged), `r5_p2_same_commit` (the same image hash from another clone path and branch), `r16_override_path` (refused, no record) and `r22_mini` (record `a45eaf28...`), each as in the earlier entries, the tooling now from the pinned checkout. |

26 cases, all PASS.

**Not tested:** a checkout replaced by a plain copy with no git at its exact place beside the
project (the HEAD and tag checks would read nothing there). R25's tag form is not checked by any
case: SPEC R23 and R16 refuse by digest, commit and tag, not by the tag's name.

## R26, R27 and R28 options (2026-10-25)

**SPEC:** shared/main `5151e77`: R26 (`CONFIG_SECURE_BOOT`, `CONFIG_SECURE_FLASH_ENC_ENABLED`
and `CONFIG_FLASH_ENCRYPTION_ENABLED` unset and `CONFIG_EFUSE_VIRTUAL` off in every release
build, whatever the profile names), R27 (every project's profile names `CONFIG_FREERTOS_HZ`) and
R28 (the three log levels at none, none and error, `CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL` none where
the Arduino core is compiled, and no `-D` for `CORE_DEBUG_LEVEL` or `LOG_LOCAL_LEVEL` in a build
file), all applied by the options check. With the reviewer's rulings of 2026-10-25: the Arduino
core is detected by two signals (a build folder's options naming
`CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL`, and an Arduino component among its components), and a
disagreement between them fails; the `-D` search reads each
CMake build folder's `compile_commands.json`; a profile that contradicts R26 or R28 is refused;
R27's value must be an integer. **Check:** `vtrux_checks/options.py` on
`shared/tools/options-r26-r28` at `aa0d67a` (the check `369c1a8`, the cases `8d65e6b` and
`aa0d67a`, from `5151e77`), written from SPEC. Its 39 unit tests in `vtrux_checks/tests/` show
each rule failing on a planted input and passing on a faithful one, the Arduino signals and the
lookalike defines among them, and 14 mutants of the R26-R28 rules each fail them; the other
`vtrux_checks` tests still pass. **Builds:** release builds by `vtrux_build/` tree `37737eb`
(`5151e77`'s), run from a checkout pinned at `aa0d67a` as in the R23 entry, of a fixture made from
gen-inhibit `3efad4c`, each case with its `sdkconfig` (and, for one, `main/CMakeLists.txt`)
change committed on top. **Profile:** gen-inhibit's `vtrux_checks.json` at `3efad4c`
(sha256 `7761ca4e...285def`), which names `CONFIG_FREERTOS_HZ` 1000. **Cases:**
`verification/options_cases.py` at `aa0d67a`, whose `--self-test` catches every broken input.
The failures each case expects, rule by rule and folder by folder, come from each build folder's
`config/sdkconfig.h` and `compile_commands.json`, not from the check's reader; a case passes when
the check names exactly those, prints PASS for each rule with none, and exits 1 when any rule has
one. **Host:** NICKY-XPS (IDF v5.4.1, its Python 3.11.9). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/sharedtools_c6f/`
(`README.md`, section "R26, R27 and R28 options check"); `xps_options_r26_r28/SHA256SUMS`
(`ef7a1286...a41f7a`) hashes every log, `options_results.json`, `tests_aa0d67a.txt` and a void
first attempt (a fixture made before R23, stopped at the pin before building).

| Case | Result | What was tested |
|---|---|---|
| `release_pass` | PASS | gen-inhibit's options as committed: R18, R26 and R27 pass (R27 reads 1000); R28 fails on `CONFIG_LOG_DEFAULT_LEVEL`, `CONFIG_LOG_MAXIMUM_LEVEL` and `CONFIG_BOOTLOADER_LOG_LEVEL` (3, info) in `build` and `build/bootloader`, and nothing else; exit 1. |
| `bootloader_missing` | PASS | The same build with `build/bootloader/config/sdkconfig.json` moved aside: exit 1, cannot read, naming `build/bootloader`. |
| `r27_no_tick` | PASS | The same build, judged against the profile with `CONFIG_FREERTOS_HZ` taken out: R27 fails, R28 as above, nothing else; exit 1. |
| `r26_efuse_virtual` | PASS | `CONFIG_EFUSE_VIRTUAL` on: R26 fails on it in both folders, and R18 too (the profile names it off), R28 as above, nothing else; exit 1. |
| `r28_levels_none` | PASS | The levels at ESP-IDF's none and the bootloader's error: every rule passes, exit 0. |
| `r28_define` | PASS | The same plus `LOG_LOCAL_LEVEL` defined by `target_compile_definitions` in `main/CMakeLists.txt`: R28 fails on the `-D` in `build` alone, nothing else; exit 1. |

| Req | Result | What was tested |
|---|---|---|
| R26, R27, R28 on a real release build | PASS | The check reads, judges and names each of them on real builds, as in the cases above. |
| R26 and R27, gen-inhibit's release builds | PASS | `release_pass`: gen-inhibit `3efad4c`'s options, release-built, with its profile. |
| R28, gen-inhibit's release builds | **not met, known anomaly 5** | `release_pass`: the three log levels are 3 (info) in both folders, against none, none and error. No `-D` is found, and the Arduino core is not compiled. `gen-inhibit/main` `ad4c367` differs from `3efad4c` only in skills. |

**Not tested on a real build:** a project that compiles the Arduino core (R28's
`CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL` and the two signals), a `-D CORE_DEBUG_LEVEL`, the other three
R26 options set, and a contradicting or non-integer profile. The unit tests cover each of these.
R18's own cases of its entry were not run again on `aa0d67a`.

## R21 from the board component, R29 board settings and R30 pins (gen-inhibit step 1a, 2026-10-27)

**SPEC:** shared/main `2f72a20`: R21 widened (the board is the one `shared/boards/*` component in
`dependencies.lock`, its data read from that component in `managed_components/`), R29 and R30,
with `../boards/SPEC.md` B2-B4 and B6. **Check:** `vtrux_checks/` on `shared/tools/board-checks`
at `653ee07` (from `2f72a20`: the checks `73ab1ec`, the cases' fix `0579b8e`, a test `653ee07`),
written from SPEC. `boarddata.py` finds the board: a git source whose path is exactly
`shared/boards/<board>`, or a local one whose path ends so; a release build is refused unless
there is exactly one, and a local one is refused because it is not in `managed_components/`.
`board.py` reads R21's target, flash size and reference table from its `board.json`; the old
inline `board` key in `vtrux_checks.json` is no longer read. R29 is in `options.py`, R30 in
`pins.py` (`python -B -m vtrux_checks pins --build <dir>`). **Unit tests:** `test_boarddata.py`
16, `test_board.py` 33, `test_options.py` 56 and `test_pins.py` 11. Each rule is shown failing
on a planted input and passing on a faithful one. `test_scope.py` and `test_switches.py` still
pass, and `test_board.py` also passes against the real ESP-IDF v5.4.1. 37 mutants of
`boarddata.py`, R21's lock part, R29 and R30 each fail them. The first mutant run missed one,
a registry source carrying a `shared/boards/` path; `653ee07` adds that test. The board reader
reads gen-inhibit's `wican_v3` folder (`shared/boards/wican-v3-data` `3690cfe` merged with the
implementor's `b66a151`): its `board.json`, and its `sdkconfig.board` as 9 options.
**Board data:** `shared/boards/wican_v3/board.json` at `3690cfe`: `esp32c3`, `4MB`, the
reference `stock-v4.13-partition-table.bin` (sha256 `98d193a6...2185e`, SPEC section 2), the
four settings required of the project, and `OBD`. **Builds:** release builds by `vtrux_build/`
at `0579b8e` of `release_cases.py`'s fixture made from gen-inhibit `d2e0bb5`, the base the
R21 entry above used. gen-inhibit's tree has been in `gen-inhibit/firmware/` since port step 0d,
and the fixture still expects it at the root; that is a harness fix to come. The minimal
project was set to esp32s3 for the esp32s3 cases. No board component is built yet, so after
each build `board_cases.py` plants `wican_v3` in `managed_components/vtrux__wican_v3/`, with a
git entry for it added to `dependencies.lock`. **Cases:** `verification/board_cases.py` at
`0579b8e`, whose `--self-test` catches every broken input; it now also covers the planted lock
entry, read by the checks' own lock reader. **Host:** NICKY-XPS (IDF v5.4.1, its Python 3.11.9),
throttled (Idle, `--jobs 1`). **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/board_cases_0579b8e_20261027/`.
Its `SHA256SUMS` (`c7172208...6051b`) hashes every log, `board_results.json`, the board data
copy, `tests_0579b8e.txt`, and the mutant runs with their script. A first run on `73ab1ec`
(`board_cases_73ab1ec_20261027/`) is void: its fixture was made from gen-inhibit `8919fb3`,
and two plumbing faults of the cases were fixed in `0579b8e`.

| Case | Result | What was tested |
|---|---|---|
| `release_pass` | PASS | The fixture's release build with `wican_v3` planted: exit 0, prefix `riscv32-esp-elf-`. |
| `other_target` | PASS | The same build, `board.json`'s target `esp32s3`: exit 1, naming the target only. |
| `small_slot` | PASS | The same build, the reference a copy of the stock table with `ota_0` one byte smaller than the image: exit 1, naming the table and the slot. |
| `missing_reference` | PASS | The same build, the reference naming no file: exit 1, cannot read. |
| `no_board` | PASS | The same build, no `shared/boards/*` entry in the lock: exit 1, cannot read, 0 components. |
| `two_boards` | PASS | The same build, a second `shared/boards/*` git entry: exit 1, cannot read, 2 components. |
| `local_board` | PASS | The same build, the board's entry a local folder: exit 1, cannot read, from a local folder. |
| `table_differs` | PASS | A release build of a commit making `storage` 296K: exit 1, naming the table only. |
| `flash_larger` | PASS | A release build of a commit setting the flash size to 8MB: exit 1, naming the flash size only. |
| `s3_pass` | PASS | The minimal project on esp32s3, its own table and flash size planted as the board's: exit 0, prefix `xtensa-esp32s3-elf-`; R17's scope check passes on the build as built. |
| `s3_board_c3` | PASS | The same build, the board's target `esp32c3`: exit 1, naming the target only. |
| `s3_elf_reader` | PASS | R20's ELF reader on the esp32s3 ELF against `xtensa-esp32s3-elf-nm`. |

| Req | Result | What was tested |
|---|---|---|
| R21 on a real release build, the board from its component | PASS | The cases above. |
| R29 | **unit tests only** | Not yet on a real build. Its reading of an option missing from the bootloader's folder, and of a required Kconfig choice, is open with the gen-inhibit reviewer (2026-10-27). Real-build cases come with the first build that takes `wican_v3` (gen-inhibit step 1a). Real builds: the entry of 2026-10-28 below. |
| R30 | **unit tests only** | Not yet on a real build: that needs a project compiling `board_pins.h` from the component (gen-inhibit step 1a). Real builds: the entry of 2026-10-28 below. |

## R18 and R29 by folder, R29's choices, R17's git components (gen-inhibit step 1a, 2026-10-27)

**SPEC:** `shared/boards/spec-folders` `6a00663` (user, 2026-10-27, in the gen-inhibit
reviewer's session): R18 and R29 judged folder by folder (an option set to a value is in the
app's build folder with it, and has it in any other folder that has the option at all; "unset"
is absent or off); R29's required choices (named by exactly one member on, and in each folder
that has the choice exactly one listed member on); R17's clause for a component taken from git
(the lock's commit is the one the pinned tag names, and the component's manifest has no
`version`). **Check:** `vtrux_checks/` on `shared/tools/board-checks` at `0bbe9ff`, written
from SPEC. `options.py`'s `judge()` takes the app's folder for R18 and for R29's fixed options;
R26 and R28 still judge every folder, absent included. `scope.py` reads the tag a git component
is pinned at from the `idf_component.yml` of the project's own components (those the build used
from the project root, not from `managed_components/`), which must name one and agree, resolves
it under `refs/tags/` from the project root (a branch or a commit is not a tag), and compares
its commit with the lock's. R29's choices were already read so at `653ee07`; the entry above
lists them as open, and they are now ruled. **Unit tests:** `test_options.py` 62 and
`test_scope.py` 17, each rule shown failing on a planted input and passing on a faithful one,
the pins read from a small repository with an annotated tag and a branch. Every other unit test
file of `shared/tools` still passes (424 tests in all), as does `board_cases.py --self-test`.
51 mutants of `boarddata.py`, R21's lock part, R18's and R29's folders, R29's choices, R17's git
clause and R30 each fail them. The board reader reads `wican_v3` from `3690cfe` merged with the
implementor's `3946c5e`. R17 on an existing release build (`board_cases.py`'s `release_pass`
fixture, which takes nothing from git) passes, with no git component to judge. The `options`
run there could not read the build's profile, because that fixture carries no
`vtrux_checks.json`, so it is not evidence. **Host:** NICKY-XPS, Python 3.14, Idle priority,
run while the interposer's bench suite was paused. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/checks_0bbe9ff_20261027/`;
its `SHA256SUMS` (`5616df27...a5eb5a7a`) hashes the test and mutant outputs, the mutant scripts,
the two check logs and the real-build R29 check with its script.

**Then `499c31b`** (the gen-inhibit reviewer's reading, 2026-10-27, R29's text): a required
choice's member absent from a folder is not judged, and the app's folder must have the choice
(at least one member); a single required option must still be in the app's folder. Kconfig
does not write a choice member whose dependencies are not met: in both c3 builds checked,
`CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG` is absent beside a USB-Serial/JTAG primary, so
`653ee07`'s rule that every member be in the app's folder would have failed the secondary
console that `wican_v3` now requires (`shared/boards/wican-v3-data` `77c0151`, section 2 at
`d196233`). `test_options.py` 65; 14 mutants of `options.py` each fail them
(`mutants_499c31b_options.txt`, 19:34:39 to 19:35:00, during the interposer's suite with its
tester's agreement). `r29_real.py` judges `77c0151`'s required settings, with a profile naming
the USB-Serial/JTAG console, no secondary console and Bluetooth on, against the options of
gen-inhibit `6926fb0`'s c3 build (`r29_real_prodstandin.txt`): R29's required settings and R18
by folder pass, while R18 judged in every folder, the old reading, fails on `CONFIG_BT_ENABLED`
absent from the bootloader's folder. The profile is the check's input, not gen-inhibit's.

| Req | Result | What was tested |
|---|---|---|
| R18, by folder | **unit tests only** | Not yet on a real build; `options_cases.py` runs again with C's builds (gen-inhibit step 1a). Real builds: the entry of 2026-10-28 below. |
| R29, by folder and choices | **unit tests only** | As the entry above: real-build cases come with the first build that takes `wican_v3`. `499c31b`'s judging was also run on a real build's options (above), which is not a release build of the board. Real builds: the entry of 2026-10-28 below. |
| R17, git components | **unit tests only** | No build takes a component from git yet; real-build cases come with C's builds. Real builds: the entry of 2026-10-28 below. |

## Real builds of gen-inhibit's step 1a C: R17's git clause, R18 and R29 by folder, R30, R21 on the built board (gen-inhibit step 1a, 2026-10-28)

**SPEC:** shared/main `77e114f` (tag `2026.10.27`), R17's git clause and R18 and R29 by folder as
ruled 2026-10-27 (the entry above), R21 and R30 as in the entry before it. **Check and tooling:**
`vtrux_checks/` and `vtrux_build/` at `77e114f`, the tag gen-inhibit pins. The cases run from
`shared/tools/fixture-layout` at `0df0757`, `e839370` and `ccdd525`, which merge `77e114f`
(`793ebc8`) and change only `verification/`, so the checks and the tooling are the same in all
three runs. **Builds:** release builds by that tooling of `release_cases.py`'s fixture made from
gen-inhibit `b6bee3f` (`gen-inhibit/tester/board-1a`: `gen-inhibit/main` `7a91650`, the
implementor's C, plus the tester's `3487cab`, R20's rows). Its project, `gen-inhibit/firmware/`,
takes `vtrux/wican_v3` from git at tag `2026.10.27`, whose commit is the lock's (`77e114f`); a
fresh fixture in each run, with the same code hash `ee41cace...` in all three. The board cases'
`--board` is `../shared-2026.10.27/shared/boards/wican_v3` (`board.json` sha256 `f0e6d65f...`).
`options_cases.py`'s profile is the project's own `vtrux_checks.json` at `b6bee3f`. **Cases:**
`verification/` at the three commits; every `--self-test` passes at each. **Host:** NICKY-XPS,
IDF v5.4.1 with its Python 3.11.9; no wall-clock test was running (bench check clear before each
case, "No builds" clear), so the builds were not throttled. **Run files**, each with a
`SHA256SUMS` hashing every file in it, under
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/`:
`c1a_realbuild_0df0757_20261028/` (`cb3ea1c0...3f49`; release, scope, options, board, pins),
`c1a_realbuild_e839370_20261028/` (`343a398c...69f4`; release and scope) and
`c1a_realbuild_ccdd525_20261028/` (`f531f3c0...63e3`; release, the esp32s3 board cases, pins
`release_pass`, and R17 on the tester's own bench build of `b6bee3f`).

**Harness faults found by the first run, fixed in `e839370`,** each with a self-test that fails
on the old code. A child process's output, CRLF on Windows, was matched line by line as LF, so
pins `release_pass` read `PASS R30` as missing; the mutant restoring the old reading is caught.
The esp32s3 minimal project had no board in its lock, which a release build now refuses (R21), so
its three cases were VOID; it now takes `vtrux/s3fx` from git at a tag, from a small repository
beside it. The R17 git cases were judged PASS although the clean build they change already fails
naming the same folder; they are now VOID unless `clean_release` passes. The `e839370` run's
board and pins cases are not evidence: a README commit (`ccdd525`) mid-run moved the tooling
under test, and they refused to start. They were run again at `ccdd525`.

**Observed vs spec (R17):**
- **F1.** A clean release build of the fixture, and the tester's bench build of `b6bee3f`, read
  two files from outside the project root, IDF and `managed_components/`: the repository's
  `.git/HEAD` and the checked-out branch's ref (in a linked worktree, the anchor clone's
  `.git/worktrees/<worktree>/HEAD` and `.git/refs/heads/<branch>`). The check fails both builds,
  `--release` and `--bench`, naming them. R17: every file the build read comes from the project
  root, IDF or `managed_components/`. Seen where the repository's `.git` is outside the project
  root (gen-inhibit's project is `gen-inhibit/firmware/` since port step 0d); not on the esp32s3
  minimal project, whose `.git` is in its project root.
- **F2.** The check reports `managed_components/vtrux__wican_v3: taken from git, and no
  idf_component.yml of the project pins vtrux/wican_v3 at a tag`, its summary `pinned nowhere`, on
  the fixture's release build and on the tester's bench build of `b6bee3f`; likewise for
  `vtrux/s3fx` on the esp32s3 build. In each, the project's `main/idf_component.yml` names the
  component with `git`, `path: shared/boards/<board>` and `version` the tag; the tag resolves
  under `refs/tags/` from the project root to the lock's commit; the downloaded component's
  manifest has no `version` (`realtree_pin_b6bee3f.txt`). R17: the lock's commit is the one the
  tag the project pins names, and the component's manifest has no `version`. The same builds'
  release tooling reports the component pinned (R16).

| Case | Run | Result | What was tested |
|---|---|---|---|
| `release_cases` `pre_unset_ri`, `a_release` | all three | PASS | The fixture's release build: refused with `REVERSE_IT_DIR` unset; built and recorded. |
| scope `clean_release` | `0df0757`, `e839370` | **FAIL** | The fixture's clean release build, `--release` and `--bench`: exit 1, F1 and F2. On the tester's bench build of `b6bee3f`, the same, both ways (`realtree_scope_b6bee3f.txt`). |
| scope `version_edited`, `managed_unlisted` | `0df0757`, `e839370` | PASS | Exit 1 both ways, naming `espressif__mdns` and the planted component. |
| scope `outside_header`, `other_override` | `0df0757`, `e839370` | PASS | Exit 1 both ways, naming the planted file. |
| scope `shared_override` | `0df0757`, `e839370` | PASS `--release`; FAIL `--bench` | `--release` exit 1 naming the override's three files; `--bench` exit 1 on F1 and F2 alone, the override not named, as R17 allows a bench build. |
| scope `git_lock_commit`, `git_tag_absent`, `git_tag_branch`, `git_own_version` | `e839370` | VOID | `clean_release` did not pass. At `0df0757` they were judged PASS, which does not count (above). |
| options `release_pass` | `0df0757` | PASS | R18 by folder, R26, R27 and R29 pass; R28 fails on the three log levels in both folders, gen-inhibit's own known anomaly 5, as the case expects; nothing else. |
| options `bootloader_missing`, `repro_off`, `rollback_off`, `wdt_panic_on`, `free_option`, `r26_efuse_virtual`, `r27_no_tick`, `r28_levels_none`, `r28_define` | `0df0757` | PASS | Each as in the R18 and R26-R28 entries above, on the built board. |
| options `r29_unnamed_choice`, `r29_unnamed_option`, `r29_two_on` | `0df0757` | PASS | Judged against the profile without the secondary console, without `CONFIG_BT_ENABLED`, and with `CONFIG_ESP_CONSOLE_UART_DEFAULT` also true: R29 fails on that required setting (and R18 on the console option for the last, in both folders). |
| options `r29_fixed_off` | `0df0757` | PASS | `CONFIG_TWAI_ERRATA_FIX_LISTEN_ONLY_DOM` off, which the board's `sdkconfig.board` fixes on: R29 fails on it in `build` only. |
| board `release_pass` to `flash_larger` (9 cases) | `0df0757` | PASS | As in the R21 entry above, on the board the build downloaded. |
| board `s3_pass` | `ccdd525` | PASS | The minimal project on esp32s3, its board `vtrux/s3fx` from git: exit 0, prefix `xtensa-esp32s3-elf-`. |
| board `s3_pass`, R17 scope | `ccdd525` | **FAIL** | `scope --release` on that build: exit 1, F2 alone. |
| board `s3_board_c3`, `s3_elf_reader` | `ccdd525` | PASS | The board's target `esp32c3`: exit 1, naming the target; R20's ELF reader against `xtensa-esp32s3-elf-nm`. |
| pins `release_pass` | `ccdd525` | PASS | Exit 0, `PASS R30`, nothing named. At `0df0757` it failed on the harness's CRLF fault (above). |
| pins `unlisted`, `listed_absent`, `listed_twice`, `no_list`, `new_reader` | `0df0757` | PASS | Exit 1 naming `main/main.c`; cannot read, three ways; naming `main/can.c` and not `main/main.c`. |

| Req | Result | What was tested |
|---|---|---|
| R17 on a real release build | **FAIL** | F1 and F2, on the fixture and on gen-inhibit's own bench build. The planted cases are each named, both ways. |
| R17, git components | **FAIL** | F2. The planted git cases are VOID until a clean build passes. |
| R18, by folder | PASS | On real release builds that take `wican_v3`. |
| R29, by folder and choices | PASS | On real release builds that take `wican_v3`. |
| R30 | PASS | On real release builds compiling `board_pins.h` from the component. |
| R21, the board the build downloaded | PASS | Both targets. |

## R17 fixed: the pins of git components, git's folder and annotated tags, on real builds (gen-inhibit step 1a, 2026-10-28)

**SPEC:** shared/main `b21cb87`. R17 as ruled 2026-10-28 (user, in the gen-inhibit reviewer's
session), on top of the git clause of the 2026-10-27 entry above: any file in the git folder of the
repository holding the project root counts as in scope, both the worktree's own (`--git-dir`) and
the clone's (`--git-common-dir`), other worktrees' records included, while another worktree's
checked-out files do not (`642b4ae`, which replaced `d23964b`'s narrower reading: `HEAD` and the
branch file it names); and the lock records what git resolved, the commit or an annotated tag
naming it, whose commit must be the one the pinned tag names (`b21cb87`, merging
`shared/tools/r17-annotated-tag` `642b4ae..b61c859`). **Check:** `vtrux_checks/scope.py` on
`shared/tools/scope-git-pin` at `af899da` (from shared/main `a2887ab`, merging `d23964b`,
`642b4ae` and `b21cb87` as they were ruled), three changes written from SPEC:
- `7960cd7` (F2 of the entry above): the components the build used were keyed by name, so the
  bootloader build's own `main` (IDF's bootloader subproject) replaced the project's `main/`, and
  the pin in its `idf_component.yml` was never read. They are now keyed by build folder and name.
- `475f4cc` (F1): the files under the repository's two git folders, as `git rev-parse` names them
  from the project root, are in scope. (`23b57b1` accepted `d23964b`'s two files only; superseded
  by the ruling before its run finished.)
- `af899da`: the lock's `version`, when a full hash, is followed to its commit by `git rev-parse
  <hash>^{commit}` from the project root, through any tags of tags, and that commit is compared with
  the pinned tag's. Anything else in the lock (a short hash, a branch's name, a tree, an unknown
  object) fails.

**Unit tests:** `test_scope.py`, 18 tests at `7960cd7`, 21 at `475f4cc`, 23 at `af899da`. Each
step's new tests fail on the step before's `scope.py`: 1 FAIL at `7960cd7`; 2 FAIL and 1 ERROR at
`23b57b1`; 5 ERRORs at `475f4cc` (the reader the test calls is missing, so the mutant ignoring the
git folders is the substantive one); 2 FAIL and 2 ERROR at `af899da`. The annotated-tag reader is
tested on a real repository pinned at an annotated and at a lightweight tag: the commit, the pinned
tag, another annotated tag on the same commit, a tag of a tag and the hash in upper case pass;
another commit, an annotated tag or a tag of a tag on it, a branch's name, a short hash, a tree, an
unknown hash and no version fail. 16 mutants of `scope.py` (3 for the pins, 7 for the git folders,
6 for the lock's hash) each fail them. Every unit test file of `vtrux_checks` passes at each step
(191 tests at `af899da`). **The real tree:** R17 on the tester's bench build of gen-inhibit
`b6bee3f` passes at `475f4cc` and at `af899da` (lock commit `77e114f`, the real tag being
lightweight), where it failed on F1 and F2 at `77e114f`.

**Cases:** `verification/scope_cases.py` at `af899da` gains `detached_worktree` (`475f4cc`: a bench
build in a detached linked worktree of the fixture's clone, which reads the clone's `HEAD`; VOID
unless that is shown) and three annotated-tag cases (`af899da`), the lock's board version replaced
by the hash of an annotated tag made in the project's repository and shown by its chain of objects
read with `git cat-file`: `git_lock_tag_other` on the tag commit's parent fails both ways naming
the board's folder; `git_lock_tag_same` (another tag on the tag's commit) and `git_lock_tag_of_tag`
pass both. `git_case` now takes the outcome wanted. `--self-test` passes, and covers the new cases'
changes, wanted outcome and undoing (lock restored, tags deleted); 6 mutants of those cases each fail
it. The real lightweight tag is `clean_release`'s pass; the annotated one is `board_cases`'
`s3_pass`, whose esp32s3 board repository is tagged with `git tag -a`.

**Builds:** release builds by the tooling at `af899da` of `release_cases.py`'s fixture made from
gen-inhibit `8b7558e` (`gen-inhibit/main`, which holds `b6bee3f`), and `board_cases.py`'s esp32s3
minimal project, `--board ../shared-2026.10.27/shared/boards/wican_v3`. **Host:** NICKY-XPS, IDF
v5.4.1 with its Python 3.11.9, Idle priority and `--jobs 1`; bench lock free, "No builds" clear and
no simulator running at the start. **Run files**, each with a `SHA256SUMS` hashing every file in
it, under `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/`:
- `c1a_realbuild_af899da_b_20261028/` (`e6bef5b0...32dd`): the run this entry rests on, 20:28:19 to
  21:36:53, on a fresh root. `s3_lock_tag_chain.txt` shows `s3_pass`'s lock recording the annotated
  tag's own hash, `e0bc87d...`, the one that failed at `475f4cc` below.
- `c1a_realbuild_af899da_20261028/` (`63db65c9...266b`): the first run at `af899da`, 20:02:59 to
  20:25:17, stopped by the harness's own guard when the interposer's emulated suite started
  (`version_edited` and `s3_pass` STOPPED, naming the running simulators). Not evidence.
- `r17_annotated_af899da_20261028/` (`ba449853...371b`), `f1_gitfolder_475f4cc_20261028/`
  (`1009ab58...ea43`), `f1_gitrecord_23b57b1_20261028/` (`360f5a1d...1f04`) and
  `f2_scopepin_7960cd7_20261028/` (`c056eb1f...277a`): each
  step's old-code and fixed unit tests, mutants, all `vtrux_checks` tests and the real tree; the
  `23b57b1` folder also holds the configure of a detached worktree behind the git-folder ruling.
- `c1a_realbuild_475f4cc_20261028/` (`87dd9df6...a771`): the run at `475f4cc`, 15:11:58 to 16:19:29: release
  2/2, scope 22/22 and `s3_pass` PASS, and R17 on `s3_pass` FAIL, `dependencies.lock records commit
  e0bc87d..., but tag 2026.10.28 names 9a4fbc4...` (`board_s3_pass.log`): IDF v5.4.1's component
  manager writes an annotated tag's own hash in the lock, the observation behind `b21cb87`. Before
  `7960cd7` the pin was not read and this was hidden behind F2.
- `c1a_realbuild_23b57b1_20261028/` (`a7fe049a...cff7`): the run at `23b57b1`, stopped in `shared_override`
  when the git-folder ruling superseded it. Not evidence.

| Case | Result | What was tested |
|---|---|---|
| `release_cases` `pre_unset_ri`, `a_release` | PASS | The fixture's release build: refused with `REVERSE_IT_DIR` unset; built and recorded (record `b4e8fa6350185bc4`). |
| scope `clean_release` | PASS | The fixture's clean release build, `--release` and `--bench`: exit 0, F1 and F2 no longer reported. |
| scope `version_edited`, `managed_unlisted` | PASS | Exit 1 both ways, naming `espressif__mdns` and the planted component. |
| scope `git_lock_commit`, `git_tag_absent`, `git_tag_branch`, `git_own_version` | PASS | Exit 1 both ways, naming `managed_components/vtrux__wican_v3:`; judged, not VOID, `clean_release` having passed. |
| scope `git_lock_tag_other` | PASS | An annotated tag on the tag commit's parent in the lock: exit 1 both ways, naming `managed_components/vtrux__wican_v3:`. |
| scope `git_lock_tag_same`, `git_lock_tag_of_tag` | PASS | Another annotated tag on the tag's commit, and a tag of such a tag: exit 0 both ways. |
| scope `outside_header`, `other_override` | PASS | Exit 1 both ways, naming the planted file. |
| scope `detached_worktree` | PASS | A bench build in a detached linked worktree of the fixture's clone, shown to read the clone's `HEAD`: exit 0 both ways. |
| scope `shared_override` | PASS | `--release` exit 1 naming the override; `--bench` exit 0, as R17 allows a bench build. |
| board `s3_pass` | PASS | The minimal project on esp32s3, its board `vtrux/s3fx` from git at an annotated tag: exit 0, prefix `xtensa-esp32s3-elf-`. |
| board `s3_pass`, R17 scope | PASS | `scope --release` on that build, whose lock records the annotated tag's own hash: exit 0. |

| Req | Result | What was tested |
|---|---|---|
| R17 on a real release build | PASS | Supersedes the entry above's FAIL: F1 and F2 no longer reported, on the fixture and on gen-inhibit's own bench build; the planted cases each named, both ways. |
| R17, git components | PASS | Supersedes the entry above's FAIL: pinned at a lightweight tag (the real one) and at an annotated tag (`s3_pass`); the planted git cases judged, not VOID. |

## R30, a shared component's own list (gen-inhibit step 1a, the stock code's move, 2026-10-29)

**SPEC:** shared/main `1b4ab4d`: R30 amended (no object compiled from another shared component
read the board's `include/board_pins.h` unless its source is on that component's own list, read
from `managed_components/`), with `../boards/SPEC.md` B6 (amended) and `../wican/SPEC.md` W10,
approved by the user 2026-10-29. **Check:** `vtrux_checks/pins.py` on `shared/tools/wican-pin-list`
at `c0c57c0` (from `1b4ab4d`), written from SPEC. The list's form was agreed with the gen-inhibit
implementor, who writes it in `vtrux/wican` (2026-10-29): `pins.json` in the component's folder,
`{"files_using_pins": [...]}`, paths from that folder with `/`; a component without one has an
empty list. A shared component is a `dependencies.lock` entry from git whose path is exactly
`shared/<name>` (`shared/boards/*` stays the board); one from a local folder whose path ends in
`shared/<name>`, or one not in `managed_components/`, is refused. **Unit tests:** `test_pins.py`
20 (11 before), each new rule shown failing on a planted input and passing on a faithful one: a
component's reader off its list, in the app's and the bootloader's folder; no `pins.json`, which
fails a reader and passes when none reads; the project's list not covering a component's file
nor a component's list a project file; a listed file reading none, printed; a bad `pins.json`
(10 forms); a component from a local folder, by `path` or `override_path`, or not downloaded; the
board not taken for a shared component; a managed component that is not shared not judged. The
11 tests from before still pass, and every `vtrux_checks` test file passes (200 tests). 10
mutants of the change each fail them; so does `1b4ab4d`'s `pins.py`, though every test errors
there on the new names, so the mutants are the substantive kill. **Cases:** `verification/pins_cases.py`
at `c0c57c0` gains `comp_unlisted`, `comp_no_list`, `comp_listed_absent` and `comp_listed_twice`,
on `release_pass`'s build with the component's `pins.json` changed and put back; each is VOID
unless a shared component's file reads pins. What they expect comes from ninja's `-t deps` and
`-t query`, and the shared components from the lock's text, not the check's readers. `unlisted`
and `listed_twice` take the project's first listed reader instead of `main/main.c`, which
leaves the project's list at the move. `--self-test` passes, and covers the new readers, and the
list cases' changes, expected failures or marks, VOIDs and undoing on a small project. 18
mutants of the cases each fail it. **Host:** NICKY-XPS, Python 3.14. **Run files:**
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/r30_complist_c0c57c0_20261029/`
(`SHA256SUMS` `7520c618...1d7c`, 8 files): the unit tests, every `vtrux_checks` test file, the
self-test, both mutant runs and their scripts.

| Req | Result | What was tested |
|---|---|---|
| R30, a shared component's own list | **unit tests only** | Not yet on a real build: that needs a project taking `vtrux/wican` with its `pins.json` (gen-inhibit's step 1a C). Its real-build cases above run in gen-inhibit's 1a acceptance run. |
