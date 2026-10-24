"""Read an ESP-IDF application image (.bin): its app descriptor, its image hash
(SPEC R1) and its code hash (SPEC R2). The image is only read, never patched.

The layout read here (ESP-IDF 5.4, every target):

    0x00  image header, 24 bytes: magic 0xE9, segment count, SPI mode and
          speed/size, entry address, then the extended header, whose bytes
          12-13 are the chip id and byte 23 is hash_appended
    0x18  segments, each an 8-byte header (load address, data length, both
          little-endian uint32) and its data
    then  zero padding up to the checksum byte, which sits at the last byte
          of a 16-byte block (esptool's align_file_position), then, when
          hash_appended is 1, the SHA-256 of every byte before it

The app descriptor (esp_app_desc_t, 256 bytes) is at the start of segment 0's
data, so at file offset 0x20:

    +0x00 magic_word 0xABCD5432   +0x50 time[16]
    +0x04 secure_version          +0x60 date[16]
    +0x08 reserv1[2]              +0x70 idf_ver[32]
    +0x10 version[32]             +0x90 app_elf_sha256[32]
    +0x30 project_name[32]        +0xB0 efuse revs, mmu page size, reserved

R1. The image hash is app_elf_sha256 as hex: the SHA-256 of the ELF, which
ESP-IDF embeds when it makes the .bin.

R2. The code hash is the SHA-256 of the whole .bin with these set to zero:
the descriptor's version, time, date and app_elf_sha256 fields, the
checksum byte, and the trailing SHA-256 when there is one. Zero is the blank;
the padding before the checksum is zero already and is not blanked.

Fails closed. An image that does not parse to exactly its own length, whose
descriptor magic is wrong, or whose checksum or trailing SHA-256 does not
match its bytes raises ImageError, never a hash. A signed image (secure boot
signature after the SHA-256) is refused for the same reason: its length is
not the one the segments give.
"""

import hashlib
import struct

IMAGE_MAGIC = 0xE9
DESC_MAGIC = 0xABCD5432
HEADER_LEN = 0x18
SEG_HEADER_LEN = 8
DESC_LEN = 0x100
CHECKSUM_SEED = 0xEF

# Descriptor fields: (offset in the descriptor, length).
DESC_VERSION = (0x10, 32)
DESC_PROJECT_NAME = (0x30, 32)
DESC_TIME = (0x50, 16)
DESC_DATE = (0x60, 16)
DESC_IDF_VER = (0x70, 32)
DESC_ELF_SHA256 = (0x90, 32)

# What R2 blanks inside the descriptor.
CODE_HASH_BLANKS = (DESC_VERSION, DESC_TIME, DESC_DATE, DESC_ELF_SHA256)

# esp_chip_id_t, for reading only.
CHIP_IDS = {0x0000: "esp32", 0x0002: "esp32s2", 0x0005: "esp32c3",
            0x0009: "esp32s3", 0x000C: "esp32c2", 0x000D: "esp32c6",
            0x0010: "esp32h2", 0x0012: "esp32p4", 0x0014: "esp32c61",
            0x0017: "esp32c5"}


class ImageError(ValueError):
    """The bytes are not an ESP-IDF app image this module can read."""


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


class AppImage:
    """One parsed app image. Build it with AppImage.read(path) or
    AppImage(data). Attributes:

        data          the image bytes
        chip_id       esp_chip_id_t from the extended header
        chip          its name, or None if unknown
        segments      [(file offset of the data, load address, length)]
        desc_offset   file offset of the app descriptor
        checksum_offset, sha256_offset (None without an appended hash)
    """

    def __init__(self, data, verify=True):
        self.data = bytes(data)
        self._parse()
        if verify:
            self.verify()

    @classmethod
    def read(cls, path, verify=True):
        with open(path, "rb") as f:
            return cls(f.read(), verify=verify)

    def _parse(self):
        b = self.data
        if len(b) < HEADER_LEN + SEG_HEADER_LEN + DESC_LEN:
            raise ImageError("too short for an app image (%d bytes)" % len(b))
        if b[0] != IMAGE_MAGIC:
            raise ImageError("image magic is 0x%02x, not 0x%02x"
                             % (b[0], IMAGE_MAGIC))
        nseg = b[1]
        if nseg == 0:
            raise ImageError("image header gives no segments")
        self.chip_id = struct.unpack_from("<H", b, 12)[0]
        self.chip = CHIP_IDS.get(self.chip_id)
        hash_appended = b[23]
        if hash_appended not in (0, 1):
            raise ImageError("hash_appended byte is %d, not 0 or 1"
                             % hash_appended)
        self.segments = []
        o = HEADER_LEN
        for i in range(nseg):
            if o + SEG_HEADER_LEN > len(b):
                raise ImageError("segment %d header runs past the end" % i)
            addr, ln = struct.unpack_from("<II", b, o)
            o += SEG_HEADER_LEN
            if o + ln > len(b):
                raise ImageError("segment %d data runs past the end" % i)
            self.segments.append((o, addr, ln))
            o += ln
        if self.segments[0][2] < DESC_LEN:
            raise ImageError("segment 0 is too short to hold the descriptor")
        self.desc_offset = self.segments[0][0]
        magic = struct.unpack_from("<I", b, self.desc_offset)[0]
        if magic != DESC_MAGIC:
            raise ImageError("no app descriptor: magic at 0x%x is 0x%08x"
                             % (self.desc_offset, magic))
        self.checksum_offset = o + (15 - o % 16)
        end = self.checksum_offset + 1
        self.sha256_offset = end if hash_appended else None
        if hash_appended:
            end += 32
        if end != len(b):
            raise ImageError("the segments give a %d-byte image and the file "
                             "has %d bytes" % (end, len(b)))
        pad = b[o:self.checksum_offset]
        if pad.strip(b"\0"):
            raise ImageError("non-zero padding before the checksum")

    def verify(self):
        """The checksum and, when present, the trailing SHA-256 match the
        bytes. Raises ImageError otherwise."""
        b = self.data
        x = CHECKSUM_SEED
        for off, _addr, ln in self.segments:
            for v in b[off:off + ln]:
                x ^= v
        if b[self.checksum_offset] != x:
            raise ImageError("checksum byte is 0x%02x, the segments give 0x%02x"
                             % (b[self.checksum_offset], x))
        if self.sha256_offset is not None:
            want = hashlib.sha256(b[:self.sha256_offset]).digest()
            if b[self.sha256_offset:] != want:
                raise ImageError("trailing SHA-256 does not match the image")

    def _field(self, field):
        off, ln = field
        s = self.desc_offset + off
        return self.data[s:s + ln]

    @property
    def version(self):
        return _cstr(self._field(DESC_VERSION))

    @property
    def project_name(self):
        return _cstr(self._field(DESC_PROJECT_NAME))

    @property
    def time(self):
        return _cstr(self._field(DESC_TIME))

    @property
    def date(self):
        return _cstr(self._field(DESC_DATE))

    @property
    def idf_ver(self):
        return _cstr(self._field(DESC_IDF_VER))

    @property
    def image_hash(self):
        """R1: the ELF's SHA-256 as ESP-IDF embedded it, 64 hex digits."""
        return self._field(DESC_ELF_SHA256).hex()

    def code_hash_blanks(self):
        """[(file offset, length)] of every range R2 blanks, in file order."""
        out = [(self.desc_offset + off, ln) for off, ln in CODE_HASH_BLANKS]
        out.append((self.checksum_offset, 1))
        if self.sha256_offset is not None:
            out.append((self.sha256_offset, 32))
        return sorted(out)

    @property
    def code_hash(self):
        """R2: SHA-256 of the image with code_hash_blanks() zeroed, 64 hex
        digits. Computed on a copy; self.data is not changed."""
        b = bytearray(self.data)
        for off, ln in self.code_hash_blanks():
            b[off:off + ln] = bytes(ln)
        return hashlib.sha256(b).hexdigest()

    def summary(self):
        """A JSON-ready dict of what this module reads from the image."""
        return {
            "size": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "image_hash": self.image_hash,
            "code_hash": self.code_hash,
            "chip_id": self.chip_id,
            "chip": self.chip,
            "version": self.version,
            "project_name": self.project_name,
            "time": self.time,
            "date": self.date,
            "idf_ver": self.idf_ver,
            "segments": [{"offset": o, "load_address": a, "length": n}
                         for o, a, n in self.segments],
            "hash_appended": self.sha256_offset is not None,
        }


def image_hash(path):
    """R1 for the .bin at path."""
    return AppImage.read(path).image_hash


def code_hash(path):
    """R2 for the .bin at path. The bench toolkit's record writer logs this
    for every image a run used (R15)."""
    return AppImage.read(path).code_hash


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
