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
It is gitignored and filled in separately in each worktree. Read it before
running Python, a build or a harness. For every Python call, use the command
it names, never a bare `python` or `python3`. If it is missing, copy
`local-paths.template.md` to it and follow the template's text.

## Reverse-it: the project behind this repo

Raw CAN logs and captures, investigations, vehicle notes, ESP plans, specs not
yet moved here, and the bench log and bench-holder all live in the reverse-it
project folder in SeaDrive, not in this repo. Its path is in
`$REVERSE_IT_DIR` and in `local-paths.md`. If the variable is unset, ask the
user.

For any of that work, first read `$REVERSE_IT_DIR/AGENTS.md` in full and
follow it there: its data-source, Python, artifact and analysis rules. Use its
tool set (`tools/`, `canre/`, `projects/vtrux/tools/`) rather than writing new
tools here. Its tools and scratchpads run from its root, so `cd` to
`$REVERSE_IT_DIR` first. Results go where its rules put them, not into this
working tree. Nothing from reverse-it is committed here unless it passes the
pre-push check.

**Identifiers in reverse-it's files**, such as the WiCAN's SSID and the boards'
MACs in `bench-holder.md`, the evidence docs and the settings files, may be
read into context and edited like anything else. They never reach git: the
pre-push check stops them. Passwords are never read.

## can-tools: the CAN adapter package

`canbench`, the package both projects use to detect and open real CAN
adapters, comes from the can-tools repo, cloned once on each machine at
`$CAN_TOOLS_DIR` (in `local-paths.md`; refused if unset or if it holds no
`canbench/`). Each project pins the can-tools commit it uses and moves its own
pin. The project's pin tool (the interposer's `can_tools_pin.py install`)
writes the pinned commit's `canbench/` into `scratch/can-tools/<commit>/`, and
the harness loads it from there by file. Neither that folder, the clone nor
reverse-it goes on `sys.path` for it, and a `canbench` already imported from
anywhere else is refused. Setup is in `$REVERSE_IT_DIR/tools-reference.md`,
"can-tools".

## Worktrees and branches

Each machine has one clone, and a worktree per role beside it, in one plain
folder (its path is in `local-paths.md`).

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
anchor worktree add --detach ../shared-<tag> <tag>`. Work moves between
machines only through origin.

**A `shared-<tag>/` checkout is made read-only when it is made:** `chmod -R
a-w ../shared-<tag>/shared`, leaving the checkout's root and its `.git` file
writable. The pins hash those files, so a stray write, such as a
`__pycache__`, changes the digest and the pin check refuses. Python run
from a pinned checkout, or with one on `PYTHONPATH`, runs with `-B` or
`PYTHONDONTWRITEBYTECODE=1`, children included; on Windows that is the only
guard, since a read-only flag does not stop new files in a folder. To remove
a checkout, `chmod -R u+w` its `shared/` first, then `git worktree remove`.

**Start-up check.** Before anything else, check that you are in your role's
worktree, on your role's branch (`git branch --show-current`). If not, stop
and tell the user.

**Branches.**
- `<project>/main` is the reviewer's, and the only branch it writes.
- The implementor and tester work on `<project>/implementor/<topic>` and
  `<project>/tester/<topic>`, each in its own worktree. Start a new topic
  from `<project>/main`.
- Every branch is visible from every worktree, so the reviewer reads the
  others' work from its own: `git log -p <project>/main..<project>/tester/<topic>`.
- Shared code has its own branches, `shared/main` and
  `shared/<component>/<topic>` (see "Shared code").
- **Always merge, never rebase.** Manifests, `VERIFICATION.md` entries and
  run records name commits, and a rebase would rewrite them.
- **Unpushed history is corrected, not patched over.** Commits on no origin
  branch may be re-made, amended or rebased, for example to keep a pre-push
  hit out of history. Re-make them on a new branch name, never push the old
  branch, and in the same go correct every note, tracker entry or record that
  names an old commit. Once a commit is on origin, the merge-only rule holds.

**Skills follow the branch.** Each worktree has the role skills of the branch
it has checked out. Each branch keeps only its own role's skill current;
changes reach other branches by cherry-pick. After a cherry-pick that touches
your skill, re-read its `SKILL.md`. Each worktree has its own
`.claude/settings.local.json`.

**Never build WiCAN firmware from an interposer worktree or branch.** A
WiCAN image's name and compiled-in commit come from the whole tree, so
interposer commits or files would change or block WiCAN builds. An
interposer run flashes the WiCAN only with a gen-inhibit image built
elsewhere and pinned as a tool of its harness, as gen-inhibit pins its X2
images (`gen-inhibit/tools/x2_pin.json`: the gen-inhibit commit and the
image's size and hashes). The run loads it at its start through the shared
flasher (`shared/bench`), OTA over the WiCAN's hotspot, with the board's
identity read before and after. Until the run's settings choose a Wi-Fi
backend that reaches the hotspot (a USB Wi-Fi adapter, or the phone over
adb), it loads the same pinned image over USB with the shared flasher's
esptool path.

## Session rules

### Read instruction docs in full

`AGENTS.md` is loaded into every session by `CLAUDE.md`, fresh from disk,
and again after every compaction, so it is never re-read by hand. Read each
of these from top to bottom, in full, every time you read it:
`local-paths.md`, every `SKILL.md` you act under, the skills' `README.md`, the
top-level `README.md` of the project or component you are working on, and in
reverse-it its `AGENTS.md`. Not a search or an excerpt: a partial read feels
like a full one, and the rule you skipped is the one you then break. If a file
is too long for one read, read it in parts.

### After a compaction, re-read your skill and docs

A compaction summary records what the session did, not what the docs say.
Before the first action a doc governs, re-read it in full: `local-paths.md`,
your role skill's `SKILL.md`, and `$REVERSE_IT_DIR/AGENTS.md` if the step
works in reverse-it. **Skills especially:** Claude Code re-attaches each
skill invoked earlier as the text it had when invoked, not the current
`SKILL.md`, so a skill edited or cherry-picked since then would be followed
in its old form.

### Messages between sessions

**Send a message only when the receiving session has to do something** with
it: act on work, answer a question, cherry-pick a commit, or change a file it
owns. Every message wakes the session and re-sends its whole context, so a
message it only reads costs as much as one it acts on.
- **No notices, FYIs or status updates** the receiver will not act on, and no
  "done", "received" or "thanks" replies. A requester that can see the result
  for itself (a pushed commit, a merge on origin, the bench log, a file) checks
  it when it next needs it; a reply is sent only when the result is not
  visible or the requester must act next.
- **One message carries everything** the receiver needs to act, and work for
  the same session is batched into one message where it can wait.
- **Don't relay what the receiver can read** from origin, a tracker, the bench
  log or a file: name where it is only if it must act on it.
- **A report that asks the reviewer to merge or review always gets one
  answer:** merged, with the commit (and tag), or sent back, with why. Its
  sender is waiting on it, so it is a go signal, not a notice; one line is
  enough.
- **Any other merge or cherry-pick is messaged only when it gives the
  receiver its next job:** pick this onto your branch, start this work from
  it, pin or read it, or fix what it broke. "Picked as X" is not sent, and a
  merge is not copied to sessions that did not ask for it.
- **Don't wait on an answer you don't need.** After sending a report, start
  the next work you already hold, unless it needs that answer.

### Compacting your own context: `/self-compact`

A session can compact its own context and resume without anyone at the
keyboard, with the `/self-compact` skill. It types `/compact` into this
session's terminal and then, once the compaction is done, one resume message
naming the next step. By default it resumes. A session sure that nothing
more is needed from it until a message, a task notification or the user
arrives may pass `--compact-only` instead, and then nothing is typed; when in
doubt, resume, since a wedged session costs more than a needless resume. It
is the only sanctioned way for a session to type into its own input.

- **When:** only when the user asks for it, at the compaction point your
  role skill names, or when the idle-compaction hook asks for it. A session
  without a role skill has no compaction point of its own, but the hook still
  applies.
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
- **After it:** re-read your role skill and the docs the step needs before
  acting (see "After a compaction").
- **If auto mode blocks it,** do not retry it, reword it or run it another
  way. Carry on without compacting and tell the user in one line.
- **Where it works:** in tmux on Linux, and in terminal Claude Code on
  Windows. Not in Claude Desktop.

The procedure, and the rules for the two messages, are in the skill.

### Pausing

To wait for a fixed time, run `bash session_pause.sh <seconds>` at the repo
root with the Bash tool's `run_in_background`; you are notified when it ends.
Do not chain `ScheduleWakeup` calls instead.

## Specification-driven components

Each project has a written specification that is the source of truth for its
behaviour. The user reviews and edits the spec, not the code. A session
touching a component runs one of three role skills -- `/reviewer`,
`/implementor` or `/tester` -- with the component name, and finds its files
in the project's file map: `gen-inhibit/README.md` or
`charge-interposer/README.md`, owned by that project's reviewer. A project
branch may hold only its own project's folder. Behaviour changes go into the
spec first (with the user's approval, per the notes rule) and into the code
second, never the other way round. **Any session that notices code, a test,
a note or a measurement contradicting the spec -- even while working on
something else -- must fix it when the fix is unambiguous and the file is
one its role owns, must otherwise tell the owning session, and must in every
case bring it to the user's attention.**

**Surfacing is not deciding.** Bringing a contradiction to the user's
attention means saying that it exists and which session holds it. Any change
to what the spec says is framed by the reviewer, put to the user in the
reviewer's session, and written into the spec there. Other sessions do not
restate a reviewer-held question to the user, even to recommend an option:
the user has one place to argue it.

Specs not yet moved into this repo are in reverse-it; the file map gives
their path under `$REVERSE_IT_DIR`.

**Project layout.** A project folder holds its file map,
`<project>/README.md`; its firmware build root, `<project>/firmware/`
(gen-inhibit's is the upstream WiCAN tree as upstream lays it out); and its
host-side tooling and tests, laid out as the file map says. **No path in the
tree may have a branch's name:** an IDF `main/` directly in a project folder
would make `<project>/main` both a branch and a path, and git then refuses
the bare branch name. **Beside the project folder, a project branch's root
holds only the files every branch shares:** `.claude/`, `.gitattributes`,
`.gitignore`, `AGENTS.md`, `CLAUDE.md`, `LICENSE`, `README.md`,
`local-paths.template.md` and `session_pause.sh`.

### Shared code

Shared components live under `shared/<component>/` on `shared/main` (plan:
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`, goal
20). A shared file has no single owning project. Whichever project needs a
change makes it, in the role that owns that kind of file (implementor for
product code, tester for harness), on a `shared/<component>/<topic>` branch,
and that project's reviewer merges it. The other project stays on its pinned
version until it chooses to take the change. Writing a file first does not
make a project its owner. A shared component's spec is drafted by the
reviewer of the project that needs the change and ruled on by the user in
that reviewer's session; the other reviewer reads it before merge.

- **Never merge between `shared/main` and a project branch,** in either
  direction. A project takes shared code only as a pinned tag.
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
through it. It commits them on its own `methodology/<topic>` branch and tells
the sessions that must cherry-pick them, batched where it can. Each one
cherry-picks the commit onto its own branch, then re-reads the changed skill
in full; it does not reply, since the owner checks the pick on origin.
**Never edit this file or a skill by hand on a role branch:** a hand edit
conflicts with the next cherry-pick.

**Docs change with the tooling.** A change to tooling or a workflow that
this file or a role skill describes is not finished until the text says the
same thing. The session making the change sends the methodology owner the
text change with it. The reviewer pushes the tooling change when it is
ready, cherry-picks the methodology commit when it comes, and keeps the
change open in its tracker until then.

**Plans, handoffs and working logs** belong to the role session that writes
them, named on an `**Owner:**` line at the top. A file without one gets an
owner from the reviewer. A file that holds validation the spec rests on is not
a working log for this rule; the reviewer rules on it. A file the reviewer
rules a **record**, closed to new material, carries this header, and its
file-map entry uses the same words:

```
**Owner:** <role> (<who ruled: the user, or which session>, <date>).
**Status: record.** Closed to new material, because <one-line reason>.
**May change:** only dated corrections beside a passage found to be wrong,
written by the owner.
**New material goes to:** <file> (<its owner>).
**Reopening it:** the user's call.
```

Don't label a file "frozen" or "locked".

**Published-interface documents are spec:** the diag schemas and their
decode DBCs that the tester tests against belong to the reviewer, and
changes need the user's approval like any spec change. Otherwise the
implementor could change what correct means.

### Spec, validation and verification

- **`VALIDATION.md`: why each requirement is worded as it is.** It holds the
  measurements, readings, corrections and user rulings behind each
  requirement.
- **`VERIFICATION.md`: test evidence that a specific commit meets the spec.**
  A pass verifies the code it ran on: it carries to any build with the same
  code hash (shared/tools R3), and to nothing else, so a commit that changes
  no code keeps its passes. When code is ported or rewritten, the old code's
  pass entries carry no weight for the new code: they are not copied across
  or cited as validation; their tests can be pointed at the new code.
- **A check that a `VERIFICATION.md` entry rests on is committed harness,**
  run from a commit the entry names, and so is the can-fail that shows it
  can fail. A hand-made script in `scratch/` is not verification.

**Never write "evidence" or "the evidence doc" without saying which:** name
`VALIDATION.md` or `VERIFICATION.md`, in docs, skills, trackers and messages.

Rules for `VALIDATION.md`:
- **A verification finding that led to a correction** (a NOT AS SPECIFIED, a
  defect) is cited only as what exposed the problem. The validation is the
  correction or the ruling that followed.
- **A measurement made on a different toolchain, board or code base is not
  the basis for a requirement on this one.** At most it is supporting
  context, labelled as such.
- **Each entry's status is "validated" or "basis pending".** Validated means
  its basis is recorded and cited. Basis pending is allowed but must say what
  is missing. A requirement that rests on a user ruling cites the ruling and
  its date. Neither status says whether the code meets the requirement; only
  `VERIFICATION.md` does. Don't write "complete", which reads as
  "implemented".

## Pushing

**The reviewer merges and pushes.** The implementor and the tester commit on
their topic branches in their own worktrees and never push. When work is
ready, they tell the reviewer the branch and the commit range. In its own
worktree, the reviewer merges the topic branch into `<project>/main`, runs the
pre-push check, and pushes. It pushes the topic branches too, after the same
check, as often as it can, unless the work isn't worth keeping: origin is
this repo's only copy off the disk. All of a project's role sessions run on
one machine, in worktrees of the same anchor clone, so the reviewer sees every
topic branch without a push.

This is the user's standing approval for routine pushes, so the reviewer
doesn't ask per push. It does ask before a force push, a branch delete, or
any push the check stops on something real.

**A false positive doesn't stop a push.** A hit the manual read clears as a
false positive, such as a common word, a version string or an accepted name,
is pushed past. The session tells the methodology owner, who fixes the scan,
and every session treats the same hit the same way until the fix lands.

- **On a merge conflict,** the reviewer aborts the merge and sends it back.
  The requester merges `<project>/main` into its topic branch and asks again.
- **If origin has moved,** the reviewer merges it into `<project>/main`
  itself, then checks and pushes.
- **The reviewer doesn't commit in another session's worktree** or on
  another session's branch.
- **Shared branches:** the reviewer whose project needs a shared change
  merges it into `shared/main` and pushes, after the same check.
- **The methodology owner** pushes its own `methodology/<topic>` branches,
  after the same check.

### The pre-push check

It covers every commit the push would publish, not just the tip. Run
`git fetch`, then

```bash
git log -p <branch> --not --remotes=origin
```

which lists the commits on no origin branch yet. Stop and bring it to the
user if it contains:
- credentials, tokens, keys, WiFi SSIDs or passwords (the bench's are passed
  in by environment variable, never committed);
- IP addresses or other home-network details;
- work-machine names or paths;
- personal information beyond the commit author;
- VINs;
- service-manual or other copyrighted excerpts;
- captures or logs not meant to be published.

Accepted: the machine names `NICKY-XPS`, `madhouse-debian` and
`NICKYDESKTOP`; bare `madhouse`, in prose and file names; WiCAN firmware
defaults such as its hotspot addresses `192.168.80.1` and `192.168.80.2`; and,
as whole paths only, `~/.espressif`, `~/esp/esp-idf` and
`~/esp/esp-idf-v<version>`.

**The scan script is an aid to this review, not a replacement for it.**
Run it first:

```bash
<python> $REVERSE_IT_DIR/firmware/prepush-scan/prepush_scan.py <worktree> <branch>
```

It fetches, then scans every commit-message line, every added line and every
added or changed file's path in that range, for the categories above and the
real values in its private value file, and prints every hit masked. Exit 0 is
clean, 1 is hits to review, 2 is refused: nothing was checked, so don't push.
Then read the `git log -p` output yourself for what no pattern matches, such
as copyrighted text or personal details. Its README
(`firmware/prepush-scan/README.md`) has the details and the known limits.

## The bench and builds

### The shared bench

**The bench is the whole setup** -- both boards, the PC, the USB adapters and
the CAN segments -- and one hardware test runs at a time. **The lock is
`$REVERSE_IT_DIR/firmware/bench-log.jsonl`,** an append-only log written only
by `shared/bench`'s `bench_lock.py`, through its command line or the chain
runner, on the bench host the log names (spec: `shared/bench/SPEC.md`).
Nothing edits or deletes a line of it. The rules for taking, loaning and
handing back, and each board's state, are in
`$REVERSE_IT_DIR/firmware/bench-holder.md`. Both files stay out of git. The
bench host is `madhouse-debian`.

- **The lock is a mutex, not a hand-over guarantee.** A free lock is taken
  without asking: `take` checks and claims it in one step. Take whenever you
  have hardware work, and release when you are not actively running. The
  holder does to the bench what its work needs, including flashing and
  rebooting either board, moving adapters between drivers and sending on
  either bus. At release nothing it started is still running on the bench,
  and its release note reports the state of everything it touched; nothing
  is put back. The next holder sets up what it needs. Details: SPEC sections
  6 and 8.
- **Who takes:** the testers, an implementor on its tester's loan for
  bring-up, and two hand-take roles with no run: `android`, the Android
  sessions' runs that touch the bench adapters, and `can-tools`, the CAN
  replay tooling session. They build images as the images' owners' tooling
  does and do not change their source.
- **To wait for a held lock, run `bench_lock.py wait`** (`shared/main`
  from tag `2026.11.16.3`) with the Bash tool's `run_in_background`:
  `wait --who <role> --pid <session's long-lived process> --note "<what
  for, how long>" [--timeout <s>]`. It queues a request and, when the lock
  frees, takes it for you under that PID, first in first out among live
  waiters, one per free, then exits 0, which wakes you holding the bench.
  Exit 3 is gave up (timeout, or the PID gone), after withdrawing its
  request; 2 is refused. Plain takes still ignore the queue. To start a run
  on the bench you won, pass the runner `--from-hold <that PID>` (also
  `vtrux_bench.accept run --from-hold`): it hands your hold to the run in
  one step, so the bench is never free in between. An acceptance run
  (`vtrux_bench.accept run`, from `shared/main` tag `2026.11.17`) needs
  neither: under the bench log it queues for the bench itself at its first
  bench part, as `wait` does, under its own PID and with no timeout. It
  keeps the bench through its last bench part, so no session has to be
  asked to keep off it, and a `wait` queued meanwhile takes the bench only
  after the last part releases, or during a part's pause. Nothing before
  the first bench part holds the lock. A STOP or SIGTERM while it waits
  withdraws its request, and its bench parts are NOT-RUN. Details: SPEC
  sections 3 and 6, and ACCEPT-SPEC A18.
- **`canmain`, CAN1's Innomaker, is for the Android app's two-bus tests.**
  The chain runner brings it down through `canctl` after its own take and at
  every resume, before the start hooks; a `canctl` failure, or `canmain`
  still up after it, stops the bench (`shared/main` from tag `2026.11.13.2`;
  an older pin refuses to start instead).
- **Root operations on the CAN adapters** (links up and down, an
  Innomaker's driver binding) go through `canctl`, run as `sudo -n
  /usr/local/sbin/canctl ...`. It is the CAN replay tooling session's, and
  its README is `$REVERSE_IT_DIR/tools/bench/canctl/README.md`.
- **Moving the bench to a new host** (SPEC section 4): on the old host, a role
  that may take runs `bench_lock.py retire --to <new host> --reason ...` while
  the lock is free; the new host runs `init` once the renamed log has synced
  there.

### Build tooling

**Build tooling is specified.** gen-inhibit's release builds are made by
the shared build tooling (`shared/tools`) under `shared/tools/SPEC.md`, at the
version gen-inhibit pins in `gen-inhibit/firmware/shared_pins.json`. They run
from that version's read-only checkout `../shared-<tag>/`, started by
`gen-inhibit/vtrux_tools.py`, which refuses and prints the command that
creates the checkout if it is missing. A gen-inhibit prod build is such a
release build, and none is made during the shared-code port. gen-inhibit's
bench and dev builds are `idf.py` / `ninja` builds, which need not meet the
release rules (shared/tools R14).

The interposer's ESP-IDF project is `charge-interposer/firmware/`, with
arduino-esp32 3.2.1 as a component. Its build identity follows shared/tools,
at the version `charge-interposer/firmware/shared_pins.json` pins. Its bench
and dev builds are the shared tooling's dev builds (shared/tools R31), one
build folder per variant, made by `charge-interposer/vtrux_tools.py build
<variant>`, with the variants in `charge-interposer/firmware/vtrux_build.json`.
No interposer release build is made during the port.

**One IDF version: ESP-IDF v5.4.2** for both projects, an install with its own
tools folder, so `IDF_TOOLS_PATH` is set before the export. Run a build only
in a shell exported for it. The shared tooling's `check-env` (shared/tools
R32) checks that; release builds and the tooling's dev builds run it
themselves, and an `idf.py` / `ninja` build runs
`<python> -B <project>/vtrux_tools.py check-env` first. A bench or dev build
under another IDF is not refused (R33, R14): the other IDF rewrites
`dependencies.lock`, so a changed lock in `git status` after a configure is
the sign. The v5.4.1 install is only for branches from before gen-inhibit's
port step 1d. Each machine's recipe is in `local-paths.md` and
`$REVERSE_IT_DIR/tools-reference.md`.

### Parallel work on the shared machine

**Parallel work is allowed by default.** Builds, emulated-time suites, mutant
runs, wall-clock tests, bench runs, other sessions' work and the local LLM
all run at the same time. Nobody waits for other work to end, asks for a
window, or holds work back for someone else's. Only two things are
exclusive, and both are physical: the bench hardware, through the bench lock,
and a simulator bench's virtual-bus groups and ports (see "One simulator
bench per set of ports"). Contention for CPU, memory and disk is handled by
where work runs (point 1), and a failure contention could explain is triaged
(point 2).

**Wall-clock tests** are tests whose stimulus is sent by the wall clock on
the shared machine: the interposer's simulator scenarios and gen-inhibit's
bench runs. Emulated-time suites are not.

1. **Placement on `madhouse-debian`.** Start each heavy job as a scope in
   its slice: `systemd-run --user --scope --slice=<slice> <command>`. A scope
   keeps the shell's environment, so a shell exported for ESP-IDF builds in
   it; a service sees none of the `IDF_*` variables, and `check-env` refuses
   it. The slices:
   - `bench.slice`, CPUWeight 2000: wall-clock tests and everything a run
     starts for them, such as simulators, witness readers, adapter I/O and
     the run's own flash or OTA load.
   - `llm.slice`, CPUWeight 1000: the local LLM server.
   - `app.slice`, CPUWeight 100: the Claude Code sessions and anything else
     started from a terminal without a slice.
   - `build.slice`, CPUWeight idle, MemoryHigh 8G: every build, mutant run
     and emulated-time suite, from every session. Builds may use every idle
     core. If an emulated-time suite's timeout trips under idle, the fallback
     is a small weight, such as 20.

   Read the placement back: `cat /proc/<pid>/cgroup` names the slice, and
   `systemctl --user show <slice> -p CPUWeight -p MemoryHigh` gives its
   settings. `nice` and process priority do not replace the slice.
2. **Triage a failure that contention could explain.** A timing failure, such
   as a simulator STALL, a stimulus gap, a timeout or a check voided on
   timing, is judged against the machine's resource record over the failed
   check's window before it counts either way:
   - **Starved:** the record shows the run's slice short of CPU, memory or
     disk (pressure, major faults or swap growth above the marks), or the
     simulator's own lateness measure shows it was not scheduled. Not a
     product result: re-run the check, and send the finding to the Local LLM
     hosting session, which tunes the load controls.
   - **True error:** the record is clean over the window. It counts.
   - **Unclear:** re-run once. A repeat with a clean record is a true error.

   Each project's tester sets its marks from measured quiet data, in its test
   plan with the user's approval. **The record** is `resource-log`, a user
   unit on `madhouse-debian` that every 5 s records each slice's and the
   box's CPU use, pressure, faults and memory, and each disk's load. Run
   `resource-log window START END` over the failed check's window (times as
   epochs or anything `date -d` takes; `--rows` prints every interval). A gap
   over 10 s in its output means the logger itself was starved or down. It is
   the Local LLM hosting session's tool (`monitoring/resource-log/README.md`
   in the Infrastructure library).
3. **The run guard records, it does not refuse.** shared/bench's chain runner
   runs a wall-clock check even when another role holds the bench lock or a
   process matches its `wallclock_patterns`, and names them in the run
   record's `wallclock-others` event. A hand-take lock does not count as a
   wall-clock test, and builds keep every core. On Linux, with its
   `placement` setting, the runner starts wall-clock checks in `bench.slice`
   and builds in `build.slice` and reads each placement back; one that does
   not read back FAILs that group's placement check, and its checks are
   NOT-RUN. A run with board work refuses to start outside the wall-clock
   slice and prints the `systemd-run` command. A NOT-RUN "refused: another
   wall-clock test" comes only from a pin older than `shared/main` tag
   `2026.11.12`.
4. **Time HTTP windows from when the request was sent,** not from when it
   returned: load slows round trips to the device too.

## Simulators, virtual CAN buses and background processes

**Read this before running any multi-process simulator.** These rules come
from incidents, the first a twenty-minute flood of the house LAN by the
interposer bench. Each rule's history and measurements are in
`$REVERSE_IT_DIR/simulators.md`; the methodology owner keeps the two files in
step.

### A virtual CAN bus is not automatically local

`python-can`'s `udp_multicast` transport defaults to `hop_limit=1`, and the
kernel routes `239.0.0.0/8` out the default interface, so every simulated
frame goes on the real wire (`ip route get 239.0.2.1` names the LAN NIC).
**Always pass `hop_limit=0`:** the datagram is looped back to local sockets
and never transmitted. Do **not** also pin `IP_MULTICAST_IF` to loopback:
`python-can` joins the group with `INADDR_ANY`, so receivers would see
nothing. The interposer's `charge-interposer/bus.py` and gen-inhibit's
`gen-inhibit/tools/gi_vbus.py` do this and verify it at startup.

**TTL 0 confines a group only while a socket on this host has joined it.**
A TTL-0 datagram sent to a group no local socket has joined goes out on the
LAN NIC like a TTL-1 one. A `python-can` bus always joins its group, so a bus
opened with `hop_limit=0` is confined. A tool that sends without a bus open
on the group, such as a send-only socket, a replayer or a probe, joins the
group itself, and its counters are checked as below.

### Measure, do not assume

Before and after any sustained simulator run, check the interface counters:

```bash
t0=$(cat /sys/class/net/eno1/statistics/tx_packets); sleep 5
t1=$(cat /sys/class/net/eno1/statistics/tx_packets); echo $(( (t1-t0)/5 )) pps
```

On `madhouse-debian` the box's own traffic is a few tens of pps, so compare
against a reading taken before the sim starts. Anything in the thousands
while a sim runs means it is on the wire.

### Killing background processes

**The Linux procedure silently does nothing on Windows.** Read the branch
for your machine.

#### Linux (`madhouse-debian`)

- **`pkill -f <pattern>` matches your own shell.** The Bash tool runs
  `bash -c '<your command>'`, so `pkill -9` kills the wrapper: exit 1 or 143
  with no output, and the rest of the command never runs.
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
`pgrep` and `pkill` exit 1 when nothing matches, so append `|| true`.

#### Windows (`NICKY-XPS`, `NICKYDESKTOP`)

**The Linux recipe matches nothing, kills nothing and reports success:** Git
Bash's `pgrep` sees only the MSYS process table, and native processes have no
`/proc/<pid>/comm`.

- **Stop the task, not the process.** Kill the background task you started
  with `run_in_background`; the process tree goes with it.
- **If you must go by PID,** use `Get-CimInstance` and exclude your own
  PID, or the `CommandLine` filter also kills your own shell (exit 255).

```powershell
$me = $PID
$pat = '(charge-interposer|gen-inhibit[/\\]tools)'   # build the pattern in a variable
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match $pat } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

**Verify by effect, not absence:** 0 fps on every segment (the real check);
`Get-Process -Name ninja,cmake,python -ErrorAction SilentlyContinue`;
`Get-NetUDPEndpoint` or `netstat -ano | findstr <port>` for a listener that
outlived its parent. When cleaning up anything that transmits, prefer
evidence that the traffic stopped over evidence that a process is missing.

### One simulator bench per set of ports

Two simulator benches on the same multicast groups and ports see each
other's frames, even with `hop_limit=0`, so they never run at once. Benches
on different groups and ports may run together: move one with its port
option.

**The chain runner enforces this for checks that declare their buses**
(`shared/main` from tag `2026.11.16`). A check whose suite gives it
`Check.bus`, run with the `bus_alloc` setting, gets multicast groups and
ports of its own, handed to it as `{bus_alloc}` on its command line and in
`VTRUX_BUS_ALLOC`. Before it starts, the runner checks that none of those
ports is bound and listens on its segments; a bound port or any frame heard
makes it NOT-RUN "refused". While it runs, an isolation monitor on its
segments makes it VOID on a frame from a sender, or a socket on its ports,
that is not the check's own. `hosts.<host>.wallclock_slots` above 1 runs
that many such checks side by side and needs `bus_alloc`. A wall-clock
check with no `Check.bus` takes every slot.

**Every other run is checked by hand.** Before starting it, look for UDP
sockets bound to the groups and ports you are about to use, then open your
segments and listen; you should hear nothing. If you find a bench, find out
whose it is before you kill anything.

## Working rules

### Files and folders

- **Every folder has an `.md` describing its contents** (a `README.md` or
  an overview file), unless upstream WiCAN already has its own convention
  for that folder. Update it when you add, move or remove files.
- **Work local to this worktree goes in `scratch/`** at the repo root, which
  is gitignored, with a subfolder per project and tool, e.g.
  `scratch/gen-inhibit-mutants/<name>`: build trees, mutant copies, logs,
  anything a committed script can regenerate, and scratchpads for work on
  this repo's own files. Not the system temp folder. `build/` stays where
  the build puts it. A scratchpad worth keeping is committed where its role's
  files live.
- **Removing scratch.** A test that writes under `scratch/` removes its own
  folder when it ends, pass or fail, in a `finally` or an exit handler, with
  an error handler that clears the read-only flag and retries (Git for
  Windows marks `.git/objects/` read-only), and on Windows with long-path-safe
  paths (`\\?\`). Never `ignore_errors=True`, which fails without a word: a
  folder it can't remove is printed with the reason.
- **Explorations that use reverse-it's data** put their scratchpads and
  results in reverse-it's `artifacts/` folders, under its rules.
- **Line endings: LF everywhere,** except `.bat` and `.cmd` files, which are
  CRLF. The `.gitattributes` files pin this and carry the same lines (the root
  one on both project branches, `shared/.gitattributes` and
  `charge-interposer/.gitattributes`); change them together. Component
  digests hash the bytes on disk, so line endings change them. Don't rely on
  `git status` to catch a CRLF file: when the CRLF bytes are what git last
  added, it shows clean. `git ls-files --eol` shows the endings on disk
  (`w/crlf`).

### Python

- **Use the Python command in `local-paths.md`,** never a bare `python` or
  `python3`.
- **No non-ASCII characters in anything Python prints or writes without an
  explicit encoding** (the Windows console raises on them). Use `->`, `~=`,
  `x`, `+/-`.
- **Inline or scratchpad.** Inline `-c` is for simple one-off lookups. Use
  a scratchpad script for anything multi-step, reused, or worth re-running.

### Editing files

Make file changes with the Edit and Write tools, whatever the auto mode
note suggests about `sed`, heredocs or short scripts. Read the file first:
both tools refuse a file this session hasn't read. If a one-off edit really
needs a script, write it in Claude Code's session scratchpad, not `scratch/`,
and never commit it.

### Searching

- **Prefer Bash `find` and `ls` over PowerShell** for file searches.
- **Scope every `find` to this worktree,** or to one named folder outside
  it with `-maxdepth 2`. Never from `/`, `~` or a user-profile root. If a
  path outside isn't in `local-paths.md`, ask the user rather than search.

### Plans, notes and memory

- **Plans live in reverse-it,** in the project's plans folder. Never in
  `~/.claude/plans/`.
- **The plan step gate.** After each step of a plan, stop and present the
  findings to the user. Only after explicit approval: write the findings to
  the notes, clear the step's worklog from `.claude/memory/`, update the
  plan to mark the step done, and start the next step. "Implement the plan"
  is not permission to run every step without stopping.
- **No unreviewed claims get committed.** Before an interpretation,
  hypothesis or assumption goes into a committed file, or into reverse-it's
  synced notes, present it to the user and get explicit approval.
  Hypotheses may go in, framed as hypotheses, once the user has seen them.
- **Verified facts are not claims for this rule.** A measurement, a
  command's output, a commit, a test result or an install, checked in the
  session, is written directly. A user ruling relayed by the session where it
  was made is written once checked against that session's record; only a
  mismatch goes back to the user.
- **Unreviewed findings go in a worklog in `.claude/memory/`,** gitignored
  and local to this worktree. Once a finding is confirmed or disproven and
  the step closes, move what was approved into the notes or the plan and
  delete the worklog entry.
- **Don't write to Claude Code's auto-memory** in `~/.claude/`, or to
  `~/.claude/plans/`.
- **`CLAUDE.md` holds only `@AGENTS.md`.** Instructions go in this file.
