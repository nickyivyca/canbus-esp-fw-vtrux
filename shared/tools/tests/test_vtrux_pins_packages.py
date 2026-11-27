"""Tests of the pins on outside packages (vtrux_pins: pinsfile, cantools,
canre): SPEC R35.

    python -B shared/tools/tests/test_vtrux_pins_packages.py
    python -B shared/tools/tests/test_vtrux_pins_packages.py --can-fail

canbench is tested against a stand-in can-tools clone, a real git repository
made in the run's scratch folder (pinpkgkit.make_clone), and canre against
stand-in installs and a stand-in pip (pinpkgkit), so no network is needed.
Each test's project is a planted git worktree: a folder with a .git file,
holding the pins file. Needs git.

--can-fail runs this file against edited copies of vtrux_pins (MUTANTS), each
a way the checks could be wrong, and passes only if the unedited copy passes
and every edited one fails. The copy is imported through
VTRUX_PINS_UNDER_TEST, the folder holding its vtrux_pins/ and vtrux_build/.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
UNDER_TEST = os.environ.get("VTRUX_PINS_UNDER_TEST")
CODE = UNDER_TEST or TOOLS
sys.path.insert(0, HERE)
sys.path.insert(0, CODE)

import pinpkgkit                                 # noqa: E402
import scratch                                   # noqa: E402
from pinpkgkit import OTHER, REPO                # noqa: E402
from vtrux_pins import canre, cantools, pinsfile  # noqa: E402
from vtrux_pins.pinsfile import PinsError        # noqa: E402

COMMIT = "ffbe0291f7b817360f03f1082e07240804cf6084"


def child_env(**extra):
    env = dict(os.environ)
    for k in ("CAN_TOOLS_DIR", "CANRE_DIR", "PYTHONPATH"):
        env.pop(k, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def planted_worktree(base):
    wt = os.path.join(base, "worktree")
    os.makedirs(wt)
    with open(os.path.join(wt, ".git"), "w") as fh:
        fh.write("gitdir: nowhere\n")
    return wt


def write_pins(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def files_under(d):
    out = {}
    for dp, _, fns in os.walk(d):
        for f in fns:
            full = os.path.join(dp, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, d).replace(os.sep, "/")] = fh.read()
    return out


def run_cli(*args, **env):
    r = subprocess.run([sys.executable, "-B", "-m", "vtrux_pins"] + list(args),
                       env=child_env(PYTHONPATH=CODE, **env),
                       capture_output=True, text=True, cwd=HERE)
    return r.returncode, r.stdout + r.stderr


class Refusing(unittest.TestCase):

    def refused(self, fn, *words, **kw):
        """fn, or (fn, args...), must raise PinsError holding every word."""
        with self.assertRaises(PinsError) as cm:
            if isinstance(fn, tuple):
                fn[0](*fn[1:], **kw)
            else:
                fn(**kw)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)


class PinsFile(Refusing):

    def setUp(self):
        self.base = scratch.fresh(self.id().rsplit(".", 1)[-1])

    def test_an_entry_is_read_and_other_keys_are_ignored(self):
        f = write_pins(os.path.join(self.base, "pins.json"), {
            "files": {"x": 1}, "_comment": "c",
            "can-tools": {"repo": "r", "commit": "a" * 40, "note": "n"},
            "canre": {"repo": REPO, "version": "0.1.0", "commit": COMMIT}})
        self.assertEqual(pinsfile.read(f, "can-tools"),
                         {"repo": "r", "commit": "a" * 40})
        self.assertEqual(pinsfile.read(f, "canre"),
                         {"repo": REPO, "version": "0.1.0", "commit": COMMIT})

    def test_an_incomplete_or_short_entry_is_refused(self):
        f = os.path.join(self.base, "pins.json")
        for entry in (None, {}, {"repo": "r"}, {"commit": "a" * 40},
                      {"repo": "r", "commit": "14f8ee6"},
                      {"repo": "r", "commit": "A" * 40},
                      {"repo": "r", "commit": "g" * 40},
                      {"repo": "", "commit": "a" * 40},
                      {"repo": 1, "commit": "a" * 40}, "r"):
            write_pins(f, {"other": 1} if entry is None
                       else {"can-tools": entry})
            self.refused((pinsfile.read, f, "can-tools"), "can-tools")
        write_pins(f, {"canre": {"repo": REPO, "commit": COMMIT}})
        self.refused((pinsfile.read, f, "canre"), "version")

    def test_a_file_that_is_not_a_json_object_is_refused(self):
        f = os.path.join(self.base, "pins.json")
        self.refused((pinsfile.read, f, "canre"), "cannot be read")
        with open(f, "w") as fh:
            fh.write("{not json")
        self.refused((pinsfile.read, f, "canre"), "cannot be read")
        write_pins(f, [1])
        self.refused((pinsfile.read, f, "canre"), "not a JSON object")

    def test_the_worktree_is_found_by_walking_up_from_the_file(self):
        wt = planted_worktree(self.base)
        deep = os.path.join(wt, "a", "b")
        os.makedirs(deep)
        f = os.path.join(deep, "pins.json")
        self.assertEqual(pinsfile.worktree(f), wt)
        self.assertIsNone(pinsfile.worktree(f, exists=lambda p: False))


class CanTools(Refusing):

    def setUp(self):
        self.base = scratch.fresh(self.id().rsplit(".", 1)[-1])
        self.clone = os.path.join(self.base, "can-tools")
        self.c = pinpkgkit.make_clone(self.clone)
        self.wt = planted_worktree(self.base)
        os.makedirs(os.path.join(self.wt, "tools"))
        self.pins = write_pins(os.path.join(self.wt, "tools", "pins.json"),
                               {"can-tools": {"repo": "r",
                                              "commit": self.c["commit"]}})
        self.env = {"CAN_TOOLS_DIR": self.clone}
        self.target = os.path.join(self.wt, "scratch", "can-tools",
                                   self.c["commit"])

    def repin(self, commit):
        write_pins(self.pins, {"can-tools": {"repo": "r", "commit": commit}})

    def test_the_clone_must_be_named_and_hold_canbench(self):
        for env in ({}, {"CAN_TOOLS_DIR": ""}, {"CAN_TOOLS_DIR": "  "}):
            self.refused(cantools.clone, "CAN_TOOLS_DIR is not set",
                         environ=env)
        self.refused(cantools.clone, "holds no canbench/",
                     environ={"CAN_TOOLS_DIR": self.wt})
        self.assertEqual(cantools.clone({"CAN_TOOLS_DIR": " %s " % self.clone}),
                         os.path.normpath(self.clone))

    def test_install_writes_the_pinned_raw_blobs(self):
        d, now = cantools.install(self.pins, self.env)
        self.assertEqual((d, now), (self.target, True))
        want = {k[len("canbench/"):]: v.encode()
                for k, v in pinpkgkit.PINNED_FILES.items()
                if k.startswith("canbench/")}
        self.assertEqual(files_under(os.path.join(d, "canbench")), want)
        self.assertEqual(os.listdir(os.path.dirname(d)), [self.c["commit"]])

    def test_a_second_install_writes_nothing(self):
        d, _ = cantools.install(self.pins, self.env)
        before = files_under(d)
        self.assertEqual(cantools.install(self.pins, self.env), (d, False))
        self.assertEqual(files_under(d), before)

    def test_install_refuses_without_the_clone_or_the_commit(self):
        self.refused((cantools.install, self.pins), "CAN_TOOLS_DIR is not set",
                     environ={})
        self.repin(OTHER)
        self.refused((cantools.install, self.pins), "has no commit " + OTHER,
                     environ=self.env)
        par = os.path.join(self.wt, "scratch", "can-tools")
        self.assertEqual(os.listdir(par) if os.path.isdir(par) else [], [])

    def test_a_pinned_commit_without_canbench_is_refused(self):
        self.repin(self.c["first"])
        self.refused((cantools.install, self.pins),
                     "has no canbench/__init__.py", environ=self.env)

    def test_a_link_under_canbench_at_the_commit_is_refused(self):
        self.repin(pinpkgkit.add_link_commit(self.clone))
        self.refused((cantools.install, self.pins), "not regular files",
                     "link.py", environ=self.env)

    def test_a_bad_folder_already_there_is_refused_not_replaced(self):
        d, _ = cantools.install(self.pins, self.env)
        p = os.path.join(d, "canbench", "live", "receiver.py")
        with open(p, "a") as fh:
            fh.write("# edited\n")
        self.refused((cantools.install, self.pins),
                     "changed ['live/receiver.py']", "not replaced",
                     environ=self.env)
        with open(p) as fh:
            self.assertIn("# edited", fh.read())

    def test_check_refuses_missing_changed_and_added_files(self):
        d, _ = cantools.install(self.pins, self.env)
        self.assertEqual(cantools.check(self.pins, self.env), d)
        os.makedirs(os.path.join(d, "canbench", "__pycache__"))
        open(os.path.join(d, "canbench", "__pycache__", "x.pyc"), "w").close()
        self.assertEqual(cantools.check(self.pins, self.env), d)
        extra = os.path.join(d, "canbench", "extra.py")
        open(extra, "w").close()
        self.refused((cantools.check, self.pins), "not in it ['extra.py']",
                     environ=self.env)
        os.remove(extra)
        note = os.path.join(d, "canbench", "live", "note.md")
        with open(note, "rb") as fh:
            keep = fh.read()
        os.remove(note)
        self.refused((cantools.check, self.pins), "missing ['live/note.md']",
                     environ=self.env)
        with open(note, "wb") as fh:
            fh.write(keep.replace(b"\n", b"\r\n"))
        self.refused((cantools.check, self.pins), "changed ['live/note.md']",
                     environ=self.env)
        with open(note, "wb") as fh:
            fh.write(keep)
        cantools.check(self.pins, self.env)

    def test_check_without_an_install_names_the_command(self):
        self.refused((cantools.check, self.pins), "is not installed",
                     "vtrux_pins can-tools install --pins", environ=self.env)

    def test_the_clones_working_tree_is_not_the_pin(self):
        p = cantools.pin(self.pins)
        msg = self.refused((cantools.verify, self.clone, p, self.clone,
                            self.pins), "is not can-tools")
        self.assertIn("live/receiver.py", msg)
        self.assertIn("__init__.py", msg)

    def test_outside_a_worktree_it_is_refused(self):
        with mock.patch.object(pinsfile, "worktree", lambda path: None):
            self.refused((cantools.folder, self.pins), "in no git worktree")
            self.refused((cantools.install, self.pins), "in no git worktree",
                         environ=self.env)

    def test_a_partial_folder_that_cannot_be_removed_refuses_install(self):
        with mock.patch.object(cantools, "remove_tree", lambda p: False):
            self.refused((cantools.install, self.pins), "partial folder",
                         environ=self.env)
        self.assertFalse(os.path.exists(self.target))

    def test_a_partial_folder_from_an_earlier_run_is_removed(self):
        stale = "%s.partial-%d" % (self.target, os.getpid())
        os.makedirs(os.path.join(stale, "canbench"))
        open(os.path.join(stale, "canbench", "stale.py"), "w").close()
        d, now = cantools.install(self.pins, self.env)
        self.assertTrue(now)
        self.assertNotIn("stale.py", os.listdir(os.path.join(d, "canbench")))
        self.assertEqual(os.listdir(os.path.dirname(d)), [self.c["commit"]])

    def test_a_failed_install_leaves_no_folder_and_no_partial(self):
        real = cantools.verify

        def fail_on_partial(d, *a):
            if ".partial-" in d:
                raise PinsError("planted failure")
            return real(d, *a)
        with mock.patch.object(cantools, "verify", fail_on_partial):
            self.refused((cantools.install, self.pins), "planted failure",
                         environ=self.env)
        self.assertEqual(os.listdir(os.path.dirname(self.target)), [])

    USE = ("import sys, json\n"
           "sys.path.insert(0, %r)\n"
           "%s\n"
           "from vtrux_pins import cantools\n"
           "from vtrux_pins.pinsfile import PinsError\n"
           "before = list(sys.path)\n"
           "try:\n"
           "    m = cantools.use(%r)\n"
           "    import canbench.live.receiver as r\n"
           "    print('LOADED ' + json.dumps([m.__file__, r.__file__, m.MARK]))\n"
           "    print('SAME ' + str(cantools.use(%r) is m))\n"
           "except PinsError as e:\n"
           "    print('REFUSED ' + str(e))\n"
           "print('PATH ' + str(sys.path == before))\n")

    def use(self, preamble="", pythonpath=None):
        env = child_env(CAN_TOOLS_DIR=self.clone)
        if pythonpath:
            env["PYTHONPATH"] = pythonpath
        r = subprocess.run(
            [sys.executable, "-B", "-c",
             self.USE % (CODE, preamble, self.pins, self.pins)],
            env=env, capture_output=True, text=True)
        return r.stdout + r.stderr

    def test_use_loads_by_file_with_sys_path_unchanged(self):
        d, _ = cantools.install(self.pins, self.env)
        out = self.use(pythonpath=self.clone)
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
        cantools.install(self.pins, self.env)
        out = self.use(preamble="import canbench", pythonpath=self.clone)
        self.assertIn("REFUSED canbench is already imported from", out)
        self.assertIn(os.path.join(self.clone, "canbench"), out)

    def test_use_refuses_an_install_that_fails_the_checks(self):
        d, _ = cantools.install(self.pins, self.env)
        with open(os.path.join(d, "canbench", "__init__.py"), "a") as fh:
            fh.write("EDITED = 1\n")
        out = self.use()
        self.assertIn("REFUSED", out)
        self.assertIn("changed ['__init__.py']", out)

    def test_the_command(self):
        rc, out = run_cli("can-tools", "check", "--pins", self.pins,
                          CAN_TOOLS_DIR=self.clone)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED: can-tools", out)
        self.assertIn("is not installed", out)
        rc, out = run_cli("can-tools", "install", "--pins", self.pins,
                          CAN_TOOLS_DIR=self.clone)
        self.assertEqual(rc, 0, out)
        self.assertIn("written and checked", out)
        rc, out = run_cli("can-tools", "install", "--pins", self.pins,
                          CAN_TOOLS_DIR=self.clone)
        self.assertEqual(rc, 0, out)
        self.assertIn("already there, checked", out)
        rc, out = run_cli("can-tools", "check", "--pins", self.pins,
                          CAN_TOOLS_DIR=self.clone)
        self.assertEqual(rc, 0, out)
        rc, out = run_cli("can-tools", "where", "--pins", self.pins)
        self.assertEqual((rc, out.strip()), (0, self.target))
        self.assertEqual(run_cli("can-tools", "check", "--pins", self.pins)[0],
                         1)
        self.assertEqual(run_cli("can-tools", "bogus", "--pins", self.pins)[0],
                         2)
        self.assertEqual(run_cli("can-tools", "check")[0], 2)
        self.assertEqual(run_cli()[0], 2)


class Canre(Refusing):

    def setUp(self):
        self.base = scratch.fresh(self.id().rsplit(".", 1)[-1])
        self.wt = planted_worktree(self.base)
        self.pins = write_pins(os.path.join(self.wt, "pins.json"), {
            "files": {}, "canre": {"repo": REPO, "version": "0.1.0",
                                   "commit": COMMIT}})
        self.p = canre.pin(self.pins)
        self.target = os.path.join(self.wt, "scratch", "canre", COMMIT)

    def make(self, name, **kw):
        return pinpkgkit.make_canre(os.path.join(self.base, name),
                                    kw.pop("commit", COMMIT), **kw)

    def verify(self, d):
        return canre.verify(d, self.p, self.pins)

    def test_a_stand_in_install_of_the_pin_passes(self):
        d = self.make("good")
        self.assertEqual(self.verify(d),
                         os.path.join(d, "canre-0.1.0.dist-info"))
        os.makedirs(os.path.join(d, "canre", "__pycache__"))
        open(os.path.join(d, "canre", "__pycache__", "x.pyc"), "wb").close()
        self.verify(d)

    def test_a_missing_folder_names_the_install_command(self):
        self.refused((self.verify, os.path.join(self.base, "absent")),
                     "is not installed", "vtrux_pins canre install --pins")

    def test_an_install_of_anything_else_is_refused(self):
        for name, kw, word in (
                ("commit", {"commit": OTHER}, OTHER),
                ("repo", {"repo": "https://example.org/fork"}, "fork"),
                ("vcs", {"vcs": "hg"}, "hg"),
                ("nodu", {"du": None}, "no direct_url.json"),
                ("editable", {"du": "editable"}, "file:///src/canre"),
                ("version", {"version": "0.2.0"}, "0.2.0")):
            self.refused((self.verify, self.make(name, **kw)), word)

    def test_two_dist_infos_are_refused(self):
        d = self.make("two")
        shutil.copytree(os.path.join(d, "canre-0.1.0.dist-info"),
                        os.path.join(d, "canre-0.0.9.dist-info"))
        self.refused((self.verify, d), "2 canre .dist-info folders")

    def test_a_changed_added_or_removed_file_is_refused(self):
        d = self.make("changed")
        with open(os.path.join(d, "canre", "parsers", "__init__.py"), "a") as fh:
            fh.write("# edited\n")
        self.refused((self.verify, d), "changed or missing", "parsers")
        d = self.make("added")
        open(os.path.join(d, "canre", "parsers", "extra.py"), "w").close()
        self.refused((self.verify, d), "not in it", "extra.py")
        d = self.make("removed")
        os.remove(os.path.join(d, "canre", "parsers", "__init__.py"))
        self.refused((self.verify, d), "changed or missing", "parsers")
        d = self.make("noinit")
        shutil.rmtree(os.path.join(d, "canre"))
        self.refused((self.verify, d), "no canre/__init__.py")

    def test_unreadable_metadata_is_a_refusal(self):
        d = self.make("nometa")
        os.remove(os.path.join(d, "canre-0.1.0.dist-info", "METADATA"))
        self.refused((self.verify, d), "METADATA cannot be read")
        d = self.make("badjson")
        with open(os.path.join(d, "canre-0.1.0.dist-info",
                               "direct_url.json"), "w") as fh:
            fh.write("{")
        self.refused((self.verify, d), "not JSON")

    def test_the_folder_is_the_worktrees_or_canre_dir(self):
        self.assertEqual(canre.folder(self.pins, environ={}), self.target)
        other = os.path.join(self.base, "elsewhere")
        self.assertEqual(canre.folder(self.pins,
                                      environ={"CANRE_DIR": other + "/"}),
                         os.path.normpath(other))
        self.refused((canre.folder, self.pins), "not an absolute path",
                     environ={"CANRE_DIR": "relative/canre"})
        with mock.patch.object(pinsfile, "worktree", lambda path: None):
            self.refused((canre.folder, self.pins), "only CANRE_DIR",
                         environ={})
            self.assertEqual(canre.folder(self.pins,
                                          environ={"CANRE_DIR": other}),
                             other)

    def test_check_reads_canre_dir_outside_a_worktree(self):
        d = self.make("shipped")
        with mock.patch.object(pinsfile, "worktree", lambda path: None):
            self.assertEqual(canre.check(self.pins, {"CANRE_DIR": d}), d)
        rc, out = run_cli("canre", "check", "--pins", self.pins, CANRE_DIR=d)
        self.assertEqual(rc, 0, out)
        rc, out = run_cli("canre", "where", "--pins", self.pins, CANRE_DIR=d)
        self.assertEqual((rc, out.strip()), (0, d))
        rc, out = run_cli("canre", "check", "--pins", self.pins)
        self.assertEqual(rc, 1, out)
        self.assertIn("is not installed", out)

    def child(self, code, *args):
        r = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.path.insert(0, %r)\n"
             "from vtrux_pins import canre\n" % CODE + code] + list(args),
            env=child_env(), capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout + r.stderr).strip()

    def test_use_loads_from_the_folder_first_on_sys_path(self):
        good = self.make("good")
        shadow = os.path.join(self.base, "shadow")
        os.makedirs(os.path.join(shadow, "canre", "parsers"))
        with open(os.path.join(shadow, "canre", "__init__.py"), "w") as fh:
            fh.write("MARK = 'shadow'\n")
        open(os.path.join(shadow, "canre", "parsers", "__init__.py"),
             "w").close()
        use = ("import os\n"
               "d = canre.use(sys.argv[1], {'CANRE_DIR': sys.argv[2]})\n")
        show = ("import canre as c, canre.parsers\n"
                "print(c.MARK, sys.path[0] == sys.argv[2], "
                "canre.parsers.__file__.startswith(sys.argv[2] + os.sep))\n")
        rc, out = self.child(use + show, self.pins, good)
        self.assertEqual((rc, out), (0, "pinned True True"))
        rc, out = self.child("sys.path.insert(0, sys.argv[3])\n" + use + show,
                             self.pins, good, shadow)
        self.assertEqual((rc, out), (0, "pinned True True"))
        rc, out = self.child("sys.path.insert(0, sys.argv[3]); import canre "
                             "as c\n" + use, self.pins, good, shadow)
        self.assertNotEqual(rc, 0)
        self.assertIn("already imported", out)
        rc, out = self.child(use, self.pins, self.make("bad", commit=OTHER))
        self.assertNotEqual(rc, 0)
        self.assertIn("PinsError", out)

    # install, with the stand-in pip
    def start_pip(self):
        fake = os.path.join(self.base, "fake_pip.py")
        with open(fake, "w") as fh:
            fh.write(pinpkgkit.FAKE_PIP)
        self.log = os.path.join(self.base, "pip_log.txt")
        p = mock.patch.object(canre, "PIP", [sys.executable, "-B", fake])
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"TESTS_DIR": HERE,
                                         "PIP_LOG": self.log})
        e.start()
        self.addCleanup(e.stop)
        for k in ("PIP_EXIT", "PIP_COMMIT", "CANRE_DIR"):
            os.environ.pop(k, None)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(ln) for ln in fh]

    def partials(self):
        par = os.path.dirname(self.target)
        return ([x for x in os.listdir(par) if ".partial-" in x]
                if os.path.isdir(par) else [])

    def test_install_runs_pip_into_the_worktree_once(self):
        self.start_pip()
        self.assertEqual(canre.install(self.pins), (self.target, True))
        self.verify(self.target)
        c = self.calls()
        self.assertEqual(len(c), 1, c)
        self.assertEqual(c[0][-1], "git+%s@%s" % (REPO, COMMIT))
        for flag in ("--no-deps", "--no-compile", "--no-input"):
            self.assertIn(flag, c[0])
        self.assertIn(".partial-", c[0][c[0].index("--target") + 1])
        self.assertEqual(self.partials(), [])
        self.assertEqual(canre.install(self.pins), (self.target, False))
        self.assertEqual(len(self.calls()), 1)

    def test_install_never_writes_to_canre_dir(self):
        self.start_pip()
        other = os.path.join(self.base, "elsewhere")
        os.environ["CANRE_DIR"] = other
        self.assertEqual(canre.install(self.pins), (self.target, True))
        self.assertFalse(os.path.exists(other))

    def test_a_pip_that_fails_or_installs_another_commit_leaves_nothing(self):
        self.start_pip()
        for env in ({"PIP_COMMIT": OTHER}, {"PIP_EXIT": "1"}):
            with mock.patch.dict(os.environ, env):
                self.refused((canre.install, self.pins))
            self.assertFalse(os.path.exists(self.target))
            self.assertEqual(self.partials(), [])

    def test_a_bad_folder_already_there_is_refused_not_replaced(self):
        self.start_pip()
        pinpkgkit.make_canre(self.target, OTHER)
        self.refused((canre.install, self.pins), "not replaced")
        self.assertEqual(self.calls(), [])
        self.refused((self.verify, self.target), OTHER)

    def test_install_outside_a_worktree_is_refused(self):
        self.start_pip()
        os.environ["CANRE_DIR"] = os.path.join(self.base, "elsewhere")
        with mock.patch.object(pinsfile, "worktree", lambda path: None):
            self.refused((canre.install, self.pins), "in no git worktree")
        self.assertEqual(self.calls(), [])

    def test_a_partial_folder_that_cannot_be_removed_refuses_install(self):
        self.start_pip()
        with mock.patch.object(canre, "remove_tree", lambda p: False):
            self.refused((canre.install, self.pins), "partial folder")
        self.assertEqual(self.calls(), [])


# (name, file in vtrux_pins/, its text, the replacement): each must make this
# file fail. The text must occur in the file exactly once.
MUTANTS = [
    ("pins: a short commit accepted", "pinsfile.py",
     '    if len(c) != 40 or c.strip("0123456789abcdef"):\n',
     '    if False:\n'),
    ("pins: an empty field accepted", "pinsfile.py",
     "isinstance(p.get(k), str) and p[k]\n", "isinstance(p.get(k), str)\n"),
    ("pins: the worktree's .git ignored", "pinsfile.py",
     '        if exists(os.path.join(d, ".git")):\n', '        if False:\n'),
    ("can-tools: a default clone", "cantools.py",
     '    v = (os.environ if environ is None else environ).get(ENV, "").strip()\n',
     '    v = (os.environ if environ is None else environ).get(ENV, "").strip()'
     ' or "."\n'),
    ("can-tools: the commit not looked for", "cantools.py",
     '    rc, _, _ = _git(c, ["cat-file", "-e", commit + "^{commit}"])\n',
     '    rc = 0\n'),
    ("can-tools: links accepted", "cantools.py",
     '        if kind != "blob" or mode not in ("100644", "100755"):\n',
     '        if kind != "blob":\n'),
    ("can-tools: no __init__.py accepted", "cantools.py",
     '    if "__init__.py" not in files:\n', '    if False:\n'),
    ("can-tools: added files accepted", "cantools.py",
     "    added = sorted(set(have) - set(want))\n", "    added = []\n"),
    ("can-tools: missing files accepted", "cantools.py",
     "    missing = sorted(set(want) - set(have))\n", "    missing = []\n"),
    ("can-tools: changed files accepted", "cantools.py",
     "if want[k] != have[k])\n", "if False)\n"),
    ("can-tools: installed outside a worktree", "cantools.py",
     '    if wt is None:\n        raise PinsError("the pins file %s is in no git '
     'worktree, so it has no "',
     '    if False:\n        raise PinsError("the pins file %s is in no git '
     'worktree, so it has no "'),
    ("can-tools: an import from elsewhere kept", "cantools.py",
     "        if not _same(getattr(have, \"__file__\", None), init):\n",
     "        if False:\n"),
    ("can-tools: put on sys.path", "cantools.py",
     "    sys.modules[PACKAGE] = mod\n",
     "    sys.modules[PACKAGE] = mod\n    sys.path.insert(0, d)\n"),
    ("can-tools: a folder there is replaced", "cantools.py",
     "    if os.path.isdir(d):\n        try:\n            verify(d, p, c, "
     "pins_path)\n",
     "    if False:\n        try:\n            verify(d, p, c, pins_path)\n"),
    ("can-tools: not checked before the move", "cantools.py",
     "        verify(tmp, p, c, pins_path)\n", ""),
    ("can-tools: a stale partial kept", "cantools.py",
     "    if not remove_tree(tmp):\n", "    if False:\n"),
    ("can-tools: the partial left on a failure", "cantools.py",
     "    finally:\n        remove_tree(tmp)\n", "    finally:\n        pass\n"),
    ("canre: the commit not compared", "canre.py",
     ' or vcs.get("commit_id") != p["commit"]', ""),
    ("canre: the repo not compared", "canre.py",
     'du.get("url") != p["repo"] or ', ""),
    ("canre: the vcs not compared", "canre.py",
     'vcs.get("vcs") != "git"\n            or ', ""),
    ("canre: the version not compared", "canre.py",
     '    if version != p["version"]:\n', "    if False:\n"),
    ("canre: no direct_url.json accepted", "canre.py",
     "    if not os.path.isfile(du_path):\n        raise",
     "    if not os.path.isfile(du_path):\n        return info\n        raise"),
    ("canre: two dist-infos accepted", "canre.py",
     "    if len(infos) != 1:\n", "    if not infos:\n"),
    ("canre: the RECORD hashes not compared", "canre.py",
     "        if got != want:\n", "        if False:\n"),
    ("canre: a file added accepted", "canre.py",
     "                  not in listed]", "                  and False]"),
    ("canre: CANRE_DIR ignored", "canre.py",
     "    if env:\n        if not os.path.isabs(env):",
     "    if False:\n        if not os.path.isabs(env):"),
    ("canre: a relative CANRE_DIR accepted", "canre.py",
     "        if not os.path.isabs(env):\n", "        if False:\n"),
    ("canre: found by its own location outside a worktree", "canre.py",
     "    if wt is None:\n        return None\n",
     "    if wt is None:\n        wt = os.path.dirname(os.path.abspath("
     "pins_path))\n"),
    ("canre: install writes to CANRE_DIR", "canre.py",
     "    d = _worktree_folder(pins_path, p)\n    if d is None:\n        raise "
     "PinsError(\"the pins file %s is in no git worktree: canre is \"",
     "    d = folder(pins_path, p)\n    if d is None:\n        raise "
     "PinsError(\"the pins file %s is in no git worktree: canre is \""),
    ("canre: an import from elsewhere kept", "canre.py",
     "    if mod is not None and not _same(", "    if False and not _same("),
    ("canre: the folder last on sys.path", "canre.py",
     "    sys.path.insert(0, d)\n", "    sys.path.append(d)\n"),
    ("canre: dependencies taken", "canre.py", '"--no-deps", ', ""),
    ("canre: not checked before the move", "canre.py",
     "        verify(tmp, p, pins_path)\n", ""),
    ("canre: a folder there is replaced", "canre.py",
     "    if os.path.isdir(d):\n        try:", "    if False:\n        try:"),
    ("canre: a stale partial kept", "canre.py",
     "    if not remove_tree(tmp):\n", "    if False:\n"),
    ("canre: the partial left on a failure", "canre.py",
     "    finally:\n        remove_tree(tmp)\n", "    finally:\n        pass\n"),
]


def can_fail(mutants=None, test_file=None):
    """Run test_file (this file) against the unedited vtrux_pins and each
    mutant; True if the first passes and every mutant fails."""
    mutants = MUTANTS if mutants is None else mutants
    test_file = test_file or os.path.abspath(__file__)
    ok = True
    for i, (name, fname, old, new) in enumerate(
            [("control", None, None, None)] + mutants):
        if old is not None:
            with open(os.path.join(TOOLS, "vtrux_pins", fname),
                      encoding="utf-8") as f:
                source = f.read()
            if source.count(old) != 1:
                print("%-56s BAD MUTANT (its text occurs %d times in %s)"
                      % (name, source.count(old), fname))
                ok = False
                continue
        base = scratch.fresh("canfail_%02d" % i)
        for pkg in ("vtrux_pins", "vtrux_build"):
            shutil.copytree(os.path.join(TOOLS, pkg), os.path.join(base, pkg),
                            ignore=shutil.ignore_patterns("__pycache__"))
        if old is not None:
            scratch.write(os.path.join(base, "vtrux_pins", fname),
                          source.replace(old, new))
        env = dict(os.environ, VTRUX_PINS_UNDER_TEST=base,
                   PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", test_file], env=env,
                           capture_output=True, text=True)
        last = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
        if old is None:
            good = r.returncode == 0
            print("%-56s %s (%s)" % (name, "passes" if good else "FAILS", last))
            if not good:
                print(r.stderr[-3000:])
        else:
            good = r.returncode != 0
            print("%-56s %s (%s)" % (name, "caught" if good else "MISSED",
                                     last))
        ok = ok and good
    print("can-fail: %s, %s" % ("PASS" if ok else "FAIL",
                                "the control passes and all %d mutants fail"
                                % len(mutants) if ok else "see above"))
    return ok


if __name__ == "__main__":
    if sys.argv[1:] == ["--can-fail"]:
        sys.exit(0 if can_fail() else 1)
    unittest.main()
