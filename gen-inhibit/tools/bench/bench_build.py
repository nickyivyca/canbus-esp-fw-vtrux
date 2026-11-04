#!/usr/bin/env python3
"""A gen-inhibit bench or dev build: shared/tools R32's check-env through the project's
launcher, then ninja in gen-inhibit/firmware/build. From port step 1d every gen-inhibit
build passes the check (user ruling, 2026-11-03, through the gen-inhibit reviewer; the
route, through the launcher, the reviewer's, the same day).

    (from a shell exported for gen-inhibit's ESP-IDF, in any folder)
    py -3.14 -B gen-inhibit/tools/bench/bench_build.py [--jobs N]

Throttled (AGENTS.md "Builds during wall-clock tests", point 3): from a PowerShell set to
Idle priority, with --jobs 1. The processes it starts inherit the class.

check-env runs first, as `<this Python> -B gen-inhibit/vtrux_tools.py check-env`, with the
caller's environment as it is. If it exits non-zero, or cannot be started, ninja is never
run and this exits 3. Otherwise ninja runs as `ninja -C gen-inhibit/firmware/build`, with
`-j N` when --jobs is given and none without (ninja's own default), and this exits with
ninja's code (4 if ninja cannot be started). The build folder must already be configured,
as for a plain ninja build.

It does not do the checks AGENTS.md asks for before any build (bench_lock status, the
"No builds" line, the process list): those stay the caller's.

The launcher's check-env comes with port step 1d part B. The form above is the one part
B's launcher takes (b2f6fdcf on gen-inhibit/implementor/port-1d, run in a v5.4.2 shell
under py -3.14 from another folder, 2026-11-04).

Gen-inhibit tester, 2026-11-03 (port step 1d).
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))          # gen-inhibit/
LAUNCHER = os.path.join(PROJECT, "vtrux_tools.py")
BUILD = os.path.join(PROJECT, "firmware", "build")
NINJA = ("ninja",)
REFUSED = 3          # check-env refused, or could not be started: nothing built
NO_NINJA = 4         # check-env passed, ninja could not be started


def check_env_cmd(launcher=LAUNCHER):
    return [sys.executable, "-B", launcher, "check-env"]


def ninja_cmd(build=BUILD, jobs=None, ninja=NINJA):
    return list(ninja) + ["-C", build] + (["-j", str(jobs)] if jobs is not None else [])


def main(argv=None, launcher=LAUNCHER, build=BUILD, ninja=NINJA):
    ap = argparse.ArgumentParser(description="check-env through the launcher, then ninja")
    ap.add_argument("--jobs", type=int, help="ninja's -j (1 for the throttled form)")
    a = ap.parse_args(argv)
    if a.jobs is not None and a.jobs < 1:
        ap.error("--jobs must be 1 or more")

    cmd = check_env_cmd(launcher)
    print("bench_build: %s" % " ".join(cmd), flush=True)
    try:
        rc = subprocess.run(cmd).returncode
    except OSError as e:
        print("bench_build: check-env could not be started (%s); nothing built" % e, flush=True)
        return REFUSED
    if rc != 0:
        print("bench_build: check-env refused (exit %d); nothing built" % rc, flush=True)
        return REFUSED

    cmd = ninja_cmd(build, a.jobs, ninja)
    print("bench_build: %s" % " ".join(cmd), flush=True)
    try:
        return subprocess.run(cmd).returncode
    except OSError as e:
        print("bench_build: ninja could not be started (%s)" % e, flush=True)
        return NO_NINJA


if __name__ == "__main__":
    sys.exit(main())
