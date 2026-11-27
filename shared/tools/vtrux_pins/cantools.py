"""canbench, the CAN adapter package, at the can-tools commit a project pins
(SPEC R35).

canbench comes from the can-tools clone that CAN_TOOLS_DIR names. The clone
is not what a run loads: it moves when anyone pulls it. The pinned commit's
canbench/ is written from the clone's git blobs into a folder of its own, in
the git worktree that holds the pins file, and loaded from there by file:

    <worktree>/scratch/can-tools/<commit>/canbench/

Outside a git worktree canbench is refused.

THE CHECKS (verify() and use(); check, and the end of install). canbench is
refused unless:
  1. CAN_TOOLS_DIR is set and holds canbench/ (it is the can-tools clone);
     there is no default;
  2. the clone has the pinned commit; every entry under canbench/ at that
     commit is a regular or executable file, and canbench/__init__.py is
     one of them;
  3. the folder exists (if not, the refusal names the install command), and
     every file in its canbench/ has the git blob hash of the same path at
     the pinned commit: none missing, none changed, none added
     (__pycache__ folders aside);
  4. use() loads canbench by file from that folder. Neither the folder nor
     the clone is put on sys.path. A canbench imported earlier from any
     other file is refused; one already loaded from the pinned folder is
     kept.

The repo in the pin is recorded, not compared with the clone's remote: the
commit fixes the content.

install() writes the raw blobs (`git cat-file blob`: no line-ending or other
filter) into <target>.partial-<pid> beside the target, and moves it into
place only once it passes check 3. A partial folder from an earlier run is
removed first; if it cannot be removed, install is refused. A target that is
already there is checked and never replaced.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys

from . import pinsfile
from .pinsfile import PinsError
from .remove import remove_tree

KEY = "can-tools"
PACKAGE = "canbench"
ENV = "CAN_TOOLS_DIR"


def pin(pins_path):
    return pinsfile.read(pins_path, KEY)


def clone(environ=None):
    """The can-tools clone, from CAN_TOOLS_DIR (check 1). No default."""
    v = (os.environ if environ is None else environ).get(ENV, "").strip()
    if not v:
        raise PinsError("CAN_TOOLS_DIR is not set. Set it to the can-tools "
                        "clone on this machine (the folder holding canbench/)")
    v = os.path.normpath(v)
    if not os.path.isfile(os.path.join(v, PACKAGE, "__init__.py")):
        raise PinsError("CAN_TOOLS_DIR=%s holds no canbench/: it must be the "
                        "can-tools clone" % v)
    return v


def folder(pins_path, p=None):
    """The pinned commit's folder: <worktree>/scratch/can-tools/<commit>."""
    p = p or pin(pins_path)
    wt = pinsfile.worktree(pins_path)
    if wt is None:
        raise PinsError("the pins file %s is in no git worktree, so it has no "
                        "scratch/can-tools/: canbench is pinned only in a "
                        "worktree" % os.path.abspath(pins_path))
    return os.path.join(wt, "scratch", "can-tools", p["commit"])


def _git(c, args, text=True):
    r = subprocess.run(["git", "-C", c] + args, capture_output=True,
                       text=text, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def tree(c, p):
    """{path under canbench/, posix: blob hash} at the pinned commit
    (check 2)."""
    commit = p["commit"]
    rc, _, _ = _git(c, ["cat-file", "-e", commit + "^{commit}"])
    if rc:
        raise PinsError("the can-tools clone at %s has no commit %s (the pin): "
                        "fetch it there (git -C %s fetch)" % (c, commit, c))
    rc, out, err = _git(c, ["ls-tree", "-r", "-z", "--full-tree", commit,
                            "--", PACKAGE])
    if rc:
        raise PinsError("git ls-tree of %s in %s failed: %s"
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
        raise PinsError("can-tools %s has entries under canbench/ that are not "
                        "regular files: %s" % (commit[:7], odd))
    if "__init__.py" not in files:
        raise PinsError("can-tools %s has no canbench/__init__.py" % commit[:7])
    return files


def blob_hash(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def verify(d, p, c, pins_path):
    """Check 3 on the folder d, against check 2's tree in the clone c.
    Returns d."""
    want = tree(c, p)
    pkg = os.path.join(d, PACKAGE)
    if not os.path.isdir(pkg):
        raise PinsError("can-tools %s (the pin) is not installed at %s: run %s"
                        % (p["commit"][:7], d,
                           pinsfile.install_command(KEY, pins_path)))
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
        raise PinsError("%s is not can-tools %s's canbench/: missing %s; "
                        "changed %s; not in it %s"
                        % (d, p["commit"][:7], missing, changed, added))
    return d


def check(pins_path, environ=None):
    """Checks 1 to 3. Returns the folder."""
    p = pin(pins_path)
    c = clone(environ)
    return verify(folder(pins_path, p), p, c, pins_path)


def _same(a, b):
    return a is not None and (os.path.normcase(os.path.realpath(a)) ==
                              os.path.normcase(os.path.realpath(b)))


def use(pins_path, environ=None):
    """All four checks; canbench loaded by file from the folder. Returns the
    module."""
    d = check(pins_path, environ)
    pkg = os.path.join(d, PACKAGE)
    init = os.path.join(pkg, "__init__.py")
    have = sys.modules.get(PACKAGE)
    if have is not None:
        if not _same(getattr(have, "__file__", None), init):
            raise PinsError("canbench is already imported from %s, not the "
                            "pinned %s" % (getattr(have, "__file__", None), pkg))
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


def install(pins_path, environ=None):
    """Write the pinned commit's canbench/ if it is not there, then check it.
    Returns (folder, written now)."""
    p = pin(pins_path)
    c = clone(environ)
    d = folder(pins_path, p)
    if os.path.isdir(d):
        try:
            verify(d, p, c, pins_path)
        except PinsError as e:
            raise PinsError("%s. It is not replaced: delete it by hand and run "
                            "install again" % e)
        return d, False
    files = tree(c, p)
    tmp = "%s.partial-%d" % (d, os.getpid())
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if not remove_tree(tmp):
        raise PinsError("%s, a partial folder from an earlier run, could not "
                        "be removed (above): remove it by hand and run install "
                        "again" % tmp)
    try:
        for rel, sha in sorted(files.items()):
            rc, data, err = _git(c, ["cat-file", "blob", sha], text=False)
            if rc:
                raise PinsError("git cat-file of canbench/%s (%s) failed: %s"
                                % (rel, sha[:7],
                                   err.decode("utf-8", "replace").strip()))
            out = os.path.join(tmp, PACKAGE, *rel.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data)
        verify(tmp, p, c, pins_path)
        try:
            os.rename(tmp, d)
        except OSError as e:
            raise PinsError("could not move %s into place at %s: %s"
                            % (tmp, d, e))
    finally:
        remove_tree(tmp)
    return d, True
