"""R21 board-data check: each rule shown failing on a planted input and passing on a faithful one.
Runs alone: python -B shared/tools/vtrux_checks/tests/test_board.py

    IDF_PATH=<an ESP-IDF>   also reads the real toolchain files for esp32c3 and esp32s3, and its
                            esp_app_format.h

The partition tables are built here from ESP-IDF's published format, so no table is committed.
"""

import ast
import glob
import hashlib
import json
import os
import shutil
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, TOOLS)
from vtrux_checks import board as B  # noqa: E402
from vtrux_checks import parttable as P  # noqa: E402
from vtrux_checks.tests.test_scope import scratch  # noqa: E402

SLOT = 0x1B3000


def entry(t, st, off, size, label, flags=0):
    return b"\xaa\x50" + struct.pack("<BBII", t, st, off, size) + label.encode().ljust(16, b"\x00") + struct.pack("<I", flags)


def table(rows=None, md5=True):
    """A partition-table .bin as ESP-IDF's gen_esp32part.py writes one: entries, MD5 entry, 0xFF to 0xC00."""
    rows = rows if rows is not None else [
        (P.DATA, 0x02, 0x9000, 0x4000, "nvs"), (P.DATA, 0x00, 0xD000, 0x2000, "otadata"),
        (P.APP, 0x10, 0x10000, SLOT, "ota_0"), (P.APP, 0x11, 0x1D0000, SLOT, "ota_1")]
    b = b"".join(entry(*r) for r in rows)
    if md5:
        b += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(b).digest()
    return b + b"\xff" * (P.MAX_LEN - len(b))


TOOLCHAIN = 'set(CMAKE_SYSTEM_NAME Generic)\nset(CMAKE_C_COMPILER %sgcc)\nset(_CMAKE_TOOLCHAIN_PREFIX %s)\n'

# esp_app_format.h as ESP-IDF writes it, with made-up names and values so that the check is seen
# to take them from the file: XMB is field value 5, YMB 6, MAX ignored.
APP_FORMAT = '''/* header */
typedef enum {
    ESP_IMAGE_SPI_SPEED_DIV_2,  /*!< not this enum */
} esp_image_spi_freq_t;

typedef enum {
    ESP_IMAGE_FLASH_SIZE_XMB = 5,   /*!< SPI flash size X MB */
    ESP_IMAGE_FLASH_SIZE_YMB,       // SPI flash size Y MB
    ESP_IMAGE_FLASH_SIZE_MAX        /*!< SPI flash size MAX */
} esp_image_flash_size_t;

#define ESP_IMAGE_HEADER_MAGIC 0xA7 /*!< The magic word */
'''


def image(size_value, n=1000, magic=0xA7, speed=0xF):
    '''An image of n bytes whose header gives flash size field size_value (and speed in the low four bits).'''
    return bytes([magic, 3, 2, (size_value << 4) | speed]) + b"\x00" * (n - 4)


class Table(unittest.TestCase):
    def test_read(self):
        e = P.read(table())
        self.assertEqual([x[4] for x in e], ["nvs", "otadata", "ota_0", "ota_1"])
        self.assertEqual(P.apps(e), [("ota_0", 0x10, 0x10000, SLOT), ("ota_1", 0x11, 0x1D0000, SLOT)])

    def test_without_md5(self):
        self.assertEqual(len(P.read(table(md5=False))), 4)

    def test_malformed(self):
        good = table()
        for label, b in (("a flipped byte", good[:40] + bytes([good[40] ^ 1]) + good[41:]),
                         ("bad magic", b"\xab" + good[1:]),
                         ("bad magic, no MD5 entry", b"\xab" + table(md5=False)[1:]),
                         ("not a whole entry", good[:100]),
                         ("too long", good + b"\xff" * 32),
                         ("bytes after the end", good[:-1] + b"\x00"),
                         ("no end marker", good[:5 * 32]),
                         ("bad MD5 padding", good[:4 * 32] + b"\xeb\xeb\x00" + good[4 * 32 + 3:])):
            with self.assertRaises(P.TableError, msg=label):
                P.read(b)


class Data(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def data(self, obj):
        with open(os.path.join(self.d, B.DATA_FILE), "w", newline="\n") as f:
            json.dump(obj if obj is not None else {}, f)

    def test_inline(self):
        self.data({"board": {"target": "esp32c3", "partition_table": "ref/pt.bin", "flash_size": "4MB"}})
        got = B.read_board(self.d)
        self.assertEqual(got, ("esp32c3", os.path.normpath(os.path.join(self.d, "ref/pt.bin")), "4MB"))

    def test_named_board_not_built(self):
        self.data({"board": "x2"})
        with self.assertRaisesRegex(B.BoardError, "not built yet"):
            B.read_board(self.d)

    def test_bad(self):
        good = {"target": "esp32c3", "partition_table": "p", "flash_size": "4MB"}
        for label, b in (("no board", None), ("extra key", dict(good, slot=1)),
                         ("missing key", {"target": "esp32c3"}),
                         ("no flash size", {"target": "esp32c3", "partition_table": "p"}),
                         ("bad target", dict(good, target="ESP32-C3")),
                         ("absolute path", dict(good, partition_table=os.path.abspath("p"))),
                         ("empty path", dict(good, partition_table="")),
                         ("flash size a number", dict(good, flash_size=4)),
                         ("empty flash size", dict(good, flash_size=""))):
            self.data({"board": b} if b is not None else {})
            with self.assertRaises(B.BoardError, msg=label):
                B.read_board(self.d)

    def test_no_file(self):
        with self.assertRaises(B.BoardError):
            B.read_board(self.d)

    def test_slot_is_smallest_app(self):
        p = os.path.join(self.d, "pt.bin")
        with open(p, "wb") as f:
            f.write(table([(P.APP, 0x00, 0x10000, 0x100000, "factory"), (P.APP, 0x10, 0x110000, 0xF0000, "ota_0"),
                           (P.DATA, 0x02, 0x9000, 0x6000, "nvs")]))
        self.assertEqual(B.slot(p)[0], 0xF0000)

    def test_slot_no_app_is_malformed(self):
        p = os.path.join(self.d, "pt.bin")
        with open(p, "wb") as f:
            f.write(table([(P.DATA, 0x02, 0x9000, 0x6000, "nvs")]))
        with self.assertRaisesRegex(B.BoardError, "malformed"):
            B.slot(p)

    def test_slot_malformed_or_missing(self):
        p = os.path.join(self.d, "pt.bin")
        with self.assertRaises(B.BoardError):
            B.slot(p)
        with open(p, "wb") as f:
            f.write(table()[:100])
        with self.assertRaisesRegex(B.BoardError, "malformed"):
            B.slot(p)


class Prefix(unittest.TestCase):
    def setUp(self):
        self.d = scratch()
        os.makedirs(os.path.join(self.d, "tools", "cmake"))

    def tearDown(self):
        shutil.rmtree(self.d)

    def toolchain(self, target, text):
        with open(os.path.join(self.d, "tools", "cmake", "toolchain-%s.cmake" % target), "w", newline="\n") as f:
            f.write(text)

    def test_from_idf(self):
        self.toolchain("chipa", TOOLCHAIN % ("arch-a-elf-", "arch-a-elf-"))
        self.toolchain("chipb", TOOLCHAIN % ("arch-b-elf-", "arch-b-elf-"))
        self.assertEqual(B.tool_prefix(self.d, "chipa"), "arch-a-elf-")
        self.assertEqual(B.tool(self.d, "chipb", "nm"), "arch-b-elf-nm")

    def test_no_toolchain(self):
        with self.assertRaises(B.BoardError):
            B.tool_prefix(self.d, "chipz")

    def test_prefix_missing_or_twice(self):
        self.toolchain("chipa", "set(CMAKE_C_COMPILER arch-a-elf-gcc)\n")
        with self.assertRaises(B.BoardError):
            B.tool_prefix(self.d, "chipa")
        self.toolchain("chipa", TOOLCHAIN % ("a-", "a-") + "set(_CMAKE_TOOLCHAIN_PREFIX b-)\n")
        with self.assertRaises(B.BoardError):
            B.tool_prefix(self.d, "chipa")

    @unittest.skipUnless(os.environ.get("IDF_PATH"), "IDF_PATH not set")
    def test_real_idf(self):
        idf = os.environ["IDF_PATH"]
        self.assertEqual(B.tool_prefix(idf, "esp32c3"), "riscv32-esp-elf-")
        self.assertEqual(B.tool_prefix(idf, "esp32s3"), "xtensa-esp32s3-elf-")


class ImageFormat(unittest.TestCase):
    """The flash size field's values and the header magic, from ESP-IDF's esp_app_format.h."""

    def setUp(self):
        self.d = scratch()
        self.h = os.path.join(self.d, *B.APP_FORMAT)
        os.makedirs(os.path.dirname(self.h))

    def tearDown(self):
        shutil.rmtree(self.d)

    def header(self, text):
        with open(self.h, "w", newline="\n") as f:
            f.write(text)

    def test_from_idf(self):
        self.header(APP_FORMAT)
        self.assertEqual(B.image_format(self.d), (0xA7, {5: "XMB", 6: "YMB"}))

    def test_values_as_c_gives_them(self):
        self.header(APP_FORMAT.replace("ESP_IMAGE_FLASH_SIZE_YMB,", "ESP_IMAGE_FLASH_SIZE_YMB = 0x9,")
                    .replace("ESP_IMAGE_FLASH_SIZE_XMB = 5,", "ESP_IMAGE_FLASH_SIZE_XMB,"))
        self.assertEqual(B.image_format(self.d)[1], {0: "XMB", 9: "YMB"})

    def test_unreadable(self):
        with self.assertRaises(B.BoardError, msg="no file"):
            B.image_format(self.d)
        for label, text in (("no magic", APP_FORMAT.replace("#define ESP_IMAGE_HEADER_MAGIC", "#define OTHER")),
                            ("no enum", APP_FORMAT.replace("esp_image_flash_size_t;", "other_t;")),
                            ("two enums", APP_FORMAT + APP_FORMAT.split("#define")[0]),
                            ("an enumerator not read", APP_FORMAT.replace("ESP_IMAGE_FLASH_SIZE_YMB,", "OTHER_NAME,")),
                            ("a name twice", APP_FORMAT.replace("ESP_IMAGE_FLASH_SIZE_YMB,", "ESP_IMAGE_FLASH_SIZE_XMB,")),
                            ("only MAX", APP_FORMAT.replace("ESP_IMAGE_FLASH_SIZE_XMB = 5,", "")
                             .replace("ESP_IMAGE_FLASH_SIZE_YMB,", ""))):
            self.header(text)
            with self.assertRaises(B.BoardError, msg=label):
                B.image_format(self.d)

    def test_header_reader(self):
        p = os.path.join(self.d, "img.bin")
        for label, data, want in (("high four bits", image(6), "YMB"), ("speed bits ignored", image(5, speed=0), "XMB"),
                                  ("a value ESP-IDF does not name", image(2), "unknown value 2")):
            with open(p, "wb") as f:
                f.write(data)
            self.assertEqual(B.header_flash_size(p, 0xA7, {5: "XMB", 6: "YMB"}), want, label)
        for label, data in (("wrong magic", image(5, magic=0xE9)), ("too short", image(5)[:3]), ("empty", b"")):
            with open(p, "wb") as f:
                f.write(data)
            with self.assertRaises(B.BoardError, msg=label):
                B.header_flash_size(p, 0xA7, {5: "XMB", 6: "YMB"})

    @unittest.skipUnless(os.environ.get("IDF_PATH"), "IDF_PATH not set")
    def test_real_idf(self):
        magic, sizes = B.image_format(os.environ["IDF_PATH"])
        self.assertEqual(magic, 0xE9)
        self.assertEqual((sizes[2], sizes[3]), ("4MB", "8MB"))


TOOL_NAME = r"(riscv32|xtensa|esp32ulp)[-\w]*-elf-"


def hard_coded_tools(source):
    """String constants (docstrings aside) in Python source that name a chip's toolchain."""
    import re
    tree = ast.parse(source)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            docs.add(id(n.body[0].value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and re.search(TOOL_NAME, n.value)]


class NoHardCodedTools(unittest.TestCase):
    """R21 (Q4 (b)): no check names a tool of one chip; tool() names it from the board's target."""

    def test_scanner_finds_one(self):
        self.assertEqual(hard_coded_tools('"""riscv32-esp-elf-nm in a docstring"""\nNM = "riscv32-esp-elf-nm"\n'),
                         ["riscv32-esp-elf-nm"])
        self.assertEqual(hard_coded_tools('def f():\n    return ["xtensa-esp32s3-elf-objdump", "-d"]\n'),
                         ["xtensa-esp32s3-elf-objdump"])
        self.assertEqual(hard_coded_tools('X = "riscv32-esp-elf"\n'), [])

    def test_checks_name_none(self):
        mods = glob.glob(os.path.join(TOOLS, "vtrux_checks", "*.py"))
        self.assertIn(os.path.join(TOOLS, "vtrux_checks", "board.py"), mods)
        found = {}
        for m in mods:
            with open(m, encoding="utf-8") as f:
                hits = hard_coded_tools(f.read())
            if hits:
                found[os.path.basename(m)] = hits
        self.assertEqual(found, {})


class Collect(unittest.TestCase):
    """A small release build folder, its project's board data, and a fake ESP-IDF."""

    def setUp(self):
        self.d = scratch()
        self.p = os.path.join(self.d, "proj")
        self.b = os.path.join(self.p, "build")
        self.idf = os.path.join(self.d, "idf")
        os.makedirs(os.path.join(self.idf, "tools", "cmake"))
        with open(os.path.join(self.idf, "tools", "cmake", "toolchain-chipa.cmake"), "w", newline="\n") as f:
            f.write(TOOLCHAIN % ("arch-a-elf-", "arch-a-elf-"))
        os.makedirs(os.path.dirname(os.path.join(self.idf, *B.APP_FORMAT)))
        with open(os.path.join(self.idf, *B.APP_FORMAT), "w", newline="\n") as f:
            f.write(APP_FORMAT)
        self.write("ref/pt.bin", table())
        self.write("build/partition_table/partition-table.bin", table())
        self.write("build/proj.bin", image(5))
        self.write("build/bootloader/bootloader.bin", image(5, 300))
        self.write("build/config/sdkconfig.json", json.dumps({"IDF_TARGET": "chipa"}))
        self.write("build/flasher_args.json", json.dumps(
            {"partition-table": {"offset": "0x8000", "file": "partition_table/partition-table.bin"},
             "bootloader": {"offset": "0x0", "file": "bootloader/bootloader.bin"}}))
        self.write("build/project_description.json", json.dumps(
            {"project_path": self.p.replace(os.sep, "/"), "idf_path": self.idf.replace(os.sep, "/"), "app_bin": "proj.bin"}))
        self.board({"target": "chipa", "partition_table": "ref/pt.bin", "flash_size": "XMB"})

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, rel, data):
        p = os.path.join(self.p, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"newline": "\n"})) as f:
            f.write(data)

    def board(self, b):
        self.write(B.DATA_FILE, json.dumps({"release_profile": {}, "board": b}))

    def run_main(self):
        class A:
            build = self.b
        return B.main(A)

    def test_faithful_passes(self):
        f = B.collect(self.b)
        self.assertEqual((f["slot"], f["image_size"], f["prefix"], f["build_target"]), (SLOT, 1000, "arch-a-elf-", "chipa"))
        self.assertEqual([(w, s) for w, _p, s in f["headers"]], [("app", "XMB"), ("bootloader", "XMB")])
        self.assertEqual(B.judge(f), [])
        self.assertEqual(self.run_main(), 0)

    def test_table_differs(self):
        t = table()
        self.write("build/partition_table/partition-table.bin", t[:-1] + b"\x00")
        self.assertEqual(len(B.judge(B.collect(self.b))), 1)
        self.write("build/partition_table/partition-table.bin", table([(P.APP, 0x10, 0x10000, SLOT, "ota_0")]))
        self.assertIn("byte-identical", B.judge(B.collect(self.b))[0])
        self.assertEqual(self.run_main(), 1)

    def test_image_at_and_over_the_slot(self):
        self.write("build/proj.bin", image(5, SLOT))
        self.assertEqual(B.judge(B.collect(self.b)), [])
        self.write("build/proj.bin", image(5, SLOT + 1))
        self.assertIn("over the %d-byte slot by 1" % SLOT, B.judge(B.collect(self.b))[0])
        self.assertEqual(self.run_main(), 1)

    def test_slot_from_the_reference_not_the_build(self):
        small = table([(P.APP, 0x10, 0x10000, 500, "ota_0"), (P.APP, 0x11, 0x20000, 0x1000, "ota_1")])
        self.write("ref/pt.bin", small)
        self.write("build/partition_table/partition-table.bin", small)
        self.assertEqual([x for x in B.judge(B.collect(self.b)) if "slot" in x], ["the image is 1000 bytes, over the 500-byte slot by 500"])

    def test_other_target(self):
        self.write("build/config/sdkconfig.json", json.dumps({"IDF_TARGET": "chipb"}))
        self.assertIn("CONFIG_IDF_TARGET is 'chipb'", B.judge(B.collect(self.b))[0])
        self.assertEqual(self.run_main(), 1)

    def test_board_target_without_toolchain(self):
        self.board({"target": "chipz", "partition_table": "ref/pt.bin", "flash_size": "XMB"})
        with self.assertRaises(B.BoardError):
            B.collect(self.b)
        self.assertEqual(self.run_main(), 1)

    def test_cannot_read(self):
        for rel in ("build/flasher_args.json", "build/partition_table/partition-table.bin", "ref/pt.bin", "build/proj.bin",
                    "build/bootloader/bootloader.bin", "../idf/components/bootloader_support/include/esp_app_format.h"):
            p = os.path.join(self.p, rel)
            os.rename(p, p + ".aside")
            try:
                self.assertEqual(self.run_main(), 1, rel)
            finally:
                os.rename(p + ".aside", p)
        self.assertEqual(self.run_main(), 0)

    def test_flash_size_app_or_bootloader(self):
        for rel, what in (("build/proj.bin", "app"), ("build/bootloader/bootloader.bin", "bootloader")):
            with open(os.path.join(self.p, rel), "rb") as f:
                keep = f.read()
            self.write(rel, image(6, len(keep)))
            self.assertEqual(B.judge(B.collect(self.b)),
                             ["the %s image's header gives flash size YMB, the board's is XMB" % what], rel)
            self.assertEqual(self.run_main(), 1)
            self.write(rel, keep)
        self.assertEqual(self.run_main(), 0)

    def test_flash_size_both_and_unknown(self):
        self.write("build/proj.bin", image(6))
        self.write("build/bootloader/bootloader.bin", image(7, 300))
        self.assertEqual(B.judge(B.collect(self.b)),
                         ["the app image's header gives flash size YMB, the board's is XMB",
                          "the bootloader image's header gives flash size unknown value 7, the board's is XMB"])

    def test_board_flash_size_from_idf(self):
        self.board({"target": "chipa", "partition_table": "ref/pt.bin", "flash_size": "YMB"})
        self.assertEqual(len(B.judge(B.collect(self.b))), 2)
        self.board({"target": "chipa", "partition_table": "ref/pt.bin", "flash_size": "4MB"})
        with self.assertRaisesRegex(B.BoardError, r"not one ESP-IDF names \(XMB, YMB\)"):
            B.collect(self.b)
        self.assertEqual(self.run_main(), 1)

    def test_named_board_refused(self):
        self.board("x2")
        self.assertEqual(self.run_main(), 1)


if __name__ == "__main__":
    unittest.main()
