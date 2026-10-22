#!/usr/bin/env python3
"""Read app_elf_sha256 out of an ESP32 app image file, with no ESP-IDF code.

    py -3.14 image_elf_sha.py build/wican-fw_obd_88021c4_man.bin [...]

Spec 11, "The page names the binary it runs" (added 2026-10-14): /gen_inhibit's
`elf_sha256` must equal the value in the flashed image file's app descriptor. The
reviewer asked for this side to be read by our own parser, not the device's code,
with the bytes recorded.

Layout, from the ESP-IDF image format (not from the firmware under test):
  file 0x00  esp_image_header_t, 24 bytes; byte 0 is the magic 0xE9
  file 0x18  the first segment header, 8 bytes (load address, length)
  file 0x20  esp_app_desc_t, the first thing in the first segment:
     +0x00 magic_word u32 LE = 0xABCD5432
     +0x04 secure_version, +0x08 reserv1[2]
     +0x10 version[32], +0x30 project_name[32], +0x50 time[16], +0x60 date[16],
     +0x70 idf_ver[32]
     +0x90 app_elf_sha256[32]
So the hash is the 32 bytes at file offset 0xB0. Both magics are checked, so a
file that is not an app image (a bootloader, a partition table, a truncated file)
is refused rather than read at the wrong offset.
"""

import hashlib
import json
import sys

IMAGE_MAGIC = 0xE9
DESC_OFF = 0x20
DESC_MAGIC = 0xABCD5432
SHA_OFF_IN_DESC = 0x90


def read_desc(data):
    if len(data) < DESC_OFF + SHA_OFF_IN_DESC + 32:
        raise ValueError("file too short for an app descriptor: %d bytes" % len(data))
    if data[0] != IMAGE_MAGIC:
        raise ValueError("image magic 0x%02X, not 0xE9" % data[0])
    magic = int.from_bytes(data[DESC_OFF:DESC_OFF + 4], "little")
    if magic != DESC_MAGIC:
        raise ValueError("app descriptor magic 0x%08X at 0x%X, not 0x%08X" % (magic, DESC_OFF, DESC_MAGIC))
    d = data[DESC_OFF:]
    cstr = lambda b: b.split(b"\0", 1)[0].decode("ascii", "replace")
    sha = d[SHA_OFF_IN_DESC:SHA_OFF_IN_DESC + 32]
    return {"elf_sha256": sha.hex(), "file_offset": "0x%X" % (DESC_OFF + SHA_OFF_IN_DESC),
            "bytes": " ".join("%02x" % x for x in sha),
            "version": cstr(d[0x10:0x30]), "project_name": cstr(d[0x30:0x50]),
            "time": cstr(d[0x50:0x60]), "date": cstr(d[0x60:0x70]), "idf_ver": cstr(d[0x70:0x90])}


def read_file(path):
    data = open(path, "rb").read()
    r = read_desc(data)
    r.update(path=path, size=len(data), file_sha256=hashlib.sha256(data).hexdigest(),
             file_md5=hashlib.md5(data).hexdigest())
    return r


if __name__ == "__main__":
    out = [read_file(p) for p in sys.argv[1:]]
    print(json.dumps(out, indent=1))
