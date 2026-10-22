#!/usr/bin/env python3
"""Mutants of e4_device_calibration.py's arm wiring; test_e4_rawpost.py must fail on each.

    py -3.14 test_e4_rawpost_mutants.py

Each mutant copies e4_device_calibration.py and test_e4_rawpost.py into a fresh
folder under runs/, applies one edit, and runs the test there with this folder
on PYTHONPATH for the rest. A mutant whose edit does not apply is an error, not
a kill. Added 2026-10-16 with the fix to run_arm's arm/pre-read wiring.
"""
import os
import shutil
import subprocess
import tempfile
import sys

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
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "e4_rawpost_mutants")   # outside SeaDrive (AGENTS.md)
    killed = errors = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % i)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if src.count(o) != 1:
            print("ERROR  m%d %s: edit matched %d times" % (i, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(o, n))
        shutil.copy(os.path.join(HERE, TEST), d)
        env = dict(os.environ, PYTHONPATH=HERE)
        r = subprocess.run([sys.executable, TEST], cwd=d, env=env, capture_output=True, text=True)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        dead = r.returncode != 0
        killed += dead
        print("%s  m%d %s  (exit %d, %d FAIL lines%s)" % (
            "KILLED  " if dead else "SURVIVED", i, name, r.returncode, len(fails),
            ("; first: " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
