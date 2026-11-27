r"""Removing a folder this package wrote: the partial folder of an install.

AGENTS.md "Removing scratch": never shutil.rmtree's ignore_errors. remove_tree
clears the read-only flag and retries once, uses \\?\ paths on Windows, never
follows or changes a link, and prints what it could not remove and why. It is
the same remover as shared/tools/tests/scratch.py's; the package does not
import the tests.
"""

import os
import shutil
import stat
import sys


def long_path(p):
    """p made absolute and, on Windows, long-path safe."""
    p = os.path.abspath(p)
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _retry_writable(func, path, errors):
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
    if os.path.islink(lp) or not os.path.isdir(lp):
        try:
            os.unlink(lp)
        except OSError as e:
            errors.append("%s: %s" % (p, e))
    else:
        shutil.rmtree(lp, **({"onexc": handler} if sys.version_info >= (3, 12)
                             else {"onerror": handler}))
    if not os.path.lexists(lp):
        return True
    more = " (and %d more)" % (len(errors) - 5) if len(errors) > 5 else ""
    print("could not remove %s: %s%s" % (
        p, "; ".join(errors[:5]) or "it is still there", more),
        file=sys.stderr, flush=True)
    return False
