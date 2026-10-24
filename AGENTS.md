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
| Generator inhibitor (`gen-inhibit`) | MeatPi WiCAN OBD, ESP32-C3 | ESP-IDF | `gen-inhibit/main` | the WiCAN tree, with its tooling in `tools/gen_inhibit/` and tests in `test/` |
| Charge interposer (`charge-interposer`) | Autosport Labs ESP32-CAN-X2, ESP32-S3 | PlatformIO / Arduino, porting to ESP-IDF | `charge-interposer/main` | `charge-interposer/` |
| Shared code | used by both | | `shared/main` | `shared/<component>/` |

`main` is upstream WiCAN. Nobody works on it.

## Local paths: `local-paths.md`

Anything that differs per machine is in `local-paths.md` at the root of this
worktree: the Python command, the ESP-IDF install, how to call PlatformIO,
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

**Skills follow the branch.** Each worktree has the role skills of the branch
it has checked out. Each branch keeps
only its own role's skill current; changes reach other branches by
cherry-pick. After a cherry-pick that touches your skill, re-read its
`SKILL.md`. On Linux every worktree shares the anchor's `settings.local.json`;
on Windows each worktree has its own.

**Never build or flash WiCAN firmware from an interposer worktree or
branch.** A WiCAN image's name and compiled-in commit come from the whole
tree, so interposer commits on a WiCAN branch, or an uncommitted interposer
file in a WiCAN worktree, would change or block WiCAN builds.

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
keyboard, with the `/self-compact` skill. It types `/compact` into this session's terminal and then, once the compaction is done, one resume message
naming the next step. It is the only sanctioned way for a session to type
into its own input.

- **When:** only when the user asks for it, or at the compaction point your
  role skill names. A session without a role skill has no self-compaction
  point.
- **Before it:** write everything worth keeping to its files. The summary
  loses detail; files do not.
- **After it:** the resume starts a fresh turn from a summary. Re-read your
  role skill and the docs the step needs before acting, per "After a
  compaction".
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
their path under `$REVERSE_IT_DIR`.

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
text change with it. The reviewer merges the tooling change and the
methodology commit's cherry-pick together, and does not push one without
the other.

**Plans, handoffs and working logs** belong to the role session that writes
them, named on an `**Owner:**` line at the top. A file without one gets an
owner from the reviewer. A file that holds validation the spec rests on is not
a working log for this rule; the reviewer rules on it (user, 2026-10-10).

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
  A pass verifies the commit it ran on and nothing else. When code is ported
  or rewritten, the old code's pass entries carry no weight for the new code:
  they are not copied across, and they are not cited as validation. Their
  tests can be pointed at the new code to make new entries.

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
- **Incomplete validation is allowed, but it must be visible.** A requirement
  without a basis is marked "basis pending", with what is missing. A
  requirement that rests on a user ruling cites the ruling and its date.

## Pushing

**The reviewer merges and pushes** (user, 2026-10-15 and 2026-10-23). The
implementor and the tester commit on their topic branches in their own
worktrees and never push. When work is ready, they tell the reviewer the
branch and the commit range. In its own worktree, the reviewer merges the
topic branch into `<project>/main`, runs the pre-push check, and pushes. It pushes the topic
branches too, after the same check, unless the work isn't worth keeping: a
disk failure loses everything that is neither pushed nor regenerable (user,
2026-10-23). All of a project's role sessions run on one machine, in
worktrees of the same anchor clone, so the reviewer sees every topic branch
without a push (user, 2026-10-23).

This rule is the user's standing approval for routine pushes, so the
reviewer doesn't ask per push. It does ask before a force push, a branch
delete, or any push the check stops.

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
are not home-network details (user, 2026-10-20).

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

**The bench is shared by the per-component testers.** It is the whole rig
-- both boards, the PC, the USB adapters and the CAN segments -- and one
hardware test runs at a time, so `$REVERSE_IT_DIR/firmware/bench-holder.md`
is one lock on all of it. Whichever tester has hardware work first takes
it, and releases it when it is not actively running. A tester can loan it
to its own component's implementor for bring-up. The rules for taking,
loaning and handing back are in that file. It stays out of git: it changes
at every hand-off, and it records home-bench details.

The lock's planned move to an append-only log, written only by
`bench_lock.py`, is specified in `shared/bench/SPEC.md` on `shared/main`.
Until that cutover, `bench-holder.md`'s rules apply as written. **The
cutover's merge carries the rewrite of this paragraph** and of
`bench-holder.md`'s rules (see "Docs change with the tooling").

### Build tooling

**Build tooling is specified.** Until the shared-code merge, each project's
spec holds its own build-identity and build-option rules (user,
2026-10-13). The shared-code and ESP-IDF port plan is
`$REVERSE_IT_DIR/projects/vtrux/notes/plans/esp-shared-code-port.md`. When
shared build tooling takes over a project's builds, this paragraph changes
in the same merge.

### Builds during wall-clock tests

**Wall-clock tests** are tests whose stimulus is sent by the wall clock on the
shared machine: the interposer's simulator scenarios and gen-inhibit's bench
runs. Emulated-time suites are not affected. On 2026-10-15, interposer
scenario #12 was voided when `charger_sim` stalled under load. The load was a
full-speed six-environment build together with the tester's own re-scoring.
The cause was never isolated (user, 2026-10-15, for all of this section):

1. **One rule for both projects** (user, 2026-10-16). While a wall-clock test
   of either project is running, every build on the shared machine, of either
   project, is throttled (point 3). A full-speed build waits until no
   wall-clock test is running. The tester's own heavy local work counts the
   same. Before a build, check both `$REVERSE_IT_DIR/firmware/bench-holder.md` and the process
   list for running simulators.
2. **The trials behind it** (2026-10-15/16). Interposer: a full-speed build
   failed (about 128 `charger_sim` STALLs a minute, worst 461 ms, a staleness
   trip). A throttled build passed (one STALL of 48 ms in 672 s) but took 3.3
   times as long (661 s against 200 s). gen-inhibit: a full-speed build passed
   (worst stimulus gap 35 ms, against 8.9 ms with no build). A new project, or
   a new build machine, runs the same trial before its tests allow builds: (a)
   a full-speed control, then (b) throttled, with pass marks set in its test
   plan.
3. **The throttled form on `NICKY-XPS`.** From the exported PowerShell, set the
   shell's priority class to Idle with `(Get-Process -Id $PID).PriorityClass =
   'Idle'`. Then build: for gen-inhibit `ninja -C build -j 1`, for the
   interposer `$env:PLATFORMIO_RUN_JOBS=1; py -3.14 -m platformio run`. The
   processes the build starts inherit the class. Idle lowers CPU priority
   only, not disk priority. It also does not reach Windows Defender, which
   scans fresh build output in its own process. If (b) still disturbs the
   test, the next suspect is Defender, and excluding the build folders from
   it is the user's call. **When a project's build command changes** (the
   interposer's ESP-IDF cutover will change its), this point changes in the
   same merge (see "Docs change with the tooling").
4. **Testers raise their simulators** to above-normal priority, never High or
   Realtime, which can starve the USB stack the CAN adapters need.
5. **The tester's own timing checks** still decide whether a run counts. A
   tester can mark a particularly expensive run "no builds" in
   `$REVERSE_IT_DIR/firmware/bench-holder.md`.
6. **HTTP round trips to the device are slowed by load too.** Under the build,
   gen-inhibit's arm POST took 39 ms instead of 18 ms. Testers time windows
   from when a request was sent, not from when it returned.

## Simulators, virtual CAN buses and background processes

**This section has a twin** in reverse-it, `$REVERSE_IT_DIR/simulators.md`,
which covers every simulator there, Coda's `flash_sim` included. The
methodology owner keeps them in step: a change to either goes through it and
is made to both at once. The deliberate differences: that copy covers
`flash_sim`, and this one describes the shared tooling's run guard.

These rules exist because on 2026-09-01 the Vtrux interposer bench flooded the
house LAN with **66,450 packets/s (13.6 MB/s, ~109 Mbit/s)** for roughly twenty
minutes and disrupted the user's network. Read them before running any
multi-process simulator.

### A virtual CAN bus is not automatically local

`python-can`'s `udp_multicast` transport defaults to **`hop_limit=1`**, and the
kernel routes `239.0.0.0/8` out the default interface. So every simulated CAN
frame goes on the real wire:

```
$ ip route get 239.0.2.1
multicast 239.0.2.1 dev eno1 src 192.0.2.10      <- the LAN NIC, not loopback
```

Multicast is flooded to every switch port unless IGMP snooping filters it, and
APs transmit it at the lowest basic rate, so wireless clients suffer worst even
when the source is wired.

**Always pass `hop_limit=0`.** TTL 0 means the datagram is looped back to local
sockets and never transmitted on any interface. Delivery between processes on
the same host is unaffected (verified: 2092 frames/s sent, 2092 received, eno1
unchanged from idle).

Do **not** also pin `IP_MULTICAST_IF` to loopback. `python-can` joins the group
with `INADDR_ANY`, which the kernel resolves to the default multicast interface,
so sending via a different interface means receivers see nothing. TTL 0 alone is
both necessary and sufficient.

The interposer's `charge-interposer/bus.py` and gen-inhibit's
`tools/gen_inhibit/gi_vbus.py` do this correctly and verify it at startup.

### Measure, do not assume

Before and after any sustained simulator run, check the interface counters:

```bash
t0=$(cat /sys/class/net/eno1/statistics/tx_packets); sleep 5
t1=$(cat /sys/class/net/eno1/statistics/tx_packets); echo $(( (t1-t0)/5 )) pps
```

Idle on `madhouse-debian` is single-digit pps. Anything in the thousands while a
sim runs means it is on the wire.

### Killing background processes

**The procedure differs by platform and the Linux one fails silently on
Windows.** Two traps on Linux first, then the Windows branch below -- read the
branch for your actual machine, not just the first one.

#### Linux (`madhouse-debian`) -- two traps

1. **`pkill -f <pattern>` matches your own shell.** The Bash tool runs commands
   through `bash -c '<your command>'`, so the wrapper's command line contains
   the pattern you are searching for and `pkill -9` kills the shell running your
   script. Symptoms: exit code 1 or 143 with no output, and later statements in
   the same command silently never running -- which also means file edits you
   believed were applied were not. Several edits were lost this way before it
   was diagnosed.

2. **Anchoring on the bare interpreter name misses subprocesses.** A runner that
   spawns children via `sys.executable` gives them a command line starting with
   `/usr/bin/python3.13`, so `pgrep -f '^python3\.13 ...'` never matches them.
   Six orphaned simulators survived every cleanup this way and kept transmitting
   long after they were believed dead.

Use this instead -- it matches by path fragment, filters to real interpreter
processes via `/proc/<pid>/comm`, and cannot match the bash wrapper:

```bash
for pid in $(pgrep -f 'charge-interposer|tools/gen_inhibit' || true); do
  case "$(cat /proc/$pid/comm 2>/dev/null)" in
    python*) kill -9 "$pid" 2>/dev/null || true ;;
  esac
done
```

Always verify afterwards (`pgrep -af <fragment>`, `ss -uanp | grep <port>`), and
remember `pgrep`/`pkill` return exit 1 when nothing matches, which aborts the
rest of the command under `set -e` -- append `|| true`.

#### The recipe above is Linux-only. On Windows it silently does nothing.

**Everything above assumes `/proc` and a Linux process table. On the Windows
machines (`NICKY-XPS`, `NICKYDESKTOP`) it matches nothing, kills nothing, and
reports success.** Discovered 2026-09-19, after it let two complete interposer
benches run at once on the same multicast groups, each re-forwarding the
other's traffic in a bridge loop -- 5,780 fps mean against 351 fps for a clean
run. The scenario "failed" on a trip that meant nothing, and the whole
measurement was thrown away.

Three separate failures, and the third is the dangerous one:

1. **`pgrep` under Git Bash does not see native Windows processes.** It sees
   only the MSYS process table. A Python simulator launched via `py -3.11` or
   PowerShell is invisible to it.
2. **`/proc/<pid>/comm` does not exist for native Windows processes**, so the
   `case` filter rejects even the few PIDs `pgrep` does return.
3. **The documented verification cannot fail.** `pgrep -af 'tools/interposer'`
   returns empty for the same reason the kill matched nothing, so you get a
   green check for a cleanup that never ran. Proof at the time: `pgrep -af
   'tools/interposer'` returned empty while a scenario was demonstrably
   mid-run and flooding the bus.

**On Windows, do this instead.**

*Stop the task, not the process.* If you started it with the Bash or PowerShell
tool's `run_in_background`, kill the background **task** -- the process tree
goes with it. That is the primary mechanism and it is more reliable than
hunting PIDs.

*If you must go by PID*, use `Get-CimInstance` and **exclude your own PID**:

```powershell
$me = $PID
$pat = '(charge-interposer|tools/gen_inhibit)'   # build the pattern in a variable
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match $pat } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

**Trap 1 has a PowerShell form and it is easy to miss.** A `CommandLine -like`
filter matches *the PowerShell process running the filter*, because the pattern
you are searching for is literally in your own command line. This kills your
own shell -- exit 255, and any later statements in that command never run.
Same failure as `pkill -f`, different shell. Two things follow: always exclude
`$PID`, and be aware that a process count taken this way **includes your own
invocation**, so a reading of "1 process alive" may be entirely phantom. Both
happened on 2026-09-19 within minutes of each other.

*Verify by observing effect, not absence.* A process-absence check is exactly
the check that cannot fail here. Confirm the thing actually stopped:

- watch the bus and see 0 fps on every segment (the real check);
- `Get-Process -Name ninja,cmake,python -ErrorAction SilentlyContinue` for
  native processes, which `pgrep` will not show you;
- `Get-NetUDPEndpoint` or `netstat -ano | findstr <port>` for a listener that
  outlived its parent.

A check whose failure mode is indistinguishable from success is not a check.
When cleaning up anything that transmits, prefer evidence that the traffic
stopped over evidence that a process is missing.

**This is the process-cleanup instance of a general failure.** The same shape
shows up in log analysis and in code scans — see
"Filters that fail silently" under Data Analysis Rules in
`$REVERSE_IT_DIR/AGENTS.md`.

### Run long simulations one at a time

Four concurrent scenario runs multiplied the frame rate by four. Run one, watch
it, then run the next.

### One simulator run per machine

Two benches on one machine see each other's frames even with `hop_limit=0`,
since both are on the same host (the 2026-09-19 bridge loop). So only one
simulator run is active per machine at a time (user, 2026-10-23).

The shared bench tooling will enforce this before it starts anything that
transmits:
- **It checks for a running bench:** UDP sockets on the bench ports, and
  Python processes running the simulator scripts. It refuses if it finds
  either, and names them.
- **It listens first.** It opens its own segments and listens before
  starting its simulators. It should hear nothing; any frame means another
  bench or an orphan is running, and it refuses.
- **To run two at once deliberately,** move one to other groups and ports
  with its port option. Both checks still run.

**Until the shared tooling has this guard, nothing enforces it** (user,
2026-10-23). Before starting a run, check for a running bench yourself, the
same two ways. If a check finds one, find out whose it is before you kill
anything: it may be another session's run. When the guard lands, this
section changes in the same merge.

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
- **Explorations that use reverse-it's data** (digging through CAN logs,
  captures, notes) put their scratchpads and results in reverse-it's
  `artifacts/` folders, under its rules (see "Reverse-it").
- **Line endings: LF everywhere** (user, 2026-10-23), except `.bat` and
  `.cmd` files, which are CRLF. The repo's `.gitattributes` files pin this
  and carry the same lines: the root one on `gen-inhibit/main`,
  `shared/.gitattributes` and `charge-interposer/.gitattributes`. Change
  them together. Component digests and embedded files hash the bytes on
  disk, so line endings change them. **Save files as LF:** a file an editor
  saves as CRLF still shows as clean in `git status`, because git compares
  after normalising, while the build and the digest see the CRLF bytes.

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
- **Unreviewed findings go in a worklog in `.claude/memory/`,** which is
  gitignored and local to this worktree, so that they live in a file and
  not only in context. It is a worklog for the current plan step and must
  not go stale: once a finding is confirmed or disproven and the step
  closes, move what was approved into the notes or the plan and delete the
  worklog entry.
- **Don't write to Claude Code's auto-memory** in `~/.claude/`, or to
  `~/.claude/plans/`.
- **`CLAUDE.md` holds only `@AGENTS.md`.** Instructions go in this file.

