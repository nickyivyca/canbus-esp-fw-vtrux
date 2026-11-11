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
every group of checks, the end pass, record. The same command runs part of
it.

    python -m vtrux_bench.run plan    --suite <file.py>:<NAME> --settings <json>
    python -m vtrux_bench.run run     --suite ... --settings ... [--group G]...
                                      [--check GLOB]... [--resume RUN_DIR]
    python -m vtrux_bench.run compare <ref run dir> <new run dir>

| File | What |
|---|---|
| `vtrux_bench/run/model.py` | The suite schema: `Suite`, `Group`, `Check`, `Image`, `KnownFail`, `Result`, `Figure`, `Tol`, the eight verdicts, and `validate`. Start here to write a suite. |
| `vtrux_bench/run/engine.py` | Executes a suite. Lanes: "bench" (groups that need the bench, in order, then the end pass) in parallel with one "logic:<host>" lane per host. Enforces the rules listed in its docstring. |
| `vtrux_bench/run/flash.py` | The built-in `esptool` flasher: md5 against expectations, the auto-reset check (`chip-id`) before (no flash if it fails), verified write, the check again after; no retry; a failed check forbids any further flash in the run. Ported from the charge-interposer tester's `flash_custody*.sh`. |
| `vtrux_bench/run/lock.py` | Reads the bench lock in reverse-it `firmware/bench-holder.md`, the lock before the cutover (settings with no `bench_lock_log`). The runner never edits it (rule 2: a chained run holds the lock from start to finish; the tester takes it, messages, and releases it). With `bench_lock_log` set the runner uses `bench_lock.py` instead (spec section 6). |
| `vtrux_bench/run/procs.py` | Commands with timeouts that kill the whole tree; the remote (ssh) line with coreutils `timeout`; the build throttle (idle priority, one job) and the process-list scan for wall-clock tests. |
| `vtrux_bench/run/record.py` | The run folder (never reused), `progress.jsonl`, `record.json` and `index.txt` (exclusive create), and `compare` with per-figure tolerances. |
| `vtrux_bench/run/cli.py` | The command line. |
| `vtrux_bench/decode.py` | Diag DBC schema identity (toolkit item 10, first part): `layout_hash`, `check_identity`, `check_history`. See "Diag DBC schema identity" below. |
| `SPEC.md` | The bench lock's spec, moved from reverse-it's `firmware/bench-lock-spec.md` (2026-10-23). |
| `DEVICES-SPEC.md` | The bench devices' spec (user, 2026-11-11, port plan step 3; named `RIG-SPEC.md` until the user's rename the same day): serial devices found by USB device ID (S1-S3), and the bench supply behind a protection service that owns it, with two tiers of limits, every write read back, the output watched, faults only the user clears, writes only from the bench's holder, and its bring-up acceptance (K1-K10). Not implemented yet. |
| `ACCEPT-SPEC.md` | The acceptance runner's spec, A1 to A16 (user, 2026-11-01): `vtrux_bench.accept` runs a step's acceptance from the reviewer's committed list (`<project>/acceptance/<step>.toml`) as a chain suite, with its own trees and builds, and closes one record. Being built in slices, in `vtrux_bench/accept/` (rows below); the charge-interposer's adapter comes first, for its port step 2a, in `charge-interposer/accept_adapter.py` beside its suite (A15). The reviewers' (A16). |
| `FLASH-SPEC.md` | The OTA flasher's spec, O1 to O13 (user, 2026-11-11): a second built-in flasher, `ota`, that loads an app image into a WiCAN over its hotspot. gen-inhibit's `bench_flash` and its suite's `upload()`/`ota()` at gen-inhibit/main `40ecfa5e` are the reference, and only the marked changes differ: the image's identity and a pinned image's pin (O3), one Wi-Fi module for Windows (`netsh`) and Linux (`nmcli`) (O4a), and disarming under a held bench (O7). Not built yet; the charge-interposer tester writes it. The reviewers'. |
| `vtrux_bench/accept/listfile.py` | The acceptance list (ACCEPT-SPEC A2, A3, A4's exact commits, A8): named `<commit>:<path>` and read with `git show` there, never from a working tree; the result records the commit in full and the list's sha256. Refuses a key it does not know at every level (so no patch or script text), a missing required key, a command that is not a committed file in its tree (a `shared/<component>/` path is looked for at the tree's commit of that component: the list's override, else the project's pin), a tree not at an exact commit (a commit's hex; a tag too for a shared component; never a branch), a name the list does not define, and a target, option, shell, judge, comparer or suite that is not the adapter's. A `[[set]]` takes exactly the catalogue entries its selectors pick (`tag:<t>`, `id:<glob>`, `all`); one that picks nothing is refused. The schema is in its docstring. Python >= 3.11 (`tomllib`). The testers' (A16). |
| `vtrux_bench/accept/record.py` | The acceptance record's start (A13): `start(runs, step)` makes `<runs>/accept_<step>_<YYYYmmddTHHMMSS>_<hex4>/` with mkdir (never reused) before anything else the runner does, names it as the first console line, and writes every line the runner says to its `console.txt`. A runs folder inside a git worktree is refused (run records are never published). Also the Python guard: `vtrux_bench.accept` refuses to import on Python < 3.11. **The runner runs on NICKY-XPS (3.14) or madhouse (3.13); WSL (3.10) is a check host, which runs the commands a check names, never a runner host** (reviewer, 2026-11-01). |
| `tests/test_accept_record.py` | Its tests (5), with a control and one mutant per rule in `record.RULES` (6) under `--can-fail`. |
| `vtrux_bench/accept/trees.py` | The acceptance runner's trees (A4): each in a folder of its own from `scratch.py`, held in the run's `Made` (A14). The project is a `git clone --shared --no-checkout` of the repo (no object copied, never a working copy), checked out detached at the tree's commit; each shared component is another such clone at the list's override or the project's pin, placed where the adapter's `tree_paths` says. A checkout is refused unless HEAD is exactly its commit and `git status` shows no change, untracked files included. The tree's record: per checkout the commit, the tree id and the sha256 of every tracked file, and the product digest over the adapter's `product_files`. A tree on a remote host (one the settings name with its `ssh` and `workroot`) is made locally first, for its record, then each checkout goes there as a git bundle of its commit, is cloned into `<workroot>/accept-<record id>/tree-<name>/`, checked out detached at the commit and checked there the same way; its checks run on that host from the remote checkout, with posix paths and the adapter's shell for that host (`shell_argv(shell, settings, host=...)`). The record's own folder on that host, `<workroot>/accept-<record id>/` with every remote tree of the run in it, and the chain runner's own work folder there, are held in the run's `Made` and removed over ssh, leaving the workroot as it was (A14). Builds, mutants from committed text and suite runs on a remote tree are refused: not built yet. |
| `tests/test_accept_remote.py` | Its remote-tree tests (8), the host being this machine through `tests/loop_ssh.py` (each ssh line run with bash here, Git Bash on Windows), so the bundle, clone, check, run and removal really happen, and the workroot is left empty; `test_remote_once`: a remote folder added twice is removed once, after the folders added after it; `test_remove_guard` gives `remove_remote` each path it must refuse (relative, `~`, `..`, not normal, the workroot itself, outside it, an empty or `/` workroot, a folder the runner does not make) through an ssh that only marks that it ran. One mutant per remote rule of `trees`, `compile`, `builds`, `canfail` and `scratch` (16) under `--can-fail`. |
| `tests/loop_ssh.py` | Stands in for ssh in those tests: runs the line with bash on this machine, standard input passed through. |
| `tests/test_accept_trees.py` | Its tests (6), with a control and one mutant per rule in `trees.RULES` (11; the remote and setup rules' are in `test_accept_remote.py` and `test_accept_setup.py`) under `--can-fail`. |
| (in `trees.py`) `setup_tree` | The adapter's `tree_setup(tree)`, run in every tree the runner makes (check trees, build trees, mutant trees; on its host for a remote tree): the commands that make a fresh clone usable, for the interposer the installs of its pinned canre and can-tools into the tree's own `scratch/` (the charge-interposer reviewer, 2026-11-02, with five conditions): each command a committed file in that tree, named by the adapter, nothing from the list or the operator; run after the tree check, and the tree checked again after it, so setup changed nothing git tracks; the tree's record holds each command, its exit code, its console and what it installed (the adapter's `tree_installed`); a failure stops the run before anything is judged (exit 2, the record left open); the installs go with the tree. |
| `tests/test_accept_setup.py` | Its tests (4), one per condition but the last, which `test_accept_run.py`'s `test_setup_failure_stops_run` shows end to end; one mutant per setup rule of `trees` (5) under `--can-fail`. A setup on a remote tree is in `test_accept_remote.py`. |
| `vtrux_bench/accept/builds.py` | The builds (A5): each `[[build]]` in a clean tree of its own (`trees.make_tree`, never a check's), through the adapter's target (the committed file it runs, its shell, its arguments, what it made). All of them run as one chain run of "build" checks, so the chain runner's build throttle applies; its record is in the acceptance record's `chain/`. Each build's record, `builds/<id>.json`: its tree's record, its verdict and, if it passed, its images with their sha256, the ELF's sha256, the variant and the toolchain. |
| `vtrux_bench/accept/settings.py` | The run's settings (A6): the machine's settings file with the build-dependent keys filled by the adapter's `settings_keys(builds, trees)`, written to a scratch folder of the run's own (removed with it), never into the record; a masked copy (MACs and the adapter's `redact_text`) goes in the record as `settings.masked.json`. A machine settings file with a key naming a password, secret, token or PSK is refused, naming the key, never its value. Each suite run gets a settings file of its own, these with the adapter's `tree_settings(tree)` over them, so the suite is told the tree it runs in (the interposer's `interposer_code`); its masked copy is `settings.<suite run id>.masked.json`. **A suite run's images** (A9: the flash and the identify after it are the chain runner's, under its own lock): a `[[suite_run]]`'s `images_from` lists builds of the list, and `suite_images()` hands their records, as this runner wrote them (a copy), to the adapter's `suite_settings(run, records)`, whose settings go over that suite run's; the adapter takes the sha256s from the records, never from the folders (the charge-interposer reviewer, 2026-11-04). A build there that made no images leaves that suite run not started, saying which, and the rest of the run goes on; two builds of one variant, an adapter with no `suite_settings`, or one that refuses them, refuse the run before anything is flashed. |
| `vtrux_bench/accept/compile.py` | The checks compiled into one chain suite (A7): one group per check in the list's order, `after` kept as order only, the command the adapter's shell then the file in its tree (a `shared/<component>/` file in that tree's checkout of it), run from the tree's project checkout. A check starts from `machine_env()`, the operating system's variables and the adapter's `MACHINE_VARS`, never the operator's shell (`Check.base_env`), with the list's or catalogue's env over it and the adapter's `tree_env(tree)` over that, naming the check's own tree to the code run in it (the interposer's `INTERPOSER_CODE`); suite runs and mutant runs (their own mutant tree, never the target's) get theirs the same way. The chain record is in the acceptance record's `chain/`. Every chain record the acceptance run writes (the builds' too) holds the masked settings, through the chain runner's `record_settings` (A6; reviewer, 2026-11-01). |
| `tests/test_accept_compile.py` | Their tests (11), on a fake build and fake checks run through the real chain runner, with a control and one mutant per rule of the three modules (28; `settings.tree_settings`'s is in `test_accept_suite_runs.py`) under `--can-fail`. |
| `vtrux_bench/accept/nested.py` | A `[[suite_run]]` (A7): `python -m vtrux_bench.accept.nested`, run by one group of the compiled suite, runs the project's suite in its tree as a nested chain run. The run settings' `runs_root` is the acceptance record's `runs/`, so its record (`<record id>-<suite run id>`) lands there, holding the masked settings. With `rerun_flagged`, the flagged cases run again as a new record, `...-rerun`, selecting only them. **The first, whole run decides** (A12 as written; the charge-interposer reviewer, 2026-11-01): the item exits with the first run's code; each flagged case's first and re-run verdicts are listed side by side in `nested.json` and on the console. |
| `vtrux_bench/accept/judge.py` | Comparisons (A10), premises (A11) and the verdict (A12). The core's comparers are `run-records` (each case's verdict, cases in one side only, each figure) and `transcript` (two logs line by line after masks: `re:<regex>` or the adapter's named `MASKS`); an adapter's comparer is called the same way. What must hold: `identical`, `same-verdicts`, `within` (figures within the list's tolerance by name or `*`) or `listed` (exactly the listed differences, one by one). Premises: the tree's product digest equal to a value, or the same as at a commit (from git objects); `board_read` is not built yet and does not hold. A failed premise makes its items (all, if it names none) NOT EXERCISED. ACCEPTANCE PASS only if every premise held and every item (check, build, suite run, comparison, can-fail) is as expected; otherwise each one that is not is listed. A check's verdict (`check_verdict`) is that of all its results, whatever their order (the charge-interposer reviewer, 2026-11-02): PASS when each is PASS, KNOWN-FAIL or NOT-APPLICABLE and no known FAIL passed; otherwise the first that any result gives of ERROR, VOID, NOT-RUN, INCONCLUSIVE, a known FAIL that passed, FAIL. So FAIL only when every result that is not a pass is a FAIL; the item names the results that are not a pass, up to six. `not_passed` lists all of them, in order, KNOWN-FAIL, NOT-APPLICABLE and a known FAIL that passed included, for a chain group or for every group of a record (a nested suite run's): the record holds the full list, checked against the known fails by hand in the VERIFICATION.md entry and the review, since the runner has no known-fail machinery (the user, 2026-11-02, through the charge-interposer reviewer). |
| `tests/test_accept_judge.py` | Its tests (11), on made-up records and logs and a small git repo, with a control and one mutant per rule in `judge.RULES` (20) under `--can-fail`. |
| `vtrux_bench/accept/close.py` | The record's close (A13), once, with the cleanup first (A14): the run's `Made` is removed and every LEFTOVER goes into the README; the adapter's `identifier_scan` runs over the folder and every hit is recorded, never refusing the close (an adapter with none is recorded as such, and a scan that fails is recorded as FAILED with its error); `README.md` (step, list commit and sha256, runner commit, trees, builds, items, every result of each check and suite run that is not a plain PASS, comparisons (the differences that count, then every other one the comparer saw, "seen, not counted": the charge-interposer reviewer, 2026-11-04), premises, flagged cases' first and re-run verdicts side by side, identifier hits, leftovers, verdict) and `verification_skeleton.md` (the facts for the entry the tester writes); then the console stops and `SHA256SUMS` covers every other file. Only then the artifacts README row, through the adapter; a failure there or after is printed and returned, never written into the closed record. A second close is refused. |
| `vtrux_bench/accept/run.py` | One step's acceptance end to end (A1): `python -m vtrux_bench.accept run --repo R --list <commit>:<path> --adapter <repo-relative path> --settings <machine settings> [--earlier <record>]`. The record folder is made first, in the machine settings' `runs_root`; the adapter is loaded from a clean clone at the list's commit (never a working copy), **so a list and its adapter always move together**: the adapter, its catalogue, comparers and build recipes are the ones committed with the list, whatever commits the trees are at. Then the list, trees, premises (a failed one's items, builds included, are not run and are NOT EXERCISED), builds, settings, can-fail mutants, one chain run, every item judged, the cleanup and the close. Each check and suite run item carries every one of its results that is not a plain PASS, for the README's full list; a suite run's are the outer chain's for it and then, as `nested/<id>`, those of its nested record's first, whole run, where the suite's known fails sit (the charge-interposer reviewer, 2026-11-02), with a line saying so when there is no nested record or it cannot be read. Every folder is in one `Made`, removed before the close and by a finally and the exit handler otherwise. A run that stops before its close says why in its console and is left open, never closed with a guess. **The runner's own tree must be clean at a commit**, ideally a checkout of a tag (`../shared-<tag>`): one with uncommitted changes (untracked files included), or in no git checkout, is refused before any record is made (A10, AGENTS.md: evidence is committed harness run from a named commit; gen-inhibit's reviewer, 2026-11-01). `--dev` runs anyway, for work on the runner itself: the record's verdict reads "DEV RUN, not evidence", never ACCEPTANCE PASS, and so does its skeleton. Exit 0 ACCEPTANCE PASS, 1 ACCEPTANCE FAIL, 2 refused or stopped, 3 a DEV RUN. A suite run whose `images_from` builds made no images is not started (NOT-RUN, saying why), and the rest runs. Not built yet: hand items and `board_read` premises (A9, A11; the charge-interposer reviewer, 2026-11-04: built when a list needs one), A8's advice, and on remote trees builds, mutants from committed text and suite runs. |
| `tests/test_accept_run.py` | Whole acceptances, and the runner's own identity (14 tests), on a small repo holding a project with the fake adapter committed in it: a pass; the adapter taken from the commit, not a working copy; a case that fails and passes on its re-run never giving ACCEPTANCE PASS (gen-inhibit's reviewer, 2026-11-01); a failed premise, whose blocked build is not run (the charge-interposer reviewer, 2026-11-01); a refusal after the record is named; a runner tree with changes refused, and with `--dev` a DEV RUN that never reads ACCEPTANCE PASS; `runner_identity()` on a real repo (a modified or untracked file is a change); a check whose judge gives a FAIL and then an ERROR, with expect FAIL, not as expected, and its README list of results that are not a pass; a suite run's list holding its nested record's FAIL and KNOWN-FAIL (the charge-interposer reviewer, 2026-11-02); a suite run given its `images_from` build's ELF sha256 from the build record, and one whose build made no images not started while the rest runs. The runner's identity is set by the tests, since they run from the tree under development, except in `test_runner_stays_clean` (gen-inhibit's reviewer, 2026-11-01): a clean clone of the runner at its HEAD runs a whole acceptance in a process of its own without `-B` and must still report no changes (its `__pycache__/` is written and ignored). A control and one mutant per rule in `run.RULES` (13), plus one that leaves a stray file in the runner's tree, under `--can-fail`. |
| `tests/test_accept_close.py` | Its tests (11), with a control and one mutant per rule in `close.RULES` (17) under `--can-fail`. |
| `tests/test_accept_suite_runs.py` | Its tests (3), on a project suite with a case that fails once and then passes, with a control and one mutant per rule in `nested.RULES` (4) and one for `settings.tree_settings` under `--can-fail`; the rules reach the nested subprocess through a test-only wrapper. |
| `tests/test_accept_list.py` | Its tests (13), each naming the A-numbers it checks, on small git repos it builds with `tests/fake_adapter.py`. `--can-fail` runs them unchanged first (the control, which must pass), then once per rule in `listfile.RULES` with that rule switched off (17); every one must make its test fail. |
| `vtrux_bench/accept/canfail.py` | Can-fails (A10). A `[[canfail]]` names its target check and either committed mutant text (`from`, a TOML file of `[[mutant]]` id, file, old, new, in the target's tree) or the harness's own can-fail mode (`mode`, an argument the target's command then runs with, which must pass). Each mutant is applied to a fresh clone of the target's tree, never the target's own; old must match exactly once and the id must be in the file, or the run is refused before anything runs. Each mutant run is one group of the compiled suite, after the target. What must change: `fails` (every mutant leaves the target not PASS), `verdict-flips` (the control, or the target unmutated, PASSes and every mutant FAILs), `new-fail-line` (every mutant's log has a FAIL line the control's has not), `mode-passes`. |
| `tests/test_accept_canfail.py` | Its tests (6), each through the real chain runner, with a control and one mutant per rule in `canfail.RULES` (8) under `--can-fail`. |
| `tests/fake_adapter.py` | A made-up project adapter (A15) for the acceptance runner's own tests, so the core is proven without any project's code (reviewer, 2026-11-01). |
| `vtrux_bench/bench_lock.py` | The bench lock as an append-only event log (reverse-it `firmware/bench-log.jsonl`), to `SPEC.md`: the replay that fails closed, the command line (`init`, `status`, `take`, `release`, `request`, `withdraw`, `clear`, `repair`: a log that does not replay is replaced by its good lines up to the first bad one, then a `repair` event; the damaged log is kept aside; `retire`: at a move to a new bench host, the `retire` event is appended and the log renamed aside, whole, as `bench-log.<old host>.<date>.jsonl`, after which every write to it refuses; a rename that failed is finished by `retire` again, appending nothing) and the runner's API (`take`, `free_paused`, `release`, `release_and_take`, `lost`, `held_by_run`; `free_paused` and `lost` have no command). Who may write: the testers and implementors of both projects, and the hand-take roles `android` and `can-tools`, which take by hand only, with no run, and release with a note (what they leave and what they transmitted). One bench host and environment, recorded by `init`: native Windows (kernel32 PIDs, msvcrt's byte lock) or native Linux in the host's PID namespace (`kill(pid, 0)`, an open-file-description `fcntl` lock, so two opens in one process exclude each other as two handles do on Windows); WSL, a nested PID namespace and anything else are refused. A rename never replaces a file, on Linux too. The runner uses it when its settings name it (`bench_lock_log`). |
| `tests/test_bench_lock.py` | Its tests (36), each naming the spec sections it checks. `--can-fail` runs each under each of its mutants (166), source edits that must each make it fail. Runs on native Windows and native Linux; the tests and mutants of the other environment's PID check, file lock and rename print `NOT RUN (other env)` (1 test and 9 mutants on Windows, 4 mutants on Linux), so the verdict needs a run on each. |
| `vtrux_bench/run/canmain.py` | The run's canmain check (`SPEC.md` section 8): a run that needs the bench refuses to start, before its take, unless `canmain` (CAN1's Innomaker) is shown down by the probe its settings name (`canmain_check`, below): `link` (the SocketCAN interface exists and is down), `unbound` (exactly one USB device with canmain's serial, no interface of it bound to a driver) or `absent` (no interface by canmain's name and no USB device with its serial; on Windows, no present Plug and Play device ending in its serial). No setting refuses. Its line, naming the probe and its reason but never the serial, is printed at the start and kept in `record.json` (`canmain_check`). `link` and `unbound` are Linux only. |
| `tests/test_canmain.py` | Its tests (9) on a made-up sysfs tree and Plug and Play listing, plus one on each environment's real listing; `--can-fail` runs each under each of its mutants (29). `test_unbound` and `test_real_linux` are Linux only, `test_real_windows` Windows only. The runner's rule is `test_run_core.py`'s `test_canmain_check`. |
| `tests/test_decode.py` | Its tests (7), with `data/golden_layout.dbc` and its hand-written canonical text `data/golden_layout.txt`. `--can-fail` runs each test under each of its mutants (33); every one must fail. |
| `tests/layout_hash_crosscheck.py` | The gen-inhibit tester's independent `layout_hash`, written from the agreed line format without reading `decode.py` (moved from reverse-it, 2026-10-22). `test_decode.py`'s `test_crosscheck` requires it to agree with `decode.py` and the golden text on every edit the other tests use. Also a CLI: `DBC [GOLDEN_TXT]`. |
| `tests/test_run_core.py` | The runner's own tests (70, the bench-lock ones on a scratch `bench_lock` log), with a fake suite, a fake two-board bench, `fake_esptool.py` and `fake_ssh.py`. `--can-fail` runs each with its rule switched off; every one must then fail. `test_base_env` (2026-11-01, for ACCEPT-SPEC A7): a local check with `Check.base_env` starts from that environment, not the runner's own. `test_record_settings` (2026-11-01, for A6): with `Engine(record_settings=...)`, or `run --record-settings JSON`, the record holds that masked copy in place of the settings the run used; off by default. Five of them check the digest bridge's extraction folder. Two digests of one commit at once get folders of their own, and the folder is gone after the call, when it succeeds and when it fails (2026-10-24; `engine.DIGEST_ROOT` points it at the test's folder). By default it is in the repo's worktree's `scratch/vtrux-bench/digest/`, never the system temp folder. It is removed even when it holds a read-only file in a read-only folder or, on Windows, a path past 260 characters, and a folder that cannot be removed is printed with the reason (2026-11-01, AGENTS.md "Removing scratch"; `engine.remove_tree`). **`test_pause_own_lock`'s mode "holder" failure, fixed in the test (2026-11-05).** It failed with `AssertionError: ('holder', {'a/first': 'PASS', 'a/second': 'NOT-RUN'})`: once in a full run on NICKY-XPS on 2026-11-01, then 3 times out of 3 for the charge-interposer tester at `a3b0f250` (it passed for them once at `4c4047c1`). The fault was in the test, not the runner. In mode "holder" (the lock before the cutover), the test's other role took the bench as soon as the PAUSE file existed, then removed the file. When the runner reached its next case boundary more than about 0.1 s after check 1 wrote the file, as on a loaded machine, it saw no pause under way and another role's Holder: a lost lock, which is what SPEC section 7 says it should do. Now the role waits for the run's record to show its pause, as a person would; under the log it already waited for `free_paused`. Each mode also runs with the runner made 0.5 s slow to reach each case gate (`_pause_scenario(gate_delay=)`); with the old wait, that run fails in mode "holder" every time (gen-inhibit tester, for the gen-inhibit reviewer). |
| `vtrux_bench/scratch.py` | The one shared scratch helper (ACCEPT-SPEC A14; AGENTS.md "Removing scratch"), merged from the charge-interposer's `scratch_dir.py` and gen-inhibit's `gi_paths.py` (charge-interposer tester, 2026-11-01). `make(parent, name)` makes `<parent>/<name>-<pid>-<hex6>/` with its owner file beside it (`<folder>.owner.json`: pid, process start time, host, platform), so a folder holding a git clone stays clean; it first removes the finished folders beside it. `run()` removes its folder in a finally; `Made()` holds a run's folders, removed by `remove_all()` in the run's finally and by an exit handler on any other way out. `remove(path)` clears read-only flags and retries, tries the whole folder again after waits of 0.05 s doubling to 1.6 s (about 3 s, for a Windows hold on a fresh folder, as `tests/_scratch.py` does), uses `\\?\` paths on Windows, removes a link or junction as a link, never uses `ignore_errors`, and returns and prints what it cannot remove as LEFTOVER lines. `scratch_root(start)` is the worktree's `scratch/`, refused outside a worktree. `Made.add_remote(ssh, host, path, workroot)` holds a folder on a remote host too, listed once however often it is added, removed over ssh (`remove_remote`: `rm -rf`, then a look that it is gone; a LEFTOVER line if not). **`remove_remote` refuses, running nothing**, a LEFTOVER with the reason, unless the path is absolute and normal (no `..`, no `~`), strictly under the host's workroot (itself absolute, normal and not `/`), and its first part below the workroot is one the runner makes: `accept-<record id>` or a chain run's work folder `<suite>_<YYYYmmddTHHMMSS>_<hex4>` (the charge-interposer reviewer, 2026-11-02: an empty setting, `/`, `~` or a mistyped workroot never becomes `rm -rf` on a host). `keep(path, why)` marks a folder kept (a failed run kept for inspection) in its owner file: it is never pruned and `Made` leaves it, listed in `.kept`; taking it out is a person's call (gen-inhibit's reviewer, 2026-11-01). The acceptance runner uses it; each project moves its own harness onto it as its own harness work. |
| `tests/test_scratch_helper.py` | Its tests (12), each naming A14. `--can-fail` runs them first on the unmutated module (the control, which must pass), then under each of its 19 mutants; every one must make a test fail. "One try only" (no whole-tree retry) must fail `test_held_briefly`, a file held open for 1.2 s; it is applied on Windows only, where an open file stops a removal. Two of them remove a kept folder, by pruning it or in `Made`. Two of them keep owner files: none may be left beside a removed folder, and an owner file whose folder is gone is pruned once its run has finished. The long-path mutant is not applied where it changes nothing (off Windows, or with `LongPathsEnabled` = 1, as on NICKY-XPS), and says so. It counts on native Windows; on POSIX it needs a file system that enforces folder permissions (not WSL's `/mnt/c`), not as root. |
| `tests/_scratch.py` | Where these tests put their files: each run's own folder, `<worktree>/scratch/vtrux-bench-tests/<name>/<time>_<pid>`, the worktree found by walking up to the nearest `.git`, so a pinned export inside a project worktree's `scratch/` lands there. No worktree above is a refusal, never the system temp folder (AGENTS.md "Files and folders"; 2026-10-24: before, every run used one fixed folder under the system temp folder, and `test_decode` and `test_run_core` shared it). The folder is removed when the process that made it exits, pass or fail: read-only files made writable and retried, the whole tree tried again for about 3 s while another process holds part of it open, `\\?\` paths on Windows, and a folder that will not go printed with the reason (AGENTS.md "Removing scratch"; 2026-11-01: before, nothing removed them, and a day's runs left 3,359 files; one try alone then left an empty run folder). Once it is gone, its `<name>/` and `vtrux-bench-tests/` go too if nothing else is in them, and another run's exit taking them while a run makes its own costs a retry, three tries in all, 0.1 s and then 0.4 s apart, on the folder gone or, on Windows, still delete-pending (`PermissionError`) (the charge-interposer reviewer, 2026-11-02: 21 empty tool folders were left; gen-inhibit's reviewer, the same day, for the delete-pending case). A killed process's folder stays, under its own name. |
| `tests/test_scratch.py` | Its tests (9): two runs at once get two folders, the walk up from deep inside `scratch/`, the refusal outside any worktree, the three suites above each in a folder of its own name and gone at exit, the folder gone at exit with a read-only file and a path past 260 characters in it, a folder that cannot be removed printed, a folder held for the first two tries still removed, the tool folders gone once empty and kept while another run's folder is in them, and the make retried on `FileNotFoundError` and `PermissionError`, a moment apart, three tries in all (these two on a stand-in worktree in the run's own folder). A test's subprocess that prints a folder it could not remove fails that test, though it exits 0. `--can-fail` runs them under each of its mutants (16), edited copies of `_scratch.py`; every one must fail, two of them only on Windows. None drops the `\\?\` form, which a Windows with long paths enabled does not need. What a mutant leaves is removed after it. |

### Boards and images

A bench may hold several boards. Each `Image` names its `board` (default
"board"); a group's `image` is one image name or a list, one per board.
The runner flashes a board only when it is not already on the image the
group needs, and identifies every board the group needs before and after it
(the group is VOID if one changed under it). A board state that is not a
flashed image, such as an X2 held in ROM, can be an `Image` with its own
flasher and expected identify value.

Images are loaded at the start, never restored at the end (user,
2026-11-11; `SPEC.md` section 6 and its Ruled entries; this supersedes the
end restore of 2026-10-20). At the start of the bench lane, before the
first group, every board the run uses (test gear included) is identified
against the first image the run needs on it, flashed if it reads anything
else, and identified again: correctness rests on that, not on what the last
run left. Before each later group, and at each resume, the same is done for
the images that group needs. At the end the runner restores no board: each
stays on whatever its last case used, and the next run, of either project,
loads what it needs from there. There is no standing image: a suite that
still names `Suite.standing_image` or `standing_images` is refused.

The end pass, after the last case on every run however it ended (but not
after a lost lock), identifies every board the run has an image for, and the
end release carries those reads (and the WiCAN's, if an end hook read it).
**Images not safe to leave idle** are the one exception: a suite marks each
image that is not safe to leave idle on the bench, such as one that floods
CAN when nothing ACKs it or one that boots armed, with `Image(...,
idle_safe=False)`, and names an idle-safe image for each board it marks one
on, `Suite.idle_safe_images = {board: image}` (an image of that board, not
itself marked). A suite that marks an image on a board with no idle-safe
image, or names one that is not such an image, is refused. A board the end
pass finds on a marked image -- or, after an identify that recognised
nothing, last loaded with one in this run -- is loaded with its board's
idle-safe image, identified before and after like any load: one attempt,
also after a flash or identify failure stopped the bench. If that load
fails, or cannot be made (no further flash allowed on the board, an
identify that failed, a lost lock, or the board a resume could not put back,
which is left as found: see "The record"), the release's note, `board_state.txt`,
`index.txt` and `record.json` (`idle_unsafe_left`) open with `LEFT ON
IDLE-UNSAFE IMAGE: <board> on <image>, <why>` (after a lost lock there is
no release, so the record and `index.txt` only). The tester copies that
line into the release message and the board's section of bench-holder.md.
A paused run (`free_paused`) leaves its boards as they are. A run that
never identified or flashed a board (a start hook stopped the bench before
the start pass) has no end reads.

Every esptool image must name its chip (`params["chip"] = {"type":
"ESP32-S3", "mac": "..."}`, both keys) or nothing is written; if the
auto-reset check reads another chip type or MAC, the port reached another
board, and nothing is written. The repo is public: the MAC comes from a
suite's local, unpublished settings at run time, never from a literal in a
file (the tests use a made-up, locally administered MAC).

An identify hook that raises (could not look) stops the bench; the runner
never flashes a board whose state nobody read.

Board hooks (user, 2026-10-20: a run killed without cleanup must not leave a
board armed). A suite may give `on_start` and `on_end`, each called as
`hook(board, image, ctx, logdir)` once per board the bench lane uses and
returning `{"ok": bool, "detail": str, ...}`; a hook that raises or returns
anything else has failed. `on_start` runs before the start pass identifies
the board (image: the first one the run needs there); a failure stops the
bench, so nothing is identified or flashed and every bench group is
NOT-RUN. `on_end` runs after the last case and the end pass (the boards
identified, an idle-unsafe image replaced by its board's idle-safe one), on
every run however it ended, a lane crash included (image: the one the end
pass found the board on, or None if it is on no image of the suite), but
not once the lock is no longer ours, when the end hooks' board actions are
skipped as before; a failure, or not running, opens
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
out, or returned no verdict -- never a PASS; or its argv, env or cwd names
a setting the run does not have, such as an acceptance list's
`{build_<id>}` after that build failed: that check alone is not started and
says which setting, and the rest of its lane runs), `NOT-RUN` (with the reason),
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
                                 "workroot": "<home>/scratch-tester/<project>-harness/runner",
                                 "env": {...}}}
                     a remote host's workroot is an absolute path in the
                     tester's scratch folder on that host, never the system
                     temp folder (AGENTS.md "Files and folders")
    esptool          {"argv": [...esptool.py], "port": "COM3", "baud": 921600}
    wallclock_patterns  regexes over process command lines that mark a
                     wall-clock test running (either project's simulators)
    canmain_check    required by every run that needs the bench (SPEC.md
                     section 8; vtrux_bench/run/canmain.py):
                     {"probe": "link" | "unbound" | "absent",
                      "reason": "<why this probe>",
                      "iface": "<canmain's SocketCAN interface>",
                      "serial_file": "<path of reverse-it firmware/bench-holder.md>"}
                     link needs iface; unbound needs serial_file; absent
                     needs both. The serial file holds one line
                     "canmain serial: <serial>"; the serial stays out of
                     git and out of every message
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
end pass and the release, but runs no start hooks and flashes nothing for
cases that will not run. A
board the resume cannot put back stops the run without losing the lock: the
rest is NOT-RUN, the record and `index.txt` open with `STOPPED: <board> is
<read>, expected <image> (<its expect value>), cannot flash`, and the
release carries what was read. The end pass leaves that board untouched,
as found for the investigation: no identify and no idle-safe load, so the
release carries the identity read at the resume. If it is on an image not
safe to leave idle, or cannot be shown off the marked image it was expected
on, it gets a `LEFT ON IDLE-UNSAFE IMAGE` line naming the stop. The other
boards go through the end pass as normal, and the end hooks still run on
every board, the stopped one included. With `--keep-lock PID` the run still hands the lock to that
session at the end, so a resume stop leaves the bench held by the session,
not free. If the bench is still free when you read that (or your session
holds it), investigate the board before any other run, try to explain why
it could not be put back, and tell your reviewer. A session's own `take --run <id>` of the run is a lost lock:
`LOCK LOST` in the record, nothing written to the log, nothing released.
`STOP` ends the run (the end pass still runs; `now` also kills the check
in progress -- on a remote host
too: the remote line records `timeout`'s pid in the check's outdir, and
the runner kills that process group once the local ssh is gone).
