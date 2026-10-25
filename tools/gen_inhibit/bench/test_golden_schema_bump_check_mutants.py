#!/usr/bin/env python3
"""Mutants of golden_schema_bump_check.py; its self-test must fail on each (exit 2 with
the self-test line FAIL). Each copy sits in this run's scratch/ folder with gi_diag.py,
gi_paths.py and pins.json beside it. Gen-inhibit tester, 2026-10-24 (toolkit item 10).

    py -3.14 -B test_golden_schema_bump_check_mutants.py
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "golden_schema_bump_check.py"
MUTANTS = [
    ("decoded differences ignored", "    if not eq:\n", "    if False:\n"),
    ("the old schema not checked", "    if any(v != old_ver for v in so):", "    if False:"),
    ("the new schema not checked", "    if any(v != new_ver for v in sn):", "    if False:"),
    ("an added or removed golden ignored", "    for n in sorted(set(old_files) ^ set(new_files)):", "    for n in []:"),
    ("a refusal passes", '        return ["refused: %s" % e]', "        return []"),
    ("phase 2 reads the wrong bit", '                if not int(p["diag_flags"]) & 0x02:', '                if not int(p["diag_flags"]) & 0x04:'),
]


def main():
    base = gi_paths.scratch("mutants-golden_schema_bump_check")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    gold = os.path.join(gi_paths.repo_root(), "test", "gen_inhibit_host", "golden")
    killed = errors = 0

    def run(name, text):
        d = os.path.join(base, name)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
        for f in ("gi_diag.py", "gi_paths.py", "pins.json"):
            shutil.copy(os.path.join(HERE, f), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TARGET), "--dirs", gold, gold,
                            "--old-ver", "7", "--new-ver", "7", "--repo", gi_paths.repo_root()], capture_output=True, text=True, cwd=d, timeout=600,
                           env=dict(os.environ, PYTHONPATH=""))
        st = next((l for l in r.stdout.splitlines() if l.startswith("self-test")), "")
        return r.returncode, st, r.stderr

    rc, st, err = run("control", src)
    control_ok = " PASS " in st
    print("%s control  (exit %d) %s" % ("PASS    " if control_ok else "FAILED  ", rc, (st or err[-200:])[:150]))
    for n, (label, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, label, src.count(old)))
            errors += 1
            continue
        rc, st, err = run("m%d" % n, src.replace(old, new))
        dead = rc == 2 and " FAIL " in st
        killed += dead
        print("%s m%d %s  (exit %d) %s" % ("KILLED  " if dead else "SURVIVED", n, label, rc, (st or err[-150:])[-110:]))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by the self-test, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
