"""Scratch folders for the tests, never in a synced folder.

AGENTS.md "Files and folders" puts regenerable local work in the worktree's
scratch/, so ROOT is <worktree>/scratch/shared-tools-tests/ when git ignores
scratch/ there (a project worktree). A worktree that does not ignore it
(shared/main has no root .gitignore) would show the tests' files as
untracked, so there ROOT is the machine temp folder's vtrux-build-tests/
instead.
"""

import os
import shutil
import stat
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _root():
    try:
        top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
        if top and subprocess.run(["git", "-C", top, "check-ignore", "-q",
                                   "scratch/"]).returncode == 0:
            return os.path.join(top, "scratch", "shared-tools-tests")
    except OSError:
        pass
    return os.path.join(tempfile.gettempdir(), "vtrux-build-tests")


ROOT = _root()


def _writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def fresh(name):
    """An empty folder ROOT/<name>, emptied first if it exists."""
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        shutil.rmtree(p, onerror=_writable)
    os.makedirs(p)
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
