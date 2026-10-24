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
```

Any session may run them; they change nothing. With ESP-IDF exported, from the
project's root, e.g. for gen-inhibit: `$env:PYTHONPATH = "<worktree>\shared\tools"` (or
`PYTHONPATH=<worktree>/shared/tools` in bash), then the line above.

Exit 0 is a pass, 1 a failure or an unreadable build, 2 a usage error. The output
names the folders it allowed and, for a failure, each file or component at fault
(the first 20 of each kind). `options` prints every option the profile names, its
profile value and spec line, and its value in each build folder.

## The checks

| Check | SPEC | What it reads | Fails when |
|---|---|---|---|
| `scope` | R17 (shared/main `5ec6f53`) | `BuildDir.files_read()` in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path` and `idf_path`; each folder's `CMakeFiles/<version>/CMake<LANG>Compiler.cmake` and `CMakeCache.txt` `CMAKE_ROOT`; `git worktree list` from the project root; `dependencies.lock` and each `managed_components/*/idf_component.yml` | a file read from outside the project root, `IDF_PATH`, the chosen toolchain's install folder (the folder above its compiler's `bin/`) and `CMAKE_ROOT`; for `--release`, a file read from a `shared/` folder of any worktree of this repo or of a `../shared-<tag>/` checkout beside the worktree root, even inside the project root; a managed component whose own manifest's `version` is not the lock's, or that the lock does not list; records that name no files, no compiler or no `CMAKE_ROOT` |
| `options` | R18 (shared/main `b5d5477`) | `config/sdkconfig.json` (`BuildDir.options()`) in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path`; `vtrux_checks.json`'s `release_profile` in the project's root | an option the profile names is not at the profile's value in some build folder: `"unset"` wants it absent or `false`, any other value wants it present with that value and JSON type (`true` is not `1`); options the profile does not name are free. Also a build folder without `config/sdkconfig.json`, and a profile that is missing or empty, names something not `CONFIG_...`, has an entry without exactly `value` and `spec`, an empty `spec`, or a value that is not `true`, an integer, a string or `"unset"` (`false` is refused: write `"unset"`) |

Run `options` on release builds only (SPEC R18): bench and dev builds are not
checked, and the check does not tell them apart.

### `vtrux_checks.json`

In the project's root, the tester's. Each check reads its own key and ignores the
rest.

```
{"release_profile": {
    "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE": {"value": true, "spec": "<the project spec line it comes from>"},
    "CONFIG_SECURE_BOOT": {"value": "unset", "spec": "..."}}}
```

## Files

| File | What |
|---|---|
| `__init__.py`, `__main__.py`, `cli.py` | the package and its command line |
| `scope.py` | R17 |
| `options.py` | R18 |
| `tests/test_scope.py` | R17's rules, each shown failing on a planted input and passing on a faithful one; its readers on small files. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_scope.py` |
| `tests/test_options.py` | R18's rules, each shown failing on a planted input and passing on a faithful one; the profile's reader; the command line on a small build tree. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_options.py` |

The checks on real builds, passing and failing, are in `../verification/scope_cases.py`
(R17) and `../verification/options_cases.py` (R18).
