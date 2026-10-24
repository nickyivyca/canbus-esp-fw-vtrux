#!/usr/bin/env python3
"""Mutants of interlock_bench.build's "modes_at" derivation (2026-10-16: the
liveflag hold cases, the host frames verbatim with the later mode lines moved);
test_interlock_judge.py must fail on each BY A CHECK, not by crashing.

    py -3.14 tools/gen_inhibit/bench/test_interlock_bench_modes_at_mutants.py

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
SRC = "interlock_bench.py"
TEST = "test_interlock_judge.py"

MUTANTS = [
    ("the mode lines are not replaced",
     "            modes = [modes[0]] + [(t, m, off) for t, m in src[3]]\n", "            pass\n"),
    ("the arm line dropped",
     "modes = [modes[0]] + [(t, m, off) for t, m in src[3]]", "modes = [(t, m, off) for t, m in src[3]]"),
    ("the offset not carried", "            off = modes[0][2]\n", "            off = 0\n"),
    ("no guard on a mode line past the frames",
     "            if max(t for t, _m in src[3]) >= fr[-1][0]:", "            if False:"),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-interlock_modes_at_mutants")   # the worktree's scratch/ (AGENTS.md)
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
