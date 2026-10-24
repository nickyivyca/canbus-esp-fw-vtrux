"""Scratch folders for the tests, in the machine temp folder
(<tempdir>/vtrux-build-tests/), never in a synced folder or the worktree."""

import os
import shutil
import stat
import tempfile

ROOT = os.path.join(tempfile.gettempdir(), "vtrux-build-tests")


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
