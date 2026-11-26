"""Pins on shared components (SPEC R16, R23): which version of each shared
component a project uses, and the check that a pinned checkout is that
version. The build tooling's own pin is one of them.

A pin is an entry of shared_pins.json in the project's root, committed with
the project, keyed by the component's folder:

    {"shared/tools": {"tag": "2026.10.24",
                      "commit": "<40 hex digits, the commit the tag marks>",
                      "digest": "<64 hex digits, R7 of shared/tools at it>"},
     "shared/bench": {...}}

A component's name is its folder below shared/, written with "/":
"shared/<folder>", or deeper, as "shared/boards/<board>". Each reader reads
only the entry it is asked for and neither rejects nor relies on others.

check(project_root, tools_root) is the build tooling's own check: R23 for
shared/tools, which must be the folder this code runs from. A release build
makes it before anything else. check_component(project_root, component)
makes the same check for any pinned component (R23's last sentence: a
project's bench and test tooling that checks a pinned checkout checks it the
same way), and returns the checked folder. entry(project_root, tag,
component) gives, for a pin move, the entry the checkout ../shared-<tag>/
would give; it writes nothing. The command line's `pin` command (main())
runs the tooling's own check first, then checks each named component.

R23 (changed 2026-11-17): the tooling runs from the checkout
../shared-<tag>/, relative to the root of the project's git worktree, and
that checkout is checked through git, not kept read-only. A component's pin
is checked against its own tag's checkout, so two components pinned at two
tags are checked in two checkouts. The checks refuse unless, in this order
(shared/tools is the component for check()):

  - the pin's component is a folder below shared/, and the pin file has its
    entry, with a tag (R16, below), a 40-hex commit and a 64-hex digest;
  - the checkout exists (if not, the refusal prints the command that
    creates it), and, for check(), the shared/tools folder this code runs
    from is its;
  - the checkout's HEAD is the pin's commit, and the pin's tag is a tag
    there that marks that commit;
  - the commit has the component's folder;
  - the pin's digest is R7 over the component at that commit, read from
    git's objects (git_digest), not from the disk;
  - every file git tracks under the component at that commit is on disk
    with exactly its bytes, read raw, so git's line-ending filters play no
    part, and with the mode git records for it: a regular file, an
    executable (the owner's execute bit) or a symbolic link (its target is
    the blob's bytes). Other permission bits are not compared, so a
    read-only file passes. None is missing;
  - there is no other file under the component, except files the
    checkout's own committed .gitignore files list: `git ls-files --others
    --exclude-per-directory=.gitignore`, never --exclude-standard, so a
    machine's own core.excludesFile and info/exclude hide nothing. A file
    staged in the checkout's index (git add) is no exception: every path
    the index holds under the component must be a file of the commit. A
    .gitignore that could hide files must itself be the committed one: an
    uncommitted .gitignore under the component, or one in a folder above it
    that differs from the commit's, is refused.

Only the named component's folder is checked: the rest of the checkout may
differ from the commit without a refusal.

shared/main's .gitignore lists __pycache__/ and *.pyc, so the bytecode
Python writes beside the code passes, and the checkout needs no -B.

On Windows the execute bit is not compared (the disk has none), and a
symbolic link checked out as a plain file passes when its bytes are the
link's target, as git writes it there without symlink support.

R16, the tooling-pin clause, applied to every component's pin: a pin that
names a commit rather than a tag is refused: no tag, an empty one, one that
reads as a commit id (7 to 40 hex digits), or one the checkout has no tag of
that name for. A tag that cannot name a folder (spaces around it, a / or a
\\) is refused too, since the checkout is ../shared-<tag>/.
"""

import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess

from .digest import excluded

PIN_FILE = "shared_pins.json"
COMPONENT = "shared/tools"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOOKS_LIKE_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
_NAME = re.compile(r"^shared(/[A-Za-z0-9_][A-Za-z0-9_.-]*)+$")
_SHOWN = 20          # differences listed in a refusal
_LINK_HOPS = 40      # symbolic links followed in a row before giving up

REGULAR, EXECUTABLE, SYMLINK = "100644", "100755", "120000"


class PinError(Exception):
    """The pin is missing or wrong, or this tooling is not the pinned
    version. str() is the reason."""


def _git(root, *args):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                       text=True)
    return r.returncode, r.stdout.strip()


def _git_bytes(root, args, stdin=None):
    """git's raw stdout. PinError, with git's message, if it fails."""
    r = subprocess.run(["git", "-C", root] + list(args), input=stdin,
                       capture_output=True)
    if r.returncode != 0:
        raise PinError("git %s failed in %s: %s (R23)"
                       % (" ".join(args), root,
                          r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout


def component_name(component):
    """component if it is a folder below shared/ ("shared/<folder>", or
    deeper, as "shared/boards/<board>"), written with "/", each name
    starting with a letter, a digit or "_" (so never "." or ".."), else
    PinError."""
    if not isinstance(component, str) or not _NAME.match(component):
        raise PinError("%r is not a shared component's name: a pin names a "
                       "folder below shared/, written with /, such as %r or "
                       "'shared/boards/wican_v3'" % (component, COMPONENT))
    return component


def _what(component):
    return ("the build tooling" if component == COMPONENT
            else "the project's %s" % component)


def tag_name(tag, where):
    """tag if a pin may name it (R16) and it can name the checkout folder
    ../shared-<tag>/, else PinError; where says whose tag it is."""
    if not isinstance(tag, str) or not tag.strip():
        raise PinError("%s names no tag; shared code is pinned at a tag, not "
                       "a commit (R16)" % where)
    if _LOOKS_LIKE_COMMIT.match(tag):
        raise PinError("%s names %r as its tag, which is a commit id; shared "
                       "code is pinned at a tag, not a commit (R16)"
                       % (where, tag))
    if tag != tag.strip() or "/" in tag or "\\" in tag:
        raise PinError("%s names the tag %r, which cannot be a checkout "
                       "folder's name (../shared-<tag>/, R23)" % (where, tag))
    return tag


def read(project_root, component=COMPONENT):
    """The project's pin of component ("shared/tools" unless named),
    {"tag", "commit", "digest"}. PinError if the name is not a component's,
    if the file or the entry is missing or malformed, or (R16) the pin names
    a commit rather than a tag."""
    component_name(component)
    path = os.path.join(project_root, PIN_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise PinError("no pin file: %s (%s). The project pins the shared "
                       "code it uses in %s, %s under %r (R23)"
                       % (path, e, PIN_FILE, _what(component), component))
    except ValueError as e:
        raise PinError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get(component), dict):
        raise PinError("%s has no %r entry, so %s is not pinned (R23)"
                       % (path, component, _what(component)))
    pin = data[component]
    tag, commit, digest = pin.get("tag"), pin.get("commit"), pin.get("digest")
    tag_name(tag, "the %r pin in %s" % (component, path))
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise PinError("the %r pin in %s must give the tag's commit as 40 "
                       "lower-case hex digits, not %r" % (component, path, commit))
    if not isinstance(digest, str) or not _DIGEST.match(digest):
        raise PinError("the %r pin in %s must give the R7 digest as 64 "
                       "lower-case hex digits, not %r" % (component, path, digest))
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


def tree(repo, commit):
    """{path: (mode, type, object id)} for every entry of commit's tree,
    recursively, paths from the repository's root with "/"."""
    out = {}
    raw = _git_bytes(repo, ["ls-tree", "-r", "-t", "-z", "--full-tree",
                            commit])
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
            raise PinError("git cat-file gave %r for the blob %s in %s (R23)"
                           % (" ".join(head), oid, repo))
        size = int(head[2])
        out[oid] = raw[nl + 1:nl + 1 + size]
        at = nl + 1 + size + 1
    return out


def _under(path, component):
    return path.startswith(component + "/")


def git_digest(repo, commit, component=COMPONENT):
    """R7 of component at commit, from git's objects: the same digest
    digest.component_digest gives for those files checked out. A symbolic
    link is read through, as R7 reads it on disk: its target's bytes when
    it leads to a file in the commit, nothing when it leads to a folder
    (os.walk does not follow a linked folder), and PinError when it leads
    nowhere in the commit, since R7 gives no digest then either."""
    entries = tree(repo, commit)
    if component not in entries or entries[component][1] != "tree":
        raise PinError("%s has no folder %s at %s (R23)"
                       % (repo, component, commit))
    files = [p for p, (mode, kind, _) in entries.items()
             if _under(p, component) and kind == "blob"]
    links = {p: entries[p][2] for p in files if entries[p][0] == SYMLINK}
    link_text = blobs(repo, links.values())
    chosen = []                                  # (relative path, blob id)
    for p in files:
        if excluded(posixpath.basename(p)):
            continue
        q, hops = p, 0
        while entries.get(q, (None,))[0] == SYMLINK:
            hops += 1
            target = link_text.get(entries[q][2])
            if target is None:
                target = blobs(repo, [entries[q][2]])[entries[q][2]]
                link_text[entries[q][2]] = target
            q = posixpath.normpath(posixpath.join(
                posixpath.dirname(q), target.decode("utf-8")))
            if hops > _LINK_HOPS or q.startswith("../") or q == "..":
                q = None
                break
        if q is None or q not in entries:
            raise PinError("%s at %s is a symbolic link that leads to no file "
                           "in the commit, so it has no R7 digest (R23)"
                           % (p, commit))
        if entries[q][1] == "tree":
            continue
        chosen.append((p[len(component) + 1:], entries[q][2]))
    chosen.sort(key=lambda t: t[0].encode("utf-8"))
    content = blobs(repo, [oid for _, oid in chosen])
    h = hashlib.sha256()
    for rel, oid in chosen:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(content[oid])
        h.update(b"\0")
    return h.hexdigest()


def _disk(checkout, path):
    return os.path.join(checkout, *path.split("/"))


def tracked_differences(checkout, commit, component=COMPONENT):
    """["<path>: <what differs>"] for each file git tracks under component
    at commit whose bytes or mode on disk are not the commit's, or that is
    missing. Bytes are read raw; the mode is git's (regular, executable,
    symbolic link), not the full permission bits."""
    entries = tree(checkout, commit)
    files = sorted(p for p, (_, kind, _) in entries.items()
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
                out.append("%s: git records a symbolic link, and the disk "
                           "has %s" % (p, _kind_of(st)))
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
                              "executable" if mode == EXECUTABLE
                              else "not executable"))
                continue
        try:
            with open(disk, "rb") as f:
                got = f.read()
        except OSError as e:
            out.append("%s: unreadable (%s)" % (p, e))
            continue
        if got != want:
            out.append("%s: its bytes differ from the commit's (%d bytes on "
                       "disk, %d in git%s)"
                       % (p, len(got), len(want),
                          "; line endings differ" if
                          got.replace(b"\r\n", b"\n") == want.replace(b"\r\n", b"\n")
                          else ""))
    return out


def _kind_of(st):
    if stat.S_ISLNK(st.st_mode):
        return "a symbolic link"
    if stat.S_ISDIR(st.st_mode):
        return "a folder"
    if stat.S_ISREG(st.st_mode):
        return "a regular file"
    return "a special file"


def untracked_files(checkout, commit, component=COMPONENT):
    """["<path>: <why>"] for each file under component that git does not
    track and that no committed .gitignore lists, for each path staged in
    the index that is not a file of the commit, and for each .gitignore
    that could hide files without being the committed one."""
    out = []
    raw = _git_bytes(checkout, ["ls-files", "-z", "--others",
                                "--exclude-per-directory=.gitignore",
                                "--", component])
    out += ["%s: not in the commit, and no committed .gitignore lists it"
            % p.decode("utf-8") for p in raw.split(b"\0") if p]
    # A file staged in the index (git add) is not "other" to ls-files, and
    # tracked_differences walks only the commit's tree, so each path in the
    # index must be a file of the commit's.
    entries = tree(checkout, commit)
    raw = _git_bytes(checkout, ["ls-files", "-z", "--cached", "--",
                                component])
    for p in sorted(set(raw.split(b"\0"))):
        p = p.decode("utf-8")
        if p and entries.get(p, (None, "tree"))[1] == "tree":
            out.append("%s: staged in the checkout's index, and not in the "
                       "commit" % p)
    # A .gitignore under the component that git does not track would count
    # in the listing above, and can hide itself, so list them without any
    # excludes.
    raw = _git_bytes(checkout, ["ls-files", "-z", "--others", "--",
                                component])
    for p in raw.split(b"\0"):
        p = p.decode("utf-8")
        if p and posixpath.basename(p) == ".gitignore" and not any(
                o.startswith(p + ":") for o in out):
            out.append("%s: a .gitignore not in the commit, which could hide "
                       "files" % p)
    # The folders above the component: their .gitignore applies too, so it
    # must be the commit's.
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
            out.append("%s: differs from the commit's, and it could hide "
                       "files under %s" % (gi, component))
    return out


def _listed(items):
    shown = "\n".join("    " + s for s in items[:_SHOWN])
    if len(items) > _SHOWN:
        shown += "\n    ... and %d more" % (len(items) - _SHOWN)
    return shown


def _missing_checkout(checkout, top, tag):
    return PinError("the pinned checkout %s does not exist. Create it, "
                    "detached at the pin's tag, with:\n"
                    "    %s" % (checkout, create_command(top, tag)))


def _check(project_root, component, runs_from=None):
    """R23 and R16's pin clause for component's pin, against its checkout.
    runs_from, if given, must be the component's folder in that checkout
    (check(): the tooling runs from it). Returns {"tag", "commit", "dirty",
    "digest", "folder"}."""
    pin = read(project_root, component)
    tag = pin["tag"]
    checkout, top = checkout_dir(project_root, tag)
    if not os.path.isdir(checkout):
        raise _missing_checkout(checkout, top, tag)
    want = os.path.join(checkout, *component.split("/"))
    if runs_from is not None and not _same(runs_from, want):
        raise PinError("this build tooling runs from %s, and the project's "
                       "pin says %s: the tooling runs from the pinned "
                       "checkout (R23)" % (os.path.realpath(runs_from), want))
    rc, head = _git(checkout, "rev-parse", "--verify", "HEAD")
    if rc != 0 or head != pin["commit"]:
        raise PinError("the checkout %s is at %s, and the %r pin says %s (R23)"
                       % (checkout, head or "(unreadable)", component,
                          pin["commit"]))
    rc, tagged = _git(checkout, "rev-parse", "--verify", "--quiet",
                      "refs/tags/%s^{commit}" % tag)
    if rc != 0 or not tagged:
        raise PinError("the %r pin names %r as its tag, and the checkout's "
                       "repository has no tag of that name; shared code is "
                       "pinned at a tag, not a commit or a branch (R16)"
                       % (component, tag))
    if tagged != pin["commit"]:
        raise PinError("the tag %s marks %s, and the %r pin says %s (R23)"
                       % (tag, tagged, component, pin["commit"]))
    digest = git_digest(checkout, pin["commit"], component)
    if digest != pin["digest"]:
        raise PinError("%s at %s has the digest %s in git, and the pin says "
                       "%s: the pin's digest is not that commit's (R23, R7)"
                       % (component, pin["commit"], digest, pin["digest"]))
    changed = tracked_differences(checkout, pin["commit"], component)
    if changed:
        raise PinError("%d file(s) git tracks under %s differ on disk from "
                       "the pinned commit %s (R23):\n%s"
                       % (len(changed), want, pin["commit"], _listed(changed)))
    extra = untracked_files(checkout, pin["commit"], component)
    if extra:
        raise PinError("%s holds %d file(s) the pinned commit does not, "
                       "beyond those its committed .gitignore files list "
                       "(R23):\n%s" % (want, len(extra), _listed(extra)))
    rc, st = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all",
                  "--", component)
    return {"tag": tag, "commit": pin["commit"],
            "dirty": bool(st) if rc == 0 else None, "digest": digest,
            "folder": want}


def check(project_root, tools_root):
    """R23 and R16's tooling-pin clause, for the shared/tools folder at
    tools_root (the one this code runs from). Returns what R13 records,
    {"tag", "commit", "dirty", "digest"}: tag, commit and digest are the
    pin's, now checked; dirty is whether git sees uncommitted changes under
    the checkout's shared/tools, which, once this check has passed, it
    cannot. PinError says what does not hold."""
    out = _check(project_root, COMPONENT, runs_from=tools_root)
    del out["folder"]
    return out


def check_component(project_root, component):
    """R23 and R16's pin clause for the project's pin of component, a
    folder below shared/, in the checkout of that pin's tag. Returns
    {"tag", "commit", "digest", "dirty", "folder"}: the pin, now checked,
    dirty as check() gives it, and the checked folder, the component in
    ../shared-<tag>/. PinError says what does not hold. It does not check
    the tooling it runs in: main() makes check() first."""
    return _check(project_root, component)


def entry(project_root, tag, component):
    """The pin entry {"tag", "commit", "digest"} the checkout ../shared-<tag>/
    beside the project's worktree gives component, for a pin move: its HEAD,
    which tag must mark, and R7 of the component there, from git's objects.
    Writes nothing. The checkout's disk is not compared with the commit: a
    pin written from it is checked by check_component() when it is used.
    PinError if the name or the tag cannot be pinned, the checkout is missing
    (the refusal prints the command that creates it), its HEAD is not the
    tag's commit, or the commit has no such component."""
    component_name(component)
    tag_name(tag, "the entry asked for")
    checkout, top = checkout_dir(project_root, tag)
    if not os.path.isdir(checkout):
        raise _missing_checkout(checkout, top, tag)
    rc, tagged = _git(checkout, "rev-parse", "--verify", "--quiet",
                      "refs/tags/%s^{commit}" % tag)
    if rc != 0 or not tagged:
        raise PinError("the checkout %s's repository has no tag %r; shared "
                       "code is pinned at a tag, not a commit or a branch (R16)"
                       % (checkout, tag))
    rc, head = _git(checkout, "rev-parse", "--verify", "HEAD")
    if rc != 0 or head != tagged:
        raise PinError("the checkout %s is at %s, and the tag %s marks %s "
                       "(R23)" % (checkout, head or "(unreadable)", tag, tagged))
    return {"tag": tag, "commit": head,
            "digest": git_digest(checkout, head, component)}


TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(args, out=None, err=None):
    """The command line's `pin` command (cli.py): args.action is "check",
    "where" or "entry", args.project the project root. Every action first
    checks the tooling it runs in (check(), against TOOLS_ROOT), so a pin is
    never checked or written by tooling that is not the project's pinned
    version.

      check COMPONENT...  each named component's pin, in order; one line
                          each: "OK: <folder> is the pinned <component> at
                          <tag>"
      where COMPONENT     the checked folder, alone on its line
      entry TAG COMPONENT the entry ../shared-<TAG>/ gives, as JSON
                          {component: entry}; writes nothing

    Exit 0; 1 with "REFUSED: <reason>" on stderr at the first refusal; 2,
    with the usage, if the action has the wrong number of names."""
    import sys
    out, err = out or sys.stdout, err or sys.stderr
    want = {"check": (1, None), "where": (1, 1), "entry": (2, 2)}[args.action]
    n = len(args.names)
    if n < want[0] or (want[1] is not None and n > want[1]):
        print("usage: pin check COMPONENT... | pin where COMPONENT | "
              "pin entry TAG COMPONENT  [--project DIR]", file=err)
        return 2
    try:
        check(args.project, TOOLS_ROOT)
        if args.action == "entry":
            tag, name = args.names
            print(json.dumps({name: entry(args.project, tag, name)},
                             indent=2), file=out)
        elif args.action == "where":
            print(check_component(args.project, args.names[0])["folder"],
                  file=out)
        else:
            for name in args.names:
                got = check_component(args.project, name)
                print("OK: %s is the pinned %s at %s"
                      % (got["folder"], name, got["tag"]), file=out)
    except PinError as e:
        print("REFUSED: %s" % e, file=err)
        return 1
    return 0
