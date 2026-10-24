#!/usr/bin/env python3
"""Mutants of interlock_judge.attribute_by_sender and its use in Ctx;
test_interlock_judge.py must fail on each BY A CHECK, not by crashing.

    py -3.14 tools/gen_inhibit/bench/test_interlock_judge_attribution_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH: the judge finds the project
root from its own path or, for a copy, from sys.path, and without it a copy crashes
on import. A crash is not a kill: the first two runs of these mutants (2026-10-16)
"killed" all six that way. So a mutant counts as killed only if the test prints at
least one FAIL line, and a crash is reported as such.
Added 2026-10-16 with the sender-log attribution (reviewer).
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
    ("drop the time term: the first of a pair is always the sender's",
     "pick = j if da < db else twin", "pick = j"),
    ("the later of a pair is always the sender's",
     "pick = j if da < db else twin", "pick = twin"),
    ("never refuse an ambiguous pair",
     "if tol is None or abs(da - db) <= tol:", "if False:"),
    ("no twin search (the payload walk alone)",
     "if ik == cmd and hk == h and k not in used:", "if False:"),
    ("offset ignored", "exp = src[p][0] + off", "exp = src[p][0]"),
    ("Ctx ignores the sender log",
     "if sent is not None:\n            self.vcm, self.dev, self.attribution",
     "if False:\n            self.vcm, self.dev, self.attribution"),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-interlock_attr_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST), os.path.join(d, "scratch")],
                           cwd=root, env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
