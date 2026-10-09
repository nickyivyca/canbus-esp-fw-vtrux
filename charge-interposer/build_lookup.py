"""Identify the firmware image a board is running, from 0x7F7.

The interposer reports its build in `0x7F7` B0-B3: the first four bytes
of the application ELF's SHA-256. This maps that back to a row in
`firmware/builds/manifest.json` -- which image, which environment, and
in particular whether it is the WITNESS build.

WHY A SHARED MODULE AND NOT A SNIPPET PER HARNESS. Every bench arm from
here on records the looked-up entry before it starts, and any arm that
needs the witness image asserts the environment rather than assuming
it. A copy of this logic in each harness would drift, and the drifted
copy would still return an answer. Import it.

EVERY FAILURE IS A REFUSAL WITH ITS OWN REASON, never a warning and
never a best guess. The four ways this can fail are four different
problems and must not be collapsed:

  NO_PAGES    no 0x7F7 frames were seen at all. The board is silent, or
              not bridging, or the dongle is on the wrong segment. This
              is NOT "unknown build" and must never read as one -- it
              is the commonest bench mistake and the one most easily
              mistaken for a firmware fault.
  NO_MANIFEST the manifest is missing or empty. Then EVERY lookup would
              return UNKNOWN_ID, and a bad build-records state would
              look like a wire problem.
  UNKNOWN_ID  a build id that is not in the manifest. A real image that
              nobody recorded -- flashed from somewhere else, or built
              before the manifest existed.
  COLLISION   two entries share this 4-byte prefix. The table cannot
              say which, so it says so instead of picking.

A caller that wants a verdict should treat anything but OK as a TEST
FAULT: the stimulus of the measurement -- knowing what is on the board
-- was not established, so no device verdict is available.

THE IDENTITY IS THE ELF HASH, NOT A MANIFEST ROW (reviewer's ruling,
2026-10-07). Builds are not reproducible, and src_digest hashes every
source while each environment compiles only a subset, so one ELF can carry
several rows -- the witness ELF 7f197bfd has four src_digests -- and one
image NAME can carry several ELFs (two clean rebuilds of 111342c1dd0f gave
c190275a and b1b23f81 under the same filename). Rev 1 returned `hits[0]`,
so the src_digest it reported depended on row order. An OK result now
carries every row that matches the full hash (`entries`), and refuses if
those rows disagree on the environment, which the same bytes cannot.
`elf_sha256_of_bin()` reads the hash out of an image file itself, so what
is about to be flashed can be identified by its bytes, never its name.
"""

import json
import os

OK = "OK"
NO_PAGES = "NO_PAGES"
NO_MANIFEST = "NO_MANIFEST"
UNKNOWN_ID = "UNKNOWN_ID"
COLLISION = "COLLISION"

BUILD_PAGE_ID = 0x7F7
WITNESS_ENV = "esp32-can-x2-witness"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "firmware", "builds", "manifest.json")


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
    def env(self):
        return (self.entry or {}).get("env")

    @property
    def is_witness(self):
        return self.env == WITNESS_ENV

    def __str__(self):
        if self.reason == OK:
            e = self.entry
            rows = "; ".join("%s (src %s, built %s)"
                             % (x.get("image"), x.get("src_digest"),
                                x.get("built_utc")) for x in self.entries)
            return ("build %s = ELF %s, env %s, fw_ver %s -- %d manifest "
                    "row(s): %s"
                    % (self.build_id, (e.get("elf_sha256") or "?")[:16],
                       e.get("env"), e.get("intp_fw_ver16"),
                       len(self.entries), rows))
        return "%s: %s" % (self.reason, self.detail)


def load_manifest(path=None):
    """Manifest entries, or [] if there is no usable manifest."""
    path = path or DEFAULT_MANIFEST
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f).get("builds", [])
    except (IOError, ValueError):
        return []


APP_DESC_MAGIC = 0xABCD5432
# esp_app_desc_t sits at the start of the first segment: 24-byte image
# header + 8-byte segment header, then magic(4) secure_version(4)
# reserv1(8) version(32) project_name(32) time(16) date(16) idf_ver(32),
# then app_elf_sha256[32] -- what `esptool image-info` prints as "ELF file
# SHA256" and what the board puts in 0x7F7.
_APP_DESC_AT = 24 + 8
_ELF_SHA_AT = _APP_DESC_AT + 4 + 4 + 8 + 32 + 32 + 16 + 16 + 32


def elf_sha256_of_bin(path):
    """The ELF SHA-256 an image file carries, as 64 hex chars, or None.

    For telling what is about to be flashed by its BYTES. The filename is
    derived from the source digest, so two different binaries can share
    it; this cannot be fooled that way."""
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
    return head[_ELF_SHA_AT:_ELF_SHA_AT + 32].hex()


def build_id_from_payload(data):
    """0x7F7 B0-B3 as the 8 hex characters the manifest stores.

    B0 IS THE FOURTH BYTE OF THE HASH, NOT THE FIRST. Corrected
    2026-10-06; this function had it backwards and so did its test.

    Trace it through the firmware rather than reasoning from the field
    name, because the two steps compose in a way the name hides:

      main.cpp buildId():   sha[0]<<24 | sha[1]<<16 | sha[2]<<8 | sha[3]
                            -> the u32 reads 7f197bfd for a hash
                               beginning 7f 19 7b fd, i.e. the same way
                               round as `elf_sha256`
      machine.cpp put32():  little-endian -- p[0]=LSB
                            -> the WIRE carries fd 7b 19 7f

    So the payload is the hash's first four bytes REVERSED, and the id
    the manifest stores is recovered by reading B0-B3 as a little-endian
    u32. `identify_from_bus` never hit this because it decodes through
    the DBC (`intp_build_id 0|32@1+`, Intel u32), which is correct; this
    function is the fallback for when the DBC has no 0x7F7 entry, and it
    returned `fd7b197f` where the manifest holds `7f197bfd`.

    The defect was invisible for the worst possible reason: the old
    docstring asserted "B0 is the FIRST byte of the hash" and warned
    that reversing it "would produce a plausible 8-hex string that never
    matches anything, which would surface as UNKNOWN_ID on a perfectly
    good board" -- which is precisely what it did. The offline test then
    asserted the same wrong premise from a hand-written byte list, so
    18/18 passing certified the bug. Verified against a real board on
    2026-10-06: DBC path `7f197bfd`, this path `fd7b197f`.
    """
    if data is None or len(data) < 4:
        return None
    b = bytes(data[:4])
    return "%08x" % (b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24))


def identify(build_ids, manifest_path=None, entries=None):
    """Resolve observed 0x7F7 build ids to one manifest entry.

    `build_ids` is every id seen on the wire -- a list, not one value,
    because seeing two different ones means two boards are reporting
    and the caller must know.
    """
    if entries is None:
        entries = load_manifest(manifest_path)

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

    # Checked AFTER the wire, deliberately. An empty manifest with a
    # silent bus is a silent bus first: reporting NO_MANIFEST there
    # would send someone to fix the records when the board is not
    # talking.
    if not entries:
        return Lookup(NO_MANIFEST, build_id=bid,
                      detail="manifest is missing or empty (%s), so every "
                             "lookup would read as UNKNOWN_ID"
                             % (manifest_path or DEFAULT_MANIFEST))

    hits = [e for e in entries
            if (e.get("elf_sha256") or "")[:8].lower() == bid.lower()]
    if not hits:
        return Lookup(UNKNOWN_ID, build_id=bid,
                      detail="build id %s is in no manifest row: this image "
                             "was not built here, or predates the manifest"
                             % bid)
    full = set((e.get("elf_sha256") or "").lower() for e in hits)
    if len(full) > 1:
        return Lookup(COLLISION, build_id=bid,
                      detail="build id %s matches %d different images (%s) -- "
                             "the manifest cannot say which"
                             % (bid, len(hits),
                                ", ".join(sorted(e.get("image", "?")
                                                 for e in hits))))
    envs = sorted(set(str(e.get("env")) for e in hits))
    if len(envs) > 1:
        return Lookup(COLLISION, build_id=bid,
                      detail="ELF %s has rows naming %d environments (%s) -- "
                             "one binary cannot come from two, so the "
                             "manifest is wrong and cannot be trusted here"
                             % (sorted(full)[0][:16], len(envs),
                                ", ".join(envs)))
    return Lookup(OK, entry=hits[0], build_id=bid, entries=hits)


def identify_from_bus(bus, dbc_path, seconds=3.0, manifest_path=None):
    """Listen for 0x7F7 and identify the running image.

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

    return identify(seen, manifest_path=manifest_path)


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
                         "  expected env %s" % (lookup, WITNESS_ENV))
    return lookup.entry
