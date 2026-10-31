#!/usr/bin/env python3
"""Mutants of interlock_judge's latch page window (B20 page phase, reviewer
2026-10-20: the judged pages start at the first page after the latch that has
caught up with it, or at latch + PAGE_LAG_S if that comes first);
test_interlock_judge.py must fail on each BY A CHECK, not by crashing.

    py -3.14 gen-inhibit/tools/bench/test_interlock_judge_phase_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH (the judge finds the
project root from sys.path when run as a copy). A mutant counts as killed only
if the test prints at least one FAIL line. Gen-inhibit tester.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1. Until then a crashed mutant was also counted as killed.
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
    ("no page in the lag counts (the old window)",
     "return early[0] if early else t_latch + PAGE_LAG_S", "return t_latch + PAGE_LAG_S"),
    ("the window always starts at the latch",
     "return early[0] if early else t_latch + PAGE_LAG_S", "return t_latch"),
    ("a page in the lag that has not caught up starts the window",
     "early = [t for t, d in pages_between(pages, t_latch, t_latch + PAGE_LAG_S) if reads(d)]",
     "early = [t for t, d in pages_between(pages, t_latch, t_latch + PAGE_LAG_S)]"),
    # The reviewer's mutant: a page after the lag that has not caught up must stay
    # a check FAIL, not drop out and become a precondition miss.
    ("only pages that have caught up are judged",
     "after = pages_between(c.p7f1, caught_up(c.p7f1, lambda d: disable_name(d) == reason), t_stop)",
     "after = [p for p in pages_between(c.p7f1, caught_up(c.p7f1, lambda d: disable_name(d) == reason), t_stop)"
     " if disable_name(p[1]) == reason]"),
    ("0x7F8 keeps the old window",
     'blk = pages_between(c.p7f8, caught_up(c.p7f8, lambda d: block_name(d) == "disabled"), t_stop)',
     "blk = pages_between(c.p7f8, t_latch + PAGE_LAG_S, t_stop)"),
    ("any 0x7F8 page in the lag has caught up",
     'caught_up(c.p7f8, lambda d: block_name(d) == "disabled")', "caught_up(c.p7f8, lambda d: True)"),
    ("0x7F1 caught up by the 0x7F8 rule's reading",
     "caught_up(c.p7f1, lambda d: disable_name(d) == reason)", "caught_up(c.p7f1, lambda d: True)"),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-interlock_phase_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(os.path.join(d, "scratch"))
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, w = (old or "").replace("\n", nl), (new or "").replace("\n", nl)
        if old is not None and src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST), os.path.join(d, "scratch")],
                           cwd=root, env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:90]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
