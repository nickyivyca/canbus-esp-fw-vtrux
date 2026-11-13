#!/usr/bin/env python3
"""The WiCAN's pinned image (wican_pin.json beside this file): read it, and
check the image's files on this machine against it.

Charge-interposer tester, 2026-11-11 (port plan step 3; the interposer
reviewer's batch). An interposer run flashes the WiCAN only with a
gen-inhibit image built elsewhere and pinned as a tool of this harness,
never with anything built from this tree (AGENTS.md, "Never build WiCAN
firmware from an interposer worktree"); gen-inhibit pins its X2 images the
same way (gen-inhibit/tools/x2_pin.json). The pin names the gen-inhibit
commit and variant, the code hash and image hash, and each file's flash
offset, size, sha256 and md5. The files live outside git, in the local
settings' wican_image_dir, and nothing in the settings can change what the
pin names. Loading the image onto the WiCAN (USB esptool until madhouse has
its Wi-Fi adapter, then the shared flasher's OTA upload) is bench_wican.py's
(2026-11-12), which reads this module; nothing flashes from here.

    python3.13 -B charge-interposer/wican_pin.py check <wican_image_dir>
        every pinned file present with its size, sha256 and md5 -> exit 0,
        else each problem named -> exit 1

wican_pin_test.py checks the committed entry and the check, with can-fails.
"""

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "wican_pin.json")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
OFFSET = re.compile(r"0x[0-9a-f]+\Z")


class PinError(Exception):
    pass


def load(path=None):
    """The pin, checked for shape. PinError naming every problem."""
    with open(path or PIN_FILE, encoding="utf-8") as f:
        p = json.load(f)
    errs = []
    src, img = p.get("source") or {}, p.get("image") or {}
    if src.get("project") != "gen-inhibit":
        errs.append("source.project is not gen-inhibit")
    if not HEX40.match(src.get("commit") or ""):
        errs.append("source.commit is not a full lower-case commit")
    for k, obj in (("source.code_hash", src), ("image.image_hash", img)):
        if not HEX64.match(obj.get(k.split(".")[1]) or ""):
            errs.append("%s is not 64 lower-case hex digits" % k)
    files = img.get("files") or []
    if not files:
        errs.append("image.files is empty")
    names, offsets = set(), set()
    for f in files:
        n = f.get("name") or ""
        if not n or os.path.isabs(n) or ".." in n.split("/"):
            errs.append("file name %r is not a relative path inside the image folder" % n)
        if not OFFSET.match(f.get("offset") or ""):
            errs.append("%s: offset %r" % (n, f.get("offset")))
        if not isinstance(f.get("size"), int) or f["size"] <= 0:
            errs.append("%s: size %r" % (n, f.get("size")))
        if not HEX64.match(f.get("sha256") or ""):
            errs.append("%s: sha256" % n)
        if not HEX32.match(f.get("md5") or ""):
            errs.append("%s: md5" % n)
        if n in names or f.get("offset") in offsets:
            errs.append("%s: name or offset twice" % n)
        names.add(n)
        offsets.add(f.get("offset"))
    if sum(1 for f in files if f.get("app")) != 1:
        errs.append("not exactly one file marked app")
    if errs:
        raise PinError("wican_pin.json refused:\n  " + "\n  ".join(errs))
    return p


def check(folder, pin=None):
    """[] if every pinned file is in folder with its size, sha256 and md5;
    else one line per problem."""
    pin = pin or load()
    bad = []
    for f in pin["image"]["files"]:
        path = os.path.join(folder, *f["name"].split("/"))
        if not os.path.isfile(path):
            bad.append("%s: missing (%s)" % (f["name"], path))
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) != f["size"]:
            bad.append("%s: %d bytes, pinned %d" % (f["name"], len(data), f["size"]))
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            bad.append("%s: sha256 is not the pinned one" % f["name"])
        if hashlib.md5(data).hexdigest() != f["md5"]:
            bad.append("%s: md5 is not the pinned one" % f["name"])
    return bad


def main(argv):
    if len(argv) != 2 or argv[0] != "check":
        print("usage: wican_pin.py check <wican_image_dir>")
        return 2
    try:
        pin = load()
    except PinError as e:
        print(str(e))
        return 1
    bad = check(argv[1], pin)
    for b in bad:
        print("FAIL  " + b)
    print("%s: pin %s, gen-inhibit %s %s, image hash %s, %d file(s)" % (
        "OK" if not bad else "REFUSED", pin["pin"], pin["source"]["commit"][:8],
        pin["source"]["variant"], pin["image"]["image_hash"][:8], len(pin["image"]["files"])))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
