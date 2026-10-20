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
| `vtrux_bench/run/lock.py` | Reads the bench lock in reverse-it `firmware/bench-holder.md`. The runner never edits it (rule 2: a chained run holds the lock from start to finish; the tester takes it, messages, and releases it). |
| `vtrux_bench/run/procs.py` | Commands with timeouts that kill the whole tree; the remote (ssh) line with coreutils `timeout`; the build throttle (idle priority, one job) and the process-list scan for wall-clock tests. |
| `vtrux_bench/run/record.py` | The run folder (never reused), `progress.jsonl`, `record.json` and `index.txt` (exclusive create), and `compare` with per-figure tolerances. |
| `vtrux_bench/run/cli.py` | The command line. |
| `vtrux_bench/decode.py` | Diag DBC schema identity (toolkit item 10, first part): `layout_hash`, `check_identity`, `check_history`. See "Diag DBC schema identity" below. |
| `tests/test_decode.py` | Its tests (6), with `data/golden_layout.dbc` and its hand-written canonical text `data/golden_layout.txt`. `--can-fail` runs each test under each of its mutants (28); every one must fail. |
| `tests/test_run_core.py` | The runner's own tests (27), with a fake suite, a fake two-board bench and `fake_esptool.py`. `--can-fail` runs each with its rule switched off; every one must then fail. |

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

An identify hook that raises (could not look) stops the bench; the runner
never flashes a board whose state nobody read.

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
code hash once it is logged (port plan goal 13); until then the commit, and
only from a clean tree. That run must also be of the whole suite: a
`--group` or `--check` selection is refused, and so is any NOT-RUN result
that is not a declared skip (a check refused by a guard, a stopped run).

`--resume RUN_DIR` carries only from a run of the same suite on the same
code, as that run's start event records them; anything else is refused.

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
    bench_holder     path of reverse-it firmware/bench-holder.md
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
checks, `STOP` to end it (the board is still restored unless the file says
`norestore`; `now` also kills the check in progress).
