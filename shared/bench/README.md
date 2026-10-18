# shared/bench -- the shared bench toolkit

Bench tooling shared by the two Vtrux ESP32 projects (gen-inhibit and
charge-interposer). Item 8, "run", of the shared bench toolkit in the
reverse-it plan `projects/vtrux/notes/plans/esp-shared-code-port.md`.
Stdlib only, Python >= 3.10. Written by the charge-interposer tester
(2026-10-17); each project writes its own suite on top.

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
| `tests/test_run_core.py` | The runner's own tests, with a fake suite and `fake_esptool.py`. `--can-fail` runs each with its rule switched off; every one must then fail. |

### Verdicts

`PASS`, `FAIL`, `KNOWN-FAIL` (a FAIL in the suite's registry, with its
reference), `VOID` (a premise was not met, or the board's image changed
under the group), `ERROR` (the check could not be judged: crashed, timed
out, or returned no verdict -- never a PASS), `NOT-RUN` (with the reason),
`INCONCLUSIVE`, `NOT-APPLICABLE`. A registry entry that passes is flagged as
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
