"""Implementor self-test of can_tools_pin.py: gen-inhibit's canbench is the
can-tools commit can_tools_pin.json pins, or it is refused. Not evidence.

    py -3.14 -B gen-inhibit/tools/test_can_tools_pin.py

A stand-in can-tools clone is a real git repo made under
scratch/can-tools-pin-test/ at this worktree's root, with three commits: one
with no canbench/, the pinned one, and a later one checked out as HEAD with
an edit left in its working tree, so a canbench taken from the clone's HEAD
or working tree instead of the pin shows. The pinned commit carries a
`* text eol=crlf` .gitattributes, so a checkout-style export (which converts)
is told apart from the raw blobs. A planted worktree (a folder with a .git
file) and a pins file go beside it. No network is needed. The real pin is
checked against $CAN_TOOLS_DIR when it is set, and reported as skipped when
not. fixture() and pointed() are also used by test_vehicle_runner_root.py.
"""

import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scratch",
                       "can-tools-pin-test")
sys.path.insert(0, HERE)
import can_tools_pin as ctp  # noqa: E402

PINNED_FILES = {
    "canbench/__init__.py": "MARK = 'pinned'\n",
    "canbench/live/__init__.py": "",
    "canbench/live/receiver.py":
        "def detect_all_can_interfaces(bitrate):\n"
        "    print('PINNED canbench %d' % bitrate)\n"
        "    return []\n",
    "canbench/live/note.md": "line one\nline two\n",
    ".gitattributes": "* text eol=crlf\n",
}
LATER_FILES = {
    "canbench/live/receiver.py":
        "def detect_all_can_interfaces(bitrate):\n"
        "    print('HEAD canbench %d' % bitrate)\n"
        "    return []\n",
}
GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=stand-in",
       "-c", "user.email=stand-in@example.invalid", "-c", "commit.gpgsign=false"]


def rmtree(path):
    """shutil.rmtree that also removes read-only files (git's objects on
    Windows)."""
    def again(fn, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    if os.path.exists(path):
        shutil.rmtree(path, onexc=again)


def write(base, files):
    for rel, text in files.items():
        p = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def git(c, *args):
    r = subprocess.run(GIT + ["-C", c] + list(args), capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def fixture(base, files=None):
    """A stand-in clone, a planted worktree and a pins file under base.
    Returns a dict: clone, worktree, pins, commit (the pinned one), later,
    first (the commit with no canbench/)."""
    rmtree(base)
    c = os.path.join(base, "can-tools")
    os.makedirs(c)
    git(c, "init", "-q")
    write(c, {"README.md": "can-tools stand-in\n"})
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "first, no canbench")
    first = git(c, "rev-parse", "HEAD")
    write(c, files or PINNED_FILES)
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "pinned")
    commit = git(c, "rev-parse", "HEAD")
    write(c, LATER_FILES)
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "later")
    later = git(c, "rev-parse", "HEAD")
    # Check HEAD out again under the .gitattributes: CRLF in the working tree.
    for rel in (files or PINNED_FILES):
        if rel.startswith("canbench/"):
            os.remove(os.path.join(c, *rel.split("/")))
    git(c, "checkout", "-q", "--", ".")
    with open(os.path.join(c, "canbench", "__init__.py"), "a") as fh:
        fh.write("WORKING_TREE_EDIT = True\n")
    wt = os.path.join(base, "worktree")
    os.makedirs(wt)
    with open(os.path.join(wt, ".git"), "w") as fh:
        fh.write("gitdir: nowhere\n")
    pins = os.path.join(base, "can_tools_pin.json")
    with open(pins, "w") as fh:
        json.dump({"can-tools": {"repo": "https://example.invalid/can-tools",
                                 "commit": commit}}, fh)
    return {"clone": c, "worktree": wt, "pins": pins, "commit": commit,
            "later": later, "first": first}


@contextlib.contextmanager
def pointed(fx, clone=True):
    """can_tools_pin pointed at the fixture: its pins file, its worktree
    and (clone=True) CAN_TOOLS_DIR set to its clone."""
    old = (ctp.PINS_FILE, ctp.START, os.environ.get(ctp.ENV))
    ctp.PINS_FILE, ctp.START = fx["pins"], fx["worktree"]
    if clone:
        os.environ[ctp.ENV] = fx["clone"]
    else:
        os.environ.pop(ctp.ENV, None)
    try:
        yield
    finally:
        ctp.PINS_FILE, ctp.START = old[0], old[1]
        if old[2] is None:
            os.environ.pop(ctp.ENV, None)
        else:
            os.environ[ctp.ENV] = old[2]


def child_env(fx=None):
    env = dict(os.environ)
    for k in (ctp.ENV, "REVERSE_IT_DIR", "PYTHONPATH"):
        env.pop(k, None)
    if fx is not None:
        env[ctp.ENV] = fx["clone"]
    return env


def files_under(d):
    out = {}
    for dp, _, fns in os.walk(d):
        for f in fns:
            full = os.path.join(dp, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, d).replace(os.sep, "/")] = fh.read()
    return out


class PinTool(unittest.TestCase):

    def setUp(self):
        self.fx = fixture(os.path.join(SCRATCH, self.id().rsplit(".", 1)[-1]))

    def refused(self, fn, *words, **kw):
        with self.assertRaises(ctp.CanToolsPinError) as cm:
            fn(**kw)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    # pin()
    def test_the_committed_pin_is_a_full_commit(self):
        p = ctp.pin()
        self.assertEqual(p["commit"], "14f8ee60d0a5b004755a9b45a852593d313b76ab")
        self.assertEqual(p["repo"], "https://github.com/nickyivyca/can-tools")

    def test_an_incomplete_or_short_pin_is_refused(self):
        bad = os.path.join(self.fx["worktree"], "bad.json")
        for entry in ({}, {"repo": "r"}, {"commit": "a" * 40},
                      {"repo": "r", "commit": "14f8ee6"},
                      {"repo": "r", "commit": "A" * 40},
                      {"repo": "", "commit": "a" * 40}):
            with open(bad, "w") as fh:
                json.dump({"can-tools": entry} if entry else {}, fh)
            self.refused(ctp.pin, "can_tools_pin.json", path=bad)

    # clone()
    def test_clone_unset_blank_or_without_canbench_is_refused(self):
        for env in ({}, {ctp.ENV: ""}, {ctp.ENV: "  "}):
            self.refused(ctp.clone, "CAN_TOOLS_DIR is not set", environ=env)
        self.refused(ctp.clone, "holds no canbench/",
                     environ={ctp.ENV: self.fx["worktree"]})
        self.assertEqual(ctp.clone({ctp.ENV: " %s " % self.fx["clone"]}),
                         os.path.normpath(self.fx["clone"]))

    # install()
    def test_install_writes_the_pinned_raw_blobs(self):
        with pointed(self.fx):
            d, now = ctp.install()
        self.assertTrue(now)
        self.assertEqual(d, os.path.join(self.fx["worktree"], "scratch",
                                         "can-tools", self.fx["commit"]))
        want = {k[len("canbench/"):]: v.encode()
                for k, v in PINNED_FILES.items() if k.startswith("canbench/")}
        self.assertEqual(files_under(os.path.join(d, "canbench")), want)
        self.assertEqual(os.listdir(os.path.dirname(d)), [self.fx["commit"]])

    def test_a_second_install_writes_nothing(self):
        with pointed(self.fx):
            d, _ = ctp.install()
            before = files_under(d)
            d2, now = ctp.install()
        self.assertEqual((d2, now), (d, False))
        self.assertEqual(files_under(d), before)

    def test_install_refuses_without_the_clone_or_the_commit(self):
        with pointed(self.fx, clone=False):
            self.refused(ctp.install, "CAN_TOOLS_DIR is not set")
        with open(self.fx["pins"], "w") as fh:
            json.dump({"can-tools": {"repo": "r", "commit": "0123456789abcdef"
                                     "0123456789abcdef01234567"}}, fh)
        with pointed(self.fx):
            self.refused(ctp.install, "has no commit 0123456789abcdef")
        self.assertFalse(os.path.exists(os.path.join(
            self.fx["worktree"], "scratch", "can-tools",
            "0123456789abcdef0123456789abcdef01234567")))
        left = os.listdir(os.path.join(self.fx["worktree"], "scratch",
                                       "can-tools")) if os.path.isdir(
            os.path.join(self.fx["worktree"], "scratch", "can-tools")) else []
        self.assertEqual(left, [])

    def test_a_pinned_commit_without_canbench_is_refused(self):
        with open(self.fx["pins"], "w") as fh:
            json.dump({"can-tools": {"repo": "r", "commit": self.fx["first"]}}, fh)
        with pointed(self.fx):
            self.refused(ctp.install, "has no canbench/__init__.py")

    def test_a_bad_folder_already_there_is_refused_not_replaced(self):
        with pointed(self.fx):
            d, _ = ctp.install()
            p = os.path.join(d, "canbench", "live", "receiver.py")
            with open(p, "a") as fh:
                fh.write("# edited\n")
            msg = self.refused(ctp.install, "changed ['live/receiver.py']",
                               "not replaced")
        with open(p) as fh:
            self.assertIn("# edited", fh.read(), msg)

    # verify()
    def test_verify_refuses_missing_changed_added_and_another_commit(self):
        with pointed(self.fx):
            d, _ = ctp.install()
            ctp.verify(d)
            os.makedirs(os.path.join(d, "canbench", "__pycache__"))
            open(os.path.join(d, "canbench", "__pycache__", "x.pyc"), "w").close()
            ctp.verify(d)
            missing = os.path.join(self.fx["worktree"], "nowhere")
            self.refused(ctp.verify, "is not installed", "install", d=missing)
            extra = os.path.join(d, "canbench", "extra.py")
            open(extra, "w").close()
            self.refused(ctp.verify, "not in it ['extra.py']", d=d)
            os.remove(extra)
            note = os.path.join(d, "canbench", "live", "note.md")
            with open(note, "rb") as fh:
                keep = fh.read()
            os.remove(note)
            self.refused(ctp.verify, "missing ['live/note.md']", d=d)
            with open(note, "wb") as fh:
                fh.write(keep.replace(b"\n", b"\r\n"))
            self.refused(ctp.verify, "changed ['live/note.md']", d=d)
            with open(note, "wb") as fh:
                fh.write(keep)
            ctp.verify(d)
        # The clone's working tree (HEAD past the pin, CRLF, an edit) is not
        # the pin.
        with pointed(self.fx):
            msg = self.refused(ctp.verify, "is not can-tools", d=self.fx["clone"])
        self.assertIn("live/receiver.py", msg)
        self.assertIn("__init__.py", msg)

    # folder()
    def test_folder_walks_up_to_the_worktree_or_is_refused(self):
        deep = os.path.join(self.fx["worktree"], "a", "b")
        os.makedirs(deep)
        with pointed(self.fx):
            self.assertEqual(ctp.folder(start=deep), os.path.join(
                self.fx["worktree"], "scratch", "can-tools", self.fx["commit"]))
        self.assertIsNone(ctp.worktree(deep, exists=lambda p: False))

    # use(), in a fresh interpreter
    USE = ("import sys, os, json\n"
           "sys.path.insert(0, %r)\n"
           "%s\n"
           "import can_tools_pin as ctp\n"
           "ctp.PINS_FILE, ctp.START = %r, %r\n"
           "before = list(sys.path)\n"
           "try:\n"
           "    m = ctp.use()\n"
           "    import canbench.live.receiver as r\n"
           "    print('LOADED ' + json.dumps([m.__file__, r.__file__, m.MARK]))\n"
           "    print('SAME ' + str(ctp.use() is m))\n"
           "except ctp.CanToolsPinError as e:\n"
           "    print('REFUSED ' + str(e))\n"
           "print('PATH ' + str(sys.path == before))\n")

    def use(self, preamble="", pythonpath=None, fx=True):
        env = child_env(self.fx if fx else None)
        if pythonpath:
            env["PYTHONPATH"] = pythonpath
        p = subprocess.run(
            [sys.executable, "-B", "-c", self.USE % (
                HERE, preamble, self.fx["pins"], self.fx["worktree"])],
            env=env, capture_output=True, text=True)
        return p.stdout + p.stderr

    def test_use_loads_the_install_with_sys_path_unchanged(self):
        with pointed(self.fx):
            d, _ = ctp.install()
        # The clone earlier on the path, not imported, does not win.
        out = self.use(pythonpath=self.fx["clone"])
        pkg = os.path.join(d, "canbench")
        line = [ln for ln in out.splitlines() if ln.startswith("LOADED ")]
        self.assertEqual(len(line), 1, out)
        init, rec, mark = json.loads(line[0][7:])
        self.assertEqual(os.path.normcase(init),
                         os.path.normcase(os.path.join(pkg, "__init__.py")))
        self.assertEqual(os.path.normcase(rec), os.path.normcase(
            os.path.join(pkg, "live", "receiver.py")))
        self.assertEqual(mark, "pinned")
        self.assertIn("SAME True", out)
        self.assertIn("PATH True", out)

    def test_use_refuses_a_canbench_imported_from_elsewhere(self):
        with pointed(self.fx):
            ctp.install()
        out = self.use(preamble="import canbench", pythonpath=self.fx["clone"])
        self.assertIn("REFUSED canbench is already imported from", out)
        self.assertIn(os.path.join(self.fx["clone"], "canbench"), out)

    def test_use_refuses_without_an_install_naming_the_command(self):
        out = self.use()
        self.assertIn("REFUSED", out)
        self.assertIn("is not installed", out)
        self.assertIn("can_tools_pin.py install", out)

    def test_use_refuses_an_install_that_fails_the_checks(self):
        with pointed(self.fx):
            d, _ = ctp.install()
        with open(os.path.join(d, "canbench", "__init__.py"), "a") as fh:
            fh.write("EDITED = 1\n")
        out = self.use()
        self.assertIn("REFUSED", out)
        self.assertIn("changed ['__init__.py']", out)

    def test_main_check_and_where(self):
        def run(*a):
            p = subprocess.run(
                [sys.executable, "-B", "-c",
                 "import sys; sys.path.insert(0, %r); import can_tools_pin as c; "
                 "c.PINS_FILE, c.START = %r, %r; sys.exit(c.main(%r))"
                 % (HERE, self.fx["pins"], self.fx["worktree"], list(a))],
                env=child_env(self.fx), capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        rc, out = run("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("is not installed", out)
        rc, out = run("install")
        self.assertEqual(rc, 0, out)
        self.assertIn("written and checked", out)
        rc, out = run("install")
        self.assertIn("already there, checked", out)
        rc, out = run("check")
        self.assertEqual(rc, 0, out)
        rc, out = run("where")
        self.assertEqual(out.strip(), os.path.join(
            self.fx["worktree"], "scratch", "can-tools", self.fx["commit"]))
        self.assertEqual(run("bogus")[0], 2)


class RealPin(unittest.TestCase):

    def test_the_real_clone_has_the_pinned_commit(self):
        if not os.environ.get(ctp.ENV, "").strip():
            self.skipTest("CAN_TOOLS_DIR is not set: the real pin is not checked")
        files = ctp.tree(ctp.clone(), ctp.pin())
        self.assertIn("live/receiver.py", files)


if __name__ == "__main__":
    unittest.main()
