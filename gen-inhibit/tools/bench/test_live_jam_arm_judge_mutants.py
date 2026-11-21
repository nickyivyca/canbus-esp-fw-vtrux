#!/usr/bin/env python3
"""Mutants of live_jam_arm.judge()'s reset detector; test_live_jam_arm_judge.py must fail on each.

    python3.13 -B gen-inhibit/tools/bench/test_live_jam_arm_judge_mutants.py

Added 2026-11-20 with the fix for the gen-inhibit reviewer's answer to the run (C) report: the
arm's own tx_ok restart at its POST (the previous arm's count, mode 0, then the new arm's, mode 3)
was counted as a device reset (livetrain p2000 run3 arm 1). A tx_ok fall now counts only between
two answered readings both in mode 3.

Each mutant copies live_jam_arm.py and the test into a fresh folder under the worktree's scratch/,
applies one edit, and runs the test from the current folder with this folder on PYTHONPATH for
everything else. An edit that does not apply is an error, not a kill. A kill is a FAIL line with no
traceback: a crash is not a kill. The unmutated copy runs first, as the control: if it fails, no
mutant is judged and the run exits 1.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "live_jam_arm.py"
TEST = "test_live_jam_arm_judge.py"

MUTANTS = [
    ("a tx_ok fall counts whatever the modes (the old behaviour)",
     "        tx_fell = (last_tx is not None and tx is not None and tx < last_tx\n"
     "                   and mode in (3, None) and last_mode in (3, None))\n",
     "        tx_fell = (last_tx is not None and tx is not None and tx < last_tx)\n"),
    ("the previous reading's mode not checked",
     "                   and mode in (3, None) and last_mode in (3, None))\n",
     "                   and mode in (3, None))\n"),
    ("the previous reading's mode never recorded",
     "            last_tx, last_mode = tx, mode\n",
     "            last_tx = tx\n"),
    ("a tx_ok fall never counts",
     "        if tx_fell or (mode not in (3, None)",
     "        if (mode not in (3, None)"),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-live_jam_arm_judge")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for i, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % i)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(
            src if old is None else src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        env = dict(os.environ, PYTHONPATH=HERE, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=root, env=env,
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fails), ", CRASHED" if crashed else ""))
            if not ok:
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d%s%s)" % ("KILLED  " if dead else "SURVIVED", i, name, r.returncode,
                                           ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
