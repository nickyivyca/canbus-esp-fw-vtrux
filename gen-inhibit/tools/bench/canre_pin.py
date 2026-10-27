#!/usr/bin/env python3
"""canre, the CAN analysis library, at the one commit the harness pins.

Gen-inhibit tester, 2026-10-26: the canre-pin step, after port step 0d and before
1a (the reviewer's canre-pin batch; port plan, "The CAN analysis library"). Until
this step the harness imported canre from SeaDrive's reverse-it root
(gi_paths.use_canre put the root on sys.path, from_capture.py put the project folder
there), so a run's parser was whatever the sync client held that day. canre is
published from the public repo, and the harness now takes it from there at one
commit: the "canre" entry of pins.json beside this file, with the repo, the version
and the full commit. The captures still come from SeaDrive through REVERSE_IT_DIR.

WHERE IT IS INSTALLED. pip installs that commit with no dependencies (canre.parsers
is pure stdlib; the [dbc] and [uds] extras are not taken) into a folder of its own,
never into an interpreter's site-packages, where it would also be what every other
tool on the machine imports:

    <worktree>/scratch/canre/<commit>/   in a git worktree (scratch/ is gitignored)
    <project>/scratch/canre/<commit>/    in a copy with no git (the host suite's
                                         ~/gi_host, which gi_host_run.sh writes)

    py -3.14 -B gen-inhibit/tools/bench/canre_pin.py install   # once per worktree
    py -3.14 -B gen-inhibit/tools/bench/canre_pin.py check     # checks, installs nothing

install puts it into a folder beside the target first and moves it into place only
once it passes the checks below. A target that is there already is checked and never
replaced: one that fails is refused, and deleting it is left to a person.

THE CHECKS (use(), check, and the end of install). A run's canre is the pinned
commit or the harness refuses it:
  1. the folder exists; if not, the refusal names the install command;
  2. it holds one canre dist-info, of the pinned version, whose direct_url.json
     (PEP 610) names the pinned repo and, as a git commit, the pinned commit;
  3. every file its RECORD lists with a hash has that hash (an installed file edited
     by hand is refused), and every file in its canre/ package is in RECORD (one
     added by hand is refused); __pycache__ aside;
  4. `import canre` loads that folder's canre/__init__.py. A canre imported earlier
     from anywhere else (SeaDrive's, an editable checkout, site-packages) is refused,
     and the folder goes first on sys.path, so canre.* imports after it come from it.

test_canre_pin.py tests this module with stand-in installs and a stand-in pip, so it
needs no network.
"""
import base64
import csv
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(HERE, "pins.json")
PACKAGE = "canre"
PIP = [sys.executable, "-m", "pip"]     # test_canre_pin.py puts a stand-in here


class CanrePinError(RuntimeError):
    pass


def pin(path=None):
    """pins.json's canre entry: {repo, version, commit}, the commit in full."""
    with open(path or PINS_FILE, encoding="utf-8") as fh:
        p = json.load(fh).get("canre")
    if not (isinstance(p, dict) and all(isinstance(p.get(k), str) and p[k] for k in ("repo", "version", "commit"))):
        raise CanrePinError("pins.json has no complete \"canre\" entry (repo, version, commit)")
    c = p["commit"]
    if len(c) != 40 or c.strip("0123456789abcdef"):
        raise CanrePinError("pins.json's canre commit %r is not a full commit (40 lower-case hex digits)" % c)
    return p


def home(start=None, exists=os.path.exists):
    """The folder whose scratch/ holds the installs: the git worktree holding start
    (default this file), found by walking up to its .git, else the project folder, two
    above this file's folder (a copy with no git)."""
    d = os.path.abspath(start or HERE)
    while True:
        if exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.normpath(os.path.join(os.path.abspath(start or HERE), "..", ".."))
        d = up


def folder(p=None, start=None):
    """The install folder of the pinned commit."""
    return os.path.join(home(start), "scratch", "canre", (p or pin())["commit"])


def install_command():
    return "%s -B %s install" % (sys.executable, os.path.abspath(__file__))


def _same(a, b):
    return a is not None and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def verify(d, p=None):
    """Checks 1 to 3 on the install folder d. Returns its dist-info folder."""
    p = p or pin()
    short = p["commit"][:7]
    if not os.path.isdir(d):
        raise CanrePinError("canre %s (the pin) is not installed at %s: run %s" % (short, d, install_command()))
    infos = sorted(x for x in os.listdir(d) if x.lower().startswith(PACKAGE + "-") and x.endswith(".dist-info"))
    if len(infos) != 1:
        raise CanrePinError("%s holds %d canre dist-info folders, not one: %s" % (d, len(infos), infos))
    info = os.path.join(d, infos[0])
    version = None
    with open(os.path.join(info, "METADATA"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
                break
            if not line.strip():
                break
    if version != p["version"]:
        raise CanrePinError("%s is canre %s, not the pinned %s" % (d, version, p["version"]))
    du_path = os.path.join(info, "direct_url.json")
    if not os.path.isfile(du_path):
        raise CanrePinError("%s has no direct_url.json, so it was not installed from the pinned commit" % d)
    with open(du_path, encoding="utf-8") as fh:
        du = json.load(fh)
    vcs = du.get("vcs_info") or {}
    if du.get("url") != p["repo"] or vcs.get("vcs") != "git" or vcs.get("commit_id") != p["commit"]:
        raise CanrePinError("%s is canre from %s at %s %s, not the pin (%s at git %s)"
                            % (d, du.get("url"), vcs.get("vcs") or "(no vcs)", vcs.get("commit_id"),
                               p["repo"], p["commit"]))
    listed, changed = set(), []
    with open(os.path.join(info, "RECORD"), encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
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
                got = base64.urlsafe_b64encode(hashlib.sha256(f.read()).digest()).rstrip(b"=").decode()
            if got != want:
                changed.append(row[0])
    pkg = os.path.join(d, PACKAGE)
    if not os.path.isfile(os.path.join(pkg, "__init__.py")):
        raise CanrePinError("%s has no %s/__init__.py" % (d, PACKAGE))
    added = []
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [x for x in dns if x != "__pycache__"]
        added += [os.path.relpath(os.path.join(dp, f), d) for f in fns
                  if os.path.normcase(os.path.normpath(os.path.join(dp, f))) not in listed]
    if changed or added:
        raise CanrePinError("%s does not match its RECORD: changed or missing %s; not in it %s"
                            % (d, sorted(changed), sorted(added)))
    return info


def use(d=None):
    """All four checks; the install folder goes first on sys.path. Returns it."""
    d = d or folder()
    verify(d)
    init = os.path.join(d, PACKAGE, "__init__.py")
    mod = sys.modules.get(PACKAGE)
    if mod is not None and not _same(getattr(mod, "__file__", None), init):
        raise CanrePinError("canre was already imported from %s, not the pinned %s"
                            % (getattr(mod, "__file__", None), init))
    while d in sys.path:
        sys.path.remove(d)
    sys.path.insert(0, d)
    importlib.invalidate_caches()
    got = importlib.import_module(PACKAGE).__file__
    if not _same(got, init):
        raise CanrePinError("import canre loaded %s, not the pinned %s" % (got, init))
    return d


def install(p=None, start=None):
    """Install the pinned commit if it is not there. Returns (folder, installed now)."""
    p = p or pin()
    d = folder(p, start)
    if os.path.isdir(d):
        try:
            verify(d, p)
        except CanrePinError as e:
            raise CanrePinError("%s. It is not replaced: delete it by hand and run install again" % e)
        return d, False
    tmp = "%s.partial-%d" % (d, os.getpid())
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    r = subprocess.run(PIP + ["install", "--no-deps", "--no-compile", "--disable-pip-version-check", "--no-input",
                              "--target", tmp, "git+%s@%s" % (p["repo"], p["commit"])], stdin=subprocess.DEVNULL)
    try:
        if r.returncode != 0:
            raise CanrePinError("pip install of canre %s failed (exit %d)" % (p["commit"][:7], r.returncode))
        verify(tmp, p)
        os.rename(tmp, d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return d, True


def main(argv):
    if argv not in (["install"], ["check"]):
        print("usage: canre_pin.py install | check", file=sys.stderr)
        return 2
    try:
        p = pin()
        if argv == ["install"]:
            d, now = install(p)
            print("canre %s %s at %s" % (p["commit"], "installed and checked" if now else "already installed, checked", d))
        else:
            d = folder(p)
            verify(d, p)
            print("canre %s checked at %s" % (p["commit"], d))
    except CanrePinError as e:
        print("canre_pin: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
