#!/usr/bin/env python3
"""Mutants of the bench's shell runners; test_bench_sh_syntax.py must fail on each BY A
CHECK that names the mutated script. A crash is not a kill, and an edit that does not apply
is an error.

    GI_BASH="C:/Program Files/Git/usr/bin/bash.EXE" py -3.14 -B gen-inhibit/tools/bench/test_bench_sh_syntax_mutants.py

Each mutant is a copy of every .sh here in the worktree's scratch/, one edit applied to
one script, checked through GI_SH_DIR. Gen-inhibit tester, 2026-10-22.
"""
import glob
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = "test_bench_sh_syntax.py"
MUTANTS = [   # (name, script, old, new)
    ("the NONODE apostrophe back (the 2026-10-20 defect)", "baseline_b_hand.sh",
     "(wican_man elf_sha256)", "(wican_man's elf_sha256)"),
    ("the step case left open", "baseline_b_hand.sh", "  esac\ndone\n", "done\n"),
]


def main():
    base = gi_paths.scratch("mutants-bench_sh_syntax")
    killed = errors = 0
    for n, (name, script, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(HERE, script), encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for s in glob.glob(os.path.join(HERE, "*.sh")):
            shutil.copy(s, d)
        with open(os.path.join(d, script), "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, GI_SH_DIR=d), timeout=600)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
        named = [f for f in fails if f.startswith("FAIL  %s parses" % script)]
        dead = bool(named) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL)%s" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                               len(fails), ("; " + fails[0][:110]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
