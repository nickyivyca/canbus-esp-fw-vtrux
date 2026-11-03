#!/usr/bin/env python3
"""The harness's shared code, from the read-only checkout of the tag the
project pins.

Charge-interposer tester, port step 2a (the reviewer, 2026-11-03; port plan
goal 20 item 8): the harness takes shared code only as a pinned tag, from
its read-only sibling checkout ../shared-<tag>/ beside the worktree's root,
checked against the project's committed pin file. This branch's own copy of
shared/ goes. The pin file is the IDF project's, beside the build tooling's
own pin (shared/tools R23), and is owned per entry:

    charge-interposer/firmware/shared_pins.json
    {"shared/tools":     {...},     the implementor's (the build tooling)
     "shared/bench":     {"tag": "2026.11.03",
                          "commit": "<40 hex: the commit the tag marks>",
                          "digest": "<64 hex: R7 of shared/bench at it>"},
     "shared/twai_port": {...}}     host_bridge's TWAI_FAKE=shared fake

The tester's entries are "shared/bench" (the chain runner, the acceptance
runner, the bench lock and the decode helper) and "shared/twai_port" (the
one host fake of the TWAI driver, test/). A pin moves only on a batch from
the reviewer, since moving it is also the decision to re-test (goal 12).

    py -3.14 -B charge-interposer/shared_pin.py check shared/bench
    py -3.14 -B charge-interposer/shared_pin.py where shared/bench
    py -3.14 -B charge-interposer/shared_pin.py entry <tag> shared/bench

`entry` prints, for a pin move, the entry the checkout ../shared-<tag>/
would give (it writes nothing); `where` prints the component's folder once
it passes the checks.

THE CHECKS (folder(), check, where). A run's shared component is the
pinned one or the harness refuses it, as the build tooling's pin does
(shared/tools R16, R23):
  1. the pin file has the entry: a tag that is not a commit id and can name
     a folder, the 40-hex commit and the 64-hex R7 digest;
  2. the checkout ../shared-<tag>/ beside the worktree's root exists (if
     not, the refusal prints the command that creates it);
  3. its HEAD is the pin's commit, and the tag there marks that commit;
  4. the component's folder in it has the pin's R7 digest, computed by that
     checkout's own shared/tools (vtrux_build.digest, loaded by file, with
     no bytecode written into the checkout).
use_bench() then puts the checked shared/bench on sys.path, and refuses a
vtrux_bench imported earlier from anywhere else.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "firmware", "shared_pins.json")
START = HERE            # where worktree() starts looking; tests move it
OURS = ("shared/bench", "shared/twai_port")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOOKS_LIKE_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")


class SharedPinError(RuntimeError):
    """The pin is missing or wrong, or the checkout is not the pinned
    version. str() is the reason."""


def _component(name):
    if name not in OURS:
        raise SharedPinError("%r is not a component this harness pins (%s)"
                             % (name, ", ".join(OURS)))
    return name


def read(name, path=None):
    """The project's pin of the component, {"tag", "commit", "digest"}."""
    _component(name)
    path = path or PIN_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise SharedPinError("no pin file: %s (%s)" % (path, e))
    except ValueError as e:
        raise SharedPinError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get(name), dict):
        raise SharedPinError("%s has no %r entry, so the harness's %s is not "
                             "pinned" % (path, name, name))
    pin = data[name]
    tag, commit, digest = pin.get("tag"), pin.get("commit"), pin.get("digest")
    if not isinstance(tag, str) or not tag.strip():
        raise SharedPinError("the %r pin in %s names no tag; shared code is "
                             "pinned at a tag, not a commit" % (name, path))
    if _LOOKS_LIKE_COMMIT.match(tag):
        raise SharedPinError("the %r pin in %s names %r as its tag, which is "
                             "a commit id; shared code is pinned at a tag"
                             % (name, path, tag))
    if tag != tag.strip() or "/" in tag or "\\" in tag:
        raise SharedPinError("the %r pin in %s names the tag %r, which cannot "
                             "be a checkout folder's name (../shared-<tag>/)"
                             % (name, path, tag))
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise SharedPinError("the %r pin in %s must give the tag's commit as "
                             "40 lower-case hex digits, not %r" % (name, path, commit))
    if not isinstance(digest, str) or not _DIGEST.match(digest):
        raise SharedPinError("the %r pin in %s must give the R7 digest as 64 "
                             "lower-case hex digits, not %r" % (name, path, digest))
    return {"tag": tag, "commit": commit, "digest": digest}


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, r.stdout.strip()


def worktree(start=None):
    """The root of the git worktree that holds start (default START)."""
    rc, top = _git(start or START, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        raise SharedPinError("%s is not in a git worktree, so the pinned "
                             "checkout ../shared-<tag>/ cannot be found"
                             % (start or START))
    return os.path.normpath(top)


def checkout_dir(tag, start=None):
    """(checkout, worktree root): ../shared-<tag>/ beside the worktree's root."""
    top = worktree(start)
    return os.path.normpath(os.path.join(top, os.pardir, "shared-" + tag)), top


def create_command(top, tag):
    return 'git -C "%s" worktree add --detach ../shared-%s %s' % (top, tag, tag)


def digest_of(checkout, folder):
    """R7 digest of folder, by the checkout's own shared/tools
    (vtrux_build/digest.py, loaded by file; no bytecode is written)."""
    src = os.path.join(checkout, "shared", "tools", "vtrux_build", "digest.py")
    if not os.path.isfile(src):
        raise SharedPinError("%s has no shared/tools/vtrux_build/digest.py, "
                             "so the R7 digest cannot be taken" % checkout)
    spec = importlib.util.spec_from_file_location("_shared_pin_digest", src)
    mod = importlib.util.module_from_spec(spec)
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod.component_digest(folder)


def folder(name, path=None, start=None):
    """The checked component folder, <checkout>/shared/<name minus shared/>,
    after checks 1-4. SharedPinError says which one fails."""
    pin = read(name, path)
    tag = pin["tag"]
    checkout, top = checkout_dir(tag, start)
    if not os.path.isdir(checkout):
        raise SharedPinError("the pinned checkout %s does not exist. Create it, "
                             "read-only and detached at the pin's tag, with:\n"
                             "    %s" % (checkout, create_command(top, tag)))
    rc, head = _git(checkout, "rev-parse", "--verify", "HEAD")
    if rc != 0 or head != pin["commit"]:
        raise SharedPinError("the checkout %s is at %s, and the %r pin says %s"
                             % (checkout, head or "(unreadable)", name, pin["commit"]))
    rc, tagged = _git(checkout, "rev-parse", "--verify", "--quiet",
                      "refs/tags/%s^{commit}" % tag)
    if rc != 0 or not tagged:
        raise SharedPinError("the %r pin names %r as its tag, and the "
                             "checkout's repository has no tag of that name"
                             % (name, tag))
    if tagged != pin["commit"]:
        raise SharedPinError("the tag %s marks %s, and the %r pin says %s"
                             % (tag, tagged, name, pin["commit"]))
    d = os.path.join(checkout, *name.split("/"))
    if not os.path.isdir(d):
        raise SharedPinError("the checkout %s has no %s" % (checkout, name))
    got = digest_of(checkout, d)
    if got != pin["digest"]:
        raise SharedPinError("%s has the digest %s, and the %r pin says %s: it "
                             "is not the pinned version, or a file in it was "
                             "changed (R7)" % (d, got, name, pin["digest"]))
    return d


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def use_bench(path=None, start=None):
    """Put the checked shared/bench on sys.path, first; refuse a vtrux_bench
    already imported from anywhere else. Returns the folder."""
    d = folder("shared/bench", path, start)
    mod = sys.modules.get("vtrux_bench")
    if mod is not None:
        where = os.path.dirname(os.path.dirname(os.path.abspath(
            getattr(mod, "__file__", None) or "")))
        if not getattr(mod, "__file__", None) or not _same(where, d):
            raise SharedPinError("vtrux_bench is already imported from %s, not "
                                 "from the pinned %s" % (where or "(no file)", d))
    if not any(_same(p, d) for p in sys.path if p):
        sys.path.insert(0, d)
    return d


def entry(tag, name, start=None):
    """The entry the checkout ../shared-<tag>/ gives for name (for a pin
    move; writes nothing)."""
    _component(name)
    checkout, top = checkout_dir(tag, start)
    if not os.path.isdir(checkout):
        raise SharedPinError("no checkout %s; create it with:\n    %s"
                             % (checkout, create_command(top, tag)))
    rc, tagged = _git(checkout, "rev-parse", "--verify", "--quiet",
                      "refs/tags/%s^{commit}" % tag)
    rc2, head = _git(checkout, "rev-parse", "--verify", "HEAD")
    if rc != 0 or rc2 != 0 or not tagged or tagged != head:
        raise SharedPinError("the checkout %s is at %s, and the tag %s marks %s"
                             % (checkout, head or "?", tag, tagged or "nothing"))
    d = os.path.join(checkout, *name.split("/"))
    return {"tag": tag, "commit": head, "digest": digest_of(checkout, d)}


def main(argv):
    try:
        if len(argv) == 2 and argv[0] in ("check", "where"):
            d = folder(argv[1])
            print(d if argv[0] == "where" else "OK: %s is the pinned %s" % (d, argv[1]))
            return 0
        if len(argv) == 3 and argv[0] == "entry":
            print(json.dumps({argv[2]: entry(argv[1], argv[2])}, indent=2))
            return 0
    except SharedPinError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    print("usage: shared_pin.py check|where <component> | entry <tag> <component>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
