#!/usr/bin/env python3
"""Test baseline_b_desk.sh: on the e3d9437 bench files each desk step's output judges
exactly as the baseline index says, and a step given no input, or the wrong number of
records, exits 2 having written no result.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> py -3.14 -B \
        gen-inhibit/tools/bench/test_baseline_b_desk.py [--steps B13,B3]

Run from the reverse-it root, with REVERSE_IT_DIR set to it. suite_judges.py comes
from this file's parent folder, gen-inhibit/tools (gi_paths.fw_tools()).
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
STEPS = ["B2", "B3", "B24", "B25", "B11", "B11raw", "B13", "B5", "B21client", "B19present"]
NDIRS = {"B2": 2, "B3": 1, "B24": 2, "B25": 1, "B11": 1, "B11raw": 1, "B13": 3, "B5": 2, "B21client": 2,
         "B19present": 4, "F4": 1}
# Steps with no e3d9437 files, tested on synthetic records only (section 5): F4 (2026-11-27)
SYNTH = ["F4"]
# B21client writes the half of B21 the client judges (topic (E), 2026-11-19); B21's own judge also reads
# the guard steps' files, so this step is checked on its two files, not through the index: each the
# baseline folder's byte for byte, VOID with 20 of 20 whole 403s (the reviewer's readings (t), (w)).
CLIENT_LAST = "verdict VOID: 20 reps, 0 PASS, 0 FAIL, 0 ERROR, 20 VOID, 20 with the whole 403 and its reason (%s body)"
# Where a step's re-judgement differs from the index, on a ruling since: B5's arms re-judged
# from their records with the jam's start the witness's first error frame at or after the
# train's send (gen-inhibit reviewer, answer to 0cfd6689, 2026-11-19). p2000_run1's arm 0,
# INCONCLUSIVE on e3d9437 ("not live at the start of the jam", against the jammer's ACK, which
# trailed the train's first error frame by 49 ms), reads A PASS and B PASS.
REJUDGED = {"B5": {"verdict": "PASS", "figures": {
    "arms_200_per_s": 3, "pass_200_per_s": 3, "inconclusive_200_per_s": 0,
    "arms_500_per_s": 9, "pass_500_per_s": 9, "inconclusive_500_per_s": 0}},
    # B11raw in the request-to-reply window (spec 3, topic (E), 2026-11-19): no page read follows any
    # reply in the e3d9437 records, a reading never sent, so every POST is VOID (the reviewer's (t), (w))
    "B11raw": {"verdict": "VOID", "figures": {
        "posts": 12, "pass": 0, "fail": 0, "error": 0, "void": 12, "witness_not_applicable": 3,
        "window_ms_min": 8.3, "window_ms_max": 37.5, "margin_ms_max": 0.46}}}
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
    ap.add_argument("--steps", default=",".join(STEPS + SYNTH))
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
    gi_paths.remove_tree(tmp)
    try:
        # 1. the e3d9437 files: each step's output judges as the index says
        # The e3d9437 index names port step 1c's renamed switches by their old names; the
        # judges report the new ones since 551dc655 (suite_judges.RENAMED, new -> old).
        new_name = {old: new for new, old in SJ.RENAMED.items()}
        for s in steps:
            if s in SYNTH:
                continue
            out = os.path.join(tmp, "real", s)
            dirs = [bench, runs] if s in ("B5", "B21client") else [runs if s == "B25" else bench] * NDIRS[s]
            rc, log = desk(s, dirs, out)
            check("%s: exits 0 on the e3d9437 files" % s, rc == 0, log[-300:])
            if s == "B5":
                # beside the run's own output, never over it: the default GI_OUT is the bench folder
                want_names = sorted(["B5_livetrain_p5000_rejudged.txt"] +
                                    ["B5_livetrain_p2000_run%d_rejudged.txt" % r for r in (1, 2, 3)])
                check("B5: writes the four re-judgements as <output>_rejudged.txt, and nothing else",
                      written(out) == want_names, written(out))
                # the baseline folder holds them beside the run's own lines (the reviewer's answer
                # to 2ddd4dcc, 2026-11-19), and the suite reads those: they are this step's output
                same = lambda n: (os.path.isfile(os.path.join(bench, n)) and open(os.path.join(out, n), "rb").read()
                                  == open(os.path.join(bench, n), "rb").read())
                check("B5: the baseline folder's re-judgements are byte for byte what this step writes",
                      all(same(n) for n in want_names), [n for n in want_names if not same(n)])
            if s == "B11raw":
                # the window judge's file is in the baseline folder beside the capture judge's (the
                # reviewer's (w)), copied in alone: it is this step's output
                n = "B11raw_window_judge.txt"
                check("B11raw: the baseline folder's window judgement is byte for byte what this step writes",
                      os.path.isfile(os.path.join(bench, n)) and os.path.isfile(os.path.join(out, n))
                      and open(os.path.join(out, n), "rb").read() == open(os.path.join(bench, n), "rb").read())
            if s == "B21client":
                want_names = ["B21_client_image_%s.txt" % C, "B21_client_multipart_%s.txt" % C]
                check("B21client: writes the two client judgements, and nothing else", written(out) == want_names,
                      written(out))
                same = lambda n: (os.path.isfile(os.path.join(bench, n)) and open(os.path.join(out, n), "rb").read()
                                  == open(os.path.join(bench, n), "rb").read())
                check("B21client: the baseline folder's are byte for byte what this step writes",
                      all(same(n) for n in want_names), [n for n in want_names if not same(n)])
                lasts = [open(os.path.join(out, "B21_client_%s_%s.txt" % (k, C)), encoding="utf-8").read().splitlines()[-1:]
                         for k in ("multipart", "image")]
                check("B21client: each VOID (no page read around a request), 20 of 20 whole 403s",
                      lasts == [[CLIENT_LAST % "multipart"], [CLIENT_LAST % "image"]], lasts)
                continue
            res = SJ.judge_one(s, out)
            v = [r["verdict"] for r in res]
            fg = {k: x["value"] for r in res for k, x in r["figures"].items()}
            want = dict(idx[s], figures={new_name.get(k, k): x for k, x in idx[s]["figures"].items()})
            want.update(REJUDGED.get(s, {}))
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
        if "B5" in steps:
            # three of the four outputs; then all four with one run's record missing: exit 2,
            # nothing written (every record is found before anything is written)
            three = os.path.join(tmp, "b5three")
            os.makedirs(three)
            for n in ("B5_livetrain_p5000.txt", "B5_livetrain_p2000_run1.txt", "B5_livetrain_p2000_run2.txt"):
                shutil.copy(os.path.join(bench, n), three)
            out = os.path.join(tmp, "b5three_out")
            rc, log = desk("B5", [three, runs], out)
            check("B5: three livetrain outputs, not four, exits 2", rc == 2, (rc, log[-200:]))
            check("B5: three livetrain outputs writes nothing", written(out) == [], written(out))
            norec = os.path.join(tmp, "b5norec")
            os.makedirs(norec)
            names = ["B5_livetrain_p5000.txt"] + ["B5_livetrain_p2000_run%d.txt" % r for r in (1, 2, 3)]
            for n in names:
                shutil.copy(os.path.join(bench, n), norec)
            recs = os.path.join(tmp, "b5recs")
            os.makedirs(recs)
            for n in names[:3]:
                with open(os.path.join(bench, n), encoding="utf-8", errors="replace") as f:
                    w = [x.strip() for x in f if x.startswith("wrote ")]
                r = w[-1].replace("\\", "/").rsplit("/", 1)[-1]
                shutil.copy(os.path.join(runs, r), recs)
            out = os.path.join(tmp, "b5norec_out")
            rc, log = desk("B5", [norec, recs], out)
            check("B5: one run's record missing exits 2", rc == 2 and "no record for B5_livetrain_p2000_run3.txt" in log,
                  (rc, log[-200:]))
            check("B5: one run's record missing writes nothing", written(out) == [], written(out))
        if "B21client" in steps:
            # two multipart probe outputs; then one body's record missing: exit 2, nothing written
            two = os.path.join(tmp, "b21two")
            os.makedirs(two)
            for p in glob.glob(os.path.join(bench, "B21_probe_*.txt")):
                shutil.copy(p, two)
            mp = glob.glob(os.path.join(two, "B21_probe_multipart_*.txt"))[0]
            shutil.copy(mp, mp[:-4] + "_again.txt")
            out = os.path.join(tmp, "b21two_out")
            rc, log = desk("B21client", [two, runs], out)
            check("B21client: two multipart probe outputs exits 2", rc == 2 and "2 multipart probe outputs" in log,
                  (rc, log[-200:]))
            check("B21client: two multipart probe outputs writes nothing", written(out) == [], written(out))
            os.remove(mp[:-4] + "_again.txt")
            recs = os.path.join(tmp, "b21recs")
            os.makedirs(recs)
            with open(mp, encoding="utf-8", errors="replace") as f:
                w = [x.strip() for x in f if "; wrote " in x]
            shutil.copy(os.path.join(runs, w[-1].replace("\\", "/").rsplit("/", 1)[-1]), recs)
            out = os.path.join(tmp, "b21norec_out")
            rc, log = desk("B21client", [two, recs], out)
            check("B21client: the image probe's record missing exits 2", rc == 2 and "no record for B21_probe_image_" in log,
                  (rc, log[-200:]))
            check("B21client: the image probe's record missing writes nothing", written(out) == [], written(out))
        if "B13" in steps:
            noheld = os.path.join(tmp, "b13noheld")
            some("B2_e4_presence_respond_%s_x2app.json" % C, 1, noheld)
            rc, log = desk("B13", [bench, noheld, noheld], os.path.join(tmp, "b13noheld_out"))
            check("B13: no held RESPOND arm exits 2", rc == 2, (rc, log[-200:]))
        if "F4" in steps:
            # 5. F4 on synthetic records (2026-11-27): the arms' records only, never the
            # _provenance.json files beside them; exit 0 and both files when every record gives its
            # figures; exit 1 when the profile counts one out
            def f4rec(path, mode="respond"):
                s = [[100.0 + 5.0 + t, 0, rx, 2, None, 0] for t, rx in ((-0.5, None), (0.5, 3), (20.0, 3))]
                a = {"arm_after_s": 5.0, "post_s": [5.0, 5.03], "post_http": 200, "poll_hz": 2.0,
                     "queue_series": s, "perf_base": 100.0, "void": False, "void_reasons": [],
                     "device": {"ctr_bad": 0, "drv": {"rx_missed": 3}}}
                with open(path, "w", encoding="ascii") as f:
                    json.dump({"mode": mode, "arms": {"idle": a}}, f)
            good = os.path.join(tmp, "f4good")
            os.makedirs(good)
            for v in ("p2hz_a_arm1", "p2hz_b_arm1"):
                f4rec(os.path.join(good, "F4_e4_respond_entry_%s_%s.json" % (C, v)))
            f4rec(os.path.join(good, "F4_e4_respond_entry_%s_p2hz_a_arm1_idle_provenance.json" % C), mode="none")
            out = os.path.join(tmp, "f4good_out")
            rc, log = desk("F4", [good], out)
            check("F4: two good records exit 0, the profile and the mode entry analysis written",
                  rc == 0 and written(out) == ["F4_mode_entry_analysis.txt", "F4_respond_entry_profile.txt"],
                  (rc, written(out), log[-300:]))
            prof = open(os.path.join(out, "F4_respond_entry_profile.txt"), encoding="utf-8").read() \
                if os.path.isfile(os.path.join(out, "F4_respond_entry_profile.txt")) else ""
            check("F4: the profile reads the two arms' records, not the provenance file beside them",
                  prof.splitlines()[-1:] == ["2 record(s), 0 counted out"] and "provenance" not in prof, prof[-300:])
            bad = os.path.join(tmp, "f4bad")
            os.makedirs(bad)
            f4rec(os.path.join(bad, "F4_e4_respond_entry_%s_p2hz_a_arm1.json" % C))
            f4rec(os.path.join(bad, "F4_e4_respond_entry_%s_unpolled_arm1.json" % C), mode="inhibit")
            out = os.path.join(tmp, "f4bad_out")
            rc, log = desk("F4", [bad], out)
            check("F4: a record the profile counts out exits 1, the profile naming it",
                  rc == 1 and "COUNTED OUT" in open(os.path.join(out, "F4_respond_entry_profile.txt"),
                                                    encoding="utf-8").read(), (rc, log[-300:]))
        if "B11raw" in steps:
            # 4. a run folder with a space in its path judges the same: the step hands each
            # record to the judge as its own word. The e3d9437 folder had spaces on Windows
            # (SeaDrive's "My Libraries"); on Linux it has none, so without this copy a
            # word-split argument list passes unseen (2026-11-16). The records and their witness
            # logs only since topic (E): no capture.
            spaced = os.path.join(tmp, "spaced run dir")
            os.makedirs(spaced)
            for j in sorted(glob.glob(os.path.join(bench, "B11raw_e4_quiesce_*.json"))):
                for p in glob.glob(os.path.splitext(j)[0] + "*"):
                    if not p.endswith(".pcapng"):
                        shutil.copy(p, spaced)
            out = os.path.join(tmp, "spaced out", "B11raw")
            rc, log = desk("B11raw", [spaced], out)
            check("B11raw: a run folder with a space in its path exits 0", rc == 0, log[-300:])
            res = SJ.judge_one("B11raw", out)
            fg = {k: x["value"] for r in res for k, x in r["figures"].items()}
            want = REJUDGED["B11raw"]["figures"]
            check("B11raw: a run folder with a space in its path, with no capture, judges as ruled (%s)"
                  % REJUDGED["B11raw"]["verdict"],
                  [r["verdict"] for r in res] == [REJUDGED["B11raw"]["verdict"]] and fg == want,
                  ([(r["verdict"], r["detail"][:120]) for r in res],
                   {k: (fg.get(k), want.get(k)) for k in set(fg) | set(want) if fg.get(k) != want.get(k)}))
    finally:
        gi_paths.remove_tree(tmp)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
