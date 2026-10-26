#!/usr/bin/env python3
"""The bench tools write their records to GI_RUNS_DIR when it is set, and to SeaDrive's
runs/ (gi_paths.runs()) when it is not (the chain runner, 2026-10-18: each check's records go in
its own folder). Gen-inhibit tester.

    py -3.14 -B test_runs_dir.py

For each tool: imported in a fresh interpreter, its RUNS is GI_RUNS_DIR when set, and
gi_paths.runs() when unset or empty. Then each tool's copy with the override taken out
(the line as it was before 2026-10-18), in the worktree's scratch/, must fail the
same check: a check that cannot fail is not a check.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ["slcan_refusal_probe", "x2_hold", "wrong_bitrate_errors", "trip6_rearm_check", "observe_ack_probe",
         "bench_census", "page_only_baseline", "interlock_bench", "test_only_inert", "restart_error_frames",
         "skip_method_bench", "x2_jam_cases", "wican_txabort_bench", "x2_flash_custody", "x2_jammer",
         "txabort_refusal_check"]
LINE = 'RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()'
BEFORE = 'RUNS = gi_paths.runs()'
PROBE = "import sys; sys.path.insert(0, '.'); sys.path.insert(0, sys.argv[2]); sys.path.insert(0, sys.argv[1]); import %s as T; print(T.RUNS)"


def runs_of(folder, tool, value):
    env = dict(os.environ)
    env.pop("GI_RUNS_DIR", None)
    if value is not None:
        env["GI_RUNS_DIR"] = value
    r = subprocess.run([sys.executable, "-B", "-c", PROBE % tool, folder, HERE], env=env, capture_output=True, text=True,
                       cwd=gi_paths.reverse_it_dir())
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else "IMPORT FAILED: %s" % r.stderr[-300:]


def follows(folder, tool):
    want_set = gi_paths.scratch("runs-dir-probe")
    default = gi_paths.runs()
    got = (runs_of(folder, tool, want_set), runs_of(folder, tool, None), runs_of(folder, tool, ""))
    return got == (want_set, default, default), got


def main():
    fails = 0
    scratch = gi_paths.scratch("mutants-runs_dir")
    for t in TOOLS:
        ok, got = follows(HERE, t)
        print("%s %s follows GI_RUNS_DIR%s" % ("ok  " if ok else "FAIL", t, "" if ok else "  -- %s" % (got,)))
        fails += not ok
        # The same tool with its override taken out must fail the check. It is copied
        # alone, so its own imports of other tools still come from HERE.
        d = os.path.join(scratch, t)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, t + ".py"), encoding="utf-8", newline="").read()
        if src.count(LINE) != 1:
            print("FAIL %s: the override line is not there once (%d)" % (t, src.count(LINE)))
            fails += 1
            continue
        open(os.path.join(d, t + ".py"), "w", encoding="utf-8", newline="").write(src.replace(LINE, BEFORE))
        mok, mgot = follows(d, t)
        # a kill is a wrong RUNS read back, not an import that fails
        killed = not mok and not any(g.startswith("IMPORT FAILED") for g in mgot)
        print("%s %s without the override fails the check%s" % ("ok  " if killed else "FAIL", t, "" if killed else "  -- %s" % (mgot,)))
        fails += not killed
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(scratch, ignore_errors=True)
    print("\n%s: %d failed" % ("FAIL" if fails else "PASS", fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
