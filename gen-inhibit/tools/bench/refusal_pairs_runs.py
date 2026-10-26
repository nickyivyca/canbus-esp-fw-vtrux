#!/usr/bin/env python3
"""Part C3 on real images: the firmware repo's test/build_checks/test_refusal_pairs.py
run once on the right images (must PASS), then once per negative control, each of
which must FAIL on the check it targets (a FAIL elsewhere does not count).
Gen-inhibit tester, 2026-10-16 (adds the stock features and the AutoPID control).

    GI_FW=<project folder> py -3.14 refusal_pairs_runs.py --tree <WiCAN tree with build-tester-*/> --sha e3d9437 --out <file>

Images are <tree>/build-tester-<name>/wican-fw_obd_<sha>_man[_rt|_flt].bin, the
names baseline_builds.ps1 gives them. The no-ELF control copies the product image
alone into the worktree's scratch/.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

FW = os.environ.get("GI_FW") or gi_paths.project_root()   # a project folder (gen-inhibit/ since port step 0d)
PAIRS = os.path.join(FW, "test", "build_checks", "test_refusal_pairs.py")
ON = {"GI_TEST_RESTART_QUIESCE": "rt", "GI_INSTRUMENT_RXQ": "rxq", "GI_INSTRUMENT_TXABORT": "txabort",
      "GI_INSTRUMENT_ABORTWIN": "abortwin", "OTA_HEALTH_FAULT_INJECT": "flt", "WICAN_FEATURE_MQTT": "fmqtt",
      "WICAN_FEATURE_ELM327": "felm", "WICAN_FEATURE_AUTOPID": "fautopid", "WICAN_FEATURE_REALDASH": "frealdash"}
CONTROL = {"WICAN_FEATURE_AUTOPID": "fmqttelm"}
AP = "WICAN_FEATURE_AUTOPID"


def image(tree, name):
    hits = glob.glob(os.path.join(tree, "build-tester-%s" % name, "wican-fw_obd_*.bin"))
    if len(hits) != 1:
        raise SystemExit("build-tester-%s: %d images" % (name, len(hits)))
    return hits[0]


def argv(tree, off="man", on=None, control=None, drop=()):
    on = dict(ON, **(on or {}))
    control = dict(CONTROL, **(control or {}))
    a = ["--off", off if os.path.isabs(off) else image(tree, off)]
    for sw, n in sorted(on.items()):
        if sw not in drop:
            a += ["--on", "%s=%s" % (sw, image(tree, n))]
    for sw, n in sorted(control.items()):
        if ("control:" + sw) not in drop:
            a += ["--control", "%s=%s" % (sw, image(tree, n))]
    return a


def run(a):
    r = subprocess.run([sys.executable, "-B", PAIRS] + a, capture_output=True, text=True, timeout=1800)
    return r.returncode, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t = args.tree
    noelf = os.path.join(gi_paths.scratch("refusal_pairs"), "noelf")
    os.makedirs(noelf)
    man = image(t, "man")
    noelf_bin = os.path.join(noelf, os.path.basename(man))
    shutil.copy(man, noelf_bin)
    negatives = [
        ("wrong ON image: ELM327's image as MQTT's", argv(t, on={"WICAN_FEATURE_MQTT": "felm"}),
         "WICAN_FEATURE_MQTT ON: 'no MQTT feature in the image' fails naming it"),
        ("the product image as AutoPID's ON", argv(t, on={AP: "man"}),
         "WICAN_FEATURE_AUTOPID ON: 'no AutoPID feature in the image' fails"),
        ("the product image as RealDash's ON", argv(t, on={"WICAN_FEATURE_REALDASH": "man"}),
         "WICAN_FEATURE_REALDASH ON: 'no boot-time bus enable in app_main' fails"),
        ("a stock-feature image (MQTT) as OFF", argv(t, off="fmqtt"), "OFF: 'no MQTT feature in the ELF' passes"),
        ("an instrument image (rxq) as OFF", argv(t, off="rxq"), "OFF: 'no instrument symbols in the ELF' passes"),
        ("the fault-injection image as OFF", argv(t, off="flt"), "OFF: 'no OTA fault injection in the image' passes"),
        ("AutoPID's own ON image as its control", argv(t, control={AP: "fautopid"}),
         "WICAN_FEATURE_AUTOPID CONTROL: 'no AutoPID feature in the ELF' passes"),
        ("the product image as AutoPID's control", argv(t, control={AP: "man"}),
         "WICAN_FEATURE_AUTOPID CONTROL carries WICAN_FEATURE_MQTT"),
        ("no control for AutoPID", argv(t, drop=("control:" + AP,)),
         "every switch that cannot be built alone has a CONTROL image"),
        ("a stock feature left out (RealDash)", argv(t, drop=("WICAN_FEATURE_REALDASH",)),
         "every test-only switch and stock feature has an ON image"),
        ("an image without its ELF as OFF", argv(t, off=noelf_bin), "OFF: 'no MQTT feature in the ELF' passes"),
    ]
    out = ["test_refusal_pairs.py on %s, tree %s (names relative to it)" % (args.sha, os.path.basename(t)), ""]
    code, text = run(argv(t))
    ok_pos = code == 0 and text.rstrip().endswith("PASS")
    out += ["## positive: every image right (must PASS) -> %s" % ("PASS" if ok_pos else "FAIL"),
            text.replace(t, "<tree>").rstrip(), ""]
    bad = 0 if ok_pos else 1
    for name, a, want in negatives:
        code, text = run(a)
        hit = [l for l in text.splitlines() if l.strip().startswith("FAIL") and want in l]
        caught = code != 0 and bool(hit)
        bad += 0 if caught else 1
        out += ["## negative: %s -> %s (expects a FAIL on %r)" % (name, "CAUGHT" if caught else "NOT CAUGHT", want)]
        out += [l.replace(t, "<tree>").replace(noelf, "<temp>") for l in text.splitlines()
                if l.strip().startswith("FAIL") or "Traceback" in l or "Error" in l]
        out.append("")
    shutil.rmtree(os.path.dirname(noelf), ignore_errors=True)
    out.append("SUMMARY: positive %s; %d of %d negatives caught on their own check"
               % ("PASS" if ok_pos else "FAIL", len(negatives) - (bad - (0 if ok_pos else 1)), len(negatives)))
    open(args.out, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(out[-1])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
