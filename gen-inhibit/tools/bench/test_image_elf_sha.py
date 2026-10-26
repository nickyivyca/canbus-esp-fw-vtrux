#!/usr/bin/env python3
"""Tests image_elf_sha.py on synthetic images, and on a real one against esptool.

    py -3.14 test_image_elf_sha.py [real_image.bin]     # exits 1 on any failure

Synthetic: a header, a segment header and a descriptor with a known hash at
+0x90, and distinct bytes on either side, so reading one byte early or late shows.
Bad image magic, bad descriptor magic and a short file must each be refused.
Real (optional): the value must equal what esptool's image_info prints as the
ELF file SHA256, an independent reader of the same field.
"""

import os
import re
import subprocess
import sys

import image_elf_sha as M

FAILS = []
# esptool is installed in the ESP-IDF virtualenv, not in the analysis Python.
ESPTOOL_PY = os.path.expanduser(r"~\.espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe")


def check(label, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" else ""))
    if not cond:
        FAILS.append(label)


def synth(sha, image_magic=0xE9, desc_magic=0xABCD5432):
    hdr = bytes([image_magic]) + bytes(range(1, 24))            # 24-byte image header
    seg = (0x3C000020).to_bytes(4, "little") + (256).to_bytes(4, "little")
    desc = bytearray(256)
    desc[0:4] = desc_magic.to_bytes(4, "little")
    desc[0x10:0x18] = b"88021c4\0"
    desc[0x30:0x3A] = b"wican-fw\0\0"
    desc[0x8F] = 0xEE                                           # the byte before the hash
    desc[0x90:0xB0] = sha
    desc[0xB0] = 0xDD                                           # the byte after it
    return hdr + seg + bytes(desc)


def main():
    sha = bytes(range(0x40, 0x60))
    try:
        r = M.read_desc(synth(sha))
    except ValueError as e:
        check("a well-formed synthetic image is read", False, e)
        print("%d failure(s)" % len(FAILS))
        return 1
    check("hash read at 0x90 of the descriptor", r["elf_sha256"] == sha.hex(), r["elf_sha256"])
    check("recorded at file offset 0xB0", r["file_offset"] == "0xB0", r["file_offset"])
    check("bytes recorded", r["bytes"].split()[0] == "40" and r["bytes"].split()[-1] == "5f")
    check("version string", r["version"] == "88021c4", r["version"])
    for label, kw in [("bad image magic refused", {"image_magic": 0xE8}),
                      ("bad descriptor magic refused", {"desc_magic": 0xABCD5433})]:
        try:
            M.read_desc(synth(sha, **kw))
            check(label, False)
        except ValueError as e:
            check(label, True, e)
    try:
        M.read_desc(synth(sha)[:0xC0])
        check("short file refused", False)
    except ValueError:
        check("short file refused", True)

    if len(sys.argv) > 1:
        p = sys.argv[1]
        mine = M.read_file(p)["elf_sha256"]
        out = subprocess.run([ESPTOOL_PY, "-m", "esptool", "--chip", "esp32c3", "image_info", "--version", "2", p],
                             capture_output=True, text=True)
        txt = out.stdout + out.stderr
        m = re.search(r"ELF file SHA256:\s*([0-9a-f]{64})", txt, re.I)
        check("real image: esptool prints an ELF SHA256", m is not None, txt[-200:])
        if m:
            check("real image: our reader equals esptool's", m.group(1).lower() == mine, (mine, m.group(1)))

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
