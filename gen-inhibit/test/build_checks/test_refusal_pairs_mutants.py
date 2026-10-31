#!/usr/bin/env python3
"""Mutants of test_refusal_pairs.py's table-rule stage (table_rules, judge_table_rule,
and SLCAN's ROWS entry; known anomaly 8, gen-inhibit tester, 2026-10-30;
and run()'s guard for a switch with no row function, 2026-10-31);
test_refusal_tables.py must fail on each by a check (a FAIL line, not a crash).

    py -3.14 -B gen-inhibit/test/build_checks/test_refusal_pairs_mutants.py

Each mutant writes the edited test_refusal_pairs.py and a copy of test_refusal_tables.py
into a fresh folder in the worktree's scratch/, loads the edited module as
test_refusal_pairs first (build_output.py puts this folder at the front of sys.path
when imported, so an import by path order would find the real one: every mutant
survived that way on the first run), and runs the copy there, with this folder on
PYTHONPATH for build_output.py and build_switches.py. An edit that does not apply is an
error, not a kill. The unmutated copy runs first, as the control: if it
fails, no mutant is judged and the run exits 1. The stage's loops in main() are tested
on real images instead, by refusal_pairs_runs.py's negative runs.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SRC = "test_refusal_pairs.py"
TEST = "test_refusal_tables.py"
# the child: the edited module goes into sys.modules before the test imports it
BOOT = ("import importlib.util, runpy, sys\n"
        "s = importlib.util.spec_from_file_location('test_refusal_pairs', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(s)\n"
        "sys.modules['test_refusal_pairs'] = m\n"
        "s.loader.exec_module(m)\n"
        "sys.argv = sys.argv[2:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n")

MUTANTS = [
    ("a project_row rule judged as produced only (judge_rule's reading)",
     '        return ok is want, "row %r %s"', '        return ok is not None, "row %r %s"'),
    ("no reference counts as a pass", "    return ok is want, detail\n",
     "    return ok is want or ok is None, detail\n"),
    ("the rule's result ignored", "    return ok is want, detail\n", "    return ok is not None, detail\n"),
    ("a malformed or missing rule passes", '        return False, "no rule, or a malformed one',
     '        return True, "no rule, or a malformed one'),
    ("a switch with no rules is skipped",
     "            rules = e[3] if isinstance(e[3], list) and e[3] else [None]",
     "            rules = e[3] if isinstance(e[3], list) and e[3] else []"),
    ("only the first table that lists a switch is read",
     "            out.setdefault(s, []).extend((label, r) for r in rules)",
     "            out.setdefault(s, [(label, r) for r in rules])"),
    ("SLCAN left out of ROWS", '    "WICAN_SLCAN_COMMANDS": (None, (), False),\n', ""),
    ("a 'not a switch' entry proved too", "            if e[0] not in (BS.TEST, BS.STOCK):\n                continue\n",
     ""),
    ("run() calls a switch's missing row function (the first post-port run's crash)",
     "    if fn is None:\n        return {}\n    BO.rows.clear()", "    BO.rows.clear()"),
]


def main():
    base = os.path.join(WORKTREE, "scratch", "build-checks-tests", "refusal_pairs_mutants_%d" % os.getpid())
    killed = errors = 0
    for i, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % i)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, "-B", "-c", BOOT, os.path.join(d, SRC), os.path.join(d, TEST)],
                           cwd=d, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=HERE), timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fails), ", CRASHED" if crashed else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d%s%s)" % ("KILLED  " if dead else "SURVIVED", i, name, r.returncode,
                                           ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
