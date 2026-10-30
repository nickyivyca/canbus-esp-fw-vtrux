#!/usr/bin/env python3
"""Mutants of e4_device_calibration.py's attribution check (2026-10-29, gen-inhibit
reviewer: the old trail-window count voids no arm, provenance decides);
test_e4_stimulus_split.py must fail on each BY A CHECK. A crash is not a kill, and an
edit that does not apply is an error. The unmutated copy runs first as the control.

    py -3.14 -B gen-inhibit/tools/bench/test_e4_attribution_mutants.py

Each mutant is a copy of e4_device_calibration.py in the worktree's scratch/, one edit
applied, run through E4_DIR. About 5 s a mutant. Gen-inhibit tester, 2026-10-29.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = os.path.join(HERE, "test_e4_stimulus_split.py")
TARGET = "e4_device_calibration.py"
MUTANTS = [
    (None, None, None),
    ("the superseded count still voids, as before 2026-10-29",
     "    if mode != MODE_RESPOND:\n", "    if False:\n"),
    ("a RESPOND probe disagreement voids nothing",
     "    if mode != MODE_RESPOND:\n", "    if True:\n"),
    ("an agreeing count is read as a disagreement",
     "    if ours_on_wire == tx_ok:\n", "    if False:\n"),
    ("run_arm drops the check's void reason",
     "        if reason:\n            void_reasons.append(reason)\n", "        if reason:\n            pass\n"),
]


def main():
    base = gi_paths.scratch("mutants-e4_attribution")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, (name, old, new) in enumerate(MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(
            src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, E4_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
