"""The OTA flasher's tests' mutant runs (FLASH-SPEC O13), in gen-inhibit's
style (its test_bench_flash_*_mutants.py at gen-inhibit/main 40ecfa5e).

    python -B shared/bench/tests/test_ota_<x>.py --can-fail

The unmutated copy runs first, as the control: if it does not pass, no mutant
is judged and the run fails. Then each mutant: a copy of the vtrux_bench
package in this run's own scratch folder (_scratch.py), one source edit in
it, which must match exactly once (else ERROR, and the run fails), and the
test run again in a process of its own against that copy (the environment
variable OTA_TEST_PKG names the folder holding it). A mutant is killed only
by a FAIL line: a crash is not a kill (a Traceback on stderr, or a FAIL whose
detail says CRASH). Every mutant must be killed. Each mutant's copy is removed
after its run; the run folder goes when this process exits.
"""

import os
import shutil
import subprocess
import sys

import _scratch

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "vtrux_bench")
ENV_PKG = "OTA_TEST_PKG"


def package_root():
    """Where the test imports vtrux_bench from: a mutant's copy, or this tree."""
    return os.environ.get(ENV_PKG) or os.path.dirname(HERE)


def run_test(test_file, root, extra=()):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env[ENV_PKG] = root
    r = subprocess.run([sys.executable, "-B", test_file] + list(extra), cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=900)
    fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
    real = [ln for ln in fails if "CRASH" not in ln]
    crashed = "Traceback" in r.stderr
    return r, fails, real, crashed


def run_mutants(test_file, mutants, name):
    """mutants: [(label, file under vtrux_bench/, old, new)]. Returns the exit code."""
    base = _scratch.run_dir("%s-mutants" % name)
    killed = errors = 0
    for n, mut in enumerate([("the control: the unmutated copy", None, None, None)] + list(mutants)):
        label, rel, old, new = mut
        d = os.path.join(base, "m%d" % n)
        shutil.copytree(PKG, os.path.join(d, "vtrux_bench"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        if rel is not None:
            path = os.path.join(d, "vtrux_bench", rel)
            with open(path, encoding="utf-8", newline="") as f:
                src = f.read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times in %s" % (n, label, src.count(old), rel))
                errors += 1
                _scratch.remove_tree(d)
                continue
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
        r, fails, real, crashed = run_test(test_file, d)
        _scratch.remove_tree(d)
        if rel is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % (
                "PASS    " if ok else "FAILED  ", label, r.returncode, len(fails),
                ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
            sys.stdout.flush()
            if not ok:
                print(r.stderr[-1500:])
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(real) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, label, r.returncode, len(fails),
            ", CRASHED: " + r.stderr.strip().splitlines()[-1][:100] if crashed else "",
            ("; " + (real or fails)[0][:90]) if fails else ""))
        sys.stdout.flush()
    print("\n%d of %d killed by a check, %d errors; the control passes"
          % (killed, len(mutants), errors))
    return 0 if killed == len(mutants) and not errors else 1
