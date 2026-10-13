#!/usr/bin/env python3
"""Tests tools/prod_build.py's refusals against spec 12.3 item 3, on a throwaway tree.

Spec 12.3 item 3, "Prod builds are built on a clean tree" (user, 2026-10-05):
the prod-build script refuses to build if the working tree has uncommitted
changes, or if a downloaded component (`managed_components/`, which git
ignores) differs from its own `CHECKSUMS.json`, or if its `.component_hash`
differs from the one in `dependencies.lock`. "Uncommitted changes" includes
untracked files (reviewer's reading, review tracker 2026-10-05). Since 2026-10-12
(user) it also "refuses to build unless HEAD is on `gen-inhibit/main`".

This runs the script's `--check-only` mode (its refusals, no build) in a
throwaway tree at --commit, never in a working tree anyone uses. SINCE 2026-10-12
THE THROWAWAY TREE IS AN ISOLATED LOCAL CLONE, NOT A `git worktree`: the baseline
must be on `gen-inhibit/main`, a worktree shares the real repo's branch refs (that
branch is checked out elsewhere, and --self-test commits fakes, which would move
it), while a clone has refs of its own. In the clone, `gen-inhibit/main` is
created at --commit; nothing is ever pushed, and the clone is deleted afterwards.

  - BASELINE: the tree as checked out on `gen-inhibit/main`, with a copy of a
    verified `managed_components/` (--components) -> must pass (exit 0). A refusal here
    would make every case below meaningless, so the run stops.
  - each CASE makes one change, expects a refusal (exit != 0 and "NOT BUILT"),
    undoes the change, and expects a pass again -- so each refusal is shown to
    come from that change and not from the tree.
  - --check-only must never create a build directory.

IT TESTS THE SCRIPT AS COMMITTED at --commit, never anyone's working tree, so an
uncommitted fix to prod_build.py is invisible to it and the run reports the old
behaviour (the implementor hit exactly this, 2026-10-10). Commit first, then run;
the first output line names the commit tested.

The population is checked before anything is judged: the component must have
a CHECKSUMS.json listing at least one file, and the first listed file must
exist, or the component cases would pass on nothing.

--self-test runs the same cases against fake scripts: one that always passes,
one that always refuses, two that get the tree rule wrong, and two that get the
branch rule wrong (a substring match on the branch name; a detached HEAD at the
branch's tip accepted). Each must make this harness report FAIL; otherwise the
harness could not catch a script that ignores the tree or the branch.

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
# Refuses a dirty tree, but by the WRONG rule (as if the clean-tree check were gone
# and only the repo-identity check were left). Every tree row must fail.
FAKE_WRONG_RULE = ('import subprocess, sys\n'
                   'r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)\n'
                   'lines = [l for l in r.stdout.splitlines() if "fake_prod_build" not in l]\n'
                   'if lines:\n    print("REFUSED: this is not the repo")\n'
                   '    print("NOT BUILT.")\n    sys.exit(1)\n'
                   'print("CHECK ONLY: every refusal passed. Nothing was built.")\n')
FAKE_SILENT = ('import subprocess, sys\n'
               'r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)\n'
               'lines = [l for l in r.stdout.splitlines() if "fake_prod_build" not in l]\n'
               'if lines:\n    sys.exit(1)\n'
               'print("CHECK ONLY: every refusal passed. Nothing was built.")\n')


# The branch rule wrong two ways. Each refuses a dirty tree properly, so only its
# branch handling can be caught.
_FAKE_TREE_OK = ('import subprocess, sys\n'
                 'r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)\n'
                 'lines = [l for l in r.stdout.splitlines() if "fake_prod_build" not in l]\n'
                 'if lines:\n    print("REFUSED: the working tree has uncommitted changes")\n'
                 '    print("NOT BUILT.")\n    sys.exit(1)\n')
# Accepts any branch whose name CONTAINS gen-inhibit/main (gen-inhibit/main-old passes).
FAKE_BRANCH_SUBSTRING = (_FAKE_TREE_OK +
                         'b = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()\n'
                         'if "gen-inhibit/main" not in b:\n'
                         '    print("REFUSED: HEAD is on %r, not gen-inhibit/main" % b)\n'
                         '    print("NOT BUILT.")\n    sys.exit(1)\n'
                         'print("CHECK ONLY: every refusal passed. Nothing was built.")\n')
# Compares commits, not the branch: a detached HEAD at the branch's tip passes.
FAKE_BRANCH_BY_COMMIT = (_FAKE_TREE_OK +
                         'h = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()\n'
                         'm = subprocess.run(["git", "rev-parse", "gen-inhibit/main"], capture_output=True, text=True).stdout.strip()\n'
                         'if h != m:\n'
                         '    print("REFUSED: HEAD is not at gen-inhibit/main")\n'
                         '    print("NOT BUILT.")\n    sys.exit(1)\n'
                         'print("CHECK ONLY: every refusal passed. Nothing was built.")\n')

BRANCH = "gen-inhibit/main"


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def run_script(tree, script):
    r = subprocess.run([sys.executable, script, "--check-only"], cwd=tree,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


RULE_WORDS = {"tree": ("uncommitted",), "component": ("component",),
              "identity": ("not the", "repo"), "branch": (BRANCH,)}


def refused_for(rule, rc, txt):
    """A refusal by the named rule: non-zero exit and a REFUSED line carrying every
    word of that rule's cause phrase (case-insensitive). rule may be a tuple of
    rules, each of which must be named on some REFUSED line (a dirty tree on the
    wrong branch must report both). The branch rule's phrase is the branch the spec
    requires, `gen-inhibit/main`."""
    rules = rule if isinstance(rule, tuple) else (rule,)
    lines = [ln.lower() for ln in txt.splitlines() if ln.startswith("REFUSED")]
    return rc != 0 and all(any(all(w in ln for w in RULE_WORDS[r]) for ln in lines)
                           for r in rules)


def build_dirs(tree):
    return sorted(d for d in os.listdir(tree) if d.startswith("build")
                  and os.path.isdir(os.path.join(tree, d)))


class Edit:
    """One change to the tree, its undo, and the rule that must refuse it.

    rule "tree": the refusal must name uncommitted changes; "component": it must name
    the downloaded component; "identity": it must say the directory is not the repo.
    Matching the cause phrase (never the exact text) is what lets a case fail for the
    wrong reason: deleting main/gen_inhibit.c, a file the script used to recognise the
    repo, passed on the identity refusal and would have passed with the clean-tree
    check gone (implementor, 2026-10-10). Reviewer ruling the same day: a tracked file
    deleted is a dirty tree and must be refused as one, whichever file it is; a
    directory that is not the repo is refused by identity."""

    def __init__(self, name, apply, undo, rule):
        self.name, self.apply, self.undo, self.rule = name, apply, undo, rule


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
    can_c = os.path.join(tree, "main", "can.c")
    chash = os.path.join(comp, ".component_hash")
    readme = os.path.join(tree, "README.md")
    keep = {p: open(p, "rb").read() for p in (first, tracked, chash, readme, can_c)}
    extra_main = os.path.join(tree, "main", "untracked_probe.c")
    extra_comp = os.path.join(comp, "unlisted_probe.c")
    moved = first + ".moved_by_test"

    def flip_hash():
        h = keep[chash].decode().strip()
        restore_bytes(chash, (("0" if h[0] != "0" else "1") + h[1:]).encode())

    return [
        Edit("tracked file edited", lambda: append_byte(tracked),
             lambda: restore_bytes(tracked, keep[tracked]), "tree"),
        Edit("tracked edit staged", lambda: (append_byte(tracked), git(["add", tracked], tree)),
             lambda: (git(["reset", "-q", "--", tracked], tree),
                      restore_bytes(tracked, keep[tracked])), "tree"),
        Edit("tracked file deleted (README.md)", lambda: os.remove(readme),
             lambda: restore_bytes(readme, keep[readme]), "tree"),
        Edit("tracked source deleted (main/can.c)", lambda: os.remove(can_c),
             lambda: restore_bytes(can_c, keep[can_c]), "tree"),
        Edit("tracked source deleted (main/gen_inhibit.c)",
             lambda: os.remove(tracked), lambda: restore_bytes(tracked, keep[tracked]), "tree"),
        Edit("untracked file added", lambda: restore_bytes(extra_main, b"int x;\n"),
             lambda: os.remove(extra_main), "tree"),
        Edit("component file edited (one byte appended)", lambda: append_byte(first),
             lambda: restore_bytes(first, keep[first]), "component"),
        Edit("component file missing", lambda: os.rename(first, moved),
             lambda: os.rename(moved, first), "component"),
        Edit("component carries an unlisted file", lambda: restore_bytes(extra_comp, b"int y;\n"),
             lambda: os.remove(extra_comp), "component"),
        Edit(".component_hash differs from dependencies.lock", flip_hash,
             lambda: restore_bytes(chash, keep[chash]), "component"),
    ] + branch_cases(tree, tracked, keep)


def branch_cases(tree, tracked, keep):
    """Spec 12.3 item 3 (2026-10-12): refuses unless HEAD is on gen-inhibit/main. Each
    case leaves the files as they are and moves HEAD only; its undo checks the branch
    out again and deletes the probe branch. The names are chosen against a match on
    part of the name: one that contains it, one that ends with it."""
    def on(name):
        return lambda: git(["checkout", "-q", "-b", name], tree)

    def back(name=None):
        def u():
            git(["checkout", "-q", BRANCH], tree)
            if name:
                git(["branch", "-q", "-D", name], tree)
        return u

    def dirty_other():
        git(["checkout", "-q", "-b", "tester-probe-dirty"], tree)
        append_byte(tracked)

    def undo_dirty_other():
        restore_bytes(tracked, keep[tracked])
        back("tester-probe-dirty")()

    return [
        Edit("on another branch, same commit", on("tester-probe-other"),
             back("tester-probe-other"), "branch"),
        Edit("detached HEAD at gen-inhibit/main's tip",
             lambda: git(["checkout", "-q", "--detach"], tree), back(), "branch"),
        Edit("on a branch whose name contains it (gen-inhibit/main-old)",
             on("gen-inhibit/main-old"), back("gen-inhibit/main-old"), "branch"),
        Edit("on a branch whose name ends with it (probe/gen-inhibit/main)",
             on("probe/gen-inhibit/main"), back("probe/gen-inhibit/main"), "branch"),
        Edit("dirty tree on another branch: both rules named", dirty_other,
             undo_dirty_other, ("tree", "branch")),
    ]


def check_all(tree, script, comp):
    """Run baseline and every case. Returns [(label, ok, detail)]."""
    out = []
    rc, txt = run_script(tree, script)
    base_ok = rc == 0 and "every refusal passed" in txt
    cur = git(["symbolic-ref", "--quiet", "--short", "HEAD"], tree).stdout.strip()
    out.append(("baseline: clean tree on %s with a verified component passes" % BRANCH,
                base_ok and cur == BRANCH, "rc %d, HEAD on %r" % (rc, cur)))
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
        out.append(("refused: %s" % e.name, refused_for(e.rule, rc, txt),
                    "rc %d; %s" % (rc, why[0] if why else "no REFUSED line")))
        rc2, _ = run_script(tree, script)
        out.append(("  passes again once undone", rc2 == 0, "rc %d" % rc2))
    out.append(("--check-only created no build directory", build_dirs(tree) == [],
                str(build_dirs(tree))))
    out.append(wrong_directory(tree, script, nested=False))
    out.append(wrong_directory(tree, script, nested=True))
    return out


def wrong_directory(tree, script, nested):
    """The script, copied into a directory that is not the repo, must refuse by identity.

    Two placements, both must refuse by identity (reviewer ruling 2026-10-10: a
    directory that is not the repo is refused by identity):
      - SIBLING of the throwaway tree, outside every git repository (the harness
        refuses to judge if git still finds one there);
      - NESTED inside the throwaway tree, a git work tree whose HEAD carries every
        marker the script looks for. Git discovery walks up, so a script that asks
        git whether a missing marker is tracked gets the outer tree's answer. The
        first version of this case put its probe there by accident, went red against
        ddddfea, and was moved out with a docstring calling the refusal "rightly" a
        dirty tree; it was a defect in prod_build.py's check_repo(), fixed in 13709ab
        (implementor). Red against ddddfea, green from 13709ab."""
    where = "nested inside the throwaway tree" if nested else "sibling, outside any repo"
    label = "refused: run from a directory that is not the repo (%s), by identity" % where
    other = os.path.join(tree, "nested_not_a_repo_probe") if nested else tree + "-not-a-repo"
    os.makedirs(os.path.join(other, "tools"))
    try:
        inside = git(["rev-parse", "--show-toplevel"], other)
        top = os.path.normcase(os.path.normpath(inside.stdout.strip())) if inside.returncode == 0 else None
        want = os.path.normcase(os.path.normpath(tree)) if nested else None
        if top != want:
            return (label, False, "probe dir's git top is %r, wanted %r; case not run" % (top, want))
        copy = os.path.join(other, "tools", os.path.basename(script))
        shutil.copyfile(script, copy)
        r = subprocess.run([sys.executable, copy, "--check-only"], cwd=other,
                           capture_output=True, text=True)
        txt = r.stdout + r.stderr
        why = [ln.strip()[:110] for ln in txt.splitlines() if ln.startswith("REFUSED")]
        return (label,
                refused_for("identity", r.returncode, txt),
                "rc %d; %s" % (r.returncode, why[0] if why else "no REFUSED line"))
    finally:
        shutil.rmtree(other, ignore_errors=True)


def make_tree(repo, commit, components, where):
    """An isolated local clone with its own gen-inhibit/main at commit. Its refs are
    its own: nothing done in it reaches the real repo's branches."""
    full = git(["rev-parse", "--verify", commit + "^{commit}"], repo).stdout.strip()
    if not full:
        raise SystemExit("cannot resolve %s" % commit)
    r = git(["clone", "-q", "--no-checkout", "--no-hardlinks", repo, where], os.path.dirname(where))
    if r.returncode != 0:
        raise SystemExit("clone failed: %s" % r.stderr.strip())
    git(["remote", "remove", "origin"], where)
    r = git(["checkout", "-q", "-B", BRANCH, full], where)
    if r.returncode != 0:
        raise SystemExit("checkout failed: %s" % r.stderr.strip())
    git(["config", "user.name", "prod-build refusal test"], where)
    git(["config", "user.email", "test@invalid"], where)
    dst = os.path.join(where, "managed_components")
    shutil.copytree(components, dst)
    return dst


def _writable(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)


def drop_tree(repo, where):
    shutil.rmtree(where, onerror=_writable)


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
                                      FAKE_SILENT, "refused: tracked"),
                                     ("refuses a dirty tree by the wrong rule",
                                      FAKE_WRONG_RULE, "refused: tracked"),
                                     ("accepts a branch whose name contains gen-inhibit/main",
                                      FAKE_BRANCH_SUBSTRING, "refused: on a branch whose name contains"),
                                     ("accepts a detached HEAD at the branch's tip",
                                      FAKE_BRANCH_BY_COMMIT, "refused: detached HEAD")):
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
