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
    ("a FAIL that passes on re-run is not noted flaky",
     'if got[0]["verdict"] != c["verdict"] and c["verdict"] in e["verdicts"][1:]:', 'if False:'),
    ("every re-run verdict change is called flaky",
     'if got[0]["verdict"] != c["verdict"] and c["verdict"] in e["verdicts"][1:]:',
     'if got[0]["verdict"] != c["verdict"] and len(e["verdicts"]) > 1:'),
    ("re-runs are decided by the last run", 'got = [r.get(cid) for r in runs]', 'got = [r.get(cid) for r in runs[::-1]]'),
    ("a desk check re-runs without its inputs", 'todo += [d for d in reads_map.get(f, []) if d not in seen]', 'pass'),
    ("a missing figure counts as the baseline's", 'b, r = base.get(k, NO_RESULT), run.get(k, NO_RESULT)',
     'b, r = base.get(k, NO_RESULT), run.get(k, base.get(k))'),
    ("finish exits 0 on flags", 'return 1 if any(e["flagged"] for e in entries) else 0', 'return 0'),
    ("finish overwrites a report", 'with open(path, "x", encoding="utf-8", newline="\\n") as f:',
     'with open(path, "w", encoding="utf-8", newline="\\n") as f:'),
    ("chain: no re-run round", '    if again:\n        print("chain: re-running', '    if False:\n        print("chain: re-running'),
    ("chain: a refused run is compared", '    if not first:\n        print("chain: the core run did not start',
     '    if False:\n        print("chain: the core run did not start'),
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
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
