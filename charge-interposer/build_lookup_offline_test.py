"""Offline test for build_lookup: every reason code, no bus, no board.

Modelled on the generator-inhibit project's autoarm_bit_offline_test.py,
and on the lesson that came with it: the helper is IMPORTED here, not
reimplemented. A test that re-derives the logic it is checking agrees
with itself.

The point of these cases is that the outcomes stay DISTINGUISHABLE.
A lookup that collapses "no frames on the wire" into "unknown build"
would send someone to check the firmware when the dongle is on the
wrong segment -- and the wrong conclusion would be delivered with a
build id attached, which makes it worse, not better.

    python charge-interposer/build_lookup_offline_test.py   (from any cwd)

2026-10-09: imports build_lookup from THIS file's folder. It imported
`projects.vtrux.tools.interposer.build_lookup` from the cwd, the pre-move
SeaDrive copy. So from the cutover (2026-10-08) until that copy was removed,
a run from the SeaDrive root tested the stale SeaDrive build_lookup.py,
not the repo's, and passed. After the removal it failed from every root.
The first check below asserts which file was imported.

Port step 2a: the candidates are image files and their own application
descriptors, not manifest rows (the manifest retired with the PlatformIO
build). The stand-in images below are written with the descriptor laid out
as ESP-IDF's esp_app_desc_t is; that the offsets match a real image is
checked against `esptool image-info` by hand when the layout is touched
(2026-11-03: the six port step 2a dev images, fd226d9 at ESP-IDF v5.4.2).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_lookup as bl  # noqa: E402
import scratch_dir  # noqa: E402

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


FULL_A = "c4bcb52b8dc74fb5eae02b5761bbbd4817a705d74b5c1d6acae24aea157598e6"
FULL_B = "0fc580b6" + "1" * 56          # same prefix as nothing else
FULL_C = "c4bcb52b" + "f" * 56          # SAME first 4 bytes as FULL_A


def entry(image, project, sha):
    return {"image": image, "project_name": project,
            "variant": bl.variant_of(project), "elf_sha256": sha,
            "version": "fd226d9", "idf_ver": "v5.4.2"}


WITNESS = entry("interposer_witness.bin", "interposer_witness", FULL_A)
BRIDGE = entry("interposer_bridge.bin", "interposer_bridge", FULL_B)
COLLIDER = entry("other/interposer_bridge.bin", "interposer_bridge", FULL_C)

GOOD = [WITNESS, BRIDGE]


def image_bytes(project, sha, version="fd226d9", idf_ver="v5.4.2",
                magic=bl.APP_DESC_MAGIC):
    """An image head with esp_app_desc_t where ESP-IDF puts it: after the
    24-byte image header and the 8-byte segment header."""
    h = bytearray(24 + 8 + 256)
    a = 32
    h[a:a + 4] = magic.to_bytes(4, "little")
    def put(at, n, s):
        b = s.encode("ascii")
        h[at:at + len(b)] = b
    put(a + 16, 32, version)
    put(a + 48, 32, project)
    put(a + 80, 16, "14:00:00")
    put(a + 96, 16, "Nov  3 2026")
    put(a + 112, 32, idf_ver)
    h[a + 144:a + 176] = bytes.fromhex(sha)
    return bytes(h)


def main():
    print("--- build_lookup offline ---")
    got = os.path.normcase(os.path.abspath(bl.__file__))
    want = os.path.normcase(os.path.join(HERE, "build_lookup.py"))
    check(got == want, "the build_lookup under test is this folder's (%s)"
          % bl.__file__)

    # the variant from the project name (spec 8.2: interposer_<variant>)
    check([bl.variant_of("interposer_" + v) for v in bl.VARIANTS]
          == list(bl.VARIANTS),
          "every spec 8.2 variant is read back from interposer_<variant>")
    # interposer_slcan: the slcan variant, dropped from spec 8.2 on
    # 2026-11-13 (tracker H; its image is shared/x2_test_images' slcan_can1)
    for odd in ("interposer_", "interposer_bridge2", "bridge", "wican_bridge",
                "interposer_slcan", "", None):
        check(bl.variant_of(odd) is None,
              "a project name that is no interposer variant (%r) gives None"
              % (odd,))

    # KNOWN
    r = bl.identify(["c4bcb52b"], entries=GOOD)
    check(r.reason == bl.OK, "a known build id resolves (reason OK)")
    check(bool(r), "and the result is truthy, so `if not r` is a safe guard")
    check(r.variant == "witness" and r.is_witness,
          "to the right variant, and is_witness is set")

    r = bl.identify(["0fc580b6"], entries=GOOD)
    check(r.reason == bl.OK and not r.is_witness and r.variant == "bridge",
          "the bridge image resolves and is NOT flagged as the witness -- "
          "the distinction the whole lookup exists for")

    # UNKNOWN
    r = bl.identify(["deadbeef"], entries=GOOD)
    check(r.reason == bl.UNKNOWN_ID, "an id no candidate carries is UNKNOWN_ID")
    check(not r, "and is falsy, so a caller cannot use it by accident")
    check(r.build_id == "deadbeef",
          "and still reports the id, so it can be looked for by hand")
    check("interposer_witness.bin" in r.detail
          and "interposer_bridge.bin" in r.detail,
          "and names the candidates it was compared with")

    # NO PAGES -- its own reason, not UNKNOWN
    r = bl.identify([], entries=GOOD)
    check(r.reason == bl.NO_PAGES,
          "no 0x7F7 frames at all is NO_PAGES, NOT unknown-build: a silent "
          "bus and an unexpected image need different fixes")
    check("0x7F7" in r.detail and "not an unknown build" in r.detail,
          "and says so in the detail, where a bench operator will read it")

    # NO IMAGES -- distinct from UNKNOWN, which is how it would otherwise
    # present
    r = bl.identify(["c4bcb52b"], entries=[])
    check(r.reason == bl.NO_IMAGES,
          "no candidate image is NO_IMAGES, not UNKNOWN_ID -- otherwise a "
          "missing images folder looks like a wire problem")
    r = bl.identify(["c4bcb52b"])
    check(r.reason == bl.NO_IMAGES,
          "and so is a call that names no images at all")
    r = bl.identify([], entries=[])
    check(r.reason == bl.NO_PAGES,
          "with BOTH wrong, the silent bus wins: fixing the images first "
          "would be the wrong order")

    # COLLISION, three ways
    r = bl.identify(["c4bcb52b"], entries=[WITNESS, COLLIDER])
    check(r.reason == bl.COLLISION,
          "two images sharing a 4-byte prefix is a COLLISION, not a pick")
    check("interposer_witness.bin" in r.detail and "other/" in r.detail,
          "and names both candidates")

    r = bl.identify(["c4bcb52b", "0fc580b6"], entries=GOOD)
    check(r.reason == bl.COLLISION,
          "two DIFFERENT ids on the wire is also a refusal -- two boards "
          "are reporting 0x7F7")

    odd = entry("copy/interposer_bridge.bin", "interposer_bridge", FULL_A)
    r = bl.identify(["c4bcb52b"], entries=[WITNESS, odd])
    check(r.reason == bl.COLLISION and "variants" in r.detail,
          "one ELF named as two variants is refused -> %s" % r.detail)

    # one ELF in two files: both carried, in either order
    sib = dict(WITNESS, image="copy/interposer_witness.bin")
    for order in ([WITNESS, sib, BRIDGE], [sib, BRIDGE, WITNESS]):
        r = bl.identify(["c4bcb52b"], entries=order)
        check(r.reason == bl.OK and len(r.entries) == 2
              and set(e["image"] for e in r.entries)
              == {"interposer_witness.bin", "copy/interposer_witness.bin"},
              "one ELF in two files resolves OK and carries BOTH, in "
              "either order (%s)" % [e["image"] for e in order])
        files = str(r).rsplit("file(s): ", 1)[-1].split("; ")
        check(sorted(files) == ["copy/interposer_witness.bin",
                                "interposer_witness.bin"],
              "and its description names both files, not the first -> %s"
              % files)

    # ------------------------------------------------------------------
    # PAYLOAD DECODING, BUILT FROM THE FIRMWARE'S OWN PACKING.
    #
    # Rewritten 2026-10-06. The previous version hand-wrote the payload
    # byte list AND asserted it decoded big-endian, so it tested the
    # decoder against the same wrong premise the decoder held, and
    # 18/18 passing certified a real defect. A test whose expected value
    # is written by the same misunderstanding as the code cannot fail.
    #
    # So derive the payload as spec 8.2 says the board sends it: the
    # hash's first four bytes as one u32 that reads like the hash, sent
    # little-endian.
    def pack_like_spec(sha_first4):
        v = ((sha_first4[0] << 24) | (sha_first4[1] << 16)
             | (sha_first4[2] << 8) | sha_first4[3])
        return bytes([v & 0xFF, (v >> 8) & 0xFF,
                      (v >> 16) & 0xFF, (v >> 24) & 0xFF])

    SHA4 = [0x7f, 0x19, 0x7b, 0xfd]        # a real board's, 2026-10-06
    wire = pack_like_spec(SHA4)
    check(wire == bytes([0xfd, 0x7b, 0x19, 0x7f]),
          "spec 8.2's packing puts the hash's first 4 bytes on the wire "
          "REVERSED (sha 7f197bfd -> wire fd 7b 19 7f)")
    check(bl.build_id_from_payload(wire + bytes([4, 0, 3, 0])) == "7f197bfd",
          "and B0-B3 decode back to the hash's first 4 bytes, matching "
          "the DBC path (intp_build_id 0|32@1+, Intel u32)")

    # THE MUTATION. Feeding the bytes in hash order -- which is what the
    # old decoder effectively assumed -- must NOT produce the right id.
    check(bl.build_id_from_payload(bytes(SHA4) + bytes([4, 0, 3, 0]))
          != "7f197bfd",
          "wire bytes supplied in HASH order do not decode to the hash -- "
          "the old big-endian reading is now a failing case")
    check(bl.build_id_from_payload(bytes(SHA4) + bytes([4, 0, 3, 0]))
          == "fd7b197f",
          "and they decode to fd7b197f, the exact string the old code "
          "returned")

    check(bl.build_id_from_payload(bytes([1, 2])) is None,
          "a short payload yields None rather than a padded guess")

    # require_witness refuses rather than returning something usable.
    for case, entries, ids in (("unidentified", GOOD, []),
                               ("the bridge image", GOOD, ["0fc580b6"])):
        try:
            bl.require_witness(bl.identify(ids, entries=entries))
            check(False, "require_witness must refuse on %s" % case)
        except SystemExit as exc:
            check("TEST FAULT" in str(exc),
                  "require_witness refuses on %s, as a TEST FAULT" % case)
    check(bl.require_witness(bl.identify(["c4bcb52b"], entries=GOOD))
          is WITNESS, "and returns the witness entry when it is the witness")

    # ------------------------------------------------------------------
    # IMAGE FILES: the descriptor out of the image's own bytes.
    d = scratch_dir.make("build-lookup-offline-test")
    try:
        def write(name, data):
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            return p

        wit_p = write("witness/interposer_witness.bin",
                      image_bytes("interposer_witness", FULL_A))
        bri_p = write("bridge/interposer_bridge.bin",
                      image_bytes("interposer_bridge", FULL_B))
        bad_p = write("badmagic.bin", image_bytes("interposer_bridge", FULL_B,
                                                  magic=0x12345678))
        short_p = write("short.bin", image_bytes("interposer_bridge",
                                                 FULL_B)[:100])

        a = bl.app_desc_of_bin(wit_p)
        check(a is not None and a["elf_sha256"] == FULL_A
              and a["project_name"] == "interposer_witness"
              and a["version"] == "fd226d9" and a["idf_ver"] == "v5.4.2"
              and a["date"] == "Nov  3 2026" and a["time"] == "14:00:00",
              "app_desc_of_bin reads hash, project name, version, ESP-IDF "
              "version, date and time from where esp_app_desc_t puts them "
              "-> %s" % a)
        check(bl.elf_sha256_of_bin(wit_p) == FULL_A,
              "elf_sha256_of_bin reads the descriptor's hash at offset 176")
        check(bl._ELF_SHA_AT == 176,
              "which is the offset the image's descriptor has always had")
        check(bl.elf_sha256_of_bin(bad_p) is None
              and bl.app_desc_of_bin(bad_p) is None,
              "and refuses (None) when the descriptor magic is wrong")
        check(bl.elf_sha256_of_bin(short_p) is None,
              "and on a file too short to hold it")
        check(bl.elf_sha256_of_bin(os.path.join(d, "missing.bin")) is None,
              "and on a missing file")

        e = bl.image_entry(wit_p)
        check(e["variant"] == "witness" and e["image"] == "interposer_witness.bin"
              and e["path"] == wit_p, "image_entry names the variant and file")

        r = bl.identify(["c4bcb52b"], images=[wit_p, bri_p])
        check(r.reason == bl.OK and r.is_witness and r.entry["path"] == wit_p,
              "identify resolves the wire's id among image FILES by their "
              "own bytes -> %s" % r)
        r = bl.identify(["0fc580b6"], images=[wit_p, bri_p])
        check(r.reason == bl.OK and r.variant == "bridge",
              "and the bridge file as the bridge")
        # the name of the file is not the identity: the witness's bytes
        # under the bridge's file name are the witness
        swapped = write("swapped/interposer_bridge.bin",
                        image_bytes("interposer_witness", FULL_A))
        r = bl.identify(["c4bcb52b"], images=[swapped, bri_p])
        check(r.reason == bl.OK and r.is_witness,
              "a file named for the bridge carrying the witness's bytes "
              "resolves as the witness: the bytes decide, not the name")
        r = bl.identify(["c4bcb52b"], images=[bad_p, short_p])
        check(r.reason == bl.NO_IMAGES and "badmagic.bin" in r.detail
              and "short.bin" in r.detail,
              "files that are not application images leave NO_IMAGES, "
              "naming them -> %s" % r.detail)
    finally:
        scratch_dir.remove(d)

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\nbuild_lookup_offline_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
