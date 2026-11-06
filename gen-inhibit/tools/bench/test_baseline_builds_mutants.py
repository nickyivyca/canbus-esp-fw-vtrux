#!/usr/bin/env python3
"""Mutants of baseline_builds.ps1's check-env step; test_baseline_builds.py must fail on each
BY A CHECK. A crash is not a kill, and an edit that does not apply is an error. Windows only.

    py -3.14 -B gen-inhibit/tools/bench/test_baseline_builds_mutants.py

Each mutant is a copy in the worktree's scratch/ (gen-inhibit/tools/bench/ with
baseline_builds.ps1, the test and gi_paths.py), one edit applied to baseline_builds.ps1.
The unmutated copy runs first, as the control: if it fails, no mutant is judged and the run
exits 1. A test run with no summary line crashed, which is not a kill (the fake launcher's
own traceback, in the crash case, is written to the build log by design).

Gen-inhibit tester, 2026-11-04 (port step 1d).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = "test_baseline_builds.py"
TARGET = "baseline_builds.ps1"
BENCH_FILES = (TARGET, TEST, "gi_paths.py")
CALL = "    & py -3.14 -B $Launcher check-env *>&1 | ForEach-Object { \"$_\" } | Out-File -Encoding utf8 -Append $log\n"
MUTANTS = [
    ("check-env is not run", CALL, ""),
    ("a refusal is ignored", "    if ($envrc -ne 0) {", "    if ($false) {"),
    ("only exit 1 is a refusal", "    if ($envrc -ne 0) {", "    if ($envrc -eq 1) {"),
    ("a refusal exits 0", "Write-Output \"$b : check-env refused (exit $envrc), nothing built\"\n        $rc = 3",
     "Write-Output \"$b : check-env refused (exit $envrc), nothing built\"\n        $rc = 0"),
    ("a refusal still builds", "Write-Output \"$b : check-env refused (exit $envrc), nothing built\"\n        $rc = 3; continue",
     "Write-Output \"$b : check-env refused (exit $envrc), nothing built\"\n        $rc = 3"),
    ("a missing launcher builds", "        Write-Output \"$b : no launcher at $Launcher, check-env refused, nothing built\"\n"
     "        $rc = 3; continue\n    }\n    Push-Location $Root\n"
     "    & py -3.14 -B $Launcher check-env *>&1 | ForEach-Object { \"$_\" } | Out-File -Encoding utf8 -Append $log\n"
     "    $envrc = $LASTEXITCODE",
     "        Write-Output \"$b : no launcher at $Launcher, check-env refused, nothing built\"\n"
     "        $rc = 3\n    }\n    Push-Location $Root\n"
     "    & py -3.14 -B $Launcher check-env *>&1 | ForEach-Object { \"$_\" } | Out-File -Encoding utf8 -Append $log\n"
     "    $envrc = 0"),
    ("the launcher is the harness tree's, not the tree's own",
     '$Launcher = Join-Path (Split-Path -Parent $Tree) "vtrux_tools.py"',
     '$Launcher = Join-Path $PSScriptRoot "..\\..\\vtrux_tools.py"'),
    ("check-env runs in the tree, not the worktree's root", "    Push-Location $Root\n", "    Push-Location $Tree\n"),
    ("check-env gets IDF_TOOLS_PATH cleared", CALL, "    $env:IDF_TOOLS_PATH = $null\n" + CALL),
    ("check-env asked as check_env", "-B $Launcher check-env *>&1", "-B $Launcher check_env *>&1"),
    ("check-env's output is not logged", CALL,
     "    & py -3.14 -B $Launcher check-env *>&1 | Out-Null\n"),
    ("a later build's failure hides a refusal", "    if ($code -ne 0 -and $rc -eq 0) { $rc = 1 }",
     "    if ($code -ne 0) { $rc = 1 }"),
]
SUMMARY = re.compile(r"^\d+ ok, \d+ failed", re.M)


def main():
    if os.name != "nt":
        print("SKIP: Windows PowerShell only")
        return 0
    base = gi_paths.scratch("mutants-baseline_builds")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in BENCH_FILES}
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and srcs[TARGET].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[TARGET].count(old), TARGET))
            errors += 1
            continue
        bench = os.path.join(base, "m%d" % n, "gen-inhibit", "tools", "bench")
        os.makedirs(bench)
        for f, s in srcs.items():
            with open(os.path.join(bench, f), "w", encoding="utf-8", newline="") as fh:
                fh.write(s.replace(old, new) if (f == TARGET and old is not None) else s)
        r = subprocess.run([sys.executable, "-B", os.path.join(bench, TEST)], cwd=bench,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        fail_lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = SUMMARY.search(r.stdout) is None
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
