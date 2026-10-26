#!/usr/bin/env python3
"""Mutants of x2_pins_noreset_check.py; test_x2_pins_noreset_check.py must fail on
each BY A CHECK. A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_x2_pins_noreset_check_mutants.py

Each mutant is a copy of the tool and the test in the worktree's scratch/, one edit
applied. Version 2 of the tool. Gen-inhibit tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_pins_noreset_check.py"
TEST = "test_x2_pins_noreset_check.py"
MUTANTS = [
    ("no control: a deaf witness passes",
     '    if missing:\n        return "VOID"', '    if False:\n        return "VOID"'),
    ("the control needs only one round frame", "missing = [i for i in ROUND if i not in control]",
     "missing = [] if any(i in ROUND for i in control) else list(ROUND)"),
    ("the control is judged after the round", "    if missing:\n",
     "    if missing and not after:\n"),
    ("the quiet window is not looked at", "    if any(i in ROUND for i in quiet):", "    if False:"),
    ("no serial read before is needed", "    if not diag_b or not t_b:", "    if False:"),
    ("the diag line before is not needed", "    if not diag_b or not t_b:", "    if not t_b:"),
    ("a round after is not looked at",
     '    if seen:\n        return "FAIL"', '    if False:\n        return "FAIL"'),
    ("only 0x7F4 after counts", "seen = sorted({i for i in after if i in ROUND})",
     "seen = sorted({i for i in after if i == 0x7F4})"),
    ("silence on serial after passes",
     '    if not diag_a or not t_a:\n        return "FAIL"', '    if False:\n        return "FAIL"'),
    ("the diag line after is not needed",
     '    if not diag_a or not t_a:\n        return "FAIL"', '    if not t_a:\n        return "FAIL"'),
    ("t= going back is not looked at", "    if min(t_a) < t_b[-1]:", "    if False:"),
    ("only the last t= after is looked at", "    if min(t_a) < t_b[-1]:", "    if t_a[-1] < t_b[-1]:"),
    ("the first t= before is the reference", "    if min(t_a) < t_b[-1]:", "    if min(t_a) < t_b[0]:"),
    ("a drop of one second passes", "    if min(t_a) < t_b[-1]:", "    if min(t_a) < t_b[-1] - 1:"),
    ("diag matches inside a word", 'DIAG_RX = re.compile(r"\\bdiag st=")', 'DIAG_RX = re.compile(r"diag st=")'),
    ("t= without its unit", 'T_RX = re.compile(r"\\bt=(\\d+)s\\b")', 'T_RX = re.compile(r"\\bt=(\\d+)")'),
    ("t= inside a word", 'T_RX = re.compile(r"\\bt=(\\d+)s\\b")', 'T_RX = re.compile(r"t=(\\d+)s\\b")'),
    ("the after window starts at the end of the pin read",
     'between("pins", "serial_after")', 'between("after", "serial_after")'),
    ("the quiet window runs into the pin read", 'between("quiet", "pins")', 'between("quiet", "after")'),
    ("the control window starts after esptool", 'between("control", "end")', 'between("control_after", "end")'),
    ("a banner before is ignored", '    if boot_b:\n        return "INCONCLUSIVE"',
     '    if False:\n        return "INCONCLUSIVE"'),
    ("a banner after is ignored", '    if boot_a:\n        return "FAIL"', '    if False:\n        return "FAIL"'),
    ("only the rst: banner counts", 'r"rst:0x[0-9a-fA-F]+ \\(|Vtrux charge interposer"', 'r"rst:0x[0-9a-fA-F]+ \\("'),
    ("only the name banner counts", 'r"rst:0x[0-9a-fA-F]+ \\(|Vtrux charge interposer"', 'r"Vtrux charge interposer"'),
    ("any 0x before a paren counts", 'r"rst:0x[0-9a-fA-F]+ \\(|Vtrux charge interposer"',
     'r"0x[0-9a-fA-F]+ \\(|Vtrux charge interposer"'),
]
# The lock check moved to bench_held.py (2026-10-25); its mutants are test_bench_held_mutants.py.


def main():
    base = gi_paths.scratch("mutants-x2_pins_noreset")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        shutil.copy(os.path.join(HERE, "gi_paths.py"), d)   # the moved tool imports it
        shutil.copy(os.path.join(HERE, "bench_held.py"), d)   # and its lock check
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        killed += bool(fails)
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if fails else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "",
            ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
