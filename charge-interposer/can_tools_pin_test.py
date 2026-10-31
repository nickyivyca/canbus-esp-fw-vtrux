#!/usr/bin/env python3
"""Tests can_tools_pin.py: the harness's canbench is the can-tools commit
pins.json pins, or it is refused.

Charge-interposer tester, 2026-10-30 (the canbench cutover; user ruling of
that day via the reviewer: pin can-tools, not just record it). The same
shape as canre_pin_test.py. A stand-in can-tools clone is a real git repo
made in a scratch_dir run folder (2026-10-31), with three commits:
one with no canbench/, the pinned one, and a later one checked out as HEAD
with an edit left in its working tree, so a canbench taken from the clone's
working tree or HEAD instead of the pin is caught. The pinned commit has a
`* text eol=crlf` .gitattributes, so a checkout-style export (which
converts) is told apart from the raw blobs. No network is needed.

  1. pin(): the committed entry is the reviewer's (repo, 14f8ee60...); a
     pins file with no can-tools entry, a commit that is short or not
     lower-case hex, or no repo, is refused.
  2. clone(): CAN_TOOLS_DIR unset is refused, naming it; a folder with no
     canbench/ is refused, saying it must be the clone; set, it is the
     folder.
  3. install(): into <worktree>/scratch/can-tools/<commit>, written now and
     checked; the files are the pinned commit's raw blobs (not HEAD's, not
     the working tree's, LF not CRLF); no partial folder is left; a second
     call writes nothing; a pin the clone does not have is refused, leaving
     no folder and no partial; so is a pinned commit with no canbench/; a
     folder there already that fails the checks is refused, not replaced; a
     blob read back wrong is refused by the check before the move, leaving
     no folder and no partial.
  4. verify(), on a copy of the pinned canbench/ the test writes itself
     (git show), so a broken install() cannot hide it: the copy passes;
     refused are a missing folder (the refusal names the install command),
     a file changed, added or removed (each by name), and the later
     commit's canbench/. A __pycache__ is not refused.
  5. use(), each in a fresh interpreter with the machine's own canbench
     (the can-tools.pth) taken off sys.path first: canbench and
     canbench.live.receiver load from the install and sys.path is unchanged;
     the stand-in clone earlier on sys.path, not imported, does not win; a
     canbench already imported from it is refused; a second call returns
     the same module; an install that fails the checks is refused.
  6. folder(): a worktree's .git (a file) is found by walking up; with no
     .git anywhere it is refused.
  7. Every .py under charge-interposer/ (firmware/ aside) that imports
     canbench calls paths.import_canbench or can_tools_pin.use first, and
     bus.py is among them. Control: a planted file importing canbench
     without either is flagged; with one, not.
  8. On the bench host (CAN_TOOLS_DIR set) the real install of the pin
     passes verify(). Elsewhere (madhouse) it is reported as not checked.

fixture(), pointed() and off_pth() are also used by paths_test.py,
bus_gs_usb_test.py and bench_suite_test.py.

    py -3.14 -B charge-interposer/can_tools_pin_test.py
    py -3.14 -B charge-interposer/can_tools_pin_test.py --can-fail

--can-fail runs the test on copies of can_tools_pin.py with one edit each
(CAN_TOOLS_PIN_SRC names the copy's folder, with pins.json beside it), and
passes only if every one is caught.
"""
import ast
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("CAN_TOOLS_PIN_SRC") or HERE
sys.path.insert(0, SRC_DIR)
import can_tools_pin  # noqa: E402
import scratch_dir  # noqa: E402

REPO = "https://github.com/nickyivyca/can-tools"
COMMIT = "14f8ee60d0a5b004755a9b45a852593d313b76ab"
OTHER = "0123456789abcdef0123456789abcdef01234567"
FAILS = []

PINNED_FILES = {
    "canbench/__init__.py": "MARK = 'pinned'\n",
    "canbench/live/__init__.py": "",
    "canbench/live/receiver.py":
        "X = 1\n\n\ndef detect_all_can_interfaces(bitrate, brands=None):\n"
        "    return []\n",
    "canbench/live/note.md": "line one\nline two\n",
    ".gitattributes": "* text eol=crlf\n",
}


def check(name, ok, detail=""):
    print("  %-4s %-72s %s" % ("ok" if ok else "FAIL", name,
                               "" if ok else repr(detail)[:220]))
    if not ok:
        FAILS.append(name)


def refused(fn, *args, has="", **kw):
    """The refusal's text if fn raises CanToolsPinError (containing has),
    else None; any other exception is not a refusal either, so a mutant
    that crashes is a FAIL."""
    try:
        fn(*args, **kw)
    except can_tools_pin.CanToolsPinError as e:
        return str(e) if has in str(e) else None
    except Exception:       # noqa: BLE001
        return None
    return None


def attempt(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except can_tools_pin.CanToolsPinError as e:
        return "refused: %s" % e
    except Exception as e:  # noqa: BLE001
        return "error: %s: %s" % (type(e).__name__, e)


@contextlib.contextmanager
def walk_within(mod, base):
    """The stand-ins are made in a scratch_dir run folder, inside this
    repo's own worktree (2026-10-31; before, the machine temp folder), so a
    walk up from them would reach the real .git. For the no-git case the
    walk sees only what is under base, as if base were the whole disk."""
    real = mod.worktree
    top = os.path.normcase(os.path.abspath(base)) + os.sep

    def within(start=None, exists=os.path.exists):
        return real(start, exists=lambda p: os.path.normcase(
            os.path.abspath(p)).startswith(top) and exists(p))
    mod.worktree = within
    try:
        yield
    finally:
        mod.worktree = real


def _write(base, files):
    for rel, text in files.items():
        p = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def fixture(base, files=None):
    """A stand-in can-tools clone (git, three commits, HEAD past the pin, an
    edit in the working tree), a planted worktree and a pins file under
    base. files: the pinned commit's files (default PINNED_FILES). ->
    dict(clone, wt, start, pins, commit, later, empty)."""
    files = PINNED_FILES if files is None else files
    clone = os.path.join(base, "can-tools")
    os.makedirs(clone)
    g = ["git", "-C", clone, "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]

    def commit(msg):
        for a in (["add", "-A"], ["commit", "-q", "--no-verify", "-m", msg]):
            subprocess.run(g + a, check=True, capture_output=True)
        return subprocess.run(g + ["rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    subprocess.run(g + ["init", "-q"], check=True, capture_output=True)
    _write(clone, {"README.md": "can-tools\n"})
    empty = commit("no canbench yet")
    _write(clone, files)
    pinned = commit("pinned")
    _write(clone, {"canbench/__init__.py": "MARK = 'later'\n",
                   "canbench/live/receiver.py": "X = 2\n"})
    later = commit("later")
    _write(clone, {"canbench/__init__.py": "MARK = 'working tree'\n"})
    wt = os.path.join(base, "wt")
    start = os.path.join(wt, "charge-interposer")
    os.makedirs(start)
    with open(os.path.join(wt, ".git"), "w") as fh:
        fh.write("gitdir: elsewhere\n")
    pins = os.path.join(base, "pins.json")
    with open(pins, "w") as fh:
        json.dump({"can-tools": {"repo": REPO, "commit": pinned}}, fh)
    return dict(clone=clone, wt=wt, start=start, pins=pins, commit=pinned,
                later=later, empty=empty,
                folder=os.path.join(wt, "scratch", "can-tools", pinned))


@contextlib.contextmanager
def pointed(fix, module=None):
    """can_tools_pin pointed at the fixture: its pins file, its worktree,
    CAN_TOOLS_DIR its clone. Restored after."""
    m = module or can_tools_pin
    saved = (m.PINS_FILE, m.START, os.environ.get("CAN_TOOLS_DIR"))
    m.PINS_FILE, m.START = fix["pins"], fix["start"]
    os.environ["CAN_TOOLS_DIR"] = fix["clone"]
    try:
        yield m
    finally:
        m.PINS_FILE, m.START = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop("CAN_TOOLS_DIR", None)
        else:
            os.environ["CAN_TOOLS_DIR"] = saved[2]


def drop_canbench():
    for k in [k for k in sys.modules if k.split(".")[0] == "canbench"]:
        del sys.modules[k]


def off_pth():
    """Take every sys.path entry holding a canbench/ package off sys.path
    (the can-tools.pth on the bench host puts the real clone there) and
    forget any canbench imported. Returns the old sys.path."""
    old = list(sys.path)
    sys.path[:] = [p for p in sys.path if not os.path.isfile(
        os.path.join(p or ".", "canbench", "__init__.py"))]
    drop_canbench()
    return old


OFF_PTH = ("import sys, os\n"
           "sys.path[:] = [p for p in sys.path if not os.path.isfile("
           "os.path.join(p or '.', 'canbench', '__init__.py'))]\n")


def child(code, fix, *args):
    """Run code in a fresh interpreter, off the .pth, with can_tools_pin
    importable and pointed at fix; (exit, output)."""
    env = dict(os.environ, CAN_TOOLS_DIR=fix["clone"])
    pre = (OFF_PTH + "sys.path.insert(0, sys.argv[1])\nimport can_tools_pin\n"
           "can_tools_pin.PINS_FILE, can_tools_pin.START = sys.argv[2], "
           "sys.argv[3]\n")
    r = subprocess.run([sys.executable, "-B", "-c", pre + code, SRC_DIR,
                        fix["pins"], fix["start"]] + list(args),
                       capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


PIN_CALLS = {"paths.import_canbench", "import_canbench", "PA.import_canbench",
             "can_tools_pin.use"}


def importers(root):
    """[(path, uses the pin)] for each .py under root (firmware/ aside) that
    imports canbench."""
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [x for x in dns if x not in ("firmware", "__pycache__")]
        for f in fns:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            tree = ast.parse(open(p, "rb").read(), p)
            imp = any((isinstance(n, ast.Import) and
                       any(a.name.split(".")[0] == "canbench" for a in n.names))
                      or (isinstance(n, ast.ImportFrom) and not n.level and
                          (n.module or "").split(".")[0] == "canbench")
                      for n in ast.walk(tree))
            if not imp:
                continue
            calls = {ast.unparse(n.func) for n in ast.walk(tree)
                     if isinstance(n, ast.Call)}
            out.append((p, bool(calls & PIN_CALLS)))
    return out


def read(*parts):
    """A file's bytes, or None if it is not there."""
    try:
        with open(os.path.join(*parts), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def raw(fix, sha_or_rev, path):
    return subprocess.run(["git", "-C", fix["clone"], "show",
                           "%s:%s" % (sha_or_rev, path)], check=True,
                          capture_output=True).stdout


def reference(fix, dest):
    """The pinned commit's canbench/ written by the test itself (git show),
    independent of install(). -> dest."""
    for rel in PINNED_FILES:
        if rel.startswith("canbench/"):
            out = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(raw(fix, fix["commit"], rel))
    return dest


def checks():
    base = scratch_dir.make("can-tools-pin-test")
    try:
        # 1. the pin
        p = can_tools_pin.pin()
        check("1 pins.json's can-tools entry is the reviewer's (repo, 14f8ee60...)",
              p == {"repo": REPO, "commit": COMMIT}, p)
        for name, entry in (("no can-tools entry", None),
                            ("a short commit", dict(p, commit=COMMIT[:7])),
                            ("an upper-case commit", dict(p, commit=COMMIT.upper())),
                            ("no repo", {"commit": COMMIT})):
            f = os.path.join(base, "pins_%s.json" % name.replace(" ", "_"))
            with open(f, "w") as fh:
                json.dump({"canre": {}} if entry is None
                          else {"can-tools": entry}, fh)
            check("1 a pins file with %s is refused" % name,
                  refused(can_tools_pin.pin, f) is not None)

        fix = fixture(os.path.join(base, "f"))
        fp = can_tools_pin.pin(fix["pins"])

        # 2. the clone
        e = refused(can_tools_pin.clone, {}, has="CAN_TOOLS_DIR is not set")
        check("2 CAN_TOOLS_DIR unset is refused, naming it", e is not None,
              attempt(can_tools_pin.clone, {}))
        e = refused(can_tools_pin.clone, {"CAN_TOOLS_DIR": fix["wt"]},
                    has="has no canbench/")
        check("2 a CAN_TOOLS_DIR with no canbench/ is refused, saying it must "
              "be the clone", e is not None and "must be the can-tools clone" in e,
              attempt(can_tools_pin.clone, {"CAN_TOOLS_DIR": fix["wt"]}))
        got = attempt(can_tools_pin.clone, {"CAN_TOOLS_DIR": fix["clone"]})
        check("2 set, it is the clone", got == os.path.normpath(fix["clone"]), got)

        # 3. install
        want = fix["folder"]

        def partials():
            par = os.path.dirname(want)
            return [x for x in os.listdir(par) if ".partial-" in x] \
                if os.path.isdir(par) else []
        got = attempt(can_tools_pin.install, fp, fix["start"], fix["clone"])
        check("3 install: into <worktree>/scratch/can-tools/<commit>, written "
              "now, checked", got == (want, True) and
              refused(can_tools_pin.verify, want, fp, fix["clone"]) is None, got)
        same = all(read(want, *rel.split("/")) == raw(fix, fix["commit"], rel)
                   for rel in PINNED_FILES if rel.startswith("canbench/"))
        check("3 install: every file is the pinned commit's blob (not HEAD's, "
              "not the working tree's)", same and b"'pinned'" in (
                  read(want, "canbench", "__init__.py") or b""))
        note = read(want, "canbench", "live", "note.md")
        check("3 install: raw blobs, LF, despite the clone's eol=crlf "
              "attributes", note == b"line one\nline two\n", note)
        top = sorted(os.listdir(want)) if os.path.isdir(want) else None
        check("3 install: nothing outside canbench/ (no .gitattributes, no "
              "README)", top == ["canbench"], top)
        check("3 install: no partial folder is left", partials() == [], partials())
        got = attempt(can_tools_pin.install, fp, fix["start"], fix["clone"])
        check("3 install again: nothing written", got == (want, False), got)
        absent = dict(fp, commit=OTHER)
        e = refused(can_tools_pin.install, absent, fix["start"], fix["clone"],
                    has="has no commit")
        check("3 a pin the clone does not have: refused, no folder, no partial",
              e is not None and not os.path.exists(os.path.join(
                  os.path.dirname(want), OTHER)) and partials() == [],
              attempt(can_tools_pin.install, absent, fix["start"], fix["clone"]))
        nocb = dict(fp, commit=fix["empty"])
        e = refused(can_tools_pin.install, nocb, fix["start"], fix["clone"],
                    has="canbench/__init__.py")
        check("3 a pinned commit with no canbench/: refused, no folder",
              e is not None and not os.path.exists(os.path.join(
                  os.path.dirname(want), fix["empty"])),
              attempt(can_tools_pin.install, nocb, fix["start"], fix["clone"]))
        bad_target = os.path.join(base, "f2")
        fix2 = fixture(bad_target)
        _write(fix2["folder"], {"canbench/__init__.py": "MARK = 'hand'\n"})
        fp2 = can_tools_pin.pin(fix2["pins"])
        e = refused(can_tools_pin.install, fp2, fix2["start"], fix2["clone"],
                    has="not replaced")
        check("3 a folder there already that fails the checks: refused, not "
              "replaced", e is not None and b"'hand'" in (read(
                  fix2["folder"], "canbench", "__init__.py") or b""),
              attempt(can_tools_pin.install, fp2, fix2["start"], fix2["clone"]))
        # a blob read back wrong (git, the disk): the check before the move
        # refuses it, and nothing is left behind
        fix3 = fixture(os.path.join(base, "f3"))
        fp3 = can_tools_pin.pin(fix3["pins"])
        real_git = can_tools_pin._git

        def bad_git(c, args, text=True):
            rc, out, err = real_git(c, args, text)
            if args[:2] == ["cat-file", "blob"] and not text:
                out = out + b"# corrupted\n"
            return rc, out, err
        can_tools_pin._git = bad_git
        try:
            e = refused(can_tools_pin.install, fp3, fix3["start"], fix3["clone"])
        finally:
            can_tools_pin._git = real_git
        par3 = os.path.dirname(fix3["folder"])
        left = sorted(os.listdir(par3)) if os.path.isdir(par3) else []
        check("3 a blob read back wrong: refused, no folder, no partial",
              e is not None and left == [], (e, left))

        # 4. verify, on a copy the test writes itself
        ref = reference(fix, os.path.join(base, "ref"))
        check("4 the pinned commit's canbench/ passes",
              attempt(can_tools_pin.verify, ref, fp, fix["clone"]) == ref,
              attempt(can_tools_pin.verify, ref, fp, fix["clone"]))
        e = refused(can_tools_pin.verify, os.path.join(base, "absent"), fp,
                    fix["clone"], has="install")
        check("4 a missing folder is refused, naming the install command",
              e is not None and "can_tools_pin.py" in e, e)

        def variant(name, edit):
            d = os.path.join(base, "v_" + name)
            shutil.copytree(ref, d)
            edit(os.path.join(d, "canbench"))
            return d, refused(can_tools_pin.verify, d, fp, fix["clone"])

        def append(pkg):
            with open(os.path.join(pkg, "live", "receiver.py"), "a") as fh:
                fh.write("# edited\n")
        d, e = variant("changed", append)
        check("4 a file changed is refused, by name",
              e is not None and "live/receiver.py" in e, e)
        d, e = variant("added", lambda pkg: open(
            os.path.join(pkg, "live", "extra.py"), "w").close())
        check("4 a file added is refused, by name",
              e is not None and "live/extra.py" in e, e)
        d, e = variant("removed", lambda pkg: os.remove(
            os.path.join(pkg, "live", "note.md")))
        check("4 a file removed is refused, by name",
              e is not None and "live/note.md" in e, e)

        def later(pkg):
            for rel in ("__init__.py", "live/receiver.py"):
                with open(os.path.join(pkg, *rel.split("/")), "wb") as fh:
                    fh.write(raw(fix, fix["later"], "canbench/" + rel))
        d, e = variant("later", later)
        check("4 the later commit's canbench/ is refused", e is not None, e)

        def pycache(pkg):
            os.makedirs(os.path.join(pkg, "__pycache__"))
            open(os.path.join(pkg, "__pycache__", "x.cpython-314.pyc"),
                 "wb").close()
        d, e = variant("pycache", pycache)
        check("4 control: a __pycache__ is not refused", e is None, e)
        bad_install = os.path.join(base, "v_changed")

        # 5. use, in fresh interpreters
        show = ("import canbench.live.receiver as r, os\n"
                "print(m.MARK, r.X, r.__file__.startswith(sys.argv[4] + os.sep), "
                "sys.path == p0)\n")
        rc, out = child("p0 = list(sys.path)\nm = can_tools_pin.use()\n" + show,
                        fix, want)
        check("5 use: canbench and canbench.live.receiver from the install, "
              "sys.path unchanged", rc == 0 and out == "pinned 1 True True",
              (rc, out[-200:]))
        rc, out = child("sys.path.insert(0, sys.argv[5]); p0 = list(sys.path)\n"
                        "m = can_tools_pin.use()\n" + show, fix, want,
                        fix["clone"])
        check("5 use: the clone earlier on sys.path, not imported, does not win",
              rc == 0 and out == "pinned 1 True True", (rc, out[-200:]))
        rc, out = child("sys.path.insert(0, sys.argv[4]); import canbench\n"
                        "can_tools_pin.use()", fix, fix["clone"])
        check("5 use: a canbench already imported from the clone is refused",
              rc != 0 and "CanToolsPinError" in out and "already imported" in out,
              (rc, out[-200:]))
        rc, out = child("print(can_tools_pin.use() is can_tools_pin.use())", fix)
        check("5 use: a second call returns the same module",
              rc == 0 and out == "True", (rc, out[-200:]))
        rc, out = child("can_tools_pin.use(sys.argv[4])", fix, bad_install)
        check("5 use: an install that fails the checks is refused",
              rc != 0 and "CanToolsPinError" in out, (rc, out[-200:]))

        # 6. folder
        check("6 worktree: a worktree's .git file is found by walking up",
              os.path.normcase(can_tools_pin.worktree(fix["start"]) or "")
              == os.path.normcase(fix["wt"]), can_tools_pin.worktree(fix["start"]))
        got = attempt(can_tools_pin.folder, fp, fix["start"])
        check("6 folder: <worktree>/scratch/can-tools/<commit>", got == want, got)
        nogit = os.path.join(base, "nogit", "code")
        os.makedirs(nogit)
        with walk_within(can_tools_pin, base):
            check("6 folder: no .git anywhere is refused",
                  refused(can_tools_pin.folder, fp, nogit) is not None,
                  attempt(can_tools_pin.folder, fp, nogit))

        # 7. every importer goes through the pin
        found = importers(HERE)
        names = sorted(os.path.basename(f) for f, _ in found)
        check("7 the canbench importers are found (bus.py among them)",
              "bus.py" in names, names)
        check("7 every one of them calls paths.import_canbench or "
              "can_tools_pin.use", all(ok for _, ok in found),
              [f for f, ok in found if not ok])
        pl = os.path.join(base, "planted")
        os.makedirs(pl)
        with open(os.path.join(pl, "a.py"), "w") as fh:
            fh.write("def f():\n    from canbench.live.receiver import x\n"
                     "    return x\n")
        with open(os.path.join(pl, "b.py"), "w") as fh:
            fh.write("import paths\npaths.import_canbench()\nimport canbench\n")
        got = sorted((os.path.basename(f), ok) for f, ok in importers(pl))
        check("7 control: an import without the pin is flagged, one with it is not",
              got == [("a.py", False), ("b.py", True)], got)

        # 8. the real install, on the bench host
        if os.environ.get("CAN_TOOLS_DIR", "").strip():
            d = os.environ.get("CAN_TOOLS_PIN_REAL") or \
                attempt(can_tools_pin.folder, p)
            e = d if d.startswith(("refused:", "error:")) else \
                attempt(can_tools_pin.verify, d, p)
            check("8 the real install of the pin passes the checks (%s)" % d,
                  e == d, e)
        else:
            print("  --   8 the real install: not checked, CAN_TOOLS_DIR unset "
                  "(not the bench host)")
    finally:
        scratch_dir.remove(base)
    return FAILS


MUTANTS = [
    ("a short commit accepted",
     '    if len(c) != 40 or c.strip("0123456789abcdef"):', "    if False:"),
    ("no repo accepted", 'for k in ("repo", "commit"))', 'for k in ("commit",))'),
    ("CAN_TOOLS_DIR unset accepted", "    if not v:\n        raise",
     "    if False:\n        raise"),
    ("a CAN_TOOLS_DIR with no canbench/ accepted",
     "    if not os.path.isfile(os.path.join(v, PACKAGE, \"__init__.py\")):",
     "    if False:"),
    ("the clone's working tree loaded",
     "    d = d or folder()\n    verify(d)\n    pkg = os.path.join(d, PACKAGE)",
     "    d = d or folder()\n    verify(d)\n    pkg = os.path.join(clone(), PACKAGE)"),
    ("install from the clone's HEAD",
     "    files = tree(c, p)\n    tmp =",
     "    files = tree(c, dict(p, commit=_git(c, [\"rev-parse\", \"HEAD\"])[1]"
     ".strip()))\n    tmp ="),
    ("install with filters (a checkout's line endings)",
     '["cat-file", "blob", sha]',
     '["cat-file", "--filters", "--path=" + PACKAGE + "/" + rel, sha]'),
    ("a commit the clone lacks not refused", "    if rc:\n        raise "
     "CanToolsPinError(\"the can-tools clone", "    if False:\n        raise "
     "CanToolsPinError(\"the can-tools clone"),
    ("no canbench/__init__.py at the pin accepted",
     '    if "__init__.py" not in files:', "    if False:"),
    ("a changed file accepted",
     "if want[k] != have[k])", "if False)"),
    ("an added file accepted", "    added = sorted(set(have) - set(want))",
     "    added = []"),
    ("a removed file accepted", "    missing = sorted(set(want) - set(have))",
     "    missing = []"),
    ("__pycache__ counted", '        dns[:] = [x for x in dns if x != "__pycache__"]',
     "        pass"),
    ("a canbench imported earlier accepted",
     "        if not _same(getattr(have, \"__file__\", None), init):",
     "        if False:"),
    ("use puts the install on sys.path",
     "    spec = importlib.util.spec_from_file_location(",
     "    sys.path.insert(0, d)\n    spec = importlib.util.spec_from_file_location("),
    ("use skips the checks", "    d = d or folder()\n    verify(d)",
     "    d = d or folder()"),
    ("install does not check before moving into place",
     "        verify(tmp, p, c)\n        os.rename", "        os.rename"),
    ("install replaces a folder that is there",
     "    if os.path.isdir(d):\n        try:", "    if False:\n        try:"),
    ("install leaves its partial folder on a failure",
     "    finally:\n        scratch_dir.remove(tmp)",
     "    finally:\n        pass"),
    ("worktree ignores .git", '        if exists(os.path.join(d, ".git")):',
     "        if False:"),
    ("no worktree falls back to a guess",
     "    if wt is None:\n        raise",
     "    if wt is None:\n        wt = os.path.dirname(os.path.abspath(start or START))\n"
     "    if False:\n        raise"),
    ("the folder not keyed by the commit",
     '"can-tools", (p or pin())["commit"])', '"can-tools", "current")'),
]


def main():
    if "--can-fail" not in sys.argv[1:]:
        got = checks()
        print("\ncan_tools_pin_test: %s" % ("all checks OK" if not got else
                                            "%d FAILED: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = scratch_dir.make("can-tools-pin-mutants")
    src = open(os.path.join(HERE, "can_tools_pin.py"), encoding="utf-8",
               newline="").read()
    # The copy's real install (8) is named by CAN_TOOLS_PIN_REAL: the
    # folder the unmutated module resolves here.
    env = dict(os.environ)
    if env.get("CAN_TOOLS_DIR", "").strip():
        env["CAN_TOOLS_PIN_REAL"] = can_tools_pin.folder()
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate([("control: unmutated", "", "")]
                                             + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            shutil.copy(os.path.join(HERE, "pins.json"), d)
            if n and src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            open(os.path.join(d, "can_tools_pin.py"), "w", encoding="utf-8",
                 newline="").write(src.replace(old, new) if n else src)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                               capture_output=True, text=True, timeout=900,
                               env=dict(env, CAN_TOOLS_PIN_SRC=d))
            if n == 0:
                ok = r.returncode == 0 and "all checks OK" in r.stdout
                print("%s m0 the unmutated copy passes" % ("ok      " if ok else "FAIL    "))
                if not ok:
                    print(r.stdout[-1500:] + r.stderr[-800:])
                    errors += 1
                continue
            fails = [ln.strip() for ln in r.stdout.splitlines()
                     if ln.strip().startswith("FAIL")]
            dead = bool(fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "".join("\n           " + f[:130] for f in fails[:2])))
    finally:
        scratch_dir.remove(base)
    print("\ncan_tools_pin_test --can-fail: %d of %d killed by a check, %d errors"
          % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
