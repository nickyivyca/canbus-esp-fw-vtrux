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
    python -B -m vtrux_build image <file.bin>...    # what an image holds, as JSON
    python -B -m vtrux_build digest <folder>...     # R7 digests

`-B` matters: a `__pycache__` written into this folder changes its own
digest (R7 counts every file but Markdown and `.gitignore`), and the record
holds the tooling's digest.

The release build runs `idf.py` at whatever priority its shell has. While a
wall-clock test runs on the machine, the build is throttled as `AGENTS.md`
"Builds during wall-clock tests" says (on `NICKY-XPS`, the shell's priority
class set to Idle first), or waits.

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
environment; a clean worktree (R8); `idf.py reconfigure` (R9); downloaded
components against `CHECKSUMS.json` and `dependencies.lock` (R10, after the
reconfigure); no shared component from a local folder (R16,
`override_path` clause); `idf.py build`; the image
`project_description.json` names (R11); HEAD unchanged since the start; the
record (R13). Its docstring has the detail.

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
| `ninja_edges()` | every build statement, includes followed, variables expanded, as `Edge`s (`rule`, `outputs`, `implicit_outputs`, `inputs`, `implicit`, `order_only`, `validations`) |
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
| `vtrux_build/components.py` | downloaded components against their manifests: R10 |
| `vtrux_build/project.py` | a project's `vtrux_build.json`: R22 |
| `vtrux_build/record.py` | the release record: R13 |
| `vtrux_build/release.py` | the release build: R8 to R11, R13, R16 (`override_path`) |
| `vtrux_build/cli.py`, `__main__.py`, `__init__.py` | the command line |
| `tests/test_*.py` | unit tests, one file per module; each runs alone (`python -B shared/tools/tests/test_image.py`) or all with `python -B -m unittest discover -s shared/tools/tests` |
| `tests/imagekit.py` | synthetic app images for the tests, so no firmware image is committed |
| `tests/scratch.py` | scratch folders in the machine temp folder (`vtrux-build-tests/`) |
