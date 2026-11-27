"""canre, the CAN analysis library, at the commit a project pins (SPEC R35).

pip installs the pinned commit with no dependencies into a folder of its own,
never into an interpreter's site-packages:

    <worktree>/scratch/canre/<commit>/   the git worktree that holds the
                                         pins file
    $CANRE_DIR                           when it is set: an absolute path

Outside a git worktree, only CANRE_DIR names the folder, and canre is refused
if it is not set (user, 2026-11-26). install always writes into the worktree,
never into CANRE_DIR, and is refused outside a git worktree.

THE CHECKS (verify() and use(); check, and the end of install). canre is
refused unless:
  1. the folder exists; if not, the refusal names the install command;
  2. it holds exactly one canre .dist-info folder, whose METADATA gives the
     pinned version and whose direct_url.json (PEP 610) names the pinned
     repo and, as a git commit, the pinned commit;
  3. every file its RECORD lists with a hash is there with that sha256, and
     every file under its canre/ package is in RECORD (__pycache__ folders
     aside); canre/__init__.py is there;
  4. use() puts the folder first on sys.path, and `import canre` must load
     that folder's canre/__init__.py. A canre imported earlier from any
     other file is refused.

install() runs pip into <target>.partial-<pid> beside the target and moves it
into place only once it passes checks 2 and 3. A partial folder from an
earlier run is removed first; if it cannot be removed, install is refused. A
target that is already there is checked and never replaced.
"""

import base64
import csv
import hashlib
import importlib
import json
import os
import subprocess
import sys

from . import pinsfile
from .pinsfile import PinsError
from .remove import remove_tree

KEY = "canre"
PACKAGE = "canre"
ENV = "CANRE_DIR"
PIP = [sys.executable, "-m", "pip"]     # the tests put a stand-in here


def pin(pins_path):
    return pinsfile.read(pins_path, KEY)


def _worktree_folder(pins_path, p):
    wt = pinsfile.worktree(pins_path)
    if wt is None:
        return None
    return os.path.join(wt, "scratch", "canre", p["commit"])


def folder(pins_path, p=None, environ=None):
    """The checked folder: $CANRE_DIR when it is set, else
    <worktree>/scratch/canre/<commit>."""
    p = p or pin(pins_path)
    env = (os.environ if environ is None else environ).get(ENV, "").strip()
    if env:
        if not os.path.isabs(env):
            raise PinsError("%s=%s is not an absolute path" % (ENV, env))
        return os.path.normpath(env)
    d = _worktree_folder(pins_path, p)
    if d is None:
        raise PinsError("the pins file %s is in no git worktree and %s is not "
                        "set: outside a git worktree, only %s names canre's "
                        "folder" % (os.path.abspath(pins_path), ENV, ENV))
    return d


def _read_text(path, what):
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, ValueError) as e:
        raise PinsError("%s cannot be read: %s" % (what, e))


def verify(d, p, pins_path):
    """Checks 1 to 3 on the folder d. Returns its .dist-info folder."""
    short = p["commit"][:7]
    if not os.path.isdir(d):
        raise PinsError("canre %s (the pin) is not installed at %s: run %s"
                        % (short, d, pinsfile.install_command(KEY, pins_path)))
    infos = sorted(x for x in os.listdir(d)
                   if x.lower().startswith(PACKAGE + "-")
                   and x.endswith(".dist-info"))
    if len(infos) != 1:
        raise PinsError("%s holds %d canre .dist-info folders, not one: %s"
                        % (d, len(infos), infos))
    info = os.path.join(d, infos[0])
    version = None
    for line in _read_text(os.path.join(info, "METADATA"),
                           "%s's METADATA" % infos[0]).splitlines():
        if line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
            break
        if not line.strip():
            break
    if version != p["version"]:
        raise PinsError("%s is canre %s, not the pinned %s"
                        % (d, version, p["version"]))
    du_path = os.path.join(info, "direct_url.json")
    if not os.path.isfile(du_path):
        raise PinsError("%s has no direct_url.json, so it was not installed "
                        "from the pinned commit" % d)
    try:
        du = json.loads(_read_text(du_path, "direct_url.json"))
    except ValueError as e:
        raise PinsError("%s's direct_url.json is not JSON: %s" % (d, e))
    if not isinstance(du, dict):
        du = {}
    vcs = du.get("vcs_info")
    vcs = vcs if isinstance(vcs, dict) else {}
    if (du.get("url") != p["repo"] or vcs.get("vcs") != "git"
            or vcs.get("commit_id") != p["commit"]):
        raise PinsError("%s is canre from %s at %s %s, not the pin (%s at git "
                        "%s)" % (d, du.get("url"), vcs.get("vcs") or "(no vcs)",
                                 vcs.get("commit_id"), p["repo"], p["commit"]))
    listed, changed = set(), []
    record = _read_text(os.path.join(info, "RECORD"), "%s's RECORD" % infos[0])
    for row in csv.reader(record.splitlines()):
        if not row:
            continue
        full = os.path.normpath(os.path.join(d, row[0]))
        listed.add(os.path.normcase(full))
        h = row[1] if len(row) > 1 else ""
        if not h:
            continue
        algo, _, want = h.partition("=")
        if algo != "sha256" or not os.path.isfile(full):
            changed.append(row[0])
            continue
        with open(full, "rb") as f:
            got = base64.urlsafe_b64encode(
                hashlib.sha256(f.read()).digest()).rstrip(b"=").decode()
        if got != want:
            changed.append(row[0])
    pkg = os.path.join(d, PACKAGE)
    if not os.path.isfile(os.path.join(pkg, "__init__.py")):
        raise PinsError("%s has no %s/__init__.py" % (d, PACKAGE))
    added = []
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [x for x in dns if x != "__pycache__"]
        added += [os.path.relpath(os.path.join(dp, f), d) for f in fns
                  if os.path.normcase(os.path.normpath(os.path.join(dp, f)))
                  not in listed]
    if changed or added:
        raise PinsError("%s does not match its RECORD: changed or missing %s; "
                        "not in it %s" % (d, sorted(changed), sorted(added)))
    return info


def check(pins_path, environ=None):
    """Checks 1 to 3. Returns the folder."""
    p = pin(pins_path)
    d = folder(pins_path, p, environ)
    verify(d, p, pins_path)
    return d


def _same(a, b):
    return a is not None and (os.path.normcase(os.path.realpath(a)) ==
                              os.path.normcase(os.path.realpath(b)))


def use(pins_path, environ=None):
    """All four checks; the folder goes first on sys.path. Returns it."""
    d = check(pins_path, environ)
    init = os.path.join(d, PACKAGE, "__init__.py")
    mod = sys.modules.get(PACKAGE)
    if mod is not None and not _same(getattr(mod, "__file__", None), init):
        raise PinsError("canre was already imported from %s, not the pinned %s"
                        % (getattr(mod, "__file__", None), init))
    while d in sys.path:
        sys.path.remove(d)
    sys.path.insert(0, d)
    importlib.invalidate_caches()
    got = importlib.import_module(PACKAGE).__file__
    if not _same(got, init):
        raise PinsError("import canre loaded %s, not the pinned %s"
                        % (got, init))
    return d


def install(pins_path):
    """Install the pinned commit into the worktree if it is not there, then
    check it. Never into CANRE_DIR. Returns (folder, installed now)."""
    p = pin(pins_path)
    d = _worktree_folder(pins_path, p)
    if d is None:
        raise PinsError("the pins file %s is in no git worktree: canre is "
                        "installed only in a worktree's scratch/canre/"
                        % os.path.abspath(pins_path))
    if os.path.isdir(d):
        try:
            verify(d, p, pins_path)
        except PinsError as e:
            raise PinsError("%s. It is not replaced: delete it by hand and run "
                            "install again" % e)
        return d, False
    tmp = "%s.partial-%d" % (d, os.getpid())
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if not remove_tree(tmp):
        raise PinsError("%s, a partial folder from an earlier run, could not "
                        "be removed (above): remove it by hand and run install "
                        "again" % tmp)
    try:
        r = subprocess.run(PIP + ["install", "--no-deps", "--no-compile",
                                  "--disable-pip-version-check", "--no-input",
                                  "--target", tmp,
                                  "git+%s@%s" % (p["repo"], p["commit"])],
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise PinsError("pip install of canre %s failed (exit %d)"
                            % (p["commit"][:7], r.returncode))
        verify(tmp, p, pins_path)
        try:
            os.rename(tmp, d)
        except OSError as e:
            raise PinsError("could not move %s into place at %s: %s"
                            % (tmp, d, e))
    finally:
        remove_tree(tmp)
    return d, True
