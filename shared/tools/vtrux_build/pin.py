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

R23 (changed 2026-11-17): the tooling runs from the checkout
../shared-<tag>/, relative to the root of the project's git worktree, and
that checkout is checked through git, not kept read-only. check() refuses
unless, in this order:

  - the checkout exists (if not, the refusal prints the command that
    creates it), and the shared/tools folder this code runs from is its;
  - the checkout's HEAD is the pin's commit, and the pin's tag is a tag
    there that marks that commit;
  - the pin's digest is R7 over shared/tools at that commit, read from
    git's objects (git_digest), not from the disk;
  - every file git tracks under shared/tools at that commit is on disk
    with exactly its bytes, read raw, so git's line-ending filters play no
    part, and with the mode git records for it: a regular file, an
    executable (the owner's execute bit) or a symbolic link (its target is
    the blob's bytes). Other permission bits are not compared, so a
    read-only file passes. None is missing;
  - there is no other file under shared/tools, except files the checkout's
    own committed .gitignore files list: `git ls-files --others
    --exclude-per-directory=.gitignore`, never --exclude-standard, so a
    machine's own core.excludesFile and info/exclude hide nothing. A file
    staged in the checkout's index (git add) is no exception: every path
    the index holds under shared/tools must be a file of the commit. A
    .gitignore that could hide files must itself be the committed one: an
    uncommitted .gitignore under shared/tools, or one in a folder above it
    that differs from the commit's, is refused.

shared/main's .gitignore lists __pycache__/ and *.pyc, so the bytecode
Python writes beside the code passes, and the checkout needs no -B.

On Windows the execute bit is not compared (the disk has none), and a
symbolic link checked out as a plain file passes when its bytes are the
link's target, as git writes it there without symlink support.

R16, the tooling-pin clause: a pin that names a commit rather than a tag is
refused: no tag, an empty one, one that reads as a commit id (7 to 40 hex
digits), or one the checkout has no tag of that name for.
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


def check(project_root, tools_root):
    """R23 and R16's tooling-pin clause, for the shared/tools folder at
    tools_root (the one this code runs from). Returns what R13 records,
    {"tag", "commit", "dirty", "digest"}: tag, commit and digest are the
    pin's, now checked; dirty is whether git sees uncommitted changes under
    the checkout's shared/tools, which, once this check has passed, it
    cannot. PinError says what does not hold."""
    pin = read(project_root)
    tag = pin["tag"]
    checkout, top = checkout_dir(project_root, tag)
    if not os.path.isdir(checkout):
        raise PinError("the pinned checkout %s does not exist. Create it, "
                       "detached at the pin's tag, with:\n"
                       "    %s" % (checkout, create_command(top, tag)))
    want = os.path.join(checkout, *COMPONENT.split("/"))
    if not _same(tools_root, want):
        raise PinError("this build tooling runs from %s, and the project's "
                       "pin says %s: the tooling runs from the pinned "
                       "checkout (R23)" % (os.path.realpath(tools_root), want))
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
    digest = git_digest(checkout, pin["commit"])
    if digest != pin["digest"]:
        raise PinError("%s at %s has the digest %s in git, and the pin says "
                       "%s: the pin's digest is not that commit's (R23, R7)"
                       % (COMPONENT, pin["commit"], digest, pin["digest"]))
    changed = tracked_differences(checkout, pin["commit"])
    if changed:
        raise PinError("%d file(s) git tracks under %s differ on disk from "
                       "the pinned commit %s (R23):\n%s"
                       % (len(changed), want, pin["commit"], _listed(changed)))
    extra = untracked_files(checkout, pin["commit"])
    if extra:
        raise PinError("%s holds %d file(s) the pinned commit does not, "
                       "beyond those its committed .gitignore files list "
                       "(R23):\n%s" % (want, len(extra), _listed(extra)))
    rc, st = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all",
                  "--", COMPONENT)
    return {"tag": tag, "commit": pin["commit"],
            "dirty": bool(st) if rc == 0 else None, "digest": digest}
