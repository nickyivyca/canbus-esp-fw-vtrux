# shared/tools/vtrux_checks

**Owner:** tester (gen-inhibit; C6f, gen-inhibit reviewer ruling 2026-10-23).

The build checks of `../SPEC.md` section 4, R17 to R21, R29 and R30, as they land. Each
reads a finished build through `vtrux_build`'s interface (`../README.md`, "The interface")
and changes nothing. A check's per-project data, where it needs any, is in
`vtrux_checks.json` in the project's root (R18, R20, R30; R17 needs none). The board's data
(R21, R29, R30) is in the board component the project depends on: `board.json` and
`sdkconfig.board` in its folder in `managed_components/` (`../../boards/SPEC.md` B2-B4).

## Running

From the project's root, with `shared/tools` on the path and `-B` (a `__pycache__`
in `shared/tools/` changes its digest):

```
python -B -m vtrux_checks scope --build build --release    # R17, a release build
python -B -m vtrux_checks scope --build build --bench      # R17, a bench or dev build
python -B -m vtrux_checks options --build build            # R18, R26-R29, a finished release build only
python -B -m vtrux_checks switches --build build           # R20, a finished release build only
python -B -m vtrux_checks board --build build              # R21, a finished release build only
python -B -m vtrux_checks pins --build build               # R30, a finished release build only
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
| `scope` | R17 (shared/main `5ec6f53`; git components, shared/boards/spec-folders `6a00663`; the repository's git folder, shared/main `642b4ae`; annotated tags in the lock, shared/main `b21cb87`) | `BuildDir.files_read()` in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path` and `idf_path`; each folder's `CMakeFiles/<version>/CMake<LANG>Compiler.cmake` and `CMakeCache.txt` `CMAKE_ROOT`; `git worktree list` from the project root; `git rev-parse --git-dir --git-common-dir` from the project root; `dependencies.lock` and each `managed_components/*/idf_component.yml`; for a component the lock says came from git, the `idf_component.yml` of the project's own components (`project_description.json`'s components under the project root, not in `managed_components/`, in every build folder) and the tag it pins, resolved by `git rev-parse refs/tags/<tag>^{commit}` from the project root; the lock's `version`, when a full hash, followed to its commit by `git rev-parse <hash>^{commit}` from the project root | a file read from outside the project root, `IDF_PATH`, the chosen toolchain's install folder (the folder above its compiler's `bin/`) and `CMAKE_ROOT`, other than a file in the git folder of the repository that holds the project root: the `--git-dir` and the `--git-common-dir` (for a linked worktree its own and the clone's, which holds the other worktrees' records under `worktrees/`; another repository's git folder, and the checked-out files of the clone or another worktree, fail); for `--release`, a file read from a `shared/` folder of any worktree of this repo or of a `../shared-<tag>/` checkout beside the worktree root, even inside the project root; a managed component whose own manifest's `version` is not the lock's, or that the lock does not list; for one taken from git (the lock's `version` is then the commit it resolved, or an annotated tag naming it, through any tags of tags): its own manifest having a `version`, no manifest of the project pinning it, the project's manifests pinning it at different versions, the version pinned not being a tag in the project's repository (a branch or a commit is not), or the tag naming another commit than the lock's (a lock `version` that is not a full hash, or names no commit in the project's repository, fails); records that name no files, no compiler or no `CMAKE_ROOT` |
| `options` | R18 (shared/main `b5d5477`; folders, shared/boards/spec-folders `6a00663`) | `config/sdkconfig.json` (`BuildDir.options()`) in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path`; `vtrux_checks.json`'s `release_profile` in the project's root | an option the profile names is not at the profile's value: `"unset"` wants it absent or `false` in every build folder; any other value wants it in the app's build folder (`build/`) with that value and JSON type (`true` is not `1`), and in every other folder that has the option at all (a folder holds only its own components' options: the bootloader's lacks many of the app's, `CONFIG_BT_ENABLED` among them); options the profile does not name are free. Also a build folder without `config/sdkconfig.json`, and a profile that is missing or empty, names something not `CONFIG_...`, has an entry without exactly `value` and `spec`, an empty `spec`, or a value that is not `true`, an integer, a string or `"unset"` (`false` is refused: write `"unset"`). **R26-R28** (shared/main `5151e77`; readings ruled by the gen-inhibit reviewer, 2026-10-25), whatever the profile names, judged in every build folder, absent included: R26, `CONFIG_SECURE_BOOT`, `CONFIG_SECURE_FLASH_ENC_ENABLED`, `CONFIG_FLASH_ENCRYPTION_ENABLED` and `CONFIG_EFUSE_VIRTUAL` not "unset" in some build folder; R27, a profile without `CONFIG_FREERTOS_HZ`; R28, `CONFIG_LOG_DEFAULT_LEVEL` or `CONFIG_LOG_MAXIMUM_LEVEL` not 0 or `CONFIG_BOOTLOADER_LOG_LEVEL` not 1 in some build folder, `CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL` not 0 where a folder names it, the Arduino core's two signals disagreeing (a folder naming `ARDUHAL_LOG_DEFAULT_LEVEL`, an Arduino component in `project_description.json`'s components), and a `-DCORE_DEBUG_LEVEL` or `-DLOG_LOCAL_LEVEL` in any build folder's `compile_commands.json`. Also a build folder without `project_description.json`'s components or `compile_commands.json`, and a profile naming one of R26's or R28's options at another value or `CONFIG_FREERTOS_HZ` as anything but an integer. **R29** (shared/main `2f72a20`; folders and choices, shared/boards/spec-folders `6a00663`), from the board (`boarddata.py`): an option the board's `sdkconfig.board` names not at the board's value, folder by folder as R18 judges; a setting `board.json` lists as required of the project that the profile does not name (an option at any value, a choice with exactly one member `true`); a required option missing from the app's folder; a required choice none of whose members is in the app's folder; a folder that has any of a required choice's members without exactly one of them `true` (a stale member list). A member absent from a folder is not judged: a member whose Kconfig dependencies are not met is not written, such as the secondary console's USB-Serial/JTAG beside a USB-Serial/JTAG primary; and a board that cannot be found or read |
| `board` | R21 (shared/main `2f72a20`) | the project's board, found in `dependencies.lock` and read from `managed_components/` (`boarddata.py`): its `board.json`'s target, flash size and reference table; the build's partition table (`flasher_args.json`'s `partition-table`), app image (`app_bin`) and bootloader image (`flasher_args.json`'s `bootloader`); `config/sdkconfig.json`'s `IDF_TARGET`; ESP-IDF's `tools/cmake/toolchain-<target>.cmake` and `esp_app_format.h` (`project_description.json`'s `idf_path`) | not exactly one `shared/boards/*` component in `dependencies.lock` (a git source whose path is `shared/boards/<board>`, or a local one ending so), or it is not downloaded into `managed_components/`; the build's partition table not byte-identical to the board's reference; the image larger than the smallest app partition of the reference; the flash size in the app's or the bootloader's image header not the board's; the build's target not the board's; ESP-IDF naming no toolchain for the target or no flash size by the board's name |
| `pins` | R30 (shared/main `2f72a20`; a shared component's own list, shared/main `1b4ab4d`) | the board's `include/board_pins.h` in `managed_components/`; in every CMake build folder under `build/`, ninja's deps log and `build.ninja` (each object traced to its source, its build statement's first input); `vtrux_checks.json`'s `stock_files_using_pins`; `dependencies.lock`'s shared components (a git source whose path is `shared/<name>`) and each one's `pins.json` `files_using_pins` in its folder in `managed_components/` | an object compiled from the project root (minus `build/` and `managed_components/`) read the header and its source is not on the list; an object compiled from a shared component's folder read it and its source is not on that component's list; an object that read it has no build statement with an input; nothing at all read it (B6: the board's own `board.c` includes it, so its absence means the records do not show it); the list missing, not paths from the project root with `/`, naming one twice or naming no file; a component's `pins.json`, when there is one, not such a list from the component's folder; a shared component from a local folder or not in `managed_components/`. A listed file whose objects read no pins is printed, not failed |
| `switches` | R20 (shared/main `d4c16e9`) | in every CMake build folder under `build/`: ninja's deps log (`BuildDir.ninja_deps()`, every file a compile read) and the `CMakeLists.txt` and `*.cmake` inputs of the `RERUN_CMAKE` edge; `project_description.json`'s `project_path`, `app_bin` and `app_elf`; the recorded cmake's `--help-variable-list` (`CMAKE_COMMAND` in `CMakeCache.txt`); `vtrux_checks.json`'s `build_switches` in the project's root and in each `managed_components/<name>/`; `vtrux_build.json`'s `shared_components`; the `.bin` (its descriptor's `project_name`, R1's image hash) and the ELF's `.symtab`; `vtrux_project_rows.json` in `build/` when a table names a project row | for the project (its files: under the project root, minus `build/` and `managed_components/`) and for each managed component with a table (its files: under its folder): a symbol tested and not listed, or listed and no longer tested; a refusal rule that does not pass. Also a component named in `shared_components` with no table; a table that is missing (the project's) or malformed; records with no C file from the project root, without the project's top-level `CMakeLists.txt`, or older than a CMake file they name; an ELF with no symbol table |

What counts as tested, in `switches.py`'s docstring: the C scan is textual (every identifier in an
`#if` or `#elif`, `CONFIG_*` left out), a header guard is left out only in its exact shape, and a
CMake variable is a build option unless the file set it itself at file scope before the test, or it
is a CMake built-in (gen-inhibit spec 12.3 item 3's readings, carried over, user 2026-10-24).
A compile definition the project's CMake sets is not listed unless the project's own compiled
source tests it (user, 2026-10-24, option b): the C scan finds it there.

Run `options`, `switches`, `board` and `pins` on release builds only (SPEC R18, R20, R21, R29,
R30): bench and dev builds are not checked, and the checks do not tell them apart.

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
 "stock_files_using_pins": ["main/can.c"]}
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

`stock_files_using_pins` (R30): the project's stock files that still use pins, paths from the
project root with `/` (`../../boards/SPEC.md` B6). An empty list is a list; a missing key fails.

A shared component's own list (R30, `../../wican/SPEC.md` W10) is in the component, not here:
`pins.json` in its folder, `{"files_using_pins": ["wican.c"]}`, paths from the component's
folder with `/`, read from `managed_components/`. A component without `pins.json` has an empty
list. Its owner is the component's `README.md`'s to record (W11).

### The board's data

In the board component's folder, `shared/boards/<board>/`, which the checks read from
`managed_components/` (`boarddata.py`). `board.json` is the tester's (B7):

```
{"target": "esp32c3",
 "flash_size": "4MB",
 "partition_table": "stock-v4.13-partition-table.bin",
 "required": [{"setting": "the console",
               "choice": ["CONFIG_ESP_CONSOLE_UART_DEFAULT", "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG", ...]},
              {"setting": "Bluetooth on or off", "option": "CONFIG_BT_ENABLED"}],
 "hardware_name": "OBD"}
```

`target`, an ESP-IDF target name; `flash_size`, as ESP-IDF names it; `partition_table`, the
reference table's path inside the board's folder, committed (git holds it; no hash is stored
beside it, and no slot: R21's slot is the reference's smallest app partition); `required`, the
settings the board leaves to the project (B3.2), each a single option or a Kconfig choice listed
by its members on the board's target; `hardware_name` (B5). The tool prefix is the one ESP-IDF's
`tools/cmake/toolchain-<target>.cmake` sets (R21 readings of 2026-10-24), and the image header's
flash size values come from the build's ESP-IDF (`components/bootloader_support/include/esp_app_format.h`).
`sdkconfig.board`, the implementor's, holds the settings fixed by the board (B3.1) in ESP-IDF's
`sdkconfig` form, which R29 reads.

## Files

| File | What |
|---|---|
| `__init__.py`, `__main__.py`, `cli.py` | the package and its command line |
| `scope.py` | R17 |
| `options.py` | R18, R26-R29 |
| `switches.py` | R20 |
| `board.py` | R21; `tool()` is where a check names a tool, from the board's target |
| `boarddata.py` | the project's board: the one `shared/boards/*` entry in `dependencies.lock`, its folder in `managed_components/`, its `board.json` and `sdkconfig.board`; for R21, R29 and R30 |
| `pins.py` | R30 |
| `parttable.py` | ESP-IDF's binary partition table, read from its published format, for R21 |
| `tests/test_scope.py` | R17's rules, each shown failing on a planted input and passing on a faithful one, a git component's included; its readers on small files, the pins of git components on a small repository with a tag, the lock's hash followed to its commit there (the commit, the pinned annotated tag, another annotated tag on the same commit and a tag of a tag pass, pinned at an annotated or a lightweight tag; another commit, an annotated tag or a tag of a tag on another commit, a branch's name, a short hash, a tree and an unknown hash fail), read from the app's build folder beside a bootloader's with a `main` of its own (in either order), and the git folders on small repositories (a branch, a project in a subfolder, linked worktrees on a branch and detached, no repository), judged with what a detached linked worktree's build reads (its own `HEAD` and the clone's) and with files beside the git folders. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_scope.py` |
| `tests/test_options.py` | R18's and R26-R29's rules, each shown failing on a planted input and passing on a faithful one; the profile's reader; the command line on a small build tree. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_options.py` |
| `tests/test_switches.py` | R20: the C and CMake scans on the ways a switch could hide; the table's reader; the ELF reader on synthetic ELFs (and against `nm` on a real one, given `VTRUX_TEST_ELF` and `VTRUX_TEST_NM`); each refusal rule and the project rows file; the judge; the build reader and the command line on a small configured build. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_switches.py` |
| `tests/test_boarddata.py` | the board found from `dependencies.lock` (one, none, two, local, not downloaded, the path forms), `board.json`'s and `sdkconfig.board`'s readers on faithful and broken files, and a real board's folder given `VTRUX_TEST_BOARD`. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_boarddata.py` |
| `tests/test_pins.py` | R30 on a small build tree with ninja's own records: each rule failing on a planted read and passing on a faithful one, the bootloader's folder included, the list's reader; a shared component's own list (a reader off it, no `pins.json`, the two lists not covering each other, a listed file reading none, the bootloader's folder, a bad list, a component from a local folder or not downloaded), the board not taken for a shared component, and a managed component that is not shared not judged. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_pins.py` |
| `tests/boardkit.py` | a board for the tests: a `dependencies.lock` naming it and its folder in `managed_components/` |
| `tests/test_board.py` | R21: the partition-table reader on built and broken tables; the board read from the board component; the slot; the tool prefix, and the image header's flash size values and magic, from a fake ESP-IDF (and the real one, given `IDF_PATH`); the image header's reader; that no check names a chip's tool; the build reader and the command line on a small build folder. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_board.py` |

The checks on real builds, passing and failing, are in `../verification/scope_cases.py`
(R17), `../verification/options_cases.py` (R18), `../verification/switches_cases.py` (R20) and
`../verification/board_cases.py` (R21), and R30's in `../verification/pins_cases.py`. R29's
real-build cases come with the first board component built (gen-inhibit port step 1a).
