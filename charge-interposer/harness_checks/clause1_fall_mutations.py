"""Can-fail for clause 1's exemptions in run_scenario._hold_follows_loads
(spec 9: pack current above bcm_chg_max fails, except during an injected
load drop's walk-down (826925f4), within 2 s after a fall in bcm_chg_max
(75b154da), within 30 s of the hold's entry (05999682), and by one count
of bcm_ibat, 0.025 A (826925f4)). Tester, 2026-10-09; re-aimed 2026-10-18
(reviewer's ruling: one mutation per exemption, each flipping a named
case, plus one that narrows the one-count margin).

Each mutant is run_scenario.py with one source edit, loaded as a separate
module and put behind test_scorer_wiring's clause-1 cases
(hold_follows_loads_cases, p3_anchor_cases, p3_walk_down_cases) and this
script's own case below. Every mutant must fail the case named here; the
unmutated control must pass them all. Before any mutant runs, each edit is
confirmed to match exactly once in the source, so a mutant that silently
fails to apply is refused rather than counted as caught.

The exemption -> the mutants -> the test_scorer_wiring cases that already
pin it (kept: those cases are the scorer's own tests; this script shows
each of them can fail on a wrong exemption):

  walk-down (each down step within 0.3 s plus one page-01 frame of the
  drop or the step before it; it ends on the first hold or up frame,
  which is judged) -- p3_walk_down_cases:
    W1  a step must come within 0.3 s, without the extra frame
    W2  a step must come strictly within 0.3 s + one frame (exclusive)
    W3  a hold frame does not end the walk
    W4  an up step does not end the walk
  2 s after a fall in bcm_chg_max -- hold_follows_loads_cases:
    F1  no fall exemption
    F2  every 0x420 at or below the previous counts as a fall
    F3  the exemption runs 2 s either side of the fall
    F4  a rise counts as a fall
    F5  the exemption runs 3 s
  30 s entry grace, every frame in it exempt -- p3_anchor_cases (29 s /
  31 s) and, for "every frame", this script's own case:
    G1  no entry grace
    G2  the grace runs 32 s
    G3  only the first frame over in the grace is exempt
  one count of bcm_ibat, 0.025 A -- p3_walk_down_cases (+0.025 passes,
  +0.050 and +0.026 fail):
    C1  the margin narrowed to 0.024 A

--can-fail checks this script's own verdicts: a mutant that changes
nothing must read SURVIVED, an edit that does not match must be REFUSED,
and a mutant caught only on a case other than its named one must not
count as caught.

Usage (from anywhere; REVERSE_IT_DIR set):
    py -3.14 clause1_fall_mutations.py [--can-fail]
"""

import importlib.util
import io
import os
import sys
from contextlib import redirect_stdout

# charge-interposer/, found from this file (port step 0, 2026-10-26):
# paths.code_dir() refuses an INTERPOSER_CODE naming another tree.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths                                           # noqa: E402
CODE = paths.code_dir()

import test_scorer_wiring as W                       # noqa: E402

SRC = open(os.path.join(CODE, "run_scenario.py"), encoding="utf-8").read()

FALL_SPAN = "[(f, f + drop_exempt_s * 1000) for f in falls]"
FALL_TEST = "if b < a - tol]"
GRACE_IN = "if any(e <= t <= e + entry_grace_s * 1000 for e in entries):"
ENTRIES = ('entries = [h0 for h0, _h1 in series["hold"]] if _entry_grace '
           'else []')
ALLOW = "allow = IBAT_COUNT_A if _one_count else 0.0"

# name -> (old, new, a fragment of the case that must fail)
MUTANTS = {
    "W1 walk step within 0.3 s, no extra frame": (
        "WALK_STEP_MS = 300 + PAGE01_PERIOD_MS", "WALK_STEP_MS = 300",
        "a step exactly 0.3 s + one frame after the last"),
    "W2 walk step bound exclusive": (
        "if t - last > WALK_STEP_MS:", "if t - last >= WALK_STEP_MS:",
        "a step exactly 0.3 s + one frame after the last"),
    "W3 a hold frame does not end the walk": (
        'return (drop, t, "a hold frame")', "pass",
        "...ON the hold frame fails"),
    "W4 an up step does not end the walk": (
        'return (drop, t, "an up step")', "last = t",
        "...and on the up-step frame fails"),
    "F1 no fall exemption": (
        FALL_SPAN, FALL_SPAN.replace("in falls", "in []"),
        "1 s after chg_max FALLS to 0 A passes"),
    "F2 every frame at or below the previous is a fall": (
        FALL_TEST, "if b <= a + tol]",
        "the pack held above a fallen chg_max for 5 s fails"),
    "F3 fall exemption 2 s either side": (
        FALL_SPAN, FALL_SPAN.replace("(f, ", "(f - drop_exempt_s * 1000, "),
        "0.5 s BEFORE a fall fails"),
    "F4 a rise counts as a fall": (
        FALL_TEST, "if abs(b - a) > tol]",
        "1 s after a RISE in chg_max fails"),
    "F5 fall exemption 3 s": (
        FALL_SPAN, FALL_SPAN.replace("f + drop_exempt_s * 1000",
                                     "f + (drop_exempt_s + 1) * 1000"),
        "...2.5 s after that fall fails"),
    "G1 no entry grace": (
        ENTRIES, "entries = []",
        "above chg_max 29 s after the hold's entry is exempt"),
    "G2 entry grace 32 s": (
        GRACE_IN, GRACE_IN.replace("entry_grace_s * 1000",
                                   "(entry_grace_s + 2) * 1000"),
        "above chg_max 31 s after the hold's entry fails"),
    "G3 only the first frame in the grace is exempt": (
        GRACE_IN, GRACE_IN.replace("for e in entries):",
                                   "for e in entries) and n_grace == 0:"),
        "on EVERY frame from 1 s to 29 s after the hold's entry passes"),
    "C1 one-count margin narrowed to 0.024 A": (
        ALLOW, ALLOW.replace("IBAT_COUNT_A if", "(IBAT_COUNT_A - 0.001) if"),
        "pack current +0.025 A over chg_max 0 passes"),
}


def own_cases():
    """Clause 1's entry grace exempts EVERY frame within 30 s of the
    entry (spec 05999682, reading A), not only the first: the wiring
    cases put one frame there. The hold enters at 50 s; chg_max 2.0 A
    until C0; the pack 2.7 A on every second from 51 s to 79 s."""
    h0 = 50000
    ib = [(t, -0.1) for t in range(h0, 500001, 1000)] + \
        [(h0 + k * 1000, 2.7) for k in range(1, 30)]
    ok, why = W.R._hold_follows_loads(W._hs(ibat=ib), "")
    W.check(ok, "clause 1: above chg_max on EVERY frame from 1 s to 29 s "
            "after the hold's entry passes (all exempt) -> %s" % why)


def load(name, text):
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = os.path.join(CODE, "run_scenario.py")
    exec(compile(text, mod.__file__, "exec"), mod.__dict__)
    return mod


def run_cases(mod):
    W.R = mod
    W.failures = 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        W.hold_follows_loads_cases()
        W.p3_anchor_cases()
        W.p3_walk_down_cases()
        own_cases()
    out = buf.getvalue().splitlines()
    fails = [ln for ln in out if ln.startswith("FAIL")]
    n_ok = sum(1 for ln in out if ln.startswith("  ok  "))
    return W.failures, fails, n_ok


def refused(mutants):
    bad = []
    for name, (old, new, _want) in mutants.items():
        n = SRC.count(old)
        if n != 1 or old == new:
            bad.append("REFUSED %s: the edit matches %d time(s) in the "
                       "source" % (name, n))
    return bad


def judge(mutants):
    """-> {name: CAUGHT | caught elsewhere | SURVIVED}, printing each."""
    out = {}
    for name, (old, new, want) in mutants.items():
        nf, fails, _n = run_cases(load("rs_mut", SRC.replace(old, new)))
        named = [f for f in fails if want in f]
        out[name] = ("CAUGHT" if named else
                     "caught elsewhere" if nf else "SURVIVED")
        print("%s: %s -- %d failing check(s); named check %s"
              % (name, out[name], nf, "failed" if named else "did NOT fail"))
        for f in fails[:3]:
            print("    " + f[:200])
    return out


def control():
    nf, fails, n_ok = run_cases(load("rs_control", SRC))
    print("control: %d check(s) ok, %d failing%s" % (
        n_ok, nf, "" if not fails else ": " + fails[0][:150]))
    return nf == 0 and n_ok > 0


def can_fail():
    """This script's own verdicts must be able to come out wrong-side."""
    bad = 0
    noop = {"X0 changes nothing": (ALLOW, ALLOW + "  ",
                                   "pack current +0.025 A over chg_max 0 passes")}
    stale = {"X1 an edit that does not match": (
        "allow = IBAT_COUNT_A if _one_count else 0.5", "allow = 0.0",
        "pack current +0.025 A over chg_max 0 passes")}
    wrong_case = {"X2 caught, but not on the named case": (
        FALL_SPAN, FALL_SPAN.replace("in falls", "in []"),
        "above chg_max 31 s after the hold's entry fails")}
    r = refused(stale)
    print("can-fail X1: %s" % (r[0] if r else "NOT refused"))
    bad += not r
    if refused(noop) or refused(wrong_case):
        print("can-fail: X0 / X2 do not apply -- re-aim the can-fail")
        return 1
    v = judge(noop)
    print("can-fail X0: %s" % ("read SURVIVED, as it must"
                               if v["X0 changes nothing"] == "SURVIVED"
                               else "NOT read as SURVIVED"))
    bad += v["X0 changes nothing"] != "SURVIVED"
    v = judge(wrong_case)
    got = v["X2 caught, but not on the named case"]
    print("can-fail X2: %s" % ("not counted as caught (%s), as it must" % got
                               if got != "CAUGHT" else "counted as CAUGHT"))
    bad += got == "CAUGHT"
    print("can-fail result: %s" % ("the script's verdicts can fail" if not bad
                                   else "%d can-fail(s) not seen" % bad))
    return 1 if bad else 0


def main():
    if "--can-fail" in sys.argv[1:]:
        sys.exit(can_fail())
    r = refused(MUTANTS)
    for ln in r:
        print(ln)
    if r:
        sys.exit(2)
    if not control():
        sys.exit(1)
    v = judge(MUTANTS)
    bad = [n for n, x in v.items() if x != "CAUGHT"]
    print("result: %s" % ("all %d mutants caught on their named case"
                          % len(v) if not bad else
                          "%d mutant(s) not caught: %s" % (len(bad),
                                                           ", ".join(bad))))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
