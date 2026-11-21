"""A [[suite_run]] of the acceptance list: a nested chain run with its own
record inside the acceptance record (ACCEPT-SPEC A7).

    python -m vtrux_bench.accept.nested --suite <file.py>:<NAME>
        --settings <run settings> --record-settings <masked copy>
        --run-id <id> --out <folder> [--rerun-flagged]
        [--from-hold PID [--keep-lock PID]] [--group G]... [--check GLOB]...
    python -m vtrux_bench.accept.nested --suite <file.py>:<NAME>
        --settings <run settings> --needs-bench [--group G]... [--check GLOB]...

--from-hold PID (the acceptance runner's, passed to every suite run): when
the first run needs the bench, the chain runner starts it from the role's
hand take under PID (shared/bench SPEC section 6). A suite run that needs no
bench takes no lock and is given nothing. nested.json records the PID and
whether the first run was given it ("from_hold").

--keep-lock PID (the acceptance runner's, with --from-hold and the same PID,
for every suite run that needs the bench but the last; the gen-inhibit
reviewer, 2026-11-16): the hold passes from run to run and the bench is
never free during the acceptance. The suite run's last run that needs the
bench ends by handing the lock back to PID (the chain runner's --keep-lock),
for the next suite run's --from-hold; without --keep-lock that run releases
the bench. A --keep-lock without --from-hold, or with another PID, is
refused (exit 2).

Under a hold, the re-run of flagged cases does the same as the first run:
the first run hands the lock back to PID when the re-run will need the
bench (a flagged case in a bench group, decided at the first run's release,
when every bench result is in; the chain runner's Engine.keep_lock_if), the
re-run starts from that hold (--from-hold), and it hands the lock back
only with --keep-lock. A re-run that needs no bench is given neither, and
then the first run hands the lock back only with --keep-lock. nested.json's
"keep_lock" records the PID asked for ("pid", or null) and whom each run
handed the lock to at its end ("first", "rerun": a PID, or null for a
release or no take).

--needs-bench prints {"needs_bench": true|false, "role": the suite's role}
as its last line for the suite and selection, and exits 0, taking no lock
and writing nothing: the acceptance runner asks each suite run before the
chain, to find its bench parts, the last of them, and the role its hold is
taken for (run.py, hold.py; ACCEPT-SPEC A18).

The run settings' runs_root is the acceptance record's runs/ (settings.py),
so the nested record lands there, holding the masked settings (A6). With
--rerun-flagged, the cases the run flagged (any result not PASS, KNOWN-FAIL
or NOT-APPLICABLE, or a known FAIL that passed) run again as a new nested
record, <id>-rerun, selecting only them: a re-run is never added to a
closed record (A7).

nested.json's "catalogue_skips" ({"<group>/<check>": reason}) is the
suite's catalogue of declared skips as loaded from its tree, every case
with Check.skip, selected or not: the acceptance runner judges a list's
declared_skips against it (ACCEPT-SPEC A12a; judge.suite_run_verdict).

The first, whole run decides (A12 as written; the charge-interposer
reviewer, 2026-11-01): this exits with the first run's exit code whatever
the re-run gives. The re-run is evidence, for the comparisons and for the
person who rules: <out>/nested.json lists each flagged case with its first
verdict and its re-run verdict side by side, and so does the console.

RULES: each is switched off by a mutant in tests/test_accept_suite_runs.py,
which must then fail.
"""

import argparse
import json
import os
import sys

from ..run.cli import load_settings, load_suite
from ..run.engine import Engine, exit_code
from ..run.lock import LockError
from ..run.model import SuiteError, Verdict

RULES = {k: True for k in ("nested_masked", "rerun_new_record", "rerun_only_flagged",
                           "first_decides", "from_hold_bench", "from_hold_bench_only",
                           "keep_lock_passed", "keep_same_pid", "keep_for_rerun",
                           "keep_only_bench_rerun", "rerun_from_hold")}

OK = (Verdict.PASS, Verdict.KNOWN_FAIL, Verdict.NOT_APPLICABLE)


def flagged(record):
    """The full check ids ("<group>/<check>") of the cases the run flagged."""
    return sorted({r["check"] for r in record["results"]
                   if r["verdict"] not in OK or r.get("known_fail_passed")})


def _worst(record, check):
    vs = [r["verdict"] for r in record["results"] if r["check"] == check]
    bad = [v for v in vs if v not in OK]
    return bad[0] if bad else (vs[0] if vs else "absent")


def rerun_selection(marked, checks):
    """What the re-run of flagged cases selects (--check patterns)."""
    return marked if RULES["rerun_only_flagged"] else list(checks)


def run(suite, settings, masked, run_id, groups=(), checks=(), rerun=False, out=print,
        from_hold=None, keep_lock=None):
    """{"first": ..., "rerun": ..., "flagged": [...], "exit": int, "from_hold":
    {"pid", "given"} or None, "keep_lock": {"pid", "first", "rerun"} or None}."""
    if keep_lock is not None and RULES["keep_same_pid"] and keep_lock != from_hold:
        raise SuiteError(
            "--keep-lock %r: the acceptance runner passes it only with --from-hold and "
            "the same PID (here %r), so the hold passes from run to run" % (keep_lock,
                                                                          from_hold))
    if not RULES["keep_lock_passed"]:
        keep_lock = None
    rec_settings = masked if RULES["nested_masked"] else None
    eng = Engine(suite, settings, run_id=run_id, groups=list(groups), checks=list(checks),
                 record_settings=rec_settings, out=out)
    given = (from_hold is not None and RULES["from_hold_bench"]
             and (eng.needs_bench() or not RULES["from_hold_bench_only"]))
    if given:
        eng.from_hold = from_hold
        if keep_lock is not None:
            eng.keep_lock = keep_lock
        elif rerun and RULES["keep_for_rerun"]:
            # hand the hold back only if the re-run will need the bench,
            # decided at the release, when every bench result is in
            def keep_if(results):
                marked = flagged({"results": results})
                if not marked:
                    return False
                if not RULES["keep_only_bench_rerun"]:
                    return True
                return Engine(suite, settings, groups=[],
                              checks=rerun_selection(marked, checks)).needs_bench()
            eng.keep_lock = from_hold
            eng.keep_lock_if = keep_if
    first = eng.run()
    rc = exit_code(first)
    res = {"first": {"run_id": first["run_id"], "dir": eng.run_dir, "exit": rc},
           "rerun": None, "flagged": [],
           "from_hold": None if from_hold is None else {"pid": from_hold,
                                                        "given": bool(given)},
           "keep_lock": None if from_hold is None else {"pid": keep_lock,
                                                        "first": eng.handed_to,
                                                        "rerun": None},
           # the suite's catalogue of declared skips, as loaded from its
           # tree: every case with Check.skip, whether selected or not, with
           # its reason (ACCEPT-SPEC A12a; judge.suite_run_verdict)
           "catalogue_skips": {"%s/%s" % (g.name, c.id): c.skip
                               for g in suite.groups for c in g.checks if c.skip}}
    marked = flagged(first)
    if rerun and marked:
        sel = rerun_selection(marked, checks)
        rid = run_id + "-rerun" if RULES["rerun_new_record"] else None
        if rid is None:
            # the mutant: the re-run's results added into the first run's folder
            with open(os.path.join(eng.run_dir, "record.json"), encoding="utf-8") as f:
                closed = json.load(f)
        eng2 = Engine(suite, settings, run_id=rid or run_id + "-x", groups=[],
                      checks=sel, record_settings=rec_settings, out=out)
        if given and RULES["rerun_from_hold"] and eng2.needs_bench():
            eng2.from_hold = from_hold
            if keep_lock is not None:
                eng2.keep_lock = keep_lock
        second = eng2.run()
        if rid is None:
            closed["results"] += second["results"]
            with open(os.path.join(eng.run_dir, "record.json"), "w", encoding="utf-8") as f:
                json.dump(closed, f)
        res["rerun"] = {"run_id": second["run_id"], "dir": eng2.run_dir,
                        "exit": exit_code(second)}
        if res["keep_lock"] is not None:
            res["keep_lock"]["rerun"] = eng2.handed_to
        res["flagged"] = [{"check": c, "first": _worst(first, c),
                           "rerun": _worst(second, c)} for c in marked]
    else:
        res["flagged"] = [{"check": c, "first": _worst(first, c), "rerun": None}
                          for c in marked]
    res["exit"] = rc if RULES["first_decides"] else (
        res["rerun"]["exit"] if res["rerun"] else rc)
    for f in res["flagged"]:
        out("flagged %-50s first %-12s re-run %s" % (f["check"], f["first"],
                                                     f["rerun"] or "(not re-run)"))
    out("suite run %s: exit %d (the first, whole run decides)" % (run_id, res["exit"]))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vtrux_bench.accept.nested")
    ap.add_argument("--suite", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--record-settings")
    ap.add_argument("--run-id")
    ap.add_argument("--out")
    ap.add_argument("--rerun-flagged", action="store_true")
    ap.add_argument("--from-hold", type=int, metavar="PID")
    ap.add_argument("--keep-lock", type=int, metavar="PID")
    ap.add_argument("--needs-bench", action="store_true")
    ap.add_argument("--group", action="append", default=[])
    ap.add_argument("--check", action="append", default=[])
    a = ap.parse_args(argv)
    if not a.needs_bench:
        missing = [o for o, v in (("--record-settings", a.record_settings),
                                  ("--run-id", a.run_id), ("--out", a.out)) if v is None]
        if missing:
            ap.error("the following arguments are required: %s" % ", ".join(missing))
    try:
        settings = load_settings(a.settings)
        suite = load_suite(a.suite, settings)
        if a.needs_bench:
            eng = Engine(suite, settings, groups=a.group, checks=a.check)
            print(json.dumps({"needs_bench": bool(eng.needs_bench()),
                              "role": suite.role}))
            return 0
        res = run(suite, settings, load_settings(a.record_settings), a.run_id,
                  a.group, a.check, a.rerun_flagged, from_hold=a.from_hold,
                  keep_lock=a.keep_lock)
    except (SuiteError, LockError) as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "nested.json"), "x", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1, sort_keys=True)
    return res["exit"]


if __name__ == "__main__":
    sys.exit(main())
