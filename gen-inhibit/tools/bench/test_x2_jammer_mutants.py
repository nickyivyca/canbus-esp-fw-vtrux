#!/usr/bin/env python3
"""Mutants of x2_jammer.py's sub_id; test_x2_jammer.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B tools/gen_inhibit/bench/test_x2_jammer_mutants.py

Written 2026-10-20 for the empty line sub_id sends before ID (the 2026-10-19 chain run:
bench_identify's "V\r" "N\r" joined the ID line). Each mutant is a copy of the tool and
the test in the worktree's scratch/, one edit applied, with x2_jammer/platformio.ini beside
them where the test looks for it (one folder up). The control, the unmutated copy, must
pass with no crash first (2026-10-24: without the ini every copy crashed after its first
failing check, so the later checks never ran). Gen-inhibit tester.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_jammer.py"
TEST = "test_x2_jammer.py"
INI = os.path.join(os.path.dirname(HERE), "x2_jammer", "platformio.ini")   # the test reads it
MUTANTS = [
    ("no empty line before ID", '    con.send("")\n', ""),
    ("the empty line after ID", '    con.send("")\n    time.sleep(0.3)\n    t = time.time()\n    con.send("ID")\n',
     '    time.sleep(0.3)\n    t = time.time()\n    con.send("ID")\n    con.send("")\n'),
]
# Not a mutant: a space instead of the empty line. The firmware's sscanf("%11s") finds no
# word in " " either and ignores the line, so it behaves the same (equivalent).


def run(base, label, text):
    """A copy of the tool (text) and the test in base/label; the test's FAIL lines, crash, exit."""
    d = os.path.join(base, label)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(text)
    shutil.copy(os.path.join(HERE, TEST), d)
    shutil.copy(os.path.join(HERE, "gi_paths.py"), d)   # the moved tool imports it
    r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
    fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
    crash = r.stderr.strip().splitlines()[-1][:120] if "Traceback" in r.stderr else ""
    return fails, crash, r.returncode


def main():
    base = gi_paths.scratch("mutants-x2_jammer")
    os.makedirs(os.path.join(base, "x2_jammer"))
    shutil.copy(INI, os.path.join(base, "x2_jammer"))
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    fails, crash, rc = run(base, "control", src)
    control_ok = rc == 0 and not fails and not crash
    print("%s control: the unmutated copy  (exit %d, %d FAIL%s)" % (
        "PASS    " if control_ok else "FAILED  ", rc, len(fails), ", CRASHED: " + crash if crash else ""))
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        fails, crash, rc = run(base, "m%d" % n, src.replace(old, new))
        killed += bool(fails)
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if fails else "SURVIVED", n, name, rc, len(fails),
            ", CRASHED: " + crash if crash else "",
            ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
