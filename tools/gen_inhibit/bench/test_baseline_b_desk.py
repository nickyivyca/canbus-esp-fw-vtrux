#!/usr/bin/env python3
"""Test baseline_b_desk.sh: on the e3d9437 bench files each desk step's output judges
exactly as the baseline index says, and a step given no input, or the wrong number of
records, exits 2 having written no result.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> py -3.14 -B \
        tools/gen_inhibit/bench/test_baseline_b_desk.py [--steps B13,B3]

Run from the reverse-it root, with REVERSE_IT_DIR set to it. suite_judges.py comes
from this file's parent folder, tools/gen_inhibit (gi_paths.fw_tools()).
DESK_SCRIPT overrides the script under test (the mutant runner). Scratch goes to the
worktree's scratch/. The full run takes about 10 minutes (B24 reads every arm).
Gen-inhibit tester, 2026-10-19.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("DESK_SCRIPT") or os.path.join(HERE, "baseline_b_desk.sh")
TOOLS = gi_paths.fw_tools()
# Not a bare "bash": on Windows CreateProcess looks in System32 before PATH, which finds
# WSL's bash.exe; PATH's (Git Bash) is the one these scripts are written for.
BASH = os.environ.get("GI_BASH") or shutil.which("bash")
C = "e3d9437"
STEPS = ["B2", "B3", "B24", "B25", "B11", "B11raw", "B13", "B19present"]
NDIRS = {"B2": 2, "B3": 1, "B24": 2, "B25": 1, "B11": 1, "B11raw": 1, "B13": 3, "B19present": 4}
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))
    sys.stdout.flush()


def desk(step, dirs, out):
    os.makedirs(out, exist_ok=True)
    fw = lambda p: p.replace("\\", "/")            # Git Bash takes forward slashes only
    env = dict(os.environ, GI_COMMIT=C, GI_OUT=fw(out))
    r = subprocess.run([BASH, fw(SCRIPT), step] + [fw(d) for d in dirs], env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def written(out):
    return sorted(n for n in os.listdir(out)) if os.path.isdir(out) else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default=",".join(STEPS))
    a = ap.parse_args()
    steps = [s for s in a.steps.split(",") if s]
    base = os.environ.get("GI_BASELINE_DIR")
    if not base:
        print("ERROR GI_BASELINE_DIR is not set")
        return 1
    gi_paths.pinned_tree(base)     # the baseline records the steps judge (toolkit item 9)
    bench = os.path.join(base, "bench")
    runs = os.path.dirname(base)            # B20's records went to runs/ on e3d9437
    sys.path.insert(0, TOOLS)
    import suite_judges as SJ
    with open(os.path.join(base, "suite_index_e3d9437.json"), encoding="utf-8") as f:
        idx = {c["id"]: c for c in json.load(f)["checks"]}
    tmp = gi_paths.scratch("desk-test")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        # 1. the e3d9437 files: each step's output judges as the index says
        for s in steps:
            out = os.path.join(tmp, "real", s)
            dirs = [runs if s == "B25" else bench] * NDIRS[s]
            rc, log = desk(s, dirs, out)
            check("%s: exits 0 on the e3d9437 files" % s, rc == 0, log[-300:])
            res = SJ.judge_one(s, out)
            v = [r["verdict"] for r in res]
            fg = {k: x["value"] for r in res for k, x in r["figures"].items()}
            want = idx[s]
            check("%s: judged as the index (%s)" % (s, want["verdict"]), v == [want["verdict"]],
                  [(r["verdict"], r["detail"][:120]) for r in res])
            check("%s: every figure is the index's, and no other" % s, fg == want["figures"],
                  {k: (fg.get(k), want["figures"].get(k)) for k in set(fg) | set(want["figures"])
                   if fg.get(k) != want["figures"].get(k)})

        # 2. no input: exit 2, no result written
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        for s in steps:
            out = os.path.join(tmp, "none", s)
            rc, log = desk(s, [empty] * NDIRS[s], out)
            check("%s: no input files exits 2" % s, rc == 2, (rc, log[-200:]))
            check("%s: no input files writes nothing" % s, written(out) == [], written(out))

        # 3. the wrong number of records
        def some(pattern, n, folder):
            os.makedirs(folder, exist_ok=True)
            got = sorted(glob.glob(os.path.join(bench, pattern)))[:n]
            for j in got:
                for p in glob.glob(os.path.splitext(j)[0] + ".*"):
                    shutil.copy(p, folder)
            return got
        if "B2" in steps:
            five = os.path.join(tmp, "b2five")
            some("B2_e4_presence_*_%s_x2app.json" % C, 3, five)
            some("B2_e4_presence_*_%s_x2held.json" % C, 2, five)
            out = os.path.join(tmp, "b2five_out")
            rc, log = desk("B2", [five, five], out)
            check("B2: five arm records, not six, exits 2", rc == 2, (rc, log[-200:]))
            check("B2: five arm records writes no count", "B2_x2_presence_count.txt" not in written(out), written(out))
        if "B25" in steps:
            two = os.path.join(tmp, "b25two")
            os.makedirs(two)
            src = sorted(glob.glob(os.path.join(runs, "interlock_rekey-long_*.json")))
            for k, n in enumerate(("interlock_rekey-long_A.json", "interlock_rekey-long_B.json")):
                shutil.copy(src[0], os.path.join(two, n))
            rc, log = desk("B25", [two], os.path.join(tmp, "b25two_out"))
            check("B25: two rekey-long records exits 2", rc == 2, (rc, log[-200:]))
        if "B19present" in steps:
            three = os.path.join(tmp, "b19three")
            os.makedirs(three)
            for t in ("rt", "txabort", "abortwin"):
                shutil.copy(os.path.join(bench, "B19_present_%s.txt" % t), three)
            rc, log = desk("B19present", [three] * 4, os.path.join(tmp, "b19three_out"))
            check("B19present: three of the four files exits 2", rc == 2, (rc, log[-200:]))
        if "B13" in steps:
            noheld = os.path.join(tmp, "b13noheld")
            some("B2_e4_presence_respond_%s_x2app.json" % C, 1, noheld)
            rc, log = desk("B13", [bench, noheld, noheld], os.path.join(tmp, "b13noheld_out"))
            check("B13: no held RESPOND arm exits 2", rc == 2, (rc, log[-200:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
