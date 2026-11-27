#!/usr/bin/env python3
"""Harness test: the tests whose scratch folder is an optional argument leave nothing behind
when run without it (AGENTS.md "Removing scratch"). Gen-inhibit tester, 2026-11-20, from the
whole tools test set: test_reply_golive_report.py required the folder and never removed
what it wrote there.

For each test in OPTIONAL: run it with no scratch folder (GI_KEEP_SCRATCH unset; the mutant
runner test_raw_http_probe_mutants.py, also fixed that day, with one target); it must exit 0,
and its gi_paths.scratch(<name>) run folder, <worktree>/scratch/gen-inhibit-tests/<name>/
<time>-<pid>-<hex6> (<time>_<pid> before port step 4 item 3), and the owner file beside it
must be gone, found by the child's pid.
Can-fail: test_reply_golive_report.py run with GI_KEEP_SCRATCH=1 keeps its folder, and the
same look must find it (then this test removes it).

    python3.13 -B test_optional_scratch.py      # exit 0 only if every check passes
                                                # (py -3.14 -B on Windows)
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

# test file -> (the name it gives gi_paths.scratch(), the other arguments it is run with)
OPTIONAL = {
    "test_reply_golive_report.py": ("reply_golive_report", []),
    "test_busoff_rearm_judge.py": ("busoff_rearm_judge", []),
    "test_interlock_judge.py": ("interlock_judge", []),
    # the mutant runner on its quickest suite (about 30 s): a target name first, no folder
    "test_raw_http_probe_mutants.py": ("raw_http_probe_mutants", ["image_elf_sha.py"]),
}
FAILS = []


def check(label, cond, detail=""):
    print("%s  %s  %s" % ("ok  " if cond else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not cond:
        FAILS.append(label)


def run(test, keep=False, args=()):
    """Run test from this folder with no scratch folder; (exit code, pid, last output line)."""
    env = {k: v for k, v in os.environ.items() if k != gi_paths.KEEP_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if keep:
        env[gi_paths.KEEP_ENV] = "1"
    p = subprocess.Popen([sys.executable, "-B", test] + list(args), cwd=HERE, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out, _ = p.communicate(timeout=600)
    lines = [l for l in out.splitlines() if l.strip()]
    return p.returncode, p.pid, (lines[-1] if lines else "")


def left(name, pid):
    """What the run with this pid left in its scratch name's folder: entries named
    <time>-<pid>-<hex6>, the shared helper's make() (port step 4 item 3), and their owner files."""
    d = os.path.join(gi_paths.worktree_of(HERE), "scratch", "gen-inhibit-tests", name)
    if not os.path.isdir(d):
        return []
    tag = re.compile(r"^\d{8}T\d{6}-%d-[0-9a-f]{6}(%s)?$" % (pid, re.escape(gi_paths.OWNER_SUFFIX)))
    return sorted(os.path.join(d, e) for e in os.listdir(d) if tag.match(e))


def main():
    for test, (name, args) in OPTIONAL.items():
        rc, pid, last = run(test, args=args)
        check("%s with no scratch folder exits 0" % test, rc == 0, (rc, last))
        stay = left(name, pid)
        check("%s with no scratch folder leaves no run folder or owner file" % test, not stay, stay)
    test = "test_reply_golive_report.py"
    rc, pid, last = run(test, keep=True)
    stay = left(OPTIONAL[test][0], pid)
    check("can-fail: %s with %s=1 keeps its folder, and the look finds it" % (test, gi_paths.KEEP_ENV),
          rc == 0 and len(stay) == 2, (rc, stay))
    for p in stay:
        if os.path.isdir(p):
            gi_paths.remove_tree(p)
        elif os.path.exists(p):
            os.remove(p)
    check("can-fail: the kept folder removed again", not left(OPTIONAL[test][0], pid), left(OPTIONAL[test][0], pid))
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
