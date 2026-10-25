"""R18 options check: each rule shown failing on a planted input and passing on a
faithful one. Runs alone: python -B shared/tools/vtrux_checks/tests/test_options.py
"""

import contextlib
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
from vtrux_checks import options as O  # noqa: E402
from vtrux_checks import cli  # noqa: E402
from test_scope import scratch  # noqa: E402

PROFILE = {"CONFIG_ON": (True, "spec 1"), "CONFIG_OFF": (O.UNSET, "spec 2"),
           "CONFIG_NUM": (1000, "spec 3"), "CONFIG_STR": ("a", "spec 4")}
GOOD = {"ON": True, "OFF": False, "NUM": 1000, "STR": "a", "FREE": 7}


def opts(**kw):
    o = dict(GOOD)
    for k, v in kw.items():
        if v is O.ABSENT:
            o.pop(k, None)
        else:
            o[k] = v
    return o


class Judge(unittest.TestCase):
    def test_faithful_passes(self):
        self.assertEqual(O.judge(PROFILE, {"build": GOOD, "build/bootloader": GOOD}), [])

    def test_unset_absent_or_off(self):
        self.assertEqual(O.judge(PROFILE, {"build": opts(OFF=O.ABSENT)}), [])
        self.assertEqual(O.judge(PROFILE, {"build": opts(OFF=False)}), [])

    def test_free_options_ignored(self):
        self.assertEqual(O.judge(PROFILE, {"build": opts(FREE=8, OTHER=True)}), [])
        self.assertEqual(O.judge(PROFILE, {"build": opts(FREE=O.ABSENT)}), [])

    def test_each_mismatch(self):
        for kw, name in (({"ON": False}, "CONFIG_ON"), ({"ON": O.ABSENT}, "CONFIG_ON"),
                         ({"ON": 1}, "CONFIG_ON"),                 # true is not 1
                         ({"OFF": True}, "CONFIG_OFF"), ({"OFF": 0}, "CONFIG_OFF"), ({"OFF": ""}, "CONFIG_OFF"),
                         ({"NUM": 999}, "CONFIG_NUM"), ({"NUM": "1000"}, "CONFIG_NUM"),
                         ({"NUM": True}, "CONFIG_NUM"), ({"NUM": O.ABSENT}, "CONFIG_NUM"),
                         ({"STR": "b"}, "CONFIG_STR"), ({"STR": O.ABSENT}, "CONFIG_STR")):
            got = O.judge(PROFILE, {"build": opts(**kw)})
            self.assertEqual([(f, n) for f, n, _, _ in got], [("build", name)], kw)

    def test_bootloader_folder_alone(self):
        got = O.judge(PROFILE, {"build": GOOD, "build/bootloader": opts(ON=False)})
        self.assertEqual(got, [("build/bootloader", "CONFIG_ON", True, False)])

    def test_every_folder_reported(self):
        got = O.judge(PROFILE, {"build": opts(OFF=True), "build/bootloader": opts(OFF=True)})
        self.assertEqual(sorted(f for f, _, _, _ in got), ["build", "build/bootloader"])


class Profile(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def put(self, data):
        with open(os.path.join(self.d, O.DATA_FILE), "w", newline="") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_reads(self):
        self.put({"release_profile": {"CONFIG_A": {"value": True, "spec": "s 1"},
                                      "CONFIG_B": {"value": "unset", "spec": "s 2"},
                                      "CONFIG_C": {"value": 3, "spec": "s 3"},
                                      "CONFIG_D": {"value": "x", "spec": "s 4"}},
                  "other_check": {}})
        self.assertEqual(O.read_profile(self.d), {"CONFIG_A": (True, "s 1"), "CONFIG_B": ("unset", "s 2"),
                                                  "CONFIG_C": (3, "s 3"), "CONFIG_D": ("x", "s 4")})

    def test_refused(self):
        for data in ("{not json", [], {}, {"release_profile": {}}, {"release_profile": []},
                     {"release_profile": {"A": {"value": True, "spec": "s"}}},
                     {"release_profile": {"CONFIG_a": {"value": True, "spec": "s"}}},
                     {"release_profile": {"CONFIG_A": True}},
                     {"release_profile": {"CONFIG_A": {"value": True}}},
                     {"release_profile": {"CONFIG_A": {"vaule": True, "spec": "s"}}},
                     {"release_profile": {"CONFIG_A": {"value": True, "spec": "s", "note": "x"}}},
                     {"release_profile": {"CONFIG_A": {"value": True, "spec": ""}}},
                     {"release_profile": {"CONFIG_A": {"value": True, "spec": "  "}}},
                     {"release_profile": {"CONFIG_A": {"value": True, "spec": 1}}},
                     {"release_profile": {"CONFIG_A": {"value": False, "spec": "s"}}},
                     {"release_profile": {"CONFIG_A": {"value": None, "spec": "s"}}},
                     {"release_profile": {"CONFIG_A": {"value": 1.5, "spec": "s"}}},
                     {"release_profile": {"CONFIG_A": {"value": [1], "spec": "s"}}},
                     {"release_profile": {"CONFIG_A": {"value": True, "spec": "s"},
                                          "CONFIG_B": {"value": False, "spec": "s"}}}):
            self.put(data)
            with self.assertRaises(O.OptionsError, msg=data):
                O.read_profile(self.d)

    def test_false_says_unset(self):
        self.put({"release_profile": {"CONFIG_A": {"value": False, "spec": "s"}}})
        with self.assertRaisesRegex(O.OptionsError, 'CONFIG_A: false; write "unset"'):
            O.read_profile(self.d)

    def test_missing(self):
        with self.assertRaises(O.OptionsError):
            O.read_profile(os.path.join(self.d, "none"))


class Build(unittest.TestCase):
    """collect() and the command line on a small build tree: build/ and build/bootloader/."""

    def setUp(self):
        self.d = scratch()
        self.b = os.path.join(self.d, "build")
        self.write("build/project_description.json", {"project_path": self.d})
        self.write("build/config/sdkconfig.json", GOOD)
        self.write("build/bootloader/CMakeCache.txt", "")
        self.write("build/bootloader/build.ninja", "")
        self.write("build/bootloader/config/sdkconfig.json", GOOD)
        self.write(O.DATA_FILE, {"release_profile": {n: {"value": v, "spec": s} for n, (v, s) in PROFILE.items()}})

    def write(self, rel, data):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def run_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["options", "--build", self.b])
        return rc, buf.getvalue()

    def test_collect(self):
        project, got = O.collect(self.b)
        self.assertEqual(os.path.normcase(project), os.path.normcase(self.d))
        self.assertEqual(sorted(got), ["build", "build/bootloader"])

    def test_cli_passes(self):
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS R18", out)

    def test_cli_fails_on_bootloader(self):
        self.write("build/bootloader/config/sdkconfig.json", opts(ON=False))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build/bootloader: CONFIG_ON is false, the profile says true (spec 1)", out)
        self.assertNotIn("PASS R18", out)

    def test_cli_fails_on_app_unset(self):
        self.write("build/config/sdkconfig.json", opts(OFF=True))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build: CONFIG_OFF is true, the profile says unset (spec 2)", out)

    def test_cli_missing_options(self):
        os.remove(os.path.join(self.b, "bootloader", "config", "sdkconfig.json"))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL R18: cannot read", out)
        self.assertIn("build/bootloader", out)

    def test_cli_missing_profile(self):
        os.remove(os.path.join(self.d, O.DATA_FILE))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn(O.DATA_FILE, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
