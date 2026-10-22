#!/usr/bin/env python3
"""Mutants of interlock_judge's diag cadence check (spec 10, reviewer 2026-10-16:
no diag id silent longer than DIAG_GAP_MAX_S while armed);
test_interlock_judge.py must fail on each BY A CHECK, not by crashing.

    py -3.14 tools/gen_inhibit/bench/test_interlock_judge_cadence_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH (the judge finds the
project root from sys.path when run as a copy). A mutant counts as killed only
if the test prints at least one FAIL line.
"""
import os
import shutil
import subprocess
import tempfile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "interlock_judge.py"
TEST = "test_interlock_judge.py"

MUTANTS = [
    ("the bound doubled", "DIAG_GAP_MAX_S = 2.5", "DIAG_GAP_MAX_S = 5.0"),
    ("spans ignore the mode (mode 0 judged too)",
     "for k, (t, m) in enumerate(posts) if m != 0 and end is not None]",
     "for k, (t, m) in enumerate(posts) if end is not None]"),
    ("no gap from the last page to the span's end",
     "x = [a] + [t for t in ts if a <= t < b] + [b]", "x = [a] + [t for t in ts if a <= t < b]"),
    ("no diag page at all accepted",
     "not over and (gaps or longest <= DIAG_GAP_MAX_S),", "not over,"),
    ("recorded, never checked",
     "not over and (gaps or longest <= DIAG_GAP_MAX_S),", "True,"),
    ("spans split at every POST (a silence across a re-arm hidden)",
     "        if spans and abs(spans[-1][1] - a) < 1e-9:", "        if False:"),
]


def main():
    root = os.getcwd()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "interlock_cadence_mutants")   # outside SeaDrive (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(os.path.join(d, "scratch"))
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, w = old.replace("\n", nl), new.replace("\n", nl)
        if src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST), os.path.join(d, "scratch")],
                           cwd=root, env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
