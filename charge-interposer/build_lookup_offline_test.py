"""Offline test for build_lookup: every reason code, no bus, no board.

Modelled on the generator-inhibit project's autoarm_bit_offline_test.py,
and on the lesson that came with it: the helper is IMPORTED here, not
reimplemented. A test that re-derives the logic it is checking agrees
with itself.

The point of these cases is that the five outcomes stay DISTINGUISHABLE.
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
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_lookup as bl  # noqa: E402

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


FULL_A = "c4bcb52b8dc74fb5eae02b5761bbbd4817a705d74b5c1d6acae24aea157598e6"
FULL_B = "0fc580b6" + "1" * 56          # same prefix as nothing else
FULL_C = "c4bcb52b" + "f" * 56          # SAME first 4 bytes as FULL_A

WITNESS = {"image": "interposer_x_witness.bin", "env": "esp32-can-x2-witness",
           "tag": "witness", "elf_sha256": FULL_A, "intp_fw_ver16": 4,
           "built_utc": "2026-10-05T04:00:00Z"}
BRIDGE = {"image": "interposer_x_bridge.bin", "env": "esp32-can-x2",
          "tag": "bridge", "elf_sha256": FULL_B, "intp_fw_ver16": 4,
          "built_utc": "2026-10-05T04:00:00Z"}
COLLIDER = {"image": "interposer_y_bridge.bin", "env": "esp32-can-x2",
            "tag": "bridge", "elf_sha256": FULL_C, "intp_fw_ver16": 4,
            "built_utc": "2026-10-05T04:00:00Z"}

GOOD = [WITNESS, BRIDGE]


def main():
    print("--- build_lookup offline ---")
    got = os.path.normcase(os.path.abspath(bl.__file__))
    want = os.path.normcase(os.path.join(HERE, "build_lookup.py"))
    check(got == want, "the build_lookup under test is this folder's (%s)"
          % bl.__file__)

    # KNOWN
    r = bl.identify(["c4bcb52b"], entries=GOOD)
    check(r.reason == bl.OK, "a known build id resolves (reason OK)")
    check(bool(r), "and the result is truthy, so `if not r` is a safe guard")
    check(r.env == "esp32-can-x2-witness" and r.is_witness,
          "to the right environment, and is_witness is set")

    r = bl.identify(["0fc580b6"], entries=GOOD)
    check(r.reason == bl.OK and not r.is_witness,
          "the bridge image resolves and is NOT flagged as the witness -- "
          "the distinction the whole lookup exists for")

    # UNKNOWN
    r = bl.identify(["deadbeef"], entries=GOOD)
    check(r.reason == bl.UNKNOWN_ID, "an unrecorded build id is UNKNOWN_ID")
    check(not r, "and is falsy, so a caller cannot use it by accident")
    check(r.build_id == "deadbeef",
          "and still reports the id, so it can be looked for by hand")

    # NO PAGES -- its own reason, not UNKNOWN
    r = bl.identify([], entries=GOOD)
    check(r.reason == bl.NO_PAGES,
          "no 0x7F7 frames at all is NO_PAGES, NOT unknown-build: a silent "
          "bus and an unrecorded image need different fixes")
    check("0x7F7" in r.detail and "not an unknown build" in r.detail,
          "and says so in the detail, where a bench operator will read it")

    # NO MANIFEST -- distinct from UNKNOWN, which is how it would
    # otherwise present
    r = bl.identify(["c4bcb52b"], entries=[])
    check(r.reason == bl.NO_MANIFEST,
          "an empty manifest is NO_MANIFEST, not UNKNOWN_ID -- otherwise a "
          "broken records state looks like a wire problem")
    r = bl.identify([], entries=[])
    check(r.reason == bl.NO_PAGES,
          "with BOTH wrong, the silent bus wins: fixing the records first "
          "would be the wrong order")

    # COLLISION, two ways
    r = bl.identify(["c4bcb52b"], entries=[WITNESS, COLLIDER])
    check(r.reason == bl.COLLISION,
          "two images sharing a 4-byte prefix is a COLLISION, not a pick")
    check("witness" in r.detail and "y_bridge" in r.detail,
          "and names both candidates")

    r = bl.identify(["c4bcb52b", "0fc580b6"], entries=GOOD)
    check(r.reason == bl.COLLISION,
          "two DIFFERENT ids on the wire is also a refusal -- two boards "
          "are reporting 0x7F7")

    # ------------------------------------------------------------------
    # PAYLOAD DECODING, BUILT FROM THE FIRMWARE'S OWN PACKING.
    #
    # Rewritten 2026-10-06. The previous version hand-wrote the payload
    # byte list AND asserted it decoded big-endian, so it tested the
    # decoder against the same wrong premise the decoder held, and
    # 18/18 passing certified a real defect. A test whose expected value
    # is written by the same misunderstanding as the code cannot fail.
    #
    # So derive the payload the way the board does, in two steps, from a
    # known hash. Anyone changing either step has to change this and will
    # see why.
    def pack_like_firmware(sha_first4):
        """main.cpp buildId() then machine.cpp put32(), composed."""
        v = ((sha_first4[0] << 24) | (sha_first4[1] << 16)
             | (sha_first4[2] << 8) | sha_first4[3])          # buildId()
        return bytes([v & 0xFF, (v >> 8) & 0xFF,
                      (v >> 16) & 0xFF, (v >> 24) & 0xFF])    # put32()

    SHA4 = [0x7f, 0x19, 0x7b, 0xfd]        # a real board's, 2026-10-06
    wire = pack_like_firmware(SHA4)
    check(wire == bytes([0xfd, 0x7b, 0x19, 0x7f]),
          "the firmware's packing puts the hash's first 4 bytes on the "
          "wire REVERSED (sha 7f197bfd -> wire fd 7b 19 7f)")
    check(bl.build_id_from_payload(wire + bytes([4, 0, 3, 0])) == "7f197bfd",
          "and B0-B3 decode back to the id the manifest stores, matching "
          "the DBC path (intp_build_id 0|32@1+, Intel u32)")

    # THE MUTATION. Feeding the bytes in hash order -- which is what the
    # old decoder effectively assumed -- must NOT produce the right id.
    # Without this, a future revert to byte-joining would pass again.
    check(bl.build_id_from_payload(bytes(SHA4) + bytes([4, 0, 3, 0]))
          != "7f197bfd",
          "wire bytes supplied in HASH order do not decode to the stored "
          "id -- the old big-endian reading is now a failing case")
    check(bl.build_id_from_payload(bytes(SHA4) + bytes([4, 0, 3, 0]))
          == "fd7b197f",
          "and they decode to fd7b197f, the exact string the old code "
          "returned and that never matched any manifest row")

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

    # Tester, 2026-10-07: the ELF is the identity, and one ELF can carry
    # several rows (the witness ELF 7f197bfd has four src_digests). Rev 1
    # returned hits[0], so which src_digest it named depended on row order.
    sib1 = dict(WITNESS, image="interposer_s1_witness.bin", src_digest="s1")
    sib2 = dict(WITNESS, image="interposer_s2_witness.bin", src_digest="s2")
    for order in ([sib1, sib2, BRIDGE], [sib2, BRIDGE, sib1]):
        r = bl.identify(["c4bcb52b"], entries=order)
        check(r.reason == bl.OK and len(r.entries) == 2
              and set(e["src_digest"] for e in r.entries) == {"s1", "s2"},
              "one ELF on two rows resolves OK and carries BOTH rows, in "
              "either manifest order (%s)" % [e["image"] for e in order])
        check("s1" in str(r) and "s2" in str(r),
              "and its description names both rows, not the first")
    odd = dict(WITNESS, image="interposer_s3_bridge.bin", env="esp32-can-x2")
    r = bl.identify(["c4bcb52b"], entries=[sib1, odd])
    check(r.reason == bl.COLLISION and "environments" in r.detail,
          "one ELF whose rows name two environments is refused -> %s"
          % r.detail)
    # The same image NAME with two different ELFs (two rebuilds of one
    # digest) is legal in the manifest and resolves by the bytes.
    rebuilt = dict(WITNESS, elf_sha256="b1b23f81" + "2" * 56)
    r = bl.identify(["b1b23f81"], entries=[WITNESS, rebuilt])
    check(r.reason == bl.OK and r.elf_sha256 == rebuilt["elf_sha256"],
          "one image name on two ELFs resolves to the ELF on the wire")

    # elf_sha256_of_bin: the hash out of the image's own bytes.
    import tempfile
    head = bytearray(256)
    head[32:36] = (0xABCD5432).to_bytes(4, "little")
    head[176:208] = bytes.fromhex(FULL_A)
    d = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
    good_p = os.path.join(d, "good.bin")
    bad_p = os.path.join(d, "badmagic.bin")
    short_p = os.path.join(d, "short.bin")
    with open(good_p, "wb") as f:
        f.write(bytes(head))
    bad = bytearray(head)
    bad[32] ^= 0xFF
    with open(bad_p, "wb") as f:
        f.write(bytes(bad))
    with open(short_p, "wb") as f:
        f.write(bytes(head[:100]))
    check(bl.elf_sha256_of_bin(good_p) == FULL_A,
          "elf_sha256_of_bin reads the descriptor's hash at offset 176")
    check(bl.elf_sha256_of_bin(bad_p) is None,
          "and refuses (None) when the descriptor magic is wrong")
    check(bl.elf_sha256_of_bin(short_p) is None,
          "and on a file too short to hold it")
    check(bl.elf_sha256_of_bin(os.path.join(d, "missing.bin")) is None,
          "and on a missing file")
    for q in (good_p, bad_p, short_p):
        os.remove(q)
    os.rmdir(d)

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\nbuild_lookup_offline_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
