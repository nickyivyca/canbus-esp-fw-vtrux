# Acceptance runner: specification

**Status:** approved by the user on 2026-11-01, in the charge-interposer
reviewer's session, and drafted that day by the charge-interposer reviewer,
whose port step 2a is the first use (gen-inhibit's 1c is accepted the
current way: user, 2026-11-01, in the gen-inhibit reviewer's session). As a
shared component's spec this follows port plan goal 20: the reviewer whose
project needs a change drafts it, the user rules in that reviewer's session,
and the other project's reviewer reads it before it merges into
`shared/main`. Design: the charge-interposer tester's sketch of 2026-11-01,
with both reviewers' answers to its questions. The bench lock's spec is
`SPEC.md`, beside this file.

Requirements are numbered A1 to A16. The numbers are stable.

## What it is

- **A1. One runner for a step's acceptance.** `vtrux_bench.accept` in
  `shared/bench` runs one step's acceptance from a list the reviewer wrote,
  and makes one acceptance record. It is a layer on the chain runner
  (`vtrux_bench.run`): what it runs, it runs as an ordinary chain suite, so
  the bench lock (SPEC.md), lanes, the wall-clock rules, resume, verdicts and
  known fails are the chain runner's, unchanged. It is project-neutral; what
  differs by project is in that project's adapter (A15). The tester runs it.

## The list

- **A2. The list is the reviewer's, and committed.** A step's acceptance list
  is a TOML file at `<project>/acceptance/<step>.toml` on the project's
  branch, owned by that project's reviewer, with a row in the project's file
  map. It says what the step must show and against which baseline. The runner
  reads it with `git show` at the commit the command line names, and refuses
  a list that is not committed there; it never reads a working-tree copy. The
  record names the list's commit and sha256.
- **A3. What a list holds:** the step (its id, the project, who ordered it
  and when, the spec sections, and the `VERIFICATION.md` section its entry
  goes in), the trees (A4), the builds (A5), the checks, the suite runs, the
  baseline, the comparisons and can-fails (A10), the premises (A11), and any
  hand items (A9). A key the runner does not know is refused. **A list never
  carries a patch, script text, or a command that is not a committed file in
  one of its trees.** Anything a step has to vary is an option of committed
  harness, added as harness work before the step (both reviewers,
  2026-11-01), with today's behaviour as its default.
- **A8. Selection is the list's.** Which harness tests a step runs is the
  list's selector over the project's committed harness-test catalogue. The
  runner may print what a rule (the paths the step changed, the tests changed
  on the branch, the previous step's set) would pick, and the difference, as
  advice; it never adds or drops a test itself (both reviewers, 2026-11-01).

## Trees, builds and settings

- **A4. Trees from git objects.** Each tree the list names is made from git
  objects at an exact commit: the project at its commit, and any shared
  component at a commit or tag the list gives, which overrides the project's
  pin for that tree. A tree is local or on a remote host the adapter knows.
  The runner refuses a tree unless its HEAD is exactly the commit and it has
  no changes, and never edits it. Each tree gets a record: the commits, tree
  ids, the product digest, and the sha256 of every file used.
- **A5. Builds the runner owns.** Each build runs through the adapter's
  target and shell in a clean tree of its own, and writes a build record: its
  images with their sha256, the ELF sha256, the variant and the toolchain.
  Builds are build work under AGENTS.md "Builds during wall-clock tests", so
  the chain runner's throttle applies. The "No builds" line in
  `bench-holder.md` stays the tester's to set by hand (AGENTS.md point 5).
- **A6. Settings from the builds.** The runner writes a settings file for the
  run from the machine's settings file. The keys that depend on a build are
  filled from the build records through the adapter's key map, never edited
  by hand. A masked copy goes in the record; the unmasked file is removed at
  the end (A14). Passwords are never read (AGENTS.md).

## Running and judging

- **A7. How it runs.** The checks, harness tests, can-fails and suite runs
  compile into one chain suite. A suite run is a nested chain run with its
  own record inside the acceptance record; a re-run of flagged cases is a new
  nested record, never added to a closed one. Each check's environment comes
  from the list and the catalogue, never from the operator's shell, apart
  from the machine variables AGENTS.md and `local-paths.md` name.
- **A9. No hand steps, if it can be helped.** A step the runner can do (a
  flash and the identify after it, for instance) is an adapter flasher run
  under the runner's own lock. A step it cannot do is a hand item: under the
  lock, the runner records the board reads before, the operator's
  confirmation, and the reads after.
- **A10. Comparisons and can-fails are committed code.** A comparison names a
  comparer, committed in `shared/bench` or the adapter, its two sides (checks,
  trees, runs or the baseline) and what must hold: identical, the same
  verdicts, figures within a tolerance, or listed differences one by one. A
  can-fail names a target check and committed mutant text (a component's
  mutate script, or a harness's own can-fail mode). The runner applies it to
  a copy of the tree, refuses unless the text matches exactly once, and
  requires the stated change. This is AGENTS.md's rule that a check a
  `VERIFICATION.md` entry rests on, and its can-fail, is committed harness run
  from a named commit (user, 2026-11-01), made true by construction.
- **A11. Premises.** A premise is what must hold before the results count
  (the product digest equal to, or unchanged since, a commit; a board read).
  A premise that fails makes the item NOT EXERCISED, never PASS or FAIL.
- **A12. The verdict.** ACCEPTANCE PASS only if every premise held, every
  check gave its expected verdict, every comparison held and every can-fail
  behaved as stated. Otherwise the runner lists each item that did not.

## The record and cleanup

- **A13. The record.** The runner names the acceptance record's folder, in
  the project's runs folder, when it starts, before any lock or build, so
  every console is written there. It closes the record once: a README (step,
  list commit, trees, builds, checks, verdicts, comparisons, can-fails,
  leftovers), SHA256SUMS over every file, the row in the project's artifacts
  README, and a fact skeleton for the `VERIFICATION.md` entry, which the
  tester still writes. After the close the folder never changes; a re-run is
  a new acceptance record that names the earlier one.
  - **Identifiers:** before the close it runs the identifier scan over the
    folder and records every hit, without refusing to close: the bench's
    identifiers may be kept in SeaDrive run records, never in git (user,
    2026-11-01, in both reviewers' sessions).
  - **Never published:** run records stay on reverse-it's held paths. They
    are under reverse-it's firmware-development trees, which its git publish
    holds entirely (`git-publish.md` ruling 16), and a runs folder the runner
    writes to must stay off any publish path.
- **A14. It removes what it made.** In a `finally` and an exit handler, the
  runner removes every tree, mutant copy, bundle, settings file and remote
  work folder it made, through one shared scratch helper merged from the
  interposer's `scratch_dir` and gen-inhibit's `gi_paths` (AGENTS.md
  "Removing scratch"). Whatever it cannot remove is a LEFTOVER line in the
  record.

## Projects and ownership

- **A15. Each project's adapter**, one module beside its suite, gives: build
  targets and shells; the settings key map; the product files and digest;
  images, identify and flashers; its comparers; redaction patterns; its
  harness-test catalogue (per entry: path, arguments, environment, shell,
  timeout, tags and the paths it covers); remote-host recipes; and its runs
  folder, artifacts README and `VERIFICATION.md` section map. The
  charge-interposer's adapter comes first, for port step 2a; gen-inhibit's
  follows.
- **A16. Ownership** is per file in `shared/bench/README.md`: the runner, the
  scratch helper, the adapters and their tests are the testers'; each
  project's acceptance lists are its reviewer's; this spec is the reviewers'.
