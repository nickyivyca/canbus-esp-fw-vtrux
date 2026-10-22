# shared/bench -- the shared bench toolkit

Bench tooling shared by the two Vtrux ESP32 projects (gen-inhibit and
charge-interposer). Item 8, "run", of the shared bench toolkit in the
reverse-it plan `projects/vtrux/notes/plans/esp-shared-code-port.md`.
Python >= 3.10. The runner is stdlib only; `vtrux_bench.decode` needs
cantools (`pip install ".[decode]"`; the golden test passes on 41.3.1
and 44.1.0). Written by the charge-interposer
tester (2026-10-17); each project writes its own suite on top.

Install it at a pinned commit rather than copying it:

    pip install "git+https://github.com/nickyivyca/canbus-esp-fw-vtrux@<sha>#subdirectory=shared/bench"

or run it from a checkout with `PYTHONPATH=shared/bench`.

## The chain runner (`vtrux_bench.run`)

One command runs a project's whole bench suite, chained: flash, identify,
every group of checks, restore, record. The same command runs part of it.

    python -m vtrux_bench.run plan    --suite <file.py>:<NAME> --settings <json>
    python -m vtrux_bench.run run     --suite ... --settings ... [--group G]...
                                      [--check GLOB]... [--resume RUN_DIR]
    python -m vtrux_bench.run compare <ref run dir> <new run dir>

| File | What |
|---|---|
| `vtrux_bench/run/model.py` | The suite schema: `Suite`, `Group`, `Check`, `Image`, `KnownFail`, `Result`, `Figure`, `Tol`, the eight verdicts, and `validate`. Start here to write a suite. |
| `vtrux_bench/run/engine.py` | Executes a suite. Lanes: "bench" (groups that need the bench, in order, then the restore) in parallel with one "logic:<host>" lane per host. Enforces the rules listed in its docstring. |
| `vtrux_bench/run/flash.py` | The built-in `esptool` flasher: md5 against expectations, the auto-reset check (`chip-id`) before (no flash if it fails), verified write, the check again after; no retry; a failed check forbids any further flash in the run. Ported from the charge-interposer tester's `flash_custody*.sh`. |
| `vtrux_bench/run/lock.py` | Reads the bench lock in reverse-it `firmware/bench-holder.md`, the lock before the cutover (settings with no `bench_lock_log`). The runner never edits it (rule 2: a chained run holds the lock from start to finish; the tester takes it, messages, and releases it). With `bench_lock_log` set the runner uses `bench_lock.py` instead (spec section 6). |
| `vtrux_bench/run/procs.py` | Commands with timeouts that kill the whole tree; the remote (ssh) line with coreutils `timeout`; the build throttle (idle priority, one job) and the process-list scan for wall-clock tests. |
| `vtrux_bench/run/record.py` | The run folder (never reused), `progress.jsonl`, `record.json` and `index.txt` (exclusive create), and `compare` with per-figure tolerances. |
| `vtrux_bench/run/cli.py` | The command line. |
| `vtrux_bench/decode.py` | Diag DBC schema identity (toolkit item 10, first part): `layout_hash`, `check_identity`, `check_history`. See "Diag DBC schema identity" below. |
| `vtrux_bench/bench_lock.py` | The bench lock as an append-only event log (reverse-it `firmware/bench-log.jsonl`), to reverse-it `firmware/bench-lock-spec.md`: the replay that fails closed, the command line (`init`, `status`, `take`, `release`, `request`, `withdraw`, `clear`, `repair`: a log that does not replay is replaced by its good lines up to the first bad one, then a `repair` event; the damaged log is kept aside) and the runner's API (`take`, `free_paused`, `release`, `release_and_take`, `lost`, `held_by_run`; `free_paused` and `lost` have no command). One bench host and environment, recorded by `init`; native Windows only. Not the lock until the cutover (spec section 7); the runner uses it only when its settings name it (`bench_lock_log`). |
| `tests/test_bench_lock.py` | Its tests (29), each naming the spec sections it checks. `--can-fail` runs each under each of its mutants (130), source edits that must each make it fail. Native Windows only. |
| `tests/test_decode.py` | Its tests (6), with `data/golden_layout.dbc` and its hand-written canonical text `data/golden_layout.txt`. `--can-fail` runs each test under each of its mutants (28); every one must fail. |
| `tests/test_run_core.py` | The runner's own tests (55, the bench-lock ones on a scratch `bench_lock` log), with a fake suite, a fake two-board bench, `fake_esptool.py` and `fake_ssh.py`. `--can-fail` runs each with its rule switched off; every one must then fail. |

### Boards and images

A bench may hold several boards. Each `Image` names its `board` (default
"board"); a group's `image` is one image name or a list, one per board.
The runner flashes a board only when it is not already on the image the
group needs, identifies every board the group needs before and after it
(the group is VOID if one changed under it), and at the end puts every
board it flashed back on that board's standing image
(`Suite.standing_images = {board: image}`, or `standing_image` for one
board). A board state that is not a flashed image, such as an X2 held in
ROM, can be an `Image` with its own flasher and expected identify value.

Board images (user, 2026-10-20). At the start of the bench lane, before the
first group, every board the run uses (test gear included) is identified
against the first image the run needs on it, flashed if it reads anything
else, and identified again: correctness rests on that, not on what the last
run left. At the end, one best-effort restore per board to its standing
image, also after a flash or identify failure stopped the bench; only a
failed auto-reset check or a chip-id mismatch (no further flash on that
board), a lost lock or STOP `norestore` prevent it, and there is never a
second attempt. Every esptool image must name its chip (`params["chip"] =
{"type": "ESP32-S3", "mac": "..."}`, both keys) or nothing is written; if
the auto-reset check reads another chip type or MAC, the port reached
another board, and nothing is written. The repo is public: the MAC comes
from a suite's local, unpublished settings at run time, never from a
literal in a file (the tests use a made-up, locally administered MAC). A board the run used that
ends off its standing image opens `board_state.txt`, `index.txt` and
`record.json` (`not_on_standing`) with `NOT ON STANDING IMAGE: <board> on
<image>, reason ...`; the tester copies that line into the release message
and the board's section of bench-holder.md.

An identify hook that raises (could not look) stops the bench; the runner
never flashes a board whose state nobody read.

Board hooks (user, 2026-10-20: a run killed without cleanup must not leave a
board armed). A suite may give `on_start` and `on_end`, each called as
`hook(board, image, ctx, logdir)` once per board the bench lane uses and
returning `{"ok": bool, "detail": str, ...}`; a hook that raises or returns
anything else has failed. `on_start` runs before the start pass identifies
the board (image: the first one the run needs there); a failure stops the
bench, so nothing is identified or flashed and every bench group is
NOT-RUN. `on_end` runs after the restore (straight after a lane crash, which
skips the restore) on every run however it ended
(image: the one the run believes the board is on, or None), but not once
the lock is no longer ours; a failure, or not running, opens
`board_state.txt`, `index.txt` and `record.json`
(`release_not_confirmed`) with `RELEASE NOT CONFIRMED: <board>: ...`. Both
results are in `record.json` (`board_hooks`) and `board_state.txt`. `on_end`
cannot run after a hard kill, so `on_start` is the guard that counts. A
suite without hooks behaves as before.

A group with `after=[...]` waits for those groups, in any lane, before its
first step -- e.g. bench groups that flash what a build group made -- and
with `after_must_pass` (the default) runs only if all their results are
PASS, KNOWN-FAIL or NOT-APPLICABLE. Cycles are refused.

On Windows, a local check whose `argv[0]` is a bare `bash` or `sh` is
refused: CreateProcess searches System32 before PATH and runs WSL's
`bash.exe`, not Git Bash, although `shutil.which` finds Git Bash. Give the
full path, e.g. a `{bash}` setting.

The build throttle counts another role's bench lock as a wall-clock test
running; under the suite's own lock, this run's own wall-clock checks and
the process scan decide.

A "wallclock" check starts only when no other wall-clock test runs on its
host: another role's bench lock, a process matching `wallclock_patterns`
that is not this runner or something it started (on a remote host: not
carrying this run's id), or another of this run's own wall-clock checks.
Otherwise it is NOT-RUN with the reason (after waiting up to
`wallclock_wait_s`). One long simulation at a time, enforced by the runner.

`Suite.requires = "<suite>"` (the bench suite requires the emulated one):
the run refuses to start unless the newest finished run of that suite is
on the same code and matched its reference (`settings.references`), or,
with no reference yet, has no FAIL, ERROR or VOID. "The same code" is the
product digest (user, 2026-10-20) when the suite gives
`Suite.product_files(root)`, the repo-relative paths of its product files
(each project gives its own split; code_info must give the repo root as
`repo`): sha256 over those files sorted by path, each as path, NUL, bytes,
NUL. A harness-only commit keeps it. The harness identity (commit, dirty
state, the suite's artifacts digest, the sha256 of each ship list) is
recorded in record.json and index.txt and not gated on. A record with no
product digest (older runs) is given the digest of its commit's tree from
git (bridge), only if that tree was clean. A suite with no product files
keeps the old identity: the code hash once it is logged, else the commit of
a clean tree. `--resume` compares the same identity. That run must also be of the whole suite: a
`--group` or `--check` selection is refused, and so is any NOT-RUN result
that is not a declared skip (a check refused by a guard, a stopped run).

A run with a `--group` or `--check` selection is a subset run (user,
2026-10-20): `record.json` and the start event carry `subset: true` and
`subset_cases` (the `group/check` ids it selected), and `index.txt` says
SUBSET RUN with those ids; a whole run carries `subset: false`. A subset run
is evidence for the change it checks and never satisfies a whole-run
requirement: the requirement refuses a run marked subset, or one whose
selection is not empty, whichever of the two says so. Whether a change
needs a whole run or a subset is the project reviewer's call. Subsets are
chosen at run time; suites define none.

`--resume RUN_DIR` carries only from a run of the same suite on the same
code, as that run's start event records them; anything else is refused.
It carries only what finished (user, 2026-10-20). A check needs its
`check-end` event in `progress.jsonl`, written once all its results are,
so a check killed between two results runs again. A check of a group that
uses a board also needs the group's `group-end` event, written after its
identify-after and amends, so no PASS its identify-after never saw is
carried. A board-less group killed mid-way keeps its finished checks. A
run from before these events carries nothing.

`record.json` lists every image the run used (`images_used`): board,
expected identity, what identify read, file digests, whether it was
flashed, and a `code_hash` field (null until the code-hash tool exists).

A check with `skip="<reason>"` (needs hands, needs the truck, blocked) is
listed NOT-RUN with its reason and never executed, so the index still
shows it.

### Diag DBC schema identity (`vtrux_bench.decode`)

User ruling, 2026-10-19, common to both projects. Each diag DBC states
`DiagSchemaVersion` (INT, the schema byte its frames carry) and
`DiagSchemaHash` (STRING, its own `layout_hash`) as database attributes;
a renamed signal carries `RenamedFrom` (its earlier names, oldest first).

`layout_hash(db)` is the first 16 hex characters of SHA-256 over a
canonical text of the parsed database: per message its id, name, DLC and
extended flag; per signal its name, start bit as written, length, byte
order, u/s/f, scale and offset as `repr(float(x))`, and multiplexing; the
choices as value=name. Messages are sorted by id, signals by start bit then
name, choices by value. Comments, units, ranges, nodes and attributes are
left out. Names are in it, so a rename needs a schema bump. A choice name
holding `|`, `=` or a line break is refused. The exact line format is in
the module docstring and pinned by the golden test.

`check_identity(db)` checks that a DBC states an integer version and its
own hash. `check_history(repo, path)` checks every commit of the DBC in
git: each commit's identity, and one layout per `DiagSchemaVersion`. A
shallow clone is refused.

### Verdicts

`PASS`, `FAIL`, `KNOWN-FAIL` (a FAIL in the suite's registry, with its
reference), `VOID` (a premise was not met, or the board's image changed
under the group), `ERROR` (the check could not be judged: crashed, timed
out, or returned no verdict -- never a PASS), `NOT-RUN` (with the reason),
`INCONCLUSIVE`, `NOT-APPLICABLE`. `--resume` carries PASS, FAIL,
KNOWN-FAIL, INCONCLUSIVE and NOT-APPLICABLE results and runs the rest
again (VOID, ERROR, NOT-RUN). A registry entry that passes is flagged as
a change; a registry entry no selected check produced is listed (renamed?).

### Settings (one JSON per machine, not in git)

    runs_root        where run folders are created (SeaDrive, results stay there)
    bench_holder     path of reverse-it firmware/bench-holder.md (the lock
                     before the cutover)
    bench_lock_log   path of reverse-it firmware/bench-log.jsonl: when set,
                     the runner takes, pauses, resumes and releases through
                     bench_lock (spec section 6) instead of reading the
                     Holder line; set at the cutover (section 7)
    hosts            {"local": {"python": [...], "env": {...}},
                      "<name>": {"ssh": "<alias>", "python": [...],
                                 "workroot": "/tmp/<project>-tester/runner",
                                 "env": {...}}}
    esptool          {"argv": [...esptool.py], "port": "COM3", "baud": 921600}
    wallclock_patterns  regexes over process command lines that mark a
                     wall-clock test running (either project's simulators)
    any other string  available to checks as {name}

### The record

Each run gets `<runs_root>/<suite>_<YYYYmmddTHHMMSS>_<hex4>/` with
`progress.jsonl` (as it happens), `record.json` and `index.txt` (at the
end), `board_state.txt` (bench runs: the board's state read at the end, for
the release message), one folder per check (its `check.log` and outputs),
and `ship_<host>_<name>.txt` (sha256 of every file shipped to a remote
host). `record.json` carries each group's wall clock and whether it needed
the bench, the code info from the suite, and a `code_hash` slot (null,
pending a user ruling). Write `PAUSE` in the run folder to hold it between
checks: before the cutover it then waits until the file is gone and the
Holder line is its role's again (the role edits the Holder by hand); with
`bench_lock_log` it frees the bench itself (`free_paused` with its board
reads) and takes it back once the file is gone (spec section 6). `run
--keep-lock PID` (log only) ends the run by handing the lock to the session
whose long-lived process is PID. If PID dies during the run, that take is
refused, and so is the release with it: the run ends LOCK NOT RELEASED and
the lock stays under the exited runner's PID until someone runs `clear`
(it fails closed). Before the cutover the Holder line is read before every
case, so a momentary read failure (SeaDrive mid-sync) ends the run as LOCK
LOST at any case boundary; that also fails closed (spec section 7), and the
run is re-run. A STOP during a pause still takes the lock back, for the
restore and the release, but runs no start hooks and flashes nothing.
`STOP` ends the run (the board is still restored unless the file says
`norestore`; `now` also kills the check in progress -- on a remote host
too: the remote line records `timeout`'s pid in the check's outdir, and
the runner kills that process group once the local ssh is gone).
