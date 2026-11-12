"""Implementor self-test of where vehicle_runner.py takes canbench from. Not
evidence.

    py -3.14 -B gen-inhibit/tools/test_vehicle_runner_root.py

--interface auto (the default) finds the dongle with canbench at the
can-tools commit gen-inhibit pins (AGENTS.md, "can-tools", user 2026-10-30):
vehicle_runner.import_canbench() calls can_tools_pin.use(), which loads the
pinned commit's canbench/ by file from scratch/can-tools/<commit>/, written
there out of the CAN_TOOLS_DIR clone by `can_tools_pin.py install`. Refused:
CAN_TOOLS_DIR unset, the pin not installed (the refusal names the install
command), and a canbench already imported from anywhere else. Neither the
installed folder, the clone nor reverse-it goes on sys.path, and neither a
canbench earlier on the path, the clone's HEAD nor reverse-it's mirror
stands in for the pin. An explicit --interface kvaser or gs_usb on Linux
applies the pinned canbench's linux_fixes before can.Bus is called, loaded
the same way, and refuses before the open without the setting or the
install (ExplicitOpen, with a fake can.Bus and a stand-in linux_fixes;
reviewer, 2026-11-12). Every other interface, and kvaser or gs_usb off
Linux, needs no setting, and importing the file needs none either.

The auto path runs in a child process against test_can_tools_pin.py's
stand-in clone and planted worktree, in this run's own folder,
gi_paths.scratch("vehicle-runner-test") under the worktree's scratch/, removed
when the run ends, pass or fail (AGENTS.md "Removing scratch"). The pinned commit's canbench.live.receiver detects
nothing, so the "no CAN interface detected" exit shows canbench came from
the pin. The child's environment is built here, so the result is the same
with CAN_TOOLS_DIR set or unset in the shell. Needs python-can, which the
file imports.
"""

import ast
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vehicle_runner as vr  # noqa: E402,F401
import can_tools_pin as ctp  # noqa: E402
from test_can_tools_pin import (PINNED_FILES, fixture, pointed, rmtree,  # noqa: E402
                                run_folder)

# Points can_tools_pin at the fixture, runs the optional %s preamble, then
# calls Runner.open_and_identify on a stand-in self, as --interface auto does,
# and prints sys.path as it was before the call and as the call left it.
AUTO = ("import sys, types\n"
        "sys.path.insert(0, %r)\n"
        "import can_tools_pin\n"
        "can_tools_pin.PINS_FILE, can_tools_pin.START = %r, %r\n"
        "%s\n"
        "import vehicle_runner as vr\n"
        "me = types.SimpleNamespace(args=types.SimpleNamespace("
        "interface='auto', bitrate=500000))\n"
        "print('BEFORE=' + repr(sys.path))\n"
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
        self.d = os.path.join(run_folder("vehicle-runner-test"),
                              self.id().rsplit(".", 1)[-1])
        rmtree(self.d)
        os.makedirs(self.d)
        self.fx = fixture(os.path.join(self.d, "fx"))

    def install(self):
        with pointed(self.fx):
            return ctp.install()[0]

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

    def run_auto(self, clone, preamble="", **kw):
        p = subprocess.run(
            [sys.executable, "-B", "-c", AUTO % (
                HERE, self.fx["pins"], self.fx["worktree"], preamble)],
            env=child_env(clone, **kw), capture_output=True, text=True)
        out = p.stdout + p.stderr
        paths = []
        for tag in ("BEFORE=", "PATH="):
            got = [ln[len(tag):] for ln in p.stdout.splitlines()
                   if ln.startswith(tag)]
            self.assertEqual(len(got), 1, out)
            paths.append([norm(e) for e in ast.literal_eval(got[0]) if e])
        before, path = paths
        # The call adds nothing to sys.path and takes nothing off it.
        self.assertEqual(path, before, out)
        return p.returncode, out, path

    def test_importing_needs_no_setting(self):
        p = subprocess.run([sys.executable, "-B", "-c",
                            "import sys; sys.path.insert(0, %r); "
                            "import vehicle_runner; print('IMPORTED')" % HERE],
                           env=child_env(None), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("IMPORTED", p.stdout)

    def test_auto_takes_canbench_from_the_pin(self):
        # Neither a canbench earlier on the path (the clone's, as the .pth
        # puts it there), the clone's HEAD nor reverse-it's mirror may win,
        # and run_auto checks the call leaves sys.path as it was: neither the
        # installed folder, the clone nor reverse-it is put on it.
        d = self.install()
        decoy = os.path.join(self.d, "decoy")
        self.canbench(decoy, "DECOY")
        r = self.reverse_it()
        rc, out, path = self.run_auto(self.fx["clone"], reverse_it=r,
                                      pythonpath=os.pathsep.join(
                                          [decoy, self.fx["clone"]]))
        self.assertIn("PINNED canbench 500000", out)
        for word in ("DECOY", "MIRROR", "HEAD canbench"):
            self.assertNotIn(word, out)
        self.assertIn("no CAN interface detected", out)
        self.assertNotEqual(rc, 0)
        for p in (d, os.path.join(d, "canbench"), r):
            self.assertNotIn(norm(p), path)

    def test_auto_without_the_setting_refuses_before_canbench(self):
        self.install()
        rc, out, _ = self.run_auto(None, reverse_it=self.reverse_it())
        self.assertNotEqual(rc, 0)
        self.assertIn("--interface auto needs canbench", out)
        self.assertIn("CAN_TOOLS_DIR is not set", out)
        self.assertIn("--interface", out)
        for word in ("MIRROR", "PINNED canbench", "ModuleNotFoundError",
                     "Traceback"):
            self.assertNotIn(word, out)

    def test_auto_without_the_install_names_the_command(self):
        rc, out, _ = self.run_auto(self.fx["clone"])
        self.assertNotEqual(rc, 0)
        self.assertIn("is not installed", out)
        self.assertIn("can_tools_pin.py install", out)
        self.assertNotIn("PINNED canbench", out)
        self.assertNotIn("HEAD canbench", out)
        self.assertNotIn("Traceback", out)

    def test_a_canbench_imported_from_elsewhere_is_refused(self):
        self.install()
        decoy = os.path.join(self.d, "decoy")
        self.canbench(decoy, "DECOY")
        rc, out, _ = self.run_auto(self.fx["clone"], pythonpath=decoy,
                                   preamble="import canbench")
        self.assertNotEqual(rc, 0)
        self.assertIn("canbench is already imported from", out)
        self.assertIn(os.path.join(decoy, "canbench"), out)
        self.assertNotIn("DECOY canbench", out)
        self.assertNotIn("PINNED canbench", out)

    def test_the_pinned_canbench_imported_already_is_taken(self):
        self.install()
        rc, out, _ = self.run_auto(self.fx["clone"],
                                   preamble="can_tools_pin.use()")
        self.assertIn("PINNED canbench 500000", out)
        self.assertIn("no CAN interface detected", out)
        self.assertNotIn("already imported", out)


# The pinned commit with a stand-in canbench.live.linux_fixes whose apply()
# records that it ran.
FIXES_FILES = dict(PINNED_FILES)
FIXES_FILES["canbench/live/linux_fixes.py"] = (
    "APPLIED = False\n"
    "def apply():\n"
    "    global APPLIED\n"
    "    APPLIED = True\n"
    "    print('PINNED linux_fixes applied')\n"
    "    return {'kvaser': True, 'gs_usb': True}\n")

# Points can_tools_pin at the fixture, replaces can.Bus with a fake that
# prints the interface and whether the pinned linux_fixes had been applied
# at the moment of the open (then stops the run), sets sys.platform, and
# calls Runner.open_and_identify for an explicit --interface.
EXPLICIT = ("import sys, types\n"
            "sys.path.insert(0, %r)\n"
            "import can_tools_pin\n"
            "can_tools_pin.PINS_FILE, can_tools_pin.START = %r, %r\n"
            "import can\n"
            "def fake_bus(**kw):\n"
            "    lf = sys.modules.get('canbench.live.linux_fixes')\n"
            "    print('OPEN %%s fixes=%%s' %% (kw.get('interface'),\n"
            "                                 getattr(lf, 'APPLIED', None)))\n"
            "    raise SystemExit('FAKE BUS')\n"
            "can.Bus = fake_bus\n"
            "import vehicle_runner as vr\n"
            "me = types.SimpleNamespace(args=types.SimpleNamespace("
            "interface=%r, bitrate=500000, channel='0'))\n"
            "sys.platform = %r\n"
            "print('BEFORE=' + repr(sys.path))\n"
            "try:\n"
            "    vr.Runner.open_and_identify(me)\n"
            "finally:\n"
            "    print('PATH=' + repr(sys.path))\n")


class ExplicitOpen(unittest.TestCase):
    """An explicit --interface kvaser or gs_usb opens with python-can's
    can.Bus, which on Linux fails without can-tools' linux_fixes (reviewer,
    2026-11-12). On Linux, vehicle_runner applies the pinned canbench's
    linux_fixes before that open, loaded through can_tools_pin as the auto
    path loads canbench; other interfaces, and kvaser or gs_usb off Linux,
    load nothing and need no setting."""

    def setUp(self):
        self.d = os.path.join(run_folder("vehicle-runner-test"),
                              self.id().rsplit(".", 1)[-1])
        rmtree(self.d)
        os.makedirs(self.d)
        self.fx = fixture(os.path.join(self.d, "fx"), files=FIXES_FILES)

    def install(self):
        with pointed(self.fx):
            return ctp.install()[0]

    def run_explicit(self, interface, platform, clone):
        p = subprocess.run(
            [sys.executable, "-B", "-c", EXPLICIT % (
                HERE, self.fx["pins"], self.fx["worktree"], interface,
                platform)],
            env=child_env(clone), capture_output=True, text=True)
        out = p.stdout + p.stderr
        paths = []
        for tag in ("BEFORE=", "PATH="):
            got = [ln[len(tag):] for ln in p.stdout.splitlines()
                   if ln.startswith(tag)]
            self.assertEqual(len(got), 1, out)
            paths.append([norm(e) for e in ast.literal_eval(got[0]) if e])
        self.assertEqual(paths[1], paths[0], out)   # sys.path left as it was
        return p.returncode, out

    def test_kvaser_and_gs_usb_on_linux_apply_the_fixes_before_the_open(self):
        self.install()
        for iface in ("kvaser", "gs_usb"):
            with self.subTest(iface=iface):
                rc, out = self.run_explicit(iface, "linux", self.fx["clone"])
                self.assertIn("PINNED linux_fixes applied", out)
                self.assertIn("OPEN %s fixes=True" % iface, out)
                self.assertNotIn("fixes=False", out)
                self.assertNotIn("fixes=None", out)
                self.assertLess(out.index("PINNED linux_fixes applied"),
                                out.index("OPEN %s" % iface), out)
                self.assertNotIn("Traceback", out)

    def test_kvaser_on_linux_without_the_setting_refuses_before_the_open(self):
        self.install()
        rc, out = self.run_explicit("kvaser", "linux", None)
        self.assertNotEqual(rc, 0)
        self.assertIn("--interface kvaser on Linux needs canbench", out)
        self.assertIn("CAN_TOOLS_DIR is not set", out)
        self.assertNotIn("OPEN kvaser", out)
        self.assertNotIn("Traceback", out)

    def test_gs_usb_on_linux_without_the_install_names_the_command(self):
        rc, out = self.run_explicit("gs_usb", "linux", self.fx["clone"])
        self.assertNotEqual(rc, 0)
        self.assertIn("can_tools_pin.py install", out)
        self.assertNotIn("OPEN gs_usb", out)
        self.assertNotIn("Traceback", out)

    def test_other_interfaces_on_linux_load_nothing(self):
        for iface in ("pcan", "ixxat"):
            with self.subTest(iface=iface):
                rc, out = self.run_explicit(iface, "linux", None)
                self.assertIn("OPEN %s fixes=None" % iface, out)
                self.assertNotIn("linux_fixes applied", out)
                self.assertNotIn("needs canbench", out)

    def test_kvaser_off_linux_loads_nothing_and_needs_no_setting(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform):
                rc, out = self.run_explicit("kvaser", platform, None)
                self.assertIn("OPEN kvaser fixes=None", out)
                self.assertNotIn("linux_fixes applied", out)
                self.assertNotIn("needs canbench", out)


if __name__ == "__main__":
    unittest.main()
