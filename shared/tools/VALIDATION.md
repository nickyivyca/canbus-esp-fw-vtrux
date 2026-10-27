# Shared build tooling: validation evidence

Why each requirement in `SPEC.md` is written as it is. Test evidence, that
the tooling meets them, is in `VERIFICATION.md`.

**How to read this file.** Each requirement has:
- **Basis:** the ruling, measurement or reading the requirement rests on.
  Most rest on a user ruling, cited with its date and where it is recorded.
- **Exposed by** (where there is one): the finding that showed the earlier
  wording or tooling was wrong. A finding is cited as what exposed the
  problem. The basis is the correction or ruling that followed.
- **Context** (where there is one): a measurement made on another toolchain,
  board or code base. It points the same way but is not a basis for this
  requirement.
- **Status:** *complete*, or *basis pending*, saying what is missing.
  Validation may be incomplete, but never silently.

A pass recorded against the tooling this replaces (gen-inhibit's
`prod_build.py` and build checks, the interposer's PlatformIO build scripts)
verified that tooling. It is not cited here.

**Where things are.** The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`.
gen-inhibit's spec, tracker and test evidence are
`$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`,
`gen-inhibit-review-2026-09-22.md` and `gen-inhibit-test-evidence.md` in the
same folder. The interposer's test evidence is
`charge-interposer-test-evidence.md` there.

## 2. Identity

### R1. Image hash
- **Basis:** port plan goal 13 (user, 2026-10-17).
- **Status:** complete.

### R2. Code hash
- **Basis:** port plan goal 13, "The code hash covers the flashed image, not
  the ELF" (user, 2026-10-17). The ELF's debug sections, never flashed,
  differ between host OSes because each host's prebuilt toolchain records
  its own internal header paths, so the code hash is taken over the `.bin`
  with the host-dependent fields blanked.
- **Context:** interposer test evidence, "Reproducible builds and the
  manifest's git fields", part (b), 2026-10-08. Same commit built on
  NICKY-XPS and on madhouse-debian, six images: equal lengths, differing
  only in 32 bytes at 0xb0 (the descriptor's ELF hash) and the trailing 33
  bytes. Those were PlatformIO/Arduino images, not ESP-IDF 5.4.1 project
  builds.
- **Measurement:** gen-inhibit test evidence, section 13, "What a cross-host IDF 5.4.1 build differs in, for the code-identical clause" (tester, 2026-10-23; run files `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/repro_a24de99/`, `compare_cross_host.json` sha256 `362121ef...`, `compare_madhouse.json` sha256 `1d526a5d...`). `gen-inhibit/main` `a24de99` with
  `CONFIG_APP_REPRODUCIBLE_BUILD` on, built at two paths on madhouse-debian
  and two on NICKY-XPS. Across hosts the `.bin`s have the same length and
  segments and differ in three runs only: the descriptor's ELF hash (32
  bytes at 0xb0), the trailing checksum (1) and the trailing SHA-256 (32),
  all inside the blank list. The ELFs differ only in `.debug_info`,
  `.debug_line` and `.debug_line_str`, as goal 13 expected. A control with
  one line of code duplicated differs throughout the code segment.
  Checked by the reviewer: the code hash as R2 defines it, computed
  independently on the four images, is the same for all four
  (`8f02c415...`) and different for the control.
- **Why version and date/time are blanked too:** they do not differ across
  hosts (the date and time are zero with the reproducible option on). The
  version is blanked so that a commit that changes no code keeps its code
  hash (R3); the date and time cover a build without the option.
- **Status:** complete.

### R3. Evidence cites the code hash
- **Basis:** port plan goal 13 (user, 2026-10-17): a commit that changes no
  code keeps its evidence across branches and merges. gen-inhibit tracker A5
  (user, 2026-10-19): bench results carry to a release build by code hash.
- **Status:** complete.

### R4. The commit enters the image once
- **Basis:** port plan goal 13 (user, 2026-10-17). gen-inhibit's `0x7F3`
  moves to the image-hash prefix with the tooling (user, 2026-10-19, tracker
  A3/A8; plan step 0c).
- **The version string's form** (user, 2026-10-25, in the gen-inhibit
  reviewer's session): ESP-IDF's default, written once here so that both
  projects cite one rule. In ESP-IDF v5.4.1, `tools/cmake/project.cmake`
  takes `PROJECT_VER` from, in order, a set `PROJECT_VER`, `version.txt`,
  `project()`'s `VERSION` argument, then `git describe` with `--always
  --tags --dirty` (`GetGitRevisionDescription.cmake`), and
  `CONFIG_APP_PROJECT_VER_FROM_CONFIG` overrides it. The string is a label
  (R6). A fixed form, the commit cut to a set length, was put to the user
  and not taken: evidence carries by the code hash, which blanks the field
  (R2, R3), and the image hash only has to name a build, which a
  clone-dependent string still does. A rule that no tag is cut on a project
  branch, which would keep the commit in every version string (a build at a
  tagged commit gets the tag name alone), was also put to the user and not
  taken (user, 2026-10-25): tags may later name a project commit, for
  example a development build promoted to a truck build.
- **`project_name`** (user, 2026-10-25, in the gen-inhibit reviewer's
  session): written once here because both projects set it the same way.
  gen-inhibit's spec 12.3 item 3 keeps its variant suffixes (`_man` or
  `_auto`, `_flt`, `_rt`), which its build-output checks read, for
  example to refuse a flight image named for a test-only variant. The
  interposer's spec 8.2 takes `interposer_<variant>` from its step 2a
  (user, 2026-10-25, in the interposer reviewer's session), moving the
  variant tag from the image's file name into the descriptor. The variant
  is the label inside the image; the image hash is its identity (R6).
- **Status:** complete.

### R5. Identity independent of branch, clone path and host OS
- **Basis:** port plan goal 11 (user, 2026-10-17): a feature-branch build
  and a `main` build of the same files get the same identity. Goal 13 for
  the code hash across host OSes. Plan step 0b (user, 2026-10-17): without
  `CONFIG_APP_REPRODUCIBLE_BUILD`, absolute source paths baked in by
  `ESP_ERROR_CHECK` and `assert` give identical code in two clones two code
  hashes.
- **Context:** the interposer's same-host builds at two paths gave identical
  ELF and `.bin` (interposer test evidence, same entry as R2, part (a)).
  PlatformIO, not ESP-IDF 5.4.1.
- **Measurement:** the R2 entry. Same host at two paths: byte-identical,
  `.bin` and ELF, on both hosts. Across hosts: the same code hash.
  On NICKY-XPS the first pair was built with ccache on; rebuilt with it
  off (gen-inhibit test evidence, section 13, "x1 and x2 again with
  ccache off", 2026-10-23), both gave the same bytes.
- **Corrected 2026-10-23 (user):** the first bullet read "the same files ...
  give the same image hash", which R4 makes false across commits. Exposed by
  the implementor's reading on C6f. The basis, goal 11's "same files, same
  identity", is met by the code hash.
- **Narrowed 2026-10-25 (user, in the gen-inhibit reviewer's session):** the
  first bullet read "at any clone path". ESP-IDF's default version string
  (R4) depends on the clone: git lengthens its abbreviation of the commit as
  the clone's object count grows, and a reachable tag prefixes the commit
  (`<tag>-<n>-g<sha>`), while a commit exactly at a tag gets the tag name
  alone. So the image hash is promised across worktrees
  of one clone only; across clones, the code hash. Step 0b's two-path
  rebuild (two worktrees of one clone, gen-inhibit tracker, 2026-10-25) is
  within the narrowed bullet.
- **Status:** complete.

### R6. Identity never rests on the version string or the file name
- **Basis:** user, 2026-10-23 (requirements list review), following goals 11
  and 13: with identity in the image hash and code hash, a name is only a
  label.
- **Exposed by:** gen-inhibit spec 12.3 item 3 records two builds that
  produced new code under an old clean-looking name: 2026-09-29 (five edited
  files rebuilt as `5364648`, overwriting the verified image of that name)
  and 2026-10-13 (an `idf.py build` over edited `.c` files, named `a3394ca`).
- **Status:** complete.

### R7. Component digest
- **Basis:** port plan goal 13 and "The split", Rules (user, 2026-10-17):
  content plus relative paths, never a git ref, so a component that moves
  folder keeps its digest. The documents are left out by name rather than
  code being listed by type (user, 2026-10-23; gen-inhibit tracker D6):
  writing evidence into `VERIFICATION.md` beside the code would otherwise
  change the digest that evidence names, and a new or unexpected file type
  is counted rather than missed.
- **Line endings** (user, 2026-10-23): the digest hashes bytes on disk, so
  line endings change it. Checkouts are pinned to LF by `.gitattributes`
  (`shared/` `440aa80`). A file an editor saves as CRLF still changes the
  digest; that gap is accepted, with no byte check (ruled in the
  methodology owner's session, recorded in the port plan; AGENTS.md "Line
  endings").
- **Edges** (user, 2026-10-24, in the gen-inhibit reviewer's session): a
  `.gitignore` is left out in any folder, not only at the root; only names
  ending `.md`, matched case-sensitively, are documents, so `.markdown` and
  `README.MD` are counted, by the same rule that an unexpected file is
  counted rather than missed; paths use `/` on every host OS, so a pin (R23)
  checks the same on Windows and on Linux. Raised by the gen-inhibit
  tester's R7 cases, C6f part A.
- **Exposed by:** the gen-inhibit reviewer's read of the bench lock spec's
  move to `shared/bench/SPEC.md` (2026-10-23). Under the definition then in
  force, adding `SPEC.md` changed `shared/bench`'s digest, and goal 20 item 6
  puts `VERIFICATION.md` beside the code.
- **Status:** complete.

## 3. Release builds

### R8. Clean worktree
- **Basis:** gen-inhibit spec 12.3 item 3, "Prod builds are built on a clean
  tree" (user, 2026-10-05). Port plan goal 13 (user, 2026-10-17) and Layer 0,
  which keeps `prod_build.py` refusal 1 as a generic mechanism.
- **Why it stays** (user, 2026-10-23, in the gen-inhibit reviewer's session,
  on the reviewer's reading): the original reason was a stale image name
  (R6). With identity no longer in the name, what remains is that the
  record's commit (R13) must describe the image, which holds only if nothing
  outside that commit was built.
- **Extended 2026-10-23 (user):** the checks after the reconfigure and at the
  end follow from the same reason: a reconfigure that rewrites a tracked
  file (`sdkconfig`, `dependencies.lock`), or a HEAD that moves during the
  build, leaves an image the record's commit does not describe. Raised by
  the implementor on C6f; not yet seen on a real build.
- **`sdkconfig` committed** (user, 2026-10-24, in the gen-inhibit reviewer's
  session, option a of two): raised by the interposer reviewer on reading
  R18. R18 compares only the options a profile names and leaves the rest to
  the committed `sdkconfig` through this check, but a project that ignored
  `sdkconfig` would pass it, since git does not report ignored files.
  gen-inhibit commits its `sdkconfig` today; the interposer will in its
  step 2a. It is the file the build used, since IDF lets a build take
  `SDKCONFIG` from elsewhere (the interposer reviewer, on reading this
  clause). That file is `project_description.json`'s `config_file`, IDF's
  `SDKCONFIG` build property: it covers `-D SDKCONFIG`, a preset, a
  `set(SDKCONFIG)` in the project's `CMakeLists.txt` and the default
  `<project>/sdkconfig` (IDF v5.4.1 `tools/cmake/project.cmake`,
  `build.cmake`, `project_description.json.in`). `CMakeCache.txt`, named
  first, holds `SDKCONFIG` only when it was given with `-D` or a preset
  (the implementor, on reading the clause).
- **Not covered, accepted** (user, 2026-10-23): a file whose CRLF bytes are
  what git last checked out or added passes the clean check, while the
  build and the R7 digest see the CRLF bytes. No byte check is added (see
  R7). Corrected 2026-10-24: this note first said any CRLF save passes. The
  gen-inhibit tester measured on git 2.52.0.windows.1 and 2.47.3 that a CRLF
  save over a file committed as LF shows as modified, so R8 refuses it
  (shared/tools VERIFICATION.md, "R9, R13 fixes, and R17's shared/ roots";
  AGENTS.md corrected by the methodology owner, 2026-10-24).
- **Status:** complete.

### R9. Reconfigure first
- **Basis:** gen-inhibit spec 12.3 item 3 (user, 2026-10-05): ESP-IDF fixes
  the embedded version when CMake configures, not when it builds. Under R4
  the commit is in that version field, so a build without a reconfigure can
  carry an old commit. Port plan Layer 0 keeps refusal 4.
- **Changed 2026-10-23 (user): ccache off.** The implementor's end-to-end
  self-check on `3b6da97` showed `idf.py`'s CMake call carrying
  `-DCCACHE_ENABLE=1` on NICKY-XPS. Read in ESP-IDF v5.4.1: `idf.py`'s
  `--ccache/--no-ccache` option defaults off but reads `IDF_CCACHE_ENABLE`
  (`tools/idf_py_actions/core_ext.py`), and the IDF export sets
  `IDF_CCACHE_ENABLE=1` wherever the ccache tool is installed (its
  `export_vars` in `tools/tools.json`). So whether a release build used the
  cache depended on how the host's IDF was installed. With the cache on, the
  R19 rebuild is served from the first build's objects and shows nothing
  about the compile. A release build gains little from the cache: every
  source includes `sdkconfig.h`, so bench and dev builds with other options
  leave nothing a release build can reuse. Ruled off (option A of three).
- **The fixed `SOURCE_DATE_EPOCH`:** see R19's readings (user, 2026-10-24).
- **Status:** complete.

### R10. Downloaded components checked after the reconfigure
- **Basis:** gen-inhibit spec 12.3 item 3 (user, 2026-10-05): the
  `CHECKSUMS.json` and `dependencies.lock` checks. Port plan Layer 0 keeps
  refusal 3. "After the reconfigure" is the correction below.
- **Exposed by:** gen-inhibit test evidence, 12.3, on `e3d9437`, NOT AS
  SPECIFIED (reviewer's ruling): on a fresh clone the component check ran
  before the reconfigure fetched `espressif__mdns`, reported "managed
  components: none on disk", and no row covered the component in the image.
  Fixed in `2df3c17`.
- **Changed 2026-10-23 (user), option (a):** as first written, R10 required
  a `CHECKSUMS.json` for every component, which the Component Manager
  supplies only for registry downloads, so a shared component pinned by git
  (port plan goal 12) would have refused every release build. Raised by the
  implementor on C6f. From the Component Manager's source (2.5.2, in
  IDF 5.4.1's environment; the implementor's read, 2026-10-23):
  - `dependencies.lock` carries a `component_hash` for every downloaded
    component, git sources included;
  - strict mode (`IDF_COMPONENT_STRICT_CHECKSUM`, off by default) re-hashes
    each component's folder at configure time and stops the configure on a
    changed file; for a git component that re-hash also catches an added
    file;
  - `IDF_COMPONENT_OVERWRITE_MANAGED_COMPONENTS` skips the check entirely;
  - for a component with a `CHECKSUMS.json`, strict mode checks only the
    files it lists and never looks for an unlisted one, so R10's own
    unlisted-file check stays.
  Using the manager's own check, rather than recomputing its hash, avoids
  keeping a copy of its file rules in step with it.
- **No lock** (user, 2026-10-24, in the gen-inhibit reviewer's session,
  option A of two): a release build without `dependencies.lock` is refused,
  rather than allowed when nothing is downloaded. One rule with no carve-out,
  and it fails closed: a project that does download components but has no
  lock after the reconfigure is caught by the same refusal. The cost is a
  one-line manifest in a project that downloads nothing. gen-inhibit has a
  lock today. The interposer, still PlatformIO, has none of its own (the
  manifest and lock at its branch's root are upstream WiCAN's); its IDF
  project gets a manifest, and so a lock, at port stage (a) step 2a, where
  arduino-esp32 comes from the Component Manager. Raised by the gen-inhibit tester's R22 case (IDF's
  hello_world, C6f part A), which was refused with a raw file-not-found
  error.
- **Status:** complete.

### R11. The image is the one the build names
- **Basis:** reviewer's reading of gen-inhibit spec 12.3 item 3, 2026-10-21
  (tracker finding row): the image is `build/project_description.json`'s
  `app_bin`, and a missing name or file is a refusal.
- **Exposed by:** `prod_build.py` recorded the newest `.bin` in `build/`,
  not the image the build named. Fixed in `95aa77a`, closed on tester
  evidence 2026-10-21.
- **Status:** complete.

### R12. Reproducible build on
- **Basis:** gen-inhibit tracker A4 (user, 2026-10-19); port plan step 0b
  (user, 2026-10-17), for the reason under R5.
- **Measurement, in part:** the R2 entry shows the second half of step 0b's
  acceptance, a rebuild from a second clone path giving the same code hash,
  with the option on.
- **Status:** basis pending for the first half: a comparison of builds with
  the option off and on, showing that only path strings and the build date
  and time change. Every build in the R2 entry had the option on.

### R13. One record per release build
- **Basis:** port plan goal 13 (user, 2026-10-17): one record named by the
  image hash, with the `.elf` and `.bin`, under `$REVERSE_IT_DIR`, stopping
  if it is unset; never edited, so SeaDrive has nothing to conflict on. The
  record's contents: user, 2026-10-23 (requirements list review). The pin
  fields follow goal 20 item 8 (user, 2026-10-23): reproducing a result
  needs the project commit and each pinned shared version. Recording the
  build tooling's own pin: user, 2026-10-23, in the gen-inhibit reviewer's
  session.
- **The lock's hash is taken LF-normalised** (user, 2026-10-24, in the
  gen-inhibit reviewer's session, option A of three, per AGENTS.md "Line
  endings"). On NICKY-XPS two release builds of one commit to one image
  recorded two lock hashes (the gen-inhibit tester's C6f follow-ups, an
  observation): the Component Manager writes `dependencies.lock` as CRLF on
  Windows, and that CRLF lock was what git had last added, so `git status`
  showed it clean (the tester's "R9, R13 fixes" entry, 2026-10-24, which
  corrects the first account, that the reconfigure rewrites it unseen).
  Hashing the committed blob instead would tie
  the record to git, which R7 avoids; hashing the bytes on disk would let
  records differ by host.
- **Status:** complete.

### R14. Bench and dev builds exempt
- **Basis:** gen-inhibit spec 12.3 item 3 (user, 2026-10-07): a bench image
  is normally built on a dirty tree, so applying the release rules to it
  would fire on every bench run and mean nothing. gen-inhibit tracker A5
  (user, 2026-10-19) for carrying results by code hash.
- **Status:** complete.

### R15. Bench runs log the code hash
- **Basis:** gen-inhibit tracker A5 (user, 2026-10-19); port plan, shared
  bench toolkit item 8 (user, 2026-10-17).
- **Status:** complete.

### R16. Shared code only at a pinned tag
- **Basis:** port plan goal 12 (user, 2026-10-17): every shared component
  pinned at a tag, `override_path` only while working on a shared change.
  Goal 20 item 8 (user, 2026-10-23, confirmed in the gen-inhibit reviewer's
  session): a pin may name a topic commit only on a project's topic branch,
  never on its main branch. The refusal of a build tooling pin that names a
  commit: user, 2026-10-23, in the gen-inhibit reviewer's session.
- **Status:** complete.

### R24. A release build's environment
- **Basis:** user, 2026-10-24, in the gen-inhibit reviewer's session: "we
  should constrain how env vars can affect the builds", as a list of what
  passes rather than of what is refused, which would miss a route nobody
  listed (option b of two), for release builds only.
- **Exposed by:** the R20 readings, which asked how environment variables
  reach a build. Read by the gen-inhibit reviewer in ESP-IDF v5.4.1:
  `tools/cmake/project.cmake` adds `EXTRA_CFLAGS`, `EXTRA_CXXFLAGS` and
  `EXTRA_CPPFLAGS` to every compile, and its comment there says the
  environment's `CFLAGS`, `CXXFLAGS` and `CPPFLAGS` also reach builds (not
  tried). The release script passed its caller's environment through,
  refusing only R10's overwrite setting and a cache left on, so a `-D` for a
  test-only switch could reach a release build with no file changed.
- **Status:** basis complete. The list itself is the implementor's to find,
  for each host OS.

## 4. Checks

### R17. Scope
- **Basis:** port plan goal 13 (user, 2026-10-17). The release-build clause
  (shared code only from `managed_components/`): user, 2026-10-23, in goal
  13, confirmed in the gen-inhibit reviewer's session.
- **Readings** (user, 2026-10-24, in the gen-inhibit reviewer's session),
  raised by the gen-inhibit tester before writing the check:
  - IDF includes the chosen toolchain's folder and `CMAKE_ROOT`, both from
    the build's own record (option a of three). Measured on a gen-inhibit
    release build on NICKY-XPS: of 5400 files read, 258 are under the
    toolchain's folder (newlib and gcc headers and libraries) and 42 under
    CMake's modules, both outside `IDF_PATH`; IDF_PATH alone would fail
    every build. All of the IDF tools folder would also accept another
    toolchain or ccache's folder.
  - "At the version" is the manifest `version` (option a of three). Content
    is R10's, for release builds; R14 frees bench and dev builds from R10.
  - Bench and dev `shared/` reads may come from any worktree of this repo or
    a `../shared-<tag>/` checkout (option a of two), so a bench build can test
    a shared change through `override_path` to the worktree it is made in.
- **Status:** complete.

### R18. Options
- **Basis:** port plan goal 13 (user, 2026-10-17).
- **Readings** (user, 2026-10-24, in the gen-inhibit reviewer's session),
  raised by the gen-inhibit tester before writing the check:
  - The build's options are its `sdkconfig.json` in each CMake build folder
    (option a of three; compiler flags follow from it, and `-D` switches are
    R20's).
  - "Match" means the options the profile names (option a of two). A stored
    copy of the whole `sdkconfig` was turned down: git is the reference
    copy, and R8 already holds the build to the commit.
  - gen-inhibit's release profile is the first five settings of its spec
    12.3 item 3, the build-output checks (rollback on; secure boot and
    flash encryption unset; virtual eFuses off; `CONFIG_TWAI_ISR_IN_IRAM`
    unset; the watchdogs as its spec 9.1 records them), plus R12.
  - It is a check on the finished release build, run by any session, not a
    refusal in the release build (option a of three), and only for release
    builds. Port plan step 0b's "the options check refuses a release build"
    is read as this check failing it.
- **Status:** complete.

### R19. Reproducible
- **Basis:** port plan goal 13 (user, 2026-10-17).
- **Measurement:** the R2 entry. The madhouse-debian builds, run twice, gave
  the same sha256s both times, and the builds at two paths on each host are
  byte-identical.
  On NICKY-XPS the first pair was built with ccache on; rebuilt with it
  off (gen-inhibit test evidence, section 13, "x1 and x2 again with
  ccache off", 2026-10-23), both gave the same bytes.
- **Readings** (user, 2026-10-24, in the gen-inhibit reviewer's session),
  raised by the gen-inhibit tester before writing the test:
  - A test of the tooling, rerun when a change could reach the image, not a
    check run on each release build. The test makes its own two builds, so
    whether a check only compares or also builds is not a question for it.
  - "The same tree" is one clean commit in any two folders on one machine
    (option b of two; the other was the same folder rebuilt). With
    reproducible builds on, ESP-IDF maps the project, IDF, build and
    toolchain paths to placeholders (its guide, "Reproducible Builds"), and
    the R2 measurement's builds at two paths were byte-identical.
  - A project's reproducible-build setting is not this test's concern. For
    gen-inhibit it is in the release profile
    (`CONFIG_APP_REPRODUCIBLE_BUILD`), so R18 checks it on each release
    build. gen-inhibit fails R18 until port plan step 0b turns it on.
  - **The compile date: a fixed `SOURCE_DATE_EPOCH`** (R9), a stopgap until
    no project compiles the Arduino core (user, 2026-10-24). ESP-IDF's
    guide says source that uses `__DATE__` or `__TIME__` makes a build
    unreproducible whatever the setting. The user first accepted that as a
    known gap, since gen-inhibit's `main/` and `components/` use neither
    macro (option a of three; the others were a rerun before each truck
    flash and a text check beside R20). The interposer reviewer's read then
    showed it live: from port step 2a the interposer compiles the
    arduino-esp32 core, which uses both (`cores/esp32/chip-debug-report.cpp`
    and `cores/esp32/firmware_msc_fat.c`, read in 3.2.1), and its PlatformIO
    build already fixes `SOURCE_DATE_EPOCH` for this
    (`charge-interposer/firmware/reproducible.py`: "the same sources built
    a minute apart differed"). GCC takes both macros from
    `SOURCE_DATE_EPOCH` when it is set; ESP-IDF v5.4.1's `tools/cmake` never
    sets it. The other options were the commit's own time, which would give
    two commits with identical files two different dates and so break the
    code-hash carry-over (R3, R5), a value in each project's data, and
    keeping the gap. Because gcc applies it to every `__DATE__`, it also
    fixes the date and time in the app descriptor when
    `CONFIG_APP_COMPILE_TIME_DATE` is on.
  - **Rerun on the Python environment too** (user, 2026-10-24, raised by the
    interposer reviewer): esptool writes the `.bin`, and ESP-IDF pins it only
    to a range (`esptool~=4.8` in v5.4's `espidf.constraints.v5.4.txt`), so
    reinstalling the environment can move it within one ESP-IDF version.
  - **Once per project** (user, 2026-10-24, raised by the interposer
    reviewer): causes in a project's own source, like the date above, show
    only on that project.
- **Status:** complete.

### R20. Build switches
- **Basis:** gen-inhibit spec 12.3 item 3, "Every build-time switch is
  classified" (user, 2026-10-13; the stock-off class 2026-10-16). Port plan
  Layer 0 (user, 2026-10-17) keeps the mechanism, with the tables as
  per-project data.
- **Exposed by:** the restart trial's linker wrap, a switch that lived in a
  CMake file with no `#if` (recorded in spec 12.3 item 3, 2026-10-13). It is
  why CMake files are in scope and are taken from the build's own record
  rather than a list kept by hand.
- **Readings** (user, 2026-10-24, in the gen-inhibit reviewer's session),
  raised by the gen-inhibit tester before writing the shared check:
  - The source scanned is what the release build compiled, from its own
    record (option a of three; the others were folders named in the
    project's data, and the project root minus `build/`,
    `managed_components/` and `test/`). A file the release build does not
    compile cannot reach its image, and what decides whether it is compiled
    is in the CMake files, which are scanned. On a gen-inhibit fixture
    release build the record names 44 of the 63 tracked files in `main/` and
    `components/` (the gen-inhibit tester, an observation); the others are
    stock-off sources and headers nothing includes, and their entries leave
    the table.
  - A shared firmware component's switches are its owner's to classify, in
    its own table, applied in each release build that uses it (option a of
    three; the others were listing them in each project's table, in full or
    only for components the project names as shared).
  - The scan rules carry over from gen-inhibit spec 12.3 item 3 and its
    check: the scan is textual and over-inclusive (every identifier in an
    `#if` counts, function-like macros included); a header guard is
    recognised only in its exact shape (first directive `#ifndef X`, then
    `#define X` with no value); "sets itself" is read narrowly (only a
    file-scope `set(VAR value)` before the test, with no `CACHE`), every
    `$ENV{}` is a build option, and CMake's built-ins are the recorded
    `cmake`'s `--help-variable-list`; and a listed symbol no longer tested
    fails, so a stale entry cannot pass as coverage.
  - Refusal rows are data where they can be, a named project test otherwise
    (option c of three). On gen-inhibit's rows only the RealDash one (no
    boot-time bus enable in `app_main`, an objdump call-site check) needs a
    project test.
  - The switch-on proof is test evidence, made once per row and made again
    on a change (option a of two; the other built every switch-on variant on
    every run).
  - Release builds only (option a of two): "release builds are the only ones
    that really need the check anyways" (user).
  - "Judges the image and the ELF" sets the build's outputs against the
    flag; it does not require a rule on each (gen-inhibit reviewer,
    2026-10-24). The image embeds the ELF's SHA-256 (R1), so an ELF rule
    judges the code in that image. gen-inhibit's RealDash row, ELF only,
    closed under its spec 12.3 item 3.
  - A compile definition a project's CMake sets (for example a `-D` on a
    target) is not listed for itself; it is listed only when the project's
    own compiled source tests it, which the C scan finds (user, 2026-10-24,
    option b of two, raised by the interposer reviewer; the other listed
    every definition the project's CMake sets on a target in its table).
- **Build switches are CMake build options:** user, 2026-10-25, in the
  interposer reviewer's session, for every project and every shared
  firmware component. Raised by the interposer's switch table for port step
  2a: its PlatformIO variants (bench, witness, flight and the separate
  test images) need a new way to be chosen under ESP-IDF. It replaces the
  open item that asked for R20 to be extended if the port turned switches
  into `CONFIG_*` options (gen-inhibit spec 12.3 item 3), since that case
  no longer arises.
  - **Context:** gen-inhibit already chooses its variants this way: its
    arm, fault-injection, restart-test and TX-abort builds and its four
    stock-off switches are CMake options its CMake files turn into compile
    definitions, and its `main/Kconfig.projbuild` holds only upstream
    WiCAN's example menu (gen-inhibit/main `0c707e9`, read 2026-10-25).
- **Status:** complete.

### R21. Board data
- **Basis:** port plan Layer 0 (user, 2026-10-17): the partition row with
  its reference and the slot and flash-size rows become board data, and the
  tool prefix comes from `IDF_TARGET`. Goal 5 (user, 2026-10-16/17): the
  build checks take the toolchain from the board's chip, not from the C3's
  RISC-V tool names. gen-inhibit spec 12.3 item 3 for the WiCAN's values
  (partition table byte-identical to stock, 1,781,760-byte slot).
- **Flash size** (user, 2026-10-24, option B of three, in the gen-inhibit
  reviewer's session): checked against the board's. No check had it until
  then. A flash size smaller than the chip already fails the build, since
  the partition table no longer fits; a larger one builds and passes the
  partition check. The other options were leaving it out, and naming it in
  each project's release profile, which would put a board fact in project
  data (R22).
- **Readings** (gen-inhibit reviewer, 2026-10-24, raised by the gen-inhibit
  tester before writing the check, and shown to the user):
  - The table compared is the build's own `partition-table.bin`, byte for
    byte, as gen-inhibit's check does today.
  - The reference is a `.bin` named by path in the board data, with no
    stored hash beside it: git is the reference copy (user, 2026-10-24,
    R18).
  - The slot is the smallest app partition of the board's reference table,
    not a separate number, and the image fits when its `.bin` is no larger.
    For the WiCAN, `ota_0` and `ota_1` are both 1,781,760 bytes, today's
    number.
  - The tool prefix is a property of the checks (goal 5): no check
    hard-codes a tool name, and each tool a check runs is named from the
    board's `IDF_TARGET`, by ESP-IDF's own per-target files. A build whose
    target is not the board's fails.
  - The first reading put the data of a board used by one project in that
    project's `vtrux_checks.json`. R22 superseded it (user, 2026-10-25):
    every board's data is under `shared/boards/`. A board's data reaches a release
    build only inside the board component pinned in `dependencies.lock`,
    under `managed_components/` (R17, goal 20).
- **Which board** (user, 2026-10-26, in the gen-inhibit reviewer's session,
  proposed by the charge-interposer reviewer): the project's one
  `shared/boards/*` dependency, and nothing else, so the fact is recorded
  once (`shared/boards/VALIDATION.md` B2).
  - Release builds only, like R18 and R20. The bootloader's contents are
    not compared, only the partition table.
- **Status:** complete. The board values are in `shared/boards/VALIDATION.md`,
  section 2 for the WiCAN and section 3 for the X2 (2026-10-27).

### R29. Board settings
- **Basis:** user, 2026-10-26, in the gen-inhibit reviewer's session: a
  board fixes some settings, and lists others that every project building
  for it must set itself, such as the console, the watchdogs and Bluetooth
  (`shared/boards/VALIDATION.md` B3). Checked in the options check, as R26
  to R28 are, so a committed `sdkconfig` cannot drift from its board, and a
  project cannot leave a required setting to a default.
- **Status:** complete.

### R30. Pins stay in the board
- **Basis:** user, 2026-10-26, in the gen-inhibit reviewer's session: pins
  in a separate header, with a listed exception for stock files that still
  use them (`shared/boards/VALIDATION.md` B6). The dependency records are
  the ones R17 reads, so the check sees every header an object read.
- **Status:** complete.

### R26. No eFuse-burning features in a release build
- **Basis:** user, 2026-10-25, in the interposer reviewer's session: the
  rule is common to every project, so it is written once here rather than
  in each project's release profile. Secure boot and flash encryption burn
  eFuses on the first boot of an image that enables them, which cannot be
  undone on a vehicle's board. Virtual eFuses are a test feature, which
  keeps eFuse state in memory or flash instead of burning the eFuses.
- **Context:** gen-inhibit's spec names three of the four, in section 2
  ("Never burn eFuses") and 12.3 item 3, settings 2 and 3, and its release
  profile holds all four (R18 readings above). The interposer's
  Arduino build has all four off: the prebuilt arduino-esp32 libraries'
  `esp32s3/sdkconfig`, as installed for its pinned PlatformIO platform
  on NICKY-XPS (read 2026-10-25). It had no release profile before port
  step 2a.
- **Status:** complete.

### R27. Each project sets its own FreeRTOS tick rate
- **Basis:** user, 2026-10-25, in the interposer reviewer's session: no
  shared tick rate, but every project must define one. A project's
  timings are written against its tick, so the value is a project
  requirement, and a profile that leaves it out would let the committed
  `sdkconfig` change it unchecked.
- **Context:** both projects run at 1000 Hz today: gen-inhibit's committed
  `sdkconfig`, and the prebuilt arduino-esp32 libraries' `esp32s3/sdkconfig`
  for the interposer (read 2026-10-25). The interposer's spec section 9
  sets 1000 Hz (port plan goal 3, user, 2026-10-22). gen-inhibit's spec
  names it in 12.3 item 3, setting 6 (1000 Hz, user, 2026-10-25), and its
  release profile does too (`vtrux_checks.json`, from gen-inhibit
  `064d73b`).
- **Status:** complete.

### R28. One set of log levels for every release build
- **Basis:** user, 2026-10-25, in the interposer reviewer's session:
  log levels made common between the projects "in a sensible way for
  each", as the same mechanism with each project's own values. Raised by
  the interposer's review of its PlatformIO `-D` lines for port step 2a,
  where `CORE_DEBUG_LEVEL` set the Arduino core's level per variant. A
  level set in the `sdkconfig` is seen by the options check (R18); a `-D`
  is not, since R20 does not list compile definitions for themselves
  (ruling b, 2026-10-24).
- **Context** (read 2026-10-25): both projects compile framework logs in
  and silence them at run time today. gen-inhibit's committed `sdkconfig`
  has ESP-IDF's default and maximum levels at info, and `app_main` calls
  `esp_log_level_set("*", ESP_LOG_NONE)` (upstream WiCAN code); its spec
  sets no level. The interposer's flight build compiles the Arduino
  core's logs out with `CORE_DEBUG_LEVEL=0` and silences ESP-IDF's prebuilt
  logs (error level) in `setup()`. A run-time silence misses what prints
  before the call, and arduino-esp32 3.2.1's `isr_log_*` macros print with
  `ets_printf`, ignoring the run-time level. The interposer's spec 8.3 sets
  none for ESP-IDF and the Arduino core and error for the bootloader, from
  its step 2a (user, 2026-10-25).
- **Made common 2026-10-25** (user, in the gen-inhibit reviewer's session):
  one value set for every project's release builds, the interposer's,
  where the first wording left each project to its own. Test-only and
  development builds may raise theirs, so gen-inhibit's OTA rollback
  rehearsal, which reads bootloader and app log lines, keeps them on its
  fault-injection build. Its other console tools match the ROM's reset
  banner and panic output, which ESP-IDF's log levels do not silence.
  gen-inhibit adopts the values at its own port step after 1a (which brings
  the `sdkconfig` layering its test builds need), accepted by a full suite
  run; until then its release builds do not meet R28, a known anomaly of
  the port. The value names are ESP-IDF v5.4.1's (`components/log/Kconfig.level`,
  `components/bootloader/Kconfig.log`).
- **Status:** complete.

## 5. Data, not code

### R22
- **Basis:** port plan goal 13 (user, 2026-10-17): the shared tooling knows
  nothing about Arduino, so removing Arduino in the interposer's stage (b)
  changes data, not tooling. Layer 0 for the per-project and per-board
  split. Goal 3 (user, 2026-10-22): the interposer has its own project
  root. Where board data lives: user, 2026-10-23, in the gen-inhibit
  reviewer's session; **changed 2026-10-25** (user, same session): every
  board under `shared/boards/`, as the port plan's Layer 1 has it, the
  WiCAN's included. The 2026-10-23 rule kept a board's data in a project's
  root when only that project builds for it, which put the WiCAN's apart
  from the plan's `shared/boards/wican_v3/`; the interposer reviewer found
  the conflict. The WiCAN's board config sits beside the stock WiCAN code
  that moves to `shared/` at step 1a and is written for that hardware.
  gen-inhibit's board data stays in its `vtrux_checks.json` until step 1a
  moves it. The X2 jammer is shared test tooling, alongside the
  bench toolkit, not part of the shared firmware code, and it builds for the
  X2 as the interposer does.
- **Status:** complete.

## 6. Running the tooling

### R23. From a read-only checkout at the pin
- **Basis:** port plan goal 20 item 8 (user, 2026-10-23): shared Python
  tooling is used from a separate checkout at the pinned tag, with the
  harness checking the checkout's digest against the pin. Applied to the
  build tooling: user, 2026-10-23, in the gen-inhibit reviewer's session.
- **Status:** complete.

### R25. Tags on `shared/main`
- **Basis:** port plan goal 12 (user, 2026-10-17) pins shared code at tags,
  and goal 20 item 7 left the naming scheme open. Ruled by the user,
  2026-10-24, in the gen-inhibit reviewer's session, on four questions:
  - One sequence for all of `shared/main` (option A of two; the other was a
    sequence per component). Each project still chooses a tag per
    component, and the one checkout per tag (R23) needs no change.
  - The date as the name (option A of two; the other was a counter, `v1`,
    `v2`, which reads like upstream WiCAN's version numbers).
  - Cut only when a project is about to pin it (option A of two; the other
    was a tag after every merge), so every tag is one a project uses. By
    the reviewer of the project about to pin it, since a commit may hold
    merges by both reviewers (the interposer reviewer's reading of the
    first wording, "the reviewer who merged the change").
  - Never moved or deleted: pins and run records name tags, and a pin's
    digest (R7) would refuse a moved tag anyway.
- **Status:** complete.
