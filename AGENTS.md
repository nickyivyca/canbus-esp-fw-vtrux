# Agent instructions: canbus-esp-fw-vtrux

This repo holds the firmware for two Vtrux CAN bus devices, built on a fork of
MeatPi's WiCAN firmware, and the tooling that builds and tests them. Both
projects are specification-driven, and three role sessions work on each:
`/reviewer`, `/implementor` and `/tester` (see "Specification-driven
components").

**This repo is public.** Everything committed here is published when it is
pushed, so every push passes the pre-push check first (see "Pushing").

| Project | Hardware | Framework | Branch | Code |
|---|---|---|---|---|
| Generator inhibitor (`gen-inhibit`) | MeatPi WiCAN OBD, ESP32-C3 | ESP-IDF | `gen-inhibit/main` | the WiCAN tree in `gen-inhibit/firmware/`, with its tooling in `gen-inhibit/tools/` and tests in `gen-inhibit/test/` |
| Charge interposer (`charge-interposer`) | Autosport Labs ESP32-CAN-X2, ESP32-S3 | ESP-IDF, with arduino-esp32 as a component | `charge-interposer/main` | `charge-interposer/` |
| Shared code | used by both | | `shared/main` | `shared/<component>/` |

`main` is upstream WiCAN. Nobody works on it.

## Local paths: `local-paths.md`

Anything that differs per machine is in `local-paths.md` at the root of this
worktree: the Python command, each ESP-IDF install and how to export it,
this machine's values for the environment variables the harnesses read, the
other clones this session works with, and this worktree's role and branch.
It is gitignored and filled in separately in each worktree, so it is never
pushed. Read it before running Python, a build or a harness. For every Python
call, use the command it names, never a bare `python` or `python3`.

If `local-paths.md` is missing, copy `local-paths.template.md` to it and
follow the template's text.

## Reverse-it: the project behind this repo

Raw CAN logs and captures, investigations, vehicle notes, ESP plans, specs not
yet moved here, and the bench log and bench-holder all live in the reverse-it
project folder in SeaDrive, not in this repo. Its path on this machine is in
`$REVERSE_IT_DIR` and in `local-paths.md`. If the variable is unset, ask the
user.

For any of that work, first read `$REVERSE_IT_DIR/AGENTS.md` in full and
follow it there: its data-source rules, its Python rules, where artifacts go
and its analysis rules. Read it from top to bottom, not a search or an
excerpt: its rules are spread across the whole file. Use its tool set (`tools/`, `canre/`,
`projects/vtrux/tools/`) rather than writing new tools here. Its tools and
scratchpads run from its root, so `cd` to `$REVERSE_IT_DIR` before running
them. Results go where reverse-it's rules put them, not into this working
tree.

Nothing from reverse-it is committed here unless it passes the pre-push check.

**Identifiers in reverse-it's files** (user, 2026-11-01): the bench's
identifiers, such as the WiCAN's SSID and the boards' MACs in
`bench-holder.md`, the evidence docs and the settings files, may be read
into context. Read and edit those files like any other. They never reach
git: the pre-push check stops them. Passwords are still never read (user,
2026-10-23).

## can-tools: the CAN adapter package

`canbench`, the package both projects use to detect and open real CAN
adapters, comes from the can-tools repo, cloned once on each machine. Its
path on this machine is in `$CAN_TOOLS_DIR` and in `local-paths.md`: no
default, and refused if unset or if it holds no `canbench/`. Each project
pins the can-tools commit it uses, as it pins `canre`, and owns its pin: it
moves the pin without going through the methodology owner (user,
2026-10-30). The project's pin tool (the interposer's `can_tools_pin.py
install`) writes the pinned commit's `canbench/` out of the clone into
`scratch/can-tools/<commit>/`, and the harness loads it from there by file.
Neither that folder, the clone nor reverse-it goes on `sys.path` for it, and
a `canbench` already imported from anywhere else is refused. reverse-it's
`canbench/` mirror is never read. Setup, and the `.pth` file reverse-it's
own scripts use, are in `$REVERSE_IT_DIR/tools-reference.md`, "can-tools"
(user, 2026-10-30).

## Worktrees and branches

Each machine has one clone, and a worktree per role beside it, in one plain
folder. The folder's path on this machine is in `local-paths.md`.

```
esp-fw-vtrux/                plain folder: not a repo, no AGENTS.md
  anchor/                    the one clone; detached at origin/main; nobody works or builds here
  gen-inhibit-reviewer/      worktree on gen-inhibit/main
  gen-inhibit-implementor/   worktree on gen-inhibit/implementor/<topic>
  gen-inhibit-tester/        worktree on gen-inhibit/tester/<topic>
  interposer-reviewer/       worktree on charge-interposer/main
  interposer-implementor/    worktree on charge-interposer/implementor/<topic>
  interposer-tester/         worktree on charge-interposer/tester/<topic>
  shared-<tag>/              detached and read-only, one per pinned shared/main commit
```

Also there while needed: a writable worktree on `shared/<component>/<topic>`
for a shared change in progress, and extra worktrees named
`<project>-<role>-<purpose>`. Create them from the anchor, e.g. `git -C
anchor worktree add --detach ../shared-<tag> <tag>`. Each machine has its own
anchor and worktrees; work moves between machines only through origin.

**Start-up check.** Before anything else, check that you are in your role's
worktree, on your role's branch (`git branch --show-current`). If not, stop
and tell the user.

**Branches.**
- `<project>/main` is the reviewer's, and the only branch it writes. Git
  lets a branch be checked out in one worktree only, so it lives in the
  reviewer's worktree.
- The implementor and tester work on `<project>/implementor/<topic>` and
  `<project>/tester/<topic>`, each in its own worktree. Start a new topic
  from `<project>/main`.
- Every branch is visible from every worktree, so the reviewer reads the
  others' work from its own: `git log -p <project>/main..<project>/tester/<topic>`.
- Shared code has its own branches, `shared/main` and
  `shared/<component>/<topic>` (see "Shared code").
- **Always merge, never rebase.** Manifests, `VERIFICATION.md` entries and run records
  name commits, and a rebase would rewrite them.
- **Unpushed history is corrected, not patched over** (user, 2026-11-11).
  Commits on no origin branch may be re-made, amended or rebased, for
  example to keep a pre-push hit out of history. Re-make them on a new
  branch name, never push the old branch, and in the same go correct
  every note, tracker entry or record that names one of the old commits
  to its new hash. Once a commit is on origin, the merge-only rule above
  holds.

**Skills follow the branch.** Each worktree has the role skills of the branch
it has checked out. Each branch keeps
only its own role's skill current; changes reach other branches by
cherry-pick. After a cherry-pick that touches your skill, re-read its
`SKILL.md`. Each worktree has its own `.claude/settings.local.json`, on
Linux as on Windows (`madhouse-debian`'s anchor has no `.claude/`, checked
2026-11-11).

**Never build WiCAN firmware from an interposer worktree or branch.** A
WiCAN image's name and compiled-in commit come from the whole tree, so
interposer commits on a WiCAN branch, or an uncommitted interposer file in
a WiCAN worktree, would change or block WiCAN builds. An interposer run
flashes the WiCAN only with a gen-inhibit image built elsewhere and pinned
as a tool of the interposer's harness, as gen-inhibit pins its X2 images
(`gen-inhibit/tools/x2_pin.json`): the pin file names the gen-inhibit
commit and the image's size and hashes. The run loads it at its start
through the shared flasher's OTA upload (`shared/bench`) over the WiCAN's
hotspot, with the board's identity read before and after, and flashes
nothing built from its own tree (user, 2026-11-11, in the interposer
reviewer's session). **Until `madhouse-debian` has its USB Wi-Fi adapter,**
the Linux baseline of port plan step 3 loads the same pinned image over
USB, with the shared flasher's esptool path, instead of OTA (user,
2026-11-11, in the CAN replay tooling session, its plan
`tools/plans/madhouse-bench-canctl.md` in reverse-it).

## Session rules

### Read instruction docs in full

Read each of these from top to bottom, in full, every time you read it:
this `AGENTS.md`, `local-paths.md`, every `SKILL.md` you act under, the
skills' `README.md`, and the top-level `README.md` of the project or
component you are working on. In reverse-it, its `AGENTS.md`. Not a search,
not an excerpt, not only the sections that look relevant. A partial read
feels like a full one from inside the session, and the rule you skipped is
the one you then break. If a file is too long for one read, read it in
parts until you have read all of it.

### After a compaction, re-read your docs and skill

A compaction summary is a record of what the session *did*, not of what the
docs *say*, and from inside the resumed session the two feel the same.
**Before the first action a doc governs, re-read the doc itself, in full**
(see "Read instruction docs in full"): this `AGENTS.md`, `local-paths.md`,
and your role skill's `SKILL.md`, plus `$REVERSE_IT_DIR/AGENTS.md` if the
step works in reverse-it.

The docs you are least likely to re-read are the ones you edited most before
the compaction. The summary describes your edits in detail, so the file feels
known, while the sections you did not touch are missing from the summary,
and their absence reads as "nothing else relevant in there".

**Skills too.** After a compaction, Claude Code re-attaches each skill you
invoked earlier as the text it had when you invoked it, not the current
`SKILL.md`. A skill edited or cherry-picked since then would be followed in
its old form, and nothing marks it as stale. So re-read
`.claude/skills/<name>/SKILL.md` in full for every skill invoked in the
session before acting under it.

### Compacting your own context: `/self-compact`

A session can compact its own context and resume without anyone at the
keyboard, with the `/self-compact` skill. It types `/compact` into this
session's terminal and then, once the compaction is done, one resume message
naming the next step. By default it resumes. A session sure that nothing
more is needed from it until a message, a task notification or the user
arrives may pass `--compact-only` instead, and then nothing is typed (user,
2026-10-31: a wedged session costs more than a needless resume, so when in
doubt, resume). It is the only sanctioned way for a session to type into its
own input.

- **When:** only when the user asks for it, at the compaction point your
  role skill names, or when the idle-compaction hook asks for it (user,
  2026-10-24). A session without a role skill has no self-compaction point
  of its own, but the hook still applies.
- **The idle-compaction hook.** Claude Code compacts a session of 200K
  tokens or more that has been idle about 54 minutes, before its prompt
  cache expires, with no instructions. In this repo a PreCompact hook,
  `.claude/skills/self-compact/idle_compact_hook.py`, blocks the first such
  attempt and wakes the session with a message asking it to self-compact.
  Do it, per the skill. If you can't, say so in one line; the hook lets the
  next attempt through, as a plain compaction. Its two entries go in each
  machine's `settings.local.json`, in the guarded form its header gives.
- **Before it:** write everything worth keeping to its files. The summary
  loses detail; files do not.
- **After it:** the next turn starts from a summary, whether the resume
  message or, after `--compact-only`, whatever wakes the session starts it.
  Re-read your role skill and the docs the step needs before acting, per
  "After a compaction".
- **If auto mode blocks it,** do not retry it, reword it or run it another
  way. Carry on without compacting and tell the user in one line.
- **Where it works:** in tmux on Linux, and in terminal Claude Code on
  Windows. Not in Claude Desktop.

The procedure, and the rules for the two messages, are in the skill.

### Pausing

To wait for a fixed time, run `session_pause.sh` at the repo root with the
Bash tool's `run_in_background`; you are notified when it ends. Do not chain
`ScheduleWakeup` calls instead.

```bash
bash session_pause.sh <seconds>
```

## Specification-driven components

Each project has a written specification that is the source of truth for its
behaviour. The spec is the translation layer between the user and the agent
sessions: the user reviews and edits the spec, not the code. A session
touching a component runs one of three role skills -- `/reviewer`,
`/implementor` or `/tester` -- with the component name, and finds its files
in the project's file map: `gen-inhibit/README.md` or
`charge-interposer/README.md`, owned by that project's reviewer. A project
branch may hold only its own project's folder. Behaviour changes go into the spec
first (with the user's approval, per the notes rule) and into the code
second, never the other way round. **Any session that notices code, a test,
a note or a measurement contradicting the spec -- even while working on
something else -- must fix it when the fix is unambiguous and the file is
one its role owns, must otherwise tell the owning session, and must in every
case bring it to the user's attention.**

**Surfacing is not deciding.** Bringing a contradiction to the user's
attention means saying that it exists and which session holds it. Any change
to what the spec says, or ruling on what it should say, is framed by the
reviewer, put to the user in the reviewer's session, and written into the
spec there. Other sessions do not restate a reviewer-held question to the
user, even to recommend an option: the user has one place to argue it (user,
2026-10-10).

Specs not yet moved into this repo are in reverse-it; the file map gives
their path under `$REVERSE_IT_DIR`. gen-inhibit's moved on 2026-11-07:
`gen-inhibit/SPEC.md`, `VALIDATION.md` and `VERIFICATION.md` on
`gen-inhibit/main`.

**Project layout** (user, 2026-10-26). A project folder holds its file map,
`<project>/README.md`; its firmware build root, `<project>/firmware/`; and
its host-side tooling and tests, laid out as the file map says.
gen-inhibit's `firmware/` is the upstream WiCAN tree as upstream lays it
out. The interposer's ESP-IDF project is `charge-interposer/firmware/`,
since port step 2a. **No path in the tree may have a branch's name.** An IDF
`main/` directly in a project folder would make `<project>/main` both a
branch and a path, and git then refuses the bare branch name in `log`,
`show`, `diff` and `reset`. **Beside the project folder, a project
branch's root holds only the files every branch shares:** `.claude/`,
`.gitattributes`, `.gitignore`, `AGENTS.md`, `CLAUDE.md`, `LICENSE`,
`README.md`, `local-paths.template.md` and `session_pause.sh`. Upstream
WiCAN's root tree, its `.github/` included, is removed from both project
branches (user, 2026-11-11: the interposer's root tree in the interposer
reviewer's session, `.github/` in the methodology owner's).

### Shared code

Shared components live under `shared/<component>/` on `shared/main` (plan:
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`, goal
20). A shared file has no single owning project. Whichever project needs a
change makes it, in the role that owns that kind of file (implementor for
product code, tester for harness), on a `shared/<component>/<topic>` branch.
The reviewer of that project merges it. The other project stays on its
pinned version until it chooses to take the change (user, 2026-10-17).
Writing a file first does not make a project its owner. A shared
component's spec is drafted by the reviewer of the project that needs the
change and ruled on by the user in that reviewer's session; the other
reviewer reads it before merge (user, 2026-10-17).

- **Never merge between `shared/main` and a project branch,** in either
  direction. A project takes shared code only as a pinned tag (port plan
  goal 20 items 7 and 8).
- **Per-file exceptions** to this ownership are in the component's
  `README.md`.

### Who owns what

**Ownership split, per file.** The implementor owns the product code on every
side of it (a Python reference and its firmware port both count) and the unit
tests of the core's internals. The tester owns everything that verifies it:
scenarios, trip and invariant checks, the reference-vs-port differential,
decoder cross-checks, bench harnesses and the harness's own tests. If a
file's owner is unclear, the project's reviewer decides and edits its file
map directly.

**The methodology owner**, a methodology session, owns this `AGENTS.md`,
the role skills and the ownership split itself, and changes to them go
through it (user, 2026-10-10). It commits them on its own
`methodology/<topic>` branch and tells each running session they affect.
Each one cherry-picks the commit onto its own branch, then re-reads the
changed file in full. **Never edit this file or a skill by hand on a role
branch:** a hand edit conflicts with the next cherry-pick (user,
2026-10-23). A running session follows a skill's text as it was when
invoked, and a compaction re-attaches that same text (user, 2026-10-16).

**Docs change with the tooling.** A change to tooling or a workflow that
this file or a role skill describes is not finished until the text says the
same thing. The session making the change sends the methodology owner the
text change with it. The reviewer doesn't hold the tooling push for it: it
pushes the tooling change when it is ready, and cherry-picks the
methodology commit when it comes, and the change stays open in its tracker
until then (user, 2026-11-08: push as much as possible, since origin is
this repo's only copy off the disk).

**Plans, handoffs and working logs** belong to the role session that writes
them, named on an `**Owner:**` line at the top. A file without one gets an
owner from the reviewer. A file that holds validation the spec rests on is not
a working log for this rule; the reviewer rules on it (user, 2026-10-10). A
file the reviewer rules a **record**, closed to new material, carries this
header, and its file-map entry uses the same words (user, 2026-10-30):

```
**Owner:** <role> (<who ruled: the user, or which session>, <date>).
**Status: record.** Closed to new material, because <one-line reason>.
**May change:** only dated corrections beside a passage found to be wrong,
written by the owner (user, 2026-10-30).
**New material goes to:** <file> (<its owner>).
**Reopening it:** the user's call.
```

Don't label a file "frozen" or "locked": the header says who ruled it, what
may change, and who can reopen it.

**Published-interface documents are spec** (user, 2026-10-07): the diag
schemas and their decode DBCs that the tester tests against belong to the
reviewer, and changes need the user's approval like any spec change.
Otherwise the implementor could change what correct means.

### Spec, validation and verification

A component's spec has two companion documents, and they answer different
questions.

- **`VALIDATION.md`: why each requirement is worded as it is.** It holds the
  measurements, readings, corrections and user rulings behind each
  requirement.
- **`VERIFICATION.md`: test evidence that a specific commit meets the spec.**
  A pass verifies the code it ran on: it carries to any build with the same
  code hash (shared/tools R3), and to nothing else (user, 2026-11-08), so a
  commit that changes no code keeps its passes. When code is ported
  or rewritten, the old code's pass entries carry no weight for the new code:
  they are not copied across, and they are not cited as validation. Their
  tests can be pointed at the new code to make new entries.
- **A check that a `VERIFICATION.md` entry rests on is committed harness,**
  run from a commit the entry names, and so is the can-fail that shows it
  can fail. A hand-made script in `scratch/` can't be re-run once scratch is
  cleaned, so its result is not verification.

**Never write "evidence" or "the evidence doc" without saying which.** Both
documents hold evidence: `VALIDATION.md` for the basis of a requirement,
`VERIFICATION.md` for a test result. Name the file, in docs, skills, trackers
and messages between sessions.

Rules for `VALIDATION.md`:
- **A verification finding that led to a correction** (a NOT AS SPECIFIED, a
  defect) is cited only as what exposed the problem. The validation is the
  correction or the ruling that followed.
- **A measurement made on a different toolchain, board or code base is not
  the basis for a requirement on this one.** At most it is supporting
  context, labelled as such.
- **Each entry's status is "validated" or "basis pending"** (user,
  2026-11-07). Validated means its basis is recorded and cited. Basis
  pending means incomplete validation, which is allowed but must be
  visible: the entry says what is missing. A requirement that rests on a
  user ruling cites the ruling and its date. Neither status says whether
  the code meets the requirement; only `VERIFICATION.md` does. Don't write
  "complete", which reads as "implemented".

## Pushing

**The reviewer merges and pushes** (user, 2026-10-15 and 2026-10-23). The
implementor and the tester commit on their topic branches in their own
worktrees and never push. When work is ready, they tell the reviewer the
branch and the commit range. In its own worktree, the reviewer merges the
topic branch into `<project>/main`, runs the pre-push check, and pushes. It
pushes the topic branches too, after the same check, as often as it can,
unless the work isn't worth keeping: origin is this repo's only copy off
the disk, and a disk failure loses everything that is neither pushed nor
regenerable (user, 2026-10-23 and 2026-11-08). All of a project's role
sessions run on one machine, in worktrees of the same anchor clone, so the
reviewer sees every topic branch without a push (user, 2026-10-23).

This rule is the user's standing approval for routine pushes, so the
reviewer doesn't ask per push. It does ask before a force push, a branch
delete, or any push the check stops on something real.

**A false positive doesn't stop a push** (user, 2026-11-08). A hit the
manual read clears as a false positive, such as a common word, a version
string or an accepted name, is pushed past. The session tells the
methodology owner, who fixes the scan, and every session treats the same
hit the same way until the fix lands, without asking again.

- **On a merge conflict,** the reviewer aborts the merge and sends it back.
  The requester merges `<project>/main` into its topic branch in its own
  worktree and asks again.
- **If origin has moved,** the reviewer merges it into `<project>/main`
  itself, then checks and pushes.
- **Always a merge, never a rebase** (see "Branches").
- **The reviewer doesn't commit in another session's worktree** or on
  another session's branch.
- **Shared branches:** the reviewer whose project needs a shared change
  merges it into `shared/main` and pushes, after the same check.
- **The methodology owner** is not part of this workflow. It pushes its own
  `methodology/<topic>` branches, after the same check (user, 2026-10-23).

### The pre-push check

It covers every commit the push would publish, not just the tip, because
history publishes the intermediate states too. Run `git fetch`, then

```bash
git log -p <branch> --not --remotes=origin
```

which lists the commits on no origin branch yet, whether or not origin
already has the branch (user, 2026-10-23). Stop and bring it to the user if
it contains:
- credentials, tokens, keys, WiFi SSIDs or passwords (the bench's are passed
  in by environment variable, never committed);
- IP addresses or other home-network details;
- work-machine names or paths;
- personal information beyond the commit author;
- VINs;
- service-manual or other copyrighted excerpts;
- captures or logs not meant to be published.

The machine names `NICKY-XPS`, `madhouse-debian` and `NICKYDESKTOP` are
accepted (user, 2026-10-23). So are WiCAN firmware defaults such as its
hotspot addresses `192.168.80.1` (device) and `192.168.80.2` (laptop), which
are not home-network details (user, 2026-10-20). So is `~/.espressif`,
ESP-IDF's default tools folder, as the whole path only (user, 2026-11-03),
and so are `~/esp/esp-idf` and `~/esp/esp-idf-v<version>`, its default
install folder, as whole paths (user, 2026-11-11).
So is bare `madhouse`, in prose and in file names, though it is also a
bench WiFi SSID (user, 2026-11-07).

**The scan script is an aid to this review, not a replacement for it.**
Run it first:

```bash
<python> $REVERSE_IT_DIR/firmware/prepush-scan/prepush_scan.py <worktree> <branch>
```

It fetches, then scans what the command above lists: every commit-message
line, every added line (binaries as text, merges by their combined diff) and
every added or changed file's path. It looks for the categories above and
the real values in its private value file, and prints every hit masked, the
counts of what it scanned, and the files added or changed, with binaries and
captures flagged. Exit 0 is clean, 1 is hits to review, 2 is refused: a
refusal means nothing was checked, so don't push on it. Then read the
`git log -p` output yourself for what no pattern matches, such as
copyrighted text or personal details. The script stays in reverse-it,
because its patterns and values name what it looks for; its README
(`firmware/prepush-scan/README.md`) has the details and the known limits.

## The bench and builds

### The shared bench

**The bench is shared by the per-component testers.** It is the whole setup
-- both boards, the PC, the USB adapters and the CAN segments -- and one
hardware test runs at a time. **The lock is
`$REVERSE_IT_DIR/firmware/bench-log.jsonl`,** an append-only log written
only by `shared/bench`'s `bench_lock.py`, through its command line or the
chain runner, on the bench host the log names (spec: `shared/bench/SPEC.md`
on `shared/main`). Nothing edits or deletes a line of it. Whichever tester
has hardware work first takes it, and releases it when it is not actively
running. A tester can loan it to its own component's implementor for
bring-up. The rules for taking, loaning and handing back, and each board's
state, are in `$REVERSE_IT_DIR/firmware/bench-holder.md`. Both files stay
out of git: they change at every hand-off, and they record home-bench
details.

**The bench host is `madhouse-debian`** from port plan step 3 (the hardware
moved on 2026-11-11; it was `NICKY-XPS` before). Its lock is native Linux,
in `shared/main` from tag `2026.11.11.2` (SPEC section 4); the testers'
pinned `shared/bench` version must include it before they take the lock
there.
- **The move to a new host** (SPEC section 4): on the old host, a role that
  may take runs `bench_lock.py retire --to <new host> --reason ...` while
  the lock is free, which renames the log aside as
  `bench-log.<old host>.<date>.jsonl`. The new host runs `init` only once
  that renamed copy has synced there and no `bench-log.jsonl` is left;
  `init` refuses otherwise.
- **Besides the testers and their loans,** two roles may take the bench by
  hand, with no run: `android`, the Android sessions' runs that touch the
  bench adapters, and `can-tools`, the CAN replay tooling session (SPEC
  section 8). They may build and flash the X2's test images (the jammer
  and the SLCAN images), but do not change them, and flash no other image
  or board (user, 2026-11-12, in the CAN replay tooling session). How
  they flash before and after the images move into
  `shared/x2_test_images`, and what the X2 is left on and recorded as at
  release, are in SPEC section 8 (`shared/main` from `1e33234d`). At
  release, everything they opened is closed and back on its driver,
  `canmain` is down, and what they transmitted is noted.
- **`canmain`, CAN1's Innomaker, is for the Android app's two-bus tests**
  and is kept down during ESP runs. Every run that needs the bench refuses
  to start unless its settings' `canmain_check` shows `canmain` down
  (`shared/bench/README.md`).
- **Root operations on the CAN adapters** (links up and down, an
  Innomaker's driver binding) go through `canctl`, run as `sudo -n
  /usr/local/sbin/canctl ...`. It is the CAN replay tooling session's, and
  its README is `$REVERSE_IT_DIR/tools/bench/canctl/README.md`.

### Build tooling

**Build tooling is specified.** gen-inhibit's release builds are made by
the shared build tooling (`shared/tools` on `shared/main`) under
`shared/tools/SPEC.md`, at the version gen-inhibit pins in
`gen-inhibit/firmware/shared_pins.json`. They run from that version's
read-only checkout `../shared-<tag>/`, started by
`gen-inhibit/vtrux_tools.py`. If the
checkout is missing, it refuses and prints the command that creates it. A
gen-inhibit prod build is such a release build (gen-inhibit spec 12.3 item
3, user, 2026-10-25), and none is made during the shared-code port.
gen-inhibit's bench and dev builds stay `idf.py` / `ninja` builds, which
need not meet the release rules (shared/tools SPEC R14). The port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`.

The interposer's builds moved onto ESP-IDF at its port step 2a. Its
project is `charge-interposer/firmware/`, on ESP-IDF v5.4.2 with
arduino-esp32 3.2.1 as a component (user, 2026-11-03). Its build identity
follows shared/tools, at the version
`charge-interposer/firmware/shared_pins.json` pins (interposer spec 8.2).
Its bench and dev builds are the shared tooling's dev builds (shared/tools
R31), one build folder per variant, made by
`charge-interposer/vtrux_tools.py build <variant>`, with the variants
listed in `charge-interposer/firmware/vtrux_build.json`. Like
gen-inhibit's, they need not meet the release rules. The tooling's
environment check (R32) runs first. A bench build made with `idf.py`
directly, such as a flash, runs
`<python> -B charge-interposer/vtrux_tools.py check-env` first. No interposer
release build is made during the port, since no mid-port build goes on
the truck (user, 2026-11-03).

**One IDF version from gen-inhibit's port step 1d** (user, 2026-11-03).
Both projects build on ESP-IDF v5.4.2, an install with its own tools
folder, so `IDF_TOOLS_PATH` is set before the export. Run a build only in a
shell exported for it. The shared tooling's `check-env` (shared/tools R32)
checks that. Release builds and the tooling's dev builds (R31) run it
themselves. gen-inhibit's `idf.py` / `ninja` bench and dev builds run
`<python> -B gen-inhibit/vtrux_tools.py check-env` first, with the Python
command `local-paths.md` names. A bench or dev
build under another IDF is not refused (R33, R14): the other IDF rewrites
`dependencies.lock` and builds, so a changed lock in `git status` after a
configure is the sign (user, 2026-11-04). The v5.4.1 install stays only for
branches from before 1d (user, 2026-11-04). Each machine's recipe is in
`local-paths.md` and `$REVERSE_IT_DIR/tools-reference.md`.

### Parallel work on the shared machine

**Parallel work is allowed by default** (user, 2026-11-12). Builds,
emulated-time suites, mutant runs, wall-clock tests, bench runs, other
sessions' work and the local LLM all run at the same time. Nobody waits
for other work to end, asks for a window, or holds work back for someone
else's (user, 2026-11-11, in the Local LLM hosting session: "run
alongside, never hold"). Only two things are exclusive, and both are
physical, not resource limits: the bench hardware, through the bench lock
(see "The shared bench"), and a simulator bench's virtual-bus groups and
ports (see "One simulator bench per set of ports"). Contention for CPU,
memory and disk is handled by where work runs (point 1), and a failure
contention could explain is triaged (point 2).

**Wall-clock tests** are tests whose stimulus is sent by the wall clock on
the shared machine: the interposer's simulator scenarios and gen-inhibit's
bench runs. Emulated-time suites are not.

1. **Placement on `madhouse-debian`.** Start each heavy job as a scope in
   its slice:
   `systemd-run --user --scope --slice=<slice> <command>`. A scope keeps
   the shell's environment, working folder, output and exit code, so a
   shell exported for ESP-IDF builds in it; a service sees none of the
   `IDF_*` variables, and the environment check (shared/tools R32) refuses
   it (the interposer implementor, checked on madhouse, 2026-11-11). The
   slices (user, 2026-11-11, in the Local LLM hosting session; read back
   with `systemctl --user show` on 2026-11-12):
   - `bench.slice`, CPUWeight 2000: wall-clock tests and everything a run
     starts for them, such as simulators, witness readers, adapter I/O and
     the run's own flash or OTA load.
   - `llm.slice`, CPUWeight 1000: the local LLM server, a user unit since
     2026-11-11.
   - `app.slice`, CPUWeight 100: the Claude Code sessions and anything else
     started from a terminal without a slice.
   - `build.slice`, CPUWeight idle, MemoryHigh 8G: every build, mutant run
     and emulated-time suite, of either project and from every session.
     Builds run at the lowest CPU priority but are free to use every idle
     core, so they are not held to one job (user, 2026-11-11). If an
     emulated-time suite's timeout trips under idle, the fallback is a
     small weight, such as 20 (user, 2026-11-11).

   Read the placement back: `cat /proc/<pid>/cgroup` names the slice, and
   `systemctl --user show <slice> -p CPUWeight -p MemoryHigh` gives its
   settings. `nice` only ranks processes inside one cgroup, so it does not
   replace the slice, and neither does a raised process priority.
2. **Triage a failure that contention could explain** (user, 2026-11-12).
   A timing failure, such as a simulator STALL, a stimulus gap, a timeout
   or a check voided on timing, is judged against the machine's resource
   record over the failed check's window before it counts either way:
   - **Starved:** over the window, the record shows the run's slice short
     of CPU, memory or disk (pressure, major faults or swap growth above
     the marks), or the simulator's own lateness measure shows it was not
     scheduled. That is not a product result: the check is re-run, and the
     finding goes to the Local LLM hosting session as information about
     the load controls.
   - **True error:** the record is clean over the window. The failure
     counts like any other.
   - **Unclear:** re-run once. A repeat with a clean record is a true
     error.

   Each project's tester sets its marks from measured quiet data, written
   in its test plan with the user's approval, as the load trial's marks
   were. The tester's own timing checks still decide whether a run counts.
   **The record:** `resource-log`, a user unit on `madhouse-debian`,
   running since 2026-11-12 (checked by the methodology owner that day).
   Every 5 s it records each slice's CPU use, CPU, memory and I/O
   pressure, faults and memory, the same for the whole box, and each
   NVMe disk's load, from the kernel's own counters. Glances has no
   per-slice or pressure data, so it is not the source. The record stays
   on madhouse, one CSV per UTC day in the folder its README names,
   kept 30 days, out of HA and Seafile unless it turns out to matter
   (user, 2026-11-12). To judge a failure, run
   `resource-log window START END` over the failed check's window, with
   times as epochs or anything `date -d` takes; `--rows` prints every
   interval. A gap over 10 s in its output means the logger itself was
   starved or down. It is the Local LLM hosting session's tool
   (`monitoring/resource-log/README.md` in the Infrastructure library).
3. **The run guard records, it does not refuse** (user, 2026-11-12). At
   `shared/main` tag `2026.11.11.7`, shared/bench's chain runner still
   refuses a wall-clock check, NOT-RUN with "refused: another wall-clock
   test", when another role holds the bench lock or a process matches its
   `wallclock_patterns`, and drops builds to one job while it thinks a
   wall-clock test runs. That is changing, as harness work: the runner
   records other lock holders and matching processes in the run record
   and runs anyway, builds keep every core, and a hand-take role's lock
   (`android`, `can-tools`) does not count as a wall-clock test. Until
   that lands in a pinned tag, a NOT-RUN of that kind is the guard, not a
   result.
4. **The trials behind the slices.** On `NICKY-XPS`, 2026-10-15/16: interposer
   scenario #12 was voided when `charger_sim` stalled under a full-speed
   six-environment build. A full-speed build then failed (about 128
   STALLs a minute, worst 461 ms); a build at Windows Idle priority and
   one job passed (one STALL of 48 ms in 672 s) but took 3.3 times as
   long. gen-inhibit passed under a full-speed build (worst stimulus gap
   35 ms, against 8.9 ms). On `madhouse-debian`, 2026-11-11 (the Local LLM
   hosting session's `lcc/llm-load/README.md` in the Infrastructure
   library): with no controls the model was pushed out of memory and its
   generation collapsed; the 8G memory ceiling stopped the displacement;
   after the move to the slices above, the model's generation speed held
   at 0.96 to 1.03 of its baseline through the interposer's emulated run,
   a lighter load than planned (7 of its 27 simulators ran).
5. **HTTP round trips to the device are slowed by load too.** Under a build
   on `NICKY-XPS`, gen-inhibit's arm POST took 39 ms instead of 18 ms.
   Testers time windows from when a request was sent, not from when it
   returned.

## Simulators, virtual CAN buses and background processes

**Read this before running any multi-process simulator.** On 2026-09-01
the interposer bench flooded the house LAN for about twenty minutes, and
these rules come from that and later incidents. Each rule's history, with
its measurements, is in reverse-it's `$REVERSE_IT_DIR/simulators.md`,
which also covers reverse-it's own simulators (Coda's `flash_sim`). The
methodology owner keeps the rules in the two files in step; this one also
describes the shared tooling's run guard (user, 2026-11-08: the rules
here, the history there).

### A virtual CAN bus is not automatically local

`python-can`'s `udp_multicast` transport defaults to `hop_limit=1`, and the
kernel routes `239.0.0.0/8` out the default interface, so every simulated
frame goes on the real wire (`ip route get 239.0.2.1` names the LAN NIC).
**Always pass `hop_limit=0`:** the datagram is looped back to local sockets
and never transmitted, and delivery between processes on the same host is
unaffected. Do **not** also pin `IP_MULTICAST_IF` to loopback: `python-can`
joins the group with `INADDR_ANY`, so receivers would see nothing. The
interposer's `charge-interposer/bus.py` and gen-inhibit's
`gen-inhibit/tools/gi_vbus.py` do this and verify it at startup.

**TTL 0 confines a group only while a socket on this host has joined
it.** A TTL-0 datagram sent to a group no local socket has joined goes
out on the LAN NIC like a TTL-1 one, whether the sending socket is bound
or not and with multicast loopback on or off. Once any socket on the
host has joined the group, the sender's own or another's, it stays at
background. Measured on `madhouse-debian` by the gen-inhibit tester,
2026-11-12, with `eno1`'s counters. The methodology owner checked the
leak without a member the same day: all 200 sends of each window went
out. A `python-can` bus always joins its group, so a bus opened with
`hop_limit=0` is confined. A tool that sends to a group without a bus
open on it, such as a send-only socket, a replayer or a probe, is not:
it joins the group itself, as the buses do, and its counters are
checked as below.

### Measure, do not assume

Before and after any sustained simulator run, check the interface counters:

```bash
t0=$(cat /sys/class/net/eno1/statistics/tx_packets); sleep 5
t1=$(cat /sys/class/net/eno1/statistics/tx_packets); echo $(( (t1-t0)/5 )) pps
```

On `madhouse-debian` the box's own traffic sets the baseline, a few tens of
pps (25-29 before any sim ran and 13-27 with the interposer's emulated sims
running, measured by the interposer tester on 2026-11-11; 29 the same day
by the methodology owner). So compare against a reading taken before the
sim starts. Anything in the thousands while a sim runs means it is on the
wire.

### Killing background processes

**The Linux procedure silently does nothing on Windows.** Read the branch
for your machine.

#### Linux (`madhouse-debian`)

- **`pkill -f <pattern>` matches your own shell.** The Bash tool runs
  `bash -c '<your command>'`, so the pattern is in the wrapper's command
  line and `pkill -9` kills the shell: exit 1 or 143 with no output, and
  the rest of the command, file edits included, never runs.
- **Anchoring on the bare interpreter name misses subprocesses** started
  via `sys.executable`, whose command lines start `/usr/bin/python3.13`.

Match by path fragment and filter on `/proc/<pid>/comm`, which cannot match
the bash wrapper:

```bash
for pid in $(pgrep -f 'charge-interposer|gen-inhibit/tools' || true); do
  case "$(cat /proc/$pid/comm 2>/dev/null)" in
    python*) kill -9 "$pid" 2>/dev/null || true ;;
  esac
done
```

Verify afterwards (`pgrep -af <fragment>`, `ss -uanp | grep <port>`).
`pgrep` and `pkill` exit 1 when nothing matches, which aborts the rest of
the command under `set -e`, so append `|| true`.

#### Windows (`NICKY-XPS`, `NICKYDESKTOP`)

**The Linux recipe matches nothing, kills nothing and reports success.**
Git Bash's `pgrep` sees only the MSYS process table, not a simulator
started by `py` or PowerShell; native processes have no `/proc/<pid>/comm`;
and so `pgrep -af` comes back empty while a run is still on the bus.

- **Stop the task, not the process.** Kill the background task you started
  with the Bash or PowerShell tool's `run_in_background`; the process tree
  goes with it.
- **If you must go by PID,** use `Get-CimInstance` and exclude your own
  PID. A `CommandLine` filter also matches the PowerShell running it, so
  without `$PID` excluded it kills your own shell (exit 255), and a count
  taken that way includes your own invocation.

```powershell
$me = $PID
$pat = '(charge-interposer|gen-inhibit[/\\]tools)'   # build the pattern in a variable
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match $pat } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

**Verify by effect, not absence:** 0 fps on every segment (the real
check); `Get-Process -Name ninja,cmake,python -ErrorAction
SilentlyContinue` for native processes; `Get-NetUDPEndpoint` or
`netstat -ano | findstr <port>` for a listener that outlived its parent.

A check whose failure looks like success is not a check: when cleaning up
anything that transmits, prefer evidence that the traffic stopped over
evidence that a process is missing (see "Filters that fail silently" in
`$REVERSE_IT_DIR/AGENTS.md`).

### One simulator bench per set of ports

Two simulator benches on the same multicast groups and ports see each
other's frames, even with `hop_limit=0`, so they never run at once (user,
2026-10-23). Benches on different groups and ports may run together: move
one with its port option (user, 2026-11-12, replacing "one simulator run
per machine" of 2026-10-23). With `hop_limit=0` their frames stay on
loopback, so the cost of two at once is CPU, which the slices handle (see
"Parallel work on the shared machine").

The shared bench tooling will check this before it starts anything that
transmits:
- **It checks its own ports:** UDP sockets bound to the groups and ports
  it is about to use. It refuses if it finds any, and names them.
- **It listens first.** It opens its own segments and listens before
  starting its simulators. It should hear nothing; any frame means another
  bench or an orphan is on those ports, and it refuses.

**Until the shared tooling has this check, nothing enforces it** (user,
2026-10-23). Before starting a run, check your ports yourself, the same two
ways. If a check finds a bench, find out whose it is before you kill
anything: it may be another session's run. When the check lands, this
section changes in the same merge. It is a different check from the chain
runner's wall-clock guard (see "Parallel work on the shared machine",
point 3).

## Working rules

### Files and folders

- **Every folder has an `.md` describing its contents** (a `README.md` or
  an overview file), unless upstream WiCAN already has its own convention
  for that folder. Update it when you add, move or remove files.
- **Work local to this worktree goes in `scratch/`** at the repo root, which
  is gitignored, with a subfolder per project and tool, e.g.
  `scratch/gen-inhibit-mutants/<name>`. That covers build trees, mutant
  copies, logs, anything else a committed script can regenerate, and
  scratchpads for work on this repo's own files. Not the system temp
  folder. `build/` stays where the build puts it. A scratchpad worth keeping
  is committed where its role's files live, and the folder's `.md` is
  updated.
- **Removing scratch.** Git for Windows marks the files in a repo's
  `.git/objects/` read-only, and `shutil.rmtree` can't delete a read-only
  file on Windows. With `ignore_errors=True` it fails without a word, and
  the folder stays (2026-10-31: 1,085 run folders and 472K files left on
  NICKY-XPS, which stalled Windows Search). So a test that writes under
  `scratch/` removes its own folder when it ends, pass or fail, in a
  `finally` or an exit handler, with an error handler that clears the
  read-only flag and retries, and on Windows with long-path-safe paths
  (`\\?\`). Never `ignore_errors=True`: a folder it can't remove is printed
  with the reason.
- **Explorations that use reverse-it's data** (digging through CAN logs,
  captures, notes) put their scratchpads and results in reverse-it's
  `artifacts/` folders, under its rules (see "Reverse-it").
- **Line endings: LF everywhere** (user, 2026-10-23), except `.bat` and
  `.cmd` files, which are CRLF. The repo's `.gitattributes` files pin this
  and carry the same lines: the root one on `gen-inhibit/main` and on
  `charge-interposer/main`, `shared/.gitattributes` and
  `charge-interposer/.gitattributes`. Change them together. Component
  digests and embedded files hash the bytes on disk, so line endings change
  them. **Save files as LF,** and don't rely on
  `git status` to catch a CRLF file. A file committed as LF and then saved
  as CRLF shows as modified, with an empty diff. But when the CRLF bytes are
  what git last checked out or added, such as a file a tool generated as
  CRLF that was then added, `git status` shows it clean while the build and
  the digest see the CRLF bytes. `git ls-files --eol` shows the line endings
  on disk (`w/crlf`).

### Python

- **Use the Python command in `local-paths.md`,** never a bare `python` or
  `python3`.
- **No non-ASCII characters in anything Python prints or writes without an
  explicit encoding.** The Windows console (cp1252) raises on characters
  like arrows, approximately-equal, multiplication or plus-minus signs. Use
  ASCII forms: `->`, `~=`, `x`, `+/-`.
- **Inline or scratchpad.** Inline `-c` is for simple one-off lookups. Use
  a scratchpad script for anything multi-step, reused across phases, or
  worth re-running.

### Editing files

Make file changes with the Edit and Write tools, whatever the auto mode
note suggests about `sed`, heredocs or short scripts. Read the file first:
both tools refuse a file this session hasn't read, a new worktree's files
included. On Windows a long heredoc can fail to parse and apply nothing
(2026-11-01). An edit script leaves a file behind that is worth nothing
once the edit is made. If a one-off edit really needs a script, write it in
Claude Code's session scratchpad, not `scratch/`, and never commit it or
copy it into a run folder.

### Searching

- **Prefer Bash `find` and `ls` over PowerShell** for file searches.
- **Scope every `find` to this worktree,** or to one named folder outside
  it with `-maxdepth 2`. Never from `/`, `~` or a user-profile root. If a
  path outside isn't in `local-paths.md`, ask the user rather than search.

### Plans, notes and memory

- **Plans live in reverse-it,** in the project's plans folder, reached
  through `$REVERSE_IT_DIR`. Never in `~/.claude/plans/`.
- **The plan step gate.** After each step of a plan, stop and present the
  findings to the user. Only after explicit approval: write the findings to
  the notes, clear the step's worklog from `.claude/memory/`, update the
  plan to mark the step done, and start the next step.
  "Implement the plan" is not permission to run every step without
  stopping.
- **No unreviewed claims get committed.** Before an interpretation,
  hypothesis or assumption goes into a committed file, or into reverse-it's
  synced notes, present it to the user and get explicit approval.
  Hypotheses may go in, framed as hypotheses, once the user has seen them.
  The commit is the gate; the conversation is the proposal space.
- **Verified facts are not claims for this rule** (user, 2026-11-08). A
  measurement, a command's output, a commit, a test result or an install,
  checked in the session, is written directly, saying where it was
  checked. A user ruling relayed by the session where it was made is
  written once checked against that session's record (its tracker, spec
  or evidence doc), naming that session. Only a mismatch goes back to the
  user. Interpretations, hypotheses and assumptions still go to the user
  first.
- **Unreviewed findings go in a worklog in `.claude/memory/`,** which is
  gitignored and local to this worktree, so that they live in a file and
  not only in context. It is a worklog for the current plan step and must
  not go stale: once a finding is confirmed or disproven and the step
  closes, move what was approved into the notes or the plan and delete the
  worklog entry.
- **Don't write to Claude Code's auto-memory** in `~/.claude/`, or to
  `~/.claude/plans/`.
- **`CLAUDE.md` holds only `@AGENTS.md`.** Instructions go in this file.

