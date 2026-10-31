#!/usr/bin/env python3
"""canbench, the CAN adapter package, at the one can-tools commit the
harness pins.

Charge-interposer tester, 2026-10-30: the canbench cutover. canbench comes
from the can-tools clone named by CAN_TOOLS_DIR (AGENTS.md, "can-tools: the
CAN adapter package", user 2026-10-30), and each project pins its own
can-tools commit (user ruling, 2026-10-30, via the reviewer). Ours is the
"can-tools" entry of pins.json beside this file, with the repo and the full
commit, next to canre's.

The clone itself is not what a run loads. It moves when anyone pulls it,
and it is also what a bare `import canbench` finds on this machine (the
can-tools.pth in the user site-packages, for reverse-it's scripts). So the
pinned commit's canbench/ is written out of the clone's git objects into a
folder of its own, and loaded from there by file:

    <worktree>/scratch/can-tools/<commit>/canbench/   (scratch/ is gitignored)

    py -3.14 -B charge-interposer/can_tools_pin.py install   # once per worktree
    py -3.14 -B charge-interposer/can_tools_pin.py check     # writes nothing
    py -3.14 -B charge-interposer/can_tools_pin.py where     # prints the folder

install writes the raw blobs (`git cat-file blob`: no line-ending or other
filters) into a folder beside the target first, and moves it into place
only once it passes the check below. A target that is there already is
checked and never replaced: one that fails is refused, and deleting it is
left to a person. The clone's HEAD, branch and working tree play no part.

THE CHECKS (use(), check, and the end of install). A run's canbench is the
pinned commit's or the harness refuses it:
  1. CAN_TOOLS_DIR is set, and holds canbench/ (it is the can-tools clone);
  2. the clone has the pinned commit, and that commit has a canbench/ of
     plain files;
  3. the folder exists (if not, the refusal names the install command), and
     every file in its canbench/ has the git blob hash of the same path at
     the pinned commit: none missing, none changed, none added
     (__pycache__ aside);
  4. canbench is loaded by file from that folder, which is never put on
     sys.path, nor is the clone. A canbench imported earlier from anywhere
     else (the clone through the .pth, reverse-it's old mirror) is refused.

The repo in the pin is recorded, not compared with the clone's remote: the
commit hash alone fixes the content. Only the bench host needs this; the
harness's tests build fake clones (can_tools_pin_test.py).
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(HERE, "pins.json")
START = HERE            # where folder() looks for the worktree; tests move it
KEY = "can-tools"
PACKAGE = "canbench"
ENV = "CAN_TOOLS_DIR"


class CanToolsPinError(RuntimeError):
    pass


def pin(path=None):
    """pins.json's can-tools entry: {repo, commit}, the commit in full."""
    with open(path or PINS_FILE, encoding="utf-8") as fh:
        p = json.load(fh).get(KEY)
    if not (isinstance(p, dict) and all(isinstance(p.get(k), str) and p[k]
                                        for k in ("repo", "commit"))):
        raise CanToolsPinError("pins.json has no complete \"%s\" entry "
                               "(repo, commit)" % KEY)
    c = p["commit"]
    if len(c) != 40 or c.strip("0123456789abcdef"):
        raise CanToolsPinError("pins.json's %s commit %r is not a full commit "
                               "(40 lower-case hex digits)" % (KEY, c))
    return p


def clone(environ=None):
    """The can-tools clone, from CAN_TOOLS_DIR (check 1)."""
    v = (os.environ if environ is None else environ).get(ENV, "").strip()
    if not v:
        raise CanToolsPinError(
            "CAN_TOOLS_DIR is not set. Set it to the absolute path of the "
            "can-tools clone on this machine (canbench/ inside it); "
            "reverse-it's canbench/ mirror is not read. See "
            "charge-interposer/paths.py.")
    v = os.path.normpath(v)
    if not os.path.isfile(os.path.join(v, PACKAGE, "__init__.py")):
        raise CanToolsPinError("CAN_TOOLS_DIR=%s has no canbench/ -- it must "
                               "be the can-tools clone" % v)
    return v


def worktree(start=None, exists=os.path.exists):
    """The git worktree holding start (default START), found by walking up
    to its .git; None in a copy with no git."""
    d = os.path.abspath(start or START)
    while True:
        if exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def folder(p=None, start=None):
    """The folder of the pinned commit: <worktree>/scratch/can-tools/<commit>."""
    wt = worktree(start)
    if wt is None:
        raise CanToolsPinError("%s is in no git worktree, so it has no "
                               "scratch/can-tools/: canbench is pinned only "
                               "in a worktree" % os.path.abspath(start or START))
    return os.path.join(wt, "scratch", "can-tools", (p or pin())["commit"])


def install_command():
    return "%s -B %s install" % (sys.executable, os.path.abspath(__file__))


def _git(c, args, text=True):
    r = subprocess.run(["git", "-C", c] + args, capture_output=True,
                       text=text, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def tree(c, p):
    """{path under canbench/, posix: blob hash} at the pinned commit (check 2)."""
    commit = p["commit"]
    rc, _, _ = _git(c, ["cat-file", "-e", commit + "^{commit}"])
    if rc:
        raise CanToolsPinError("the can-tools clone at %s has no commit %s (the "
                               "pin): fetch it there (git -C %s fetch)"
                               % (c, commit, c))
    rc, out, err = _git(c, ["ls-tree", "-r", "-z", "--full-tree", commit,
                            "--", PACKAGE])
    if rc:
        raise CanToolsPinError("git ls-tree of %s in %s failed: %s"
                               % (commit[:7], c, err.strip()))
    files, odd = {}, []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            odd.append("%s (%s %s)" % (path, mode, kind))
            continue
        files[path[len(PACKAGE) + 1:]] = sha
    if odd:
        raise CanToolsPinError("can-tools %s has entries under canbench/ that "
                               "are not plain files: %s" % (commit[:7], odd))
    if "__init__.py" not in files:
        raise CanToolsPinError("can-tools %s has no canbench/__init__.py"
                               % commit[:7])
    return files


def blob_hash(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def verify(d, p=None, c=None):
    """Checks 1 to 3 on the folder d. Returns d."""
    p = p or pin()
    want = tree(c or clone(), p)
    pkg = os.path.join(d, PACKAGE)
    if not os.path.isdir(pkg):
        raise CanToolsPinError("can-tools %s (the pin) is not installed at %s: "
                               "run %s" % (p["commit"][:7], d, install_command()))
    have = {}
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [x for x in dns if x != "__pycache__"]
        for f in fns:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, pkg).replace(os.sep, "/")
            with open(full, "rb") as fh:
                have[rel] = blob_hash(fh.read())
    missing = sorted(set(want) - set(have))
    added = sorted(set(have) - set(want))
    changed = sorted(k for k in set(want) & set(have) if want[k] != have[k])
    if missing or added or changed:
        raise CanToolsPinError("%s is not can-tools %s's canbench/: missing %s; "
                               "changed %s; not in it %s"
                               % (d, p["commit"][:7], missing, changed, added))
    return d


def _same(a, b):
    return a is not None and (os.path.normcase(os.path.realpath(a)) ==
                              os.path.normcase(os.path.realpath(b)))


def use(d=None):
    """All four checks; canbench loaded by file from the folder. Returns the
    module."""
    d = d or folder()
    verify(d)
    pkg = os.path.join(d, PACKAGE)
    init = os.path.join(pkg, "__init__.py")
    have = sys.modules.get(PACKAGE)
    if have is not None:
        if not _same(getattr(have, "__file__", None), init):
            raise CanToolsPinError("canbench is already imported from %s, not "
                                   "the pinned %s (CAN_TOOLS_DIR)"
                                   % (getattr(have, "__file__", None), pkg))
        return have
    spec = importlib.util.spec_from_file_location(
        PACKAGE, init, submodule_search_locations=[pkg])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[PACKAGE]
        raise
    return mod


def install(p=None, start=None, c=None):
    """Write the pinned commit's canbench/ if it is not there. Returns
    (folder, installed now)."""
    p = p or pin()
    c = c or clone()
    d = folder(p, start)
    if os.path.isdir(d):
        try:
            verify(d, p, c)
        except CanToolsPinError as e:
            raise CanToolsPinError("%s. It is not replaced: delete it by hand "
                                   "and run install again" % e)
        return d, False
    # here, not at the top: use() must load with only this folder on
    # sys.path, and only install() removes anything
    import scratch_dir
    files = tree(c, p)
    tmp = "%s.partial-%d" % (d, os.getpid())
    os.makedirs(os.path.dirname(d), exist_ok=True)
    scratch_dir.remove(tmp)
    try:
        for rel, sha in sorted(files.items()):
            rc, data, err = _git(c, ["cat-file", "blob", sha], text=False)
            if rc:
                raise CanToolsPinError("git cat-file of canbench/%s (%s) failed: "
                                       "%s" % (rel, sha[:7], err.decode(
                                           "utf-8", "replace").strip()))
            out = os.path.join(tmp, PACKAGE, *rel.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data)
        verify(tmp, p, c)
        os.rename(tmp, d)
    finally:
        scratch_dir.remove(tmp)
    return d, True


def main(argv):
    if argv not in (["install"], ["check"], ["where"]):
        print("usage: can_tools_pin.py install | check | where", file=sys.stderr)
        return 2
    try:
        p = pin()
        if argv == ["install"]:
            d, now = install(p)
            print("can-tools %s canbench/ %s at %s" % (
                p["commit"], "written and checked" if now
                else "already there, checked", d))
        elif argv == ["where"]:
            print(folder(p))
        else:
            d = verify(folder(p), p)
            print("can-tools %s canbench/ checked at %s" % (p["commit"], d))
    except CanToolsPinError as e:
        print("can_tools_pin: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
