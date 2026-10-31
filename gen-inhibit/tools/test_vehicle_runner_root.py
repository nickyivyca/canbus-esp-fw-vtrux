"""Implementor self-test of where vehicle_runner.py takes canbench from. Not
evidence.

    py -3.14 -B gen-inhibit/tools/test_vehicle_runner_root.py

--interface auto (the default) finds the dongle with canbench, which comes
from the can-tools clone named by CAN_TOOLS_DIR (AGENTS.md, "can-tools", user
2026-10-30): no default, refused if unset or if the folder holds no
canbench/, loaded from $CAN_TOOLS_DIR/canbench with neither the clone nor
reverse-it on sys.path, and a canbench already imported from anywhere else is
refused. reverse-it's mirror is never read. The other interfaces need no
setting, and importing the file needs none either. The auto path runs in a
child process against a stand-in clone, under scratch/vehicle-runner-test/ at
this worktree's root, whose canbench.live.receiver detects nothing: the "no
CAN interface detected" exit shows canbench came from the clone named. The
child's environment is built here, so the result is the same with
CAN_TOOLS_DIR set or unset in the shell. Needs python-can, which the file
imports.
"""

import ast
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scratch",
                       "vehicle-runner-test")
sys.path.insert(0, HERE)
import vehicle_runner as vr  # noqa: E402

# Calls Runner.open_and_identify on a stand-in self, as --interface auto does,
# after the optional %s preamble, and prints sys.path as it was left.
AUTO = ("import sys, types\n"
        "sys.path.insert(0, %r)\n"
        "%s\n"
        "import vehicle_runner as vr\n"
        "me = types.SimpleNamespace(args=types.SimpleNamespace("
        "interface='auto', bitrate=500000))\n"
        "try:\n"
        "    vr.Runner.open_and_identify(me)\n"
        "finally:\n"
        "    print('PATH=' + repr(sys.path))\n")


def child_env(clone, reverse_it=None, pythonpath=None):
    env = dict(os.environ)
    for k in ("CAN_TOOLS_DIR", "REVERSE_IT_DIR", "PYTHONPATH"):
        env.pop(k, None)
    if clone is not None:
        env["CAN_TOOLS_DIR"] = clone
    if reverse_it is not None:
        env["REVERSE_IT_DIR"] = reverse_it
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    return env


def norm(p):
    return os.path.normcase(os.path.normpath(p))


class Root(unittest.TestCase):

    def setUp(self):
        self.d = os.path.join(SCRATCH, self.id().rsplit(".", 1)[-1])
        if os.path.exists(self.d):
            shutil.rmtree(self.d)
        os.makedirs(self.d)

    def clone(self, canbench=True):
        c = os.path.join(self.d, "can-tools")
        os.makedirs(c)
        if canbench:
            self.canbench(c, "STAND-IN")
        return c

    def reverse_it(self):
        # A reverse-it root as it is during the cutover: canre/, projects/
        # and the mirror's canbench/ beside them.
        r = os.path.join(self.d, "reverse-it")
        for sub in ("canre", "projects"):
            os.makedirs(os.path.join(r, sub))
        self.canbench(r, "MIRROR")
        return r

    def canbench(self, where, says):
        live = os.path.join(where, "canbench", "live")
        os.makedirs(live)
        for p in (os.path.join(where, "canbench", "__init__.py"),
                  os.path.join(live, "__init__.py")):
            open(p, "w").close()
        with open(os.path.join(live, "receiver.py"), "w") as f:
            f.write("def detect_all_can_interfaces(bitrate):\n"
                    "    print('%s canbench %%d' %% bitrate)\n"
                    "    return []\n" % says)

    def refused(self, environ, *words):
        with self.assertRaises(SystemExit) as cm:
            vr.can_tools_dir(environ)
        msg = str(cm.exception.code)
        for w in words:
            self.assertIn(w, msg)

    def test_unset_or_blank_is_refused_naming_the_variable(self):
        for env in ({}, {"CAN_TOOLS_DIR": ""}, {"CAN_TOOLS_DIR": "  "}):
            self.refused(env, "CAN_TOOLS_DIR", "--interface")

    def test_reverse_it_is_no_default(self):
        self.refused({"REVERSE_IT_DIR": self.reverse_it()}, "needs CAN_TOOLS_DIR")

    def test_a_folder_without_canbench_is_refused(self):
        self.refused({"CAN_TOOLS_DIR": os.path.join(self.d, "nowhere")},
                     "holds no canbench/")
        c = self.clone(canbench=False)
        self.refused({"CAN_TOOLS_DIR": c}, c, "holds no canbench/")
        os.makedirs(os.path.join(c, "canbench"))   # a folder, no package
        self.refused({"CAN_TOOLS_DIR": c}, c, "holds no canbench/")

    def test_the_clone_is_returned(self):
        c = self.clone()
        self.assertEqual(vr.can_tools_dir({"CAN_TOOLS_DIR": " %s " % c}),
                         os.path.normpath(c))

    def test_importing_needs_no_setting(self):
        p = subprocess.run([sys.executable, "-B", "-c",
                            "import sys; sys.path.insert(0, %r); "
                            "import vehicle_runner; print('IMPORTED')" % HERE],
                           env=child_env(None), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("IMPORTED", p.stdout)

    def run_auto(self, clone, preamble="", **kw):
        p = subprocess.run([sys.executable, "-B", "-c", AUTO % (HERE, preamble)],
                           env=child_env(clone, **kw), capture_output=True,
                           text=True)
        out = p.stdout + p.stderr
        path = [ln[5:] for ln in p.stdout.splitlines() if ln.startswith("PATH=")]
        self.assertEqual(len(path), 1, out)
        return p.returncode, out, [norm(e) for e in ast.literal_eval(path[0]) if e]

    def test_auto_takes_canbench_from_the_clone(self):
        # Neither a canbench earlier on the path nor reverse-it's mirror may
        # win, and neither the clone nor reverse-it is put on the path.
        decoy = os.path.join(self.d, "decoy")
        self.canbench(decoy, "DECOY")
        c, r = self.clone(), self.reverse_it()
        rc, out, path = self.run_auto(c, reverse_it=r, pythonpath=decoy)
        self.assertIn("STAND-IN canbench 500000", out)
        self.assertNotIn("DECOY", out)
        self.assertNotIn("MIRROR", out)
        self.assertIn("no CAN interface detected", out)
        self.assertNotEqual(rc, 0)
        self.assertNotIn(norm(c), path)
        self.assertNotIn(norm(r), path)

    def test_auto_without_the_setting_refuses_before_canbench(self):
        rc, out, _ = self.run_auto(None, reverse_it=self.reverse_it())
        self.assertNotEqual(rc, 0)
        self.assertIn("needs CAN_TOOLS_DIR", out)
        self.assertNotIn("canbench", out.replace("canbench's", "")
                         .replace("holding canbench/", ""))
        self.assertNotIn("MIRROR", out)
        self.assertNotIn("ModuleNotFoundError", out)

    def test_a_canbench_imported_from_elsewhere_is_refused(self):
        decoy = os.path.join(self.d, "decoy")
        self.canbench(decoy, "DECOY")
        c = self.clone()
        rc, out, _ = self.run_auto(c, pythonpath=decoy, preamble="import canbench")
        self.assertNotEqual(rc, 0)
        self.assertIn("canbench is already imported from", out)
        self.assertIn(os.path.join(decoy, "canbench"), out)
        self.assertNotIn("DECOY canbench", out)
        self.assertNotIn("STAND-IN canbench", out)

    def test_the_clones_canbench_imported_already_is_taken(self):
        c = self.clone()
        rc, out, _ = self.run_auto(
            c, preamble="import vehicle_runner; vehicle_runner.import_canbench()")
        self.assertIn("STAND-IN canbench 500000", out)
        self.assertIn("no CAN interface detected", out)
        self.assertNotIn("already imported", out)


if __name__ == "__main__":
    unittest.main()
