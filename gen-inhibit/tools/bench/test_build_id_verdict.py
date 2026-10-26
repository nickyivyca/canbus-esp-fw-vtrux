"""Offline test of diag_build_page_check.build_id_verdict, schema 8 (port step 0c).

Spec 10 (0x7F3 names the binary, schema 8): diag_build_id is the first four bytes of the
image hash, "a u32 with the hash's first byte most significant, sent little-endian, so the
DBC decodes it as an unsigned 32-bit value that, printed as eight hex digits, reads as the
hash's first four bytes in order". Spec 12.4, "0x7F3 carries the build id" (bench) and
"the page names the binary": the decoded build id equals the first four bytes of the
flashed image's hash and of the page's elf_sha256, and the page's elf_sha256 equals the
image file's.

Each case packs a 0x7F3 payload by hand, from the spec's words, decodes it with the
schema 8 DBC from git (gi_diag), and gives the decoded value to build_id_verdict with an
image hash and a page elf_sha256. A payload packed the other way round, a page naming
another image, and every missing or malformed input must each fail. The schema 7 DBC
must decode the same bytes as diag_git_hash, so an old capture still reads its own field.

Run:  py -3.14 -B gen-inhibit/tools/bench/test_build_id_verdict.py   (exit 0 = all pass)
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gi_diag  # noqa: E402  the diag DBC from git, by schema byte

try:
    from diag_build_page_check import BUILD_ID, BUILD_ID_SCHEMA, build_id_verdict  # noqa: E402
except ImportError as exc:
    raise SystemExit("cannot import diag_build_page_check (it needs python-can): %r" % exc)

SCHEMAS = gi_diag.SchemaSet.from_git()
failures = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, (": " + detail) if detail else ""))
    if not ok:
        failures.append(label)


def payload(first4, schema, spec_order=True):
    """B0-B3 the build id: per spec 10, the u32 whose most significant byte is the hash's first
    byte, sent little-endian, so B0 is the hash's fourth byte. spec_order=False sends the
    hash's bytes in order instead (the wrong packing). B4-B5 fw_ver 3, B6 the schema, B7 0."""
    b03 = bytes(reversed(first4)) if spec_order else bytes(first4)
    return b03 + bytes([3, 0, schema, 0])


def decode(data, schema):
    return SCHEMAS.get(schema).db.decode_message(BUILD_ID, data, decode_choices=False)


def main():
    h = hashlib.sha256(b"an image").hexdigest()
    first4 = bytes.fromhex(h[:8])
    other = hashlib.sha256(b"another image").hexdigest()
    check("the 0x7F3 schema is %d" % BUILD_ID_SCHEMA, BUILD_ID_SCHEMA == 8)
    check("the DBC set from git has schema 8 at its head", SCHEMAS.head().version == 8, repr(SCHEMAS.head().version))

    good = decode(payload(first4, 8), 8)
    bid = good.get("diag_build_id")
    check("schema 8 decodes B0-B3 as diag_build_id", isinstance(bid, int), repr(good))
    check("printed %08x it reads as the hash's first four bytes", isinstance(bid, int) and "%08x" % bid == h[:8],
          "%s vs %s" % ("%08x" % bid if isinstance(bid, int) else bid, h[:8]))
    old = decode(payload(first4, 7), 7)
    check("schema 7 decodes the same bytes as diag_git_hash", "diag_git_hash" in old and "diag_build_id" not in old,
          ", ".join(sorted(old)))

    ok, rows = build_id_verdict(bid, h, h)
    check("faithful: wire, image and page name one image -> ok", ok and all(r[1] for r in rows),
          "; ".join("%s %s" % (r[0], r[1]) for r in rows if not r[1]))
    ok, _ = build_id_verdict(bid, h.upper(), h.upper())
    check("hex case does not matter -> ok", ok)

    wrong = decode(payload(first4, 8, spec_order=False), 8)["diag_build_id"]
    for label, args, failing in (
            ("packed in the hash's byte order on the wire", (wrong, h, h),
             {"build id = the image hash's first four bytes", "build id = the page elf_sha256's first four bytes"}),
            ("the page names another image", (bid, h, other),
             {"build id = the page elf_sha256's first four bytes", "page elf_sha256 = the image hash"}),
            ("the page differs from the image past the first four bytes", (bid, h, h[:-1] + ("0" if h[-1] != "0" else "1")),
             {"page elf_sha256 = the image hash"}),
            ("the image is another one", (bid, other, h),
             {"build id = the image hash's first four bytes", "page elf_sha256 = the image hash"}),
            ("no image hash", (bid, None, h),
             {"the flashed image's hash read", "build id = the image hash's first four bytes",
              "page elf_sha256 = the image hash"}),
            ("no page elf_sha256", (bid, h, None),
             {"the page's elf_sha256 read", "build id = the page elf_sha256's first four bytes",
              "page elf_sha256 = the image hash"}),
            ("a short page elf_sha256", (bid, h, h[:63]),
             {"the page's elf_sha256 read", "build id = the page elf_sha256's first four bytes",
              "page elf_sha256 = the image hash"}),
            ("no build id decoded", (None, h, h),
             {"diag_build_id decoded as a u32", "build id = the image hash's first four bytes",
              "build id = the page elf_sha256's first four bytes"}),
            ("a build id wider than 32 bits", (bid + (1 << 32), h, h),
             {"diag_build_id decoded as a u32", "build id = the image hash's first four bytes",
              "build id = the page elf_sha256's first four bytes"}),
            ("the build id as text", ("%08x" % bid, h, h),
             {"diag_build_id decoded as a u32", "build id = the image hash's first four bytes",
              "build id = the page elf_sha256's first four bytes"})):
        ok, rows = build_id_verdict(*args)
        got = {r[0] for r in rows if not r[1]}
        check("%s -> FAIL on exactly its rows" % label, not ok and got == failing,
              "failed %s" % sorted(got) if got != failing else "")

    print("\n=== %s ===" % ("PASS" if not failures else "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
