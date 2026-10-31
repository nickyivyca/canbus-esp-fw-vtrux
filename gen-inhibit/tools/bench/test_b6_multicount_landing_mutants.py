#!/usr/bin/env python3
"""Mutants of b6_multicount_landing.py; test_b6_multicount_landing.py must fail on each
BY A CHECK. A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_b6_multicount_landing_mutants.py

Each mutant is a copy of the script in the worktree's scratch/, one edit applied, run
by the test through LANDING_SCRIPT. Gen-inhibit tester, 2026-10-20.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1. A FAIL line the test writes for a crash it caught ("ran
without raising") kills nothing; since 2026-10-30 nor does any FAIL line beside a
traceback.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "b6_multicount_landing.py"
TEST = "test_b6_multicount_landing.py"
MUTANTS = [
    ("the frame before is the first frame, not the last",
     "        if f[0] < t:\n            before = f\n", "        if f[0] < t:\n            before = before or f\n"),
    ("the frame after is the last frame",
     "        elif f[0] > t:\n            after = f\n            break\n", "        elif f[0] > t:\n            after = f\n"),
    ("a frame at the error stamp is the next frame (the tie reading)", "        elif f[0] > t:\n", "        elif f[0] >= t:\n"),
    ("the multi-count bound raised", "MULTI = 5\n", "MULTI = 12\n"),
    ("the pulse time is the cluster's last error frame", "e = cl[i][0] if i < len(cl) else None",
     "e = cl[i][-1] + 0.5 if i < len(cl) else None"),
    ("bus_error deltas from the TEC", 'be = [(s[0], s[1].get("drv_bus_error")) for s in samples]',
     'be = [(s[0], s[1].get("drv_tec")) for s in samples]'),
    ("a missing witness file reads as no frames",
     '    if w is None:\n        return [("no witness file", path)]\n',
     '    if w is None:\n        w = {"frames": [], "errs": []}\n'),
]


def main():
    base = gi_paths.scratch("mutants-b6_landing")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, SRC)
        open(path, "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, LANDING_SCRIPT=path))
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = (bool(fails) and not all("ran without raising" in f for f in fails)
                and "Traceback" not in r.stderr)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
