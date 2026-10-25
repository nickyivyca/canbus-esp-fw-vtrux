"""ESP-IDF's binary partition table, read from its published format (for R21).

Entries are 32 bytes: magic AA 50, type, subtype, offset (u32 LE), size (u32 LE), label (16 bytes,
NUL-padded), flags (u32 LE). An optional MD5 entry (EB EB, 14 x FF, then the MD5 of the entries
before it) follows; the table ends at an entry of 32 x FF. The table occupies at most 0xC00 bytes.
"""
import hashlib
import struct

ENTRY = 32
MAX_LEN = 0xC00
APP = 0x00
DATA = 0x01


class TableError(ValueError):
    pass


def read(b):
    """[(type, subtype, offset, size, label, flags)] from a partition-table .bin; TableError if malformed."""
    if len(b) > MAX_LEN or len(b) % ENTRY:
        raise TableError("length %d" % len(b))
    out = []
    for i in range(0, len(b), ENTRY):
        e = b[i:i + ENTRY]
        if e == b"\xff" * ENTRY:
            if any(c != 0xFF for c in b[i:]):
                raise TableError("bytes after the end marker at %#x" % i)
            return out
        if e[:2] == b"\xeb\xeb":
            if e[2:16] != b"\xff" * 14:
                raise TableError("MD5 entry padding at %#x" % i)
            if e[16:] != hashlib.md5(b[:i]).digest():
                raise TableError("MD5 mismatch at %#x" % i)
            continue
        if e[:2] != b"\xaa\x50":
            raise TableError("bad magic at %#x" % i)
        t, st, off, size = struct.unpack_from("<BBII", e, 2)
        label = e[12:28].split(b"\x00", 1)[0].decode("ascii", "replace")
        flags = struct.unpack_from("<I", e, 28)[0]
        out.append((t, st, off, size, label, flags))
    raise TableError("no end marker")


def apps(entries):
    """The app partitions: [(label, subtype, offset, size)]."""
    return [(lab, st, off, size) for t, st, off, size, lab, _f in entries if t == APP]

