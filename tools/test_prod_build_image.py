#!/usr/bin/env python3
"""Self-test: prod_build.py judges and records the image the build NAMED.

    python tools/test_prod_build_image.py [--script PATH]

The image prod_build.py hands to refusal 6 and names in `<image>.prod.json` is
the one `build/project_description.json` gives as "app_bin" (reviewer,
2026-10-21). Until then it was the newest wican-fw_obd_*.bin in build/ by
mtime, so a stray, newer .bin of another name copied into build/ would have
been the one judged and recorded.

Each case runs prod_build.main() against a scratch folder standing in for the
repo, with the tree, branch, component, cache and IDF checks replaced by
passes, `idf.py` replaced by a fake whose build writes build/'s contents, and
refusal 6 replaced by a recorder of the image it was given. The record is
written by the real write_record(). Nothing is compiled, and no IDF
environment or git is needed.

  stray       the named image, plus a newer .bin of another name: the named
              image must be the one judged and recorded, and the stray must
              get no record.
  no-desc     build/ has the image but no project_description.json: refused,
              nothing judged, no record.
  no-key      project_description.json has no "app_bin": refused likewise.
  no-file     "app_bin" names a file build/ does not have, beside another
              image: refused likewise.

--script runs another copy of prod_build.py against the same cases;
test_prod_build_image_mutants.py uses it.

Implementor self-test, not evidence. Runs on Windows or Linux.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# AGENTS.md "Files and folders": this worktree's scratch/, never the system
# temp folder. One folder per run, so two runs at once never share one.
SCRATCH = os.path.join(os.path.dirname(HERE), "scratch", "gen-inhibit-prodbuild")
WORK = None                             # this run's, made by main()
NAMED = "wican-fw_obd_1234567_man.bin"
STRAY = "wican-fw_obd_7654321_man_ecc.bin"


def load(script):
    spec = importlib.util.spec_from_file_location("prod_build_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def put(path, data, mtime):
    with open(path, "wb") as fh:
        fh.write(data)
    os.utime(path, (mtime, mtime))


def make_build(layout):
    """What the fake `idf.py build` leaves in build/, per case."""
    def build(repo):
        bdir = os.path.join(repo, "build")
        os.makedirs(bdir, exist_ok=True)
        now = time.time()
        if layout in ("stray", "no-desc", "no-key"):
            put(os.path.join(bdir, NAMED), b"named image", now - 60)
        if layout in ("stray", "no-file"):
            put(os.path.join(bdir, STRAY), b"stray image", now)
        desc = {"project_name": NAMED[:-4], "app_elf": NAMED[:-4] + ".elf"}
        if layout != "no-key":
            desc["app_bin"] = NAMED
        if layout != "no-desc":
            with open(os.path.join(bdir, "project_description.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(desc, fh)
    return build


def run_case(script, layout):
    """prod_build.main() on a scratch repo; returns (rc, judged, records, out)."""
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    mod = load(script)
    mod.REPO = WORK

    def passes(*_a, **_kw):
        return True

    def clean_tree(state):
        state.update(commit="1234567" + "0" * 33, describe="1234567",
                     branch="gen-inhibit/main")
        return True

    mod.check_repo = passes
    mod.check_clean_tree = clean_tree
    mod.check_branch = passes
    mod.check_components = passes
    mod.check_build_cache = passes
    mod.check_idf_env = passes
    judged = []

    def judge(image):
        judged.append(image)
        return True

    mod.check_image_symbols = judge
    build = make_build(layout)
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "idf.py":
            if "build" in cmd:
                build(WORK)
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, *a, **kw)

    out = io.StringIO()
    mod.subprocess.run = fake_run
    argv, sys.argv = sys.argv, [script]     # main() parses its own arguments
    try:
        with contextlib.redirect_stdout(out):
            rc = mod.main()
    finally:
        mod.subprocess.run = real_run
        sys.argv = argv
    bdir = os.path.join(WORK, "build")
    records = {}
    for f in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
        if f.endswith(".prod.json"):
            with open(os.path.join(bdir, f), encoding="ascii") as fh:
                records[f] = json.load(fh)
    return rc, [os.path.basename(j) for j in judged], records, out.getvalue()


def verdict(layout, rc, judged, records):
    """None when the case behaved as required, else why not."""
    if layout == "stray":
        rec = NAMED + ".prod.json"
        if rc != 0:
            return "prod_build refused (rc %s) with the named image present" % rc
        if judged != [NAMED]:
            return "refusal 6 was given %s, not %s" % (judged or "nothing", NAMED)
        if sorted(records) != [rec]:
            return "records written: %s, want only %s" % (sorted(records) or "none",
                                                          rec)
        if records[rec].get("image") != NAMED:
            return "the record names %r" % records[rec].get("image")
        return None
    if rc == 0:
        return "prod_build completed (rc 0)"
    if judged:
        return "refusal 6 was given %s" % judged
    if records:
        return "records written: %s" % sorted(records)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=os.path.join(HERE, "prod_build.py"))
    args = ap.parse_args()

    global WORK
    os.makedirs(SCRATCH, exist_ok=True)
    WORK = tempfile.mkdtemp(prefix="image-", dir=SCRATCH)
    print("script under test: %s" % args.script)
    print("scratch repo: %s" % WORK)
    cases = ["stray", "no-desc", "no-key", "no-file"]
    failed = 0
    for layout in cases:
        try:
            rc, judged, records, out = run_case(args.script, layout)
            why = verdict(layout, rc, judged, records)
            tag = "FAIL" if why else "PASS"
        except Exception as e:          # a crash is reported, never a pass
            why, tag, out = "%s: %s" % (type(e).__name__, e), "ERROR", ""
        failed += why is not None
        print("[%s] %-8s %s" % (tag, layout, why or "as required"))
        if why:
            for ln in out.splitlines():
                if "REFUSED" in ln or "prod record" in ln:
                    print("         " + ln.strip())
    shutil.rmtree(WORK, ignore_errors=True)
    print("%d of %d cases failed" % (failed, len(cases)) if failed
          else "all %d cases pass" % len(cases))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
