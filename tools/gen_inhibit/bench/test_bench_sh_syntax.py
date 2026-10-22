#!/usr/bin/env python3
"""Harness test: every shell runner in this folder parses, by `bash -n` with the bash the
suite runs them with (settings "bash", Git Bash).

    GI_BASH="C:/Program Files/Git/usr/bin/bash.EXE" py -3.14 -B tools/gen_inhibit/bench/test_bench_sh_syntax.py

Written 2026-10-22: baseline_b_hand.sh had failed to parse since its NONODE step was added
(2026-10-20). An apostrophe in a "${VAR:?message}" opened a quote that ran to the end of the
file, so bash exited 2 before running any step, whichever step was asked for. Nothing ran it
between that edit and the move, and nothing parsed it. GI_BASH as in test_baseline_b_desk.py;
GI_SH_DIR checks another folder (the mutant runner, test_bench_sh_syntax_mutants.py).
Gen-inhibit tester.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SH_DIR = os.environ.get("GI_SH_DIR") or HERE
# Not a bare "bash": on Windows CreateProcess looks in System32 before PATH, which finds
# WSL's bash.exe; PATH's (Git Bash) is the one these scripts are written for.
BASH = os.environ.get("GI_BASH") or shutil.which("bash")
FAILS = []


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:160]))
    if not ok:
        FAILS.append(label)


def parse(path):
    """bash -n on one file: (exit code, first line of stderr)."""
    r = subprocess.run([BASH, "-n", path.replace("\\", "/")], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stderr.strip().splitlines() or [""])[0]


def main():
    if not BASH:
        check("a bash to run", False, "no GI_BASH and no bash on PATH")
        return 1
    v = subprocess.run([BASH, "--version"], capture_output=True, text=True, timeout=60).stdout.splitlines()
    check("bash is Git Bash, as the suite runs the scripts", bool(v) and "-pc-msys" in v[0], v[0] if v else "no output")
    # The check must be able to fail: the defect's own shape fails, the same line without it parses.
    tmp = tempfile.mkdtemp(prefix="gi-sh-syntax-")
    try:
        for name, line, want_fail in (
                ("apostrophe", ': "${Q:?set Q (man\'s sha)}"', True),
                ("none", ': "${Q:?set Q (man sha)}"', False)):
            p = os.path.join(tmp, name + ".sh")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("case $1 in\n  x) %s ;;\nesac\n" % line)
            rc, err = parse(p)
            check("control: %s in a ${VAR:?message} %s" % (name, "fails to parse" if want_fail else "parses"),
                  (rc != 0) == want_fail, "rc %d %s" % (rc, err))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    scripts = sorted(glob.glob(os.path.join(SH_DIR, "*.sh")))
    names = [os.path.basename(s) for s in scripts]
    check("the folder's runners are found (baseline_b_hand.sh among them)", "baseline_b_hand.sh" in names,
          "%d .sh in %s" % (len(names), SH_DIR))
    for s in scripts:
        rc, err = parse(s)
        check("%s parses" % os.path.basename(s), rc == 0, "rc %d %s" % (rc, err.replace(SH_DIR.replace("\\", "/"), ".")))
    print("\nFAIL: %d check(s) failed" % len(FAILS) if FAILS else "\nPASS (%d scripts)" % len(scripts))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
