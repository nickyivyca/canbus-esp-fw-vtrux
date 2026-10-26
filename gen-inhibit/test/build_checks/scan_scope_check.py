#!/usr/bin/env python3
"""Spec 12.3 item 3, the switch scan's CMake scope, shown both ways on real
builds (spec 13 "Checks, not built", 2026-10-13, user via reviewer). Each step
is a build, not only a reconfigure, since 2026-10-24: the scan's C side reads
the files the build compiled from its deps log (reading changed, reviewer).

    python scan_scope_check.py --commit <sha> --components <a tree's managed_components> --out run.json

Run from a shell where ESP-IDF is exported (IDF_PATH set), with its Python.

WHAT IT SHOWS. The scan takes its CMake files from the build's own record, not
from a list, and a build option is a variable that can be set from outside the
file that tests it. So:

    baseline    the commit as it is, `idf.py build`        -> the scan's result
    planted     a NEW repo file, cmake/gi_planted_option.cmake, included from the
                top-level CMakeLists.txt, testing GI_PLANTED_OPTION; configured
                with -DGI_PLANTED_OPTION=ON          -> the scan FAILS, naming it
    set-first   the same file with `set(GI_PLANTED_OPTION OFF)` before the test;
                configured with the same -D           -> the scan PASSES

Each configure is also shown to be what the step claims, by an observation
independent of the scan: the planted file prints a line when its test is true,
so the planted step must print it (the -D reaches the test: it is a build
option) and the set-first step must not (the file's own set wins: it is not).
The planted file must be in the record in both steps, or the step proves
nothing.

It runs in a throwaway detached worktree of the commit beside the repo, with
managed_components/ copied into its WiCAN tree (gen-inhibit/firmware/ from port step
0d, the worktree's root before), and removes it afterwards. Nothing is committed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_switches as BS                                        # noqa: E402

NAME = "GI_PLANTED_OPTION"
PLANTED = "cmake/gi_planted_option.cmake"
INCLUDE = "include(${CMAKE_CURRENT_LIST_DIR}/%s)\n" % PLANTED
SAYS_ON = "%s is on" % NAME
PLANTED_TEXT = ("# planted by scan_scope_check.py\n"
                "if(%s)\n    message(STATUS \"%s\")\nendif()\n" % (NAME, SAYS_ON))
SET_FIRST_TEXT = ("# planted by scan_scope_check.py\n"
                  "set(%s OFF)\n"
                  "if(%s)\n    message(STATUS \"%s\")\nendif()\n" % (NAME, NAME, SAYS_ON))


def judge_planted(rc, log, scanned, found, unlisted):
    """(ok, detail): the configure took the -D, the record names the planted
    file, and the scan reports the name as unlisted, where it is."""
    if rc != 0:
        return False, "the configure failed (rc %d)" % rc
    if scanned is None:
        return False, "the build's record could not be read"
    if PLANTED not in scanned:
        return False, "the record does not name %s" % PLANTED
    if SAYS_ON not in log:
        return False, "the -D did not reach the test (no %r), so it is not shown to be a build option" % SAYS_ON
    where = (found or {}).get(NAME) or []
    if NAME not in (unlisted or []) or not any(w.startswith(PLANTED + ":") for w in where):
        return False, "the scan did not report %s in %s (found at %r)" % (NAME, PLANTED, where)
    return True, "the scan failed on it: %s unlisted at %s" % (NAME, ", ".join(where))


def judge_set_first(rc, log, scanned, found, unlisted, stale, bad):
    """(ok, detail): the configure ignored the -D, the record names the planted
    file, the scan does not report the name, and the scan passes."""
    if rc != 0:
        return False, "the configure failed (rc %d)" % rc
    if scanned is None:
        return False, "the build's record could not be read"
    if PLANTED not in scanned:
        return False, "the record does not name %s, so the scan never read it" % PLANTED
    if SAYS_ON in log:
        return False, "the test was still true despite the set, so the name is still a build option"
    if NAME in (found or {}):
        return False, "the scan still reports %s at %r" % (NAME, found[NAME])
    if unlisted or stale or bad:
        return False, "the scan fails for other reasons: unlisted %r, stale %r, malformed %r" % (unlisted, stale, bad)
    return True, "the scan passed; %s is not reported" % NAME


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def configure(tree, log, defines=()):
    cmd = [sys.executable, os.path.join(os.environ["IDF_PATH"], "tools", "idf.py")]
    cmd += ["-D%s" % d for d in defines] + ["build"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True)
    out = r.stdout + r.stderr
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("\n===== %s (rc %d, %.1f s)\n" % (" ".join(cmd[2:]), r.returncode, time.time() - t0))
        fh.write(out[-30000:])
    return r.returncode, out


def scan_tree(tree):
    """(scanned, found, unlisted, stale, bad, error)."""
    try:
        rec = BS.read_record(tree, os.path.join(tree, "build"))
    except BS.RecordError as e:
        return None, None, None, None, None, str(e)
    found = BS.scan(tree, rec)
    unlisted, stale, bad = BS.classify(found)
    return rec.scanned, found, unlisted, stale, bad, None


def wican_tree(wt, commit, repo):
    """The WiCAN tree (the IDF build root) in worktree wt of commit: gen-inhibit/firmware/
    from port step 0d (2026-10-26) on, the worktree itself before."""
    moved = git(["cat-file", "-e", "%s:gen-inhibit/firmware/CMakeLists.txt" % commit], repo).returncode == 0
    return os.path.join(wt, "gen-inhibit", "firmware") if moved else wt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--components", required=True, help="a managed_components/ directory to copy in")
    ap.add_argument("--out", required=True, help="JSON record; the configure log goes beside it (.log)")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway worktree")
    a = ap.parse_args()
    if "IDF_PATH" not in os.environ:
        print("FAIL: IDF_PATH is not set -- run from a shell where ESP-IDF is exported")
        return 2

    repo = git(["rev-parse", "--show-toplevel"], HERE).stdout.strip()
    c0 = git(["rev-parse", a.commit], repo).stdout.strip()
    tree = os.path.join(os.path.dirname(repo), "wican-scanscope-%s-%s" % (c0[:7], time.strftime("%Y%m%dT%H%M%S")))
    log = os.path.splitext(a.out)[0] + ".log"
    r = git(["worktree", "add", "--detach", tree, c0], repo)
    if r.returncode != 0:
        print("FAIL: worktree add: %s" % r.stderr.strip())
        return 2
    # The WiCAN tree, the IDF build root: gen-inhibit/firmware/ in a commit from port step 0d
    # (2026-10-26) on, the worktree itself before. Everything below configures and scans there.
    wt, tree = tree, wican_tree(tree, c0, repo)
    shutil.copytree(a.components, os.path.join(tree, "managed_components"))
    record = {"commit": c0, "tree": os.path.basename(wt), "project": os.path.relpath(tree, wt).replace("\\", "/"),
              "steps": [],
              "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        # baseline
        rc, out = configure(tree, log)
        scanned, found, unlisted, stale, bad, err = scan_tree(tree)
        base = {"step": "baseline", "rc": rc, "record": scanned, "error": err,
                "unlisted": unlisted, "stale": stale, "malformed": bad,
                "ok": rc == 0 and err is None}
        record["steps"].append(base)
        print("  %-4s %-10s rc %d, %s" % ("ok" if base["ok"] else "FAIL", "baseline", rc,
                                          err or "scanned %s; unlisted %r, stale %r, malformed %r"
                                          % (scanned, unlisted, stale, bad)), flush=True)

        # planted
        os.makedirs(os.path.join(tree, "cmake"), exist_ok=True)
        with open(os.path.join(tree, PLANTED), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(PLANTED_TEXT)
        with open(os.path.join(tree, "CMakeLists.txt"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + INCLUDE)
        time.sleep(1.1)
        rc, out = configure(tree, log, ["%s=ON" % NAME])
        scanned, found, unlisted, stale, bad, err = scan_tree(tree)
        ok, detail = judge_planted(rc, out, scanned, found, unlisted)
        record["steps"].append({"step": "planted", "rc": rc, "record": scanned, "error": err,
                                "said_on": SAYS_ON in out, "where": (found or {}).get(NAME),
                                "unlisted": unlisted, "ok": ok, "detail": detail})
        print("  %-4s %-10s %s" % ("ok" if ok else "FAIL", "planted", detail), flush=True)

        # set-first
        with open(os.path.join(tree, PLANTED), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(SET_FIRST_TEXT)
        time.sleep(1.1)
        rc, out = configure(tree, log, ["%s=ON" % NAME])
        scanned, found, unlisted, stale, bad, err = scan_tree(tree)
        ok2, detail2 = judge_set_first(rc, out, scanned, found, unlisted, stale, bad)
        record["steps"].append({"step": "set-first", "rc": rc, "record": scanned, "error": err,
                                "said_on": SAYS_ON in out, "unlisted": unlisted, "stale": stale,
                                "malformed": bad, "ok": ok2, "detail": detail2})
        print("  %-4s %-10s %s" % ("ok" if ok2 else "FAIL", "set-first", detail2), flush=True)
    finally:
        steps = record["steps"]
        record["verdict"] = ("PASS" if len(steps) == 3 and all(s["ok"] for s in steps) else "FAIL")
        record["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not a.keep:
            git(["worktree", "remove", "--force", wt], repo)
            record["removed"] = not os.path.exists(wt)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1)
    print("%s  (%s)" % (record["verdict"], a.out))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
