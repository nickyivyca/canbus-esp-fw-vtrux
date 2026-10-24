"""The command line.

    python -B -m vtrux_build release [--project DIR] [--check-only]
    python -B -m vtrux_build image BIN...     what the image library reads
                                              from each .bin, as JSON
    python -B -m vtrux_build digest DIR...    the R7 digest of each folder

Exit 0 on success, 1 on a refusal or an unreadable input, 2 on a usage
error.
"""

import argparse
import json
import sys

from . import release
from .digest import component_digest
from .image import AppImage, ImageError


def _image(args):
    out, rc = {}, 0
    for p in args.bins:
        try:
            out[p] = AppImage.read(p).summary()
        except (OSError, ImageError) as e:
            out[p] = {"error": str(e)}
            rc = 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return rc


def _digest(args):
    rc = 0
    for d in args.dirs:
        try:
            print("%s  %s" % (component_digest(d), d))
        except OSError as e:
            print("ERROR  %s: %s" % (d, e))
            rc = 1
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vtrux_build")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("release", help="a release build (SPEC section 3)")
    r.add_argument("--project", default=".",
                   help="the ESP-IDF project root (default: here)")
    r.add_argument("--check-only", action="store_true",
                   help="the pre-build refusals only; build nothing")
    i = sub.add_parser("image", help="read app images")
    i.add_argument("bins", nargs="+")
    d = sub.add_parser("digest", help="R7 component digests")
    d.add_argument("dirs", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "release":
        return release.main(args)
    if args.cmd == "image":
        return _image(args)
    return _digest(args)


if __name__ == "__main__":
    sys.exit(main())
