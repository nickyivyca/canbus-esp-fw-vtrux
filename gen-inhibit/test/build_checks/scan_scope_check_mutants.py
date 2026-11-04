#!/usr/bin/env python3
"""Mutants of scan_scope_check.py's check-env step (port step 1d, 2026-11-03);
test_scan_scope_check.py must fail on each BY A CHECK. A crash is not a kill, and an edit
that does not apply is an error. The unmutated copy runs first, as the control: if it
fails, no mutant is judged and the run exits 1.

    py -3.14 -B gen-inhibit/test/build_checks/scan_scope_check_mutants.py

Each copy goes in its own folder under the worktree's scratch/gen-inhibit-tests/, laid out
as the tree is (gen-inhibit/test/build_checks/ with scan_scope_check.py, build_switches.py
and the test; gen-inhibit/firmware/vtrux_checks.json, which build_switches reads on
import), and is removed at the end. Gen-inhibit tester, 2026-11-03.
"""
import os
import subprocess
import sys


def _bench_dir():
    """gen-inhibit/tools/bench in this file's worktree, found by walking up to its .git (a
    copy in scratch/ finds the same one); in a copy with no git, two folders up."""
    d = here = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            return os.path.normpath(os.path.join(here, "..", "..", "tools", "bench"))
        d = os.path.dirname(d)
    return os.path.join(d, "gen-inhibit", "tools", "bench")


sys.path.append(_bench_dir())
import gi_paths  # noqa: E402  scratch and remove_tree (AGENTS.md "Removing scratch")

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = "test_scan_scope_check.py"
TARGET = "scan_scope_check.py"
FILES = (TARGET, "build_switches.py", TEST)
TABLE = "vtrux_checks.json"      # build_switches reads it from ../../firmware/ on import
MUTANTS = [
    ("a refused check-env is ignored", "    if rc != 0:\n        return\n", "    if False:\n        return\n"),
    ("no check-env at all", "    rc, out = check_env(wt, log)\n", "    rc, out = 0, ''\n"),
    ("check-env after the baseline configure", "    rc, out = check_env(wt, log)\n",
     "    configure(tree, log)\n    rc, out = check_env(wt, log)\n"),
    ("check-env recorded ok whatever its rc", '    record["check_env"] = {"rc": rc, "ok": rc == 0}',
     '    record["check_env"] = {"rc": rc, "ok": True}'),
    ("the launcher is asked for check_env", '"vtrux_tools.py"), "check-env"]', '"vtrux_tools.py"), "check_env"]'),
    ("the harness's own launcher, not the commit's",
     '    return [sys.executable, "-B", os.path.join(wt, "gen-inhibit", "vtrux_tools.py"), "check-env"]',
     '    return [sys.executable, "-B", os.path.join(os.path.dirname(os.path.dirname(HERE)), "vtrux_tools.py"),'
     ' "check-env"]'),
    ("the launcher's environment loses names", "        r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True)",
     "        r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True,"
     " env={k: v for k, v in os.environ.items() if k.upper() in ('PATH', 'SYSTEMROOT')})"),
    ("its rc not logged", '        fh.write("\\n===== check-env (rc %d)\\n" % rc)', '        fh.write("\\n")'),
    ("a refusal reads FAIL, not REFUSED", '        return "REFUSED"', '        return "FAIL"'),
    ("a refusal with three ok steps reads PASS", '    if not (record.get("check_env") or {}).get("ok"):',
     '    if not (record.get("check_env") or {}).get("ok") and len(record["steps"]) != 3:'),
    ("no check-env key reads as passed", '    if not (record.get("check_env") or {}).get("ok"):',
     '    if not (record.get("check_env") or {"ok": True}).get("ok"):'),
]


def main():
    base = gi_paths.scratch("mutants-scan_scope_check")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in FILES}
    table = open(os.path.join(HERE, "..", "..", "firmware", TABLE), encoding="utf-8", newline="").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and srcs[TARGET].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[TARGET].count(old), TARGET))
            errors += 1
            continue
        top = os.path.join(base, "m%d" % n, "gen-inhibit")
        d = os.path.join(top, "test", "build_checks")
        os.makedirs(d)
        os.makedirs(os.path.join(top, "firmware"))
        with open(os.path.join(top, "firmware", TABLE), "w", encoding="utf-8", newline="") as fh:
            fh.write(table)
        for f, s in srcs.items():
            with open(os.path.join(d, f), "w", encoding="utf-8", newline="") as fh:
                fh.write(s.replace(old, new) if (f == TARGET and old is not None) else s)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fail_lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback (most recent call last)" in r.stderr
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
