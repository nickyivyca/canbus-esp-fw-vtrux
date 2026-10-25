"""The tester's own reading of SPEC R1, R2 and R7, written from SPEC.md and ESP-IDF's
published app image format, never from vtrux_build's source, so the verification
compares two independent readings (as shared/bench's layout_hash_crosscheck does).

The image format (ESP-IDF "App Image Format"): a 24-byte header (magic 0xE9, the
segment count at byte 1, hash_appended at byte 23); each segment an 8-byte header
(load address, length) and its data; zero padding up to the byte before a 16-byte
boundary, then a checksum byte (0xEF XOR every segment data byte); then, when
hash_appended is 1, the SHA-256 of everything before it. The app descriptor
(esp_app_desc_t, magic 0xABCD5432) opens the first segment's data, at 0x20: version
at 0x30 (32 bytes), project_name 0x50 (32), time 0x70 (16), date 0x80 (16), idf_ver
0x90 (32), app_elf_sha256 0xb0 (32).

Gen-inhibit tester, 2026-10-24 (C6f verification).
"""
import hashlib
import os
import struct
import time

IMAGE_MAGIC = 0xE9
DESC_MAGIC = 0xABCD5432
DESC = 0x20
FIELDS = {"version": (0x30, 32), "project_name": (0x50, 32), "time": (0x70, 16), "date": (0x80, 16),
          "idf_ver": (0x90, 32), "elf_sha256": (0xB0, 32)}
R2_FIELDS = ("version", "time", "date", "elf_sha256")      # SPEC R2: blanked descriptor fields


class Bad(Exception):
    pass


def layout(data):
    """(segments [(offset of data, length)], checksum offset, sha offset or None); raises Bad
    unless the image parses to exactly its length with a good checksum and SHA-256."""
    if len(data) < 24 or data[0] != IMAGE_MAGIC:
        raise Bad("no image header")
    pos, segs, x = 24, [], 0xEF
    for _ in range(data[1]):
        if pos + 8 > len(data):
            raise Bad("segment header past the end")
        _addr, n = struct.unpack_from("<II", data, pos)
        pos += 8
        if pos + n > len(data):
            raise Bad("segment data past the end")
        segs.append((pos, n))
        for b in data[pos:pos + n]:
            x ^= b
        pos += n
    cks = pos + (15 - pos % 16)
    if cks >= len(data) or any(data[pos:cks]):
        raise Bad("no checksum byte, or padding not zero")
    if data[cks] != x:
        raise Bad("checksum")
    sha = None
    end = cks + 1
    if data[23] == 1:
        sha, end = end, end + 32
        if end > len(data) or hashlib.sha256(data[:sha]).digest() != data[sha:end]:
            raise Bad("SHA-256")
    if end != len(data):
        raise Bad("%d bytes after the image" % (len(data) - end))
    if not segs or struct.unpack_from("<I", data, DESC)[0] != DESC_MAGIC:
        raise Bad("no app descriptor")
    return segs, cks, sha


def blanks(data):
    """The (offset, length) ranges SPEC R2 blanks."""
    _segs, cks, sha = layout(data)
    out = [FIELDS[f] for f in R2_FIELDS] + [(cks, 1)]
    if sha is not None:
        out.append((sha, 32))
    return sorted(out)


def code_hash(data):
    """SPEC R2: the .bin's SHA-256 with the blanked ranges zeroed (a copy; the image is read)."""
    b = bytearray(data)
    for off, n in blanks(data):
        b[off:off + n] = bytes(n)
    return hashlib.sha256(bytes(b)).hexdigest()


def image_hash(data):
    """SPEC R1: the ELF's SHA-256 as the image embeds it."""
    layout(data)
    off, n = FIELDS["elf_sha256"]
    return data[off:off + n].hex()


def field(data, name):
    off, n = FIELDS[name]
    return data[off:off + n]


def build(segments=None, version=b"abc1234", project=b"proj", tm=b"00:00:00", dt=b"Jan  1 2026",
          idf=b"v5.4.1", elf_sha=bytes(range(32)), chip_id=5, hash_appended=1):
    """A valid synthetic app image: the descriptor opens segment 0 (followed by its data)."""
    def pad(v, n):
        return v + bytes(n - len(v))
    desc = (struct.pack("<II8x", DESC_MAGIC, 0) + pad(version, 32) + pad(project, 32) + pad(tm, 16) +
            pad(dt, 16) + pad(idf, 32) + elf_sha + bytes(256 - 0xB0 - 32 + DESC))[:256]
    segments = segments or [bytes((i * 7 + 3) % 256 for i in range(200)), bytes((i * 13 + 1) % 256 for i in range(333))]
    segs = [desc + segments[0]] + list(segments[1:])
    hdr = struct.pack("<BBBBI", IMAGE_MAGIC, len(segs), 2, 0x1F, 0x40380000)
    hdr += struct.pack("<B3sHBHH4sB", 0xEE, b"\0\0\0", chip_id, 0, 0, 0xFFFF, b"\0" * 4, hash_appended)
    body, x = bytearray(hdr), 0xEF
    for i, s in enumerate(segs):
        body += struct.pack("<II", 0x3C000000 + 0x10000 * i, len(s)) + s
        for b in s:
            x ^= b
    body += bytes(15 - len(body) % 16) + bytes([x])
    if hash_appended:
        body += hashlib.sha256(bytes(body)).digest()
    return bytes(body)


def with_field(data, name, value):
    """data with one descriptor field set, rebuilt so it stays a valid image."""
    segs, cks, sha = layout(data)
    b = bytearray(data[:cks])
    off, n = FIELDS[name]
    b[off:off + n] = value + bytes(n - len(value))
    return reseal(b, segs, data[23] == 1)


def with_byte(data, offset, value):
    """data with one byte changed inside a segment, resealed."""
    segs, cks, _sha = layout(data)
    b = bytearray(data[:cks])
    b[offset] = value
    return reseal(b, segs, data[23] == 1)


def reseal(b, segs, hashed):
    x = 0xEF
    for off, n in segs:
        for v in b[off:off + n]:
            x ^= v
    b = bytes(b) + bytes([x])
    return b + hashlib.sha256(b).digest() if hashed else b


def scratch(name):
    """This run's own folder in the worktree's gitignored scratch/ (AGENTS.md "Files and folders")."""
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("not in a git worktree")
        d = os.path.dirname(d)
    d = os.path.join(d, "scratch", "shared-tools-verification", name,
                     "%s_%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid()))
    os.makedirs(d)
    return d
