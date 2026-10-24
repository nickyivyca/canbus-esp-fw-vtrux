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
```

Exit 0 is a pass, 1 a failure or an unreadable build, 2 a usage error. The output
names the folders it allowed and, for a failure, each file or component at fault
(the first 20 of each kind).

## The checks

| Check | SPEC | What it reads | Fails when |
|---|---|---|---|
| `scope` | R17 (shared/main `5ec6f53`) | `BuildDir.files_read()` in every CMake build folder under `build/` (the app's and the bootloader's); `project_description.json`'s `project_path` and `idf_path`; each folder's `CMakeFiles/<version>/CMake<LANG>Compiler.cmake` and `CMakeCache.txt` `CMAKE_ROOT`; `git worktree list` from the project root; `dependencies.lock` and each `managed_components/*/idf_component.yml` | a file read from outside the project root, `IDF_PATH`, the chosen toolchain's install folder (the folder above its compiler's `bin/`) and `CMAKE_ROOT`; for `--release`, a file read from a `shared/` folder of any worktree of this repo or of a `../shared-<tag>/` checkout beside the worktree root, even inside the project root; a managed component whose own manifest's `version` is not the lock's, or that the lock does not list; records that name no files, no compiler or no `CMAKE_ROOT` |

## Files

| File | What |
|---|---|
| `__init__.py`, `__main__.py`, `cli.py` | the package and its command line |
| `scope.py` | R17 |
| `tests/test_scope.py` | R17's rules, each shown failing on a planted input and passing on a faithful one; its readers on small files. Runs alone: `python -B shared/tools/vtrux_checks/tests/test_scope.py` |

The checks on real builds, passing and failing, are in `../verification/scope_cases.py`.
