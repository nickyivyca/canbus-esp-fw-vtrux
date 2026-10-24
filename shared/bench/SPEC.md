# Bench lock: specification

Approved by the user 2026-10-21. Drafted by the charge-interposer reviewer.
Moved into git from reverse-it's `firmware/bench-lock-spec.md` (sha256
`8c2a54eb...c5d7`) on 2026-10-23 (user); only this header and the paths into
reverse-it changed. As a shared component's spec, it is changed by the
reviewer whose project needs the change, ruled on by the user in that
reviewer's session, and read by the other reviewer before merge (port plan
goal 20). Not in force until the cutover (section 7); until then the Holder
line in `$REVERSE_IT_DIR/firmware/bench-holder.md` is the lock.
Source: the methodology session's shape (user, 2026-10-21) and its additions
from the gen-inhibit reviewer's conditions (user, 2026-10-21).

## 1. What it is

One shared bench (the WiCAN, the X2 and the adapters on the bench host, today
NICKY-XPS) and one lock on it. The lock's state is the replay of an append-only log,
`$REVERSE_IT_DIR/firmware/bench-log.jsonl`. `shared/bench/vtrux_bench/bench_lock.py`
is the only code that writes it, through its command line or its API (the
runner uses the API). Nothing edits or deletes a line, by hand or by code; a
log that does not replay is replaced only by `repair` (section 3), which keeps
the original.
Board state, what is on each board, stays in
`$REVERSE_IT_DIR/firmware/bench-holder.md`.

## 2. The log

- One JSON object per line, UTF-8, `\n`-terminated.
- Every line has `ts` (the bench host's local time, ISO 8601 with its UTC offset,
  seconds), `who` (a role, e.g. `tester (charge-interposer)`), `event`, and
  optionally `run` and `note`. Each event below lists its other fields.
- An append is made under an OS file lock on the log, and the writer reads the
  log, checks the event against the replayed state and appends in that one
  locked step, so two writers cannot both take a free lock.
- A field not listed for its event (the common fields above, or the event's
  extra fields in section 3) makes the line illegal.
- A line that does not parse, or an event that the replay finds illegal,
  makes every command and the runner refuse (fail closed). The only way out
  is `repair` (section 3).

## 3. Events and when each is legal

| event | legal when | extra fields | effect |
|---|---|---|---|
| `init` | the log does not exist | `host`, `env` (section 4) | creates it, naming the bench host and environment; `who` is the role creating it |
| `take` | the lock is free | `pid` (required), `run` (optional) | held by `who`, for `run` if given |
| `release` | held by `who` | `boards` (required: per board, the image read off the wire and when, or `not read`) | free; board state known only if every board was read off the wire |
| `free_paused` | held by `who` with a `run`; written only by that run's runner, between cases (section 6) | `boards` (required, as for `release`) | free, with that run recorded as paused; board state known only if every board was read off the wire |
| `request` | `who` is not the holder and has no request queued | `note` (what for, how long) | queued; shown by `status` |
| `withdraw` | `who` has a request queued | -- | its request leaves the queue |
| `clear` | held, and the holder's `pid` is not running in the bench environment | `reason` (required) | free; board state unknown |
| `lost` | written only by a runner whose run lost the lock | `run` | records the stop; board state unknown |
| `repair` | the log does not replay, and no holder it can find is alive (below) | `damaged` (the old file's new name and sha256, each bad line's number and error), `dropped_runs`, `reason` (required) | see below |

- `take` after `free_paused` with the same `run` resumes that run. A `take` of a
  paused run's id by a different `who` is refused.
- `request` does not reserve the bench: `take` does not consult the queue.
  The queue is for the humans and sessions deciding who goes next.
- **`repair`** (user, 2026-10-21, proposed by methodology): run on the bench
  host and environment only, by the roles that may take, and refused if the
  log replays cleanly. It renames the damaged log aside, keeping it, as
  `bench-log.<ts>.damaged.jsonl`, and writes a new log: the old log's good
  lines in order up to the first bad one (truncated there, never skipped
  past, so a state built on an illegal step is never replayed), then the
  `repair` event. After it the lock is free, the board state unknown (as
  after `clear`), and any held or paused run is ended. A repair is messaged
  to both testers, like a clear.
- **If line 1 (the `init`) is the bad line**, there is nothing to keep:
  `repair` writes a fresh `init` for the bench host and environment it runs
  in, then the `repair` event. No hand edit or rename is ever needed.
- **`repair` has `clear`'s PID fail-safe.** It refuses while any holder it
  can find is alive in the bench environment: the holder the kept lines
  leave, and the `pid` of every `take` it can still parse among the dropped
  lines. Unsure counts as alive.
- **Run ids in the dropped lines stay used.** The `repair` event lists every
  run id it can parse from the dropped lines (`dropped_runs`), and the
  replay counts them as seen, so section 8's use-once rule still holds.

## 4. Bench host, environment and PID

- **The bench lives on one host, in one execution environment**: one PID
  namespace and one file-lock mechanism. `init` records both, as `host` and
  `env` (today `NICKY-XPS`, `windows`).
- Every command, and every runner using the API, refuses unless it runs on
  that host in that environment. The environment is detected explicitly,
  never inferred from the host name (inside WSL the host name is usually the
  Windows one). A PID or a file lock from another environment (WSL against
  Windows, either way) is never trusted.
- **Nothing else in the code or the rules names a host or an OS.** The PID
  check and the file lock sit behind one per-environment interface. Moving
  the bench (for example to madhouse-debian, native Linux) is a new log with
  its own `init` and that environment's implementation of the interface,
  not a restructure (user, 2026-10-21).
- **Implemented now: native Windows Python only** (user, 2026-10-21). WSL
  and any other environment are refused until implemented.
- `pid` is the process that holds the bench for the holder: the runner's PID
  for a run, otherwise the session's long-lived process given explicitly
  (`--pid`). The command's own PID is never accepted, since it exits at once.
- `clear` checks that `pid` is gone in the bench environment at the moment of
  the append.

## 5. Command line

`bench_lock.py init | status | take | release | request | withdraw | clear | repair`

`free_paused`, like `lost`, is the runner's and is written only through the API.

- `status` prints: holder, run, since, paused runs, the request queue, and
  `BOARD STATE UNKNOWN` until a `release` with every board read off the wire
  follows the last `clear`, `lost` or `repair`. Exit 0 always, unless the log fails to
  replay (exit 2).
- Every writing command prints the line it appended, and exits non-zero
  without appending if the event is not legal; the message says why and
  names the current holder.

## 6. The runner's check

- Before every bench case (not only at group start), the runner replays the
  log. It goes on only while the lock is held by its own role, its own run
  id **and** its own PID, not paused (user, 2026-10-22: a hand
  `take --run <id>` under a session's PID is not the runner's lock).
- **The runner pauses and resumes its own run** (user, 2026-10-22). A file
  named `PAUSE` in the run folder asks for a pause. At the next case boundary
  the runner does what it does at its end release, minus the restore: it
  identifies each board it has an image for and runs the suite's end hooks
  (the WiCAN release reboot). It writes `free_paused` with each board's read,
  that identify or hook read or `not read: <why>`, then waits, opening
  nothing. Nobody writes `free_paused` by hand.
- When the `PAUSE` file is removed, the runner takes the lock back with its
  own PID and run id. While another role holds the lock the take is refused
  and it keeps waiting. Others' takes and releases in between are expected
  and are not a loss.
- **After the resume take, before the next case** (user, 2026-10-22, from
  the gen-inhibit reviewer and the tester), the runner runs the suite's
  start hooks again, then identifies every board its remaining cases use
  against the image it expects, as at run start. It flashes and
  re-identifies any board that differs. The record notes the resume and the
  identities read at it.
- **A board it cannot put back is a stop, not a lost lock** (user,
  2026-10-22): the runner still holds the lock. It records every remaining
  case NOT-RUN, opens its record and `index.txt` with
  `STOPPED: <board> is <image read>, expected <image>, cannot flash`, and
  releases with the identities it has just read, so the bench is free and
  the wrong board is in the release. If the bench is still free when the
  role reads the stop, the role investigates that board before any other
  run and tries to explain why it could not be put back.
- **A case already in progress always runs to its end.** That is safe: only
  `clear` frees a held lock, and `clear` needs the runner's PID to be gone.
- Any other state -- free, held by another role or run, cleared, or a log
  that does not replay -- is a lost lock: the runner appends `lost` (except
  when the log does not replay, since then nothing can be appended: it writes
  `LOCK LOST` only into its own record and `index.txt`), records
  every remaining case NOT-RUN, skips the end restore and the end hooks'
  board actions, and stops. The record and `index.txt` open with
  `LOCK LOST: <state>`.
- **Held by its own role and run id under another PID** (a hand
  `take --run <id>` under a session's PID; user, 2026-10-22, the tester's
  Q6 (ii)) is a lost lock as above, except that the runner writes nothing to
  the log (section 8 refuses `lost` for the holder's run) and releases
  nothing: the lock stays with the session that took it.
- The run id the runner checks is its own, the one it took the lock with
  at start (section 8).
- **At its run's end the runner releases** (user, 2026-10-22), after its end
  restore and end hooks. Each board's read is the one it made (the X2
  identified after the restore, the WiCAN if an end hook read it), or
  `not read: <why>`.
- **With `--keep-lock PID`** it instead releases and takes again in one
  step, with nothing in between: the release carries the same board reads,
  and the take is a hand take for its own role, with no run, under `PID`,
  the long-lived process of the session doing the follow-on work. That
  session releases by hand when it is done.
- After a LOCK LOST the runner releases nothing. If the runner dies, the
  lock stays held under its PID until `clear`.
- **Board reads after a LOCK LOST are not prevented** (user, 2026-10-24):
  the runner may still identify boards after it has lost the lock, and
  nothing enforces otherwise. A role that sees such reads in its run's
  record tells the session that held the bench at that time, with what was
  read, how and when, so that session can re-run any test that looked off
  nominal around then. Seen first in the gen-inhibit tester's cutover
  check, run `b014` (2026-10-24): after LOCK LOST it identified the WiCAN
  by HTTP and the X2 off the wire.

## 7. Cutover

At cutover methodology rewrites the bench rules; the Holder and Since fields
of `$REVERSE_IT_DIR/firmware/bench-holder.md` stop being the lock. Cutover waits until gen-inhibit has
re-pinned the shared core to the commit carrying section 6, and the
charge-interposer runner fix (lock checked before every case, a pause ending
only on its own lock) goes in that same commit.

**Before cutover pausing is manual** (user, 2026-10-22): the `PAUSE` file
stops the runner at a case boundary, and the role edits the Holder line by
hand. A paused run waits until the Holder is its own again, whatever the
`PAUSE` file says. It is lost only when, between cases with no pause under
way, the Holder is not its own.

## 8. Details (settled with the tester's code, 2026-10-21)

- **Creating the log:** an explicit `init`, refused if the file exists. Every
  other command refuses a missing log, so a mistyped path never reads as free.
- **Run ids are used once.** A `take` may not name an id the log has seen,
  except to resume its own paused run. Ids match `[A-Za-z0-9._-]{1,80}`.
- **A role with a paused run** lets its runner resume it before taking under
  another id. A paused run whose runner has died is ended by
  `take --run <id>` then `release`, under the session's own PID.
- **Who takes for a run: the runner itself**, through the API, with its own
  PID and run id, at start. A take by hand (work that is not a run) gives the
  Claude Code session's process as `--pid`. `--pid` has no default.
- **PID checks fail safe:** unsure, or access denied, counts as alive, so PID
  reuse can only make `clear` refuse, never wrongly allow it.
- **Who may write:** the tester or implementor of gen-inhibit or
  charge-interposer. Reviewers and other sessions cannot take.
- **request:** one queued request per role; the holder cannot request.
- **lost:** only by the role that took that run, never for the current
  holder's run; it also ends that run's paused state.
- **Board reads on release and free_paused** (free_paused frees the bench too): one
  `--board NAME=TEXT` for each of `wican` and `x2`, both required; TEXT is the
  image read off the wire and when, or `not read: <why>`.
- **status refuses off the bench host and environment** like the writes, since a synced copy
  elsewhere may be stale.
- **clear ends the cleared holder's run**, which cannot then be resumed. A
  paused run with no holder is ended by `take` then `release`, not by clear.
- **Defaults:** `--log` is `$REVERSE_IT_DIR/firmware/bench-log.jsonl`. Exit 0
  done, 2 refused, the reason on stderr.
- **Lines:** compact JSON, ASCII only, flushed and fsynced. A last line with
  no newline is a torn write and fails closed.

## Ruled

- **The request queue is advisory** (user, 2026-10-21): `take` does not
  consult it.
- **Release and free_paused carry a fixed per-board read** (user, 2026-10-21),
  as section 8 sets out.
- **Every release reads both boards**, the X2 included on a gen-inhibit
  release (user, 2026-10-21, on the gen-inhibit reviewer's question): a
  release that does not read a board leaves BOARD STATE UNKNOWN. The
  gen-inhibit tester already reads the X2 off the wire at its releases.
- **A damaged log is repaired by `repair`** (user, 2026-10-21, methodology's
  proposal with the `repair` event after the kept lines, so `init` stays
  first); the request row matches section 8; `init` records the creating
  role; unlisted fields are illegal.
- **Repair follow-ups** (user, 2026-10-21, from methodology and the
  gen-inhibit reviewer): status counts `repair`; a damaged `init` is
  replaced by a fresh one written by `repair`; `repair` refuses while a
  holder it can find is alive; run ids in dropped lines stay used.
- **From the gen-inhibit reviewer's read** (user, 2026-10-21): pause (now
  `free_paused`) carries board reads; a log that does not replay gets no `lost` line; the bench is
  one host and one environment, structurally portable, Windows only for now;
  a case in progress runs to its end.
- **The runner wiring** (user, 2026-10-22, on the tester's questions): the
  runner releases at its run's end, or hands the lock to a session with
  `--keep-lock PID`; the runner writes `free_paused` and resumes itself,
  driven by the `PAUSE` file; the log event `pause` is renamed `free_paused`
  so it is not confused with the `PAUSE` file; before cutover pausing stays
  manual.
- **Pause and resume read the boards** (user, 2026-10-22, from the
  gen-inhibit reviewer and the tester): `free_paused` identifies the boards
  and runs the end hooks; the resume runs the start hooks and re-identifies
  every board the remaining cases use, flashing any that differs; the runner
  goes on only under its own PID.
- **A resume board that cannot be put back is a stop** (user, 2026-10-22,
  the tester's Q6 (i)): the runner releases with its reads instead of
  writing `lost`, which section 8 refuses for its own held run; if the bench
  is still free, the role investigates and tries to explain why. A session's
  take of the run under its own PID (Q6 (ii)) is a lost lock with nothing
  written to the log; the lock stays with the session.
