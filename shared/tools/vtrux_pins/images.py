"""Pins on outside images (SPEC R36): the files of firmware images that a
project's bench flashes but does not build.

The pin is a committed JSON file, which the caller names:

    {"pin": "<id: a plain folder name>",
     "source": {"project": "<name>", "commit": "<40 hex>", ...},
     "images": {
       "<image name>": {
         "image_hash": "<64 hex: R1, the app descriptor's ELF sha256>",
         "code_hash": "<64 hex: R2>",
         "identity": "<optional: what the bench reads off the board>",
         "files": [{"offset": "0x10000", "name": "app.bin", "size": 1021520,
                    "sha256": "<64 hex>", "md5": "<32 hex>", "app": true},
                   ...]}}}

Other keys at the top, in `source` and in each image are recorded and not
judged. A file entry has only the keys above.

The files are not in git. Each is at <dir>/<pin id>/<image name>/<name>,
where dir comes from the local settings. Nothing in the settings can change
what the pin names: load() refuses every file that is not the pinned bytes.

read_pin() raises Refused for a malformed pin. load() and install() raise
Mismatch for a file that is missing or is not the pinned one.
"""

import hashlib
import json
import os
import re
import shutil

from vtrux_build.image import AppImage, ImageError

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX8 = re.compile(r"[0-9a-f]{8}\Z")
OFFSET = re.compile(r"0x[0-9a-f]+\Z")
NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
FILE_KEYS = {"offset", "name", "size", "sha256", "md5", "app"}
FILE_NEED = FILE_KEYS - {"app"}


class Refused(Exception):
    """The pin itself is malformed, or the arguments are: nothing is
    checked."""


class Mismatch(Exception):
    """A pinned file is missing, or is not the pinned bytes."""


def _plain(name):
    return isinstance(name, str) and bool(NAME.match(name)) \
        and name not in (".", "..")


def _hex(rx, v):
    return isinstance(v, str) and bool(rx.match(v))


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


def read_pin(pin_file):
    """The pin, checked for shape: (its dict, the sha256 of its bytes).
    Raises Refused."""
    try:
        with open(pin_file, "rb") as f:
            raw = f.read()
        pin = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise Refused("the pin %s cannot be read: %s" % (pin_file, e))
    if not isinstance(pin, dict):
        raise Refused("the pin %s is not a JSON object" % pin_file)
    if not _plain(pin.get("pin")):
        raise Refused("the pin's id must be a plain folder name, not %r"
                      % (pin.get("pin"),))
    src = pin.get("source")
    if not isinstance(src, dict):
        raise Refused("the pin names no source")
    if not (isinstance(src.get("project"), str) and src["project"]):
        raise Refused("the pin's source names no project")
    if not _hex(HEX40, src.get("commit")):
        raise Refused("the pin's source.commit must be a full commit (40 "
                      "lower-case hex digits)")
    images = pin.get("images")
    if not isinstance(images, dict) or not images:
        raise Refused("the pin names no images")
    for iname, im in images.items():
        where = "image %s" % iname
        if not _plain(iname):
            raise Refused("%s: not a plain folder name" % where)
        if not isinstance(im, dict):
            raise Refused("%s: not a JSON object" % where)
        for k in ("image_hash", "code_hash"):
            if not _hex(HEX64, im.get(k)):
                raise Refused("%s: %s must be 64 lower-case hex digits"
                              % (where, k))
        if "identity" in im:
            ident = im["identity"]
            if not (isinstance(ident, str) and ident):
                raise Refused("%s: identity, when given, must be a non-empty "
                              "string" % where)
            if HEX8.match(ident) and not im["image_hash"].startswith(ident):
                raise Refused("%s: an 8-hex identity must be the image hash's "
                              "first 8 digits" % where)
        files = im.get("files")
        if not isinstance(files, list) or not files:
            raise Refused("%s: no files" % where)
        offs, names, apps = set(), set(), 0
        for k, fe in enumerate(files):
            fw = "%s file %d" % (where, k)
            if not isinstance(fe, dict) or not FILE_NEED <= set(fe) <= FILE_KEYS:
                raise Refused("%s: its keys must be %s, and app if it is the "
                              "app" % (fw, sorted(FILE_NEED)))
            if not _hex(OFFSET, fe["offset"]):
                raise Refused("%s: offset must be lower-case hex like 0x10000"
                              % fw)
            if not _plain(fe["name"]):
                raise Refused("%s: name must be a plain file name" % fw)
            if (not isinstance(fe["size"], int) or isinstance(fe["size"], bool)
                    or fe["size"] <= 0):
                raise Refused("%s: size must be a positive integer" % fw)
            if not _hex(HEX64, fe["sha256"]):
                raise Refused("%s: sha256 must be 64 lower-case hex digits" % fw)
            if not _hex(HEX32, fe["md5"]):
                raise Refused("%s: md5 must be 32 lower-case hex digits" % fw)
            if "app" in fe and fe["app"] is not True:
                raise Refused("%s: app, when given, must be true" % fw)
            apps += fe.get("app") is True
            if fe["offset"] in offs or fe["name"] in names:
                raise Refused("%s: an offset or a name given twice" % fw)
            offs.add(fe["offset"])
            names.add(fe["name"])
        if apps != 1:
            raise Refused("%s: exactly one file must be the app, not %d"
                          % (where, apps))
    return pin, hashlib.sha256(raw).hexdigest()


def image_dir(pin_dir, pin, iname):
    return os.path.join(pin_dir, pin["pin"], iname)


def load(pin_file, pin_dir):
    """Every file checked against the pin. Returns

        {"pin": id, "pin_sha256": sha256 of the pin file's bytes, "dir": dir,
         "images": {name: {"files": [[offset, path], ...] in flash order,
                           "app": [offset, path], "identity": str or None,
                           "image_hash", "code_hash",
                           "sha256": {name: sha256}, "md5": {path: md5}}}}

    Raises Refused (the pin) or Mismatch (a file)."""
    pin, pin_sha = read_pin(pin_file)
    if not pin_dir or not os.path.isdir(pin_dir):
        raise Mismatch("the image folder %r is not a folder" % (pin_dir,))
    out = {"pin": pin["pin"], "pin_sha256": pin_sha, "dir": pin_dir,
           "images": {}}
    for iname, im in sorted(pin["images"].items()):
        d = image_dir(pin_dir, pin, iname)
        files, md5, sha, app = [], {}, {}, None
        for fe in sorted(im["files"], key=lambda e: int(e["offset"], 16)):
            p = os.path.join(d, fe["name"])
            if not os.path.isfile(p):
                raise Mismatch("%s: %s is missing" % (iname, p))
            size, s, m = digests(p)
            if (size, s, m) != (fe["size"], fe["sha256"], fe["md5"]):
                raise Mismatch("%s: %s is not the pinned file (size %d sha256 "
                               "%s md5 %s; pinned %d %s %s)"
                               % (iname, p, size, s[:16], m, fe["size"],
                                  fe["sha256"][:16], fe["md5"]))
            files.append([fe["offset"], p])
            md5[p] = m
            sha[fe["name"]] = s
            if fe.get("app"):
                app = [fe["offset"], p]
                try:
                    a = AppImage.read(p)
                except (ImageError, OSError) as e:
                    raise Mismatch("%s: %s does not parse as an ESP-IDF app "
                                   "image: %s" % (iname, p, e))
                if a.image_hash != im["image_hash"]:
                    raise Mismatch("%s: the app's image hash is %s, pinned %s"
                                   % (iname, a.image_hash[:16],
                                      im["image_hash"][:16]))
                if a.code_hash != im["code_hash"]:
                    raise Mismatch("%s: the app's code hash is %s, pinned %s"
                                   % (iname, a.code_hash[:16],
                                      im["code_hash"][:16]))
        out["images"][iname] = {
            "files": files, "app": app, "identity": im.get("identity"),
            "image_hash": im["image_hash"], "code_hash": im["code_hash"],
            "sha256": sha, "md5": md5}
    return out


def install(pin_file, pin_dir, sources, say=print):
    """Copy each pinned file into place from the sources, then load(). Returns
    (number copied, load()'s result). Every file is planned before any is
    copied; a file in place with other bytes is refused, never overwritten.
    Raises Refused or Mismatch."""
    pin, _ = read_pin(pin_file)
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
            if os.path.lexists(dest):
                if not os.path.isfile(dest) or digests(dest) != want:
                    raise Mismatch("%s is in place with other bytes: it is not "
                                   "overwritten" % dest)
                plan.append((iname, fe["name"], None, dest))
                continue
            src = None
            for s in sources:
                c = os.path.join(s, fe["name"])
                if os.path.isfile(c) and digests(c) == want:
                    src = c
                    break
            if src is None:
                raise Mismatch("%s %s: no --source folder holds it with size "
                               "%d sha256 %s" % (iname, fe["name"], fe["size"],
                                                 fe["sha256"][:16]))
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
    return copied, load(pin_file, pin_dir)
