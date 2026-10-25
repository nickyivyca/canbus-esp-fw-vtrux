"""The build tooling's own pin (SPEC R16, R23): which version of shared/tools a
project uses, and the check that this tooling is that version.

The pin is the "shared/tools" entry of shared_pins.json in the project's
root, committed with the project:

    {"shared/tools": {"tag": "2026.10.24",
                      "commit": "<40 hex digits, the commit the tag marks>",
                      "digest": "<64 hex digits, R7 of shared/tools at it>"}}

The file is keyed by component so that other shared tooling (the bench
toolkit) can pin itself in the same file; this module reads only its own
entry and neither rejects nor relies on others.

R23: the tooling runs from the read-only checkout ../shared-<tag>/, relative
to the root of the project's git worktree, and refuses to start unless

  - that checkout exists (if not, the refusal prints the command that
    creates it);
  - the shared/tools folder this code runs from is that checkout's;
  - its R7 digest is the pin's digest;
  - the checkout's HEAD is the pin's commit, and the pin's tag is a tag
    there that marks that commit.

R16, the tooling-pin clause: a pin that names a commit rather than a tag is
refused: no tag, an empty one, one that reads as a commit id (7 to 40 hex
digits), or one the checkout has no tag of that name for.
"""

import json
import os
import re
import subprocess

from .digest import component_digest

PIN_FILE = "shared_pins.json"
COMPONENT = "shared/tools"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOOKS_LIKE_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")


class PinError(Exception):
    """The pin is missing or wrong, or this tooling is not the pinned
    version. str() is the reason."""


def _git(root, *args):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                       text=True)
    return r.returncode, r.stdout.strip()


def read(project_root):
    """The project's "shared/tools" pin, {"tag", "commit", "digest"}.
    PinError if the file or the entry is missing or malformed, or (R16) the
    pin names a commit rather than a tag."""
    path = os.path.join(project_root, PIN_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise PinError("no tooling pin: %s (%s). The project pins the "
                       "build tooling it uses in %s, under %r (R23)"
                       % (path, e, PIN_FILE, COMPONENT))
    except ValueError as e:
        raise PinError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get(COMPONENT), dict):
        raise PinError("%s has no %r entry, so the build tooling is not "
                       "pinned (R23)" % (path, COMPONENT))
    pin = data[COMPONENT]
    tag, commit, digest = pin.get("tag"), pin.get("commit"), pin.get("digest")
    if not isinstance(tag, str) or not tag.strip():
        raise PinError("the %r pin in %s names no tag; the build tooling is "
                       "pinned at a tag, not a commit (R16)" % (COMPONENT, path))
    if _LOOKS_LIKE_COMMIT.match(tag):
        raise PinError("the %r pin in %s names %r as its tag, which is a "
                       "commit id; the build tooling is pinned at a tag, not "
                       "a commit (R16)" % (COMPONENT, path, tag))
    if tag != tag.strip() or "/" in tag or "\\" in tag:
        raise PinError("the %r pin in %s names the tag %r, which cannot be a "
                       "checkout folder's name (../shared-<tag>/, R23)"
                       % (COMPONENT, path, tag))
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise PinError("the %r pin in %s must give the tag's commit as 40 "
                       "lower-case hex digits, not %r" % (COMPONENT, path, commit))
    if not isinstance(digest, str) or not _DIGEST.match(digest):
        raise PinError("the %r pin in %s must give the R7 digest as 64 "
                       "lower-case hex digits, not %r" % (COMPONENT, path, digest))
    return {"tag": tag, "commit": commit, "digest": digest}


def checkout_dir(project_root, tag):
    """(checkout, worktree root): ../shared-<tag>/ beside the root of the
    project's git worktree, and that root. PinError if the project is not in
    a git worktree."""
    rc, top = _git(project_root, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        raise PinError("%s is not in a git worktree, so the pinned checkout "
                       "../shared-<tag>/ cannot be found (R23)" % project_root)
    return os.path.normpath(os.path.join(top, os.pardir, "shared-" + tag)), top


def create_command(top, tag):
    """The command that creates the pinned checkout, run from anywhere."""
    return 'git -C "%s" worktree add --detach ../shared-%s %s' % (top, tag, tag)


def _same(a, b):
    return (os.path.normcase(os.path.realpath(a))
            == os.path.normcase(os.path.realpath(b)))


def check(project_root, tools_root):
    """R23 and R16's tooling-pin clause, for the shared/tools folder at
    tools_root (the one this code runs from). Returns what R13 records,
    {"tag", "commit", "dirty", "digest"}: tag, commit and digest are the
    pin's, now checked; dirty is whether git sees uncommitted changes under
    the checkout's shared/tools (only a .md or .gitignore file can differ
    there without changing the digest). PinError says what does not hold."""
    pin = read(project_root)
    tag = pin["tag"]
    checkout, top = checkout_dir(project_root, tag)
    if not os.path.isdir(checkout):
        raise PinError("the pinned checkout %s does not exist. Create it, "
                       "read-only and detached at the pin's tag, with:\n"
                       "    %s" % (checkout, create_command(top, tag)))
    want = os.path.join(checkout, "shared", "tools")
    if not _same(tools_root, want):
        raise PinError("this build tooling runs from %s, and the project's "
                       "pin says %s: the tooling runs from the pinned "
                       "checkout (R23)" % (os.path.realpath(tools_root), want))
    digest = component_digest(tools_root)
    if digest != pin["digest"]:
        raise PinError("%s has the digest %s, and the pin says %s: it is not "
                       "the pinned version, or a file in it was changed "
                       "(R23, R7)" % (want, digest, pin["digest"]))
    rc, head = _git(checkout, "rev-parse", "--verify", "HEAD")
    if rc != 0 or head != pin["commit"]:
        raise PinError("the checkout %s is at %s, and the pin says %s (R23)"
                       % (checkout, head or "(unreadable)", pin["commit"]))
    rc, tagged = _git(checkout, "rev-parse", "--verify", "--quiet",
                      "refs/tags/%s^{commit}" % tag)
    if rc != 0 or not tagged:
        raise PinError("the pin names %r as its tag, and the checkout's "
                       "repository has no tag of that name; the build tooling "
                       "is pinned at a tag, not a commit or a branch (R16)"
                       % tag)
    if tagged != pin["commit"]:
        raise PinError("the tag %s marks %s, and the pin says %s (R23)"
                       % (tag, tagged, pin["commit"]))
    rc, st = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all",
                  "--", "shared/tools")
    return {"tag": tag, "commit": pin["commit"],
            "dirty": bool(st) if rc == 0 else None, "digest": digest}
