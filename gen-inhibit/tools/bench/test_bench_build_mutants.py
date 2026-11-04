#!/usr/bin/env python3
"""Mutants of bench_build.py; test_bench_build.py must fail on each BY A CHECK. A crash is
not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_bench_build_mutants.py

Each mutant is a copy in the worktree's scratch/, laid out as the tree is
(gen-inhibit/tools/bench/ with bench_build.py, the test and gi_paths.py, and
gen-inhibit/vtrux_tools.py, whose presence the test checks), one edit applied to
bench_build.py. The unmutated copy runs first, as the control: if it fails, no mutant is
judged and the run exits 1. A test run with no summary line crashed, which is not a kill
(the fake launcher's own traceback, in the crash case, is expected on stderr).

Gen-inhibit tester, 2026-11-03 (port step 1d).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = "test_bench_build.py"
TARGET = "bench_build.py"
BENCH_FILES = (TARGET, TEST, "gi_paths.py")
PROJECT_FILES = ("vtrux_tools.py",)
MUTANTS = [
    ("a refusal is ignored", "    if rc != 0:\n        print(\"bench_build: check-env refused",
     "    if False:\n        print(\"bench_build: check-env refused"),
    ("only exit 1 is a refusal", "    if rc != 0:\n        print(\"bench_build: check-env refused",
     "    if rc == 1:\n        print(\"bench_build: check-env refused"),
    ("a refusal exits 0", "        print(\"bench_build: check-env refused (exit %d); nothing built\" % rc, flush=True)\n"
     "        return REFUSED", "        print(\"bench_build: check-env refused (exit %d); nothing built\" % rc, flush=True)\n"
     "        return 0"),
    ("check-env is not waited for", "        rc = subprocess.run(cmd).returncode",
     "        rc = subprocess.Popen(cmd) and 0"),
    ("ninja runs before check-env", "    cmd = check_env_cmd(launcher)\n",
     "    subprocess.run(ninja_cmd(build, a.jobs, ninja))\n    cmd = check_env_cmd(launcher)\n"),
    ("the launcher is asked for check_env", '    return [sys.executable, "-B", launcher, "check-env"]',
     '    return [sys.executable, "-B", launcher, "check_env"]'),
    ("the launcher gets the jobs too", '    cmd = check_env_cmd(launcher)\n',
     '    cmd = check_env_cmd(launcher) + (["--jobs", str(a.jobs)] if a.jobs else [])\n'),
    ("check-env gets a default IDF_TOOLS_PATH", "        rc = subprocess.run(cmd).returncode",
     "        rc = subprocess.run(cmd, env=dict(os.environ, IDF_TOOLS_PATH=os.environ.get("
     "\"IDF_TOOLS_PATH\") or os.path.expanduser(\"~/.espressif\"))).returncode"),
    ("ninja's environment loses the planted names", "        return subprocess.run(cmd).returncode",
     "        return subprocess.run(cmd, env={k: v for k, v in os.environ.items() if k.startswith(('IDF_', 'PATH', "
     "'SYSTEMROOT', 'BB_F', 'BB_N'))}).returncode"),
    ("-j dropped", '(["-j", str(jobs)] if jobs is not None else [])', '[]'),
    ("-j always", '(["-j", str(jobs)] if jobs is not None else [])', '["-j", str(jobs or 1)]'),
    ("-j 1 whatever --jobs says", '(["-j", str(jobs)] if jobs is not None else [])',
     '(["-j", "1"] if jobs is not None else [])'),
    ("the build folder is firmware/", 'BUILD = os.path.join(PROJECT, "firmware", "build")',
     'BUILD = os.path.join(PROJECT, "firmware")'),
    ("the launcher is the pinned tooling's, not the project's", 'LAUNCHER = os.path.join(PROJECT, "vtrux_tools.py")',
     'LAUNCHER = os.path.join(PROJECT, "..", "shared", "tools", "vtrux_build", "__main__.py")'),
    ("ninja's code is not returned", "        return subprocess.run(cmd).returncode",
     "        subprocess.run(cmd)\n        return 0"),
    ("ninja not started reads as built", "        return NO_NINJA", "        return 0"),
    ("--jobs 0 taken", "    if a.jobs is not None and a.jobs < 1:", "    if a.jobs is not None and a.jobs < 0:"),
]


def main():
    base = gi_paths.scratch("mutants-bench_build")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in BENCH_FILES}
    proj = os.path.dirname(os.path.dirname(HERE))
    psrcs = {f: open(os.path.join(proj, f), encoding="utf-8", newline="").read() for f in PROJECT_FILES}
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and srcs[TARGET].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[TARGET].count(old), TARGET))
            errors += 1
            continue
        top = os.path.join(base, "m%d" % n, "gen-inhibit")
        bench = os.path.join(top, "tools", "bench")
        os.makedirs(bench)
        for f, s in srcs.items():
            with open(os.path.join(bench, f), "w", encoding="utf-8", newline="") as fh:
                fh.write(s.replace(old, new) if (f == TARGET and old is not None) else s)
        for f, s in psrcs.items():
            with open(os.path.join(top, f), "w", encoding="utf-8", newline="") as fh:
                fh.write(s)
        r = subprocess.run([sys.executable, "-B", os.path.join(bench, TEST)], cwd=bench,
                           capture_output=True, text=True)
        fail_lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        # The fake launcher's own traceback (the crash case) reaches stderr by design, so a
        # crash of the test is judged by its missing summary line instead.
        crashed = re.search(r"^(PASS|FAIL), \d+ ok, \d+ FAIL$", r.stdout, re.M) is None
        if old is None:
            ok = r.returncode == 0 and not fail_lines and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                     len(fail_lines), ", CRASHED" if crashed else ""))
            if not ok:
                print(r.stdout[-2000:] + r.stderr[-2000:])
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fail_lines) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fail_lines),
            ", CRASHED" if crashed else "", ("; " + fail_lines[0][:100]) if fail_lines else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
