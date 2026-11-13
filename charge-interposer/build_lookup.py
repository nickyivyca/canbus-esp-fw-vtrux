"""Identify the firmware image a board is running, from 0x7F7.

The interposer reports its build in `0x7F7` B0-B3: the first four bytes
of the application ELF's SHA-256 (spec 8.2). This maps that back to one of
the images the caller says may be on the board -- which image, which
variant, and in particular whether it is the WITNESS build -- by reading
each image's own ESP-IDF application descriptor (`esp_app_desc_t`): its
ELF SHA-256 and its project name, `interposer_<variant>` (spec 8.2, from
port step 2a; shared/tools R1, R4).

PORT STEP 2a: NO MANIFEST. Before 2a this looked the id up in
`firmware/builds/manifest.json`, which the PlatformIO build wrote and which
retired with it (reviewer, 2026-11-03). The images carry everything the
manifest held that identification needs, in their own bytes, so a record
cannot drift from the file it describes.

WHY A SHARED MODULE AND NOT A SNIPPET PER HARNESS. Every bench arm from
here on records the looked-up entry before it starts, and any arm that
needs the witness image asserts the variant rather than assuming it. A
copy of this logic in each harness would drift, and the drifted copy would
still return an answer. Import it.

EVERY FAILURE IS A REFUSAL WITH ITS OWN REASON, never a warning and
never a best guess. The ways this can fail are different problems and
must not be collapsed:

  NO_PAGES    no 0x7F7 frames were seen at all. The board is silent, or
              not bridging, or the dongle is on the wrong segment. This
              is NOT "unknown build" and must never read as one -- it
              is the commonest bench mistake and the one most easily
              mistaken for a firmware fault.
  NO_IMAGES   the caller named no candidate images, or none of them is an
              ESP-IDF application image. Then EVERY lookup would return
              UNKNOWN_ID, and a missing images folder would look like a
              wire problem.
  UNKNOWN_ID  a build id that none of the candidate images carries. A real
              image that is not the one expected -- flashed from somewhere
              else, or from another commit.
  COLLISION   two different images share this 4-byte prefix, or one ELF
              is named as two variants. It cannot say which, so it says
              so instead of picking.

A caller that wants a verdict should treat anything but OK as a TEST
FAULT: the stimulus of the measurement -- knowing what is on the board
-- was not established, so no device verdict is available.

THE IDENTITY IS THE ELF HASH (reviewer's ruling, 2026-10-07; the user
ruled the same way for the spec on 2026-10-08 -- whether two images are
the same is decided by the ELF hash, never by a file name or a source
digest). Several candidate files can carry the same ELF (a copy in two
folders); an OK result carries every one of them (`entries`), and refuses
if they disagree on the variant, which the same bytes cannot.
`elf_sha256_of_bin()` reads the hash out of an image file itself, so what
is about to be flashed can be identified by its bytes, never its name.
"""

import os

OK = "OK"
NO_PAGES = "NO_PAGES"
NO_IMAGES = "NO_IMAGES"
UNKNOWN_ID = "UNKNOWN_ID"
COLLISION = "COLLISION"

BUILD_PAGE_ID = 0x7F7
# spec 8.2: the image's project name is interposer_<variant>, one of these
PROJECT_PREFIX = "interposer_"
VARIANTS = ("bridge", "witness", "truck", "selftest", "diag")
WITNESS_VARIANT = "witness"


class Lookup(object):
    """The result. Falsy unless `reason` is OK, so `if not r:` is safe."""

    def __init__(self, reason, entry=None, detail="", build_id=None,
                 entries=None):
        self.reason = reason
        self.entry = entry              # the first match, kept for callers
        self.entries = list(entries or ([entry] if entry else []))
        self.detail = detail
        self.build_id = build_id

    @property
    def elf_sha256(self):
        return (self.entry or {}).get("elf_sha256")

    def __bool__(self):
        return self.reason == OK

    __nonzero__ = __bool__          # py2-style callers, harmless here

    @property
    def variant(self):
        return (self.entry or {}).get("variant")

    @property
    def is_witness(self):
        return self.variant == WITNESS_VARIANT

    def __str__(self):
        if self.reason == OK:
            e = self.entry
            files = "; ".join(x.get("image", "?") for x in self.entries)
            return ("build %s = ELF %s, %s (variant %s), version %s, %s -- "
                    "%d file(s): %s"
                    % (self.build_id, (e.get("elf_sha256") or "?")[:16],
                       e.get("project_name"), e.get("variant"),
                       e.get("version"), e.get("idf_ver"),
                       len(self.entries), files))
        return "%s: %s" % (self.reason, self.detail)


APP_DESC_MAGIC = 0xABCD5432
# esp_app_desc_t sits at the start of the first segment: 24-byte image
# header + 8-byte segment header, then magic(4) secure_version(4)
# reserv1(8) version(32) project_name(32) time(16) date(16) idf_ver(32),
# then app_elf_sha256[32] -- what `esptool image-info` prints as "ELF file
# SHA256" and what the board puts in 0x7F7.
_APP_DESC_AT = 24 + 8
_VERSION_AT = _APP_DESC_AT + 4 + 4 + 8
_PROJECT_AT = _VERSION_AT + 32
_TIME_AT = _PROJECT_AT + 32
_DATE_AT = _TIME_AT + 16
_IDF_VER_AT = _DATE_AT + 16
_ELF_SHA_AT = _IDF_VER_AT + 32


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def app_desc_of_bin(path):
    """The application descriptor an image file carries, or None if it is
    not an ESP-IDF application image: {"version", "project_name", "time",
    "date", "idf_ver", "elf_sha256"} (strings; the hash as 64 hex)."""
    try:
        with open(path, "rb") as f:
            head = f.read(_ELF_SHA_AT + 32)
    except IOError:
        return None
    if len(head) < _ELF_SHA_AT + 32:
        return None
    magic = (head[_APP_DESC_AT] | (head[_APP_DESC_AT + 1] << 8)
             | (head[_APP_DESC_AT + 2] << 16) | (head[_APP_DESC_AT + 3] << 24))
    if magic != APP_DESC_MAGIC:
        return None
    return {"version": _cstr(head[_VERSION_AT:_VERSION_AT + 32]),
            "project_name": _cstr(head[_PROJECT_AT:_PROJECT_AT + 32]),
            "time": _cstr(head[_TIME_AT:_TIME_AT + 16]),
            "date": _cstr(head[_DATE_AT:_DATE_AT + 16]),
            "idf_ver": _cstr(head[_IDF_VER_AT:_IDF_VER_AT + 32]),
            "elf_sha256": head[_ELF_SHA_AT:_ELF_SHA_AT + 32].hex()}


def elf_sha256_of_bin(path):
    """The ELF SHA-256 an image file carries, as 64 hex chars, or None.

    For telling what is about to be flashed by its BYTES. Two different
    binaries can share a file name; this cannot be fooled that way."""
    d = app_desc_of_bin(path)
    return d["elf_sha256"] if d else None


def variant_of(project_name):
    """`<variant>` of a project name interposer_<variant> (spec 8.2), or
    None if the name is not one of the interposer's variants."""
    if not project_name or not project_name.startswith(PROJECT_PREFIX):
        return None
    v = project_name[len(PROJECT_PREFIX):]
    return v if v in VARIANTS else None


def image_entry(path):
    """The entry for one image file, from its own descriptor, or None if
    the file is not an ESP-IDF application image."""
    d = app_desc_of_bin(path)
    if d is None:
        return None
    return dict(d, image=os.path.basename(path), path=path,
                variant=variant_of(d["project_name"]))


def entries_of(images):
    """(entries, files that are not application images)."""
    entries, bad = [], []
    for p in images or ():
        e = image_entry(p)
        (entries.append(e) if e else bad.append(p))
    return entries, bad


def build_id_from_payload(data):
    """0x7F7 B0-B3 as 8 hex characters, the hash's first four bytes.

    B0 IS THE FOURTH BYTE OF THE HASH, NOT THE FIRST. Corrected
    2026-10-06; this function had it backwards and so did its test.

    Spec 8.2: the four bytes are sent as one little-endian 32-bit value
    whose hexadecimal form reads like the start of the hash, so on the
    wire B0 is the hash's fourth byte and B3 its first. A board whose
    hash starts 7f197bfd sends fd 7b 19 7f; reading B0-B3 as a
    little-endian u32 gives back 7f197bfd.

    `identify_from_bus` decodes through the DBC (`intp_build_id 0|32@1+`,
    Intel u32), which is correct; this function is the fallback for when
    the DBC has no 0x7F7 entry. The defect it once had was invisible for
    the worst possible reason: the old docstring asserted "B0 is the FIRST
    byte of the hash", and the offline test asserted the same wrong
    premise from a hand-written byte list, so a passing test certified the
    bug. Verified against a real board on 2026-10-06: DBC path `7f197bfd`,
    this path `fd7b197f` before the fix.
    """
    if data is None or len(data) < 4:
        return None
    b = bytes(data[:4])
    return "%08x" % (b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24))


def identify(build_ids, images=None, entries=None):
    """Resolve observed 0x7F7 build ids to one of the candidate images.

    `build_ids` is every id seen on the wire -- a list, not one value,
    because seeing two different ones means two boards are reporting
    and the caller must know. `images` are the image files (.bin) that
    may be on the board; `entries` (image_entry() dicts) may be given
    instead.
    """
    bad = []
    if entries is None:
        entries, bad = entries_of(images)

    ids = [b for b in build_ids if b]
    if not ids:
        return Lookup(NO_PAGES,
                      detail="no 0x%X frames seen: the board is silent, is "
                             "not bridging, or the adapter is on the wrong "
                             "segment. This is not an unknown build."
                             % BUILD_PAGE_ID)

    distinct = sorted(set(ids))
    if len(distinct) > 1:
        return Lookup(COLLISION,
                      detail="more than one build id on the wire (%s) -- two "
                             "boards are reporting 0x%X"
                             % (", ".join(distinct), BUILD_PAGE_ID))
    bid = distinct[0]

    # Checked AFTER the wire, deliberately. No candidates with a silent
    # bus is a silent bus first: reporting NO_IMAGES there would send
    # someone to fix the images folder when the board is not talking.
    if not entries:
        return Lookup(NO_IMAGES, build_id=bid,
                      detail="no candidate image to compare with%s, so every "
                             "lookup would read as UNKNOWN_ID"
                             % (" (not application images: %s)"
                                % ", ".join(bad) if bad else ""))

    hits = [e for e in entries
            if (e.get("elf_sha256") or "")[:8].lower() == bid.lower()]
    if not hits:
        return Lookup(UNKNOWN_ID, build_id=bid,
                      detail="build id %s is carried by none of the %d "
                             "candidate image(s) (%s): this image is not one "
                             "expected here"
                             % (bid, len(entries),
                                ", ".join(sorted(e.get("image", "?")
                                                 for e in entries))))
    full = set((e.get("elf_sha256") or "").lower() for e in hits)
    if len(full) > 1:
        return Lookup(COLLISION, build_id=bid,
                      detail="build id %s matches %d different images (%s) -- "
                             "it cannot say which"
                             % (bid, len(hits),
                                ", ".join(sorted(e.get("image", "?")
                                                 for e in hits))))
    variants = sorted(set(str(e.get("variant")) for e in hits))
    if len(variants) > 1:
        return Lookup(COLLISION, build_id=bid,
                      detail="ELF %s is named as %d variants (%s) -- one "
                             "binary cannot be two, so these files cannot be "
                             "trusted here"
                             % (sorted(full)[0][:16], len(variants),
                                ", ".join(variants)))
    return Lookup(OK, entry=hits[0], build_id=bid, entries=hits)


def identify_from_bus(bus, dbc_path, seconds=3.0, images=None):
    """Listen for 0x7F7 and identify the running image among `images`.

    Decoding goes through the DBC with cantools rather than slicing
    bytes here, so a schema change to the build page is picked up in
    one place. The raw payload is used only if the DBC has no entry for
    the page.
    """
    import time
    import cantools

    db = cantools.database.load_file(dbc_path)
    try:
        msg = db.get_message_by_frame_id(BUILD_PAGE_ID)
    except KeyError:
        msg = None

    seen = []
    end = time.time() + seconds
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is None or m.arbitration_id != BUILD_PAGE_ID:
            continue
        # 0x7F7 is a standard 11-bit id, DLC 8 (spec 8.2). An extended
        # frame that happens to share the number is somebody else's.
        if getattr(m, "is_extended_id", False) or len(m.data) < 8:
            continue
        if msg is not None:
            d = msg.decode(bytes(m.data))
            raw = d.get("intp_build_id")
            if raw is not None:
                seen.append("%08x" % (int(raw) & 0xFFFFFFFF))
                continue
        seen.append(build_id_from_payload(m.data))

    return identify(seen, images=images)


def require_witness(lookup):
    """Raise unless `lookup` resolved to the witness image.

    For arms that are only meaningful on the instrumented build. The
    message names what WAS found, because "not the witness build" and
    "nothing identified" need different fixes.
    """
    if not lookup:
        raise SystemExit("TEST FAULT -- cannot identify the running image.\n"
                         "  %s\n"
                         "  No device verdict is available: what is on the "
                         "board was never established." % lookup)
    if not lookup.is_witness:
        raise SystemExit("TEST FAULT -- this arm needs the witness build.\n"
                         "  running: %s\n"
                         "  expected variant %s (project %s%s)"
                         % (lookup, WITNESS_VARIANT, PROJECT_PREFIX,
                            WITNESS_VARIANT))
    return lookup.entry
