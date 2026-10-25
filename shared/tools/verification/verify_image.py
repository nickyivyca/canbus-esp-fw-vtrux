#!/usr/bin/env python3
"""Verify vtrux_build.image against SPEC R1, R2 and R15 (its library side).

    python -B verify_image.py [--same A.bin B.bin ...] [--different C.bin] [--expect-code PREFIX]
    python -B verify_image.py --self-test      # the same checks against fake libraries

Synthetic images (independent.build) and, when given, real ones. Each real image's
.elf beside it, when present, is hashed for R1. --same images must share one code
hash, --different ones must not share it; --expect-code is that shared hash's
prefix, when one was found by someone else. The library's answers are compared with
independent.py, the tester's own reading of SPEC, never with the library's source.
--self-test runs every synthetic check against fakes that each break one rule and
must each be caught, and one fake that breaks none and must pass.

Gen-inhibit tester, 2026-10-24 (C6f verification).
"""
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import independent as I  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print("  %-4s %-72s %s" % ("ok" if ok else "FAIL", name, "" if ok else str(detail)[:150]))
    if not ok:
        FAILS.append(name)


def raises(lib, data):
    try:
        lib.AppImage(data)
    except lib.ImageError:
        return True
    except Exception as e:                      # another exception is not the refusal R2's reader owes
        return "raised %r, not ImageError" % e
    return False


def synthetic(lib, tmp):
    base = I.build()
    img = lib.AppImage(base)
    check("R2: the code hash is the tester's reading of R2", img.code_hash == I.code_hash(base),
          (img.code_hash, I.code_hash(base)))
    check("R2: the blanked ranges are R2's", sorted(tuple(r) for r in img.code_hash_blanks()) == I.blanks(base),
          (img.code_hash_blanks(), I.blanks(base)))
    check("R1: the image hash is the embedded ELF SHA-256", img.image_hash == I.image_hash(base),
          (img.image_hash, I.image_hash(base)))
    for name, value in (("version", b"def5678-dirty"), ("time", b"12:34:56"), ("date", b"Oct 24 2026"),
                        ("elf_sha256", bytes(range(100, 132)))):
        v = I.with_field(base, name, value)
        check("R2: a different %s keeps the code hash" % name, lib.AppImage(v).code_hash == img.code_hash)
    v = I.with_field(base, "elf_sha256", bytes(range(100, 132)))
    check("R1: a different ELF SHA-256 is a different image hash", lib.AppImage(v).image_hash != img.image_hash)
    for name, value in (("project_name", b"other"), ("idf_ver", b"v5.4.2")):
        v = I.with_field(base, name, value)
        check("R2: a different %s is a different code hash" % name, lib.AppImage(v).code_hash != img.code_hash)
    segs, _c, _s = I.layout(base)
    off, n = segs[1]
    v = I.with_byte(base, off + n // 2, (base[off + n // 2] + 1) % 256)
    check("R2: one code byte changed is a different code hash", lib.AppImage(v).code_hash != img.code_hash)
    check("R2: ... and the same image hash (the ELF field is unchanged)", lib.AppImage(v).image_hash == img.image_hash)
    three = I.build(segments=[b"\x01" * 64, b"\x02" * 70, b"\x03" * 99])
    check("R2: three segments: the tester's reading", lib.AppImage(three).code_hash == I.code_hash(three))
    _s, cks, _h = I.layout(base)
    badck = bytearray(base[:cks + 1])
    badck[cks] ^= 0x55
    badck = bytes(badck) + hashlib.sha256(bytes(badck)).digest()      # the SHA good, the checksum not
    for label, bad in (("truncated by one byte", base[:-1]), ("one byte extra", base + b"\0"),
                       ("a bad checksum", badck),
                       ("a bad SHA-256", base[:-1] + bytes([base[-1] ^ 1])),
                       ("not an image", b"\0" * 64)):
        r = raises(lib, bad)
        check("R2: an image with %s is refused (ImageError)" % label, r is True, r)
    p = os.path.join(tmp, "synthetic.bin")
    open(p, "wb").write(base)
    st = os.stat(p)
    a = lib.AppImage.read(p)
    check("R2: read, never patched (bytes and mtime unchanged)",
          open(p, "rb").read() == base and os.stat(p).st_mtime_ns == st.st_mtime_ns)
    check("R2: AppImage.read gives the same hashes", (a.code_hash, a.image_hash) == (img.code_hash, img.image_hash))
    check("R15: code_hash(path) is the tester's R2", lib.code_hash(p) == I.code_hash(base))
    check("R1: image_hash(path) is the embedded ELF SHA-256", lib.image_hash(p) == I.image_hash(base))


def real(lib, same, different, expect):
    codes = {}
    for p in same + different:
        data = open(p, "rb").read()
        try:
            img = lib.AppImage.read(p)
        except Exception as e:
            check("%s: read" % p, False, repr(e))
            continue
        codes[p] = img.code_hash
        check("R2: %s: the tester's reading" % os.path.basename(os.path.dirname(p)), img.code_hash == I.code_hash(data),
              (img.code_hash, I.code_hash(data)))
        check("R15: %s: code_hash(path)" % os.path.basename(os.path.dirname(p)), lib.code_hash(p) == I.code_hash(data))
        elf = p[:-4] + ".elf"
        if os.path.exists(elf):
            h = hashlib.sha256(open(elf, "rb").read()).hexdigest()
            check("R1: %s: the image hash is the sha256 of the ELF beside it" % os.path.basename(os.path.dirname(p)),
                  img.image_hash == h, (img.image_hash, h))
        print("       %s  code %s  image %s" % (p, img.code_hash, img.image_hash))
    s = {codes[p] for p in same if p in codes}
    if same:
        check("R2: the --same images share one code hash", len(s) == 1, s)
    if expect and len(s) == 1:
        check("R2: ... and it is %s..." % expect, next(iter(s)).startswith(expect), s)
    for p in different:
        check("R2: %s's code hash differs from theirs" % os.path.basename(os.path.dirname(p)),
              p in codes and codes[p] not in s)


class Fake:
    """A library made from independent.py with one rule broken (or none)."""
    class ImageError(Exception):
        pass

    def __init__(self, broken=None):
        self.broken = broken
        lib = self

        class AppImage:
            def __init__(s, data):
                try:
                    I.layout(data)
                    b = I.blanks(data)
                except I.Bad as e:
                    if broken == "accepts a bad SHA-256" and str(e) == "SHA-256":
                        b = I.blanks(data[:-32] + hashlib.sha256(data[:-32]).digest())
                    else:
                        raise lib.ImageError(str(e))
                if broken == "the version is not blanked":
                    b = [r for r in b if r != I.FIELDS["version"]]
                if broken == "the trailing SHA is not blanked":
                    b = b[:-1]
                if broken == "the ELF hash field is not blanked":
                    b = [r for r in b if r != I.FIELDS["elf_sha256"]]
                if broken == "idf_ver is blanked too":
                    b = sorted(b + [I.FIELDS["idf_ver"]])
                s._b = b
                c = bytearray(data)
                for off, n in b:
                    c[off:off + n] = bytes(n)
                s.code_hash = hashlib.sha256(bytes(c)).hexdigest()
                if broken == "the code hash is the file's sha256":
                    s.code_hash = hashlib.sha256(data).hexdigest()
                off, n = I.FIELDS["elf_sha256"]
                s.image_hash = data[off:off + n].hex()
                if broken == "the image hash is the file's sha256":
                    s.image_hash = hashlib.sha256(data).hexdigest()

            def code_hash_blanks(s):
                return s._b

            @classmethod
            def read(cls, path):
                data = open(path, "rb").read()
                if broken == "read patches the file":
                    open(path, "wb").write(data)
                return cls(data)
        self.AppImage = AppImage

    def code_hash(self, p):
        return self.AppImage.read(p).code_hash

    def image_hash(self, p):
        return self.AppImage.read(p).image_hash


BROKEN = ["the version is not blanked", "the trailing SHA is not blanked", "the ELF hash field is not blanked",
          "idf_ver is blanked too", "the code hash is the file's sha256", "the image hash is the file's sha256",
          "accepts a bad SHA-256", "read patches the file"]


def main(argv):
    tmp = I.scratch("verify_image")
    try:
        if "--self-test" in argv:
            ok = True
            for b in [None] + BROKEN:
                del FAILS[:]
                print("--- fake: %s" % (b or "nothing broken (must pass)"))
                synthetic(Fake(b), tmp)
                caught = bool(FAILS)
                good = caught == (b is not None)
                ok &= good
                print("%s %s  (%d failing check(s))" % ("CAUGHT  " if caught and b else "PASSED  " if not b and not caught
                                                       else "MISSED  ", b or "the faithful fake", len(FAILS)))
            print("\nself-test: %s" % ("every broken fake caught, the faithful one passed" if ok else "FAILED"))
            return 0 if ok else 1
        import vtrux_build.image as lib
        args = {"--same": [], "--different": []}
        cur, expect = None, None
        for a in argv:
            if a in args:
                cur = a
            elif a == "--expect-code":
                cur = "x"
            elif cur == "x":
                expect, cur = a, None
            elif cur:
                args[cur].append(a)
        print("library: %s" % lib.__file__)
        synthetic(lib, tmp)
        real(lib, args["--same"], args["--different"], expect)
        print("\n%s" % ("all ok" if not FAILS else "%d FAIL: %s" % (len(FAILS), "; ".join(FAILS))))
        return 1 if FAILS else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
