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

The run's folder is removed when the process that made it exits, pass or
fail (AGENTS.md "Removing scratch"): read-only files made writable and tried
again, the whole tree tried again for a few seconds while another process
holds part of it, long-path-safe on Windows, and a folder that will not go
printed to stderr with the reason, never ignored. Before 2026-11-01 nothing removed
them, and a day's runs left 3,359 files. A killed process's folder stays,
under its own <time>_<pid>, so it never stops the next run. Once its run folder
is gone, its <name>/ and vtrux-bench-tests/ go too if nothing else is in them
(the charge-interposer reviewer, 2026-11-02: 21 empty tool folders were left).
"""

import atexit
import os
import shutil
import stat
import sys
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


def long_path(p):
    """p made absolute, and on Windows in the \\\\?\\ form, which has no 260-character limit."""
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return p


# Waits before each further try at a tree still there, about 3 s in all. On
# Windows a process that has a file or folder open for a moment (Windows Search,
# Defender) stops its removal, or leaves a file delete-pending so its folder is
# not yet empty; one try left an empty run folder after 09ebe6b (2026-11-01).
RETRY_WAITS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


def remove_tree(path):
    """Remove path and everything in it. A file or folder that will not go is made
    writable and tried again (Git for Windows' objects are read-only, which
    shutil.rmtree alone cannot remove on Windows), and a tree still there is tried
    again after each of RETRY_WAITS. True if path is gone; otherwise the path and
    the reason are printed to stderr and False is returned."""
    failed = []

    def again(func, p, exc):
        try:
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if os.path.isdir(p) else 0))
            func(p)
        except OSError as e:
            failed.append((p, e))

    lp = long_path(path)
    for wait in (0,) + RETRY_WAITS:
        if not os.path.lexists(lp):
            return True
        time.sleep(wait)
        del failed[:]
        if sys.version_info >= (3, 12):
            shutil.rmtree(lp, onexc=again)
        else:
            shutil.rmtree(lp, onerror=lambda func, p, ei: again(func, p, ei[1]))
    if os.path.lexists(lp):
        why = failed[0] if failed else (path, "still there after removal")
        print("could not remove %s: %s (%s)" % (path, why[1], why[0]), file=sys.stderr)
        return False
    return True


def drop_empty(d):
    """After run folder d is removed: its tool folder <name>/, then
    vtrux-bench-tests/, each removed if nothing is left in it. Another run's
    folder keeps them, so os.rmdir, which takes only an empty folder, and its
    refusal is not an error; nor is a folder another run's exit already took."""
    for p in (os.path.dirname(d), os.path.dirname(os.path.dirname(d))):
        try:
            os.rmdir(long_path(p))
        except OSError:
            pass


def _remove_run(d):
    if remove_tree(d):
        drop_empty(d)


MAKE_TRIES = 3


def run_dir(name, start=None):
    """This run's own folder for test `name`, created, and removed when this process
    exits, with the tool folders above it once empty (drop_empty). `start` is a
    path inside the worktree meant (default this file)."""
    d = os.path.join(worktree_of(start or __file__), "scratch", "vtrux-bench-tests", name,
                     "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    # another run's exit can take <name>/ or vtrux-bench-tests/ while this one is
    # making them (drop_empty): they are made again, MAKE_TRIES tries in all
    for attempt in range(MAKE_TRIES):
        try:
            os.makedirs(d, exist_ok=True)
            break
        except FileNotFoundError:
            if attempt == MAKE_TRIES - 1:
                raise
    atexit.register(_remove_run, d)
    return d
