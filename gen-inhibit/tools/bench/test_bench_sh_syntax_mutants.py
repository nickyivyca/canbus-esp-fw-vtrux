#!/usr/bin/env python3
"""Mutants of the bench's shell runners; test_bench_sh_syntax.py must fail on each BY A
CHECK that names the mutated script. A crash is not a kill, and an edit that does not apply
is an error.

    GI_BASH="C:/Program Files/Git/usr/bin/bash.EXE" py -3.14 -B gen-inhibit/tools/bench/test_bench_sh_syntax_mutants.py

Each mutant is a copy of every .sh here in the worktree's scratch/, one edit applied to
one script, checked through GI_SH_DIR. Gen-inhibit tester, 2026-10-22.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1.
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
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed
    for n, (name, script, old, new) in enumerate([("the control: the unmutated copy", None, None, None)] + MUTANTS):
        src = open(os.path.join(HERE, script), encoding="utf-8", newline="").read() if script else ""
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for s in glob.glob(os.path.join(HERE, "*.sh")):
            shutil.copy(s, d)
        if old is not None:
            with open(os.path.join(d, script), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, GI_SH_DIR=d), timeout=600)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
        if old is None:
            ok = r.returncode == 0 and not fails and "Traceback" not in r.stderr
            print("%s m0 %s  (exit %d, %d FAIL%s)%s" % ("PASS    " if ok else "FAILED  ", name, r.returncode, len(fails),
                                                     ", CRASHED" if "Traceback" in r.stderr else "",
                                                     ("; " + fails[0][:110]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        named = [f for f in fails if f.startswith("FAIL  %s parses" % script)]
        dead = bool(named) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL)%s" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                               len(fails), ("; " + fails[0][:110]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
