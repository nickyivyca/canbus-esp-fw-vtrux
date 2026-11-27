"""The command line of vtrux_pins.

    python -B -m vtrux_pins can-tools install|check|where --pins FILE
    python -B -m vtrux_pins canre install|check|where --pins FILE
    python -B -m vtrux_pins image check --pin FILE --dir DIR
    python -B -m vtrux_pins image install --pin FILE --dir DIR --source FOLDER [--source FOLDER ...]

can-tools and canre: exit 0; 1 with the reason at a refusal; 2 for a usage
error. image: exit 0 when every file matches; 1 when a file is missing or
does not match; 2 when the pin is refused or the arguments are wrong.
"""

import argparse
import sys

from . import canre, cantools, images
from .pinsfile import PinsError

PACKAGES = {"can-tools": cantools, "canre": canre}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print("%s: error: %s" % (self.prog, message), file=sys.stderr)
        sys.exit(2)


def _parser():
    ap = _Parser(prog="vtrux_pins",
                 description="Pins on outside packages (R35) and outside "
                             "images (R36).")
    sub = ap.add_subparsers(dest="what", required=True)
    for key in PACKAGES:
        p = sub.add_parser(key, help="the %s pin" % key)
        p.add_argument("action", choices=("install", "check", "where"))
        p.add_argument("--pins", required=True, help="the pins file")
    p = sub.add_parser("image", help="an image pin")
    p.add_argument("action", choices=("check", "install"))
    p.add_argument("--pin", required=True, help="the image pin file")
    p.add_argument("--dir", required=True,
                   help="the image folder the local settings name")
    p.add_argument("--source", action="append", default=[],
                   help="install: a folder to copy from")
    return ap


def _package(key, action, pins):
    mod = PACKAGES[key]
    try:
        p = mod.pin(pins)
        if action == "install":
            d, now = mod.install(pins)
            print("%s %s %s at %s" % (key, p["commit"],
                                      "written and checked" if now
                                      else "already there, checked", d))
        elif action == "check":
            d = mod.check(pins)
            print("%s %s checked at %s" % (key, p["commit"], d))
        else:
            print(mod.folder(pins, p))
    except PinsError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 1
    return 0


def _image(action, pin, pin_dir, sources):
    if action == "check" and sources:
        print("REFUSED: --source is for install only", file=sys.stderr)
        return 2
    try:
        if action == "install":
            n, got = images.install(pin, pin_dir, sources)
            print("installed: %d file(s) copied" % n)
        else:
            got = images.load(pin, pin_dir)
    except images.Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    except images.Mismatch as e:
        print("MISMATCH: %s" % e, file=sys.stderr)
        return 1
    print("pin %s (pin file sha256 %s)" % (got["pin"], got["pin_sha256"]))
    for iname, im in sorted(got["images"].items()):
        print("%s: image hash %s, code hash %s%s" % (
            iname, im["image_hash"][:16], im["code_hash"][:16],
            ", identity %s" % im["identity"] if im["identity"] else ""))
        for off, p in im["files"]:
            name = p.replace("\\", "/").rsplit("/", 1)[-1]
            print("  %-8s %s  sha256 %s" % (off, name, im["sha256"][name]))
    print("OK: every pinned file matches")
    return 0


def main(argv=None):
    a = _parser().parse_args(argv)
    if a.what == "image":
        return _image(a.action, a.pin, a.dir, a.source)
    return _package(a.what, a.action, a.pins)
