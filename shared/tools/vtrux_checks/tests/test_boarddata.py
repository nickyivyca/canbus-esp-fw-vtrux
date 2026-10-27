"""The project's board (R21, R29, R30; shared/boards/SPEC.md B2-B4): found from dependencies.lock,
read from managed_components/. Each rule shown failing on a planted input and passing on a
faithful one. Runs alone: python -B shared/tools/vtrux_checks/tests/test_boarddata.py

    VTRUX_TEST_BOARD=<a board's folder>   also reads a real board's board.json and sdkconfig.board
"""

import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
from vtrux_checks import boarddata as D  # noqa: E402
from test_scope import scratch  # noqa: E402
import boardkit as K  # noqa: E402


class Find(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_one_git_board(self):
        f = K.board(self.d)
        self.assertEqual(D.find(self.d), (K.NAME, f))

    def test_path_forms(self):
        for label, path, want in (("exact", "shared/boards/b", True), ("./ and a trailing /", "./shared/boards/b/", True),
                                  ("backslashes", "shared\\boards\\b", True), ("deeper", "shared/boards/b/sub", False),
                                  ("another folder", "shared/tools", False), ("not at the root", "x/shared/boards/b", False),
                                  ("the boards folder itself", "shared/boards", False)):
            self.assertEqual(D.is_board({"source": dict(K.GIT, path=path)}), want, label)
        for label, path, want in (("relative", "../shared-x/shared/boards/b", True),
                                  ("absolute", "C:/w/shared/boards/b", True), ("bare", "shared/boards/b", True),
                                  ("other", "../shared-x/shared/tools", False)):
            self.assertEqual(D.is_board({"source": {"type": "local", "path": path}}), want, label)
        self.assertFalse(D.is_board({"source": {"type": "service"}}), "a registry component")
        self.assertFalse(D.is_board({"source": {"type": "idf"}}), "ESP-IDF")
        self.assertFalse(D.is_board({}), "no source")

    def test_none(self):
        K.lock(self.d, {"espressif/x": {"type": "service", "registry_url": "https://example.invalid"}})
        with self.assertRaisesRegex(D.BoardError, "0 shared/boards"):
            D.find(self.d)

    def test_two(self):
        K.lock(self.d, {"vtrux/a": dict(K.GIT, path="shared/boards/a"), "vtrux/b": dict(K.GIT, path="shared/boards/b")})
        with self.assertRaisesRegex(D.BoardError, "2 shared/boards.*vtrux/a, vtrux/b"):
            D.find(self.d)

    def test_git_and_local_count_together(self):
        K.lock(self.d, {"vtrux/a": dict(K.GIT, path="shared/boards/a"),
                        "vtrux/b": {"type": "local", "path": "../shared-x/shared/boards/b"}})
        with self.assertRaisesRegex(D.BoardError, "2 shared/boards"):
            D.find(self.d)

    def test_local_refused(self):
        K.lock(self.d, {"vtrux/a": {"type": "local", "path": "../w/shared/boards/a"}})
        with self.assertRaisesRegex(D.BoardError, "local folder"):
            D.find(self.d)

    def test_not_downloaded(self):
        K.board(self.d)
        shutil.rmtree(K.folder(self.d))
        with self.assertRaisesRegex(D.BoardError, "not in managed_components"):
            D.find(self.d)

    def test_no_lock(self):
        with self.assertRaisesRegex(D.BoardError, "dependencies.lock"):
            D.find(self.d)


class Read(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def put(self, obj):
        with open(os.path.join(self.d, D.BOARD_JSON), "w", newline="\n") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_reads(self):
        self.put(K.data())
        b = D.read(self.d)
        self.assertEqual((b["target"], b["flash_size"], b["hardware_name"]), ("chipa", "XMB", "A"))
        self.assertEqual(b["partition_table"], os.path.normpath(os.path.join(self.d, "ref/pt.bin")))
        self.assertEqual(b["required"], [
            {"setting": "a single option", "kind": "option", "options": ["CONFIG_REQ"]},
            {"setting": "a choice", "kind": "choice", "options": ["CONFIG_CON_A", "CONFIG_CON_B", "CONFIG_CON_C"]}])

    def test_no_required(self):
        self.put(K.data(required=[]))
        self.assertEqual(D.read(self.d)["required"], [])

    def test_bad(self):
        good = K.data()
        bad = [("not JSON", "{"), ("not an object", [good]),
               ("extra key", dict(good, slot=1)), ("a hash beside the table", dict(good, sha256="0" * 64))]
        for k in D.KEYS:
            bad.append(("no " + k, {x: v for x, v in good.items() if x != k}))
        bad += [("bad target", K.data(target="ESP32-C3")), ("empty flash size", K.data(flash_size="")),
                ("flash size a number", K.data(flash_size=4)),
                ("absolute table", K.data(partition_table=os.path.abspath("p.bin"))),
                ("table outside the board", K.data(partition_table="../p.bin")),
                ("table outside, backslash", K.data(partition_table="ref\\..\\..\\p.bin")),
                ("empty table", K.data(partition_table="")), ("empty hardware name", K.data(hardware_name="")),
                ("required not a list", K.data(required={"REQ": 1})),
                ("no setting", K.data(required=[{"option": "CONFIG_REQ"}])),
                ("empty setting", K.data(required=[{"setting": "", "option": "CONFIG_REQ"}])),
                ("option and choice", K.data(required=[{"setting": "s", "option": "CONFIG_A", "choice": ["CONFIG_B", "CONFIG_C"]}])),
                ("neither", K.data(required=[{"setting": "s", "value": "CONFIG_A"}])),
                ("option without CONFIG_", K.data(required=[{"setting": "s", "option": "REQ"}])),
                ("option lower case", K.data(required=[{"setting": "s", "option": "CONFIG_req"}])),
                ("choice of one", K.data(required=[{"setting": "s", "choice": ["CONFIG_A"]}])),
                ("choice not a list", K.data(required=[{"setting": "s", "choice": "CONFIG_A"}])),
                ("choice member bad", K.data(required=[{"setting": "s", "choice": ["CONFIG_A", "B"]}])),
                ("choice member twice", K.data(required=[{"setting": "s", "choice": ["CONFIG_A", "CONFIG_A"]}])),
                ("an option in two settings", K.data(required=[{"setting": "s", "option": "CONFIG_A"},
                                                               {"setting": "t", "choice": ["CONFIG_A", "CONFIG_B"]}]))]
        for label, obj in bad:
            self.put(obj)
            with self.assertRaises(D.BoardError, msg=label):
                D.read(self.d)

    def test_missing(self):
        with self.assertRaisesRegex(D.BoardError, "board.json"):
            D.read(self.d)


class SdkconfigBoard(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def put(self, text):
        with open(os.path.join(self.d, D.SDKCONFIG_BOARD), "w", newline="\n") as f:
            f.write(text)

    def test_reads(self):
        self.put(K.SDKCONFIG + "\nCONFIG_NEG=-1\nCONFIG_DEC=40\n  # a comment\nCONFIG_EMPTY=\"\"\n")
        self.assertEqual(D.read_sdkconfig_board(self.d), {
            "CONFIG_FIXED_ON": True, "CONFIG_FIXED_HEX": 0x1000, "CONFIG_FIXED_STR": 'a "b"',
            "CONFIG_FIXED_OFF": D.UNSET, "CONFIG_APP_ONLY": True, "CONFIG_NEG": -1, "CONFIG_DEC": 40, "CONFIG_EMPTY": ""})

    def test_bad(self):
        for label, text in (("set twice", "CONFIG_A=y\nCONFIG_A=y\n"), ("set and unset", "CONFIG_A=y\n# CONFIG_A is not set\n"),
                            ("n is not written", "CONFIG_A=n\n"), ("unquoted string", "CONFIG_A=abc\n"),
                            ("not a line", "CONFIG_A y\n"), ("lower case", "CONFIG_a=y\n"),
                            ("sets nothing", "# only comments\n\n"), ("empty", "")):
            self.put(text)
            with self.assertRaises(D.BoardError, msg=label):
                D.read_sdkconfig_board(self.d)

    def test_missing(self):
        with self.assertRaisesRegex(D.BoardError, "sdkconfig.board"):
            D.read_sdkconfig_board(self.d)


@unittest.skipUnless(os.environ.get("VTRUX_TEST_BOARD"), "VTRUX_TEST_BOARD not set")
class RealBoard(unittest.TestCase):
    """A real board's folder reads, and its reference table is there."""

    def test_reads(self):
        f = os.environ["VTRUX_TEST_BOARD"]
        b = D.read(f)
        self.assertTrue(os.path.isfile(b["partition_table"]), b["partition_table"])
        if os.path.isfile(os.path.join(f, D.SDKCONFIG_BOARD)):
            self.assertTrue(D.read_sdkconfig_board(f))


if __name__ == "__main__":
    unittest.main(verbosity=2)
