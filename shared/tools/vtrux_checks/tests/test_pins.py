"""R30 pins check: each rule shown failing on a planted input and passing on a faithful one, on a
small build tree (build/ and build/bootloader/) with ninja's own records.
Runs alone: python -B shared/tools/vtrux_checks/tests/test_pins.py
"""

import contextlib
import io
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(TOOLS, "tests"))
from vtrux_checks import pins as P  # noqa: E402
from vtrux_checks import cli  # noqa: E402
from test_scope import scratch  # noqa: E402
from test_builddir import deps_log  # noqa: E402
import boardkit as K  # noqa: E402

BOARD = "managed_components/vtrux__board_a"
OBJ = "esp-idf/%s/CMakeFiles/__idf_%s.dir/%s.obj"
# (component, source from the project root): what build/ compiles.
SOURCES = [("main", "main/can.c"), ("main", "main/app.c"), ("main", "main/sub/led.c"),
           ("board_a", BOARD + "/board.c")]


class Build(unittest.TestCase):
    def setUp(self):
        self.d = scratch()
        self.b = os.path.join(self.d, "build")
        K.board(self.d)
        for _c, src in SOURCES:
            self.write(src, "/* source */\n")
        self.write(BOARD + "/include/board_pins.h", "#define PIN 1\n")
        self.write(BOARD + "/include/board.h", "int board_can_ports(void);\n")
        self.write("build/project_description.json", json.dumps({"project_path": self.d}))
        self.write("build/bootloader/CMakeCache.txt", "")
        self.write("build/bootloader/project_description.json", json.dumps({"project_path": self.d}))
        self.ninja("build/bootloader", [("bootloader", "../../boot.c")])
        self.deps("build/bootloader", [(OBJ % ("bootloader", "bootloader", "boot.c"), ["../../boot.c"])])
        self.ninja("build", [(c, "../" + s) for c, s in SOURCES])
        # board.c and can.c read the pins; app.c reads only board.h; led.c reads nothing of the board's.
        self.reads = {"main/can.c": [BOARD + "/include/board_pins.h"], "main/app.c": [BOARD + "/include/board.h"],
                      "main/sub/led.c": [], BOARD + "/board.c": [BOARD + "/include/board_pins.h", BOARD + "/include/board.h"]}
        self.write_deps()
        self.listed(["main/can.c"])

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, rel, data):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"newline": "\n"})) as f:
            f.write(data)

    def obj(self, comp, src):
        return OBJ % (comp, comp, os.path.basename(src))

    def ninja(self, folder, edges):
        text = "rule CC\n  command = gcc -c $in -o $out\n"
        text += "".join("build %s: CC %s\n" % (self.obj(c, s), s) for c, s in edges)
        self.write(folder + "/build.ninja", text)

    def deps(self, folder, records):
        self.write(folder + "/.ninja_deps", bytes(deps_log(records)))

    def write_deps(self):
        """build/'s deps log from self.reads: each object read its source and its headers, written
        as ninja writes them, relative to the build folder or absolute."""
        recs = []
        for i, (c, s) in enumerate(SOURCES):
            heads = [("../" + h) if i % 2 else os.path.join(self.d, h).replace(os.sep, "/") for h in self.reads[s]]
            recs.append((self.obj(c, s), ["../" + s] + heads))
        self.deps("build", recs)

    def listed(self, lst):
        self.write(P.DATA_FILE, json.dumps({"release_profile": {}, P.KEY: lst}))

    def run_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["pins", "--build", self.b])
        return rc, buf.getvalue()

    def test_faithful_passes(self):
        f = P.collect(self.b)
        got = sorted((os.path.relpath(src, self.d).replace(os.sep, "/"), ours) for _f, _o, src, ours in f["found"])
        self.assertEqual(got, sorted([(BOARD + "/board.c", False), ("main/can.c", True)]))
        self.assertEqual(P.judge(f), ([], []))
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS R30", out)

    def test_unlisted_project_file(self):
        self.reads["main/app.c"].append(BOARD + "/include/board_pins.h")
        self.write_deps()
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build: main/app.c read board_pins.h and is not on stock_files_using_pins", out)

    def test_subfolder_and_empty_list(self):
        self.reads["main/sub/led.c"].append(BOARD + "/include/board_pins.h")
        self.write_deps()
        self.listed(["main/can.c", "main/sub/led.c"])
        self.assertEqual(self.run_cli()[0], 0)
        self.listed([])
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("main/can.c read board_pins.h", out)
        self.assertIn("main/sub/led.c read board_pins.h", out)

    def test_another_header_of_that_name_is_not_the_boards(self):
        self.write("main/board_pins.h", "#define PIN 2\n")
        self.reads["main/app.c"].append("main/board_pins.h")
        self.write_deps()
        self.assertEqual(self.run_cli()[0], 0)

    def test_listed_but_reads_no_pins(self):
        self.listed(["main/can.c", "main/app.c"])
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        self.assertIn("listed, read no pins: main/app.c", out)

    def test_nothing_read_the_header(self):
        self.reads = {s: [] for _c, s in SOURCES}
        self.write_deps()
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("no object read the board's include/board_pins.h", out)

    def test_object_without_an_input(self):
        self.ninja("build", [(c, "../" + s) for c, s in SOURCES if s != "main/can.c"])
        with open(os.path.join(self.b, "build.ninja"), "a", newline="\n") as f:
            f.write("build %s: CC | ../main/can.c\n" % self.obj("main", "main/can.c"))
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("can.c.obj read board_pins.h and has no build statement with an input", out)

    def test_records_of_old_outputs_ignored(self):
        """An object today's build.ninja does not write is not in this build (ninja_deps' current_only)."""
        self.ninja("build", [(c, "../" + s) for c, s in SOURCES if s != "main/can.c"])
        self.listed([])
        self.assertEqual(self.run_cli()[0], 0)

    def test_bootloader_folder_read(self):
        self.write("main/boot_hook.c", "/* source */\n")
        self.ninja("build/bootloader", [("bootloader", "../../boot.c"), ("main", "../../main/boot_hook.c")])
        self.deps("build/bootloader", [(self.obj("main", "main/boot_hook.c"),
                                        ["../../main/boot_hook.c", "../../" + BOARD + "/include/board_pins.h"])])
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("build/bootloader: main/boot_hook.c read board_pins.h", out)

    def test_bad_list(self):
        for label, lst in (("missing", None), ("not a list", "main/can.c"), ("absolute", [os.path.join(self.d, "main/can.c")]),
                           ("backslash", ["main\\can.c"]), ("leading slash", ["/main/can.c"]), ("dot dot", ["main/../main/can.c"]),
                           ("dot", ["./main/can.c"]), ("empty", [""]), ("twice", ["main/can.c", "main/can.c"]),
                           ("not a file", ["main/can.c", "main/gone.c"]), ("a folder", ["main"]), ("a number", [1])):
            if lst is None:
                self.write(P.DATA_FILE, json.dumps({"release_profile": {}}))
            else:
                self.listed(lst)
            rc, out = self.run_cli()
            self.assertEqual(rc, 1, label)
            self.assertIn("FAIL R30: cannot read", out, label)

    def test_board_not_found(self):
        K.lock(self.d, {})
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("0 shared/boards/* components", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
