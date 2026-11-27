"""The pins file of outside packages (SPEC R35), and where its worktree is.

One JSON object, which the caller names. The two entries this package reads:

    {"can-tools": {"repo": "<url>", "commit": "<40 lower-case hex>"},
     "canre": {"repo": "<url>", "version": "<version>",
               "commit": "<40 lower-case hex>"}}

Any other key is ignored (R35, user 2026-11-26): gen-inhibit keeps these
entries beside its input-file pins.
"""

import json
import os

FIELDS = {"can-tools": ("repo", "commit"),
          "canre": ("repo", "version", "commit")}


class PinsError(RuntimeError):
    """A refusal: the package is not the pinned one, or cannot be checked."""


def read(path, key):
    """The entry `key` of the pins file at path, checked for shape."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise PinsError("the pins file %s cannot be read: %s" % (path, e))
    if not isinstance(data, dict):
        raise PinsError("the pins file %s is not a JSON object" % path)
    fields = FIELDS[key]
    p = data.get(key)
    if not (isinstance(p, dict) and all(isinstance(p.get(k), str) and p[k]
                                        for k in fields)):
        raise PinsError("the pins file %s has no complete \"%s\" entry (%s)"
                        % (path, key, ", ".join(fields)))
    c = p["commit"]
    if len(c) != 40 or c.strip("0123456789abcdef"):
        raise PinsError("the pins file %s: the %s commit %r is not a full "
                        "commit (40 lower-case hex digits)" % (path, key, c))
    return {k: p[k] for k in fields}


def worktree(path, exists=os.path.exists):
    """The git worktree that holds the file at path, found by walking up to
    its .git; None outside a git worktree."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def install_command(key, path):
    return "python -B -m vtrux_pins %s install --pins %s" % (
        key, os.path.abspath(path))
