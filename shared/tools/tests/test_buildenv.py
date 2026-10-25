"""Tests of a release build's environment (vtrux_build.buildenv, SPEC R24).

    python -B shared/tools/tests/test_buildenv.py

RealConfigure runs ESP-IDF itself: two configures of a small project, about
40 s. It is a build for AGENTS.md "Builds during wall-clock tests", so it
runs only when asked: VTRUX_IDF_TESTS=1, from an exported ESP-IDF shell.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import scratch                                                   # noqa: E402
from vtrux_build import buildenv                                 # noqa: E402
from vtrux_build.builddir import BuildDir                        # noqa: E402
from vtrux_build.release import COMPILE_RULE, idf_runner         # noqa: E402


class Environment(unittest.TestCase):

    def test_windows(self):
        """Only the pass list, matched case-insensitively as Windows does;
        every other name dropped; the tooling's settings added."""
        caller = {"Path": "p", "SystemRoot": "s", "TEMP": "t", "TMP": "t",
                  "PROCESSOR_ARCHITECTURE": "AMD64", "IDF_PATH": "i",
                  "IDF_PYTHON_ENV_PATH": "v", "IDF_TOOLS_PATH": "x",
                  "ESP_ROM_ELF_DIR": "r",
                  "EXTRA_CFLAGS": "-DX", "CFLAGS": "-DY", "IDF_TARGET": "esp32",
                  "IDF_CCACHE_ENABLE": "1", "PYTHONPATH": "q",
                  "IDF_COMPONENT_STORAGE_URL": "u", "USERPROFILE": "h"}
        env, passed, dropped = buildenv.environment(caller, "nt")
        self.assertEqual(passed, ["ESP_ROM_ELF_DIR", "IDF_PATH",
                                  "IDF_PYTHON_ENV_PATH", "IDF_TOOLS_PATH",
                                  "Path", "PROCESSOR_ARCHITECTURE",
                                  "SystemRoot", "TEMP", "TMP"])
        self.assertEqual(dropped, ["CFLAGS", "EXTRA_CFLAGS",
                                   "IDF_CCACHE_ENABLE",
                                   "IDF_COMPONENT_STORAGE_URL", "IDF_TARGET",
                                   "PYTHONPATH", "USERPROFILE"])
        want = {n: caller[n] for n in passed}
        want.update({"IDF_COMPONENT_STRICT_CHECKSUM": "1",
                     "SOURCE_DATE_EPOCH": buildenv.EPOCH})
        self.assertEqual(env, want)

    def test_posix_is_case_sensitive(self):
        caller = {"PATH": "p", "path": "q", "IDF_PATH": "i", "TMPDIR": "t",
                  "HOME": "h", "LANG": "C"}
        env, passed, dropped = buildenv.environment(
            caller, "posix", default_tools_path="/x")
        self.assertEqual(passed, ["IDF_PATH", "PATH", "TMPDIR"])
        self.assertEqual(dropped, ["HOME", "LANG", "path"])
        self.assertEqual(env["IDF_TOOLS_PATH"], "/x")

    def test_tooling_settings_win(self):
        """The caller's own values for the tooling's settings never reach
        the build: they are not on the list, and the tooling sets its own."""
        caller = {"PATH": "p", "SOURCE_DATE_EPOCH": "5",
                  "IDF_COMPONENT_STRICT_CHECKSUM": "0"}
        env, passed, dropped = buildenv.environment(
            caller, "posix", default_tools_path="/x")
        self.assertEqual(env["SOURCE_DATE_EPOCH"], buildenv.EPOCH)
        self.assertEqual(env["IDF_COMPONENT_STRICT_CHECKSUM"], "1")
        self.assertEqual(dropped, ["IDF_COMPONENT_STRICT_CHECKSUM",
                                   "SOURCE_DATE_EPOCH"])

    def test_epoch_is_one_fixed_value(self):
        self.assertEqual(buildenv.EPOCH, "1767225600")
        a = buildenv.environment({}, "posix", default_tools_path="/x")[0]
        b = buildenv.environment({"PATH": "other"}, "nt",
                                 default_tools_path="C:/x")[0]
        self.assertEqual(a["SOURCE_DATE_EPOCH"], b["SOURCE_DATE_EPOCH"])

    def test_tools_path(self):
        """The caller's IDF_TOOLS_PATH passes; unset or empty, the default
        is set, so the build looks where R9's toolchain check looked."""
        for caller, want in (({"IDF_TOOLS_PATH": "/mine"}, "/mine"),
                             ({"IDF_TOOLS_PATH": ""}, "/default"),
                             ({}, "/default")):
            env = buildenv.environment(caller, "posix",
                                       default_tools_path="/default")[0]
            self.assertEqual(env["IDF_TOOLS_PATH"], want, caller)
            self.assertEqual([n for n in env if n == "IDF_TOOLS_PATH"],
                             ["IDF_TOOLS_PATH"])
        env = buildenv.environment({"idf_tools_path": ""}, "nt",
                                   default_tools_path="C:/d")[0]
        self.assertEqual({n: v for n, v in env.items()
                          if n.upper() == "IDF_TOOLS_PATH"},
                         {"IDF_TOOLS_PATH": "C:/d"})
        env = buildenv.environment({}, "posix")[0]
        self.assertEqual(env["IDF_TOOLS_PATH"], os.path.join(
            os.path.expanduser("~"), ".espressif"))

    def test_names_only(self):
        """passed and dropped hold names, never values."""
        caller = {"PATH": "secret-path", "EXTRA_CFLAGS": "secret-flag"}
        _, passed, dropped = buildenv.environment(caller, "posix",
                                                  default_tools_path="/x")
        self.assertNotIn("secret", " ".join(passed + dropped))

    def test_default_is_this_process(self):
        env, passed, dropped = buildenv.environment()
        self.assertEqual(len(passed) + len(dropped), len(os.environ))


PROJECT = {
    "CMakeLists.txt": ("cmake_minimum_required(VERSION 3.16)\n"
                       "set(COMPONENTS main)\n"
                       "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
                       "project(r24)\n"),
    "main/CMakeLists.txt": 'idf_component_register(SRCS "m.c")\n',
    "main/m.c": "void app_main(void) {}\n",
    "sdkconfig.defaults": 'CONFIG_IDF_TARGET="esp32c3"\n',
}
LEAK = "VTRUX_R24_LEAK"


@unittest.skipUnless(os.environ.get("VTRUX_IDF_TESTS") == "1"
                     and os.environ.get("IDF_PATH")
                     and os.environ.get("IDF_PYTHON_ENV_PATH"),
                     "runs ESP-IDF: set VTRUX_IDF_TESTS=1 in an exported shell")
class RealConfigure(unittest.TestCase):
    """R24 against ESP-IDF itself: EXTRA_CFLAGS in the caller's environment
    reaches every compile command of a configure given that environment
    (the control, showing the route is real), and none of a configure
    given the release build's."""

    def compile_commands(self, build_dir):
        cmds = [e.command for e in BuildDir(build_dir).ninja_edges()
                if COMPILE_RULE.match(e.rule) and e.command]
        self.assertTrue(cmds, "no compile commands in %s" % build_dir)
        return cmds

    def test_extra_cflags_does_not_reach_the_compile(self):
        root = scratch.fresh("buildenv_real")
        proj = os.path.join(root, "proj")
        for rel, text in PROJECT.items():
            scratch.write(os.path.join(proj, *rel.split("/")), text)
        caller = dict(os.environ, EXTRA_CFLAGS="-D%s=1" % LEAK)
        run = idf_runner(proj)
        control = os.path.join(root, "build-caller")
        self.assertEqual(run(["-B", control, "--no-ccache", "reconfigure"],
                             caller), 0)
        self.assertTrue(any(LEAK in c for c in
                            self.compile_commands(control)))
        env, passed, dropped = buildenv.environment(caller)
        self.assertIn("EXTRA_CFLAGS", dropped)
        release = os.path.join(root, "build-release")
        self.assertEqual(run(["-B", release, "--no-ccache", "reconfigure"],
                             env), 0)
        self.assertFalse([c for c in self.compile_commands(release)
                          if LEAK in c])


if __name__ == "__main__":
    unittest.main()
