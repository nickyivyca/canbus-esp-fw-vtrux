# shared/twai_port/probe_analysis/

The analysis of the transmit-abort probe's answers (`../SPEC.md` T13, the
probe in `../test_app/`), in Python, with no transport: the answer parsing,
the judgement and the analysis that each project's bench driver uses. It is
harness, so it is the tester's: the tester of the project that needs a
change makes it (AGENTS.md, "Who owns what"). It is not in `test_app/`,
which T11 gives to the implementors.

**The move.** It was moved here from gen-inhibit's bench drivers at
gen-inhibit/main `74555175` (the shared probe-analysis move, agreed by the
charge-interposer reviewer and the gen-inhibit reviewer, 2026-11-27) by the
charge-interposer tester, 2026-11-28. Every moved function has its source's
code unchanged, so no verdict changes. The transport stayed in each project:
- gen-inhibit: the HTTP `POST /gi_txabort`, the hotspot and its status
  page, the witness's capture, the flood and the acknowledging node's open
  and close;
- the charge interposer: the T19 app's serial command `txabort
  <key>=<value> ...` (`../test_app/README.md`, "The T19 app on the S3"),
  whose `"answer"` field is the probe's answer.

Each project moves its callers at its next change of those files.

    python3.13 -B shared/twai_port/probe_analysis/test_unacked_analysis.py [--mutants]
    python3.13 -B shared/twai_port/probe_analysis/test_unacked_judge.py [--mutants]
    python3.13 -B shared/twai_port/probe_analysis/test_txabort_analysis.py [--mutants]
    python3.13 -B shared/twai_port/probe_analysis/test_mutants.py [--mutants]

Each test prints one line per check and exits 0 only if every check passes.
With `--mutants` it applies each of its mutants to a copy and runs itself on
the copy; it exits 0 only if the unmutated module passes and every mutant
fails a check. The copies go in `scratch/twai_port-analysis-mutants/` at the
worktree's root, removed when the run ends.

| File | What | Source at gen-inhibit/main `74555175` |
|---|---|---|
| `unacked_analysis.py` | m1-m3: each trial's TS rises and the TS = 0 spans between them by error state, the resends with TS held at 1, the witness join and its offset, the abort, the hold's check of the acknowledging node, and the M1, abort and hold tables | `gen-inhibit/tools/bench/unacked_analysis.py`, whole |
| `unacked_judge.py` | m1-m3 on the bench: the plan of calls (the gen-inhibit reviewer's minimums), each call's arguments (`call_args()`, new) and its echo check, the witness's trial copies, each answer processed into its summary and hold check, on the witness clock or the host clock, the tables, the positive control, the rejudge of a saved record, the restart and stop rules | `gen-inhibit/tools/bench/unacked_bench.py`, every function but the transport's and the bench's: `http_post`, `Node`, `run_call`, `start_refusal`, `page_brief`, `rejudge` (the file), `main` and `_line`. `call_args()` is `query()`'s argument list without the URI |
| `txabort_analysis.py` | the older phases (control, noabort, abort): the alert words, the echo check, the arguments (`probe_args()`, new) and the answer record (`answer_record()`, new), the witness's rows by trial number (`WitnessRows`), each trial's outcome, the join and the phase's summary with its stop conditions | `gen-inhibit/tools/bench/wican_txabort_bench.py`, every function but the transport's and the bench's: `http`, `gi_status`, `device_id`, `run_probe`, `Flood`, the capture thread of `Witness` and `main`. `probe_args()` and `answer_record()` are `run_probe()` without the HTTP call |
| `mutants.py` | the mutant runner the tests share: one copy per mutant, the control first, every edit matched exactly once, the scratch folder removed | the runner in `test_unacked_analysis.py` and `test_unacked_bench.py` |
| `test_unacked_analysis.py` | 47 checks and 25 mutants, all as in its source | `gen-inhibit/tools/bench/test_unacked_analysis.py` |
| `test_unacked_judge.py` | 51 checks and 35 mutants. Its source's 55 checks and 36 mutants, less those of the code that stayed with gen-inhibit's driver (the call that cannot connect, the four start refusals, and their three mutants), with the query's checks made on `call_args()`, one check and two mutants for it added | `gen-inhibit/tools/bench/test_unacked_bench.py` |
| `test_txabort_analysis.py` | 56 checks and 39 mutants, new: gen-inhibit had no test of `wican_txabort_bench.py`. `../test_app/README.md` gives m1-m3's answer, not the older phases' fields, so the synthetic trials use the fields `wican_txabort_bench.py` read | none |
| `test_mutants.py` | 8 checks and 6 mutants of `mutants.py`: a killed mutant, a survivor, an edit with no match or two, a failing control, a mutant whose test raises, the scratch folder removed | none |
