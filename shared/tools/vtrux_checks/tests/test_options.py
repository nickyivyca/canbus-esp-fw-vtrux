"""R18, R26-R29 options check: each rule shown failing on a planted input and passing
on a faithful one. Runs alone: python -B shared/tools/vtrux_checks/tests/test_options.py
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
import boardkit as K  # noqa: E402

PROFILE = {"CONFIG_ON": (True, "spec 1"), "CONFIG_OFF": (O.UNSET, "spec 2"),
           "CONFIG_NUM": (1000, "spec 3"), "CONFIG_STR": ("a", "spec 4"),
           "CONFIG_FREERTOS_HZ": (1000, "spec 5"), "CONFIG_REQ": (True, "spec 6"), "CONFIG_CON_B": (True, "spec 7")}
# R26 and R28 as every release build has them; SECURE_BOOT absent, EFUSE_VIRTUAL off.
COMMON = {"SECURE_FLASH_ENC_ENABLED": False, "FLASH_ENCRYPTION_ENABLED": False, "EFUSE_VIRTUAL": False,
          "LOG_DEFAULT_LEVEL": 0, "LOG_MAXIMUM_LEVEL": 0, "BOOTLOADER_LOG_LEVEL": 1}
GOOD = dict({"ON": True, "OFF": False, "NUM": 1000, "STR": "a", "FREERTOS_HZ": 1000, "FREE": 7}, **COMMON)
GOOD.update(K.FIXED, **K.REQ_BUILD)
APP = dict(GOOD, APP_ONLY=True)     # the app's folder: the board's options its components alone define


def opts(**kw):
    o = dict(GOOD)
    for k, v in kw.items():
        if v is O.ABSENT:
            o.pop(k, None)
        else:
            o[k] = v
    return o


def app(**kw):
    """opts() for the app's folder, which also has the board's APP_ONLY."""
    return dict(opts(**kw), APP_ONLY=True)


REQUIRED = [{"setting": "a single option", "kind": "option", "options": ["CONFIG_REQ"]},
            {"setting": "a choice", "kind": "choice", "options": ["CONFIG_CON_A", "CONFIG_CON_B", "CONFIG_CON_C"]}]
FIXED = {"CONFIG_FIXED_ON": True, "CONFIG_FIXED_HEX": 0x1000, "CONFIG_FIXED_STR": 'a "b"',
         "CONFIG_FIXED_OFF": O.UNSET, "CONFIG_APP_ONLY": True}


class BoardSettings(unittest.TestCase):
    """R29: judge_fixed() and judge_required() on options as read."""

    def both(self, a=None, b=None):
        return {"build": a if a is not None else APP, "build/bootloader": b if b is not None else GOOD}

    def test_faithful(self):
        self.assertEqual(O.judge_fixed(FIXED, self.both(), "build"), [])
        self.assertEqual(O.judge_required(REQUIRED, PROFILE, self.both(), "build"), [])

    def test_fixed_wrong_in_app(self):
        for name, wrong in (("FIXED_ON", False), ("FIXED_HEX", 0x2000), ("FIXED_HEX", "0x1000"), ("FIXED_STR", "a b"),
                            ("FIXED_OFF", True), ("FIXED_ON", O.ABSENT), ("APP_ONLY", O.ABSENT)):
            a = opts(**{name: wrong}) if name == "APP_ONLY" else app(**{name: wrong})
            got = O.judge_fixed(FIXED, self.both(a=a), "build")
            self.assertEqual([(f, n) for f, n, _w, _g in got], [("build", "CONFIG_" + name)], (name, wrong))

    def test_fixed_off_absent_in_app_passes(self):
        self.assertEqual(O.judge_fixed(FIXED, self.both(a=app(FIXED_OFF=O.ABSENT)), "build"), [])

    def test_fixed_absent_elsewhere_not_judged(self):
        self.assertNotIn("APP_ONLY", GOOD)
        self.assertEqual(O.judge_fixed(FIXED, self.both(b=opts(FIXED_ON=O.ABSENT)), "build"), [])

    def test_fixed_wrong_elsewhere(self):
        got = O.judge_fixed(FIXED, self.both(b=opts(FIXED_HEX=4)), "build")
        self.assertEqual(got, [("build/bootloader", "CONFIG_FIXED_HEX", 0x1000, 4)])

    def test_app_is_the_named_folder(self):
        got = O.judge_fixed(FIXED, self.both(), "build/bootloader")
        self.assertEqual([(f, n) for f, n, _w, _g in got], [("build/bootloader", "CONFIG_APP_ONLY")])

    def test_required_option_not_named(self):
        prof = {k: v for k, v in PROFILE.items() if k != "CONFIG_REQ"}
        self.assertEqual(O.judge_required(REQUIRED, prof, self.both(), "build"),
                         ["a single option: the profile does not name CONFIG_REQ"])

    def test_required_option_named_unset(self):
        prof = dict(PROFILE, CONFIG_REQ=(O.UNSET, "s"))
        self.assertEqual(O.judge_required(REQUIRED, prof, self.both(), "build"), [])

    def test_choice_named_by_none_or_two(self):
        none = {k: v for k, v in PROFILE.items() if k != "CONFIG_CON_B"}
        unset = dict(none, CONFIG_CON_A=(O.UNSET, "s"))
        two = dict(PROFILE, CONFIG_CON_C=(True, "s"))
        for label, prof, n in (("none", none, 0), ("only unset", unset, 0), ("two", two, 2)):
            got = O.judge_required(REQUIRED, prof, self.both(), "build")
            self.assertEqual(len(got), 1, label)
            self.assertIn("sets %d of its choice's options true" % n, got[0], label)

    def test_choice_with_unset_members_passes(self):
        prof = dict(PROFILE, CONFIG_CON_A=(O.UNSET, "s"), CONFIG_CON_C=(O.UNSET, "s"))
        self.assertEqual(O.judge_required(REQUIRED, prof, self.both(), "build"), [])

    def test_choice_in_a_folder(self):
        for label, folder_opts, n in (("two true", opts(CON_A=True), 2), ("none true", opts(CON_B=False), 0)):
            got = O.judge_required(REQUIRED, PROFILE, self.both(b=folder_opts), "build")
            self.assertEqual(got, ["a choice: build/bootloader has %d of its choice's options true (%s)" % (
                n, "CONFIG_CON_A, CONFIG_CON_B" if n else "none")], label)

    def test_choice_absent_from_a_folder_not_judged(self):
        b = opts(CON_A=O.ABSENT, CON_B=O.ABSENT, CON_C=O.ABSENT)
        self.assertEqual(O.judge_required(REQUIRED, PROFILE, self.both(b=b), "build"), [])

    def test_required_option_missing_from_app(self):
        got = O.judge_required(REQUIRED, PROFILE, self.both(a=app(REQ=O.ABSENT)), "build")
        self.assertEqual(got, ["a single option: CONFIG_REQ is not in the app's build folder's options"])

    def test_choice_member_absent_not_judged(self):
        """A member whose Kconfig dependencies are not met is not written, in any folder."""
        a, b = app(CON_C=O.ABSENT), opts(CON_C=O.ABSENT)
        self.assertEqual(O.judge_required(REQUIRED, PROFILE, self.both(a=a, b=b), "build"), [])

    def test_choice_absent_from_app(self):
        a = app(CON_A=O.ABSENT, CON_B=O.ABSENT, CON_C=O.ABSENT)
        got = O.judge_required(REQUIRED, PROFILE, self.both(a=a), "build")
        self.assertEqual(got, ["a choice: none of its choice's options is in the app's build folder's options "
                               "(CONFIG_CON_A, CONFIG_CON_B, CONFIG_CON_C)"])

    def test_choice_member_on_absent_from_app(self):
        """The member the profile sets on missing from the app's folder: none there is on."""
        got = O.judge_required(REQUIRED, PROFILE, self.both(a=app(CON_B=O.ABSENT)), "build")
        self.assertEqual(got, ["a choice: build has 0 of its choice's options true (none)"])


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

    def test_absent_elsewhere_not_judged(self):
        b = opts(ON=O.ABSENT, NUM=O.ABSENT, STR=O.ABSENT, OFF=O.ABSENT)
        self.assertEqual(O.judge(PROFILE, {"build": GOOD, "build/bootloader": b}, "build"), [])

    def test_absent_from_app_fails(self):
        for name in ("ON", "NUM", "STR"):
            got = O.judge(PROFILE, {"build": opts(**{name: O.ABSENT}), "build/bootloader": GOOD}, "build")
            self.assertEqual(got, [("build", "CONFIG_" + name, PROFILE["CONFIG_" + name][0], O.ABSENT)], name)

    def test_present_elsewhere_still_judged(self):
        got = O.judge(PROFILE, {"build": GOOD, "build/bootloader": opts(ON=False, OFF=True)}, "build")
        self.assertEqual(got, [("build/bootloader", "CONFIG_OFF", O.UNSET, True),
                               ("build/bootloader", "CONFIG_ON", True, False)])

    def test_app_is_the_named_folder(self):
        got = O.judge(PROFILE, {"build": GOOD, "build/bootloader": opts(ON=O.ABSENT)}, "build/bootloader")
        self.assertEqual(got, [("build/bootloader", "CONFIG_ON", True, O.ABSENT)])


class Common(unittest.TestCase):
    """R26 and R28's fixed options, whatever the profile names."""

    def test_faithful(self):
        self.assertEqual(O.judge(O.R26, {"build": GOOD, "build/bootloader": GOOD}), [])
        self.assertEqual(O.judge(O.R28, {"build": GOOD, "build/bootloader": GOOD}), [])

    def test_r26_each_on(self):
        for name in ("SECURE_BOOT", "SECURE_FLASH_ENC_ENABLED", "FLASH_ENCRYPTION_ENABLED", "EFUSE_VIRTUAL"):
            got = O.judge(O.R26, {"build": GOOD, "build/bootloader": opts(**{name: True})})
            self.assertEqual(got, [("build/bootloader", "CONFIG_" + name, O.UNSET, True)], name)

    def test_r28_each_level(self):
        for name, wrong in (("LOG_DEFAULT_LEVEL", 3), ("LOG_MAXIMUM_LEVEL", 3), ("BOOTLOADER_LOG_LEVEL", 0),
                            ("LOG_DEFAULT_LEVEL", O.ABSENT), ("BOOTLOADER_LOG_LEVEL", "1"),
                            ("LOG_DEFAULT_LEVEL", False)):
            got = O.judge(O.R28, {"build": opts(**{name: wrong}), "build/bootloader": GOOD})
            self.assertEqual([(f, n) for f, n, _, _ in got], [("build", "CONFIG_" + name)], (name, wrong))

    def test_tick(self):
        self.assertIsNone(O.judge_tick(PROFILE))
        p = dict(PROFILE)
        del p["CONFIG_FREERTOS_HZ"]
        self.assertIn("does not name CONFIG_FREERTOS_HZ", O.judge_tick(p))


class Arduino(unittest.TestCase):
    """R28's Arduino question: the Kconfig and the components must agree (ruling (c))."""

    def test_neither(self):
        self.assertEqual(O.arduino({"build": GOOD}, {"build": ["main", "freertos"]}), (False, None))

    def test_both(self):
        self.assertEqual(O.arduino({"build": opts(ARDUHAL_LOG_DEFAULT_LEVEL=0)}, {"build": ["main", "arduino"]}),
                         (True, None))

    def test_kconfig_only(self):
        compiles, why = O.arduino({"build": opts(ARDUHAL_LOG_DEFAULT_LEVEL=0)}, {"build": ["main"]})
        self.assertTrue(compiles)
        self.assertIn("no Arduino component is built", why)
        self.assertIn("build", why)

    def test_component_only(self):
        compiles, why = O.arduino({"build": GOOD}, {"build": ["main", "espressif__arduino-esp32"]})
        self.assertTrue(compiles)
        self.assertIn("espressif__arduino-esp32", why)
        self.assertIn("CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL", why)

    def test_component_names(self):
        for c in ("arduino", "arduino-esp32", "arduino_esp32", "espressif__arduino-esp32", "Arduino"):
            self.assertTrue(O._ARDUINO_COMPONENT.match(c), c)
        for c in ("myarduino", "arduino_ish", "arduino-esp32-libs", "esp_arduino", "main"):
            self.assertFalse(O._ARDUINO_COMPONENT.match(c), c)

    def test_level(self):
        self.assertEqual(O.judge_arduino({"build": opts(ARDUHAL_LOG_DEFAULT_LEVEL=0), "build/bootloader": GOOD}), [])
        self.assertEqual(O.judge_arduino({"build": opts(ARDUHAL_LOG_DEFAULT_LEVEL=3), "build/bootloader": GOOD}),
                         [("build", "CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL", 0, 3)])


class Defines(unittest.TestCase):
    """R28: no -DCORE_DEBUG_LEVEL / -DLOG_LOCAL_LEVEL in a compile command (ruling (a))."""

    def cmd(self, c, f="a.c"):
        return {"directory": "/b", "file": f, "command": c}

    def test_none(self):
        self.assertEqual(O.log_defines({"build": [self.cmd("gcc -DESP_PLATFORM -DLOG_COLORS=0 -c a.c")]}), [])

    def test_found(self):
        got = O.log_defines({"build": [self.cmd("gcc -DLOG_LOCAL_LEVEL=3 -c a.c"),
                                       self.cmd("gcc -DLOG_LOCAL_LEVEL -c b.c", "b.c")],
                             "build/bootloader": [{"file": "c.c", "arguments": ["gcc", "-D", "CORE_DEBUG_LEVEL=0"]}]})
        self.assertEqual(got, [("build", "LOG_LOCAL_LEVEL", ["a.c", "b.c"]),
                               ("build/bootloader", "CORE_DEBUG_LEVEL", ["c.c"])])

    def test_quoted(self):
        self.assertEqual(O.log_defines({"build": [self.cmd('gcc "-DCORE_DEBUG_LEVEL=5" -c a.c')]}),
                         [("build", "CORE_DEBUG_LEVEL", ["a.c"])])

    def test_lookalikes(self):
        for c in ("gcc -DLOG_LOCAL_LEVEL_X=1", "gcc -DXLOG_LOCAL_LEVEL=1", "gcc -DMY_CORE_DEBUG_LEVEL=1",
                  "gcc -UCORE_DEBUG_LEVEL", "gcc -I/x/LOG_LOCAL_LEVEL"):
            self.assertEqual(O.log_defines({"build": [self.cmd(c)]}), [], c)


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

    def test_agreeing_with_r26_r28_reads(self):
        self.put({"release_profile": {"CONFIG_SECURE_BOOT": {"value": "unset", "spec": "s"},
                                      "CONFIG_LOG_DEFAULT_LEVEL": {"value": 0, "spec": "s"},
                                      "CONFIG_BOOTLOADER_LOG_LEVEL": {"value": 1, "spec": "s"},
                                      "CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL": {"value": 0, "spec": "s"},
                                      "CONFIG_FREERTOS_HZ": {"value": 100, "spec": "s"}}})
        self.assertEqual(len(O.read_profile(self.d)), 5)

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

    def test_contradictions_refused(self):
        for name, value, rule in (("CONFIG_SECURE_BOOT", True, "R26"), ("CONFIG_EFUSE_VIRTUAL", True, "R26"),
                                  ("CONFIG_FLASH_ENCRYPTION_ENABLED", "y", "R26"),
                                  ("CONFIG_LOG_DEFAULT_LEVEL", 3, "R28"), ("CONFIG_LOG_MAXIMUM_LEVEL", "0", "R28"),
                                  ("CONFIG_BOOTLOADER_LOG_LEVEL", 0, "R28"), ("CONFIG_LOG_DEFAULT_LEVEL", "unset", "R28"),
                                  ("CONFIG_BOOTLOADER_LOG_LEVEL", True, "R28"),        # true is not 1
                                  ("CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL", 2, "R28"),
                                  ("CONFIG_FREERTOS_HZ", "1000", "R27"), ("CONFIG_FREERTOS_HZ", True, "R27"),
                                  ("CONFIG_FREERTOS_HZ", "unset", "R27")):
            self.put({"release_profile": {name: {"value": value, "spec": "s"}}})
            with self.assertRaisesRegex(O.OptionsError, "%s: .*%s" % (name, rule), msg=(name, value)):
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
        self.desc("build", ["main", "freertos"])
        self.write("build/config/sdkconfig.json", APP)
        self.write("build/compile_commands.json", [{"directory": "/b", "file": "main.c", "command": "gcc -c main.c"}])
        self.write("build/bootloader/CMakeCache.txt", "")
        self.write("build/bootloader/build.ninja", "")
        self.desc("build/bootloader", ["bootloader", "log"])
        self.write("build/bootloader/config/sdkconfig.json", GOOD)
        self.write("build/bootloader/compile_commands.json",
                   [{"directory": "/b", "file": "boot.c", "command": "gcc -c boot.c"}])
        self.profile(PROFILE)
        K.board(self.d)

    def profile(self, prof):
        self.write(O.DATA_FILE, {"release_profile": {n: {"value": v, "spec": s} for n, (v, s) in prof.items()}})

    def desc(self, folder, comps):
        self.write(folder + "/project_description.json",
                   {"project_path": self.d, "build_components": comps + [""],
                    "build_component_info": {c: {"dir": os.path.join(self.d, "c", c)} for c in comps}})

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
        project, got, comps, cmds = O.collect(self.b)
        self.assertEqual(os.path.normcase(project), os.path.normcase(self.d))
        self.assertEqual(sorted(got), ["build", "build/bootloader"])
        self.assertEqual(comps["build"], ["freertos", "main"])
        self.assertEqual(len(cmds["build/bootloader"]), 1)

    def test_cli_passes(self):
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        for rule in ("PASS R18", "PASS R26", "PASS R28", "PASS R27: CONFIG_FREERTOS_HZ 1000 (spec 5)", "PASS R29"):
            self.assertIn(rule, out)

    def test_cli_fails_on_bootloader(self):
        self.write("build/bootloader/config/sdkconfig.json", opts(ON=False))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build/bootloader: CONFIG_ON is false, the profile says true (spec 1)", out)
        self.assertNotIn("PASS R18", out)

    def test_cli_absent_from_bootloader(self):
        self.write("build/bootloader/config/sdkconfig.json", opts(ON=O.ABSENT, NUM=O.ABSENT))
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS R18", out)
        self.write("build/config/sdkconfig.json", app(ON=O.ABSENT))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build: CONFIG_ON is absent, the profile says true (spec 1)", out)

    def test_cli_r26_r28_absent_from_bootloader(self):
        self.write("build/bootloader/config/sdkconfig.json", opts(BOOTLOADER_LOG_LEVEL=O.ABSENT))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build/bootloader: CONFIG_BOOTLOADER_LOG_LEVEL is absent, R28 says 1", out)

    def test_cli_fails_on_app_unset(self):
        self.write("build/config/sdkconfig.json", app(OFF=True))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build: CONFIG_OFF is true, the profile says unset (spec 2)", out)

    def test_cli_r26(self):
        self.write("build/config/sdkconfig.json", app(SECURE_BOOT=True))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL R26", out)
        self.assertIn("build: CONFIG_SECURE_BOOT is true, R26 says unset", out)
        self.assertIn("PASS R18", out)
        self.assertIn("PASS R28", out)

    def test_cli_r28_level(self):
        self.write("build/bootloader/config/sdkconfig.json", opts(BOOTLOADER_LOG_LEVEL=3))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL R28", out)
        self.assertIn("build/bootloader: CONFIG_BOOTLOADER_LOG_LEVEL is 3, R28 says 1", out)
        self.assertIn("PASS R26", out)

    def test_cli_r28_define(self):
        self.write("build/bootloader/compile_commands.json",
                   [{"directory": "/b", "file": "boot.c", "command": "gcc -DLOG_LOCAL_LEVEL=5 -c boot.c"}])
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build/bootloader: -DLOG_LOCAL_LEVEL in 1 compile command(s), e.g. boot.c", out)

    def test_cli_r28_arduino(self):
        self.desc("build", ["main", "arduino"])
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("the Arduino core: an Arduino component is built (build: arduino)", out)
        self.write("build/config/sdkconfig.json", app(ARDUHAL_LOG_DEFAULT_LEVEL=0))
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        self.assertIn("Arduino core compiled: yes", out)
        self.write("build/config/sdkconfig.json", app(ARDUHAL_LOG_DEFAULT_LEVEL=3))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build: CONFIG_ARDUHAL_LOG_DEFAULT_LEVEL is 3, R28 says 0", out)

    def test_cli_r29_fixed(self):
        self.write("build/config/sdkconfig.json", app(FIXED_HEX=0x2000))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build: CONFIG_FIXED_HEX is 8192, the board's sdkconfig.board says 4096", out)
        self.assertNotIn("PASS R29", out)
        for rule in ("PASS R18", "PASS R26", "PASS R28", "PASS R27"):
            self.assertIn(rule, out)

    def test_cli_r29_required(self):
        self.profile({k: v for k, v in PROFILE.items() if k != "CONFIG_REQ"})
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("a single option: the profile does not name CONFIG_REQ", out)
        self.assertIn("names none of CONFIG_REQ", out)

    def test_cli_r29_no_board(self):
        K.lock(self.d, {})
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL R29: cannot read the board: dependencies.lock has 0 shared/boards/* components", out)
        self.assertIn("PASS R18", out)

    def test_cli_r29_board_files(self):
        for f in ("board.json", "sdkconfig.board"):
            p = os.path.join(K.folder(self.d), f)
            os.rename(p, p + ".aside")
            try:
                rc, out = self.run_cli()
                self.assertEqual(rc, 1, f)
                self.assertIn("FAIL R29: cannot read the board", out, f)
            finally:
                os.rename(p + ".aside", p)
        self.assertEqual(self.run_cli()[0], 0)

    def test_cli_r27(self):
        prof = {n: {"value": v, "spec": s} for n, (v, s) in PROFILE.items() if n != "CONFIG_FREERTOS_HZ"}
        self.write(O.DATA_FILE, {"release_profile": prof})
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL R27: the profile does not name CONFIG_FREERTOS_HZ", out)

    def test_cli_contradicting_profile(self):
        prof = {n: {"value": v, "spec": s} for n, (v, s) in PROFILE.items()}
        prof["CONFIG_LOG_DEFAULT_LEVEL"] = {"value": 3, "spec": "s"}
        self.write(O.DATA_FILE, {"release_profile": prof})
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("CONFIG_LOG_DEFAULT_LEVEL: 3 contradicts R28", out)

    def test_cli_missing_options(self):
        os.remove(os.path.join(self.b, "bootloader", "config", "sdkconfig.json"))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL R18: cannot read", out)
        self.assertIn("build/bootloader", out)

    def test_cli_missing_compile_commands(self):
        os.remove(os.path.join(self.b, "compile_commands.json"))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("compile_commands.json cannot be read", out)
        self.assertIn("R26, R27 and R28 not checked", out)

    def test_cli_missing_profile(self):
        os.remove(os.path.join(self.d, O.DATA_FILE))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn(O.DATA_FILE, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
