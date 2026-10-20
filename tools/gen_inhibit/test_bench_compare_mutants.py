#!/usr/bin/env python3
"""Mutants of bench_compare.py; test_bench_compare.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B test_bench_compare_mutants.py

Each mutant copies bench_compare.py and its test into a fresh folder in the machine's
temp folder and applies one edit. Gen-inhibit tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "bench_compare.py"
TEST = "test_bench_compare.py"

MUTANTS = [
    ("a figure inside the spread re-runs", 'rows.append((k, "DIFF", not inside,', 'rows.append((k, "DIFF", True,'),
    ("a figure outside the spread does not re-run", 'rows.append((k, "DIFF", not inside,', 'rows.append((k, "DIFF", False,'),
    ("the spread's edges are outside it", 'inside = sp[0] <= r <= sp[1]', 'inside = sp[0] < r < sp[1]'),
    ("a figure with no spread does not re-run",
     'rows.append((k, "DIFF", True, "no baseline spread: any change re-runs"))',
     'rows.append((k, "DIFF", False, "no baseline spread: any change re-runs"))'),
    ("the spread pair is not looked up", 'if not m:\n        return None', 'if True:\n        return None'),
    ("identity figures are flagged", 'if k in IDENTITY:', 'if False:'),
    ("a figure that differs is not flagged", '            if state == "DIFF":\n                e["flagged"] = True',
     '            if state == "DIFF":\n                pass'),
    ("a verdict that differs is not flagged", 'if got[0]["verdict"] != c["verdict"]:', 'if False:'),
    ("stage (a) checks are compared", 'if c.get("stage_a"):', 'if False:'),
    ("a check-level NOT-RUN reaches only the check's own id",
     'for i in ids_of.get(fid) or [fid.split("/")[1]]:', 'for i in [fid.split("/")[1]]:'),
    ("an exit-code ERROR is dropped", 'e["extra"] += ["%s %s" % (r["verdict"], r.get("detail", "")) for r in rs]', 'pass'),
    ("a re-run's verdict is not looked at",
     'if any(c2 != c["verdict"] for c2 in e["verdicts"][1:] if c2 != NO_RESULT):', 'if False:'),
    ("a disagreement is not noted flaky",
     'if len(set(ran)) > 1 and c["verdict"] in ran:', 'if False:'),
    ("only FAIL then PASS is flaky (the 2026-10-19 rule)",
     'if len(set(ran)) > 1 and c["verdict"] in ran:',
     'if len(set(ran)) > 1 and c["verdict"] in ran[1:] and ran[0] != c["verdict"]:'),
    ("every re-run verdict change is called flaky",
     'if len(set(ran)) > 1 and c["verdict"] in ran:', 'if len(set(ran)) > 1:'),
    ("NOT-RUN counts as a verdict",
     'ran = [v for v in e["verdicts"] if v not in (NO_RESULT, "NOT-RUN")]',
     'ran = [v for v in e["verdicts"] if v != NO_RESULT]'),
    ("the order-specific notes are kept beside the flaky one",
     'e["notes"] = [n for n in e["notes"] if not n.startswith("VERDICT ")',
     'e["notes"] = [n for n in e["notes"] if True'),
    ("a flaky PASS first keeps its re-run note",
     'and n != "a re-run\'s verdict is not the baseline\'s"]', ']'),
    ("the flaky note names the baseline's verdict as the re-run's",
     '% (ran[0], next(v for v in ran[1:] if v != ran[0])))', '% (ran[0], c["verdict"]))'),
    ("re-runs are decided by the last run", 'got = [r.get(cid) for r in runs]', 'got = [r.get(cid) for r in runs[::-1]]'),
    ("a desk check re-runs without its inputs", 'todo += [d for d in reads_map.get(f, []) if d not in seen]', 'pass'),
    ("a missing figure counts as the baseline's", 'b, r = base.get(k, NO_RESULT), run.get(k, NO_RESULT)',
     'b, r = base.get(k, NO_RESULT), run.get(k, base.get(k))'),
    ("finish exits 0 on flags", 'return 1 if any(e["flagged"] for e in entries) else 0', 'return 0'),
    ("finish overwrites a report", 'with open(path, "x", encoding="utf-8", newline="\\n") as f:',
     'with open(path, "w", encoding="utf-8", newline="\\n") as f:'),
    ("chain: no re-run round", '    if again:\n        print("chain: re-running', '    if False:\n        print("chain: re-running'),
    ("chain: a refused run is compared", '    if rec1 is None:\n        print("chain: the core run did not finish',
     '    if False:\n        print("chain: the core run did not finish'),
    ("chain: a killed run (its folder, no record) is read anyway",
     'rec1 = run_record(first) if first else None', 'rec1 = load_json(os.path.join(first, "record.json")) if first else None'),
    ("chain: a killed run is said not to have started",
     'print("chain: the core run did not finish (rc %s, %s)', 'print("chain: the core run did not start (rc %s, %s)'),
    ("chain: a killed re-run is read anyway",
     'rec2 = run_record(second) if second else None', 'rec2 = load_json(os.path.join(second, "record.json")) if second else None'),
    ("chain: a killed re-run is listed beside the first",
     '        else:\n            print("chain: the re-run did not finish',
     '        else:\n            dirs.append(second)\n            print("chain: the re-run did not finish'),
    ("report: a run with no record is compared", '        if void:\n', '        if False:\n'),
    ("report: a void run exits 0", '                print("VOID: %s has no record.json (killed, or still running); nothing compared" % d)\n            return 2',
     '                print("VOID: %s has no record.json (killed, or still running); nothing compared" % d)\n            return 0'),
    ("report: only the first run is looked at", 'void = [d for d, r in zip(a.runs, recs) if r is None]',
     'void = [d for d, r in zip(a.runs[:1], recs) if r is None]'),
    ("chain: the re-run repeats the whole selection", 'rc2, second, _ = core_run(a.settings, (), again)',
     'rc2, second, _ = core_run(a.settings, a.group, a.check)'),
]


def main():
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "bench_compare")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "",
            ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
