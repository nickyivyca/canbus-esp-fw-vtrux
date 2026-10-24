#!/usr/bin/env python3
"""Verify vtrux_build.digest against SPEC R7, by its properties.

    python -B verify_digest.py [--commit C [--component shared/tools] [--expect-digest D]]
    python -B verify_digest.py --self-test      # the same checks against fake libraries

SPEC R7 defines what a digest covers, not how it is serialised, so these checks
never compute a digest of their own. They build small trees and compare digests of
pairs: a pair that SPEC R7 says covers the same thing must give one digest, and a
pair that differs in anything it covers must give two. component_files must list
exactly the files R7 covers. With --commit, the component at that commit is taken
from a clone checked out at the commit and from a `git archive` export of its tree
(no git at all), and the two must give one digest (R7: "never covers a git ref").
--expect-digest is that component's digest as the other host OS gave it: R7 writes
paths with "/" on every host OS, so one tree gives one digest on Windows and Linux.
--self-test runs the synthetic checks against fakes that each break one rule and
must each be caught, and one fake that breaks none and must pass. There the other
host's digest is the faithful fake's.

R7's edges as the user ruled them (2026-10-24, SPEC 904b98a): .gitignore is left out
in any folder; only names ending ".md", matched case-sensitively, are left out, so
.markdown and README.MD count; paths use "/" on every host OS.

Gen-inhibit tester, 2026-10-24 (C6f verification).
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import independent as I  # noqa: E402

FAILS = []
BASE = {"a.py": b"print(1)\n", "sub/b.txt": b"x\n", "sub/deep/c.bin": bytes(range(10)),
        ".gitattributes": b"* text=auto eol=lf\n"}


def check(name, ok, detail=""):
    print("  %-4s %-72s %s" % ("ok" if ok else "FAIL", name, "" if ok else str(detail)[:150]))
    if not ok:
        FAILS.append(name)


class Trees:
    """Each tree in its own folder under the run's scratch folder."""
    def __init__(self, tmp):
        self.tmp, self.n = tmp, 0

    def make(self, files, dirs=(), under=""):
        self.n += 1
        root = os.path.join(self.tmp, "t%03d" % self.n, under, "comp")
        os.makedirs(root)
        for rel, data in files.items():
            p = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        for d in dirs:
            os.makedirs(os.path.join(root, *d.split("/")), exist_ok=True)
        return root


def edit(base, **kw):
    t = dict(base)
    for k, v in kw.pop("drop", {}).items():
        del t[k]
    t.update(kw.get("add", {}))
    return t


def synthetic(lib, tmp, other=None):
    """other: the other host OS's digest of a tree, in the self-test only (the faithful fake);
    a real run compares across hosts with --expect-digest instead."""
    T = Trees(tmp)
    D = lib.component_digest
    base = T.make(BASE)
    d0 = D(base)
    check("R7: the same folder twice gives one digest", D(base) == d0)

    def same(label, root):
        check("R7: same digest: " + label, D(root) == d0, (D(root), d0))

    def different(label, root):
        check("R7: different digest: " + label, D(root) != d0)

    same("a copy at another path", T.make(BASE))
    same("a copy one folder deeper", T.make(BASE, under="x/y"))
    t = T.make(BASE)
    os.utime(os.path.join(t, "a.py"), ns=(10 ** 18, 10 ** 18))
    same("one file's mtime changed", t)
    same("README.md added at the root", T.make(edit(BASE, add={"README.md": b"# r\n"})))
    same("notes.md added in a subfolder", T.make(edit(BASE, add={"sub/deep/notes.md": b"n\n"})))
    same(".gitignore added at the root", T.make(edit(BASE, add={".gitignore": b"*.pyc\n"})))
    same(".gitignore added in a subfolder", T.make(edit(BASE, add={"sub/deep/.gitignore": b"*.o\n"})))
    same("an empty folder added", T.make(BASE, dirs=["empty", "sub/empty2"]))
    t = T.make(BASE, under="repo")
    gitdir = os.path.join(os.path.dirname(os.path.dirname(t)), ".git")
    os.makedirs(gitdir)
    with open(os.path.join(gitdir, "HEAD"), "w") as f:
        f.write("ref: refs/heads/topic\n")
    same("a .git/HEAD (on a branch) in the folder above it, and none", t)
    with open(os.path.join(gitdir, "HEAD"), "w") as f:
        f.write("ref: refs/tags/v1\n")
    same("... and with HEAD moved to a tag", t)
    withmd = edit(BASE, add={"README.md": b"one\n"})
    a, b = T.make(withmd), T.make(edit(withmd, add={"README.md": b"two\n"}))
    check("R7: same digest: only a .md file's content differs", D(a) == D(b))

    different("one content byte changed", T.make(edit(BASE, add={"sub/b.txt": b"y\n"})))
    different("a file renamed", T.make(edit(BASE, drop={"a.py": 1}, add={"a2.py": BASE["a.py"]})))
    different("a file moved into a subfolder", T.make(edit(BASE, drop={"a.py": 1}, add={"sub/a.py": BASE["a.py"]})))
    different("a new empty file", T.make(edit(BASE, add={"empty.txt": b""})))
    different("__pycache__/a.cpython-314.pyc added",
              T.make(edit(BASE, add={"__pycache__/a.cpython-314.pyc": b"\x00\x01pyc"})))
    different("a.py saved with CRLF", T.make(edit(BASE, add={"a.py": b"print(1)\r\n"})))
    different("README.md.txt added (not *.md)", T.make(edit(BASE, add={"README.md.txt": b"r\n"})))
    different("notes.markdown added (only .md is left out)", T.make(edit(BASE, add={"notes.markdown": b"m\n"})))
    different("README.MD added (matched case-sensitively)", T.make(edit(BASE, add={"README.MD": b"u\n"})))
    different("sub/x.Md added (matched case-sensitively)", T.make(edit(BASE, add={"sub/x.Md": b"u\n"})))
    different(".gitattributes changed (a dotfile is a file)",
              T.make(edit(BASE, add={".gitattributes": b"* -text\n"})))
    different("a file removed", T.make(edit(BASE, drop={"sub/deep/c.bin": 1})))
    p = {"p.txt": b"AAAA", "q.txt": b"BBBB"}
    check("R7: different digest: two files' contents swapped (paths go with their content)",
          D(T.make(p)) != D(T.make({"p.txt": b"BBBB", "q.txt": b"AAAA"})))
    check("R7: different digest: path/content boundary ('a' holding 'bc' against 'ab' holding 'c')",
          D(T.make({"a": b"bc"})) != D(T.make({"ab": b"c"})))
    check("R7: different digest: one file against the same bytes split over two",
          D(T.make({"f": b"xy"})) != D(T.make({"f": b"x", "g": b"y"})))

    extra = edit(BASE, add={"README.md": b"r\n", "sub/n.md": b"n\n", ".gitignore": b"x\n", "sub/.gitignore": b"y\n",
                            "__pycache__/m.pyc": b"c", "README.md.txt": b"t\n", "x.markdown": b"k\n",
                            "sub/deep/NOTES.MD": b"u\n"})      # not beside a .md of the same name: NTFS folds case
    t = T.make(extra, dirs=["empty"])
    want = sorted(k for k in extra if not k.endswith(".md") and k.split("/")[-1] != ".gitignore")
    got = lib.component_files(t)
    rels = sorted(r.replace("\\", "/") for r, _a in got)
    check("R7: component_files lists exactly what R7 covers", rels == want, (rels, want))
    check("R7: component_files: each entry's path is that file under the root",
          all(os.path.samefile(a, os.path.join(t, *r.replace("\\", "/").split("/"))) for r, a in got))
    if other:
        check("R7: the same tree (with subfolders) on the other host OS gives one digest", D(base) == other(base),
              (D(base), other(base)))


def cross_host(digest, expect):
    check("R7: the same tree on this host and the other host OS gives one digest", digest == expect, (digest, expect))


def git(*args, **kw):
    return subprocess.run(["git"] + list(args), check=True, capture_output=True, **kw).stdout


def real(lib, tmp, commit, component, expect=None):
    repo = HERE
    while not os.path.exists(os.path.join(repo, ".git")):
        repo = os.path.dirname(repo)
    sha = git("-C", repo, "rev-parse", commit + "^{commit}", text=True).strip()
    clone = os.path.join(tmp, "clone")
    git("clone", "--quiet", "--no-checkout", repo, clone)
    git("-C", clone, "-c", "advice.detachedHead=false", "checkout", "--quiet", sha)
    exp = os.path.join(tmp, "export")
    os.makedirs(exp)
    tarfile.open(fileobj=io.BytesIO(git("-C", repo, "archive", "--format=tar", sha, component))).extractall(exp, filter="data")
    a = os.path.join(clone, *component.split("/"))
    b = os.path.join(exp, *component.split("/"))
    da, db = lib.component_digest(a), lib.component_digest(b)
    print("       %s at %s: checkout %s  export %s  (%d files)" % (component, sha[:12], da, db, len(lib.component_files(a))))
    check("R7: the checkout at the commit and the git-archive export give one digest", da == db, (da, db))
    check("R7: ... with the same file list",
          sorted(r for r, _ in lib.component_files(a)) == sorted(r for r, _ in lib.component_files(b)))
    if expect:
        cross_host(da, expect)


class Fake:
    """A component digest written for the self-test, with one rule broken (or none)."""
    def __init__(self, broken=None):
        self.broken = broken

    def _walk(self, folder):
        out = []
        for d, dirs, files in os.walk(folder):
            dirs.sort()
            if self.broken == "skips __pycache__" and "__pycache__" in dirs:
                dirs.remove("__pycache__")
            if self.broken == "skips subfolders":
                dirs[:] = []
            for f in sorted(files):
                p = os.path.join(d, f)
                rel = os.path.relpath(p, folder).replace(os.sep, "/")
                if self.broken == "writes paths with a backslash":
                    rel = rel.replace("/", "\\")
                if f.endswith(".md") and self.broken != "includes .md":
                    continue
                if self.broken == "leaves out .markdown too" and f.endswith(".markdown"):
                    continue
                if self.broken == "matches .md case-insensitively" and f.lower().endswith(".md"):
                    continue
                if f == ".gitignore" and self.broken != "includes .gitignore" and not (
                        self.broken == "leaves out .gitignore at the root only" and d != folder):
                    continue
                if self.broken == "skips every dotfile" and f.startswith("."):
                    continue
                if self.broken == "skips empty files" and os.path.getsize(p) == 0:
                    continue
                out.append((rel, p))
        return out

    def component_files(self, folder):
        return self._walk(folder)

    def component_digest(self, folder):
        h = hashlib.sha256()
        for rel, p in self._walk(folder):
            data = open(p, "rb").read()
            if self.broken == "normalises CRLF":
                data = data.replace(b"\r\n", b"\n")
            if self.broken == "path and content with no separator":
                h.update(rel.encode() + data)
                continue
            if self.broken == "absolute paths":
                rel = os.path.abspath(p)
            if self.broken != "content only, no paths":
                h.update(rel.encode() + b"\0")
            h.update(b"%d\0" % len(data) + data)
            if self.broken == "uses mtime":
                h.update(b"%d" % os.stat(p).st_mtime_ns)
        if self.broken == "mixes in the git HEAD above it":
            d = os.path.abspath(folder)
            while os.path.dirname(d) != d:
                head = os.path.join(d, ".git", "HEAD")
                if os.path.exists(head):
                    h.update(open(head, "rb").read())
                    break
                d = os.path.dirname(d)
        return h.hexdigest()


BROKEN = ["includes .md", "includes .gitignore", "content only, no paths", "uses mtime", "skips __pycache__",
          "normalises CRLF", "skips every dotfile", "absolute paths", "mixes in the git HEAD above it",
          "skips empty files", "path and content with no separator", "skips subfolders",
          "leaves out .gitignore at the root only", "leaves out .markdown too", "matches .md case-insensitively",
          "writes paths with a backslash"]


def main(argv):
    tmp = I.scratch("verify_digest")
    try:
        if "--self-test" in argv:
            ok = True
            for b in [None] + BROKEN:
                del FAILS[:]
                print("--- fake: %s" % (b or "nothing broken (must pass)"))
                sub = os.path.join(tmp, "fake%02d" % (BROKEN.index(b) + 1 if b else 0))
                os.makedirs(sub)
                synthetic(Fake(b), sub, other=Fake(None).component_digest)
                caught = bool(FAILS)
                ok &= caught == (b is not None)
                print("%s %s  (%d failing check(s))" % ("CAUGHT  " if caught and b else "PASSED  " if not b and not caught
                                                       else "MISSED  ", b or "the faithful fake", len(FAILS)))
            print("\nself-test: %s" % ("every broken fake caught, the faithful one passed" if ok else "FAILED"))
            return 0 if ok else 1
        import vtrux_build.digest as lib
        print("library: %s" % lib.__file__)
        synthetic(lib, os.path.join(tmp))
        if "--commit" in argv:
            comp = argv[argv.index("--component") + 1] if "--component" in argv else "shared/tools"
            exp = argv[argv.index("--expect-digest") + 1] if "--expect-digest" in argv else None
            real(lib, tmp, argv[argv.index("--commit") + 1], comp, exp)
        print("\n%s" % ("all ok" if not FAILS else "%d FAIL: %s" % (len(FAILS), "; ".join(FAILS))))
        return 1 if FAILS else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
