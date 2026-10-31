#!/usr/bin/env python3
"""Print the folder of the stock WiCAN component, vtrux/wican, at the version
gen-inhibit pins, for the shim's include path (shared/wican W3, port step 1a).

gen_inhibit.c, compiled here as shipped, includes the stock header
comm_server.h, which is the component's now, and since port step 1b the shim
compiles vtrux/can_port and vtrux/twai_port as shipped, from beside it. The
shim compiles
against the pinned version's read-only checkout, ../shared-<tag>/ beside the
worktree root (shared/tools R23), so it tests the headers the firmware builds
with. The tag is the one firmware/main/idf_component.yml gives vtrux/wican.

Prints the folder and exits 0, or names what is missing on stderr and exits 2.
The Makefile's WICANDIR overrides it, e.g. on a machine whose checkout is
elsewhere.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))            # gen-inhibit/
WORKTREE = os.path.dirname(PROJECT)
MANIFEST = os.path.join(PROJECT, "firmware", "main", "idf_component.yml")
NAME = "vtrux/wican"

_DEP = re.compile(r"^(\s+)%s:\s*(?:#.*)?$" % re.escape(NAME))
_VERSION = re.compile(r"""^\s+version:\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$""")


def pinned_tag(text):
    """The version a manifest's text gives vtrux/wican, or None."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = _DEP.match(ln)
        if not m:
            continue
        indent = len(m.group(1))
        for sub in lines[i + 1:]:
            if not sub.strip() or sub.lstrip().startswith("#"):
                continue
            if len(sub) - len(sub.lstrip()) <= indent:
                break
            v = _VERSION.match(sub)
            if v:
                return v.group(1)
        return None
    return None


def main():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            tag = pinned_tag(f.read())
    except OSError as e:
        print("wican_ref: cannot read %s: %s" % (MANIFEST, e), file=sys.stderr)
        return 2
    if not tag:
        print("wican_ref: %s gives %s no version" % (MANIFEST, NAME), file=sys.stderr)
        return 2
    checkout = os.path.join(os.path.dirname(WORKTREE), "shared-%s" % tag)
    folder = os.path.join(checkout, "shared", "wican")
    shared = os.path.dirname(folder)
    if not (os.path.isfile(os.path.join(folder, "comm_server.h"))
            and os.path.isfile(os.path.join(shared, "can_port", "include", "can.h"))
            and os.path.isfile(os.path.join(shared, "twai_port", "twai_port.c"))):
        print("wican_ref: no checkout of %s at %s; create it with\n"
              "  git -C <anchor> worktree add --detach %s %s\n"
              "or pass WICANDIR=<folder> to make"
              % (tag, folder, checkout, tag), file=sys.stderr)
        return 2
    print(folder.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
