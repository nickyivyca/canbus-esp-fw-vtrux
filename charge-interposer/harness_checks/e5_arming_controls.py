"""E5 arming checks (spec 3 at 8a66ac01; test_trips rearm / teardown /
stale), shown able to fail. Tester, 2026-10-09.

Phase wrong: prints what each of test_trips' three wrapped wrong cores
('session', 'ageless', 'boundary') is reported for by each check -- the
detail test_arming_checks_can_fail only asserts.

Phase e4: runs the same three checks against a REAL earlier core, E4's
machine.py taken from git (default 856b698), loaded under the name test_trips
uses (test_trips.M swapped; protocol.py is the current one). E4 armed on a
12 seen earlier in the session (the point ruled on 2026-10-09), so the
re-arm check must report it; whatever E4 does in the other two is printed.
The E4 file's sha256 is printed and must differ from the current machine.py.

Usage (the code is the tree this file is in):
    py -3.14 e5_arming_controls.py [e4_sha]
"""

import hashlib
import importlib.util
import os
import subprocess
import sys

# charge-interposer/, found from this file (port step 0, 2026-10-26):
# paths.code_dir() refuses an INTERPOSER_CODE naming another tree.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths                                           # noqa: E402
CODE = paths.code_dir()

import test_trips as T                                 # noqa: E402

CHECKS = (("rearm", T.rearm_problems), ("teardown", T.teardown_problems),
          ("stale", T.stale_problems))


def phase_wrong():
    print("== wrapped wrong cores")
    for kind in ("session", "ageless", "boundary"):
        for name, fn in CHECKS:
            probs = fn(wrong=T._wrong_arming(kind))
            print("%-8s x %-8s: %d problem(s)" % (kind, name, len(probs)))
            for p in probs[:3]:
                print("    " + p)
    for name, fn in CHECKS:
        print("control %-8s: %s" % (name, fn() or "0 problems"))


def phase_e4(sha):
    print("== E4's core (%s:charge-interposer/machine.py)" % sha)
    repo = os.path.dirname(CODE)
    src = subprocess.check_output(
        ["git", "-C", repo, "show", "%s:charge-interposer/machine.py" % sha])
    cur = open(os.path.join(CODE, "machine.py"), "rb").read()
    print("sha256 E4 %s, current %s" % (hashlib.sha256(src).hexdigest()[:16],
                                        hashlib.sha256(cur).hexdigest()[:16]))
    if src == cur:
        sys.exit("REFUSED: E4's machine.py is the current one")
    spec = importlib.util.spec_from_loader("machine_e4", loader=None)
    m = importlib.util.module_from_spec(spec)
    m.__file__ = os.path.join(CODE, "machine.py")
    exec(compile(src.decode("utf-8"), m.__file__, "exec"), m.__dict__)
    keep = T.M
    T.M = m
    try:
        for name, fn in CHECKS:
            probs = fn()
            print("E4 x %-8s: %d problem(s)" % (name, len(probs)))
            for p in probs[:6]:
                print("    " + p)
    finally:
        T.M = keep


if __name__ == "__main__":
    phase_wrong()
    phase_e4(sys.argv[1] if len(sys.argv) > 1 else "856b698")
