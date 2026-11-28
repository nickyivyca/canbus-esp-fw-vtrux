#!/usr/bin/env python3
"""The mutant runner the analysis tests share: each edit in a test's MUTANTS is applied to a copy
of the module under test, and the test is run on the copy. Charge-interposer tester, 2026-11-28,
from the runner gen-inhibit's test_unacked_analysis.py and test_unacked_bench.py had at
gen-inhibit/main 74555175, with its scratch folder and its removal made shared/twai_port's own.

A mutant is KILLED when the test prints a "FAIL " line and raised no Traceback. The run passes
only if the unmutated module passes the test, every edit's text occurs exactly once in the module,
and every mutant is killed.

The copies go in this run's own folder, scratch/twai_port-analysis-mutants/<time>_<pid>/ at the
worktree's root, removed when the run ends, pass or fail (AGENTS.md "Removing scratch"). The test
finds the copy through the environment variable it names (`env_var`): it puts that folder first
on sys.path.
"""
import os
import shutil
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# the worktree's root; a run hands its own to the tests it runs, so a copy of this file in scratch/
# (test_mutants.py's mutants) puts its folders under the same root
ROOT = os.environ.get("TWAI_PORT_ANALYSIS_ROOT") or os.path.normpath(os.path.join(HERE, "..", "..", ".."))


def long_path(p):
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def remove_tree(p):
    """Remove `p`, clearing read-only flags and retrying, and say so when it can't (AGENTS.md
    "Removing scratch": never ignore_errors=True)."""
    def retry(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError as e:
            print("could not remove %s: %s" % (path, e))
    if os.path.lexists(p):
        if sys.version_info >= (3, 12):
            shutil.rmtree(long_path(p), onexc=retry)
        else:
            shutil.rmtree(long_path(p), onerror=retry)
    if os.path.lexists(p):
        print("LEFT BEHIND: %s" % p)
        return False
    return True


def run(test_file, module, mutants, env_var, beside=(), src_dir=HERE):
    """Run `test_file` on each mutant of `module` (a file name in `src_dir`, this folder unless
    test_mutants.py gives another). `beside`: the other modules of `src_dir` the copy needs next to
    it. The control runs the test on `src_dir` itself. Returns the exit code: 0 when the control
    passes and every mutant is killed, 1 otherwise."""
    src = open(os.path.join(src_dir, module), encoding="utf-8", newline="").read()
    if subprocess.run([sys.executable, "-B", test_file], capture_output=True,
                      env=dict(os.environ, **{env_var: src_dir, "PYTHONDONTWRITEBYTECODE": "1"})).returncode != 0:
        print("ERROR    control: the unmutated tool fails this test")
        return 1
    print("PASS     control: the unmutated tool passes this test")
    base = os.path.join(ROOT, "scratch", "twai_port-analysis-mutants",
                        "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(mutants, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, module), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
            for b in beside:
                shutil.copy2(os.path.join(src_dir, b), d)
            r = subprocess.run([sys.executable, "-B", test_file], capture_output=True, text=True,
                               env=dict(os.environ, **{env_var: d, "PYTHONDONTWRITEBYTECODE": "1",
                                                       "TWAI_PORT_ANALYSIS_ROOT": ROOT}))
            failed = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            dead = bool(failed) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "  (%s)" % failed[0][:150] if failed else ""))
    finally:
        remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(mutants), errors))
    return 0 if killed == len(mutants) and not errors else 1
