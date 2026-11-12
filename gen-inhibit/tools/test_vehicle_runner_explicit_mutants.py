#!/usr/bin/env python3
"""Mutants of vehicle_runner.py's explicit-open fix; test_vehicle_runner_root.py's
ExplicitOpen class must fail on each BY A CHECK (an assertion), not by an error.

    python3.13 -B gen-inhibit/tools/test_vehicle_runner_explicit_mutants.py

On Linux an explicit --interface kvaser or gs_usb opens with python-can's can.Bus, which
fails there without can-tools' linux_fixes; vehicle_runner applies the pinned canbench's
linux_fixes just before that open (the implementor's b592e348, gen-inhibit/main a553e9ec;
the reviewer asked for this runner, 2026-11-12). The implementor tried three mutants by hand
(no apply, apply after the open, platform check removed); they are committed here with two
more (interface check removed, the fixes loaded but not applied).

Each mutant is a copy of this folder (gen-inhibit/tools, the test's imports and its
fixtures beside it) in the worktree's scratch/, one edit applied to vehicle_runner.py, and
`python -B test_vehicle_runner_root.py ExplicitOpen` run there. The unmutated copy runs
first as the control: if it does not pass, no mutant is judged and the run exits 1. A
mutant whose edit does not match exactly once is an error. Gen-inhibit tester, 2026-11-12.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402

TARGET = "vehicle_runner.py"
TEST = "test_vehicle_runner_root.py"
CALL = "            apply_linux_fixes(a.interface)      # before the open, or no-op\n"
OPEN = "            self.bus = can.Bus(**kw)\n"
GATE = '    if interface not in LINUX_FIX_INTERFACES or not sys.platform.startswith("linux"):\n'
MUTANTS = [
    ("no apply before the explicit open", CALL + OPEN, OPEN),
    ("the fixes applied after the open", CALL + OPEN, OPEN + CALL),
    ("platform check removed (applied off Linux too)", GATE,
     "    if interface not in LINUX_FIX_INTERFACES:\n"),
    ("interface check removed (loaded for every interface on Linux)", GATE,
     '    if not sys.platform.startswith("linux"):\n'),
    ("the fixes loaded but not applied", "    done = linux_fixes.apply()\n", "    done = None\n"),
]


def run_copy(base, tag, text):
    """Copies this folder to base/tag/tools with vehicle_runner.py as text and runs the
    ExplicitOpen tests there. Returns (exit code, failures, errors, output tail)."""
    d = os.path.join(base, tag, "tools")
    gi_paths.remove_tree(os.path.join(base, tag))
    shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="") as f:
        f.write(text)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST), "ExplicitOpen"], cwd=d, env=env,
                       capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    m = re.search(r"FAILED \(([^)]*)\)", out)
    counts = dict(re.findall(r"(failures|errors)=(\d+)", m.group(1))) if m else {}
    gi_paths.remove_tree(os.path.join(base, tag))
    return r.returncode, int(counts.get("failures", 0)), int(counts.get("errors", 0)), out.strip()[-300:]


def main():
    base = gi_paths.scratch("mutants-vehicle_runner_explicit")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    try:
        rc, nf, ne, tail = run_copy(base, "control", src)
        control_ok = rc == 0 and nf == 0 and ne == 0 and "OK" in tail
        print("%s control: the unmodified copy  (exit %d, %d failures, %d errors)%s" % (
            "PASS    " if control_ok else "FAILED  ", rc, nf, ne, "" if control_ok else "\n           " + tail))
        if not control_ok:
            return 1
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            rc, nf, ne, tail = run_copy(base, "m%d" % n, src.replace(old, new))
            dead = rc != 0 and nf > 0
            killed += dead
            print("%s m%d %s  (exit %d, %d failures, %d errors)" % ("KILLED  " if dead else "SURVIVED", n, name,
                                                                 rc, nf, ne))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
