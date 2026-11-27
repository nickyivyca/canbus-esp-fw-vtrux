#!/usr/bin/env python3
"""Tests shared_pin.py: the harness's shared code is the tag the project
pins, from its checkout ../shared-<tag>/ checked through git (shared/tools
R23), or it is refused.

Charge-interposer tester, port step 2a (the reviewer, 2026-11-03; port plan
goal 20 item 8); check 4 by R23's git rule from the re-pin to 2026.11.17.3
(the charge-interposer reviewer's batch, 2026-11-17). The fixture is made in
a scratch_dir run folder: a stand-in shared repo `src` with two tagged
commits (T1, and T2 with a changed vtrux_bench), a project worktree `wt`
beside it, and checkouts `shared-<tag>` beside `wt`, as on the bench host.
Its root .gitignore lists Python's bytecode, as shared/main's does from
2026.11.17.3; its shared/bench holds a .md (R7 leaves it out), an
executable and, off Windows, a symbolic link to a file.

  1. read(): a good entry reads; refused are no file, bad JSON, no entry, no
     tag, a tag that is a commit id, a tag with a slash, a short commit, a
     bad digest, and a component the harness does not pin.
  2. folder(): the pinned shared/bench and shared/twai_port pass; refused
     are a missing checkout (the refusal prints the create command), a
     checkout at another commit, a tag the repository lacks, a tag marking
     another commit than the pin's, a file changed, added or removed in the
     component, an added .md (R7 leaves it out, R23 does not), and no
     worktree.
  3. The digest is git's (4a): git_digest equals R7 (os.walk, R7's
     exclusions, path NUL bytes NUL in byte order) of the clean checkout on
     disk; a pin carrying a plain sha256 of the files is refused; a
     __pycache__ the committed .gitignore lists passes, though an on-disk
     R7 would count it.
  9. R23's git rule, each case its own (4b, 4c): refused are a file's bytes
     changed, the same text with CRLF endings (named as line endings), a
     regular file made executable, an executable made not executable, a
     tracked file missing, a folder where git has a file, an untracked file,
     a file staged in the checkout's index (git add) but not in the commit,
     one hidden only by the checkout's info/exclude or only by the global
     core.excludesFile (each shown first to hide it from git status), an
     uncommitted .gitignore under the component that hides a file and
     itself, a root .gitignore changed to hide a file, and off Windows a
     symbolic link retargeted or replaced by a file. Passing: the whole
     component made read-only (chmod a-w, folders too), and a *.pyc the
     committed .gitignore lists.
  4. use_bench(), each in a fresh interpreter: vtrux_bench loads from the
     checked folder, first on sys.path, once; a vtrux_bench imported earlier
     is kept if it is the shared/bench of a pinned checkout anywhere, with no
     checkout beside the worktree (the acceptance runner's core, 2026-11-04),
     and refused from elsewhere, from a checkout at another commit, or from
     another folder of a pinned checkout; a failing pin is refused before
     sys.path changes.
  5. entry(): gives the values folder() then accepts; a checkout whose HEAD
     is not the tag is refused.
  6. main(): check/where exit 0 on the pin, 1 refused, 2 on a bad call.
  7. The real pin file: once it has the tester's entries, each passes
     folder() on this host (pins whose digest was taken on disk before R23,
     such as twai_port's and can_port's at 2026.11.08, so show git_digest
     equal to the on-disk R7 of real checkouts); until then reported as not
     checked. And the
     real twai_port and can_port pins pass idf-match against the real
     firmware/main/idf_component.yml.
  8. idf-match (port step 2b): each dependency's own version read from a
     manifest laid out as the firmware's (comments, other dependencies,
     a trailing comment); pins at those versions pass; refused are
     twai_port or can_port at another version, no can_port in the
     manifest, can_port's block without a version (the next block's is
     not taken), and the two pins at different tags; main() exits 0 and 1.
     The diag pin (schema 5) at its own tag: it passes at a tag that is
     not the other two's, and is refused at another version than the
     manifest's vtrux/diag, or with no vtrux/diag in the manifest.

    py -3.14 -B charge-interposer/shared_pin_test.py
    py -3.14 -B charge-interposer/shared_pin_test.py --can-fail

--can-fail runs the test on copies of shared_pin.py with one edit each
(SHARED_PIN_SRC names the copy's folder), and passes only if every one is
caught.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("SHARED_PIN_SRC") or HERE
sys.path.insert(0, SRC_DIR)
import shared_pin as P  # noqa: E402
sys.path.insert(0, HERE)
import scratch_dir  # noqa: E402

FAILS = []
GITIGNORE = "__pycache__/\n*.pyc\n"
UNIX = os.name != "nt"


def r7_disk(root):
    """R7 of the folder on disk, written here from shared/tools SPEC R7
    (every file under it by os.walk, links to files read through, files
    named .gitignore or ending .md left out; each relative path with "/",
    NUL, its bytes, NUL, in the paths' byte order), not from the module."""
    rels = []
    for d, _dirs, files in os.walk(root):
        for f in files:
            if f == ".gitignore" or f.endswith(".md"):
                continue
            rels.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    h = hashlib.sha256()
    for rel in sorted(rels, key=lambda s: s.encode("utf-8")):
        with open(os.path.join(root, *rel.split("/")), "rb") as f:
            h.update(rel.encode("utf-8") + b"\0" + f.read() + b"\0")
    return h.hexdigest()


def check(name, ok, detail=""):
    print("%s %s%s" % ("ok      " if ok else "FAIL    ", name,
                       ("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def refused(name, fn, *args, has="", **kw):
    try:
        fn(*args, **kw)
    except P.SharedPinError as e:
        check(name, has in str(e), "refused, but without %r: %s" % (has, e))
        return str(e)
    except Exception as e:  # noqa: BLE001
        check(name, False, "raised %s: %s, not SharedPinError" % (type(e).__name__, e))
        return None
    check(name, False, "not refused")
    return None


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "core.autocrlf=false", "-C", cwd] + list(args),
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def write(base, files):
    for rel, text in files.items():
        p = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def plain_digest(root):
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for f in sorted(files):
            h.update(open(os.path.join(d, f), "rb").read())
    return h.hexdigest()


def fixture(base):
    """{src, wt, a, b, pins}: the stand-in shared repo with tags T1 (a) and
    T2 (b), the project worktree, and its pin file path."""
    # "s": git worktree add runs on it, which git for Windows refuses once
    # the repo's path passes 214 (scratch_dir, 2026-11-05)
    src, wt = os.path.join(base, "s"), os.path.join(base, "wt")
    os.makedirs(src)
    git(src, "init", "-q", "-b", "m")
    write(src, {".gitignore": GITIGNORE,
                "shared/tools/vtrux_build/digest.py": "# stand-in\n",
                "shared/bench/vtrux_bench/__init__.py": "MARK = 'T1'\n",
                "shared/bench/tests/test_x.py": "X = 1\n",
                "shared/bench/README.md": "readme\n",
                "shared/bench/run.sh": "#!/bin/sh\necho run\n",
                "shared/twai_port/test/twai_fake.c": "int fake;\n",
                "shared/can_port/can_port.c": "int port;\n",
                "shared/diag/diag_pack.c": "int pack;\n"})
    if UNIX:
        os.symlink("tests/test_x.py", os.path.join(src, "shared", "bench", "link.py"))
    git(src, "add", "-A")
    git(src, "update-index", "--chmod=+x", "shared/bench/run.sh")
    git(src, "commit", "-q", "-m", "a")
    git(src, "tag", "-a", "T1", "-m", "T1")
    a = git(src, "rev-parse", "HEAD")
    write(src, {"shared/bench/vtrux_bench/__init__.py": "MARK = 'T2'\n"})
    git(src, "commit", "-q", "-am", "b")
    git(src, "tag", "T2")
    b = git(src, "rev-parse", "HEAD")
    os.makedirs(os.path.join(wt, "charge-interposer", "firmware"))
    git(wt, "init", "-q")
    git(src, "worktree", "add", "-q", "--detach", os.path.join(base, "shared-T1"), "T1")
    pins = os.path.join(wt, "charge-interposer", "firmware", "shared_pins.json")
    return {"src": src, "wt": wt, "a": a, "b": b, "pins": pins,
            "start": os.path.join(wt, "charge-interposer")}


def pin_values(fx, tag="T1", commit=None):
    co = os.path.join(os.path.dirname(fx["wt"]), "shared-" + tag)
    out = {}
    for name in P.OURS:
        out[name] = {"tag": tag, "commit": commit or fx["a"],
                     "digest": P.git_digest(co, commit or fx["a"], name)}
    return out


def set_pins(fx, entries, extra=None):
    data = {"shared/tools": {"tag": "T1", "commit": fx["a"], "digest": "0" * 64}}
    data.update(entries)
    data.update(extra or {})
    write(os.path.dirname(fx["pins"]), {"shared_pins.json": json.dumps(data, indent=2)})


def child(fx, code):
    """Run code in a fresh interpreter with the module under test."""
    pre = ("import sys; sys.path.insert(0, %r); import shared_pin as P\n"
           "P.START = %r; P.PIN_FILE = %r\n" % (SRC_DIR, fx["start"], fx["pins"]))
    return subprocess.run([sys.executable, "-B", "-c", pre + code],
                          capture_output=True, text=True, timeout=120)


def rm_tree(path):
    shutil.rmtree(path)


def passes(name, *args):
    try:
        P.folder(*args)
        check(name, True)
    except P.SharedPinError as e:
        check(name, False, str(e))


def hidden_from_status(co, rel):
    """git status (with every exclude git uses) does not list rel."""
    out = git(co, "status", "--porcelain=v1", "--untracked-files=all")
    return rel not in out


def r23_cases(fx, co1, bench):
    """9. R23's git rule (module docstring), on the pinned checkout co1."""
    args = ("shared/bench", fx["pins"], fx["start"])
    base = os.path.dirname(co1)

    def restore():
        git(co1, "checkout", "-q", "--", ".")

    x = os.path.join(bench, "tests", "test_x.py")
    with open(x, "wb") as f:
        f.write(b"X = 1\r\n")
    refused("9 the same text with CRLF endings", P.folder, *args,
            has="test_x.py: its bytes differ from the commit's (7 bytes on disk, "
                "6 in git; line endings differ)")
    restore()

    if UNIX:
        m = os.stat(x).st_mode
        os.chmod(x, m | 0o111)
        refused("9 a regular file made executable", P.folder, *args,
                has="test_x.py: executable on disk, and git records it not executable")
        os.chmod(x, m)
        sh = os.path.join(bench, "run.sh")
        m = os.stat(sh).st_mode
        check("9 (fixture) run.sh is checked out executable", bool(m & stat.S_IXUSR))
        os.chmod(sh, m & ~0o111)
        refused("9 an executable made not executable", P.folder, *args,
                has="run.sh: not executable on disk, and git records it executable")
        os.chmod(sh, m)

        # read-only: every file and folder of the component, a-w
        made = []
        try:
            for d, dirs, files in os.walk(bench, topdown=False):
                for n in files + dirs:
                    p = os.path.join(d, n)
                    if not os.path.islink(p):
                        made.append((p, os.stat(p).st_mode))
                        os.chmod(p, os.stat(p).st_mode & ~0o222)
            made.append((bench, os.stat(bench).st_mode))
            os.chmod(bench, os.stat(bench).st_mode & ~0o222)
            check("9 (fixture) the component is read-only",
                  not os.stat(x).st_mode & 0o222 and not os.stat(bench).st_mode & 0o222)
            passes("9 the component made read-only (chmod a-w, folders too) passes",
                   *args)
        finally:
            for p, mode in reversed(made):
                os.chmod(p, mode)
    else:
        print("NOTE     9 execute bits and chmod a-w: not compared on Windows")

    os.remove(x)
    os.mkdir(x)
    refused("9 a folder where git has a file", P.folder, *args,
            has="test_x.py: git records a regular file, and the disk has a folder")
    os.rmdir(x)
    restore()

    write(bench, {"tests/stray.pyc": "bytecode"})
    passes("9 a *.pyc the committed .gitignore lists passes", *args)
    os.remove(os.path.join(bench, "tests", "stray.pyc"))

    write(bench, {"staged.txt": "b\n"})
    git(co1, "add", "--", "shared/bench/staged.txt")
    check("9 (control) git ls-files --others skips the staged staged.txt",
          "staged.txt" not in git(co1, "ls-files", "--others", "--", "shared/bench"))
    refused("9 a file staged in the index (git add) but not in the commit",
            P.folder, *args,
            has="shared/bench/staged.txt: in the checkout's index, not in the commit")
    git(co1, "rm", "-q", "--cached", "--", "shared/bench/staged.txt")
    os.remove(os.path.join(bench, "staged.txt"))

    stray = os.path.join(bench, "stray.txt")
    excl = os.path.join(co1, git(co1, "rev-parse", "--git-path", "info/exclude"))
    old = open(excl, encoding="utf-8").read() if os.path.exists(excl) else None
    os.makedirs(os.path.dirname(excl), exist_ok=True)
    try:
        with open(excl, "a", encoding="utf-8", newline="\n") as f:
            f.write("\nstray.txt\n")
        write(bench, {"stray.txt": "x\n"})
        check("9 (control) info/exclude hides stray.txt from git status",
              hidden_from_status(co1, "stray.txt"))
        refused("9 a file only info/exclude hides", P.folder, *args,
                has="stray.txt: not in the commit")
    finally:
        if old is None:
            os.remove(excl)
        else:
            with open(excl, "w", encoding="utf-8", newline="\n") as f:
                f.write(old)
    gx, gconf = os.path.join(base, "gx"), os.path.join(base, "gconfig")
    with open(gx, "w", encoding="utf-8", newline="\n") as f:
        f.write("stray.txt\n")
    with open(gconf, "w", encoding="utf-8", newline="\n") as f:
        f.write("[core]\n\texcludesFile = %s\n" % gx.replace("\\", "/"))
    was = os.environ.get("GIT_CONFIG_GLOBAL")
    os.environ["GIT_CONFIG_GLOBAL"] = gconf
    try:
        check("9 (control) the global core.excludesFile hides stray.txt from git status",
              hidden_from_status(co1, "stray.txt"))
        refused("9 a file only the global core.excludesFile hides", P.folder, *args,
                has="stray.txt: not in the commit")
    finally:
        if was is None:
            del os.environ["GIT_CONFIG_GLOBAL"]
        else:
            os.environ["GIT_CONFIG_GLOBAL"] = was
    os.remove(stray)

    write(bench, {"tests/.gitignore": "stray.txt\n.gitignore\n", "tests/stray.txt": "x\n"})
    check("9 (control) the uncommitted tests/.gitignore hides itself and stray.txt",
          hidden_from_status(co1, "stray.txt") and hidden_from_status(co1, ".gitignore"))
    refused("9 an uncommitted .gitignore under the component", P.folder, *args,
            has="tests/.gitignore: a .gitignore not in the commit")
    os.remove(os.path.join(bench, "tests", ".gitignore"))
    os.remove(os.path.join(bench, "tests", "stray.txt"))

    write(co1, {".gitignore": GITIGNORE + "stray.txt\n"})
    write(bench, {"stray.txt": "x\n"})
    refused("9 the root .gitignore changed to hide a file", P.folder, *args,
            has=".gitignore: differs from the commit's")
    os.remove(stray)
    restore()

    if UNIX:
        link = os.path.join(bench, "link.py")
        os.remove(link)
        os.symlink("vtrux_bench/__init__.py", link)
        refused("9 a symbolic link retargeted", P.folder, *args,
                has="link.py: a symbolic link to")
        os.remove(link)
        write(bench, {"link.py": "tests/test_x.py"})
        refused("9 a symbolic link replaced by a file", P.folder, *args,
                has="link.py: git records a symbolic link, and the disk has a regular file")
        os.remove(link)
        os.symlink("tests/test_x.py", link)
    passes("9 the checkout passes again once each case is undone", *args)


def checks():
    # depth: git worktree add runs on s (2 below the run folder), + 36 for
    # that (measured 2026-11-05 as src; scratch_dir). The tool's name is
    # short for the same reason: in the acceptance runner's tree the run
    # folder alone is about 180 characters
    base = scratch_dir.make("spt", depth=38)
    try:
        fx = fixture(base)
        good = pin_values(fx)
        co1 = os.path.join(base, "shared-T1")

        # 1. read()
        set_pins(fx, good)
        r = P.read("shared/bench", fx["pins"])
        check("1 a good entry reads", r == good["shared/bench"], repr(r))
        refused("1 no pin file", P.read, "shared/bench", fx["pins"] + ".x", has="no pin file")
        write(os.path.dirname(fx["pins"]), {"bad.json": "{nope"})
        refused("1 bad JSON", P.read, "shared/bench",
                os.path.join(os.path.dirname(fx["pins"]), "bad.json"), has="not valid JSON")
        set_pins(fx, {"shared/twai_port": good["shared/twai_port"]})
        refused("1 no entry", P.read, "shared/bench", fx["pins"], has="no 'shared/bench' entry")
        for label, change, has in (
                ("no tag", {"tag": ""}, "names no tag"),
                ("a commit id as the tag", {"tag": fx["a"][:12]}, "commit id"),
                ("a tag with a slash", {"tag": "x/y"}, "cannot be a checkout"),
                ("a short commit", {"commit": fx["a"][:12]}, "40 lower-case hex"),
                ("a bad digest", {"digest": "ab"}, "64 lower-case hex")):
            e = dict(good["shared/bench"], **change)
            set_pins(fx, {"shared/bench": e})
            refused("1 " + label, P.read, "shared/bench", fx["pins"], has=has)
        set_pins(fx, good)
        refused("1 a component the harness does not pin", P.read, "shared/tools",
                fx["pins"], has="not a component this harness pins")

        # 2. folder()
        for name in P.OURS:
            try:
                d = P.folder(name, fx["pins"], fx["start"])
                check("2 the pinned %s passes" % name,
                      os.path.normcase(d) == os.path.normcase(
                          os.path.join(co1, *name.split("/"))), d)
            except P.SharedPinError as e:
                check("2 the pinned %s passes" % name, False, str(e))
        set_pins(fx, {"shared/bench": dict(good["shared/bench"], tag="T2")})
        e = refused("2 a missing checkout", P.folder, "shared/bench", fx["pins"],
                    fx["start"], has="does not exist")
        check("2 the refusal prints the create command",
              bool(e) and "worktree add --detach ../shared-T2 T2" in e, e or "")
        os.rename(co1, os.path.join(base, "shared-T2"))
        refused("2 a tag marking another commit than the pin's", P.folder, "shared/bench",
                fx["pins"], fx["start"], has="the tag T2 marks")
        os.rename(os.path.join(base, "shared-T2"), os.path.join(base, "shared-T9"))
        set_pins(fx, {"shared/bench": dict(good["shared/bench"], tag="T9")})
        refused("2 a tag the repository lacks", P.folder, "shared/bench",
                fx["pins"], fx["start"], has="no tag of that name")
        os.rename(os.path.join(base, "shared-T9"), co1)
        set_pins(fx, good)
        git(co1, "checkout", "-q", "--detach", "T2")
        refused("2 a checkout at another commit", P.folder, "shared/bench",
                fx["pins"], fx["start"], has="is at %s" % fx["b"])
        git(co1, "checkout", "-q", "--detach", "T1")
        bench = os.path.join(co1, "shared", "bench")
        for label, act, undo, has in (
                ("changed", lambda: write(bench, {"tests/test_x.py": "X = 2\n"}),
                 lambda: git(co1, "checkout", "-q", "--", "."), "its bytes differ"),
                ("added", lambda: write(bench, {"tests/new.py": ""}),
                 lambda: os.remove(os.path.join(bench, "tests", "new.py")),
                 "tests/new.py: not in the commit"),
                ("removed", lambda: os.remove(os.path.join(bench, "tests", "test_x.py")),
                 lambda: git(co1, "checkout", "-q", "--", "."), "test_x.py: missing"),
                (".md added (R7 leaves it out, R23 does not)",
                 lambda: write(bench, {"NOTES.md": "notes\n"}),
                 lambda: os.remove(os.path.join(bench, "NOTES.md")),
                 "NOTES.md: not in the commit")):
            act()
            refused("2 a file %s in the component" % label, P.folder, "shared/bench",
                    fx["pins"], fx["start"], has=has)
            undo()
        # the run folder is inside this worktree's scratch/: git is kept
        # from looking above it, as on a host with no git above the code
        os.environ["GIT_CEILING_DIRECTORIES"] = os.path.dirname(base)
        try:
            refused("2 no worktree", P.folder, "shared/bench", fx["pins"], base,
                    has="not in a git worktree")
        finally:
            del os.environ["GIT_CEILING_DIRECTORIES"]

        # 3. the digest is git's (4a)
        check("3 git_digest equals R7 of the clean checkout on disk",
              good["shared/bench"]["digest"] == r7_disk(bench),
              "%s vs %s" % (good["shared/bench"]["digest"], r7_disk(bench)))
        set_pins(fx, {"shared/bench": dict(good["shared/bench"],
                                           digest=plain_digest(bench))})
        refused("3 a plain sha256 of the files is not the pin's digest", P.folder,
                "shared/bench", fx["pins"], fx["start"], has="in git")
        set_pins(fx, good)
        write(bench, {"vtrux_bench/__pycache__/__init__.cpython-313.pyc": "bytecode"})
        try:
            P.folder("shared/bench", fx["pins"], fx["start"])
            check("3 a __pycache__ the committed .gitignore lists passes "
                  "(an on-disk R7 would count it)", True)
        except P.SharedPinError as e:
            check("3 a __pycache__ the committed .gitignore lists passes "
                  "(an on-disk R7 would count it)", False, str(e))
        rm_tree(os.path.join(bench, "vtrux_bench", "__pycache__"))

        # 9. R23's git rule (4b, 4c)
        r23_cases(fx, co1, bench)

        # 4. use_bench(), in fresh interpreters
        r = child(fx, "d = P.use_bench(); d2 = P.use_bench()\n"
                      "import vtrux_bench\n"
                      "print(vtrux_bench.MARK, sys.path[0] == d, sys.path.count(d))\n")
        check("4 vtrux_bench loads from the checked folder, first, once",
              r.returncode == 0 and r.stdout.split() == ["T1", "True", "1"],
              r.stdout + r.stderr[-400:])
        other = os.path.join(base, "elsewhere")
        write(other, {"vtrux_bench/__init__.py": "MARK = 'other'\n"})
        r = child(fx, "sys.path.insert(0, %r); import vtrux_bench\n"
                      "try:\n    P.use_bench()\n    print('NOT REFUSED')\n"
                      "except P.SharedPinError as e:\n    print('REFUSED', e)\n" % other)
        check("4 a vtrux_bench imported from elsewhere is refused",
              r.stdout.startswith("REFUSED") and "already imported" in r.stdout,
              r.stdout + r.stderr[-400:])
        # the acceptance runner's case: its core, imported first, is the
        # shared/bench of a pinned checkout that is not beside the worktree
        # (here the sibling is moved away for the run)
        far = os.path.join(base, "far", "co")
        git(fx["src"], "worktree", "add", "-q", "--detach", far, "T1")
        far_bench = os.path.join(far, "shared", "bench")
        os.rename(co1, co1 + ".away")
        try:
            r = child(fx, "sys.path.insert(0, %r); import vtrux_bench\n"
                          "d = P.use_bench()\n"
                          "print(vtrux_bench.MARK, P._same(d, %r), sys.path.count(d))\n"
                          % (far_bench, far_bench))
        finally:
            os.rename(co1 + ".away", co1)
        check("4 a vtrux_bench imported from a pinned checkout elsewhere is kept, with no "
              "checkout beside the worktree", r.returncode == 0 and
              r.stdout.split() == ["T1", "True", "1"], r.stdout + r.stderr[-400:])
        git(far, "checkout", "-q", "--detach", "T2")
        r = child(fx, "sys.path.insert(0, %r); import vtrux_bench\n"
                      "try:\n    P.use_bench()\n    print('NOT REFUSED')\n"
                      "except P.SharedPinError as e:\n    print('REFUSED', e)\n" % far_bench)
        check("4 one imported from a checkout at another commit is refused",
              r.stdout.startswith("REFUSED") and "already imported" in r.stdout,
              r.stdout + r.stderr[-400:])
        git(far, "checkout", "-q", "--detach", "T1")
        # inside a pinned checkout but not its shared/bench: the checks pass
        # on the checkout, and the folder is still not the pinned one
        stray = os.path.join(far, "x", "y")
        write(stray, {"vtrux_bench/__init__.py": "MARK = 'stray'\n"})
        r = child(fx, "sys.path.insert(0, %r); import vtrux_bench\n"
                      "try:\n    P.use_bench()\n    print('NOT REFUSED')\n"
                      "except P.SharedPinError as e:\n    print('REFUSED', e)\n" % stray)
        check("4 one imported from another folder of a pinned checkout is refused",
              r.stdout.startswith("REFUSED") and "already imported" in r.stdout,
              r.stdout + r.stderr[-400:])
        set_pins(fx, {"shared/bench": dict(good["shared/bench"], digest="1" * 64)})
        r = child(fx, "before = list(sys.path)\n"
                      "try:\n    P.use_bench()\n    print('NOT REFUSED')\n"
                      "except P.SharedPinError:\n    print('REFUSED', sys.path == before)\n")
        check("4 a failing pin is refused before sys.path changes",
              r.stdout.split() == ["REFUSED", "True"], r.stdout + r.stderr[-400:])
        set_pins(fx, good)

        # 5. entry()
        try:
            got = {n: P.entry("T1", n, fx["start"]) for n in P.OURS}
        except P.SharedPinError as e:
            got = "refused: %s" % e
        check("5 entry() gives the values folder() accepts", got == good, repr(got))
        git(co1, "checkout", "-q", "--detach", "T2")
        refused("5 entry() refuses a checkout whose HEAD is not the tag", P.entry,
                "T1", "shared/bench", fx["start"], has="the tag T1 marks")
        git(co1, "checkout", "-q", "--detach", "T1")

        # 6. main()
        r = child(fx, "sys.exit(P.main(['where', 'shared/bench']))")
        check("6 where exits 0 and prints the folder", r.returncode == 0 and
              os.path.normcase(r.stdout.strip()) == os.path.normcase(bench),
              r.stdout + r.stderr[-300:])
        r = child(fx, "sys.exit(P.main(['check', 'shared/tools']))")
        check("6 a refusal exits 1", r.returncode == 1 and "REFUSED" in r.stderr, r.stderr[-300:])
        r = child(fx, "sys.exit(P.main(['frob']))")
        check("6 a bad call exits 2", r.returncode == 2, str(r.returncode))

        # 8. idf-match: the twai_port and can_port pins against the
        # firmware's idf_component.yml (port step 2b)
        ydir = os.path.join(base, "yml")
        good_yml = YML % {"can": "T1", "twai": "T1", "diag": "T1"}
        write(ydir, {"good.yml": good_yml})
        yml = os.path.join(ydir, "good.yml")
        got = P.idf_versions(yml)
        check("8 idf_versions reads each dependency's own version",
              got == {"vtrux/can_port": "T1", "vtrux/twai_port": "T1",
                      "vtrux/diag": "T1"}, repr(got))
        try:
            check("8 pins at the versions the manifest takes pass",
                  P.idf_match(fx["pins"], yml) == "T1")
        except P.SharedPinError as e:
            check("8 pins at the versions the manifest takes pass", False, str(e))
        for label, text, has in (
                ("twai_port at another version",
                 YML % {"can": "T1", "twai": "T2", "diag": "T1"},
                 "takes vtrux/twai_port at T2"),
                ("can_port at another version",
                 YML % {"can": "T2", "twai": "T1", "diag": "T1"},
                 "takes vtrux/can_port at T2"),
                ("diag at another version (schema 5)",
                 YML % {"can": "T1", "twai": "T1", "diag": "T2"},
                 "takes vtrux/diag at T2"),
                ("no diag in the manifest (schema 5)", YML_NO_DIAG,
                 "names no version for vtrux/diag"),
                ("no can_port in the manifest", YML_NO_CAN,
                 "names no version for vtrux/can_port"),
                ("can_port's block without a version (the next one's not taken)",
                 YML_CAN_NO_VERSION, "names no version for vtrux/can_port")):
            write(ydir, {"bad.yml": text})
            refused("8 " + label, P.idf_match, fx["pins"], os.path.join(ydir, "bad.yml"),
                    has=has)
        two = os.path.join(base, "shared-T2")
        git(fx["src"], "worktree", "add", "-q", "--detach", two, "T2")
        t2 = {"tag": "T2", "commit": fx["b"],
              "digest": P.git_digest(two, fx["b"], "shared/twai_port")}
        set_pins(fx, dict(good, **{"shared/twai_port": t2}))
        write(ydir, {"split.yml": YML % {"can": "T1", "twai": "T2", "diag": "T1"}})
        refused("8 the two pins at different tags", P.idf_match, fx["pins"],
                os.path.join(ydir, "split.yml"), has="different tags")
        # the diag pin at its own tag (schema 5): not held to the other two's
        d2 = {"tag": "T2", "commit": fx["b"],
              "digest": P.git_digest(two, fx["b"], "shared/diag")}
        set_pins(fx, dict(good, **{"shared/diag": d2}))
        write(ydir, {"own.yml": YML % {"can": "T1", "twai": "T1", "diag": "T2"}})
        try:
            check("8 the diag pin at a tag of its own passes",
                  P.idf_match(fx["pins"], os.path.join(ydir, "own.yml")) == "T1")
        except P.SharedPinError as e:
            check("8 the diag pin at a tag of its own passes", False, str(e))
        refused("8 the diag pin at its own tag, the manifest at the others'",
                P.idf_match, fx["pins"], yml, has="takes vtrux/diag at T1")
        set_pins(fx, good)
        r = child(fx, "P.IDF_YML = %r\nsys.exit(P.main(['idf-match']))" % yml)
        check("8 idf-match exits 0 on a match", r.returncode == 0 and
              r.stdout.startswith("OK"), r.stdout + r.stderr[-300:])
        r = child(fx, "P.IDF_YML = %r\nsys.exit(P.main(['idf-match']))"
                  % os.path.join(ydir, "bad.yml"))
        check("8 idf-match exits 1 on a refusal", r.returncode == 1 and
              "REFUSED" in r.stderr, r.stderr[-300:])
    finally:
        scratch_dir.remove(base)

    # 7. the real pin file
    try:
        with open(P.PIN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not all(n in data for n in P.OURS):
        print("NOTE     7 the real pin file has no tester entries yet: not checked")
    else:
        for name in P.OURS:
            try:
                P.folder(name)
                check("7 the real %s pin passes on this host" % name, True)
            except P.SharedPinError as e:
                check("7 the real %s pin passes on this host" % name, False, str(e))
    # the real files by this test's own tree (under --can-fail the module is
    # a copy elsewhere, whose own defaults name nothing)
    try:
        check("7 the real twai_port, can_port and diag pins are the firmware's versions "
              "(idf-match)", bool(P.idf_match(
                  os.path.join(HERE, "firmware", "shared_pins.json"),
                  os.path.join(HERE, "firmware", "main", "idf_component.yml"))))
    except P.SharedPinError as e:
        check("7 the real twai_port, can_port and diag pins are the firmware's versions "
              "(idf-match)", False, str(e))
    return FAILS


# idf_component.yml stand-ins for 8, laid out as the firmware's: comments,
# other dependencies before and after, a trailing comment on a version
YML = """## the interposer's dependencies
dependencies:
  espressif/arduino-esp32:
    version: "3.2.1"
  # the board
  vtrux/x2:
    git: https://example.invalid/r.git
    path: shared/boards/x2
    version: "T9"
  vtrux/can_port:
    git: https://example.invalid/r.git
    path: shared/can_port
    version: "%(can)s"
  vtrux/twai_port:
    git: https://example.invalid/r.git
    path: shared/twai_port
    version: "%(twai)s"   # the vehicle port
  # the diag packer, at its own tag (schema 5)
  vtrux/diag:
    git: https://example.invalid/r.git
    path: shared/diag
    version: "%(diag)s"
  idf:
    version: "5.4.2"
"""
YML_NO_CAN = """dependencies:
  vtrux/twai_port:
    version: "T1"
  vtrux/diag:
    version: "T1"
  idf:
    version: "5.4.2"
"""
YML_NO_DIAG = """dependencies:
  vtrux/twai_port:
    version: "T1"
  vtrux/can_port:
    version: "T1"
  idf:
    version: "5.4.2"
"""
YML_CAN_NO_VERSION = """dependencies:
  vtrux/diag:
    version: "T1"
  vtrux/twai_port:
    version: "T1"
  vtrux/can_port:
    git: https://example.invalid/r.git
  idf:
    version: "T1"
"""


MUTANTS = [
    ("no worktree check of the tag's commit",
     "    if tagged != pin[\"commit\"]:\n        raise SharedPinError(\"the tag %s marks",
     "    if False:\n        raise SharedPinError(\"the tag %s marks"),
    ("HEAD not compared with the pin",
     "if rc != 0 or head != pin[\"commit\"]:", "if rc != 0:"),
    ("the tag's existence not checked",
     "    if rc != 0 or not tagged:\n        raise SharedPinError(\"the %r pin names",
     "    if False:\n        raise SharedPinError(\"the %r pin names"),
    ("the digest not compared", "    if got != pin[\"digest\"]:", "    if False:"),
    # check 4 by R23's git rule (the re-pin to 2026.11.17.3)
    ("the digest taken on disk, not from git (4a)",
     "    got = git_digest(checkout, pin[\"commit\"], name)",
     "    def _r7(root):\n        h = hashlib.sha256()\n        fs = []\n"
     "        for dd, _ds, ff in os.walk(root):\n"
     "            fs += [os.path.relpath(os.path.join(dd, f), root).replace(os.sep, '/')\n"
     "                   for f in ff if not excluded(f)]\n"
     "        for rel in sorted(fs, key=lambda s: s.encode('utf-8')):\n"
     "            h.update(rel.encode('utf-8') + b'\\0'\n"
     "                     + open(os.path.join(root, rel), 'rb').read() + b'\\0')\n"
     "        return h.hexdigest()\n"
     "    got = _r7(d)"),
    ("R7's exclusions not applied to git's files (4a)",
     "        if excluded(posixpath.basename(p)):\n            continue\n", ""),
    ("git's files hashed in tree order, not byte order (4a)",
     '    chosen.sort(key=lambda t: t[0].encode("utf-8"))\n',
     "    chosen.reverse()\n"),
    ("a tracked file's bytes not compared (4b)",
     "        if got != want:\n            out.append(\"%s: its bytes differ",
     "        if False:\n            out.append(\"%s: its bytes differ"),
    ("line endings normalised before the bytes are compared (4b)",
     "                got = f.read()\n        except OSError as e:\n"
     "            out.append(\"%s: unreadable (%s)\" % (p, e))",
     "                got = f.read().replace(b\"\\r\\n\", b\"\\n\")\n"
     "        except OSError as e:\n"
     "            out.append(\"%s: unreadable (%s)\" % (p, e))"),
    ("the execute bit not compared (4b)",
     "            if executable != (mode == EXECUTABLE):", "            if False:"),
    ("the full permission bits compared, so read-only is refused (4b)",
     "            if executable != (mode == EXECUTABLE):",
     "            if executable != (mode == EXECUTABLE) or not st.st_mode & stat.S_IWUSR:"),
    ("a missing tracked file not refused (4b)",
     "            out.append(\"%s: missing\" % p)\n            continue",
     "            continue"),
    ("a tracked file's kind not checked (4b)",
     "        elif not stat.S_ISREG(st.st_mode):", "        elif False:"),
    ("a symbolic link's target not compared (4b)",
     "                if got != want:\n                    out.append(\"%s: a symbolic link to",
     "                if False:\n                    out.append(\"%s: a symbolic link to"),
    ("a symbolic link replaced by a file accepted (4b)",
     "            if not (windows and stat.S_ISREG(st.st_mode)):",
     "            if not stat.S_ISREG(st.st_mode):"),
    ("tracked differences not refused (4b)",
     "    if changed:\n", "    if False:\n"),
    ("untracked files not refused (4c)",
     "    if extra:\n", "    if False:\n"),
    ("the machine's excludes count (4c)",
     '"--exclude-per-directory=.gitignore",', '"--exclude-standard",'),
    ("the committed .gitignore not honoured (4c)",
     '"--exclude-per-directory=.gitignore",\n', ""),
    ("a file staged in the index and not in the commit accepted (4c)",
     "        if p and p not in committed:", "        if False:"),
    ("an uncommitted .gitignore under the component accepted (4c)",
     '        if p and posixpath.basename(p) == ".gitignore" and not any(',
     "        if False and not any("),
    ("a changed .gitignore above the component accepted (4c)",
     "        elif got != r.stdout:", "        elif False:"),
    ("a missing checkout without the create command",
     "\"    %s\" % (checkout, create_command(top, tag)))",
     "\"%s\" % (checkout, \"\"))"),
    ("a commit-like tag accepted", "    if _LOOKS_LIKE_COMMIT.match(tag):", "    if False:"),
    ("a short commit accepted", '_COMMIT = re.compile(r"^[0-9a-f]{40}$")',
     '_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")'),
    ("any component accepted", "    if name not in OURS:", "    if False:"),
    ("an earlier vtrux_bench from another folder kept",
     "        if not _same(where, d):", "        if False:"),
    ("an earlier vtrux_bench kept unchecked",
     '            d = _checked(checkout, "shared/bench", pin)\n',
     "            d = where\n"),
    ("an earlier pinned vtrux_bench made to need the sibling",
     "    if mod is None:\n        d = folder(", "    if True:\n        d = folder("),
    ("the folder appended, not first", "        sys.path.insert(0, d)", "        sys.path.append(d)"),
    ("the folder added every call",
     "    if not any(_same(p, d) for p in sys.path if p):\n        sys.path.insert(0, d)",
     "    sys.path.insert(0, d)"),
    ("the checkout beside the worktree's parent",
     "os.path.join(top, os.pardir, \"shared-\" + tag)",
     "os.path.join(top, os.pardir, os.pardir, \"shared-\" + tag)"),
    ("a refusal exits 0 in main", "        return 1\n    print(__doc__", "        return 0\n    print(__doc__"),
    # idf-match (port step 2b)
    ("a pin not compared with its version", "        if tag != vers[dep]:",
     "        if False:"),
    ("only twai_port compared",
     "    for name, dep in IDF_OWN_TAG + IDF_DEPS:\n        tag = read",
     "    for name, dep in IDF_OWN_TAG + IDF_DEPS[:1]:\n        tag = read"),
    ("the diag pin not compared (schema 5)",
     "    for name, dep in IDF_OWN_TAG + IDF_DEPS:\n        tag = read",
     "    for name, dep in IDF_DEPS:\n        tag = read"),
    ("the diag pin held to the other two's tag (schema 5)",
     "        if (name, dep) in IDF_DEPS:\n            tags.add(tag)",
     "        if True:\n            tags.add(tag)"),
    ("pins at different tags accepted", "    if len(tags) != 1:", "    if False:"),
    ("a missing version not refused", "        if dep not in vers:", "        if False:"),
    ("a version from past the dependency's block",
     "        if cur is not None and indent <= cur_indent:\n            cur = None\n", ""),
    ("comments not stripped", '        s = ln.split("#", 1)[0].rstrip()',
     "        s = ln.rstrip()"),
    ("idf-match not a command", '        if argv == ["idf-match"]:',
     '        if argv == ["idf_match"]:'),
]


def main():
    if "--can-fail" not in sys.argv[1:]:
        got = checks()
        print("\nshared_pin_test: %s" % ("all checks OK" if not got else
                                         "%d FAILED: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = scratch_dir.make("shared-pin-mutants")
    src = open(os.path.join(HERE, "shared_pin.py"), encoding="utf-8", newline="").read()
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate([("control: unmutated", "", "")] + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            if n and src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            with open(os.path.join(d, "shared_pin.py"), "w", encoding="utf-8",
                      newline="") as f:
                f.write(src.replace(old, new) if n else src)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                               capture_output=True, text=True, timeout=900,
                               env=dict(os.environ, SHARED_PIN_SRC=d))
            if n == 0:
                ok = r.returncode == 0 and "all checks OK" in r.stdout
                print("%s m0 the unmutated copy passes" % ("ok      " if ok else "FAIL    "))
                if not ok:
                    print(r.stdout[-1500:] + r.stderr[-800:])
                    errors += 1
                continue
            fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
            dead = bool(fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "".join("\n           " + f[:130] for f in fails[:2])))
    finally:
        scratch_dir.remove(base)
    print("\nshared_pin_test --can-fail: %d of %d killed by a check, %d errors"
          % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
