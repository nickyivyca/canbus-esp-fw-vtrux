#!/usr/bin/env python3
"""Baseline part C1, C2 and C7 in one record: build_output.py on each image given,
build_switches.py on the image's build directory, elf_checks.py on each ELF, and
every test/build_checks/test_* that needs no build or image. Gen-inhibit tester,
2026-10-16 (written for e3d9437; part C on c65c45e was run by hand).

    py -3.14 build_checks_light.py --checks <a test/build_checks folder> --repo <worktree> \
        --build <worktree>/build-tester-man --image <bin> [--image <bin> ...] --out <file>

--checks picks WHICH copy of the checks runs: the commit's own (in the worktree), or
the tester's clone with unpublished harness changes. Run under py -3.14, which has
cantools; the IDF venv's Python does not, and the DBC row would be NO REF.
Paths in the record are made relative to --repo's parent.
"""
import argparse
import glob
import os
import subprocess
import sys
import time

# Tests that need no build, image or network. test_refusal_pairs.py needs images
# (refusal_pairs_runs.py), so it is not here.
LIGHT = ("test_build_switches.py", "test_switch_rows.py", "test_scan_scope_check.py", "test_flt_row.py",
         "test_fw_version_row.py", "test_partition_row.py", "test_prod_record.py", "test_image_rows.py")


def run(cmd, cwd):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=1800)
    return r.returncode, (r.stdout + r.stderr), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--image", action="append", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    c = os.path.abspath(args.checks)
    base = os.path.dirname(os.path.abspath(args.repo))
    py = [sys.executable, "-B"]
    steps = []
    for img in args.image:
        steps.append(("build_output.py --bin %s" % img, py + ["build_output.py", "--bin", os.path.abspath(img)]))
    steps.append(("build_switches.py --repo %s --build %s" % (args.repo, args.build),
                  py + ["build_switches.py", "--repo", os.path.abspath(args.repo), "--build", os.path.abspath(args.build)]))
    for img in args.image:
        elf = os.path.splitext(img)[0] + ".elf"
        steps.append(("elf_checks.py %s" % elf, py + ["elf_checks.py", os.path.abspath(elf)]))
    present = {os.path.basename(p) for p in glob.glob(os.path.join(c, "test_*.py"))}
    for t in LIGHT:
        if t in present:
            steps.append((t, py + [t]))
    out = ["# build_checks light rows, NICKY-XPS %s, %s, checks from %s"
           % (time.strftime("%Y-%m-%d %H:%M"), os.path.basename(sys.executable), c.replace(base, "<git>")), ""]
    out += ["# not run: %s (not in this copy)" % t for t in LIGHT if t not in present]
    bad = []
    for name, cmd in steps:
        code, text, dt = run(cmd, c)
        out += ["===== " + name.replace(base, "<git>"), text.replace(base, "<git>").rstrip(),
                "===== exit %d (%.0f s)" % (code, dt), ""]
        if code != 0:
            bad.append("%s exit %d" % (name.replace(base, "<git>"), code))
    out.append("SUMMARY: %d steps, %s" % (len(steps), "all exit 0" if not bad else "non-zero: " + "; ".join(bad)))
    open(args.out, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(out[-1])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
