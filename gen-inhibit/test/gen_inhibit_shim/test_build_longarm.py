#!/usr/bin/env python3
"""Implementor self-test of build_longarm.py's rebuild rule. Not evidence.

    python3 -B test_build_longarm.py      (py -3.14 -B on Windows)

Each case runs build_longarm.main() against a scratch tree: a stand-in
reverse-it root (canre/ and the capture), a stand-in from_capture.py that
writes its --out file and logs each call, and a stand-in pins.json. The
output and its stamp go to the scratch tree too, so the real scenarios/ is
never touched. The scratch tree is scratch/build-longarm-test/<case>/ under
the folder canre_pin.home() names: the git worktree, or the copy
gi_host_run.sh syncs (no git there, so not gi_paths.scratch()). Each case's
folder is removed when the case ends, pass or fail, through
gi_paths.remove_tree() (AGENTS.md "Removing scratch"). No canre install and no
capture needed.
"""
import contextlib
import io
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_longarm as bl  # noqa: E402
import canre_pin  # noqa: E402  (build_longarm put tools/bench on the path)
import gi_paths  # noqa: E402

SCRATCH = os.path.join(canre_pin.home(HERE), "scratch", "build-longarm-test")
ENTRY = {"repo": "https://example.invalid/canre", "version": "0.1.0",
         "commit": "a" * 40}
CONVERTER = (
    "import sys\n"
    "a = sys.argv[1:]\n"
    "with open(%r, 'a') as f:\n"
    "    f.write('call\\n')\n"
    "if %r:\n"
    "    sys.exit(3)\n"
    "with open(a[a.index('--out') + 1], 'w') as f:\n"
    "    f.write('scn\\n')\n")


class Rebuild(unittest.TestCase):

    def remove(self):
        self.assertTrue(gi_paths.remove_tree(self.d),
                        "%s could not be removed (above)" % self.d)

    def setUp(self):
        self.d = os.path.join(SCRATCH, self.id().rsplit(".", 1)[-1])
        self.assertTrue(gi_paths.remove_tree(self.d),
                        "an earlier run's %s is still there (above)" % self.d)
        self.addCleanup(self.remove)
        root = os.path.join(self.d, "reverse-it")
        os.makedirs(os.path.join(root, "canre"))
        self.capture = os.path.join(root, bl.CAPTURE)
        os.makedirs(os.path.dirname(self.capture))
        with open(self.capture, "w") as f:
            f.write("capture\n")
        self.calls = os.path.join(self.d, "calls.log")
        self.converter = os.path.join(self.d, "from_capture.py")
        self.pins = os.path.join(self.d, "pins.json")
        self.write_pins(ENTRY)
        self.converter_fails(False)
        # The inputs are old, so only the stamp can make a build stale.
        past = time.time() - 3600
        for p in (self.capture, self.converter):
            os.utime(p, (past, past))
        self.saved = (bl.OUT, bl.STAMP, bl.FROM_CAPTURE, canre_pin.PINS_FILE,
                      os.environ.get("REVERSE_IT_DIR"))
        bl.OUT = os.path.join(self.d, "scenarios", "replay.scn")
        bl.STAMP = bl.OUT + ".canre"
        bl.FROM_CAPTURE = self.converter
        canre_pin.PINS_FILE = self.pins
        os.environ["REVERSE_IT_DIR"] = root

    def tearDown(self):
        bl.OUT, bl.STAMP, bl.FROM_CAPTURE, canre_pin.PINS_FILE, env = self.saved
        if env is None:
            os.environ.pop("REVERSE_IT_DIR", None)
        else:
            os.environ["REVERSE_IT_DIR"] = env

    def write_pins(self, entry, files=None):
        past = None
        if os.path.exists(self.converter):
            past = os.path.getmtime(self.converter)
        with open(self.pins, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"files": files or {}, "canre": entry}, f, indent=1)
        if past is not None:
            # pins.json's mtime is not an input: keep the test honest by
            # making it no newer than anything else.
            os.utime(self.pins, (past, past))

    def converter_fails(self, fails):
        with open(self.converter, "w", encoding="utf-8", newline="\n") as f:
            f.write(CONVERTER % (self.calls, fails))
        past = time.time() - 3600
        os.utime(self.converter, (past, past))

    def build(self):
        """(exit status, stdout) of build_longarm.main()."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                rc = bl.main()
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue()

    def ncalls(self):
        if not os.path.exists(self.calls):
            return 0
        with open(self.calls) as f:
            return len(f.readlines())

    def stamp(self):
        with open(bl.STAMP, encoding="utf-8") as f:
            return json.load(f)

    def test_first_build_writes_the_stamp(self):
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.assertIn("(missing)", out)
        self.assertEqual(self.ncalls(), 1)
        self.assertTrue(os.path.isfile(bl.OUT))
        self.assertEqual(self.stamp(), ENTRY)

    def test_same_pin_is_up_to_date(self):
        self.build()
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.assertIn("up to date", out)
        self.assertEqual(self.ncalls(), 1)

    def test_a_moved_commit_rebuilds(self):
        self.build()
        moved = dict(ENTRY, commit="b" * 40)
        self.write_pins(moved)
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.assertIn("another canre pin entry", out)
        self.assertEqual(self.ncalls(), 2)
        self.assertEqual(self.stamp(), moved)
        rc, out = self.build()
        self.assertIn("up to date", out)
        self.assertEqual(self.ncalls(), 2)

    def test_any_change_to_the_entry_rebuilds(self):
        self.build()
        n = 1
        for change in ({"version": "0.2.0"},
                       {"repo": "https://example.invalid/other"}):
            self.write_pins(dict(ENTRY, **change))
            rc, out = self.build()
            n += 1
            self.assertEqual(self.ncalls(), n, (change, out))

    def test_other_entries_of_pins_json_do_not_rebuild(self):
        self.build()
        self.write_pins(ENTRY, files={"x": "0" * 64})
        now = time.time()
        os.utime(self.pins, (now, now))   # newer than the output, and ignored
        rc, out = self.build()
        self.assertIn("up to date", out)
        self.assertEqual(self.ncalls(), 1)

    def test_a_missing_or_unreadable_stamp_rebuilds(self):
        self.build()
        os.remove(bl.STAMP)
        rc, out = self.build()
        self.assertIn("no record of the canre pin", out)
        self.assertEqual(self.ncalls(), 2)
        with open(bl.STAMP, "w") as f:
            f.write("{not json")
        rc, out = self.build()
        self.assertIn("no record of the canre pin", out)
        self.assertEqual(self.ncalls(), 3)
        self.assertEqual(self.stamp(), ENTRY)

    def test_a_failed_build_leaves_the_old_file_and_stamp(self):
        self.build()
        moved = dict(ENTRY, commit="c" * 40)
        self.write_pins(moved)
        self.converter_fails(True)
        rc, out = self.build()
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.stamp(), ENTRY)     # still names the file's pin
        self.assertFalse(os.path.exists(bl.OUT + ".tmp"))
        self.converter_fails(False)
        rc, out = self.build()                    # so the next run rebuilds
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.stamp(), moved)

    def test_mtime_inputs_still_rebuild(self):
        self.build()
        now = time.time() + 5
        os.utime(self.capture, (now, now))
        rc, out = self.build()
        self.assertIn("older than", out)
        self.assertEqual(self.ncalls(), 2)

    def test_a_bad_entry_stops_the_build(self):
        bad = dict(ENTRY)
        del bad["commit"]
        self.write_pins(bad)
        rc, out = self.build()
        self.assertIsInstance(rc, str)
        self.assertIn("canre", rc)
        self.assertEqual(self.ncalls(), 0)


if __name__ == "__main__":
    unittest.main()
