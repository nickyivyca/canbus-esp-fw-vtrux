# Shared build tooling: specification

**Status: approved by the user 2026-10-23, in the gen-inhibit reviewer's
session.** Drafted that day by the gen-inhibit reviewer, from the
requirements list the user reviewed. As a shared
component's spec it follows port plan goal 20: the reviewer whose project
needs the change drafts it, the user rules in that reviewer's session, and the
other project's reviewer (charge interposer) reads it before it merges into
`shared/main`.

- **Why** each requirement is written as it is: `VALIDATION.md`, beside this
  file. Some requirements have no basis yet; that file marks them.
- **Test evidence** that the tooling meets each requirement:
  `VERIFICATION.md`. Evidence recorded against the tooling this replaces
  verified that tooling, not this one, so none is carried over.
- **Sources:** the ESP shared-code port plan,
  `$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`
  (goals 6, 11, 12, 13 and 20, and Layer 0 of "The split"), and gen-inhibit's
  spec 12.3 item 3,
  `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md`.

Requirements are numbered R1 to R30. The numbers are stable: the two evidence
documents cite them.

## Terms

- **Release build:** an image meant for a vehicle. gen-inhibit's spec calls
  it a prod build.
- **Bench build, dev build:** any other image.
- **Image hash:** the SHA-256 of the ELF, as ESP-IDF embeds it in the image.
- **Code hash:** defined in R2.
- **Component digest:** defined in R7.
- **Pin:** a project's committed record of which version of a shared
  component it uses (goal 12 for firmware components, goal 20 item 8 for
  shared Python tooling).

## 1. Scope

The build tooling lives in `shared/tools/` and never enters a firmware image.
It covers:
- the build script and the record writer;
- the two build checks (scope, options), the build-switch check and the
  reproducible test;
- the image-reading library: the app descriptor parser and the image hash
  read from a `.bin`;
- the comparison tool of goal 6, which the tester owns. Its requirements are
  not written yet (section 8).

It sits outside the layer table of goal 6: a change to it voids no evidence
unless the image changes, which the reproducible test (R19) shows.

## 2. Identity

- **R1. Image hash.** The image hash is the ELF's SHA-256 that ESP-IDF
  embeds, read from the `.bin`.
- **R2. Code hash.** The code hash is the SHA-256 of the `.bin` with these
  blanked: the app descriptor's version, date and time, and ELF-hash fields,
  and the image's trailing checksum and SHA-256. The image is read, never
  patched.
- **R3. Evidence cites the code hash.** Bench results carry to a release
  build with the same code hash.
- **R4. The commit enters the image once,** in the app descriptor's version
  field. Nothing else compiles it in. The field holds ESP-IDF's default
  version string, `git describe --always --tags --dirty` run at configure:
  a project sets no `PROJECT_VER`, no `version.txt` and no `VERSION`
  argument to `project()`, and leaves `CONFIG_APP_PROJECT_VER_FROM_CONFIG`
  off. `project_name` names the project and its build variant, is fixed
  for each build configuration, and carries no commit; each project's spec
  lists its variants.
- **R5. Identity does not depend on branch, clone path or host OS:**
  - the same commit built on the same host OS, on any branch, in any
    worktree of one clone, gives the same image hash. The image hash
    includes the version string (R4), so two commits with identical files
    give two image hashes, and two clones of one commit can too, since the
    version string depends on the clone (how far git abbreviates the
    commit, and which tags it holds). The code hash is what stays the same
    (R2, R3);
  - the same files built on either host OS give the same code hash. The
    image hash may differ between host OSes.
- **R6. Identity never rests on the version string or the file name.** Both
  are labels. No check compares a name against the tree.
- **R7. Component digest.** A component's digest covers the content of every
  file under the component's root, with each file's path relative to that
  root, written with `/` on every host OS, so one tree gives one digest on
  Windows and on Linux. It leaves out, in any folder, files whose name ends
  `.md` and files named `.gitignore`, matched case-sensitively. Every other
  file is counted, `.markdown` and `README.MD` included. It never covers a
  git ref, so a component taken from a tag or from a tree gives the same
  digest.

## 3. Release builds

- **R8. Clean worktree.** A release build is refused unless the worktree has
  no uncommitted changes and no untracked files when it starts, and again
  after the reconfigure (R9), before anything is compiled. It is also
  refused if HEAD or the worktree has changed by the time the build
  finishes. And it is refused unless the `sdkconfig` the build used
  (`config_file` in its `project_description.json`) is committed in the
  project:
  tracked by git and not ignored, so that this check sees any change to it.
  R18 leaves the options a profile does not name to it.
- **R9. Reconfigure first.** A release build runs `idf.py reconfigure`, then
  builds, in one run, with the compiler cache (ccache) off whatever the
  environment sets. A build whose configuration still has it on is refused.
  It also runs with `SOURCE_DATE_EPOCH` set to one fixed value, the same for
  every project and commit, so that source using `__DATE__` or `__TIME__`
  builds the same each time (R19). This is a stopgap for the Arduino core
  the interposer compiles, to be removed when no project compiles it.
- **R10. Downloaded components checked after the reconfigure.** The
  reconfigure runs with the Component Manager's strict checksum mode on
  (`IDF_COMPONENT_STRICT_CHECKSUM`), and a release build is refused if
  anything is set that lets the manager overwrite managed components; a
  component whose files differ from its recorded hash then stops the
  configure. After the reconfigure, every component in `managed_components/`
  has a `.component_hash` equal to the `component_hash` `dependencies.lock`
  gives it, and one that came with a `CHECKSUMS.json` (a registry download)
  matches it file for file, with no file it does not list. A mismatch is a
  refusal. A release build is also refused if the project has no
  `dependencies.lock` after the reconfigure, even one that downloads nothing:
  such a project carries a manifest so that it has a lock.
- **R11. The image is the one the build names.** The image recorded is the
  one `build/project_description.json` names as `app_bin`. A missing name or
  a missing file is a refusal.
- **R12. Reproducible build on.** The project's release profile has
  `CONFIG_APP_REPRODUCIBLE_BUILD` on.
- **R13. One record per release build.** Each release build writes one
  record, named by its image hash, with its `.elf` and `.bin` beside it, to
  `firmware/builds/<project>/` under `$REVERSE_IT_DIR`. The build stops if
  `$REVERSE_IT_DIR` is unset. A record is never edited after it is written.
  The record holds:
  - the commit;
  - the image hash, the code hash and the image size;
  - the host OS, the toolchain version and the IDF version;
  - the digest of each component the build used, and the hash of
    `dependencies.lock`, taken with its line endings normalised to LF, so
    one lock gives one hash on any host and in any checkout;
  - the tag of each pinned shared firmware component;
  - the build tooling's own pin: its tag, commit and digest.
- **R14. Bench and dev builds** need not meet R8 to R11 or R16. Their results
  carry to a release build by code hash (R3).
- **R15. Bench runs log the code hash** of each image they ran. The image
  library provides it; the bench toolkit's record writer logs it.
- **R16. Shared code only at a pinned tag.** A release build is refused if:
  - any shared firmware component is taken from a local folder
    (`override_path`) rather than its pinned version; or
  - the build tooling's own pin names a commit rather than a tag.
- **R24. A release build's environment.** A release build runs ESP-IDF in
  an environment the tooling builds from an empty one. It passes through
  from its caller only the variables on its own list, which holds only what
  ESP-IDF needs to find itself and its tools and what the host OS needs to
  run programs, and it adds its own settings (R9, R10). It prints the names
  it dropped, and the record lists the names it passed. Bench and dev builds
  are not constrained.

## 4. Checks

- **R17. Scope.** From ninja's dependency records, every file the build read
  comes from the project root, IDF or `managed_components/`, and each
  managed component is at the version `dependencies.lock` records: its own
  manifest's `version` is the lock's. For a component taken from git, the
  lock records what git resolved, not a version: the commit, or an
  annotated tag naming it. That commit is the one the tag the project pins
  names, and the component's manifest has no `version` of its own. IDF here is `IDF_PATH` plus exactly
  two folders named by the build's own record: the install folder of the
  toolchain whose compiler CMake chose (`CMake<LANG>Compiler.cmake`), and
  CMake's own `CMAKE_ROOT` (`CMakeCache.txt`). Nothing else beside them in
  the IDF tools folder counts. The build may also read any file in the git
  folder of the repository that holds the project root, which IDF reads to
  keep the version (R4) current: for a linked worktree, both its own git
  folder and the clone's. For a bench or dev build, files under
  `shared/` may also be read directly: the `shared/` folder of any worktree
  of this repo, or a `../shared-<tag>/` checkout. For a release build they
  may not: shared code counts only from `managed_components/`, so a release
  build that reads another worktree fails.
- **R18. Options.** A release build's options match the project's release
  profile. The build's options are the configuration it compiled with,
  `config/sdkconfig.json` in every CMake build folder, the app's and the
  bootloader's. The release profile is per-project data (R22) naming
  options and their values, each value citing the line of the project's
  spec it comes from. Every option the profile names has the profile's
  value, "unset" meaning absent or off; options it does not name are free.
  A build folder holds only the options of the components it builds, so
  the bootloader's lacks many of the app's. An option the profile sets to a
  value must be in the app's folder with that value, and in any other
  folder has that value where the folder has the option at all.
  The whole `sdkconfig` is not compared: it is committed, and R8 holds the
  build to the commit. Like the other checks here, it runs on the finished
  build and is not a refusal in the release build; any session may run it.
  Bench and dev builds are not checked.
- **R19. Reproducible.** One clean commit, built twice on the same machine
  in two different folders, each time by the release build script, gives
  the same image hash both times. This is a test of the tooling, not a check
  on each release build. It is rerun whenever something that can reach the
  image changes: the build script or the record writer, the image-reading
  library, the ESP-IDF version or its Python environment (esptool, which
  writes the `.bin`, included), or the compiler, CMake or Ninja version.
  Each project also runs it once when it first builds with this tooling,
  since some causes show only in a project's own source.
- **R20. Build switches.** Every preprocessor symbol tested by the project's
  source that the release build compiled (other than ESP-IDF's `CONFIG_*`
  and header guards), and every build option tested by a CMake file the
  build reads from the repo, is listed in the project's table. Both sets of
  files are taken from the build's own record of the files it read. A build
  option is a variable that can be set from outside the file that tests it:
  the CMake cache, a `-D`, or the environment. A variable the file sets
  itself before testing it, and CMake's built-in variables, are not build
  options. An unlisted symbol fails the check, and so does a listed one that
  those files no longer test. Each entry has a class and a reason:
  - **shipping variant**;
  - **test-only**;
  - **stock feature off in this product**;
  - **not a switch** (a default value, a value the build injects, a library
    macro, or another kind named with its reason).

  Each test-only or stock-off entry has a row that refuses an image
  containing its feature. The row judges the image and the ELF, never the
  flag, so a switch set off but not wired to its code is still caught. A
  row is data the check evaluates where it can be (for example a string
  absent from the `.bin` or a symbol absent from the ELF), and otherwise
  names a test in the project's own harness whose result for this image the
  check reads. Each row is shown failing on an image built with the switch
  on and passing on one built without it. That is test evidence, made once
  and made again when the switch's code or its row changes.

  A shared firmware component's switches are listed in its own table, by
  that component's owner, and the check applies that table in every release
  build that uses the component. Like R18, the check runs on the finished
  release build and any session may run it. Bench and dev builds are not
  checked.

  **Build switches are CMake build options.** A project or a shared
  firmware component that needs a build switch makes it a CMake option, set
  from the CMake cache or with `idf.py -D`, and turns it into compile
  definitions in its own CMake files. It is never a Kconfig option. A
  shared component's switches use the same mechanism, listed in its own
  table (above).
- **R21. Board data.** The partition table is byte-identical to the board's
  reference, the image fits the board's application slot, the flash size in
  the headers of the images the build writes (the app's and the
  bootloader's) is the board's, and the tool prefix comes from the board's
  `IDF_TARGET`. The board is the one `shared/boards/*` component in the
  project's `dependencies.lock`, and its data is read from that component
  in `managed_components/` (`shared/boards/SPEC.md` B2 and B4). A release
  build is refused unless there is exactly one.
- **R29. Board settings.** Every option the board's `sdkconfig.board` names
  has the board's value in the build, folder by folder as R18 judges, and
  the project's release profile names every setting the board lists as
  required of the project, with the value the project's own spec sets and
  cites (`shared/boards/SPEC.md` B3). A required choice is named when the
  profile sets exactly one of its members on, and in each folder that has
  the choice exactly one listed member is on, so a stale member list
  fails.
  The options check (R18) applies it, on the finished release build; bench
  and dev builds are not checked.
- **R30. Pins stay in the board.** From ninja's dependency records, no
  object compiled from the project root read the board's
  `include/board_pins.h` unless its source file is on the project's list of
  stock files that still use pins (`shared/boards/SPEC.md` B6). The list is
  per-project data (R22). Like R18, the check runs on the finished release
  build; bench and dev builds are not checked.
- **R26. No eFuse-burning features in a release build.** Every release
  build has `CONFIG_SECURE_BOOT`, `CONFIG_SECURE_FLASH_ENC_ENABLED` and
  `CONFIG_FLASH_ENCRYPTION_ENABLED` unset, and `CONFIG_EFUSE_VIRTUAL` off,
  in every project, whatever the project's release profile names. The
  options check (R18) applies it to every release build, so a project's
  profile need not list these options. Like R18, it is a check on the
  finished release build, and bench and dev builds are not checked.
- **R27. Each project sets its own FreeRTOS tick rate.** Every project's
  release profile names `CONFIG_FREERTOS_HZ`, with the value its own spec
  sets and cites. The tooling sets no common value. The options check (R18)
  fails a release build whose project's profile does not name it.
- **R28. One set of log levels for every release build.** Every release
  build, of every project, has `CONFIG_LOG_DEFAULT_LEVEL` 0 (none),
  `CONFIG_LOG_MAXIMUM_LEVEL` 0 (none) and `CONFIG_BOOTLOADER_LOG_LEVEL` 1
  (error), and, in a project that compiles the Arduino core,
  `CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL` 0 (none). The options check (R18)
  applies it to every release build whatever the project's profile names,
  as R26 does. Log levels are set only in the `sdkconfig`, never by a `-D`
  in a build file (`CORE_DEBUG_LEVEL`, `LOG_LOCAL_LEVEL`). Test-only and
  development builds set their own levels in their own spec; they are not
  release builds and are not checked.

## 5. Data, not code

- **R22.** The tooling knows nothing about Arduino or about any one project.
  Release profiles, switch tables, `sdkconfig` needs and component lists
  are per-project data. Partition tables, slot sizes and flash sizes are
  per-board data. Every board's data is in its own folder under
  `shared/boards/` (port plan Layer 1), however many projects build for it:
  the WiCAN's and the X2's alike. A project with its own project root (the
  interposer, goal 3) supplies its own per-project data and needs no change
  to the tooling.

## 6. Running the tooling

- **R23. From a read-only checkout at the pin.** The build tooling runs from
  the shared checkout `../shared-<tag>/`, relative to the project's worktree
  root, as the bench toolkit does (goal 20 item 8). It refuses to start
  unless that checkout's digest (R7) matches the project's committed pin. If
  the checkout is missing, it refuses and prints the command that creates
  it.
- **R25. Tags on `shared/main`.** One sequence of tags covers the whole of
  `shared/main`: a tag marks the branch at one commit, and each project
  still pins each shared component at whichever tag it chooses. A tag is
  the date it is cut, `YYYY.MM.DD`, with `.2`, `.3` and so on for a second
  or later tag the same day (for example `2026.10.24`, `2026.10.24.2`). A
  tag is cut on a `shared/main` commit only when a project is about to pin
  it, by the reviewer of that project, after the pre-push check. A tag is
  never moved and never deleted. Its checkout is `../shared-<tag>/`
  (R23).

## 7. Not in this spec

- In gen-inhibit's spec: `0x7F3` on the image-hash prefix, the retirement of
  the name stamp and the name-once rule (port step 0c), and the diag schema
  and DBC checks.
- Retired, not carried over: `prod_build.py`'s branch refusal (goal 11),
  `stale_name_check`, `name_once_check` and `git_tree_name.cmake` (goals 11
  and 13).

## 8. Still open

- The comparison tool's requirements (goal 6: compares two images component
  by component and lists which evidence entries still stand).
