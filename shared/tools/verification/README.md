# shared/tools/verification

**Owner:** tester (gen-inhibit; C6f, reviewer ruling 2026-10-24).

The tester's verification of the build tooling against `../SPEC.md`. The
expected values come from SPEC and from ESP-IDF's published app image format,
never from `vtrux_build`'s source: `independent.py` is the tester's own reading,
and each check compares the library's answer with it, or tests a property SPEC
states. The build checks (R17, R18, R20, R21) are not here; they are in
`../vtrux_checks/`, and their real-build cases are here. R19 is a test of the
tooling, not a check, and is here (`repro_cases.py`).

Each script has a `--self-test` that runs the same checks against fake
libraries: each fake breaks one rule and must be caught, and one fake breaks
none and must pass. A check that no fake can fail is not a check.

## Files

| File | What |
|---|---|
| `independent.py` | The tester's reading of R1 and R2: the image layout, the ranges R2 blanks, the code hash and the image hash. It also builds valid synthetic images and changes one field or byte while keeping the image valid. `scratch(name)` gives a run its own folder in the worktree's `scratch/shared-tools-verification/`. |
| `verify_image.py` | R1, R2 and R15 (the library side). On synthetic images: the code hash and its blanked ranges, which fields keep it and which change it, refusal of a truncated, padded or badly sealed image, and that reading never patches the file. With `--same`, `--different` and `--expect-code`, on real images: the same checks, plus one shared code hash across builds of the same files. Where an `.elf` sits beside a `.bin`, it also checks R1 against the ELF's sha256. 8 fakes. |
| `verify_digest.py` | R7, by its properties only, since SPEC does not define the serialisation. Pairs that R7 says cover the same thing give one digest: a copy elsewhere, an mtime change, `*.md` added, a root `.gitignore`, an empty folder, a `.git` above. Pairs that differ in what it covers give two: a content byte, a rename or move, an empty file, `__pycache__`, CRLF, `README.md.txt`, `.markdown`, `README.MD`, a dotfile. A `.gitignore` in a subfolder is left out too. These are R7's edges as the user ruled them (2026-10-24, SPEC `904b98a`). `component_files` lists exactly what R7 covers. With `--commit`, the component checked out at a commit and exported with `git archive` give one digest. With `--expect-digest`, that digest must equal the other host OS's. 16 fakes. |
| `release_cases.py` | Real release builds: R5, R8-R11, R13, R14 with R3, R16 (`override_path`), R22. Cases include, for R8: staged, deleted and untracked files, and a committed sdkconfig the reconfigure rewrites. For R8's sdkconfig clause, the `sdkconfig` the build used (`project_description.json`'s `config_file`, the case's `SDKCONFIG` set in `CMakeLists.txt` before `project.cmake` is included): tracked but ignored by `.gitignore` or by `.git/info/exclude`, untracked and ignored, written by the reconfigure and ignored, and outside the project in a folder whose name starts like it, each refused; a committed one elsewhere in the project, built and recorded. Each confirms `config_file` names the file it planted, and asks git itself whether the file is tracked and ignored. For R9: ccache launchers and ccache masquerading as the compiler on PATH, as a copy (Windows), a symlink (Linux) or a hard link (either), judged by the compiler CMake chose and the compiles ccache cached in its own `CCACHE_DIR`. For R13: each record judged field by field, its lock hash against the lock's sha256 with line endings as LF; and the R22 project built with its committed lock on disk as CRLF and as LF, each record giving the committed lock's sha256. For R10: a project with no `dependencies.lock`. It clones gen-inhibit at `--base` into `--root` and adds a fixture commit: `vtrux_build.json`, reproducible build on, and the tracked files as `idf.py reconfigure` writes them. Branch `fx-r11` deletes the build's own `.bin`; branch `fx-r16` takes a shared component from a local folder. A minimal second project for R22 is IDF's `hello_world` on esp32c3. Records go to a stand-in `REVERSE_IT_DIR` under `--root`. Each case judges from outside: the exit code, the record folder before and after, the build folder and git. It never judges message wording. A case whose trigger did not happen is VOID. Before each case it checks the bench lock, the "No builds" line and running simulators, and stops if any is set. `--self-test` runs the R13 record judge, the second-build judge, the refusal verdict, the LF-normalised lock hash, the masquerade's evidence readers and the R8 sdkconfig helpers (on a small repo) against broken inputs. |
| `scope_cases.py` | R17's check (`../vtrux_checks/scope.py`) on real builds of `release_cases.py`'s fixture: a clean release build passes `--release` and `--bench`; the same tree with a managed component's manifest version changed, or with a managed component the lock does not list, fails; a header included from outside the project root fails both; a shared component taken by `override_path` from a `../shared-<tag>/shared/` folder passes `--bench` and fails `--release`; the same from a folder that is not a `shared/` fails both. Each case confirms its trigger without the check's reader (ninja's `-t deps`, or `build.ninja`'s text), and judges the check's exit code and whether its output names the planted file. `--self-test` runs that judge against broken inputs. |
| `options_cases.py` | R18's check (`../vtrux_checks/options.py`) on real release builds of `release_cases.py`'s fixture, each with one sdkconfig change committed on top (and what the reconfigure then rewrites), judged against `--profile` (a project's `vtrux_checks.json`, copied in after the build): the fixture as it is passes; the same build with the bootloader's `config/sdkconfig.json` moved aside fails as unreadable, naming the folder; `CONFIG_APP_REPRODUCIBLE_BUILD` off, `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` off and `CONFIG_ESP_TASK_WDT_PANIC` on each fail on that option; `CONFIG_LOG_COLORS` off, which the profile does not name, passes. The failures the check must name, folder by folder, come from each build folder's `config/sdkconfig.h`, not the check's reader; a case whose change does not show there is VOID. `--self-test` runs its judges against broken inputs. |
| `switches_cases.py` | R20's check (`../vtrux_checks/switches.py`) on real builds of a fixture made from `--base`, a gen-inhibit commit whose `vtrux_checks.json` holds its `build_switches` table and whose `build_output.py` writes `vtrux_project_rows.json` (made here if `--root` has none). A release build passes; the same build fails naming a planted table entry nothing tests (stale), a table planted in `managed_components/espressif__mdns/` missing one symbol the build tests there (found with a probe table first, since the check lists a component's symbols only when it has a table) and listing one it does not (both named under the component), and `espressif/mdns` named in `shared_components` with no table. A release build of a commit adding a switch to `main/main.c` and to `main/CMakeLists.txt`, one to `main/ftp.h` (not compiled), and a variable the CMake file sets before testing it fails naming exactly the first two. A build with each test-only or stock-off switch on (`idf.py -D`) fails on that switch's refusal rules, the R20 "shown failing" evidence; other entries may be named too, as refusals only (R20 reading (a), gen-inhibit reviewer, 2026-10-24), and the detail names them. AutoPID's ON build has MQTT and ELM327 too, and its control (those two) fails on them and must not name AutoPID. The project's harness (`build_output.py --bin`) writes the rows file before each check. Each case confirms its trigger without the check's reader (ninja's deps log, `CMakeCache.txt`, the rows file) and judges the exit code and the failures named, (scope, symbol, kind). `--self-test` runs that judge, the unlisted-symbol reader and the cache reader against broken inputs. |
| `repro_cases.py` | R19, the reproducible test. Each case clones one commit into two new folders of different length and depth under `--root/r19/`, runs the release build script in each, one after the other, each with its own stand-in record folder, and compares the image hashes. Each build's image hash is read from its record, by `vtrux_build.image` and by `independent.py`, and the three must agree. Cases that must meet R19: `release_cases.py`'s fixture and its minimal project, each with reproducible build on; the fixture with reproducible build off (gen-inhibit as it is until port step 0b, its own R19 run); the minimal project printing `__DATE__` and `__TIME__` (the release build's fixed `SOURCE_DATE_EPOCH`, R9). The minimal project printing a define that holds `CMAKE_BINARY_DIR` must not, which shows the test can fail. A case is VOID unless both clones are clean at the same commit with no build folder, and its trigger shows in each build (the setting in `config/sdkconfig.h`, the marker string in the `.bin`). The driver log records what R19 says to rerun on: the tooling commit and `vtrux_build`'s digest, ESP-IDF and its Python environment (its prefix, esptool's version, a sha256 of `pip freeze`), gcc, CMake, Ninja. `--self-test` runs its judges and premise check against broken inputs and its hash readers on a synthetic image. |

## Running

From this folder, with the Python command in `local-paths.md` and `-B` (a
`__pycache__` in `shared/tools/` changes its digest):

```
py -3.14 -B verify_image.py --self-test
py -3.14 -B verify_image.py [--same A.bin B.bin ...] [--different C.bin] [--expect-code PREFIX]
py -3.14 -B verify_digest.py --self-test
py -3.14 -B verify_digest.py [--commit <commit> [--component shared/tools]]
py -3.14 -B release_cases.py --self-test
py -3.14 -B scope_cases.py --self-test
py -3.14 -B options_cases.py --self-test
py -3.14 -B repro_cases.py --self-test
py -3.14 -B switches_cases.py --self-test
```

`scope_cases.py`, `options_cases.py` and `repro_cases.py` run like `release_cases.py`, with the
same `--root` (the fixture must already be there) and `--out`; `switches_cases.py` takes `--base`
and makes its fixture in a `--root` of its own; `options_cases.py` also takes
`--profile`, and `repro_cases.py` takes `--keep` to keep each case's two builds.

`release_cases.py` runs from an exported ESP-IDF shell, with its Python:
`python -B release_cases.py --root <short folder in scratch/> --out <run folder>
--base <gen-inhibit commit> [--cases a,b] [--jobs N] [--fixture <repo>]`.
Set `REAL_REVERSE_IT_DIR` to the real reverse-it folder so it can read the "No
builds" line. Keep `--root` short: the IDF build paths under it are long.

Exit 0 is all ok, 1 is a FAIL. The run records go to the gen-inhibit harness
folder in reverse-it (`projects/vtrux/notes/artifacts/gen-inhibit/runs/`), and
the results to `../VERIFICATION.md`.
