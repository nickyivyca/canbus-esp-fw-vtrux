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
plan goal 6 are the tester's (the same ruling), in files of their own,
added to this table when they land. They read the build through the
interface below and do not change it.

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
digest (R7 counts every file but Markdown and `.gitignore`), and the record
holds the tooling's digest.

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
it is refused: ccache, or any other launcher. So is a chosen compiler that
is ccache itself once its links are resolved (ccache's masquerade mode, a
link named after the compiler on `PATH`). This is checked in `build/`
after the reconfigure, and in every CMake build folder under `build/` after
the build, the bootloader's included, since the build configures it. A
folder with no compile commands, or no recorded compiler, is refused too.

## A project's build data (R22)

The tooling holds no project facts. A project gives them in
`vtrux_build.json` in its root (`vtrux_build/project.py`):

| Key | Read by | What |
|---|---|---|
| `project` | the build script | the record folder's name, `firmware/builds/<project>/` |
| `shared_components` | the build script | the dependency names of the shared firmware components it pins; R16 and the record use them. `[]` for none |
| anything else | the build checks | the release profile, switch table and board data, in the form the checks define |

## The release build (SPEC section 3)

`vtrux_build/release.py`, in order, each refusal an exit: the project's
data; `REVERSE_IT_DIR` set (R13, before anything is built); the IDF
environment, with the Component Manager's overwrite setting
(`IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS`) not set at all (R10); a
clean worktree (R8); `idf.py --no-ccache reconfigure` with
`IDF_COMPONENT_STRICT_CHECKSUM=1` (R9, R10); the worktree still clean and
HEAD unchanged (R8); `CCACHE_ENABLE` off in `build/CMakeCache.txt` and no compiler
launcher in its compile commands (R9); every downloaded component's `.component_hash` against
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
| `vtrux_build/release.py` | the release build: R8 to R11, R13, R16 (`override_path`) |
| `vtrux_build/cli.py`, `__main__.py`, `__init__.py` | the command line |
| `tests/test_*.py` | unit tests, one file per module; each runs alone (`python -B shared/tools/tests/test_image.py`) or all with `python -B -m unittest discover -s shared/tools/tests` |
| `tests/imagekit.py` | synthetic app images for the tests, so no firmware image is committed |
| `tests/scratch.py` | the tests' scratch folders: `<worktree>/scratch/shared-tools-tests/` where git ignores `scratch/` (a project worktree), else the machine temp folder's `vtrux-build-tests/` |
