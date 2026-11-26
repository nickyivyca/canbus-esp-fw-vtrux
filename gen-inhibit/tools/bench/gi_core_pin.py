#!/usr/bin/env python3
"""gen-inhibit's shared/bench (the core), from the checkout of the tag the project pins.

Port plan goal 20 item 8, port step 4 item 1 (the gen-inhibit reviewer's batch of
2026-11-25): gen-inhibit takes shared/bench only as a pinned tag, from its sibling
checkout ../shared-<tag>/ beside the git worktree's root, and no longer from PYTHONPATH.
The pin is the "shared/bench" entry of the IDF project's pin file, beside the build
tooling's own entry:

    gen-inhibit/firmware/shared_pins.json
    {"shared/tools": {...},                  the build tooling's (vtrux_tools.py)
     "shared/bench": {"tag": "2026.11.23.5",
                      "commit": "<40 hex: the commit the tag marks>",
                      "digest": "<64 hex: R7 of shared/bench at it>"}}

The tester owns the "shared/bench" entry. It moves only on a batch from the reviewer,
since moving it is also the decision to re-test (goal 12).

    python3.13 -B gen-inhibit/tools/bench/gi_core_pin.py check
    python3.13 -B gen-inhibit/tools/bench/gi_core_pin.py where
    python3.13 -B gen-inhibit/tools/bench/gi_core_pin.py entry <tag>
    python3.13 -B gen-inhibit/tools/bench/gi_core_pin.py module <module> [args...]

check   runs the checks below and prints the pin; exit 0 passes, 2 refused.
where   prints the checked shared/bench folder.
entry   prints the entry the checkout ../shared-<tag>/ would give, for a pin move; it
        writes nothing.
module  runs a core module as `python -m <module> [args...]` would, after the checks,
        with the checked shared/bench first on sys.path: the chain runner is
        `module vtrux_bench.run run --suite ... --settings ...`, the bench lock
        `module vtrux_bench.bench_lock status`. Only vtrux_bench modules are taken.

THE CHECKS (check(), use_bench()). They are shared/tools R23's, run by the pinned
commit's own shared/tools code: vtrux_build/pin.py and vtrux_build/digest.py are read
from git's objects at the pin's commit (`git show`), never from the disk, and run with
the component "shared/bench". Nothing of them is copied here. In order:
  1. the pin file has the entry: a tag that is not a commit id (7 to 40 hex digits) and
     can name a folder, the 40-hex commit and the 64-hex digest;
  2. the checkout ../shared-<tag>/ beside the worktree's root exists (if not, the
     refusal prints the commands that create it, read-only);
  3. its HEAD is the pin's commit, and the tag there marks that commit;
  4. the pin's digest is R7 over shared/bench at that commit, from git's objects
     (pin.git_digest);
  5. every file git tracks under shared/bench at that commit is on disk with its bytes
     and git's mode, and none is missing (pin.tracked_differences; a read-only file
     passes);
  6. no other file is under shared/bench, beyond those the checkout's committed
     .gitignore files list, and nothing is staged there (pin.untracked_files).
use_bench() then puts the checked shared/bench first on sys.path. A vtrux_bench
imported earlier is kept only if it was imported from that folder; else it is refused.

The worktree root is git's (`git rev-parse --show-toplevel` from this file's folder),
so a copy of this file in a scratch folder inside the worktree finds the same pin and
checkout.
"""
import json
import os
import re
import runpy
import subprocess
import sys
import types

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = "shared/bench"
PIN_REL = os.path.join("gen-inhibit", "firmware", "shared_pins.json")
CHECKER = ("digest", "pin")         # shared/tools/vtrux_build/<name>.py, from git
CHECKER_DIR = "shared/tools/vtrux_build"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOOKS_LIKE_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
_SHOWN = 20
START = HERE        # where the worktree root is looked for; the tests move it
_DONE = {}          # bench folder of a passed check, per (pin file, tag, commit, digest)


class Refused(Exception):
    """The pin is missing or wrong, or the checkout is not the pinned commit's. str() is
    the reason."""


def _git(root, *args):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace").strip()


def worktree_root(start=None):
    rc, out, err = _git(start or START, "rev-parse", "--show-toplevel")
    top = out.decode("utf-8", "replace").strip()
    if rc != 0 or not top:
        raise Refused("%s is not in a git worktree, so the pinned checkout ../shared-<tag>/ "
                      "cannot be found (%s)" % (start or START, err))
    return os.path.normpath(top)


def pin_file(start=None):
    return os.path.join(worktree_root(start), PIN_REL)


def read_pin(path):
    """The "shared/bench" entry of the pin file at path, {"tag", "commit", "digest"}.
    Refused if the file or the entry is missing or malformed, or the tag is a commit id."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise Refused("no pin file: %s (%s)" % (path, e))
    except ValueError as e:
        raise Refused("%s is not valid JSON: %s" % (path, e))
    pin = data.get(COMPONENT) if isinstance(data, dict) else None
    if not isinstance(pin, dict):
        raise Refused("%s has no %r entry, so shared/bench is not pinned (port plan goal 20 "
                      "item 8)" % (path, COMPONENT))
    tag, commit, digest = pin.get("tag"), pin.get("commit"), pin.get("digest")
    if not isinstance(tag, str) or not tag.strip():
        raise Refused("the %r pin in %s names no tag; it is pinned at a tag, not a commit"
                      % (COMPONENT, path))
    if _LOOKS_LIKE_COMMIT.match(tag):
        raise Refused("the %r pin in %s names %r as its tag, which is a commit id; it is "
                      "pinned at a tag, not a commit" % (COMPONENT, path, tag))
    if tag != tag.strip() or "/" in tag or "\\" in tag:
        raise Refused("the %r pin in %s names the tag %r, which cannot be a checkout "
                      "folder's name (../shared-<tag>/)" % (COMPONENT, path, tag))
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise Refused("the %r pin in %s must give the tag's commit as 40 lower-case hex "
                      "digits, not %r" % (COMPONENT, path, commit))
    if not isinstance(digest, str) or not _DIGEST.match(digest):
        raise Refused("the %r pin in %s must give the R7 digest as 64 lower-case hex "
                      "digits, not %r" % (COMPONENT, path, digest))
    return {"tag": tag, "commit": commit, "digest": digest}


def create_commands(top, tag):
    checkout = os.path.normpath(os.path.join(top, os.pardir, "shared-" + tag))
    return ('    git -C "%s" worktree add --detach ../shared-%s %s\n'
            '    chmod -R a-w "%s"' % (top, tag, tag, os.path.join(checkout, "shared")))


def checkout_of(tag, start=None):
    """(checkout, worktree root) for tag. Refused, naming the commands that create it, if
    the checkout is missing."""
    top = worktree_root(start)
    checkout = os.path.normpath(os.path.join(top, os.pardir, "shared-" + tag))
    if not os.path.isdir(os.path.join(checkout, *COMPONENT.split("/"))):
        raise Refused("the pinned checkout %s is missing. Create it, read-only and detached "
                      "at the pin's tag, with:\n%s" % (checkout, create_commands(top, tag)))
    return checkout, top


def _head_and_tag(checkout, tag):
    """(HEAD, the commit tag marks) in checkout; None for one git cannot give."""
    rc, out, _ = _git(checkout, "rev-parse", "--verify", "HEAD")
    head = out.decode().strip() if rc == 0 else None
    rc, out, _ = _git(checkout, "rev-parse", "--verify", "--quiet", "refs/tags/%s^{commit}" % tag)
    tagged = out.decode().strip() if rc == 0 else None
    return head or None, tagged or None


def checker(checkout, commit):
    """shared/tools' vtrux_build pin and digest modules as they are at commit, read from
    git's objects in checkout and run as a private package; returns the pin module."""
    name = "gi_core_pin_vtrux_build_" + commit[:12]
    if name + ".pin" in sys.modules:
        return sys.modules[name + ".pin"]
    pkg = types.ModuleType(name)
    pkg.__path__ = []
    pkg.__package__ = name
    mods = {}
    for sub in CHECKER:
        path = "%s/%s.py" % (CHECKER_DIR, sub)
        rc, src, err = _git(checkout, "show", "%s:%s" % (commit, path))
        if rc != 0:
            raise Refused("the pin's commit %s has no %s, so it cannot be checked by its own "
                          "shared/tools R23 code (%s)" % (commit, path, err))
        mod = types.ModuleType(name + "." + sub)
        mod.__package__ = name
        mod.__file__ = "<git %s:%s>" % (commit[:12], path)
        mods[sub] = (mod, src, path)
    sys.modules[name] = pkg
    try:
        for sub in CHECKER:
            mod, src, path = mods[sub]
            sys.modules[mod.__name__] = mod
            exec(compile(src, mod.__file__, "exec"), mod.__dict__)
            setattr(pkg, sub, mod)
    except Exception:
        for sub in CHECKER:
            sys.modules.pop(name + "." + sub, None)
        sys.modules.pop(name, None)
        raise
    pin = mods["pin"][0]
    for fn in ("git_digest", "tracked_differences", "untracked_files"):
        if not callable(getattr(pin, fn, None)):
            raise Refused("shared/tools' vtrux_build/pin.py at %s has no %s(): the pin's "
                          "commit is older than shared/tools R23's git check (2026.11.17.3)"
                          % (commit, fn))
    return pin


def _listed(items):
    shown = "\n".join("    " + s for s in items[:_SHOWN])
    if len(items) > _SHOWN:
        shown += "\n    ... and %d more" % (len(items) - _SHOWN)
    return shown


def check(start=None):
    """The checks in the module docstring. Returns {"tag", "commit", "digest", "folder",
    "pin_file"}: the pin's, now checked, and the checked shared/bench folder."""
    path = pin_file(start)
    pin = read_pin(path)
    key = (path, pin["tag"], pin["commit"], pin["digest"])
    if key in _DONE:
        return dict(pin, folder=_DONE[key], pin_file=path)
    checkout, _ = checkout_of(pin["tag"], start)
    head, tagged = _head_and_tag(checkout, pin["tag"])
    if head != pin["commit"]:
        raise Refused("the checkout %s is at %s, and the pin says %s"
                      % (checkout, head or "(unreadable)", pin["commit"]))
    if tagged is None:
        raise Refused("the pin names %r as its tag, and the checkout's repository has no tag "
                      "of that name; shared/bench is pinned at a tag" % pin["tag"])
    if tagged != pin["commit"]:
        raise Refused("the tag %s marks %s, and the pin says %s"
                      % (pin["tag"], tagged, pin["commit"]))
    P = checker(checkout, pin["commit"])
    try:
        digest = P.git_digest(checkout, pin["commit"], COMPONENT)
        if digest != pin["digest"]:
            raise Refused("%s at %s has the digest %s in git, and the pin says %s"
                          % (COMPONENT, pin["commit"], digest, pin["digest"]))
        folder = os.path.join(checkout, *COMPONENT.split("/"))
        changed = P.tracked_differences(checkout, pin["commit"], COMPONENT)
        if changed:
            raise Refused("%d file(s) git tracks under %s differ on disk from the pinned "
                          "commit %s:\n%s" % (len(changed), folder, pin["commit"], _listed(changed)))
        extra = P.untracked_files(checkout, pin["commit"], COMPONENT)
        if extra:
            raise Refused("%s holds %d file(s) the pinned commit does not, beyond those its "
                          "committed .gitignore files list:\n%s"
                          % (folder, len(extra), _listed(extra)))
    except P.PinError as e:
        raise Refused(str(e))
    _DONE[key] = folder
    return dict(pin, folder=folder, pin_file=path)


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def use_bench(start=None):
    """The checked shared/bench folder, first on sys.path. Refused if the checks fail, or
    if a vtrux_bench imported earlier came from anywhere else."""
    folder = check(start)["folder"]
    vb = sys.modules.get("vtrux_bench")
    if vb is not None:
        f = getattr(vb, "__file__", None)
        got = os.path.dirname(os.path.dirname(os.path.abspath(f))) if f else None
        if got is None or not _same(got, folder):
            raise Refused("vtrux_bench is already imported from %s, not from the pinned %s"
                          % (got or "(no file)", folder))
    if not sys.path or not _same(sys.path[0], folder):
        sys.path[:] = [p for p in sys.path if not (p and _same(p, folder))]
        sys.path.insert(0, folder)
    return folder


def entry(tag, start=None):
    """The "shared/bench" entry the checkout ../shared-<tag>/ gives: its HEAD, checked to
    be the tag's commit, and R7 of shared/bench there from git's objects."""
    checkout, _ = checkout_of(tag, start)
    head, tagged = _head_and_tag(checkout, tag)
    if head is None or tagged != head:
        raise Refused("the checkout %s is at %s, and the tag %s marks %s"
                      % (checkout, head, tag, tagged))
    P = checker(checkout, head)
    try:
        digest = P.git_digest(checkout, head, COMPONENT)
    except P.PinError as e:
        raise Refused(str(e))
    return {"tag": tag, "commit": head, "digest": digest}


def run_module(name, args):
    """Runs the core module name as `python -m name args` would, after use_bench()."""
    if name != "vtrux_bench" and not name.startswith("vtrux_bench."):
        raise Refused("only vtrux_bench modules are run here, not %r" % name)
    use_bench()
    sys.argv = [name] + list(args)
    runpy.run_module(name, run_name="__main__", alter_sys=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else None
    try:
        if cmd == "check" and len(argv) == 1:
            c = check()
            print("shared/bench %s %s digest %s: checked (%s)"
                  % (c["tag"], c["commit"], c["digest"], c["folder"]))
            return 0
        if cmd == "where" and len(argv) == 1:
            print(use_bench())
            return 0
        if cmd == "entry" and len(argv) == 2:
            print(json.dumps({COMPONENT: entry(argv[1])}, indent=2))
            return 0
        if cmd == "module" and len(argv) >= 2:
            run_module(argv[1], argv[2:])
            return 0
    except Refused as e:
        print("gi_core_pin: REFUSED: %s" % e, file=sys.stderr)
        return 2
    print("usage: gi_core_pin.py check | where | entry <tag> | module <vtrux_bench module> [args...]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
