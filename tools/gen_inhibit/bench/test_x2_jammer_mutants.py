#!/usr/bin/env python3
"""Mutants of x2_jammer.py's sub_id; test_x2_jammer.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B tools/gen_inhibit/bench/test_x2_jammer_mutants.py

Written 2026-10-20 for the empty line sub_id sends before ID (the 2026-10-19 chain run:
bench_identify's "V\r" "N\r" joined the ID line). Each mutant is a copy of the tool and
the test in the machine's temp folder, one edit applied. Gen-inhibit tester.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_jammer.py"
TEST = "test_x2_jammer.py"
MUTANTS = [
    ("no empty line before ID", '    con.send("")\n', ""),
    ("the empty line after ID", '    con.send("")\n    time.sleep(0.3)\n    t = time.time()\n    con.send("ID")\n',
     '    time.sleep(0.3)\n    t = time.time()\n    con.send("ID")\n    con.send("")\n'),
]
# Not a mutant: a space instead of the empty line. The firmware's sscanf("%11s") finds no
# word in " " either and ignores the line, so it behaves the same (equivalent).


def main():
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "x2_jammer")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
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
