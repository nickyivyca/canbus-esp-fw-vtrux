"""Tests of the image library (vtrux_build.image): SPEC R1 and R2.

    python -B shared/tools/tests/test_image.py

On synthetic images (imagekit.py). Run against real IDF 5.4.1 images by
hand when the layout is in doubt: `python -B -m vtrux_build image <bin>`.
"""

import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from imagekit import descriptor, make_image, refresh_trailer  # noqa: E402
from vtrux_build.image import AppImage, ImageError              # noqa: E402


def poke(data, off, new):
    b = bytearray(data)
    b[off:off + len(new)] = new
    return bytes(b)


class ImageHash(unittest.TestCase):
    """R1: the ELF SHA-256 the image embeds."""

    def test_reads_embedded_elf_sha(self):
        sha = hashlib.sha256(b"some elf").digest()
        data, _ = make_image(desc=descriptor(elf_sha256=sha))
        self.assertEqual(AppImage(data).image_hash, sha.hex())

    def test_fields_and_chip(self):
        data, _ = make_image(desc=descriptor(version="v1-2-gabc",
                                             project_name="p", idf_ver="v5.4.1"),
                             chip_id=0x0009)
        a = AppImage(data)
        self.assertEqual((a.version, a.project_name, a.idf_ver, a.chip),
                         ("v1-2-gabc", "p", "v5.4.1", "esp32s3"))


class CodeHash(unittest.TestCase):
    """R2: the blanked fields, and only those, leave the code hash."""

    def setUp(self):
        self.data, self.offs = make_image()
        self.base = AppImage(self.data).code_hash

    def test_blanked_fields_do_not_change_it(self):
        d = self.offs["desc"]
        for off, ln in ((0x10, 32), (0x50, 16), (0x60, 16), (0x90, 32)):
            edited = refresh_trailer(poke(self.data, d + off, b"\x5a" * ln),
                                     self.offs)
            self.assertEqual(AppImage(edited).code_hash, self.base,
                             "descriptor +0x%x changed the code hash" % off)
            self.assertNotEqual(edited, self.data)

    def test_different_version_same_code(self):
        a, _ = make_image(desc=descriptor(version="aaaaaaa", date="Jan  1 2026",
                                          time_="00:00:01"))
        b, _ = make_image(desc=descriptor(version="bbbbbbb-dirty",
                                          date="Oct 23 2026", time_="23:59:59"))
        self.assertNotEqual(AppImage(a).image_hash, AppImage(b).image_hash)
        self.assertEqual(AppImage(a).code_hash, AppImage(b).code_hash)

    def test_every_other_byte_changes_it(self):
        blanks = set()
        for off, ln in AppImage(self.data).code_hash_blanks():
            blanks.update(range(off, off + ln))
        seen = set()
        for off in range(len(self.data)):
            if off in blanks:
                continue
            b = bytearray(self.data)
            b[off] ^= 0x01
            try:
                h = AppImage(bytes(b), verify=False).code_hash
            except ImageError:
                h = None        # a structural byte: no hash at all
            self.assertNotEqual(h, self.base, "byte 0x%x is not covered" % off)
            seen.add(off)
        self.assertEqual(len(seen) + len(blanks), len(self.data))

    def test_blank_list_is_exactly_the_spec(self):
        a = AppImage(self.data)
        d = self.offs["desc"]
        self.assertEqual(a.code_hash_blanks(), sorted([
            (d + 0x10, 32), (d + 0x50, 16), (d + 0x60, 16), (d + 0x90, 32),
            (self.offs["checksum"], 1), (self.offs["sha256"], 32)]))

    def test_blanks_are_zero(self):
        a = AppImage(self.data)
        b = bytearray(self.data)
        for off, ln in a.code_hash_blanks():
            b[off:off + ln] = bytes(ln)
        self.assertEqual(a.code_hash, hashlib.sha256(bytes(b)).hexdigest())

    def test_without_appended_hash(self):
        data, offs = make_image(hash_appended=False)
        a = AppImage(data)
        self.assertIsNone(a.sha256_offset)
        self.assertEqual(a.code_hash_blanks()[-1], (offs["checksum"], 1))

    def test_image_not_changed(self):
        a = AppImage(self.data)
        a.code_hash
        self.assertEqual(a.data, self.data)


class FailsClosed(unittest.TestCase):

    def setUp(self):
        self.data, self.offs = make_image()

    def bad(self, data, verify=True):
        with self.assertRaises(ImageError):
            AppImage(data, verify=verify).code_hash

    def test_image_magic(self):
        self.bad(poke(self.data, 0, b"\xE8"))

    def test_descriptor_magic(self):
        self.bad(refresh_trailer(poke(self.data, self.offs["desc"], b"\0"),
                                 self.offs), verify=False)

    def test_extra_bytes(self):
        self.bad(self.data + b"\0" * 16, verify=False)

    def test_truncated(self):
        self.bad(self.data[:-1], verify=False)

    def test_checksum(self):
        b = bytearray(self.data)
        b[self.offs["checksum"]] ^= 0xFF
        b[self.offs["sha256"]:] = hashlib.sha256(
            bytes(b[:self.offs["sha256"]])).digest()
        self.bad(bytes(b))

    def test_trailing_sha(self):
        b = bytearray(self.data)
        b[-1] ^= 0xFF
        self.bad(bytes(b))

    def test_nonzero_padding(self):
        o = self.offs["checksum"] - 1
        if self.data[o] != 0:
            self.skipTest("no padding in this layout")
        self.bad(poke(self.data, o, b"\x01"), verify=False)

    def test_segment_past_end(self):
        b = bytearray(self.data)
        b[24 + 4:24 + 8] = (0x7FFFFFFF).to_bytes(4, "little")
        self.bad(bytes(b), verify=False)

    def test_hash_appended_flag(self):
        self.bad(poke(self.data, 23, b"\x02"), verify=False)


if __name__ == "__main__":
    unittest.main()
