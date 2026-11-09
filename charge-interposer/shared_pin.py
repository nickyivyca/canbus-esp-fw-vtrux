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
     "shared/twai_port": {...},     host_bridge's vehicle port and fake
     "shared/can_port":  {...}}     host_bridge's port interface

The tester's entries are "shared/bench" (the chain runner, the acceptance
runner, the bench lock and the decode helper), "shared/twai_port" (the one
host fake of the TWAI driver, test/, and since port step 2b the real port
host_bridge compiles on it) and "shared/can_port" (since port step 2b: the
port interface host_bridge compiles twai_port behind). A pin moves only on
a batch from the reviewer, since moving it is also the decision to re-test
(goal 12).

    py -3.14 -B charge-interposer/shared_pin.py check shared/bench
    py -3.14 -B charge-interposer/shared_pin.py where shared/bench
    py -3.14 -B charge-interposer/shared_pin.py entry <tag> shared/bench
    py -3.14 -B charge-interposer/shared_pin.py idf-match

THE HOST BUILD IS ON THE PRODUCT'S VERSIONS (idf-match; port step 2b,
the charge-interposer reviewer's rulings for 2b, 2026-11-08). The
firmware takes vtrux/twai_port and vtrux/can_port from git at the versions
firmware/main/idf_component.yml names, and host_bridge compiles the same
two components from the pinned checkout. idf-match passes only if the
"shared/twai_port" and "shared/can_port" pins name one tag, and it is the
version idf_component.yml gives each; it reads the two files and nothing
else (no git), so it runs on any host. host_bridge/run.sh runs it before
any build.

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
use_bench() then puts the checked shared/bench on sys.path. A vtrux_bench
imported earlier is kept only if its own checkout passes checks 3 and 4
against the pin, wherever it is (the acceptance runner's core, started from
the pinned checkout, loads the harness from a checkout of the list's commit
with no shared-<tag>/ beside it; 2026-11-04); else it is refused.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "firmware", "shared_pins.json")
IDF_YML = os.path.join(HERE, "firmware", "main", "idf_component.yml")
START = HERE            # where worktree() starts looking; tests move it
OURS = ("shared/bench", "shared/twai_port", "shared/can_port")
# the pins idf-match compares, and each one's dependency in IDF_YML
IDF_DEPS = (("shared/twai_port", "vtrux/twai_port"),
            ("shared/can_port", "vtrux/can_port"))
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
    return _checked(checkout, name, pin)


def _checked(checkout, name, pin):
    """The component folder in checkout, after checks 3 and 4 against pin."""
    tag = pin["tag"]
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
    """Put the checked shared/bench on sys.path, first. Returns the folder.

    A vtrux_bench imported earlier is kept only if it is the shared/bench of
    a checkout that passes checks 3 and 4 against the pin, wherever that
    checkout is: the acceptance runner's own core, started from the pinned
    checkout, loads this harness from a checkout of the list's commit with
    no shared-<tag>/ beside it (2026-11-04). Anything else imported earlier
    is refused."""
    mod = sys.modules.get("vtrux_bench")
    if mod is None:
        d = folder("shared/bench", path, start)
    else:
        f = getattr(mod, "__file__", None)
        if not f:
            raise SharedPinError("vtrux_bench is already imported, from no file")
        where = os.path.dirname(os.path.dirname(os.path.abspath(f)))
        checkout = os.path.dirname(os.path.dirname(where))
        pin = read("shared/bench", path)
        try:
            d = _checked(checkout, "shared/bench", pin)
        except SharedPinError as e:
            raise SharedPinError("vtrux_bench is already imported from %s, which is not "
                                 "the pinned shared/bench: %s" % (where, e))
        if not _same(where, d):
            raise SharedPinError("vtrux_bench is already imported from %s, not "
                                 "from the pinned %s" % (where, d))
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


def idf_versions(path=None):
    """{dependency: version} for each IDF_DEPS dependency, from the
    manifest's top-level `dependencies:` map: the `version:` line inside
    that dependency's own block (indented under it, before the next key at
    its level). A dependency with no block, or a block with no version, is
    missing from the result."""
    path = path or IDF_YML
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise SharedPinError("cannot read %s (%s)" % (path, e))
    out = {}
    want = {dep for _n, dep in IDF_DEPS}
    cur, cur_indent = None, None
    for ln in lines:
        s = ln.split("#", 1)[0].rstrip()
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip(" "))
        key = s.strip()
        if cur is not None and indent <= cur_indent:
            cur = None
        if cur is None:
            if key.endswith(":") and key[:-1] in want:
                cur, cur_indent = key[:-1], indent
            continue
        m = re.match(r'^version:\s*"?([^"\s]+)"?$', key)
        if m and cur not in out:
            out[cur] = m.group(1)
    return out


def idf_match(path=None, yml=None):
    """The idf-match check (module docstring). The tag, or SharedPinError."""
    vers = idf_versions(yml)
    tags = set()
    for name, dep in IDF_DEPS:
        tag = read(name, path)["tag"]
        if dep not in vers:
            raise SharedPinError("%s names no version for %s, so the %r pin "
                                 "cannot be compared with it"
                                 % (yml or IDF_YML, dep, name))
        if tag != vers[dep]:
            raise SharedPinError("the %r pin is at %s, and %s takes %s at %s: "
                                 "host_bridge would build another version than "
                                 "the firmware's" % (name, tag, yml or IDF_YML,
                                                     dep, vers[dep]))
        tags.add(tag)
    if len(tags) != 1:
        raise SharedPinError("the %s pins name different tags (%s): twai_port is "
                             "taken with can_port at the same shared/main tag"
                             % (" and ".join(n for n, _d in IDF_DEPS),
                                ", ".join(sorted(tags))))
    return tags.pop()


def main(argv):
    try:
        if argv == ["idf-match"]:
            print("OK: %s and %s pinned at %s, as idf_component.yml takes them"
                  % (IDF_DEPS[0][0], IDF_DEPS[1][0], idf_match()))
            return 0
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
    print("usage: shared_pin.py check|where <component> | entry <tag> <component>"
          " | idf-match", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
