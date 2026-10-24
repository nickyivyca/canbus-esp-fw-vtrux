"""Where shared/bench's tests put their files: this run's own folder in the
enclosing git worktree's scratch/, never the system temp folder (AGENTS.md,
"Files and folders").

    <worktree>/scratch/vtrux-bench-tests/<name>/<time>_<pid>

The worktree is the nearest folder upwards holding a .git (a folder in a clone,
a file in a worktree), found by walking up rather than by counting, so a pinned
export of shared/ inside a project worktree's scratch/ lands in that worktree.
The per-run part means two runs at once, from two sessions or two worktrees,
never share a folder; before 2026-10-24 every run used one fixed folder under
the system temp folder, and test_decode and test_run_core shared theirs. With
no worktree above, the tests refuse rather than fall back to the temp folder.
"""

import os
import time


class ScratchError(RuntimeError):
    pass


def worktree_of(path):
    """The git worktree holding path."""
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise ScratchError("%s is in no git worktree, so there is no scratch/ for "
                               "the tests' files" % path)
        d = up


def run_dir(name, start=None):
    """This run's own folder for test `name`, created. `start` is a path inside the
    worktree meant (default this file)."""
    d = os.path.join(worktree_of(start or __file__), "scratch", "vtrux-bench-tests", name,
                     "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d, exist_ok=True)
    return d
