#!/usr/bin/env python3
"""Mutants of test_only_inert.witness_listening (the PCAN marker control,
2026-10-16); test_test_only_inert.py must fail on each BY A CHECK. A crash is
not a kill, and an edit that does not apply is an error. The unmutated copy runs
first, as the control (2026-10-30): if it fails, no mutant is judged and the run
exits 1.

    py -3.14 gen-inhibit/tools/bench/test_test_only_inert_marker_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH with this folder.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "test_only_inert.py"
TEST = "test_test_only_inert.py"

MUTANTS = [
    ("any frame counts (the old rule)",
     "    got = {d for _t, i, d in seen if i == MARKER_ID}\n    return all(m in got for m in MARKERS)",
     "    return len(seen) > 0"),
    ("one marker is enough", "return all(m in got for m in MARKERS)", "return any(m in got for m in MARKERS)"),
    ("the id is ignored", "got = {d for _t, i, d in seen if i == MARKER_ID}", "got = {d for _t, i, d in seen}"),
    ("the payload is ignored", "return all(m in got for m in MARKERS)",
     "return len(got) >= 1 and len([1 for _t, i, _d in seen if i == MARKER_ID]) >= 2"),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-inert_marker_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        if old is not None:
            o, w = old.replace("\n", nl), new.replace("\n", nl)
            if src.count(o) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
                errors += 1
                continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=root,
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:80]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
