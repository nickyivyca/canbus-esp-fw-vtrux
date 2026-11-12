"""Tests of the --can-fail control in test_bench_lock, test_run_core and
test_canmain (the harness tests the harness). No hardware.

    python -B shared/bench/tests/test_canfail_control.py            every test
    python -B shared/bench/tests/test_canfail_control.py --can-fail  every test
        on the unmutated files first (the control, which must pass), then
        again under each mutant of the three files' control, each of which
        must make a test FAIL

The rule checked (gen-inhibit reviewer, 2026-11-11, modelled on
test_scratch_helper and _ota_mutants): in --can-fail mode a test file runs
every selected test unmutated first, as the control; if any fails, it
counts no mutant and exits 1, since a test that already fails would count
every mutant of it as caught. Before this, none of the three ran a control.

Each case loads one of the three files in a process of its own, by its
path, with its TESTS and MUTANTS replaced by one made-up test and one
made-up mutant, and runs its main(["--can-fail"]):
  control_fails  the test fails unmutated: exit 1, REFUSED, no mutant applied
  caught         the test fails only under the mutant: exit 0, 1 of 1 caught
  survives       the test passes under the mutant too: exit 1
A mutant of a file is a text edit of its source made in that process before
it runs (an edit not found exactly once is NOT APPLIED, a failure). The
files' own folders (_scratch.py) are made and removed by those processes.
"""

import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["test_bench_lock.py", "test_run_core.py", "test_canmain.py"]
SCENARIOS = ["control_fails", "caught", "survives"]
MARK = "CANFAIL-CONTROL-APPLIED "


# ---- the driver: runs in its own process ------------------------------------
def driver(path, scenario, old, new):
    sys.path.insert(0, HERE)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if old is not None:
        if src.count(old) != 1:
            print("NOT APPLIED: the edit matched %d times" % src.count(old))
            return 3
        src = src.replace(old, new)
    mod = types.ModuleType(os.path.splitext(os.path.basename(path))[0])
    mod.__file__ = path
    sys.modules[mod.__name__] = mod
    exec(compile(src, path, "exec"), mod.__dict__)

    state = {"mutated": False, "applied": 0}

    def test_fake():
        if scenario == "control_fails" or (scenario == "caught" and state["mutated"]):
            raise AssertionError("made-up failure")

    def on():
        state["mutated"] = True
        state["applied"] += 1

    def off():
        state["mutated"] = False

    mod.TESTS = [test_fake]
    if os.path.basename(path) == "test_run_core.py":
        mod.MUTANTS = {"test_fake": (on, off)}
    else:
        mod.MUTANTS = {"test_fake": [("made-up mutant", (on, off))]}
    rc = mod.main(["--can-fail"])
    print(MARK + str(state["applied"]))
    sys.stdout.flush()
    return rc


# ---- the cases ----------------------------------------------------------------
def run_case(name, scenario, edit=None):
    old, new = edit if edit else (None, None)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--driver",
                        json.dumps([os.path.join(HERE, name), scenario, old, new])],
                       cwd=HERE, env=env, capture_output=True, text=True, timeout=600)
    applied = None
    for ln in r.stdout.splitlines():
        if ln.startswith(MARK):
            applied = int(ln[len(MARK):])
    out = r.stdout
    if r.returncode == 3:
        return "NOT APPLIED"
    if "Traceback" in r.stderr or applied is None:
        return "CRASH: exit %d, %s" % (r.returncode, (r.stderr.strip().splitlines() or ["?"])[-1][:160])
    if scenario == "control_fails":
        if r.returncode != 1 or "REFUSED: the control fails (test_fake)" not in out or applied:
            return ("a failing control was not refused: exit %d, %d mutant(s) applied, "
                    "REFUSED %s" % (r.returncode, applied, "printed" if "REFUSED" in out else "not printed"))
    elif scenario == "caught":
        if r.returncode != 0 or applied != 1 or "1 of 1 mutants caught" not in out \
                or "the control passes" not in out:
            return "a caught mutant with a passing control: exit %d, %d applied" % (
                r.returncode, applied)
    elif scenario == "survives":
        if r.returncode != 1 or applied != 1 or "STILL PASSES" not in out:
            return "a surviving mutant: exit %d, %d applied" % (r.returncode, applied)
    return None


CASES = [(f, s) for f in FILES for s in SCENARIOS]


def run_all(only=None, edit=None):
    """Every case, or only file only's with edit (old, new) made to it. Returns
    the failures."""
    bad = []
    for f, s in CASES:
        if only is not None and f != only:
            continue
        outcome = run_case(f, s, edit)
        label = "%s %s" % (f, s)
        print("%s  %-36s %s" % ("ok  " if outcome is None else "FAIL", label, outcome or ""),
              flush=True)
        if outcome is not None:
            bad.append(label)
    return bad


CONTROL_START = '    if can_fail:\n        print("-- control: every test, unmutated", flush=True)\n'
MUTANTS = []
for _f in FILES:
    MUTANTS.append(("%s: no control" % _f, _f,
                    CONTROL_START,
                    '    if False:\n        print("-- control: every test, unmutated", flush=True)\n'))
    MUTANTS.append(("%s: a failing control not refused" % _f, _f,
                    "        if control_failed:\n", "        if False:\n"))


def main(argv):
    if "--driver" in argv:
        path, scenario, old, new = json.loads(argv[argv.index("--driver") + 1])
        return driver(path, scenario, old, new)
    if "--can-fail" not in argv:
        bad = run_all()
        print("\n%d of %d passed" % (len(CASES) - len(bad), len(CASES)))
        return 1 if bad else 0
    print("-- control: the unmutated files", flush=True)
    bad = run_all()
    if bad:
        print("\nREFUSED: the control fails (%s), so a mutant's failures would mean "
              "nothing; no mutant is counted" % ", ".join(bad))
        return 1
    caught = 0
    for n, (label, f, old, new) in enumerate(MUTANTS, 1):
        with open(os.path.join(HERE, f), encoding="utf-8") as fh:
            k = fh.read().count(old)
        if k != 1:
            print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)" % (n, label, k))
            continue
        print("-- m%d %s" % (n, label), flush=True)
        bad = run_all(f, (old, new))
        caught += bool(bad)
        print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, label,
                                  ("fails " + ", ".join(bad)) if bad
                                  else "STILL PASSES -- nothing checks it"), flush=True)
    print("\n%d of %d mutants caught; the control passes" % (caught, len(MUTANTS)))
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
