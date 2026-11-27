# shared/tools -- the shared build tooling

Build tooling shared by the two Vtrux ESP32 projects, to `SPEC.md` beside
this file. It never enters a firmware image. Python >= 3.10, stdlib only,
so it runs under ESP-IDF's own Python (3.11 on IDF 5.4.1 and 5.4.2) as well
as the machine's.

- **Why** each requirement is worded as it is: `VALIDATION.md`.
- **Test evidence** that this tooling meets it: `VERIFICATION.md` (the
  tester's entries).

## Who owns which file

The files in `vtrux_build/` and `tests/` listed below are the implementor's:
product tooling and its unit tests (gen-inhibit reviewer, 2026-10-23, batch
C6f). So are the pin tools in `vtrux_pins/` (R35, R36), from gen-inhibit's
port step 4 items 6 and 7. The build checks, SPEC R17 to R21, R29 and R30 (apart from R21's
one-board refusal, which the release build makes), and the comparison tool of port
plan goal 6 are the tester's (the same ruling), in files of their own:
`verification/` and `vtrux_checks/` in the table below, with the rest
added when they land. They read the build through the interface below and
do not change it.

## Running it

A project runs the tooling from the checkout of the tag it pins,
`../shared-<tag>/` beside its worktree's root (R23), not from a worktree of
its own. The checkout is not made read-only: the release build checks it
through git instead (below). The pin is the `"shared/tools"` entry of `shared_pins.json` in the
project's root, committed with the project (`vtrux_build/pin.py`):

    {"shared/tools": {"tag": "2026.10.24",
                      "commit": "<the commit the tag marks, 40 hex digits>",
                      "digest": "<R7 digest of shared/tools there, 64 hex digits>"}}

The file is keyed by component, so a project pins its other shared
components there too. The release build reads only the tooling's entry;
the `pin` command checks the others ("Pins on other shared components",
below). The checkout is made once per tag, from any worktree of the
repository:

    git -C <worktree> worktree add --detach ../shared-<tag> <tag>

and the digest for a new pin is `python -B -m vtrux_build digest
../shared-<tag>/shared/tools`, run from that checkout before anything else
has run there (it reads the disk, and R7 counts every file, bytecode
included). The release build compares the pin with the digest it takes
from git, so a wrong one is refused, never used.

A release build refuses to start, `--check-only` included, unless
(`vtrux_build/pin.py`, in this order):
- the project has the pin, and (R16) it names a tag, not a commit: an entry
  with no tag, or one that reads as a commit id (7 to 40 hex digits), is
  refused, and so is a tag the checkout's repository does not have (a
  branch name, say);
- the checkout exists; if not, the refusal prints the command above for it;
- the `shared/tools` folder the tooling runs from is that checkout's, not a
  copy of it elsewhere;
- the checkout's HEAD is the pin's commit, and the pin's tag marks that
  commit;
- the pin's digest is R7 of `shared/tools` at that commit, taken from
  git's objects (`git ls-tree`, `git cat-file`), not from the disk;
- every file git tracks under `shared/tools` is on disk with exactly its
  bytes at that commit, read raw, so git's line-ending settings change
  nothing and a file rewritten with CRLF is refused; with the mode git
  records for it (a regular file, an executable, or a symbolic link whose
  target is the blob), not its permission bits, so a read-only file
  passes; and none is missing. A `.md` file is compared too, though R7
  leaves it out;
- there is no other file under `shared/tools`, except files the checkout's
  committed `.gitignore` files list. They are listed with `git ls-files
  --others --exclude-per-directory=.gitignore`, not `--exclude-standard`,
  so a machine's `core.excludesFile` or `.git/info/exclude` hides nothing;
  a file staged in the checkout's index (`git add`) but not in the commit,
  which `--others` does not list, is found from `git ls-files --cached` and
  refused; and a `.gitignore` that is not the commit's, under
  `shared/tools` or in a folder above it, is itself refused.

`shared/main`'s root `.gitignore` lists `__pycache__/` and `*.pyc`, so the
bytecode Python writes when it runs from the checkout passes, and the
checkout needs no `-B`. On Windows the execute bit is not compared, since
the disk has none, and a symbolic link checked out as a plain file passes
when its bytes are the link's target.

The record's `tooling` holds the pin's tag, commit and digest, as checked,
and `dirty`: whether git sees uncommitted changes under the checkout's
`shared/tools`, which it cannot once the check has passed.

A project usually starts the checkout's code through a small launcher of its
own that reads the tag from its pin and nothing else, so that a missing
checkout is named before any of it runs; every check above is here, in the
pinned code. Without one, from an exported ESP-IDF shell, in the project's
root, with `REVERSE_IT_DIR` set:

    $env:PYTHONPATH = "..\shared-<tag>\shared\tools" # PowerShell
    python -B -m vtrux_build release                # a release build
    python -B -m vtrux_build release --check-only   # its pre-build refusals only
    python -B -m vtrux_build release --jobs 1       # built with ninja -j 1
    python -B -m vtrux_build build <variant> [--jobs N]  # a dev build (R31)
    python -B -m vtrux_build check-env              # the IDF environment check (R32)
    python -B -m vtrux_build image <file.bin>...    # what an image holds, as JSON
    python -B -m vtrux_build digest <folder>...     # R7 digests

`release` and `build` take `--project DIR`, the ESP-IDF project root, the
current folder if not given.

The release build runs at whatever priority it is started with. Builds run
alongside wall-clock tests and the machine's other work, with no throttle
and no waiting (`AGENTS.md` "Parallel work on the shared machine"). On
`madhouse-debian` a build is placed by its slice: start it from the
exported shell as `systemd-run --user --scope --slice=build.slice
<command>`, a scope, which keeps the shell's environment, folder, output
and exit code. `build.slice` gives it the lowest CPU priority and every
idle core, so it needs no `--jobs`. `idf.py build` has no jobs option, so
with `--jobs N` the build step is `ninja -C build -j N all` (what `idf.py
build` runs), in the same environment, after the same `idf.py
reconfigure`. Without `--jobs` it is `idf.py build`.

ESP-IDF runs in an environment the tooling builds from an empty one (R24,
`vtrux_build/buildenv.py`), so nothing in the caller's environment reaches
the build unless it is on the tooling's list: `EXTRA_CFLAGS` and its
`CXX`/`CPP` forms, which ESP-IDF adds to every compile, `CFLAGS`,
`IDF_TARGET`, the Component Manager's settings and `PYTHONPATH` are all
dropped. The list holds what ESP-IDF needs to find itself and its tools,
as the export sets them (`PATH`, `IDF_PATH`, `IDF_PYTHON_ENV_PATH`,
`IDF_TOOLS_PATH`, `ESP_ROM_ELF_DIR`), and what the host OS needs to run
programs: on Windows `SYSTEMROOT`, `PROCESSOR_ARCHITECTURE`, `TEMP` and
`TMP`, each found by a configure or build that failed without it; on Linux
only `TMPDIR`, not yet tried on a Linux host. A name the build needs and the
list lacks fails the build. Then the tooling adds its own settings:
`IDF_COMPONENT_STRICT_CHECKSUM=1` (R10) and `SOURCE_DATE_EPOCH` (R9,
below). It sets no default for a name it passes: a name the caller does
not set is not set (R24 as amended 2026-11-03). The build prints the
names it passed and the names it dropped, never values, and the record
lists the names passed (`environment_passed`). Bench and dev builds are not
constrained.

## Pins on other shared components (R23)

A project's bench and test tooling that uses a pinned checkout checks it
the same way as the release build checks the tooling (R23's last sentence),
with `vtrux_build/pin.py`. Every entry of `shared_pins.json` is a pin of the
same form, keyed by the component's folder below `shared/`, written with
`/`: `"shared/<folder>"`, or deeper, as `"shared/boards/<board>"`. Each pin is
checked in the checkout of its own tag, `../shared-<tag>/` beside the
worktree's root, as in "Running it", so two components pinned at two tags
are checked in two checkouts.

    python -B -m vtrux_build pin check COMPONENT... [--project DIR]
    python -B -m vtrux_build pin where COMPONENT [--project DIR]
    python -B -m vtrux_build pin entry TAG COMPONENT [--project DIR]

`--project` is the folder that holds `shared_pins.json`, the current folder
if not given. Each action first makes the release build's own check of the
tooling's pin, so a pin is never checked or written by tooling that is not
the project's pinned version. Then:
- `check` checks each named component's pin in order, and prints `OK:
  <folder> is the pinned <component> at <tag>` for each;
- `where` checks one component's pin and prints its folder alone on the
  line, for a script to use;
- `entry` prints, as JSON `{component: entry}`, the pin that the checkout
  `../shared-<TAG>/` beside the worktree's root gives the component, for a
  pin move. Its commit is the
  checkout's HEAD, which the tag must mark. Its digest is R7 of the
  component at that commit, from git's objects. It writes nothing, and does
  not compare the checkout's disk with the commit: the pin it gives is
  checked when it is used.

Exit 0; 1 with `REFUSED: <reason>` on stderr at the first refusal; 2 for a
wrong number of names. A component's pin is refused, in this order, unless:
- the name is a folder below `shared/`, and the file has its entry, with a tag
  that is not a commit id and can name a folder, a 40-hex commit and a
  64-hex digest (R16);
- the checkout `../shared-<tag>/` beside the worktree's root exists; if
  not, the refusal prints the command that creates it;
- the checkout's HEAD is the pin's commit, and the pin's tag is a tag there
  that marks it;
- the commit has the component's folder, and the pin's digest is R7 of it,
  from git's objects;
- every file git tracks under the component is on disk with its bytes and
  mode at the commit, none is missing, and no other file is under it but
  those the checkout's committed `.gitignore` files list, all as for the
  tooling ("Running it" above).

Only the named component's folder is checked: the rest of the checkout may
differ from the commit. In Python, `pin.check_component(project_root,
component)` makes the same check and returns `{"tag", "commit", "digest",
"dirty", "folder"}`; it does not check the tooling it runs in. A caller that
imports check_component() must first call pin.check(project_root,
tools_root), with the shared/tools folder it loaded pin.py from, to get the
same guarantee as the command. `pin.entry(project_root, tag, component)`
gives the entry. Both raise `PinError`.

## Pins on outside packages (R35)

`vtrux_pins` pins two outside Python packages that a project's harness
imports, each at one commit, and loads them so that no other copy is used:
- **canbench,** the CAN adapter package, from the can-tools repo
  (`vtrux_pins/cantools.py`);
- **canre,** the CAN analysis library, from the public reverse-engineering
  repo (`vtrux_pins/canre.py`).

Both pins are in one pins file, which the caller names. Each entry has the
repo and the full commit; canre's also has its version:

    {"can-tools": {"repo": "https://github.com/nickyivyca/can-tools",
                   "commit": "<40 lower-case hex digits>"},
     "canre": {"repo": "https://github.com/nickyivyca/canbus-reveng-vtrux-coda",
               "version": "0.1.0",
               "commit": "<40 lower-case hex digits>"}}

The file may hold other keys, such as a project's other pins, and they are
ignored. An entry that is missing, has an empty field, or has a commit that
is not 40 lower-case hex digits is refused.

    python -B -m vtrux_pins can-tools install|check|where --pins FILE
    python -B -m vtrux_pins canre install|check|where --pins FILE

- `install` writes the pinned commit's package if it is not there, then
  checks it.
- `check` checks it and writes nothing.
- `where` prints its folder.

Exit 0; 1 with `REFUSED: <reason>` on stderr at a refusal; 2 for a usage
error. In Python, `check(pins_file)` makes checks 1 to 3 below and returns
the folder, `use(pins_file)` makes every check and loads the package, and
`install(pins_file)` installs. Each raises `vtrux_pins.pinsfile.PinsError`.
The caller loads `vtrux_pins` from the pinned shared/tools checkout, and
first checks the tooling's own pin, as for `check_component()` above.

**Where each package is written.** Never into an interpreter's
site-packages, where every other tool on the machine would import it. The
worktree is the git worktree that holds the pins file, found by walking up
from the file to a `.git`:
- canbench: `<worktree>/scratch/can-tools/<commit>/canbench/`. Outside a
  git worktree canbench is refused.
- canre: `<worktree>/scratch/canre/<commit>/`. If `CANRE_DIR` is set, it is
  that folder instead, which must be an absolute path. This is how a copy of
  the code with no git, such as one shipped to the bench host, names its
  canre. Outside a git worktree only `CANRE_DIR` names the folder, and canre
  is refused if it is not set: a copy never finds canre by its own location.

**Install.** The package is written into `<target>.partial-<pid>` beside the
target, and moved into place only after it passes the checks. A partial
folder from an earlier run is removed first; if it cannot be removed,
install is refused. The partial folder is removed at the end, pass or fail
(`vtrux_pins/remove.py`, AGENTS.md "Removing scratch"). A target that is
there already is checked and never replaced: if it fails, install is
refused, and deleting it is left to a person. Install is refused outside a
git worktree.
- canbench is written from the can-tools clone that `CAN_TOOLS_DIR` names.
  Every file under `canbench/` at the pinned commit is written from its git
  blob (`git cat-file blob`), so no line-ending or other filter applies. The
  clone's HEAD, branch and working tree play no part.
- canre is installed by pip: `pip install --no-deps --no-compile
  --disable-pip-version-check --no-input --target <partial folder>
  git+<repo>@<commit>`. pip needs the network. Install always writes into
  the worktree, never into `CANRE_DIR`.

**The canbench checks**, in order. canbench is refused unless:
1. `CAN_TOOLS_DIR` is set and holds `canbench/`. There is no default.
2. The clone has the pinned commit; if not, the refusal says to fetch it.
   Every entry under `canbench/` at that commit is a regular or executable
   file (no link, no submodule), and `canbench/__init__.py` is one of them.
3. The folder exists; if not, the refusal names the install command. Every
   file in its `canbench/` has the git blob hash of the same path at the
   pinned commit. None is missing, none is changed and none is added.
   `__pycache__` folders are not compared.
4. `use()` loads canbench by file from that folder. Neither the folder nor
   the clone is put on `sys.path`, so nothing else imports from them by
   accident. A canbench imported earlier from any other file is refused;
   one already loaded from the pinned folder is kept.

The repo in the pin is recorded, not compared with the clone's remote: the
commit fixes the content.

**The canre checks**, in order. canre is refused unless:
1. The folder exists; if not, the refusal names the install command.
2. It holds exactly one canre `.dist-info` folder. Its `METADATA` gives the
   pinned version. Its `direct_url.json` (PEP 610) names the pinned repo
   and, as a git commit, the pinned commit. A folder with no
   `direct_url.json` is refused, since it was not installed from a commit.
3. Every file its `RECORD` lists with a hash is there with that sha256, so
   an installed file edited by hand is refused. Every file under its
   `canre/` package is in `RECORD`, so a file added by hand is refused.
   `__pycache__` folders are not compared. A package with no
   `canre/__init__.py` is refused.
4. `use()` puts the folder first on `sys.path`, so `canre.*` imports after
   it come from it, and `import canre` must load that folder's
   `canre/__init__.py`. A canre imported earlier from any other file is
   refused.

## Pins on outside images (R36)

An image pin names the files of firmware images that a project's bench
flashes but does not build, such as gen-inhibit's X2 images and the
interposer's WiCAN image (`vtrux_pins/images.py`). The pin is a committed
JSON file, which the caller names. The files are not in git: they sit in a
folder that the local settings name, and nothing in the settings can change
what the pin names.

    {"_comment": "...",
     "pin": "<id: a plain folder name>",
     "source": {"project": "gen-inhibit", "commit": "<40 hex>", ...},
     "images": {
       "<image name>": {
         "image_hash": "<64 hex: R1, the app descriptor's ELF sha256>",
         "code_hash": "<64 hex: R2>",
         "identity": "<optional: what the bench reads off the board>",
         "files": [{"offset": "0x10000", "name": "app.bin", "size": 1021520,
                    "sha256": "<64 hex>", "md5": "<32 hex>", "app": true},
                   ...]}}}

Other keys at the top, in `source` and in each image (how it was built, how
its identity is read, chip and flash settings) are recorded and not judged.

    python -B -m vtrux_pins image check --pin FILE --dir DIR
    python -B -m vtrux_pins image install --pin FILE --dir DIR --source FOLDER [--source FOLDER ...]

**The pin is refused**, before any file is read, unless:
- its id is a plain folder name (letters, digits, `.`, `_` and `-`; not `.`
  or `..`);
- its `source` names a non-empty `project` and a 40-hex `commit`;
- it names at least one image, and each image name is a plain folder name;
- each image has a 64-hex `image_hash` and a 64-hex `code_hash`, both
  required, and at least one file;
- each image's `identity`, if given, is a non-empty string, and an 8-hex
  identity is the first 8 digits of the image hash (a build id the board
  reports);
- each file has only the keys `offset`, `name`, `size`, `sha256`, `md5`
  and `app`, all but `app` required: an offset written as lower-case hex
  (`0x10000`), a plain file name, a positive size, a 64-hex sha256 and a
  32-hex md5. `app`, if given, is `true`;
- no image gives one offset or one name twice;
- exactly one file in each image is the app.

**check** (and `images.load(pin_file, dir)`, in Python) finds each file at
`<DIR>/<pin id>/<image name>/<name>`. It passes only if:
- DIR is a folder;
- every pinned file is there with its size, sha256 and md5;
- each app file parses as an ESP-IDF app image (`vtrux_build.image`), and
  its image hash (R1) and code hash (R2) are the image's `image_hash` and
  `code_hash`.

It prints the pin's id, the sha256 of the pin file's bytes, and each image
and file. `load()` returns them for the flasher: each image's files with
their offsets, in flash order, its app file and its identity.

**install** copies each pinned file into place, from the first `--source`
folder that holds a file of that name with the pinned size, sha256 and md5.
It does not search sub-folders. It plans every file before it copies any:
- a file already in place with the pinned bytes is left as it is;
- a file in place with other bytes is refused, and nothing is overwritten;
- a file that no source holds is refused.

Each copy is written to `<name>.partial` and then renamed into place. Then
install runs check.

Exit 0 when every file matches; 1 when a file is missing or does not match
(`MISMATCH:`); 2 when the pin is refused or the arguments are wrong
(`REFUSED:`).

## The IDF environment check (R32)

Before a release build or a dev build hands anything to ESP-IDF, the
environment it was started in is checked (`vtrux_build/idfenv.py`):
`IDF_PATH` is set and not empty; `IDF_TOOLS_PATH` is set, not empty and has
no spaces around it, since the value checked is the value ESP-IDF gets; and
`IDF_PYTHON_ENV_PATH` and `ESP_ROM_ELF_DIR` are set and inside
`IDF_TOOLS_PATH`. A failure is a refusal naming the variable. Nothing is
defaulted: an unset `IDF_TOOLS_PATH` is never replaced by `~/.espressif`,
which on a machine with two ESP-IDF installs, each with its own tools
folder, may be the other install's. `check-env` runs the check alone,
exit 0 or 1; a project whose bench builds do not go through `build` runs
it before each. An `IDF_PATH` of one version with the other's tools is not
caught here. For a release build the project's exact IDF version (R33,
below) covers it: the ESP-IDF version the configure reports must be the
one the project names. Bench and dev builds are not guarded against it.

## Dev builds (R31)

`build <variant> [--jobs N]` (`vtrux_build/devbuild.py`) makes a bench or
dev build of one of the variants the project's `vtrux_build.json` lists
(below). A variant not listed is refused, naming the list; a project that
lists none has no build command. R32 runs first. Then, in
`build/<variant>` under the project root, `idf.py -B build/<variant> -D
<option>=<value>... reconfigure` with the variant's options, and `ninja -C
build/<variant> all`, with `-j N` when `--jobs` is given (a build's slice,
not `--jobs`, places it beside other work: "Running it" above). Both run
in the caller's environment as it is: nothing added, nothing dropped.
None of the release build's other conditions apply (R14).

`SOURCE_DATE_EPOCH` is one fixed value, `1767225600` (2026-01-01T00:00:00Z),
for every project and commit (R9): GCC takes `__DATE__` and `__TIME__` from
it, so source using them, the Arduino core the interposer compiles among
it, builds the same each time. ESP-IDF v5.4.1 never sets it. It is a
stopgap, to be removed when no project compiles that core.

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
(which R32 requires set; it is never defaulted), holds a file of that
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
| `variants` | the dev build command only (R31) | `{variant: {CMake option: value}}`, e.g. `{"truck": {"INTP_VARIANT": "truck"}}`: each bench and dev build variant and the `-D` options it is configured with. A name is letters, digits, `_` and `-`, since it names the build folder. Absent or `{}`: no build command. A release build does not read it |

The project's ESP-IDF version is not in this file either. It is the `idf`
dependency of `main/idf_component.yml`, one exact version, `5.4.2` or
`==5.4.2` (R33); a release build refuses a range or no version. Under
another IDF a lockless configure then fails; with the committed lock the
configure rewrites it and goes on. So after the reconfigure, before
anything is compiled, a release build is refused unless the ESP-IDF version
the configure reports, `git_revision` in `build/project_description.json`
(the value its record names, R13), is exactly `v` and that version, such
as `v5.4.2`. A suffix (`-dirty`, `-3-g<hash>` for commits past the tag), a
bare commit hash, another version, or no value is refused, and the refusal
names both versions. That also refuses a lock solved under the other IDF
and committed, which the configure does not rewrite; a rewritten lock is
seen by R8's clean-worktree check after the reconfigure as well. Only a
release build runs it: bench and dev builds are not guarded (R14), and
`check-env`, `image` and `digest` do not run it.

The build checks' data (the release profile, the switch table and its
refusal rows) is not in this file. It is in `vtrux_checks.json` in the
project's root, which the tester owns, in the form the checks define.
`project.py` reads only the two keys above: it neither rejects other keys
nor relies on any.

Nor is the project's board. A project names it by its dependency and
nowhere else (R21, `shared/boards/SPEC.md` B2): the one `shared/boards/*`
component in its `dependencies.lock`: a git source whose path is exactly
`shared/boards/<board>`, from the repository's root, or a local source whose
path ends in it. A release build is refused after the reconfigure unless
there is exactly one, and unless it is the git one: a board is never taken
from a local folder, whether or not the project lists it as shared (R16,
`shared/boards/SPEC.md` B1). The board's data
is that component's `board.json`, which the checks read from
`managed_components/`, so the board checked is the board built.

## The release build (SPEC section 3)

`vtrux_build/release.py`, in order, each refusal an exit: the project's
data; the tooling's pin (R23, and R16's tooling-pin clause; "Running it"
above); the project's exact IDF version (R33); `REVERSE_IT_DIR` set (R13,
before anything is built); the IDF environment check (R32); the IDF
environment, with the Component Manager's overwrite setting
(`IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS`) not set at all (R10); the
environment ESP-IDF runs in, from an empty one (R24); a
clean worktree (R8); `idf.py --no-ccache reconfigure` in that environment,
with `IDF_COMPONENT_STRICT_CHECKSUM=1` and the fixed `SOURCE_DATE_EPOCH`
(R9, R10); the ESP-IDF version the configure reports, exactly the
project's (R33); the `sdkconfig` the build
used, inside the project, tracked and not ignored (R8); the worktree still clean and
HEAD unchanged (R8); `CCACHE_ENABLE` off in `build/CMakeCache.txt` and no compiler
launcher in its compile commands (R9); a `dependencies.lock` after the
reconfigure, even for a project that downloads nothing (R10); every
downloaded component's `.component_hash` against
`dependencies.lock`, and a registry download against its `CHECKSUMS.json`,
unlisted files included (R10, after the reconfigure); no shared component
from a local folder (R16, `override_path` clause); exactly one
`shared/boards/*` component in `dependencies.lock` (R21, the board), and
that one from git (R16);
`idf.py --no-ccache
build`, or `ninja -C build -j N all` with `--jobs N`, in the same
environment;
`CCACHE_ENABLE` still off and no launcher in any build folder's compile
commands (R9); the image `project_description.json` names
(R11); HEAD unchanged and the worktree clean at the end (R8); the record
(R13). Its docstring has the detail.

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
| `pyproject.toml`, `.gitignore` | the package (`vtrux-build`, command `vtrux-build`, with `vtrux_pins`) |
| `vtrux_build/image.py` | the image library: R1, R2 |
| `vtrux_build/digest.py` | the component digest: R7 |
| `vtrux_build/builddir.py` | the build folder readers: R11, and the records the checks read |
| `vtrux_build/lockfile.py` | `dependencies.lock` reader |
| `vtrux_build/manifest.py` | `idf_component.yml` dependency reader (the pinned tag, `override_path`), and R33's reading of the `idf` dependency |
| `vtrux_build/components.py` | downloaded components against the lock and, where the registry supplied one, their `CHECKSUMS.json`: R10, with the Component Manager's strict mode doing the re-hash |
| `vtrux_build/project.py` | a project's `vtrux_build.json`: R22, and R31's variants |
| `vtrux_build/idfenv.py` | the IDF environment check: R32 |
| `vtrux_build/devbuild.py` | the dev build command and the `check-env` command: R31, R32 |
| `vtrux_build/record.py` | the release record: R13 |
| `vtrux_build/toolchain.py` | this IDF's toolchain, from its `tools.json` and toolchain file: R9's check that each chosen compiler is the toolchain's own |
| `vtrux_build/buildenv.py` | a release build's environment, from an empty one, with no defaults: R24, and R9's `SOURCE_DATE_EPOCH` |
| `vtrux_build/pin.py` | the pins in `shared_pins.json`: the tooling's own, and the check that the tooling runs from the pinned checkout, made through git (HEAD and tag, R7 from git's objects, tracked files' bytes and modes, no other file but what committed `.gitignore` files list): R23, R16's tooling-pin clause; the same check for any other pinned component, the entry for a pin move, and the `pin` command (R23's last sentence) |
| `vtrux_build/release.py` | the release build: R8 to R11, R13, R16 (both clauses, the tooling pin through `pin.py`), R21's one-board refusal, R23, R24, R32 (through `idfenv.py`), R33 |
| `vtrux_build/cli.py`, `__main__.py`, `__init__.py` | the command line |
| `tests/test_*.py` | unit tests, one file per module (`toolchain.py`'s and the command line's are in `test_release.py`, with the check that uses it; `project.py`'s variants are in `test_devbuild.py`); each runs alone (`python -B shared/tools/tests/test_image.py`) or all with `python -B -m unittest discover -s shared/tools/tests`. `test_buildenv.py`'s `RealConfigure` runs ESP-IDF itself, two configures of a small project (about 40 s), so it is skipped unless `VTRUX_IDF_TESTS=1` is set in an exported ESP-IDF shell; it runs in `build.slice` like a build (`AGENTS.md` "Parallel work on the shared machine") |
| `tests/imagekit.py` | synthetic app images for the tests, so no firmware image is committed |
| `tests/pinkit.py` | a pinned checkout for the tests: a small tagged repository at `<folder>/shared-<tag>/`, with a root `.gitignore` listing Python's bytecode as `shared/main`'s does, other components beside `shared/tools` if asked for, and the pin that matches each; `test_release.py` gives every test project one |
| `tests/test_pin.py` | `pin.py`'s tests, among them R23's: a stray `__pycache__` or `.pyc` passes; a changed, CRLF-rewritten, missing or `.md` tracked file refuses; an execute bit flipped either way refuses and a read-only component passes; an untracked file refuses, and so does one staged in the index but not in the commit, or hidden only by `core.excludesFile` or `info/exclude`, or by a `.gitignore` that is not the commit's; a pin digest that is not the commit's refuses. For other components: a name that is not a folder below `shared/` refuses, and a board's folder two below it is checked like any other; each pin is checked in its own tag's checkout, against its own folder only (a changed `shared/tools` beside it does not refuse it), with the same refusals; `entry` takes its digest from git and writes nothing; the `pin` command checks the tooling first, so every action refuses when the tooling is a copy or changed. `--can-fail` runs them against 26 edited copies of `pin.py` and passes only if the unedited copy passes and every edited one fails |
| `vtrux_pins/pinsfile.py` | the pins file of outside packages, and the worktree that holds it: R35 |
| `vtrux_pins/cantools.py` | the canbench pin: R35 |
| `vtrux_pins/canre.py` | the canre pin: R35 |
| `vtrux_pins/images.py` | the pins on outside images: R36, with `vtrux_build.image` for R1 and R2 |
| `vtrux_pins/remove.py` | the remover of an install's partial folder (AGENTS.md "Removing scratch"), the same as `tests/scratch.py`'s |
| `vtrux_pins/cli.py`, `__main__.py`, `__init__.py` | the command line of the pin tools |
| `tests/test_vtrux_pins_packages.py` | the package pins' tests: the pins file (other keys ignored; an incomplete or short entry refused), canbench against a stand-in clone (raw blobs written; a link at the commit, a missing commit or no `canbench/__init__.py` refused; a missing, changed, CRLF-rewritten or added file refused; a folder there is never replaced; a stale partial removed, and one that cannot be removed refuses; outside a worktree refused; loaded by file with `sys.path` unchanged, and a canbench from elsewhere refused), and canre against stand-in installs and a stand-in pip (another commit, repo, vcs or version, no `direct_url.json`, two `.dist-info` folders, and a changed, added or removed file refused; `CANRE_DIR` used when set and absolute, and the only way outside a worktree; install never writes to it; first on `sys.path`), and both commands. `--can-fail` runs them against 36 edited copies of `vtrux_pins` and passes only if the unedited copy passes and every edited one fails |
| `tests/test_vtrux_pins_images.py` | the image pins' tests, on synthetic images: every malformed pin refused, the code hash required; files found only at `<dir>/<pin>/<image>/<name>`; each of size, sha256 and md5 compared; the app's image hash and code hash compared; files in flash order; install takes the first source with the bytes, searches no sub-folder, plans before it copies and never overwrites; the command's exit codes. `--can-fail` runs them against 22 edited copies of `images.py` |
| `tests/pinpkgkit.py` | the package pins' stand-ins: a can-tools clone, canre installs and pip |
| `tests/scratch.py` | the tests' scratch folders: `<worktree>/scratch/shared-tools-tests/<pid>/`, the run's own, removed when the run ends, pass or fail; refused, with no other place, outside a git worktree or where git does not ignore `scratch/` (`AGENTS.md` "Files and folders"), and so under WSL in a worktree made by Git for Windows, whose `.git` file Linux git cannot follow. Its remover clears read-only flags and retries, uses `\\?\` paths on Windows and prints what it could not remove ("Removing scratch"): a copy of `verification/independent.py`'s, since the unit tests do not import the tester's files. Tested in `tests/test_scratch.py` |
| `verification/` | the tester's: its verification of this tooling against `SPEC.md`, the evidence behind `VERIFICATION.md`, and the build checks' real-build cases; its `README.md` lists the files |
| `vtrux_checks/` | the tester's: the build checks, SPEC R17 to R21, R29 and R30, reading a finished build through the interface above; their per-project data is the tester's `vtrux_checks.json` in the project's root; its `README.md` lists the checks |
