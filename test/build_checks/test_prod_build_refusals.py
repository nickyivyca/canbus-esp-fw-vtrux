#!/usr/bin/env python3
"""Tests tools/prod_build.py's refusals against spec 12.3 item 3, on a throwaway tree.

Spec 12.3 item 3, "Prod builds are built on a clean tree" (user, 2026-10-05):
the prod-build script refuses to build if the working tree has uncommitted
changes, or if a downloaded component (`managed_components/`, which git
ignores) differs from its own `CHECKSUMS.json`, or if its `.component_hash`
differs from the one in `dependencies.lock`. "Uncommitted changes" includes
untracked files (reviewer's reading, review tracker 2026-10-05).

This runs the script's `--check-only` mode (its refusals, no build) in a
throwaway `git worktree` at --commit, never in a working tree anyone uses:

  - BASELINE: the tree as checked out, with a copy of a verified
    `managed_components/` (--components) -> must pass (exit 0). A refusal here
    would make every case below meaningless, so the run stops.
  - each CASE makes one change, expects a refusal (exit != 0 and "NOT BUILT"),
    undoes the change, and expects a pass again -- so each refusal is shown to
    come from that change and not from the tree.
  - --check-only must never create a build directory.

The population is checked before anything is judged: the component must have
a CHECKSUMS.json listing at least one file, and the first listed file must
exist, or the component cases would pass on nothing.

--self-test runs the same cases against two fake scripts, one that always
passes and one that always refuses. Each must make this harness report FAIL;
otherwise the harness could not catch a script that ignores the tree.

    py -3.14 test_prod_build_refusals.py --components <a tree's managed_components>
    py -3.14 test_prod_build_refusals.py --components <...> --self-test
Prints one line per check and PASS/FAIL; exit 0 only on PASS. Writes nothing
outside the throwaway tree, which it removes.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

FAKE_PASS = 'print("CHECK ONLY: every refusal passed. Nothing was built.")\n'
FAKE_REFUSE = ('import sys\nprint("NOT BUILT. (fake)")\nsys.exit(1)\n')
# Passes a clean tree, and on a dirty one exits 1 WITHOUT saying it refused (a crash
# looks like this). Only components then pass, so every tree row must fail: a
# non-zero exit alone is not a refusal.
FAKE_SILENT = ('import subprocess, sys\n'
               'r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)\n'
               'lines = [l for l in r.stdout.splitlines() if "fake_prod_build" not in l]\n'
               'if lines:\n    sys.exit(1)\n'
               'print("CHECK ONLY: every refusal passed. Nothing was built.")\n')


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def run_script(tree, script):
    r = subprocess.run([sys.executable, script, "--check-only"], cwd=tree,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def refused(rc, txt):
    """A refusal is a non-zero exit that says so. The script ends a refusal of the
    tree or a component with "NOT BUILT"; a tree it does not recognise as the repo
    (e.g. main/gen_inhibit.c gone) is refused earlier, with a "REFUSED:" line and no
    summary, which is a refusal all the same (2026-10-10)."""
    return rc != 0 and ("NOT BUILT" in txt or "REFUSED" in txt)


def build_dirs(tree):
    return sorted(d for d in os.listdir(tree) if d.startswith("build")
                  and os.path.isdir(os.path.join(tree, d)))


class Edit:
    """One change to the tree, and its undo."""

    def __init__(self, name, apply, undo):
        self.name, self.apply, self.undo = name, apply, undo


def append_byte(path):
    with open(path, "ab") as f:
        f.write(b"\n")


def restore_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def cases(tree, comp):
    """The edits. comp is the component dir inside the throwaway tree."""
    listed = json.load(open(os.path.join(comp, "CHECKSUMS.json"), encoding="utf-8"))["files"]
    first = os.path.join(comp, listed[0]["path"])
    tracked = os.path.join(tree, "main", "gen_inhibit.c")
    chash = os.path.join(comp, ".component_hash")
    readme = os.path.join(tree, "README.md")
    keep = {p: open(p, "rb").read() for p in (first, tracked, chash, readme)}
    extra_main = os.path.join(tree, "main", "untracked_probe.c")
    extra_comp = os.path.join(comp, "unlisted_probe.c")
    moved = first + ".moved_by_test"

    def flip_hash():
        h = keep[chash].decode().strip()
        restore_bytes(chash, (("0" if h[0] != "0" else "1") + h[1:]).encode())

    return [
        Edit("tracked file edited", lambda: append_byte(tracked),
             lambda: restore_bytes(tracked, keep[tracked])),
        Edit("tracked edit staged", lambda: (append_byte(tracked), git(["add", tracked], tree)),
             lambda: (git(["reset", "-q", "--", tracked], tree),
                      restore_bytes(tracked, keep[tracked]))),
        Edit("tracked file deleted (README.md)", lambda: os.remove(readme),
             lambda: restore_bytes(readme, keep[readme])),
        Edit("tracked source deleted (main/gen_inhibit.c)", lambda: os.remove(tracked),
             lambda: restore_bytes(tracked, keep[tracked])),
        Edit("untracked file added", lambda: restore_bytes(extra_main, b"int x;\n"),
             lambda: os.remove(extra_main)),
        Edit("component file edited (one byte appended)", lambda: append_byte(first),
             lambda: restore_bytes(first, keep[first])),
        Edit("component file missing", lambda: os.rename(first, moved),
             lambda: os.rename(moved, first)),
        Edit("component carries an unlisted file", lambda: restore_bytes(extra_comp, b"int y;\n"),
             lambda: os.remove(extra_comp)),
        Edit(".component_hash differs from dependencies.lock", flip_hash,
             lambda: restore_bytes(chash, keep[chash])),
    ]


def check_all(tree, script, comp):
    """Run baseline and every case. Returns [(label, ok, detail)]."""
    out = []
    rc, txt = run_script(tree, script)
    base_ok = rc == 0 and "every refusal passed" in txt
    out.append(("baseline: clean tree with a verified component passes", base_ok,
                "rc %d" % rc))
    if not base_ok:
        out.append(("baseline output", False, txt[-800:]))
        return out
    for e in cases(tree, comp):
        e.apply()
        try:
            rc, txt = run_script(tree, script)
        finally:
            e.undo()
        why = [ln.strip()[:110] for ln in txt.splitlines() if ln.startswith("REFUSED")]
        out.append(("refused: %s" % e.name, refused(rc, txt),
                    "rc %d; %s" % (rc, why[0] if why else "no REFUSED line")))
        rc2, _ = run_script(tree, script)
        out.append(("  passes again once undone", rc2 == 0, "rc %d" % rc2))
    out.append(("--check-only created no build directory", build_dirs(tree) == [],
                str(build_dirs(tree))))
    return out


def make_tree(repo, commit, components, where):
    r = git(["worktree", "add", "--detach", where, commit], repo)
    if r.returncode != 0:
        raise SystemExit("worktree add failed: %s" % r.stderr.strip())
    dst = os.path.join(where, "managed_components")
    shutil.copytree(components, dst)
    return dst


def drop_tree(repo, where):
    git(["worktree", "remove", "--force", where], repo)
    if os.path.exists(where):
        shutil.rmtree(where, ignore_errors=True)
    git(["worktree", "prune"], repo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--components", required=True,
                    help="a managed_components/ directory to copy in (verified separately)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    repo = git(["rev-parse", "--show-toplevel"], HERE).stdout.strip()
    sha = git(["rev-parse", "--short", a.commit], repo).stdout.strip()
    where = os.path.join(os.path.dirname(repo), "wican-prodbuild-refusals-%s"
                         % time.strftime("%Y%m%dT%H%M%S"))
    comp_src = os.path.join(a.components, "espressif__mdns")
    ck = os.path.join(comp_src, "CHECKSUMS.json")
    if not os.path.exists(ck):
        print("FAIL: %s has no espressif__mdns/CHECKSUMS.json" % a.components)
        return 2
    listed = json.load(open(ck, encoding="utf-8")).get("files") or []
    if not listed or not os.path.exists(os.path.join(comp_src, listed[0]["path"])):
        print("FAIL: the component lists no files, or its first listed file is absent")
        return 2

    print("commit %s, throwaway tree %s, component lists %d files"
          % (sha, os.path.basename(where), len(listed)))
    make_tree(repo, a.commit, a.components, where)
    comp = os.path.join(where, "managed_components", "espressif__mdns")
    fails = []
    try:
        real = os.path.join(where, "tools", "prod_build.py")
        if a.self_test:
            for name, body, must in (("always passes", FAKE_PASS, "refused:"),
                                     ("always refuses", FAKE_REFUSE, "baseline:"),
                                     ("exits 1 on a dirty tree without saying so",
                                      FAKE_SILENT, "refused: tracked")):
                fake = os.path.join(where, "fake_prod_build.py")
                restore_bytes(fake, body.encode())
                git(["update-index", "--add", "--cacheinfo", "100644",
                     git(["hash-object", "-w", fake], where).stdout.strip(),
                     "fake_prod_build.py"], where)
                git(["commit", "-q", "-m", "fake", "--no-verify"], where)
                res = check_all(where, fake, comp)
                caught = any(not ok for label, ok, _ in res if label.startswith(must))
                print("  %-4s self-test: a script that %s is caught" % ("ok" if caught else "FAIL", name))
                if not caught:
                    fails.append(name)
                git(["reset", "-q", "--hard", "HEAD~1"], where)
        else:
            for label, ok, detail in check_all(where, real, comp):
                print("  %-4s %s  (%s)" % ("ok" if ok else "FAIL", label, detail))
                if not ok:
                    fails.append(label)
    finally:
        drop_tree(repo, where)
    print("throwaway tree removed: %s" % (not os.path.exists(where)))
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
