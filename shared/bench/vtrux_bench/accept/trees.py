"""The acceptance runner's trees, made from git objects (ACCEPT-SPEC A4).

Each tree the list names gets its own folder from the shared scratch helper
(vtrux_bench/scratch.py), held in the run's Made so it is removed with the
run (A14). In it, where the adapter's tree_paths(name, tree) puts them:

    the project      a clone of the repo (git clone --shared --no-checkout,
                     so no object is copied), checked out detached at the
                     tree's commit
    each component   another such clone, checked out at the commit the list
                     gives for that tree, else the project's pin

A clone reads git objects only: an uncommitted edit in the repo never
reaches a tree. Each checkout is refused (TreeError) unless its HEAD is
exactly its commit and `git status` shows no change, untracked files
included; the runner never edits it. The adapter points the project tree at
its shared checkouts (A15).

Each tree's record: per checkout the commit, the tree id and the sha256 of
every tracked file as checked out; and the product digest over the
adapter's product_files (vtrux_bench.run.engine.product_digest).

Local trees only so far; a tree on a remote host is refused until that part
is built.

RULES: each is switched off by a mutant in tests/test_accept_trees.py,
which must then fail.
"""

import hashlib
import os
import shutil
import subprocess

from .. import scratch as S
from ..run.engine import product_digest

RULES = {k: True for k in (
    "exact_checkout", "shared_checkout", "from_objects", "head_check", "clean_check",
    "untracked_check", "record_tree_id", "record_files", "record_digest", "layout",
    "made_tracked")}


class TreeError(Exception):
    pass


def _git(args, cwd=None):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise TreeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def clone_at(repo, commit, dest):
    """A clone of repo at dest, checked out detached at commit, made from
    git objects only. Line endings as the repo's .gitattributes say,
    whatever this machine's core.autocrlf."""
    _git(["clone", "--quiet", "--shared", "--no-checkout", "-c", "core.autocrlf=false",
          os.path.abspath(repo), dest])
    _git(["-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", commit],
         cwd=dest)


def check_tree(path, commit):
    """Refuse a checkout whose HEAD is not exactly commit, or that has any
    change, untracked files included."""
    head = _git(["rev-parse", "HEAD"], cwd=path).strip()
    if RULES["head_check"] and head != commit:
        raise TreeError("%s: HEAD is %s, not %s (A4)" % (path, head[:12], commit[:12]))
    st = _git(["status", "--porcelain", "--untracked-files=%s"
               % ("all" if RULES["untracked_check"] else "no")], cwd=path).strip()
    if RULES["clean_check"] and st:
        raise TreeError("%s has changes, so it is not its commit %s (A4):\n%s"
                        % (path, commit[:12], st))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def checkout_record(repo, path, commit):
    """{commit, tree_id, files: {repo-relative path: sha256}}."""
    tree_id = _git(["rev-parse", commit + "^{tree}"], cwd=repo).strip()
    rels = [r for r in _git(["ls-files", "-z"], cwd=path).split("\0") if r]
    if RULES["record_files"]:
        files = {r: _sha256(os.path.join(path, r)) for r in rels}
    else:
        files = {r: "" for r in rels}
    return {"commit": commit, "tree_id": tree_id if RULES["record_tree_id"] else commit,
            "files": files}


def _overlay_working_copy(repo, dest):
    """Only the from_objects mutant: the repo's working copy over a tree."""
    for d, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x != ".git"]
        for n in files:
            src = os.path.join(d, n)
            dst = os.path.join(dest, os.path.relpath(src, repo))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)


def make_trees(repo, alist, adapter, parent, made, say=print):
    """Make every tree of the list under parent. {name: {"dir", "project_dir",
    "shared_dirs" {component: dir}, "record"}}."""
    out = {}
    for name, t in sorted(alist.trees.items()):
        if t.get("host", "local") != "local":
            raise TreeError("tree %s is on host %s: remote trees are not built yet"
                            % (name, t["host"]))
        d = S.make(parent, "tree-" + name)
        if RULES["made_tracked"]:
            made.add(d)
        if RULES["layout"]:
            paths = adapter.tree_paths(name, t)
        else:
            paths = dict({"project": "p"}, **{c: "s-" + c for c in t["shared"]})
        pd = os.path.join(d, paths["project"])
        clone_at(repo, t["project"] if RULES["exact_checkout"] else "HEAD", pd)
        check_tree(pd, t["project"])
        if not RULES["from_objects"]:
            _overlay_working_copy(repo, pd)
        rec = {"project": checkout_record(repo, pd, t["project"]), "shared": {}}
        shared_dirs = {}
        for comp, c in sorted(t["shared"].items()):
            sd = os.path.join(d, paths[comp])
            clone_at(repo, c if RULES["shared_checkout"] else t["project"], sd)
            check_tree(sd, c)
            rec["shared"][comp] = checkout_record(repo, sd, c)
            shared_dirs[comp] = sd
        files = adapter.product_files(pd) if RULES["record_digest"] else list(rec["project"]["files"])
        rec["product_digest"] = product_digest(pd, files)
        rec["product_files"] = len(set(files))
        out[name] = {"dir": d, "project_dir": pd, "shared_dirs": shared_dirs, "record": rec}
        say("tree %s: project %s%s, product digest %s" % (
            name, t["project"][:12], "".join(", %s %s" % (c, v[:12]) for c, v in
                                             sorted(t["shared"].items())),
            rec["product_digest"][:12]))
    return out
