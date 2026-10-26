#!/usr/bin/env python3
"""Mutants of x2_flash_custody.py's dump-record path (2026-10-23); test_x2_flash_custody.py
must fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply is
an error.

    py -3.14 -B gen-inhibit/tools/bench/test_x2_flash_custody_mutants.py

Needs REVERSE_IT_DIR and INTERPOSER_CODE, as the test does. Each mutant is a copy of
x2_flash_custody.py with one edit, in the worktree's scratch/, imported by the test
through CUSTODY_DIR. A few seconds each. Gen-inhibit tester, 2026-10-23.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_flash_custody.py"
TEST = "test_x2_flash_custody.py"
MUTANTS = [
    ("dump: path recorded relative to bench/ again (a24de99)",
     'os.path.relpath(p, gi_paths.data_dir()).replace(', 'os.path.relpath(p, HERE).replace('),
    ("dump: path recorded absolute",
     'os.path.relpath(p, gi_paths.data_dir()).replace(', 'os.path.abspath(p).replace('),
    ("readers: the refusal dropped",
     '    if os.path.isabs(rel) or rel.startswith(("/", "\\\\")) or ".." in parts:\n',
     '    if False:\n'),
    ("check-dump: resolves the path itself, without the refusal",
     '    path = dump_path(a.dump_record, dr["dump"])\n', '    path = gi_paths.data_dir(dr["dump"])\n'),
    ("_checked_dump: resolves the path itself, without the refusal",
     '    p = dump_path(a.dump_record, d["dump"])\n', '    p = gi_paths.data_dir(d["dump"])\n'),
]


def main():
    base = gi_paths.scratch("mutants-custody_path")
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
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, CUSTODY_DIR=d), timeout=600)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        # killed only by a check line; a traceback with no FAIL line is a crash
        dead = bool(fails) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fails), ("; " + fails[0][:90]) if fails else ""))
        if not dead and r.stderr.strip():
            print("         stderr: %s" % r.stderr.strip().splitlines()[-1][:160])
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
