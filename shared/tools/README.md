# shared/tools -- the shared build tooling

Build tooling shared by the two Vtrux ESP32 projects, to `SPEC.md` beside
this file. It never enters a firmware image. Python >= 3.10, stdlib only,
so it runs under ESP-IDF's own Python (3.11 on IDF 5.4.1) as well as the
machine's.

- **Why** each requirement is worded as it is: `VALIDATION.md`.
- **Test evidence** that this tooling meets it: `VERIFICATION.md` (the
  tester's entries).

## Who owns which file

The files in `vtrux_build/` and `tests/` listed below are the implementor's:
product tooling and its unit tests (gen-inhibit reviewer, 2026-10-23, batch
C6f). The build checks, SPEC R17 to R21, and the comparison tool of port
plan goal 6 are the tester's (the same ruling), in files of their own:
`verification/` and `vtrux_checks/` in the table below, with the rest
added when they land. They read the build through the interface below and
do not change it.

## Running it

Until R23 (running from a pinned `../shared-<tag>/` checkout) lands, put
this folder on the path. From an exported ESP-IDF shell, in the project's
root, with `REVERSE_IT_DIR` set:

    $env:PYTHONPATH = "<checkout>\shared\tools"     # PowerShell
    python -B -m vtrux_build release                # a release build
    python -B -m vtrux_build release --check-only   # its pre-build refusals only
    python -B -m vtrux_build release --jobs 1       # built with ninja -j 1
    python -B -m vtrux_build image <file.bin>...    # what an image holds, as JSON
    python -B -m vtrux_build digest <folder>...     # R7 digests

`-B` matters: a `__pycache__` written into this folder changes its own
digest (R7 counts every file but those whose name ends `.md` and those named
`.gitignore`, case-sensitively), and the record holds the tooling's digest.

The release build runs at whatever priority its shell has. While a
wall-clock test runs on the machine, the build is throttled as `AGENTS.md`
"Builds during wall-clock tests" says, or waits. On `NICKY-XPS` that is
`--jobs 1` from a shell whose priority class is Idle. `idf.py build` has no
jobs option, so with `--jobs N` the build step is `ninja -C build -j N all`
(what `idf.py build` runs), in the same strict environment, after the same
strict `idf.py reconfigure`. Without `--jobs` it is `idf.py build`.

The compiler cache is off in a release build (R9), whatever the
environment sets: the IDF export sets `IDF_CCACHE_ENABLE=1` wherever ccache
is installed, and `idf.py`'s `--no-ccache`, which both `idf.py` calls pass,
wins over it. The build is refused unless `build/CMakeCache.txt` has
`CCACHE_ENABLE` off, after the reconfigure and again after the build, on
either build path.

That setting is only one way in: `CMAKE_C_COMPILER_LAUNCHER` (and its CXX
and ASM forms), from the environment or the cache, launches ccache with
`CCACHE_ENABLE` off. So the effect is checked too. Every compile command
CMake wrote into `build.ninja`, as ninja would run it, must start with the
compiler CMake chose for its language
(`CMakeFiles/<version>/CMake<LANG>Compiler.cmake`). Anything in front of
it is refused: ccache, or any other launcher. A folder with no compile
commands, or no recorded compiler, is refused too.

Nor may the chosen compiler be ccache itself. In ccache's masquerade mode a
copy, hardlink or link of it, or a wrapper script, named after the
compiler and first on `PATH` becomes the compiler CMake chooses, and the
compile commands then show nothing in front of it. Looking for ccache
cannot see a copy or a wrapper, so the check is positive
(`vtrux_build/toolchain.py`): every compiler CMake chose, C at least, must
be this IDF's own toolchain binary. The build folder's
`CMAKE_TOOLCHAIN_FILE`, which must be in `$IDF_PATH/tools/cmake/`, names
each language's compiler. Exactly one tool in `$IDF_PATH/tools/tools.json`,
at the version it marks recommended and installed under `IDF_TOOLS_PATH`
(`~/.espressif` if unset, as `idf_tools.py` has it), holds a file of that
name. The chosen compiler has that name and, with its links resolved, is a
file in that tool's folder. It trusts the IDF install itself: a toolchain
whose own files were replaced is not detected.

Both checks run in `build/` after the reconfigure, and in every CMake build
folder under `build/` after the build, the bootloader's included, since the
build configures it.

The `sdkconfig` the build used must be committed in the project (R8), since
R18 leaves the options a release profile does not name to it. It is
`config_file` in `build/project_description.json`, IDF's `SDKCONFIG` build
property, so it covers every way IDF takes one (`-D SDKCONFIG`, a preset,
the project's `CMakeLists.txt`) as well as the default
`<project>/sdkconfig`. After the reconfigure, before the worktree is checked
again, the build is refused if that file is missing, is outside the project
(links resolved), is not tracked by git (`git ls-files --error-unmatch`), or
is matched by any ignore rule (`git check-ignore --no-index`, so a tracked
file is matched too). An ignored `sdkconfig` would otherwise pass the
clean-worktree check whatever it held.

## A project's build data (R22)

The tooling holds no project facts. A project gives them in
`vtrux_build.json` in its root (`vtrux_build/project.py`):

| Key | Read by | What |
|---|---|---|
| `project` | the build script | the record folder's name, `firmware/builds/<project>/` |
| `shared_components` | the build script | the dependency names of the shared firmware components it pins; R16 and the record use them. `[]` for none |

The build checks' data (the release profile, the switch table and its
refusal rows, and the project's board data) is not in this file. It is in
`vtrux_checks.json` in the project's root, which the tester owns, in the form
the checks define. `project.py` reads only the two keys above: it neither
rejects other keys nor relies on any.

## The release build (SPEC section 3)

`vtrux_build/release.py`, in order, each refusal an exit: the project's
data; `REVERSE_IT_DIR` set (R13, before anything is built); the IDF
environment, with the Component Manager's overwrite setting
(`IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS`) not set at all (R10); a
clean worktree (R8); `idf.py --no-ccache reconfigure` with
`IDF_COMPONENT_STRICT_CHECKSUM=1` (R9, R10); the `sdkconfig` the build
used, inside the project, tracked and not ignored (R8); the worktree still clean and
HEAD unchanged (R8); `CCACHE_ENABLE` off in `build/CMakeCache.txt` and no compiler
launcher in its compile commands (R9); a `dependencies.lock` after the
reconfigure, even for a project that downloads nothing (R10); every
downloaded component's `.component_hash` against
`dependencies.lock`, and a registry download against its `CHECKSUMS.json`,
unlisted files included (R10, after the reconfigure); no shared component
from a local folder (R16, `override_path` clause); `idf.py --no-ccache
build`, or `ninja -C build -j N all` with `--jobs N`, also strict;
`CCACHE_ENABLE` still off and no launcher in any build folder's compile
commands (R9); the image `project_description.json` names
(R11); HEAD unchanged and the worktree clean at the end (R8); the record
(R13). Its docstring has the detail.

Not here yet: R16's refusal of a tooling pin that names a commit, and R23,
which both wait on the `shared/main` tag scheme (SPEC section 8). The
record therefore holds the identity of the checkout the tooling ran from
(tag, commit, dirty, digest), not a pin file's contents.

## The interface (stable: the build checks and the bench toolkit use it)

`vtrux_build.image` -- R1 and R2:

| Name | What |
|---|---|
| `AppImage.read(path)`, `AppImage(data)` | parse a `.bin`; raises `ImageError` unless it parses to exactly its length and its checksum and SHA-256 match |
| `.image_hash`, `.code_hash` | R1, R2, 64 hex digits |
| `.code_hash_blanks()` | the `(offset, length)` ranges R2 zeroes |
| `.version`, `.project_name`, `.time`, `.date`, `.idf_ver`, `.chip_id`, `.chip`, `.segments` | read from the image |
| `.summary()` | all of the above as a dict |
| `image_hash(path)`, `code_hash(path)` | shortcuts; `code_hash` is what the bench record writer logs per image (R15) |

`vtrux_build.builddir.BuildDir(path)` -- the build's own record, read only:

| Name | What |
|---|---|
| `description()` | `project_description.json` |
| `app_bin()`, `app_elf()` | R11: the files it names; `BuildDirError` if a name or file is missing, never a guess |
| `config_file()` | R8: the `sdkconfig` the build used (`config_file`), absolute; `BuildDirError` if not named |
| `components()` | `{name: folder}` of every component the build used |
| `options()` | `config/sdkconfig.json`: the options in effect |
| `cmake_cache()` | `CMakeCache.txt` as `{name: (type, value)}` |
| `ninja_deps(current_only=True)` | `.ninja_deps` as `{output: [files]}`; by default only outputs in today's `build.ninja` (matches `ninja -t deps`) |
| `ninja_edges()` | every build statement, includes followed, variables expanded, as `Edge`s (`rule`, `outputs`, `implicit_outputs`, `inputs`, `implicit`, `order_only`, `validations`, `bindings`: its own variables, `command`: its rule's command as ninja would run it, `None` for phony) |
| `cmake_inputs()` | the CMake files the configure read (the `RERUN_CMAKE` edge's inputs) |
| `files_read()` | the union of the three above, absolute and normalised |
| `resolve(p)` | a ninja path made absolute |

`vtrux_build.digest.component_digest(folder)` -- R7; the exact definition
is in the module docstring. `component_files(folder)` lists what it covers.

`vtrux_build.lockfile` and `vtrux_build.manifest` -- `read(path)` of
`dependencies.lock` and `idf_component.yml` dependencies, as dicts.

`vtrux_build.record` -- `read(path)` of a release record; the fields are in
the module docstring (`RECORD_SCHEMA` 1).

## Files

| File | What |
|---|---|
| `SPEC.md`, `VALIDATION.md`, `VERIFICATION.md` | the spec and its two companions (the reviewer's and the tester's) |
| `pyproject.toml`, `.gitignore` | the package (`vtrux-build`, command `vtrux-build`) |
| `vtrux_build/image.py` | the image library: R1, R2 |
| `vtrux_build/digest.py` | the component digest: R7 |
| `vtrux_build/builddir.py` | the build folder readers: R11, and the records the checks read |
| `vtrux_build/lockfile.py` | `dependencies.lock` reader |
| `vtrux_build/manifest.py` | `idf_component.yml` dependency reader (the pinned tag, `override_path`) |
| `vtrux_build/components.py` | downloaded components against the lock and, where the registry supplied one, their `CHECKSUMS.json`: R10, with the Component Manager's strict mode doing the re-hash |
| `vtrux_build/project.py` | a project's `vtrux_build.json`: R22 |
| `vtrux_build/record.py` | the release record: R13 |
| `vtrux_build/toolchain.py` | this IDF's toolchain, from its `tools.json` and toolchain file: R9's check that each chosen compiler is the toolchain's own |
| `vtrux_build/release.py` | the release build: R8 to R11, R13, R16 (`override_path`) |
| `vtrux_build/cli.py`, `__main__.py`, `__init__.py` | the command line |
| `tests/test_*.py` | unit tests, one file per module (`toolchain.py`'s are in `test_release.py`, with the check that uses it); each runs alone (`python -B shared/tools/tests/test_image.py`) or all with `python -B -m unittest discover -s shared/tools/tests` |
| `tests/imagekit.py` | synthetic app images for the tests, so no firmware image is committed |
| `tests/scratch.py` | the tests' scratch folders: `<worktree>/scratch/shared-tools-tests/` where git ignores `scratch/` (a project worktree), else the machine temp folder's `vtrux-build-tests/` |
| `verification/` | the tester's: its verification of this tooling against `SPEC.md`, the evidence behind `VERIFICATION.md`, and the build checks' real-build cases; its `README.md` lists the files |
| `vtrux_checks/` | the tester's: the build checks, SPEC R17 to R21, reading a finished build through the interface above; their per-project data is the tester's `vtrux_checks.json` in the project's root; its `README.md` lists the checks |
