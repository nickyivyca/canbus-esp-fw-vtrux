#!/usr/bin/env python3
"""Spec 12.4 "no stale image name" (12.3 item 3, "Every build names its image
from the tree it compiled"; added 2026-10-13, user).

Run from a shell where ESP-IDF is exported (export.ps1 on Windows), because it
drives real builds:

    python stale_name_check.py --commit <sha> --components <a tree's managed_components> --out run.json

WHAT THE SPEC ASKS. Starting from a clean build, edit a source file and rebuild
with `idf.py build`, then the same with `ninja` directly; each image's embedded
version and name read `<commit>-dirty`. Revert the edit and rebuild: the clean
name comes back. Commit and rebuild: the new commit's name.

THE STEPS, each one a rebuild of the same build directory:
    clean         a fresh tree, `idf.py build`                  -> <C>
    edit/idf      a source edited, `idf.py build`               -> <C>-dirty
    revert/idf    the edit reverted, `idf.py build`             -> <C>
    edit/ninja    the same edit, `ninja -C build`               -> <C>-dirty
    revert/ninja  reverted, `ninja -C build`                    -> <C>
    commit/ninja  an edit committed (C1), `ninja -C build`      -> <C1>
    commit/idf    another edit committed (C2), `idf.py build`   -> <C2>
    readme-edit/ninja    README.md edited, `ninja -C build`     -> <C2>-dirty
    readme-revert/ninja  reverted, `ninja -C build`             -> <C2>
    readme-commit/ninja  a README.md-only commit (C3), `ninja`  -> <C3>
The source edit is a comment appended to main/gen_inhibit.c, so every build
compiles. The three README.md steps are readings of the 12.3 text beyond the
12.4 row (reviewer, 2026-10-13): an edit to a tracked file that is not compiled
is an uncommitted change, and a commit touching only README.md is a git-state
change. In those steps nothing compiled changes, so a build that does not
re-run the naming writes no image at all -- and that fails, as "re-runs the
naming before the build" requires (reviewer).
    nogit/ninja          git cannot answer, `ninja -C build`    -> the build fails
                                                                   and writes no image
The last step is the 12.3 text's "if the naming can't be re-run, the build fails
rather than producing an image under a stale name" (reviewer: show it once).
The worktree's .git is moved aside, GIT_CEILING_DIRECTORIES stops git looking
above the tree (checked: git rev-parse must fail there first), and a source
edit is made so that a build that ignored git's failure would recompile and
write an image. Then .git and the edit are put back.

WHAT IS JUDGED, for each step: the image this build wrote (a top-level
build/*.bin of this project, modified during the step -- exactly one, or the
step is undetermined), and three readings of it, each of which must carry the
expected commit and the expected -dirty state:
    the filename, the esp_app_desc_t version (0x10), and its project_name (0x30).
The expected commit is HEAD's full sha, which the reading's abbreviation must
be a prefix of; the expected dirty state is what this script did to the tree,
cross-checked against `git status`. Anything undetermined fails: a build that
failed, no image written, two images written, an unreadable app descriptor.

RUN IT IN A THROWAWAY TREE, never one anyone uses (reviewer, 2026-10-13): the
script makes a detached worktree of --commit beside the repo, copies
managed_components/ in, commits there on a detached HEAD (no branch moves), and
removes the worktree afterwards (--keep keeps it).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EDIT_FILE = "main/gen_inhibit.c"
README = "README.md"
# The commit token in a filename or project_name: wican-fw_obd_<ver>_man[...]
NAME_VER = re.compile(r"_([0-9a-f]{7,40}(?:-dirty)?)_(?:man|auto)(?=_|\.bin$|$)")
VERSION = re.compile(r"^([0-9a-f]{7,40})(-dirty)?$")
NOT_APP = ("ota_data_initial.bin",)


def parse_version(s):
    """(abbrev, dirty) from '<hex>' or '<hex>-dirty', else None."""
    m = VERSION.match(s or "")
    return (m.group(1), bool(m.group(2))) if m else None


def name_version(name):
    """The commit token of a filename or project_name, else None."""
    m = NAME_VER.search(name or "")
    return m.group(1) if m else None


def carries(reading, full_sha, dirty):
    """True if a reading names full_sha (as a prefix of >= 7) with exactly this dirty state."""
    p = parse_version(reading)
    return bool(p) and full_sha.startswith(p[0]) and p[1] == dirty


def written_images(build_dir, since):
    """This project's top-level images modified at or after `since`."""
    out = []
    for f in sorted(os.listdir(build_dir)) if os.path.isdir(build_dir) else []:
        p = os.path.join(build_dir, f)
        if (f.endswith(".bin") and f not in NOT_APP and f.startswith("wican-fw_")
                and os.path.isfile(p) and os.path.getmtime(p) >= since):
            out.append(p)
    return out


def step_start(gap=1.1, sleep=time.sleep):
    """The time a step's window opens: AFTER a gap, so the previous step's image
    (written moments ago) is older than it. The first version opened the window
    a second BEFORE the step, and on 95619b7 that counted revert/ninja's image as
    written by commit/ninja, failing it for two images (2026-10-13)."""
    sleep(gap)
    return time.time()


def judge(rc, images, desc, full_sha, dirty):
    """(ok, detail). ok is False for every undetermined case, never None."""
    if rc != 0:
        return False, "the build failed (rc %d)" % rc
    if len(images) != 1:
        return False, "%d images written by this build (%s); one is required" % (
            len(images), ", ".join(os.path.basename(p) for p in images) or "none")
    if not desc:
        return False, "no esp_app_desc_t in %s" % os.path.basename(images[0])
    version, project = desc[0], desc[1]
    readings = {"filename": name_version(os.path.basename(images[0])),
                "version": version, "project_name": name_version(project)}
    want = full_sha[:7] + ("-dirty" if dirty else "")
    bad = ["%s %r" % (k, v) for k, v in readings.items() if not carries(v, full_sha, dirty)]
    if bad:
        return False, "want %s; %s" % (want, ", ".join(bad))
    return True, "all three read %s" % readings["version"]


def judge_nogit(git_answered, rc, images):
    """(ok, detail) for the step where git cannot answer: the build must fail and
    write no image. If git still answered, the step proves nothing and fails."""
    if git_answered:
        return False, "git still answered in the tree, so this step tests nothing"
    if images:
        return False, "an image was written (%s)" % ", ".join(os.path.basename(p) for p in images)
    if rc == 0:
        return False, "the build succeeded without git"
    return True, "the build failed (rc %d) and wrote no image" % rc


def git(args, cwd, env=None):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, env=env)


def build(tree, how, log, env=None):
    if how == "idf":
        cmd = [sys.executable, os.path.join(os.environ["IDF_PATH"], "tools", "idf.py"), "build"]
    else:
        cmd = ["ninja", "-C", "build"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=env)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("\n===== %s (rc %d, %.1f s)\n" % (" ".join(cmd[-2:]), r.returncode, time.time() - t0))
        fh.write(r.stdout[-20000:])
        fh.write(r.stderr[-20000:])
    return r.returncode, time.time() - t0


def append_probe(tree, n, rel=EDIT_FILE):
    line = "/* stale-name probe %d */\n" if rel.endswith((".c", ".h")) else "stale-name probe %d\n"
    with open(os.path.join(tree, rel), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line % n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--components", required=True, help="a managed_components/ directory to copy in")
    ap.add_argument("--out", required=True, help="JSON record; the build log goes beside it (.log)")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway worktree")
    a = ap.parse_args()
    if "IDF_PATH" not in os.environ:
        print("FAIL: IDF_PATH is not set -- run from a shell where ESP-IDF is exported")
        return 2
    import build_output as BO

    repo = git(["rev-parse", "--show-toplevel"], HERE).stdout.strip()
    c0 = git(["rev-parse", a.commit], repo).stdout.strip()
    tree = os.path.join(os.path.dirname(repo), "wican-stalename-%s-%s" % (c0[:7], time.strftime("%Y%m%dT%H%M%S")))
    log = os.path.splitext(a.out)[0] + ".log"
    r = git(["worktree", "add", "--detach", tree, c0], repo)
    if r.returncode != 0:
        print("FAIL: worktree add: %s" % r.stderr.strip())
        return 2
    shutil.copytree(a.components, os.path.join(tree, "managed_components"))
    ident = ["-c", "user.name=stale-name-check", "-c", "user.email=stale-name-check@invalid"]
    original = {rel: open(os.path.join(tree, rel), "rb").read() for rel in (EDIT_FILE, README)}

    def revert(rel=EDIT_FILE):
        with open(os.path.join(tree, rel), "wb") as fh:
            fh.write(original[rel])

    def commit(n, rel=EDIT_FILE):
        append_probe(tree, n, rel)
        git(ident + ["commit", "-q", "--no-verify", "-am", "stale-name probe %d" % n], tree)

    steps = [
        ("clean", "idf", lambda: None, False),
        ("edit/idf", "idf", lambda: append_probe(tree, 1), True),
        ("revert/idf", "idf", revert, False),
        ("edit/ninja", "ninja", lambda: append_probe(tree, 1), True),
        ("revert/ninja", "ninja", revert, False),
        ("commit/ninja", "ninja", lambda: commit(2), False),
        ("commit/idf", "idf", lambda: commit(3), False),
        ("readme-edit/ninja", "ninja", lambda: append_probe(tree, 4, README), True),
        ("readme-revert/ninja", "ninja", lambda: revert(README), False),
        ("readme-commit/ninja", "ninja", lambda: commit(5, README), False),
    ]
    record = {"commit": c0, "tree": os.path.basename(tree), "steps": [],
              "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    fails = []
    try:
        for name, how, change, dirty in steps:
            change()
            head = git(["rev-parse", "HEAD"], tree).stdout.strip()
            porcelain = git(["status", "--porcelain", "--untracked-files=no"], tree).stdout.strip()
            since = step_start()
            rc, secs = build(tree, how, log)
            images = written_images(os.path.join(tree, "build"), since)
            desc = BO.app_desc(images[0]) if len(images) == 1 else None
            ok, detail = judge(rc, images, desc, head, dirty)
            if bool(porcelain) != dirty:
                ok, detail = False, "the tree's state is not the step's: git status %r" % porcelain
            rec = {"step": name, "build": how, "head": head, "dirty": dirty, "rc": rc,
                   "seconds": round(secs, 1), "images": [os.path.basename(p) for p in images],
                   "md5": hashlib.md5(open(images[0], "rb").read()).hexdigest() if len(images) == 1 else None,
                   "version": desc[0] if desc else None, "project_name": desc[1] if desc else None,
                   "ok": ok, "detail": detail}
            record["steps"].append(rec)
            print("  %-4s %-13s %s" % ("ok" if ok else "FAIL", name, detail), flush=True)
            if not ok:
                fails.append(name)
            if name == "clean" and not ok:
                print("  the clean build is the precondition; stopping")
                break
        else:
            dotgit, hidden = os.path.join(tree, ".git"), os.path.join(tree, ".git.stale-name-off")
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(tree))
            before = open(os.path.join(tree, EDIT_FILE), "rb").read()
            append_probe(tree, 6)
            os.rename(dotgit, hidden)
            try:
                answered = git(["rev-parse", "HEAD"], tree, env).returncode == 0
                since = step_start()
                rc, secs = build(tree, "ninja", log, env)
                images = written_images(os.path.join(tree, "build"), since)
            finally:
                os.rename(hidden, dotgit)
                with open(os.path.join(tree, EDIT_FILE), "wb") as fh:
                    fh.write(before)
            ok, detail = judge_nogit(answered, rc, images)
            record["steps"].append({"step": "nogit/ninja", "build": "ninja", "git_answered": answered,
                                    "rc": rc, "seconds": round(secs, 1),
                                    "images": [os.path.basename(p) for p in images], "ok": ok, "detail": detail})
            print("  %-4s %-13s %s" % ("ok" if ok else "FAIL", "nogit/ninja", detail), flush=True)
            if not ok:
                fails.append("nogit/ninja")
    finally:
        record["verdict"] = "PASS" if not fails and len(record["steps"]) == len(steps) + 1 else "FAIL"
        record["failed"] = fails
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1)
        if not a.keep:
            git(["worktree", "remove", "--force", tree], repo)
            if os.path.exists(tree):
                shutil.rmtree(tree, ignore_errors=True)
            git(["worktree", "prune"], repo)
    print(record["verdict"] + ("" if not fails else ": " + ", ".join(fails)))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
