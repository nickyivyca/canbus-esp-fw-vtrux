"""Tests of the IDF environment check (vtrux_build.idfenv, SPEC R32).

    python -B shared/tools/tests/test_idfenv.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vtrux_build import idfenv                                   # noqa: E402

TOOLS = os.path.abspath(os.path.join(os.sep, "t", "espressif-5.4.2"))
GOOD = {
    "IDF_PATH": os.path.abspath(os.path.join(os.sep, "t", "esp-idf-v5.4.2")),
    "IDF_TOOLS_PATH": TOOLS,
    "IDF_PYTHON_ENV_PATH": os.path.join(TOOLS, "python_env",
                                        "idf5.4_py3.11_env"),
    "ESP_ROM_ELF_DIR": os.path.join(TOOLS, "tools", "esp-rom-elfs",
                                    "20241011", ""),
    "PATH": "p",
}


def env(**change):
    e = dict(GOOD)
    for n, v in change.items():
        if v is None:
            e.pop(n)
        else:
            e[n] = v
    return e


class Check(unittest.TestCase):

    def refused(self, environ, *words):
        with self.assertRaises(idfenv.EnvError) as cm:
            idfenv.check(environ)
        for w in ("R32",) + words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_exported_shell_passes(self):
        self.assertEqual(idfenv.check(GOOD),
                         {n: GOOD[n] for n in idfenv.NAMES})

    def test_each_variable_unset_or_empty(self):
        for name in idfenv.NAMES:
            self.assertIn("not set", self.refused(env(**{name: None}), name))
            self.assertIn("empty", self.refused(env(**{name: ""}), name))

    def test_blank_tools_path(self):
        self.refused(env(IDF_TOOLS_PATH="   "), "IDF_TOOLS_PATH")

    def test_spaces_around_the_tools_path(self):
        """Refused with the plain reason, not passed on unstripped: the value
        checked is the value ESP-IDF gets (the interposer reviewer's nit,
        2026-11-04)."""
        for padded in (" " + TOOLS, TOOLS + " ", "\t" + TOOLS + "\n"):
            why = self.refused(env(IDF_TOOLS_PATH=padded), "IDF_TOOLS_PATH",
                               "leading or trailing spaces")
            self.assertNotIn("not inside", why)

    def test_tools_path_never_defaulted(self):
        why = self.refused(env(IDF_TOOLS_PATH=None), "IDF_TOOLS_PATH")
        self.assertIn("never defaulted", why)

    def test_outside_the_tools_folder(self):
        """The export of another install's tools folder: each of the two
        must be inside the IDF_TOOLS_PATH this shell has."""
        other = os.path.abspath(os.path.join(os.sep, "t", "espressif"))
        for name in idfenv.INSIDE_TOOLS:
            moved = GOOD[name].replace(TOOLS, other)
            self.refused(env(**{name: moved}), name, "not inside")
        self.refused(env(IDF_TOOLS_PATH=other), "IDF_PYTHON_ENV_PATH",
                     "not inside")

    def test_a_sibling_whose_name_starts_like_it_is_outside(self):
        """espressif-5.4.2x is not inside espressif-5.4.2."""
        self.refused(env(IDF_PYTHON_ENV_PATH=TOOLS + "x"),
                     "IDF_PYTHON_ENV_PATH", "not inside")

    def test_relative_parts_resolved(self):
        sneaky = os.path.join(TOOLS, "python_env", os.pardir, os.pardir,
                              "elsewhere")
        self.refused(env(IDF_PYTHON_ENV_PATH=sneaky), "not inside")
        fine = os.path.join(TOOLS, "tools", os.pardir, "python_env", "v")
        idfenv.check(env(IDF_PYTHON_ENV_PATH=fine))

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_windows_case_and_drives(self):
        idfenv.check(env(IDF_PYTHON_ENV_PATH=GOOD["IDF_PYTHON_ENV_PATH"]
                         .upper()))
        self.refused(env(IDF_TOOLS_PATH="C:\\t\\x",
                         IDF_PYTHON_ENV_PATH="D:\\t\\x\\v"),
                     "IDF_PYTHON_ENV_PATH")

    def test_default_is_this_process(self):
        try:
            got = idfenv.check()
        except idfenv.EnvError:
            return
        self.assertEqual(got, {n: os.environ[n] for n in idfenv.NAMES})


class Inside(unittest.TestCase):

    def test_inside(self):
        self.assertTrue(idfenv.inside(TOOLS, TOOLS))
        self.assertTrue(idfenv.inside(os.path.join(TOOLS, "a", "b"), TOOLS))
        self.assertFalse(idfenv.inside(os.path.dirname(TOOLS), TOOLS))
        self.assertFalse(idfenv.inside(TOOLS + "-x", TOOLS))


if __name__ == "__main__":
    unittest.main()
