#!/usr/bin/env python3
"""Spec 11, "The page names the binary it runs": the page against the image file.

    py -3.14 spec11_page_check.py --image <flashed.bin> --other <other build.bin> --out runs/<x>.json

Reads `elf_sha256` off /gen_inhibit and compares it with the value in the flashed
image file's app descriptor, read by image_elf_sha.py (our parser, not the
device's code). The 12.4 row: "`elf_sha256` on the page equals the value in the
flashed image file's descriptor, and differs between two builds of one commit".

The negative control is the other build of the same commit: the page must NOT
equal its descriptor. If the two builds carry the same value the comparison
cannot tell them apart, and the run is VOID rather than a pass.

Verdicts:
  AS SPECIFIED   page == this image's value, and != the other build's
  NOT AS SPECIFIED  the page's value is absent, not 64 hex, or not this image's
  VOID           the two builds carry the same value (the control cannot fail)
"""

import argparse
import json
import re
import sys
import time
import urllib.request

import image_elf_sha as M

HOST = "192.168.80.1"
PORT = 80


def read_page(host=HOST, port=PORT, timeout=10.0):
    url = "http://%s:%d/gen_inhibit" % (host, port)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def judge(page, mine, other):
    v = page.get("elf_sha256") if isinstance(page, dict) else None
    res = {"page_elf_sha256": v, "image_elf_sha256": mine, "other_build_elf_sha256": other,
           "uptime_ms": page.get("uptime_ms") if isinstance(page, dict) else None}
    if mine == other:
        res["verdict"] = "VOID: the two builds carry the same value, so the control cannot fail"
    elif v is None:
        res["verdict"] = "NOT AS SPECIFIED: no elf_sha256 on the page"
    elif not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v):
        res["verdict"] = "NOT AS SPECIFIED: elf_sha256 is not 64 lower-case hex: %r" % (v,)
    elif v != mine:
        res["verdict"] = ("NOT AS SPECIFIED: page %s, image %s%s"
                          % (v, mine, " (it is the OTHER build's)" if v == other else ""))
    else:
        res["verdict"] = "AS SPECIFIED"
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--other", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    img, oth = M.read_file(a.image), M.read_file(a.other)
    page = read_page(a.host, a.port)
    res = judge(page, img["elf_sha256"], oth["elf_sha256"])
    res.update(when=time.strftime("%Y-%m-%dT%H:%M:%S"),
               image={k: img[k] for k in ("elf_sha256", "bytes", "file_offset", "version", "time", "file_md5")},
               image_file=a.image.replace("\\", "/").rsplit("/", 1)[-1],
               other_file=a.other.replace("\\", "/").rsplit("/", 1)[-1],
               page_git_version=page.get("git_version"), page_mode=page.get("mode"))
    json.dump(res, open(a.out, "w"), indent=1)
    print(res["verdict"])
    print("  page  %s\n  image %s\n  other %s" % (res["page_elf_sha256"], res["image_elf_sha256"],
                                                 res["other_build_elf_sha256"]))
    return 0 if res["verdict"] == "AS SPECIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
