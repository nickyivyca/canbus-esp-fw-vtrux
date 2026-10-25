# shared/tools/vtrux_checks

**Owner:** tester (gen-inhibit; C6f, gen-inhibit reviewer ruling 2026-10-23).

The build checks of `../SPEC.md` section 4, R17 to R21, as they land. Each reads a
finished build through `vtrux_build`'s interface (`../README.md`, "The interface")
and changes nothing. A check's per-project data, where it needs any, is in
`vtrux_checks.json` in the project's root (R18, R20, R21; R17 needs none).

## Running

From the project's root, with `shared/tools` on the path and `-B` (a `__pycache__`
in `shared/tools/` changes its digest):

```
python -B -m vtrux_checks scope --build build --release    # R17, a release build
python -B -m vtrux_checks scope --build build --bench      # R17, a bench or dev build
python -B -m vtrux_checks options --build build            # R18, a finished release build only
python -B -m vtrux_checks switches --build build           # R20, a finished release build only
python -B -m vtrux_checks board --build build              # R21, a finished release build only
```

Any session may run them; they change nothing. With ESP-IDF exported, from the
project's root, e.g. for gen-inhibit: `$env:PYTHONPATH = "<worktree>\shared\tools"` (or
`PYTHONPATH=<worktree>/shared/tools` in bash), then the line above.

Exit 0 is a pass, 1 a failure or an unreadable build, 2 a usage error. The output
names the folders it allowed and, for a failure, each file or component at fault
(the first 20 of each kind). `options` prints every option the profile names, its
profile value and spec line, and its value in each build folder. `switches` prints,
for the project and each managed component with a table, every symbol tested or
listed, its class, and where it is tested (the first 3 places).

## The checks

| Check | SPEC | What it reads | Fails when |
|---|---|---|---|
| `scope` | R17 (shared/main `5ec6f53`) | `BuildDir.files_read()` in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path` and `idf_path`; each folder's `CMakeFiles/<version>/CMake<LANG>Compiler.cmake` and `CMakeCache.txt` `CMAKE_ROOT`; `git worktree list` from the project root; `dependencies.lock` and each `managed_components/*/idf_component.yml` | a file read from outside the project root, `IDF_PATH`, the chosen toolchain's install folder (the folder above its compiler's `bin/`) and `CMAKE_ROOT`; for `--release`, a file read from a `shared/` folder of any worktree of this repo or of a `../shared-<tag>/` checkout beside the worktree root, even inside the project root; a managed component whose own manifest's `version` is not the lock's, or that the lock does not list; records that name no files, no compiler or no `CMAKE_ROOT` |
| `options` | R18 (shared/main `b5d5477`) | `config/sdkconfig.json` (`BuildDir.options()`) in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path`; `vtrux_checks.json`'s `release_profile` in the project's root | an option the profile names is not at the profile's value in some build folder: `"unset"` wants it absent or `false`, any other value wants it present with that value and JSON type (`true` is not `1`); options the profile does not name are free. Also a build folder without `config/sdkconfig.json`, and a profile that is missing or empty, names something not `CONFIG_...`, has an entry without exactly `value` and `spec`, an empty `spec`, or a value that is not `true`, an integer, a string or `"unset"` (`false` is refused: write `"unset"`) |
| `switches` | R20 (shared/main `d4c16e9`) | in every CMake build folder under `build/`: ninja's deps log (`BuildDir.ninja_deps()`, every file a compile read) and the `CMakeLists.txt` and `*.cmake` inputs of the `RERUN_CMAKE` edge; `project_description.json`'s `project_path`, `app_bin` and `app_elf`; the recorded cmake's `--help-variable-list` (`CMAKE_COMMAND` in `CMakeCache.txt`); `vtrux_checks.json`'s `build_switches` in the project's root and in each `managed_components/<name>/`; `vtrux_build.json`'s `shared_components`; the `.bin` (its descriptor's `project_name`, R1's image hash) and the ELF's `.symtab`; `vtrux_project_rows.json` in `build/` when a table names a project row | for the project (its files: under the project root, minus `build/` and `managed_components/`) and for each managed component with a table (its files: under its folder): a symbol tested and not listed, or listed and no longer tested; a refusal rule that does not pass. Also a component named in `shared_components` with no table; a table that is missing (the project's) or malformed; records with no C file from the project root, without the project's top-level `CMakeLists.txt`, or older than a CMake file they name; an ELF with no symbol table |

What counts as tested, in `switches.py`'s docstring: the C scan is textual (every identifier in an
`#if` or `#elif`, `CONFIG_*` left out), a header guard is left out only in its exact shape, and a
CMake variable is a build option unless the file set it itself at file scope before the test, or it
is a CMake built-in (gen-inhibit spec 12.3 item 3's readings, carried over, user 2026-10-24).
A compile definition the project's CMake sets is not listed unless the project's own compiled
source tests it (user, 2026-10-24, option b): the C scan finds it there.

Run `options`, `switches` and `board` on release builds only (SPEC R18, R20, R21): bench and dev
builds are not checked, and the checks do not tell them apart.

### `vtrux_checks.json`

In the project's root, the tester's. Each check reads its own key and ignores the
rest.

```
{"release_profile": {
    "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE": {"value": true, "spec": "<the project spec line it comes from>"},
    "CONFIG_SECURE_BOOT": {"value": "unset", "spec": "..."}},
 "build_switches": {
    "GEN_INHIBIT_AUTOARM": {"class": "shipping variant", "reason": "..."},
    "DIAG_FW_VERSION": {"class": "not a switch", "kind": "default value", "reason": "..."},
    "GI_INSTRUMENT_RXQ": {"class": "test-only", "reason": "...",
                          "refusal": [{"bin_strings_absent": ["..."]}, {"elf_symbols_absent": ["..."]}]},
    "WICAN_FEATURE_REALDASH": {"class": "stock feature off in this product", "reason": "...",
                               "refusal": [{"project_row": "no boot-time bus enable in app_main"}]}},
 "board": {"target": "esp32c3", "partition_table": "test/build_checks/reference/stock-v4.13-partition-table.bin",
           "flash_size": "4MB"}}
```

`build_switches` (R20): the classes are `shipping variant`, `test-only`, `stock feature off in
this product` and `not a switch`; every entry has a `reason`, a `not a switch` entry a `kind`,
and a test-only or stock-off entry a non-empty `refusal`, whose rules are `bin_strings_absent`,
`elf_symbols_absent`, `elf_symbol_prefixes_absent` (each a list), `project_name_token_absent`
and `project_row` (each a string). A project row's result is read from `build/vtrux_project_rows.json`,
which the project's harness writes for the image it judged: `{"image_hash": "<R1 hash>", "rows":
{"<row>": "PASS" | "FAIL"}}`. The same key in a shared component's own `vtrux_checks.json` is
that component's table (SPEC R20), applied wherever the release build uses it. Other keys,
such as a project's test triggers, are the project's own and ignored here.

`board` (R21, gen-inhibit reviewer's readings of 2026-10-24): the board's `target`, an ESP-IDF
target name, and `partition_table`, the path from the project root to the board's reference
partition-table `.bin`, committed (git holds the reference; no hash is stored beside it), and
`flash_size`, the board's flash size as ESP-IDF names it (`4MB`; SPEC R21 as worded in a5e7c46,
user 2026-10-24). The application slot is the smallest app partition in that table, and the
tool prefix is the one ESP-IDF's `tools/cmake/toolchain-<target>.cmake` sets; neither is written
here. The flash size is compared with the field in the app's and the bootloader's image
headers, whose values, like the header's magic byte, come from the build's ESP-IDF
(`components/bootloader_support/include/esp_app_format.h`). This inline form
is for a board only this project builds for (R22: the WiCAN). A board named by a string, from a
shared board component pinned in `dependencies.lock` and found as `boards/<name>/` under
`managed_components/`, is **not built yet**: no board component exists until the interposer's
port step 2a, and the check refuses the form, naming that.

## Files

| File | What |
|---|---|
| `__init__.py`, `__main__.py`, `cli.py` | the package and its command line |
| `scope.py` | R17 |
| `options.py` | R18 |
| `switches.py` | R20 |
| `board.py` | R21; `tool()` is where a check names a tool, from the board's target |
| `parttable.py` | ESP-IDF's binary partition table, read from its published format, for R21 |
| `tests/test_scope.py` | R17's rules, each shown failing on a planted input and passing on a faithful one; its readers on small files. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_scope.py` |
| `tests/test_options.py` | R18's rules, each shown failing on a planted input and passing on a faithful one; the profile's reader; the command line on a small build tree. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_options.py` |
| `tests/test_switches.py` | R20: the C and CMake scans on the ways a switch could hide; the table's reader; the ELF reader on synthetic ELFs (and against `nm` on a real one, given `VTRUX_TEST_ELF` and `VTRUX_TEST_NM`); each refusal rule and the project rows file; the judge; the build reader and the command line on a small configured build. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_switches.py` |
| `tests/test_board.py` | R21: the partition-table reader on built and broken tables; the board data's reader; the slot; the tool prefix, and the image header's flash size values and magic, from a fake ESP-IDF (and the real one, given `IDF_PATH`); the image header's reader; that no check names a chip's tool; the build reader and the command line on a small build folder. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_board.py` |

The checks on real builds, passing and failing, are in `../verification/scope_cases.py`
(R17), `../verification/options_cases.py` (R18), `../verification/switches_cases.py` (R20) and
`../verification/board_cases.py` (R21).
