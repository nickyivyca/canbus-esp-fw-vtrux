#!/usr/bin/env python3
"""Where the WiCAN's board component is, for the build checks: the stock partition table and
board.json come from it (shared/boards wican_v3; gen-inhibit reviewer, 2026-10-27: one copy,
the board's, git being the reference copy).

  pinned_tag()        the tag firmware/main/idf_component.yml pins vtrux/wican_v3 at
  pinned_folder()     that tag's read-only checkout, ../shared-<tag>/ beside the worktree root
                      (shared/tools R23), and the board's folder in it
  managed_folder(p)   the copy a build downloaded, <project>/managed_components/vtrux__wican_v3/
  read(folder)        board.json, and the path of its reference table

Gen-inhibit tester, 2026-10-27 (port step 1a C).
"""

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(os.path.dirname(os.path.dirname(HERE)), "firmware")   # the WiCAN tree (port step 0d)
NAME = "vtrux/wican_v3"
MANAGED = "vtrux__wican_v3"
PATH = "shared/boards/wican_v3"
MANIFEST = os.path.join("main", "idf_component.yml")
_DEP = re.compile(r"^(\s+)%s:\s*(?:['\"]?([^'\"\s#]+)['\"]?)?\s*(?:#.*)?$" % re.escape(NAME))
_VERSION = re.compile(r"""^\s+version:\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$""")


class BoardRefError(ValueError):
    pass


def parse_pin(text):
    """The version (for a git source, the tag) a manifest's text gives vtrux/wican_v3, or None."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = _DEP.match(ln)
        if not m:
            continue
        if m.group(2):
            return m.group(2)                   # the inline form, "vtrux/wican_v3: <version>"
        indent = len(m.group(1))
        for sub in lines[i + 1:]:
            if not sub.strip() or sub.lstrip().startswith("#"):
                continue
            if len(sub) - len(sub.lstrip()) <= indent:
                break
            v = _VERSION.match(sub)
            if v:
                return v.group(1)
        return None
    return None


def pinned_tag(repo=REPO):
    p = os.path.join(repo, MANIFEST)
    try:
        with open(p, encoding="utf-8") as f:
            tag = parse_pin(f.read())
    except OSError as e:
        raise BoardRefError("%s cannot be read (%s)" % (p, e))
    if not tag:
        raise BoardRefError("%s does not pin %s" % (MANIFEST.replace(os.sep, "/"), NAME))
    return tag


def pinned_folder(repo=REPO):
    """(tag, the board's folder in ../shared-<tag>/). Raises BoardRefError if the checkout is missing."""
    tag = pinned_tag(repo)
    top = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode:
        raise BoardRefError("%s is not in a git worktree" % repo)
    co = os.path.join(os.path.dirname(os.path.normpath(top.stdout.strip())), "shared-" + tag)
    folder = os.path.join(co, *PATH.split("/"))
    if not os.path.isdir(folder):
        raise BoardRefError("no %s: make it with git worktree add --detach ../shared-%s %s" % (folder, tag, tag))
    return tag, folder


def managed_folder(project):
    return os.path.join(project, "managed_components", MANAGED)


def read(folder):
    """(board.json as an object, the absolute path of its reference table)."""
    p = os.path.join(folder, "board.json")
    try:
        with open(p, encoding="utf-8") as f:
            board = json.load(f)
    except (OSError, ValueError) as e:
        raise BoardRefError("%s cannot be read (%s)" % (p, e))
    rel = board.get("partition_table") if isinstance(board, dict) else None
    if not isinstance(rel, str) or not rel:
        raise BoardRefError("%s names no partition_table" % p)
    return board, os.path.normpath(os.path.join(folder, rel))
