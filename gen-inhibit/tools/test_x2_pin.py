#!/usr/bin/env python3
"""Tests for x2_pin.py: the committed pin is well formed and self-consistent; a malformed pin is
refused; load() refuses a missing file, a file with other bytes and an app with another ELF;
install() copies only the pinned bytes, leaves a matching file alone and never overwrites one
that differs. Made-up images in this run's own scratch folder; no hardware.

    py -3.14 -B gen-inhibit/tools/test_x2_pin.py

Prints "ok ..." or "FAIL ..." per check and PASS or FAIL at the end; exit 0 only on PASS.
Gen-inhibit tester, 2026-11-10.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x2_pin as P  # noqa: E402


def _gi_paths():
    """bench/gi_paths from the worktree holding this file (walking up to its .git), so a
    mutant's copy in scratch/ finds it too."""
    d = HERE
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("%s is in no git worktree" % __file__)
        d = os.path.dirname(d)
    sys.path.append(os.path.join(d, "gen-inhibit", "tools", "bench"))
    import gi_paths
    return gi_paths


gi_paths = _gi_paths()
BASE = gi_paths.scratch("x2_pin", start=__file__)
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def raises(fn, exc):
    try:
        fn()
    except exc as e:
        return e
    except Exception as e:      # the wrong kind of failure is not the one wanted
        return None if exc is not Exception else e
    return None


def attempt(fn):
    """(value, None) or (None, the exception): a call a check judges, so a mutant that makes it
    raise is caught by the check, not by a crash."""
    try:
        return fn(), None
    except Exception as e:
        return None, e


def app_bytes(elf_fill, size=0x300):
    b = bytearray(size)
    b[0] = 0xE9
    b[0x20:0x24] = b"\x32\x54\xcd\xab"
    b[0xB0:0xD0] = bytes([elf_fill]) * 32
    return bytes(b)


def entry(offset, name, data, app=False):
    e = {"offset": offset, "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
         "md5": hashlib.md5(data).hexdigest()}
    if app:
        e["app"] = True
    return e


FILES = {
    "x2_app": [("0x0", "bootloader.bin", b"BOOT" * 64), ("0x8000", "partitions.bin", b"PART" * 32),
               ("0xe000", "boot_app0.bin", b"\xff" * 128), ("0x10000", "bridge.bin", app_bytes(0xA7))],
    "x2_slcan": [("0x0", "bootloader.bin", b"BOOT" * 64), ("0x8000", "partitions.bin", b"PART" * 32),
                 ("0xe000", "boot_app0.bin", b"\xff" * 128), ("0x10000", "slcan.bin", app_bytes(0xE5))],
}


def make_pin(path, mutate=None):
    images = {}
    for iname, fl in FILES.items():
        app = fl[-1][2]
        elf = app[0xB0:0xD0].hex()
        images[iname] = {"env": iname, "identity": elf[:8] if iname == "x2_app" else "slcan:V0101:NF001",
                         "identity_read": "test", "app_elf_sha256": elf,
                         "files": [entry(o, n, d, app=(n.endswith(("bridge.bin", "slcan.bin")))) for o, n, d in fl]}
    pin = {"_comment": "test", "pin": "testpin", "source": {"project": "test"}, "images": images}
    if mutate:
        mutate(pin)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pin, f)
    return path


def put_files(folder, pin_id="testpin", only=None, alter=None):
    """Writes FILES under folder/<pin_id>/<image>/; alter: {(image, name): bytes}."""
    for iname, fl in FILES.items():
        for _o, n, d in fl:
            if only and (iname, n) not in only:
                continue
            p = os.path.join(folder, pin_id, iname, n)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write((alter or {}).get((iname, n), d))


def fresh(name):
    d = os.path.join(BASE, name)
    os.makedirs(d)
    return d


# ------------------------------------------------------------------ the committed pin

def test_committed():
    pin, sha = P.read_pin(P.PIN_FILE)
    check("the committed pin is well formed", pin["pin"] == "36edf3e55552", pin["pin"])
    with open(P.PIN_FILE, "rb") as f:
        raw = f.read()
    check("read_pin gives the pin file's own sha256", sha == hashlib.sha256(raw).hexdigest())
    check("the committed pin is LF only, ASCII", b"\r" not in raw and max(raw) < 0x80)
    im = pin["images"]
    check("the committed pin has x2_app and x2_slcan", sorted(im) == ["x2_app", "x2_slcan"], sorted(im))
    check("x2_app's identity is 756d6714, its app ELF's first 8 digits",
          im["x2_app"]["identity"] == "756d6714" and im["x2_app"]["app_elf_sha256"].startswith("756d6714"))
    check("x2_slcan's identity is the SLCAN answers", im["x2_slcan"]["identity"] == "slcan:V0101:NF001")
    offs = [[f["offset"] for f in im[n]["files"]] for n in ("x2_app", "x2_slcan")]
    check("both write 0x0, 0x8000, 0xe000 and the app at 0x10000",
          offs == [["0x0", "0x8000", "0xe000", "0x10000"]] * 2 and
          all([f["offset"] for f in im[n]["files"] if f.get("app")] == ["0x10000"] for n in im), offs)
    check("the source is charge-interposer 4a596478, digest 36edf3e55552",
          pin["source"]["commit"].startswith("4a596478") and pin["source"]["src_digest"] == "36edf3e55552")


# ------------------------------------------------------------------ a malformed pin

def test_refused():
    d = fresh("refused")
    cases = [
        ("an unknown top-level key", lambda p: p.update(extra=1)),
        ("no source", lambda p: p.update(source={})),
        ("a pin id with a path in it", lambda p: p.update(pin="../x")),
        ("no images", lambda p: p.update(images={})),
        ("an image with an unknown key", lambda p: p["images"]["x2_app"].update(port="COM9")),
        ("an image without an identity", lambda p: p["images"]["x2_app"].update(identity="")),
        ("an 8-hex identity that is not the ELF's first digits",
         lambda p: p["images"]["x2_app"].update(identity="deadbeef")),
        ("an app ELF sha256 in upper case", lambda p: p["images"]["x2_app"].update(
            app_elf_sha256=p["images"]["x2_app"]["app_elf_sha256"].upper())),
        ("a file with a path in its name", lambda p: p["images"]["x2_app"]["files"][0].update(name="../boot.bin")),
        ("a file with a subfolder in its name", lambda p: p["images"]["x2_app"]["files"][0].update(name="a/b.bin")),
        ("a file without an md5", lambda p: p["images"]["x2_app"]["files"][0].pop("md5")),
        ("a file with an unknown key", lambda p: p["images"]["x2_app"]["files"][0].update(path="x")),
        ("a decimal offset", lambda p: p["images"]["x2_app"]["files"][0].update(offset="65536")),
        ("a size of 0", lambda p: p["images"]["x2_app"]["files"][0].update(size=0)),
        ("a size given as true", lambda p: p["images"]["x2_app"]["files"][0].update(size=True)),
        ("a short sha256", lambda p: p["images"]["x2_app"]["files"][0].update(sha256="ab" * 31)),
        ("a file's sha256 in upper case", lambda p: p["images"]["x2_app"]["files"][0].update(
            sha256=p["images"]["x2_app"]["files"][0]["sha256"].upper())),
        ("a short md5", lambda p: p["images"]["x2_app"]["files"][0].update(md5="ab" * 15)),
        ("two files at one offset", lambda p: p["images"]["x2_app"]["files"][1].update(offset="0x0")),
        ("two files of one name", lambda p: p["images"]["x2_app"]["files"][1].update(name="bootloader.bin")),
        ("no app file", lambda p: p["images"]["x2_app"]["files"][3].pop("app")),
        ("two app files", lambda p: p["images"]["x2_app"]["files"][0].update(app=True)),
        ("app given as false", lambda p: p["images"]["x2_app"]["files"][0].update(app=False)),
        ("no files", lambda p: p["images"]["x2_app"].update(files=[])),
    ]
    for k, (label, mut) in enumerate(cases):
        path = make_pin(os.path.join(d, "pin%d.json" % k), mut)
        e = raises(lambda: P.read_pin(path), P.Refused)
        check("refused: %s" % label, e is not None, e)
    with open(os.path.join(d, "bad.json"), "w") as f:
        f.write("{not json")
    check("refused: a pin that is not JSON", raises(lambda: P.read_pin(os.path.join(d, "bad.json")), P.Refused))
    check("refused: a pin file that is not there",
          raises(lambda: P.read_pin(os.path.join(d, "none.json")), P.Refused))
    good = make_pin(os.path.join(d, "good.json"))
    check("a well-formed test pin is read", P.read_pin(good)[0]["pin"] == "testpin")


# ------------------------------------------------------------------ load

def test_load():
    d = fresh("load")
    pin = make_pin(os.path.join(d, "pin.json"))
    files = os.path.join(d, "files")
    put_files(files)
    got, e = attempt(lambda: P.load(files, pin))
    check("load: the pinned files at <dir>/<pin>/<image>/ are found", e is None, e)
    if e is not None:
        return
    a = got["images"]["x2_app"]
    want_paths = [os.path.join(files, "testpin", "x2_app", n) for _o, n, _d in FILES["x2_app"]]
    check("load: each image's files at <dir>/<pin>/<image>/<name>, with their offsets",
          a["files"] == [[o, p] for (o, _n, _d), p in zip(FILES["x2_app"], want_paths)], a["files"])
    check("load: md5 by path, sha256 by name", a["md5"][want_paths[3]] == hashlib.md5(FILES["x2_app"][3][2]).hexdigest()
          and a["sha256"]["bridge.bin"] == hashlib.sha256(FILES["x2_app"][3][2]).hexdigest(), a)
    check("load: the identity and the app", a["expect"] == "a7a7a7a7" and a["app"] == ["0x10000", want_paths[3]],
          (a["expect"], a["app"]))
    check("load: the pin's id and sha256", got["pin"] == "testpin" and got["pin_sha256"] == P.read_pin(pin)[1])

    for label, kw in (
            ("a missing file", dict(only={(i, n) for i in FILES for _o, n, _d in FILES[i]} - {("x2_slcan", "boot_app0.bin")})),
            ("a file with other bytes, same size", dict(alter={("x2_app", "partitions.bin"): b"PARX" * 32})),
            ("a file one byte longer", dict(alter={("x2_app", "bootloader.bin"): b"BOOT" * 64 + b"\0"}))):
        f2 = os.path.join(d, "f_%d" % len(os.listdir(d)))
        put_files(f2, **kw)
        e = raises(lambda: P.load(f2, pin), P.Mismatch)
        check("load refuses %s" % label, e is not None, e)

    # the files right, the pin's row wrong in one digest only: each digest is compared on its own
    for label, field, value in (("size", "size", len(FILES["x2_app"][1][2]) + 1),
                                ("sha256", "sha256", "ab" * 32), ("md5", "md5", "ab" * 16)):
        def wrong(p, field=field, value=value):
            p["images"]["x2_app"]["files"][1][field] = value
        pw = make_pin(os.path.join(d, "pin_%s.json" % field), wrong)
        e = raises(lambda: P.load(files, pw), P.Mismatch)
        check("load refuses a file whose %s alone is not the pinned one" % label, e is not None, e)

    # an app whose bytes match its row but whose ELF hash is not the pinned one
    def other_elf(p):
        p["images"]["x2_slcan"]["app_elf_sha256"] = "ee" * 32
    pin2 = make_pin(os.path.join(d, "pin_elf.json"), other_elf)
    e = raises(lambda: P.load(files, pin2), P.Mismatch)
    check("load refuses an app whose ELF sha256 is not the pinned one", e is not None and "ELF" in str(e), e)
    check("load refuses a pin dir that is not a folder",
          raises(lambda: P.load(os.path.join(d, "nowhere"), pin), P.Mismatch) is not None)
    check("load refuses no pin dir", raises(lambda: P.load("", pin), P.Mismatch) is not None)


# ------------------------------------------------------------------ install

def test_install():
    d = fresh("install")
    pin = make_pin(os.path.join(d, "pin.json"))
    src_app, src_slcan, src_b0, decoy = (os.path.join(d, n) for n in ("app", "slcan", "b0", "decoy"))
    for folder, items in ((src_app, FILES["x2_app"][:2] + FILES["x2_app"][3:]),
                          (src_slcan, FILES["x2_slcan"][3:]), (src_b0, FILES["x2_app"][2:3])):
        os.makedirs(folder)
        for _o, n, data in items:
            with open(os.path.join(folder, n), "wb") as f:
                f.write(data)
    os.makedirs(decoy)
    with open(os.path.join(decoy, "bootloader.bin"), "wb") as f:
        f.write(b"NOTBOOT" * 9)
    dest = os.path.join(d, "pin_dir")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        n, e = attempt(lambda: P.install(dest, [decoy, src_app, src_slcan, src_b0], pin))
    check("install copies all 8 files, past a decoy of the same name with other bytes", n == 8, (n, e, out.getvalue()))
    got, e = attempt(lambda: P.load(dest, pin))
    check("install: load then finds every file", e is None and sorted(got["images"]) == ["x2_app", "x2_slcan"], e)
    if e is not None:
        return
    with contextlib.redirect_stdout(io.StringIO()):
        n2 = P.install(dest, [src_app, src_slcan, src_b0], pin)
    check("install again copies nothing: the files in place are the pinned ones", n2 == 0, n2)
    check("install leaves no .partial file",
          not [f for r, _ds, fs in os.walk(dest) for f in fs if f.endswith(".partial")])

    victim = os.path.join(dest, "testpin", "x2_app", "partitions.bin")
    with open(victim, "wb") as f:
        f.write(b"MINE")
    e = raises(lambda: P.install(dest, [src_app, src_slcan, src_b0], pin), P.Mismatch)
    with open(victim, "rb") as f:
        kept = f.read()
    check("install refuses to overwrite a file in place with other bytes, and leaves it", e is not None
          and kept == b"MINE", (e, kept))
    dest2 = os.path.join(d, "pin_dir2")
    e = raises(lambda: P.install(dest2, [src_app, src_b0], pin), P.Mismatch)
    check("install refuses when no source holds a pinned file (the slcan app), and copies nothing",
          e is not None and not os.path.exists(dest2), e)
    check("install refuses a source that is not a folder",
          raises(lambda: P.install(dest2, [os.path.join(d, "none")], pin), P.Refused) is not None)
    check("install refuses no source", raises(lambda: P.install(dest2, [], pin), P.Refused) is not None)

    # the command line: exit codes
    def code(*a):
        with contextlib.redirect_stdout(io.StringIO()) as o:
            rc = P.run(*a)
        return rc, o.getvalue()
    rc, txt = code("check", os.path.join(d, "pin_dir_ok"), [], pin)
    check("check on a folder that is not there exits 1", rc == 1, (rc, txt))
    rc, txt = code("install", os.path.join(d, "pin_dir_ok"), [src_app, src_slcan, src_b0], pin)
    check("install then check exits 0 and says OK", rc == 0 and "OK: every pinned file matches" in txt, (rc, txt))
    rc, txt = code("check", dest, [], pin)
    check("check with a file not matching exits 1", rc == 1 and "MISMATCH" in txt, (rc, txt))
    bad = make_pin(os.path.join(d, "badpin.json"), lambda p: p.update(pin=""))
    rc, txt = code("check", dest, [], bad)
    check("check with a malformed pin exits 2", rc == 2 and "REFUSED" in txt, (rc, txt))


def main():
    try:
        test_committed()
        test_refused()
        test_load()
        test_install()
    finally:
        if not gi_paths.remove_tree(BASE):
            fails.append("scratch folder not removed")
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
