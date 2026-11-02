r"""Scratch folders for the tests, in the worktree's scratch/ and nowhere else.

AGENTS.md "Files and folders" puts regenerable local work in the worktree's
gitignored scratch/, so ROOT is <worktree>/scratch/shared-tools-tests/<pid>/,
this run's own folder. Outside a git worktree, or in one whose git does not
ignore scratch/, importing this module raises Refused: there is no other
place for the tests' files, the machine temp folder included.

ROOT is removed when the run ends, pass or fail, by an exit handler
(AGENTS.md "Removing scratch"). remove_tree is the one remover here: it
clears the read-only flag Git for Windows sets on .git/objects and retries,
uses \\?\ paths on Windows, and prints what it could not remove and why. It
is a copy of shared/tools/verification/independent.py's remove_tree, not an
import of it: verification/ is the tester's, and the unit tests do not
depend on it.
"""

import atexit
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = "shared-tools-tests"


class Refused(RuntimeError):
    pass


def scratch_dir(here, env=None):
    """<worktree>/scratch/shared-tools-tests for the worktree holding here.
    Refused when here is in no git worktree, or git does not ignore scratch/
    there."""
    try:
        r = subprocess.run(["git", "-C", here, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, env=env)
    except OSError as e:
        raise Refused("the tests' scratch: git could not run (%s)" % e)
    top = r.stdout.strip()
    if r.returncode != 0 or not top:
        raise Refused("the tests' scratch: %s is not in a git worktree, and "
                      "the tests write only under a worktree's scratch/ "
                      "(AGENTS.md \"Files and folders\")" % here)
    r = subprocess.run(["git", "-C", top, "check-ignore", "-q", "scratch/"],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise Refused("the tests' scratch: git does not ignore scratch/ in "
                      "%s, so the tests' files would show as untracked "
                      "(AGENTS.md \"Files and folders\")" % top)
    return os.path.join(os.path.normpath(top), "scratch", FOLDER)


def long_path(p):
    """p made absolute and, on Windows, long-path safe: the \\\\?\\ prefix
    lifts the 260-character limit and stops Windows rewriting names (it drops
    a trailing dot or space)."""
    p = os.path.abspath(p)
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _retry_writable(func, path, errors):
    """rmtree's error handler: clear the read-only flag and try once more. A
    second failure is kept, with its reason. A link is never followed."""
    if os.path.islink(path):
        errors.append("%s: a link, not followed" % path)
        return
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        func(path)
    except OSError as e:
        errors.append("%s: %s" % (path, e))


def remove_tree(p):
    """Remove folder p and everything in it. True when it is gone, or was
    never there. When any of it is left, prints the folder and the reasons
    and returns False: it is never silent."""
    lp = long_path(p)
    if not os.path.lexists(lp):
        return True
    errors = []

    def handler(func, path, _exc):
        _retry_writable(func, path, errors)
    shutil.rmtree(lp, **({"onexc": handler} if sys.version_info >= (3, 12)
                         else {"onerror": handler}))
    if not os.path.lexists(lp):
        return True
    more = " (and %d more)" % (len(errors) - 5) if len(errors) > 5 else ""
    print("could not remove %s: %s%s" % (
        p, "; ".join(errors[:5]) or "it is still there", more), flush=True)
    return False


ROOT = os.path.join(scratch_dir(HERE), str(os.getpid()))
atexit.register(remove_tree, ROOT)


def fresh(name):
    """An empty folder ROOT/<name>, emptied first if it exists."""
    p = os.path.join(ROOT, name)
    if not remove_tree(p):
        raise Refused("could not empty %s" % p)
    os.makedirs(long_path(p))
    return p


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    kw = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": ""}
    with open(path, mode, **kw) as f:
        f.write(data)
    return path
