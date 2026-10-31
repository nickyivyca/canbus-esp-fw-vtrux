#!/usr/bin/env python3
"""Mutants of e4_device_calibration.py's arm wiring; test_e4_rawpost.py must fail on each.

    py -3.14 test_e4_rawpost_mutants.py

Each mutant copies e4_device_calibration.py and test_e4_rawpost.py into a fresh
folder in the worktree's scratch/, applies one edit, and runs the test there with
this folder on PYTHONPATH for the rest. A mutant whose edit does not apply is an
error, not a kill. Added 2026-10-16 with the fix to run_arm's arm/pre-read wiring.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1. A mutant is killed by a FAIL line with no crash; until then
any non-zero exit, a crash included, counted as a kill.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "e4_device_calibration.py"
TEST = "test_e4_rawpost.py"

MUTANTS = [
    ("arm POST's raw record dropped", '"post_http": poster.status, "post_raw": poster.raw,',
     '"post_http": poster.status, "post_raw": None,'),
    ("arm deferred whenever no raw timeout (the 2026-10-14 nesting)",
     'return ("now" if arm_after_s is None else "deferred"), post_raw_timeout is not None',
     'return ("now" if post_raw_timeout is not None else "deferred"), post_raw_timeout is not None'),
    ("arm-after ignored: always arms now",
     'return ("now" if arm_after_s is None else "deferred"), post_raw_timeout is not None',
     'return "now", post_raw_timeout is not None'),
    ("pre-read always made",
     "    if pre_read:\n",
     "    if True:\n"),
    ("pre-read never made",
     "    if pre_read:\n",
     "    if False:\n"),
    ("deferred branch builds no poster",
     "        poster = ModePoster(mode)\n        print(\"  arm: deferred",
     "        poster = None\n        print(\"  arm: deferred"),
]


def main():
    base = gi_paths.scratch("mutants-e4_rawpost_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for i, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % i)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, n = (old or "").replace("\n", nl), (new or "").replace("\n", nl)
        if old is not None and src.count(o) != 1:
            print("ERROR  m%d %s: edit matched %d times" % (i, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(o, n))
        shutil.copy(os.path.join(HERE, TEST), d)
        env = dict(os.environ, PYTHONPATH=HERE)
        r = subprocess.run([sys.executable, TEST], cwd=d, env=env, capture_output=True, text=True)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s  m0 %s  (exit %d, %d FAIL lines%s%s)" % (
                "PASS    " if ok else "FAILED  ", name, r.returncode, len(fails), ", CRASHED" if crashed else "",
                ("; first: " + fails[0][:90]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s  m%d %s  (exit %d, %d FAIL lines%s%s)" % (
            "KILLED  " if dead else "SURVIVED", i, name, r.returncode, len(fails), ", CRASHED" if crashed else "",
            ("; first: " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
