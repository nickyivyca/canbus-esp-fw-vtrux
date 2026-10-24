"""Tests of the release build record (vtrux_build.record): SPEC R13.

    python -B shared/tools/tests/test_record.py
"""

import hashlib
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scratch                                  # noqa: E402
from imagekit import descriptor, make_image     # noqa: E402
from vtrux_build import record                  # noqa: E402
from vtrux_build.image import AppImage          # noqa: E402

FIELDS = {"commit": "c" * 40, "host_os": {"system": "Test"}}


def built(name, elf=b"\x7fELF fake", version="abc1234"):
    d = scratch.fresh(name)
    data, _ = make_image(desc=descriptor(
        version=version, elf_sha256=hashlib.sha256(elf).digest()))
    b = scratch.write(os.path.join(d, "build", "proj_%s.bin" % version), data)
    e = scratch.write(os.path.join(d, "build", "proj_%s.elf" % version), elf)
    return d, b, e


class Write(unittest.TestCase):

    def test_named_by_image_hash_with_bin_and_elf(self):
        d, b, e = built("rec_ok")
        dest = os.path.join(d, "records")
        p = record.write("proj", b, e, FIELDS, dest=dest)
        ih = AppImage.read(b).image_hash
        self.assertEqual(os.path.basename(p), ih + ".json")
        self.assertEqual(sorted(os.listdir(dest)),
                         sorted([ih + ".bin", ih + ".elf", ih + ".json"]))
        r = record.read(p)
        self.assertEqual(r["image_hash"], ih)
        self.assertEqual(r["code_hash"], AppImage.read(b).code_hash)
        self.assertEqual(r["image_size"], os.path.getsize(b))
        self.assertEqual(r["commit"], "c" * 40)
        self.assertEqual(r["project"], "proj")
        self.assertEqual(r["image"]["built_as"], "proj_abc1234.bin")
        with open(os.path.join(dest, ih + ".bin"), "rb") as f, \
                open(b, "rb") as g:
            self.assertEqual(f.read(), g.read())

    def test_never_edited(self):
        d, b, e = built("rec_again")
        dest = os.path.join(d, "records")
        p = record.write("proj", b, e, FIELDS, dest=dest)
        before = scratch.read(p)
        mtime = os.path.getmtime(p)
        p2 = record.write("proj", b, e, dict(FIELDS, commit="d" * 40),
                          dest=dest)
        self.assertEqual(p2, p)
        self.assertEqual(scratch.read(p), before)
        self.assertEqual(os.path.getmtime(p), mtime)

    def test_existing_record_with_other_bytes(self):
        d, b, e = built("rec_clash")
        dest = os.path.join(d, "records")
        p = record.write("proj", b, e, FIELDS, dest=dest)
        scratch.write(p[:-5] + ".bin", b"tampered")
        before = scratch.read(p)
        with self.assertRaises(record.RecordError):
            record.write("proj", b, e, FIELDS, dest=dest)
        self.assertEqual(scratch.read(p), before)

    def test_left_over_bin_without_record(self):
        d, b, e = built("rec_partial")
        dest = os.path.join(d, "records")
        ih = AppImage.read(b).image_hash
        scratch.write(os.path.join(dest, ih + ".bin"), scratch.read(b))
        self.assertTrue(os.path.isfile(record.write("proj", b, e, FIELDS,
                                                    dest=dest)))
        d, b, e = built("rec_partial_bad")
        dest = os.path.join(d, "records")
        scratch.write(os.path.join(dest, ih + ".bin"), b"partial")
        with self.assertRaises(record.RecordError):
            record.write("proj", b, e, FIELDS, dest=dest)
        self.assertFalse(os.path.exists(os.path.join(dest, ih + ".json")))

    def test_elf_must_be_the_embedded_one(self):
        d, b, e = built("rec_elf")
        scratch.write(e, b"\x7fELF another")
        dest = os.path.join(d, "records")
        with self.assertRaises(record.RecordError):
            record.write("proj", b, e, FIELDS, dest=dest)
        self.assertFalse(os.path.exists(dest) and os.listdir(dest))

    def test_fields_cannot_override_the_writers(self):
        d, b, e = built("rec_override")
        with self.assertRaises(record.RecordError):
            record.write("proj", b, e, dict(FIELDS, image_hash="0" * 64),
                         dest=os.path.join(d, "records"))


class BuildsDir(unittest.TestCase):

    def test_under_reverse_it_dir(self):
        root = scratch.fresh("rec_rid")
        os.makedirs(os.path.join(root, "firmware"))
        with mock.patch.dict(os.environ, {"REVERSE_IT_DIR": root}):
            self.assertEqual(record.builds_dir("gen-inhibit"),
                             os.path.join(root, "firmware", "builds",
                                          "gen-inhibit"))

    def test_unset_or_wrong(self):
        env = dict(os.environ)
        env.pop("REVERSE_IT_DIR", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(record.RecordError):
                record.builds_dir("p")
        with mock.patch.dict(os.environ, {"REVERSE_IT_DIR": scratch.fresh(
                "rec_nofw")}):
            with self.assertRaises(record.RecordError):
                record.builds_dir("p")


class Toolchain(unittest.TestCase):

    def test_first_line(self):
        cc = scratch.write(os.path.join(scratch.fresh("rec_cc"), "gcc.exe"), b"")
        tc = record.toolchain(cc, lambda a: (0, "gcc (crosstool) 14.2.0\nmore\n"))
        self.assertEqual(tc, {"compiler": "gcc.exe",
                              "version": "gcc (crosstool) 14.2.0"})

    def test_refuses(self):
        cc = scratch.write(os.path.join(scratch.fresh("rec_cc2"), "gcc"), b"")
        for c, run in ((None, None), (cc + ".missing", None),
                       (cc, lambda a: (1, "x")), (cc, lambda a: (0, ""))):
            with self.assertRaises(record.RecordError):
                record.toolchain(c, run)


if __name__ == "__main__":
    unittest.main()
