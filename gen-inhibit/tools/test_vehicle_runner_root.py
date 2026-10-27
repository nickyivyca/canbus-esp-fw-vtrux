"""Implementor self-test of vehicle_runner.py's reverse-it root. Not evidence.

    py -3.14 -B gen-inhibit/tools/test_vehicle_runner_root.py

--interface auto (the default) finds the dongle with reverse-it's canbench, so
it needs SeaDrive's reverse-it root, from REVERSE_IT_DIR under gi_paths' rule:
no default, refused if unset or if the folder lacks canre/ or projects/. The
other interfaces need no setting, and importing the file needs none either.
The auto path runs in a child process against a stand-in root, under
scratch/vehicle-runner-test/ at this worktree's root, whose
canbench.live.receiver detects nothing: the "no CAN interface detected" exit
shows canbench came from the root named. Needs python-can, which the file
imports.
"""

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

# Calls Runner.open_and_identify on a stand-in self, as --interface auto does.
AUTO = ("import sys, types\n"
        "sys.path.insert(0, %r)\n"
        "import vehicle_runner as vr\n"
        "me = types.SimpleNamespace(args=types.SimpleNamespace("
        "interface='auto', bitrate=500000))\n"
        "vr.Runner.open_and_identify(me)\n")


def child_env(root):
    env = dict(os.environ)
    env.pop("REVERSE_IT_DIR", None)
    if root is not None:
        env["REVERSE_IT_DIR"] = root
    return env


class Root(unittest.TestCase):

    def setUp(self):
        self.d = os.path.join(SCRATCH, self.id().rsplit(".", 1)[-1])
        if os.path.exists(self.d):
            shutil.rmtree(self.d)
        os.makedirs(self.d)

    def root(self, canre=True, projects=True, canbench=False):
        r = os.path.join(self.d, "reverse-it")
        os.makedirs(r)
        if canre:
            os.makedirs(os.path.join(r, "canre"))
        if projects:
            os.makedirs(os.path.join(r, "projects"))
        if canbench:
            self.canbench(r, "STAND-IN")
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
            vr.reverse_it_dir(environ)
        msg = str(cm.exception.code)
        for w in words:
            self.assertIn(w, msg)

    def test_unset_or_blank_is_refused_naming_the_variable(self):
        for env in ({}, {"REVERSE_IT_DIR": ""}, {"REVERSE_IT_DIR": "  "}):
            self.refused(env, "REVERSE_IT_DIR", "--interface")

    def test_a_folder_without_canre_or_projects_is_refused(self):
        self.refused({"REVERSE_IT_DIR": os.path.join(self.d, "nowhere")},
                     "canre/ and projects/")
        r = self.root(canre=False)
        self.refused({"REVERSE_IT_DIR": r}, r, "canre/ and projects/")
        shutil.rmtree(r)
        r = self.root(projects=False)
        self.refused({"REVERSE_IT_DIR": r}, r, "canre/ and projects/")

    def test_the_root_is_returned(self):
        r = self.root()
        self.assertEqual(vr.reverse_it_dir({"REVERSE_IT_DIR": " %s " % r}),
                         os.path.normpath(r))

    def test_importing_needs_no_setting(self):
        p = subprocess.run([sys.executable, "-B", "-c",
                            "import sys; sys.path.insert(0, %r); "
                            "import vehicle_runner; print('IMPORTED')" % HERE],
                           env=child_env(None), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("IMPORTED", p.stdout)

    def run_auto(self, root, pythonpath=None):
        env = child_env(root)
        if pythonpath:
            env["PYTHONPATH"] = pythonpath
        p = subprocess.run([sys.executable, "-B", "-c", AUTO % HERE],
                           env=env, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_auto_takes_canbench_from_the_root(self):
        # A decoy canbench earlier on the path must not win: the root goes
        # first, as the walk-up's insert did.
        decoy = os.path.join(self.d, "decoy")
        self.canbench(decoy, "DECOY")
        rc, out = self.run_auto(self.root(canbench=True), pythonpath=decoy)
        self.assertIn("STAND-IN canbench 500000", out)
        self.assertNotIn("DECOY", out)
        self.assertIn("no CAN interface detected", out)
        self.assertNotEqual(rc, 0)

    def test_auto_without_the_setting_refuses_before_canbench(self):
        rc, out = self.run_auto(None)
        self.assertNotEqual(rc, 0)
        self.assertIn("needs REVERSE_IT_DIR", out)
        self.assertNotIn("canbench", out.replace("canbench's", ""))
        self.assertNotIn("ModuleNotFoundError", out)


if __name__ == "__main__":
    unittest.main()
