#!/usr/bin/env python3
"""Mutants of interlock_judge.judge_trip's evidence rule (spec 7, user 2026-10-16:
the evidence 0x051 may be answered; the inhibit stops from the next one);
test_interlock_judge.py must fail on each BY A CHECK, not by crashing.

    py -3.14 tools/gen_inhibit/bench/test_interlock_judge_evidence_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH (the judge finds the
project root from sys.path when run as a copy). A mutant counts as killed only
if the test prints at least one FAIL line.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "interlock_judge.py"
TEST = "test_interlock_judge.py"

MUTANTS = [
    ("the old rule: silent from the evidence 0x051 itself",
     "silent_from(c, v, after,\n", "silent_from(c, v, evidence,\n"),
    ("a second answer let through",
     "after = c.vcm_after(evidence)\n", "after = c.vcm_after(c.vcm_after(evidence))\n"),
    ("no silence check after the evidence",
     "            if after is not None:\n                silent_from(c, v, after,",
     "            if False:\n                silent_from(c, v, after,"),
    ("evidence answers not counted",
     "evidence_answered=len(", "evidence_answered=0 * len("),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-interlock_evidence_mutants")   # the worktree's scratch/ (AGENTS.md)
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
