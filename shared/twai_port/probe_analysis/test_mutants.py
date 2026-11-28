#!/usr/bin/env python3
"""Test of mutants.py, the analysis tests' mutant runner, on unacked_analysis.py and its test: a
mutant a check catches is killed and the run passes; a mutant no check catches survives and the run
fails; an edit that matches no text, or more than once, fails the run; a control that fails stops
the run; a mutant that makes the test raise is not counted killed, even after a FAIL line; and each
run removes its scratch folder. Charge-interposer tester, 2026-11-28.

    python3.13 -B test_mutants.py [--mutants]
--mutants runs mutants.py's own mutants through mutants.py: each must fail a check here.
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("MUTANTS_DIR") or HERE)
import mutants as MR  # noqa: E402

TEST = os.path.join(HERE, "test_unacked_analysis.py")
MOD = "unacked_analysis.py"
ENV = "UNACKED_ANALYSIS_DIR"
SCRATCH = os.path.join(MR.ROOT, "scratch", "twai_port-analysis-mutants")
fails = []
oks = 0

KILLS = ("the passive mark at 127", "PASSIVE_TEC = 128\n", "PASSIVE_TEC = 127\n")
NO_OP = ("a comment added", "PASSIVE_TEC = 128\n", "PASSIVE_TEC = 128  # the same\n")
NO_MATCH = ("no such text", "PASSIVE_TEC = 129\n", "PASSIVE_TEC = 127\n")
TWICE = ("an edit that matches more than once", "    return out\n", "    return None\n")
# span_stats() gives {}: the M1 table's check prints FAIL, then a later check's ["n"] raises
RAISES = ("FAIL then a Traceback", '    xs = sorted(xs)\n    return {"n": len(xs)', '    return {}\n    return {"n": len(xs)')


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[-300:]))


def dirs():
    return set(os.listdir(SCRATCH)) if os.path.isdir(SCRATCH) else set()


def runner(mutants, src_dir=HERE):
    """(exit code, output, the run folders it left in scratch). A folder it left is then removed here,
    so a runner that keeps its folder leaves nothing behind this test."""
    before = dirs()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = MR.run(TEST, MOD, mutants, ENV, src_dir=src_dir)
    except Exception as e:      # noqa: BLE001  a check fails on it, not the test
        rc = "raised %s: %s" % (type(e).__name__, e)
    left = dirs() - before
    for d in left:
        MR.remove_tree(os.path.join(SCRATCH, d))
    return rc, buf.getvalue(), left


def main():
    rc, out, left = runner([KILLS])
    check("a mutant a check catches: killed, the run passes", rc == 0 and "KILLED   m1" in out, (rc, out))
    check("its scratch folder removed", not left, left)
    rc, out, left = runner([KILLS, NO_OP])
    check("a mutant no check catches: survives, the run fails", rc == 1 and "SURVIVED m2" in out, (rc, out))
    check("its scratch folder removed on a failed run too", not left, left)
    rc, out, _ = runner([KILLS, NO_MATCH])
    check("an edit that matches no text fails the run", rc == 1 and "matched 0 times" in out, (rc, out))
    rc, out, _ = runner([KILLS, TWICE])
    check("an edit that matches more than once fails the run", rc == 1 and "ERROR    m2" in out, (rc, out))
    rc, out, _ = runner([KILLS, RAISES])
    check("a mutant whose test raises after a FAIL is not killed", rc == 1 and "SURVIVED m2" in out, (rc, out))
    broken = os.path.join(MR.ROOT, "scratch", "twai_port-analysis-mutants-test", "%d" % os.getpid())
    try:
        os.makedirs(broken, exist_ok=True)
        src = open(os.path.join(HERE, MOD), encoding="utf-8", newline="").read()
        with open(os.path.join(broken, MOD), "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(KILLS[1], KILLS[2]))
        rc, out, _ = runner([KILLS], src_dir=broken)
        check("a control that fails stops the run", rc == 1 and "ERROR    control" in out and "m1" not in out,
              (rc, out))
    finally:
        if not MR.remove_tree(broken):
            check("the broken copy's folder removed", False, broken)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("a survivor counted as killed", '            dead = bool(failed) and "Traceback" not in r.stderr\n',
     "            dead = True\n"),
    ("a Traceback counted as killed", '            dead = bool(failed) and "Traceback" not in r.stderr\n',
     "            dead = bool(failed)\n"),
    ("the control not run", "                      env=dict(os.environ, **{env_var: src_dir, \"PYTHONDONTWRITEBYTECODE\": \"1\"})).returncode != 0:\n",
     "                      env=dict(os.environ, **{env_var: src_dir, \"PYTHONDONTWRITEBYTECODE\": \"1\"})).returncode != 0 and False:\n"),
    ("an edit that matches twice applied", "            if src.count(old) != 1:\n", "            if src.count(old) == 0:\n"),
    ("the scratch folder kept", "        remove_tree(base)\n", "        pass\n"),
    ("a survivor passes the run", "    return 0 if killed == len(mutants) and not errors else 1\n",
     "    return 0 if not errors else 1\n"),
]


def mutants():
    sys.path.insert(0, HERE)
    import mutants as MR2
    return MR2.run(os.path.abspath(__file__), "mutants.py", MUTANTS, "MUTANTS_DIR")


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
