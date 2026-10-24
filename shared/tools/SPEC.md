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

Requirements are numbered R1 to R23. The numbers are stable: the two evidence
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
- the three build checks (scope, options, reproducible) and the build-switch
  check;
- the image-reading library: the app descriptor parser and the image hash
  read from a `.bin`;
- the comparison tool of goal 6, which the tester owns. Its requirements are
  not written yet (section 8).

It sits outside the layer table of goal 6: a change to it voids no evidence
unless the image changes, which the reproducible check (R19) shows.

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
  field. Nothing else compiles it in.
- **R5. Identity does not depend on branch, clone path or host OS:**
  - the same commit built on the same host OS, on any branch or at any clone
    path, gives the same image hash. The image hash includes the commit
    (R4), so two commits with identical files give two image hashes; the
    code hash is what stays the same (R2, R3);
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
  (`SDKCONFIG` in its `CMakeCache.txt`) is committed in the project:
  tracked by git and not ignored, so that this check sees any change to it.
  R18 leaves the options a profile does not name to it.
- **R9. Reconfigure first.** A release build runs `idf.py reconfigure`, then
  builds, in one run, with the compiler cache (ccache) off whatever the
  environment sets. A build whose configuration still has it on is refused.
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

## 4. Checks

- **R17. Scope.** From ninja's dependency records, every file the build read
  comes from the project root, IDF or `managed_components/`, and each
  managed component is at the version `dependencies.lock` records: its own
  manifest's `version` is the lock's. IDF here is `IDF_PATH` plus exactly
  two folders named by the build's own record: the install folder of the
  toolchain whose compiler CMake chose (`CMake<LANG>Compiler.cmake`), and
  CMake's own `CMAKE_ROOT` (`CMakeCache.txt`). Nothing else beside them in
  the IDF tools folder counts. For a bench or dev build, files under
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
  The whole `sdkconfig` is not compared: it is committed, and R8 holds the
  build to the commit. Like the other checks here, it runs on the finished
  build and is not a refusal in the release build; any session may run it.
  Bench and dev builds are not checked.
- **R19. Reproducible.** Rebuilding the same tree on the same host OS gives
  the same image hash.
- **R20. Build switches.** Every preprocessor symbol the project's source
  tests (other than ESP-IDF's `CONFIG_*` and header guards), and every build
  option tested by a CMake file the build reads from the repo, is listed in
  the project's table. The CMake files are taken from the build's own record
  of the files it read. A build option is a variable that can be set from
  outside the file that tests it. An unlisted symbol fails the check. Each
  entry has a class and a reason:
  - **shipping variant**;
  - **test-only**;
  - **stock feature off in this product**;
  - **not a switch** (a default value, a value the build injects, a library
    macro, or another kind named with its reason).

  Each test-only or stock-off entry has a row that refuses an image
  containing its feature. The row judges the image and the ELF, never the
  flag, and is shown failing on an image built with the switch on and
  passing on one built without it.
- **R21. Board data.** The partition table is byte-identical to the board's
  reference, the image fits the board's application slot, and the tool
  prefix comes from the board's `IDF_TARGET`.

## 5. Data, not code

- **R22.** The tooling knows nothing about Arduino or about any one project.
  Release profiles, switch tables, `sdkconfig` needs and component lists
  are per-project data. Partition tables, slot sizes and flash sizes are
  per-board data. A board's data is in `shared/` when more than one project
  or shared tool builds images for it (the X2: the interposer and the X2
  jammer), and in the project's own root when only that project does (the
  WiCAN). A project with its own project root (the interposer, goal
  3) supplies its own data and needs no change to the tooling.

## 6. Running the tooling

- **R23. From a read-only checkout at the pin.** The build tooling runs from
  the shared checkout `../shared-<tag>/`, relative to the project's worktree
  root, as the bench toolkit does (goal 20 item 8). It refuses to start
  unless that checkout's digest (R7) matches the project's committed pin. If
  the checkout is missing, it refuses and prints the command that creates
  it.

## 7. Not in this spec

- In gen-inhibit's spec: `0x7F3` on the image-hash prefix, the retirement of
  the name stamp and the name-once rule (port step 0c), and the diag schema
  and DBC checks.
- Retired, not carried over: `prod_build.py`'s branch refusal (goal 11),
  `stale_name_check`, `name_once_check` and `git_tree_name.cmake` (goals 11
  and 13).

## 8. Still open

- The tag naming scheme for `shared/main`, which R16 and R23 use.
- If the port turns build switches into `CONFIG_*` options, R20 must be
  extended to classify them, because it excludes `CONFIG_*` as written
  (gen-inhibit spec 12.3 item 3).
- The comparison tool's requirements (goal 6: compares two images component
  by component and lists which evidence entries still stand).
