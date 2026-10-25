"""Synthetic ESP-IDF app images for the tests, written from the layout in
vtrux_build/image.py's docstring (esptool's elf2image output), so no real
firmware image is committed. make_image() returns the bytes and the file
offsets of every field a test needs to poke at.
"""

import hashlib
import struct

DESC_MAGIC = 0xABCD5432


def descriptor(version="abc1234", project_name="proj", time_="12:00:00",
               date="Oct 23 2026", idf_ver="v5.4.1", elf_sha256=None,
               secure_version=0):
    d = bytearray(256)
    struct.pack_into("<II", d, 0, DESC_MAGIC, secure_version)
    for off, ln, val in ((0x10, 32, version), (0x30, 32, project_name),
                         (0x50, 16, time_), (0x60, 16, date),
                         (0x70, 32, idf_ver)):
        b = val.encode("ascii")[:ln - 1]
        d[off:off + len(b)] = b
    sha = elf_sha256 if elf_sha256 is not None else hashlib.sha256(
        b"elf of " + version.encode()).digest()
    d[0x90:0xB0] = sha
    return bytes(d)


def make_image(segments=None, desc=None, chip_id=0x0005, hash_appended=True):
    """(bytes, offsets). segments: [(load address, data)]; segment 0's data
    is prefixed with the descriptor. offsets has "desc", "checksum" and
    "sha256" (None without a hash) and "segments": [data offsets]."""
    desc = desc if desc is not None else descriptor()
    segments = segments if segments is not None else [
        (0x3C000020, b"\x11\x22\x33" * 50), (0x40380000, bytes(range(256)))]
    segs = [(segments[0][0], desc + segments[0][1])] + list(segments[1:])
    hdr = bytearray(24)
    hdr[0] = 0xE9
    hdr[1] = len(segs)
    hdr[2], hdr[3] = 2, 0x1F
    struct.pack_into("<I", hdr, 4, 0x40380080)
    struct.pack_into("<H", hdr, 12, chip_id)
    hdr[23] = 1 if hash_appended else 0
    out = bytearray(hdr)
    seg_offsets = []
    x = 0xEF
    for addr, data in segs:
        out += struct.pack("<II", addr, len(data))
        seg_offsets.append(len(out))
        out += data
        for v in data:
            x ^= v
    pad = 15 - len(out) % 16
    out += bytes(pad)
    checksum = len(out)
    out.append(x)
    sha = None
    if hash_appended:
        sha = len(out)
        out += hashlib.sha256(bytes(out)).digest()
    return bytes(out), {"desc": seg_offsets[0], "checksum": checksum,
                        "sha256": sha, "segments": seg_offsets}


def refresh_trailer(data, offs):
    """Recompute the checksum and SHA-256 after a test edited segment
    bytes, so the edited image is still self-consistent."""
    b = bytearray(data)
    x = 0xEF
    i = 24
    for _ in range(b[1]):
        ln = struct.unpack_from("<I", b, i + 4)[0]
        for v in b[i + 8:i + 8 + ln]:
            x ^= v
        i += 8 + ln
    b[offs["checksum"]] = x
    if offs["sha256"] is not None:
        b[offs["sha256"]:] = hashlib.sha256(bytes(b[:offs["sha256"]])).digest()
    return bytes(b)
