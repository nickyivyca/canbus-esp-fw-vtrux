#!/usr/bin/env python3
"""The harness's shared code, from the checkout of the tag the project pins,
checked through git (shared/tools R23).

Charge-interposer tester, port step 2a (the reviewer, 2026-11-03; port plan
goal 20 item 8): the harness takes shared code only as a pinned tag, from
its sibling checkout ../shared-<tag>/ beside the worktree's root, checked
against the project's committed pin file. This branch's own copy of
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
  4. the component's folder in it is the pinned commit's, by shared/tools
     R23's git rule (changed 2026-11-17 from an R7 digest of the files on
     disk; the charge-interposer reviewer's batch for tag 2026.11.17.3):
     a. the pin's digest is R7 over the component's files at the commit,
        read from git's objects (git_digest), not from the disk;
     b. every file git tracks under the component at the commit is on disk
        with exactly its bytes, read raw, so git's line-ending filters play
        no part, and with the mode git records for it: a regular file, an
        executable (the owner's execute bit) or a symbolic link (its
        target is the blob's bytes). Other permission bits are not
        compared, so a read-only file passes (a pinned checkout made before
        R23 may be read-only), and a flipped execute bit is refused. None
        is missing;
     c. no other file is under the component, except files the checkout's
        own committed .gitignore files list (`git ls-files --others
        --exclude-per-directory=.gitignore`, never --exclude-standard, so a
        machine's core.excludesFile and info/exclude hide nothing). A
        .gitignore that could hide files must be the committed one: an
        uncommitted one under the component, or one in a folder above it
        that differs from the commit's, is refused.
     shared/main's .gitignore lists Python's bytecode from tag 2026.11.17.3,
     so a __pycache__ under a component pinned there passes. This check is
     the harness's own, mirroring shared/tools' vtrux_build/pin.py at
     2026.11.17.3 rather than loading the checkout's: a component may be
     pinned at a tag whose shared/tools has no such check, and R7's
     exclusions (EXCLUDED_NAMES, EXCLUDED_SUFFIXES) are R7's at every tag
     so far. On Windows the execute bit is not compared (the disk has
     none), and a symbolic link checked out as a plain file passes when its
     bytes are the link's target.
use_bench() then puts the checked shared/bench on sys.path. A vtrux_bench
imported earlier is kept only if its own checkout passes checks 3 and 4
against the pin, wherever it is (the acceptance runner's core, started from
the pinned checkout, loads the harness from a checkout of the list's commit
with no shared-<tag>/ beside it; 2026-11-04); else it is refused.
"""
import hashlib
import json
import os
import posixpath
import re
import stat
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
# R7's exclusions (shared/tools vtrux_build/digest.py, the same at every tag
# so far): in any folder, files named .gitignore and files ending .md
EXCLUDED_NAMES = {".gitignore"}
EXCLUDED_SUFFIXES = (".md",)
REGULAR, EXECUTABLE, SYMLINK = "100644", "100755", "120000"
_SHOWN = 20          # differences listed in a refusal
_LINK_HOPS = 40      # symbolic links followed in a row before giving up


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


def _git_bytes(root, args, stdin=None):
    """git's raw stdout. SharedPinError, with git's message, if it fails."""
    try:
        r = subprocess.run(["git", "-C", root] + list(args), input=stdin,
                           capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise SharedPinError("git %s failed in %s: %s" % (" ".join(args), root, e))
    if r.returncode != 0:
        raise SharedPinError("git %s failed in %s: %s"
                             % (" ".join(args), root,
                                r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout


def excluded(name):
    """R7 leaves the file out (by its name, in any folder)."""
    return name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES)


def tree(repo, commit):
    """{path: (mode, type, object id)} for every entry of commit's tree,
    recursively, paths from the repository's root with "/"."""
    out = {}
    raw = _git_bytes(repo, ["ls-tree", "-r", "-t", "-z", "--full-tree", commit])
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split(" ")
        out[path.decode("utf-8")] = (mode, kind, oid)
    return out


def blobs(repo, oids):
    """{object id: bytes} for the blobs oids, as git stores them: no
    filter, so no line-ending conversion."""
    oids = sorted(set(oids))
    if not oids:
        return {}
    raw = _git_bytes(repo, ["cat-file", "--batch"],
                     stdin="".join(o + "\n" for o in oids).encode("ascii"))
    out, at = {}, 0
    for oid in oids:
        nl = raw.index(b"\n", at)
        head = raw[at:nl].decode("ascii").split(" ")
        if len(head) != 3 or head[0] != oid or head[1] != "blob":
            raise SharedPinError("git cat-file gave %r for the blob %s in %s"
                                 % (" ".join(head), oid, repo))
        size = int(head[2])
        out[oid] = raw[nl + 1:nl + 1 + size]
        at = nl + 1 + size + 1
    return out


def _under(path, component):
    return path.startswith(component + "/")


def git_digest(repo, commit, component):
    """R7 of component at commit, from git's objects: the digest R7 gives
    for those files checked out. A symbolic link is read through, as R7
    reads it on disk: its target's bytes when it leads to a file in the
    commit, nothing when it leads to a folder, and SharedPinError when it
    leads nowhere in the commit."""
    entries = tree(repo, commit)
    if component not in entries or entries[component][1] != "tree":
        raise SharedPinError("%s has no folder %s at %s" % (repo, component, commit))
    files = [p for p, (_m, kind, _o) in entries.items()
             if _under(p, component) and kind == "blob"]
    link_text = blobs(repo, [entries[p][2] for p in files
                             if entries[p][0] == SYMLINK])
    chosen = []                                  # (relative path, blob id)
    for p in files:
        if excluded(posixpath.basename(p)):
            continue
        q, hops = p, 0
        while entries.get(q, (None,))[0] == SYMLINK:
            hops += 1
            oid = entries[q][2]
            if oid not in link_text:
                link_text.update(blobs(repo, [oid]))
            q = posixpath.normpath(posixpath.join(
                posixpath.dirname(q), link_text[oid].decode("utf-8")))
            if hops > _LINK_HOPS or q.startswith("../") or q == "..":
                q = None
                break
        if q is None or q not in entries:
            raise SharedPinError("%s at %s is a symbolic link that leads to no "
                                 "file in the commit, so it has no R7 digest"
                                 % (p, commit))
        if entries[q][1] == "tree":
            continue
        chosen.append((p[len(component) + 1:], entries[q][2]))
    chosen.sort(key=lambda t: t[0].encode("utf-8"))
    content = blobs(repo, [oid for _r, oid in chosen])
    h = hashlib.sha256()
    for rel, oid in chosen:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(content[oid])
        h.update(b"\0")
    return h.hexdigest()


def _disk(checkout, path):
    return os.path.join(checkout, *path.split("/"))


def _kind_of(st):
    if stat.S_ISLNK(st.st_mode):
        return "a symbolic link"
    if stat.S_ISDIR(st.st_mode):
        return "a folder"
    if stat.S_ISREG(st.st_mode):
        return "a regular file"
    return "a special file"


def tracked_differences(checkout, commit, component):
    """["<path>: <what differs>"] for each file git tracks under component
    at commit whose bytes or mode on disk are not the commit's, or that is
    missing (check 4b)."""
    entries = tree(checkout, commit)
    files = sorted(p for p, (_m, kind, _o) in entries.items()
                   if _under(p, component) and kind != "tree")
    content = blobs(checkout, [entries[p][2] for p in files
                               if entries[p][1] == "blob"])
    windows = os.name == "nt"
    out = []
    for p in files:
        mode, kind, oid = entries[p]
        disk = _disk(checkout, p)
        try:
            st = os.lstat(disk)
        except FileNotFoundError:
            out.append("%s: missing" % p)
            continue
        except OSError as e:
            out.append("%s: unreadable (%s)" % (p, e))
            continue
        if kind != "blob":
            out.append("%s: git records it as %s %s, which a pinned checkout "
                       "does not hold" % (p, kind, mode))
            continue
        want = content[oid]
        if mode == SYMLINK:
            if stat.S_ISLNK(st.st_mode):
                got = os.fsencode(os.readlink(disk))
                if got != want:
                    out.append("%s: a symbolic link to %r, and git has %r"
                               % (p, got, want))
                continue
            if not (windows and stat.S_ISREG(st.st_mode)):
                out.append("%s: git records a symbolic link, and the disk has %s"
                           % (p, _kind_of(st)))
                continue
        elif not stat.S_ISREG(st.st_mode):
            out.append("%s: git records a %s file, and the disk has %s"
                       % (p, "executable" if mode == EXECUTABLE else "regular",
                          _kind_of(st)))
            continue
        elif not windows:
            executable = bool(st.st_mode & stat.S_IXUSR)
            if executable != (mode == EXECUTABLE):
                out.append("%s: %s on disk, and git records it %s"
                           % (p, "executable" if executable else "not executable",
                              "executable" if mode == EXECUTABLE else "not executable"))
                continue
        try:
            with open(disk, "rb") as f:
                got = f.read()
        except OSError as e:
            out.append("%s: unreadable (%s)" % (p, e))
            continue
        if got != want:
            out.append("%s: its bytes differ from the commit's (%d bytes on disk, "
                       "%d in git%s)" % (p, len(got), len(want),
                                         "; line endings differ" if
                                         got.replace(b"\r\n", b"\n")
                                         == want.replace(b"\r\n", b"\n") else ""))
    return out


def untracked_files(checkout, commit, component):
    """["<path>: <why>"] for each file under component that git does not
    track and that no committed .gitignore lists, and for each .gitignore
    that could hide files without being the committed one (check 4c)."""
    raw = _git_bytes(checkout, ["ls-files", "-z", "--others",
                                "--exclude-per-directory=.gitignore",
                                "--", component])
    out = ["%s: not in the commit, and no committed .gitignore lists it"
           % p.decode("utf-8") for p in raw.split(b"\0") if p]
    # an uncommitted .gitignore under the component can hide itself, so
    # list them with no excludes at all
    raw = _git_bytes(checkout, ["ls-files", "-z", "--others", "--", component])
    for p in raw.split(b"\0"):
        p = p.decode("utf-8")
        if p and posixpath.basename(p) == ".gitignore" and not any(
                o.startswith(p + ":") for o in out):
            out.append("%s: a .gitignore not in the commit, which could hide "
                       "files" % p)
    # the folders above the component: their .gitignore applies too, so it
    # must be the commit's
    parts = component.split("/")[:-1]
    for i in range(len(parts) + 1):
        gi = "/".join(parts[:i] + [".gitignore"])
        disk = _disk(checkout, gi)
        if not os.path.lexists(disk):
            continue
        r = subprocess.run(["git", "-C", checkout, "cat-file", "blob",
                            "%s:%s" % (commit, gi)], capture_output=True)
        try:
            with open(disk, "rb") as f:
                got = f.read()
        except OSError as e:
            out.append("%s: unreadable (%s)" % (gi, e))
            continue
        if r.returncode != 0:
            out.append("%s: a .gitignore not in the commit, which could hide "
                       "files under %s" % (gi, component))
        elif got != r.stdout:
            out.append("%s: differs from the commit's, and it could hide files "
                       "under %s" % (gi, component))
    return out


def _listed(items):
    shown = "\n".join("    " + s for s in items[:_SHOWN])
    if len(items) > _SHOWN:
        shown += "\n    ... and %d more" % (len(items) - _SHOWN)
    return shown


def folder(name, path=None, start=None):
    """The checked component folder, <checkout>/shared/<name minus shared/>,
    after checks 1-4. SharedPinError says which one fails."""
    pin = read(name, path)
    tag = pin["tag"]
    checkout, top = checkout_dir(tag, start)
    if not os.path.isdir(checkout):
        raise SharedPinError("the pinned checkout %s does not exist. Create it, "
                             "detached at the pin's tag, with:\n"
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
    got = git_digest(checkout, pin["commit"], name)
    if got != pin["digest"]:
        raise SharedPinError("%s at %s has the digest %s in git, and the %r pin "
                             "says %s: the pin's digest is not that commit's "
                             "(R23, R7)" % (name, pin["commit"], got, name,
                                            pin["digest"]))
    changed = tracked_differences(checkout, pin["commit"], name)
    if changed:
        raise SharedPinError("%d file(s) git tracks under %s differ on disk from "
                             "the pinned commit %s (R23):\n%s"
                             % (len(changed), d, pin["commit"], _listed(changed)))
    extra = untracked_files(checkout, pin["commit"], name)
    if extra:
        raise SharedPinError("%s holds %d file(s) the pinned commit does not, "
                             "beyond those its committed .gitignore files list "
                             "(R23):\n%s" % (d, len(extra), _listed(extra)))
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
    return {"tag": tag, "commit": head, "digest": git_digest(checkout, head, name)}


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
