"""Can-fail for spec 9's declared --bypass exemption from the diag check
(spec c383868a834b6158, user 2026-10-10). Tester, 2026-10-10.

Replaces run_scenario._diag_check with three wrong rules in turn and runs
test_scorer_wiring.bypass_diag_cases() against each; every wrong rule must
fail at least one case, and the real rule must pass them all:

  name     exempt any scenario whose NAME contains "bypass"
  noframes exempt any scenario that showed no diag frames
  none     no exemption at all (spec 9 as it stood before 2026-10-10)

Usage (REVERSE_IT_DIR set):
    py -3.14 bypass_diag_mutations.py
"""

import contextlib
import io
import os
import sys

# charge-interposer/, found from this file (port step 0, 2026-10-26):
# paths.code_dir() refuses an INTERPOSER_CODE naming another tree.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths                                           # noqa: E402
CODE = paths.code_dir()

import run_scenario as R                               # noqa: E402
import test_scorer_wiring as W                         # noqa: E402

NA = (True, "not applicable")
WRONG = {
    "name": lambda sc, d, f: NA if "bypass" in sc.name
    else R._diag_frames_ok(d, f),
    "noframes": lambda sc, d, f: NA if not d else R._diag_frames_ok(d, f),
    "none": lambda sc, d, f: R._diag_frames_ok(d, f),
}


def run(rule):
    keep = R._diag_check
    R._diag_check = rule
    W.failures = 0
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            W.bypass_diag_cases()
    finally:
        R._diag_check = keep
    return [ln for ln in buf.getvalue().splitlines() if ln.startswith("FAIL")]


def main():
    bad = 0
    fails = run(R._diag_check)
    print("control (the real rule): %d failing case(s)" % len(fails))
    bad += bool(fails)
    for name, rule in WRONG.items():
        fails = run(rule)
        print("%-8s: %s, %d failing case(s)"
              % (name, "CAUGHT" if fails else "SURVIVED", len(fails)))
        for f in fails:
            print("    " + f[:160])
        bad += not fails
    print("result: %s" % ("every wrong rule caught, control clean" if not bad
                          else "%d problem(s)" % bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
