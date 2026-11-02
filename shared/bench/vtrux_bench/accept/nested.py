"""A [[suite_run]] of the acceptance list: a nested chain run with its own
record inside the acceptance record (ACCEPT-SPEC A7).

    python -m vtrux_bench.accept.nested --suite <file.py>:<NAME>
        --settings <run settings> --record-settings <masked copy>
        --run-id <id> --out <folder> [--rerun-flagged]
        [--group G]... [--check GLOB]...

The run settings' runs_root is the acceptance record's runs/ (settings.py),
so the nested record lands there, holding the masked settings (A6). With
--rerun-flagged, the cases the run flagged (any result not PASS, KNOWN-FAIL
or NOT-APPLICABLE, or a known FAIL that passed) run again as a new nested
record, <id>-rerun, selecting only them: a re-run is never added to a
closed record (A7).

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
from ..run.model import SuiteError, Verdict

RULES = {k: True for k in ("nested_masked", "rerun_new_record", "rerun_only_flagged",
                           "first_decides")}

OK = (Verdict.PASS, Verdict.KNOWN_FAIL, Verdict.NOT_APPLICABLE)


def flagged(record):
    """The full check ids ("<group>/<check>") of the cases the run flagged."""
    return sorted({r["check"] for r in record["results"]
                   if r["verdict"] not in OK or r.get("known_fail_passed")})


def _worst(record, check):
    vs = [r["verdict"] for r in record["results"] if r["check"] == check]
    bad = [v for v in vs if v not in OK]
    return bad[0] if bad else (vs[0] if vs else "absent")


def run(suite, settings, masked, run_id, groups=(), checks=(), rerun=False, out=print):
    """{"first": ..., "rerun": ..., "flagged": [...], "exit": int}."""
    rec_settings = masked if RULES["nested_masked"] else None
    eng = Engine(suite, settings, run_id=run_id, groups=list(groups), checks=list(checks),
                 record_settings=rec_settings, out=out)
    first = eng.run()
    rc = exit_code(first)
    res = {"first": {"run_id": first["run_id"], "dir": eng.run_dir, "exit": rc},
           "rerun": None, "flagged": []}
    marked = flagged(first)
    if rerun and marked:
        sel = marked if RULES["rerun_only_flagged"] else list(checks)
        rid = run_id + "-rerun" if RULES["rerun_new_record"] else None
        if rid is None:
            # the mutant: the re-run's results added into the first run's folder
            with open(os.path.join(eng.run_dir, "record.json"), encoding="utf-8") as f:
                closed = json.load(f)
        eng2 = Engine(suite, settings, run_id=rid or run_id + "-x", groups=[],
                      checks=sel, record_settings=rec_settings, out=out)
        second = eng2.run()
        if rid is None:
            closed["results"] += second["results"]
            with open(os.path.join(eng.run_dir, "record.json"), "w", encoding="utf-8") as f:
                json.dump(closed, f)
        res["rerun"] = {"run_id": second["run_id"], "dir": eng2.run_dir,
                        "exit": exit_code(second)}
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
    ap.add_argument("--record-settings", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rerun-flagged", action="store_true")
    ap.add_argument("--group", action="append", default=[])
    ap.add_argument("--check", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        settings = load_settings(a.settings)
        suite = load_suite(a.suite, settings)
        res = run(suite, settings, load_settings(a.record_settings), a.run_id,
                  a.group, a.check, a.rerun_flagged)
    except SuiteError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "nested.json"), "x", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1, sort_keys=True)
    return res["exit"]


if __name__ == "__main__":
    sys.exit(main())
