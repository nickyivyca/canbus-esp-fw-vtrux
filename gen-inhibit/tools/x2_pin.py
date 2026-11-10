#!/usr/bin/env python3
"""The X2's pinned images for gen-inhibit's bench chain: load, check and install them.

    py -3.14 -B gen-inhibit/tools/x2_pin.py check --dir <x2_pin_dir>
    py -3.14 -B gen-inhibit/tools/x2_pin.py install --dir <x2_pin_dir> --source <folder> [--source <folder> ...]

The pin is x2_pin.json beside this file, committed: for each image (x2_app, x2_slcan) the
files to write, each with its offset, name, size, sha256 and md5, the identity the chain's
identify reads off the board, and the app's ELF sha256. The files are not in git. Each sits at
<x2_pin_dir>/<pin>/<image>/<name>, where x2_pin_dir comes from the local settings and <pin>
is the pin's id. load() refuses the pin unless every file is there with its size, sha256 and
md5, so a local setting can move the files but cannot change an image or its identity (user
ruling 2026-11-10, through the gen-inhibit reviewer).

  check    loads the pin from --dir and prints each file. Exit 0 when every file matches.
  install  copies each pinned file into --dir, taking it from the first --source folder that
           holds a file of that name with the pinned sha256 (not recursive). A file already in
           place with the pinned bytes is left alone. One in place with other bytes is refused,
           and nothing is overwritten. Then it runs check.

Exit 0 OK, 1 a file missing or not matching, 2 refused (the pin malformed, or bad arguments).
Gen-inhibit tester, 2026-11-10.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "x2_pin.json")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
OFFSET = re.compile(r"^0x[0-9a-f]+$")
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
TOP_KEYS = {"_comment", "pin", "source", "images"}
IMAGE_KEYS = {"env", "identity", "identity_read", "app_elf_sha256", "files"}
FILE_KEYS = {"offset", "name", "size", "sha256", "md5", "app"}
FILE_NEED = FILE_KEYS - {"app"}


class Refused(Exception):
    """The pin itself is malformed: nothing can be checked."""


class Mismatch(Exception):
    """A pinned file is missing, or is not the pinned bytes."""


def digests(path):
    """(size, sha256, md5) of a file."""
    s, m = hashlib.sha256(), hashlib.md5()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            s.update(chunk)
            m.update(chunk)
            n += len(chunk)
    return n, s.hexdigest(), m.hexdigest()


def app_elf_sha256(path):
    """The ESP32 app descriptor's ELF sha256 (offset 0xB0 of an app image)."""
    with open(path, "rb") as f:
        head = f.read(0xD0)
    if len(head) < 0xD0 or head[0] != 0xE9 or head[0x20:0x24] != b"\x32\x54\xcd\xab":
        raise Mismatch("%s is not an ESP32 app image with a descriptor" % path)
    return head[0xB0:0xD0].hex()


def read_pin(pin_file=PIN_FILE):
    """The pin, validated: (its dict, the sha256 of its bytes). Raises Refused."""
    try:
        with open(pin_file, "rb") as f:
            raw = f.read()
        pin = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise Refused("the pin %s cannot be read: %s" % (pin_file, e))
    if not isinstance(pin, dict) or set(pin) != TOP_KEYS:
        raise Refused("the pin's keys must be %s" % sorted(TOP_KEYS))
    if not (isinstance(pin["pin"], str) and NAME.match(pin["pin"])):
        raise Refused("the pin's id must be a plain folder name, not %r" % (pin["pin"],))
    if not isinstance(pin["source"], dict) or not pin["source"]:
        raise Refused("the pin names no source")
    images = pin["images"]
    if not isinstance(images, dict) or not images:
        raise Refused("the pin names no images")
    for iname, im in images.items():
        where = "image %s" % iname
        if not (isinstance(iname, str) and NAME.match(iname)):
            raise Refused("%s: not a plain folder name" % where)
        if not isinstance(im, dict) or set(im) != IMAGE_KEYS:
            raise Refused("%s: its keys must be %s" % (where, sorted(IMAGE_KEYS)))
        if not (isinstance(im["identity"], str) and im["identity"]):
            raise Refused("%s: no identity" % where)
        if not (isinstance(im["app_elf_sha256"], str) and HEX64.match(im["app_elf_sha256"])):
            raise Refused("%s: app_elf_sha256 must be 64 lowercase hex digits" % where)
        if re.match(r"^[0-9a-f]{8}$", im["identity"]) and not im["app_elf_sha256"].startswith(im["identity"]):
            raise Refused("%s: an 8-hex identity must be the app ELF sha256's first 8 digits (the 0x7F7 build id)"
                          % where)
        files = im["files"]
        if not isinstance(files, list) or not files:
            raise Refused("%s: no files" % where)
        offs, names, apps = set(), set(), 0
        for k, fe in enumerate(files):
            fw = "%s file %d" % (where, k)
            if not isinstance(fe, dict) or not FILE_NEED <= set(fe) <= FILE_KEYS:
                raise Refused("%s: its keys must be %s (and optionally app)" % (fw, sorted(FILE_NEED)))
            if not (isinstance(fe["offset"], str) and OFFSET.match(fe["offset"])):
                raise Refused("%s: offset must be lowercase hex like 0x10000" % fw)
            if not (isinstance(fe["name"], str) and NAME.match(fe["name"])) or fe["name"] in (".", ".."):
                raise Refused("%s: name must be a plain file name" % fw)
            if not isinstance(fe["size"], int) or isinstance(fe["size"], bool) or fe["size"] <= 0:
                raise Refused("%s: size must be a positive integer" % fw)
            if not (isinstance(fe["sha256"], str) and HEX64.match(fe["sha256"])):
                raise Refused("%s: sha256 must be 64 lowercase hex digits" % fw)
            if not (isinstance(fe["md5"], str) and HEX32.match(fe["md5"])):
                raise Refused("%s: md5 must be 32 lowercase hex digits" % fw)
            if "app" in fe and fe["app"] is not True:
                raise Refused("%s: app, when given, must be true" % fw)
            apps += fe.get("app") is True
            if fe["offset"] in offs or fe["name"] in names:
                raise Refused("%s: an offset or a name given twice" % fw)
            offs.add(fe["offset"])
            names.add(fe["name"])
        if apps != 1:
            raise Refused("%s: exactly one file must be the app, not %d" % (where, apps))
    return pin, hashlib.sha256(raw).hexdigest()


def image_dir(pin_dir, pin, iname):
    return os.path.join(pin_dir, pin["pin"], iname)


def load(pin_dir, pin_file=PIN_FILE):
    """{"pin", "pin_sha256", "dir", "images": {name: {"files": [[offset, path], ...],
    "md5": {path: md5}, "expect": identity, "app": [offset, path], "app_elf_sha256",
    "sha256": {name: sha256}}}}. Raises Refused (the pin) or Mismatch (a file)."""
    pin, pin_sha = read_pin(pin_file)
    if not pin_dir or not os.path.isdir(pin_dir):
        raise Mismatch("x2_pin_dir %r is not a folder" % (pin_dir,))
    out = {"pin": pin["pin"], "pin_sha256": pin_sha, "dir": pin_dir, "images": {}}
    for iname, im in sorted(pin["images"].items()):
        d = image_dir(pin_dir, pin, iname)
        files, md5, sha, app = [], {}, {}, None
        for fe in im["files"]:
            p = os.path.join(d, fe["name"])
            if not os.path.isfile(p):
                raise Mismatch("%s: %s is missing" % (iname, p))
            size, s, m = digests(p)
            if (size, s, m) != (fe["size"], fe["sha256"], fe["md5"]):
                raise Mismatch("%s: %s is not the pinned file (size %d sha256 %s md5 %s; pinned %d %s %s)"
                               % (iname, p, size, s[:16], m, fe["size"], fe["sha256"][:16], fe["md5"]))
            files.append([fe["offset"], p])
            md5[p] = m
            sha[fe["name"]] = s
            if fe.get("app"):
                app = [fe["offset"], p]
                elf = app_elf_sha256(p)
                if elf != im["app_elf_sha256"]:
                    raise Mismatch("%s: the app's ELF sha256 is %s, pinned %s" % (iname, elf[:16],
                                                                                im["app_elf_sha256"][:16]))
        out["images"][iname] = {"files": files, "md5": md5, "expect": im["identity"], "app": app,
                                "app_elf_sha256": im["app_elf_sha256"], "sha256": sha}
    return out


def install(pin_dir, sources, pin_file=PIN_FILE, say=print):
    """Copies each pinned file into place from the sources. Returns the number copied.
    Raises Refused or Mismatch; never overwrites a file that differs."""
    pin, _sha = read_pin(pin_file)
    if not sources:
        raise Refused("no --source folder")
    for s in sources:
        if not os.path.isdir(s):
            raise Refused("--source %r is not a folder" % s)
    if not pin_dir:
        raise Refused("no --dir")
    plan = []
    for iname, im in sorted(pin["images"].items()):
        d = image_dir(pin_dir, pin, iname)
        for fe in im["files"]:
            dest = os.path.join(d, fe["name"])
            want = (fe["size"], fe["sha256"], fe["md5"])
            if os.path.exists(dest):
                if not os.path.isfile(dest) or digests(dest) != want:
                    raise Mismatch("%s is in place with other bytes: refusing to overwrite it" % dest)
                plan.append((iname, fe["name"], None, dest))
                continue
            src = None
            for s in sources:
                c = os.path.join(s, fe["name"])
                if os.path.isfile(c) and digests(c) == want:
                    src = c
                    break
            if src is None:
                raise Mismatch("%s %s: no --source folder holds it with sha256 %s"
                               % (iname, fe["name"], fe["sha256"][:16]))
            plan.append((iname, fe["name"], src, dest))
    copied = 0
    for iname, name, src, dest in plan:
        if src is None:
            say("in place  %s/%s" % (iname, name))
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".partial"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        copied += 1
        say("copied    %s/%s" % (iname, name))
    return copied


def main(argv=None):
    ap = argparse.ArgumentParser(description="The X2's pinned images: check or install them.")
    ap.add_argument("action", choices=("check", "install"))
    ap.add_argument("--dir", required=True, help="x2_pin_dir, as the local settings name it")
    ap.add_argument("--source", action="append", default=[], help="install: a folder to copy from")
    a = ap.parse_args(argv)
    return run(a.action, a.dir, a.source, PIN_FILE)


def run(action, pin_dir, sources, pin_file):
    try:
        if action == "install":
            n = install(pin_dir, sources, pin_file)
            print("installed: %d file(s) copied" % n)
        got = load(pin_dir, pin_file)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    except Mismatch as e:
        print("MISMATCH: %s" % e)
        return 1
    print("pin %s (x2_pin.json sha256 %s)" % (got["pin"], got["pin_sha256"]))
    for iname, im in sorted(got["images"].items()):
        print("%s: identity %s, app ELF %s" % (iname, im["expect"], im["app_elf_sha256"][:16]))
        for off, p in im["files"]:
            print("  %-8s %s  sha256 %s" % (off, os.path.basename(p), im["sha256"][os.path.basename(p)]))
    print("OK: every pinned file matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
