#!/usr/bin/env python3
"""What a host build of gen-inhibit's core needs for its diag frames, since stage (b)
batch 2 (gen-inhibit/main db1bfeae): the frame table generated from the DBC,
firmware/main/gi_diag_table.c, and shared/diag's packer, diag_pack.c with its include
folder, from the read-only checkout of the tag firmware/main/idf_component.yml pins
vtrux/diag at, ../shared-<tag>/ beside the worktree root (shared/tools R23; in
~/gi_host, the copy gi_host_run.sh ships, see diag_dir()). With
--wrap, also misfit_wrap.c and the link flag that routes every diag_pack_message()
call through it, so the packer's misfit report reaches the test (spec 10, 12.3 item 2).

  diag_host.py args [--wrap] [--diag DIR]
                                 every argument a cc line adds, on one line (DIR: another
                                 shared/diag folder than the pinned one)
  diag_host.py dir               the pinned shared/diag folder

Exit 0, or 2 with what is missing on stderr (no pin, no checkout). Gen-inhibit
tester, 2026-11-23.
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))            # gen-inhibit/
WORKTREE = os.path.dirname(PROJECT)
FIRMWARE = os.path.join(PROJECT, "firmware")
MAIN = os.path.join(FIRMWARE, "main")
MANIFEST = os.path.join(MAIN, "idf_component.yml")
NAME = "vtrux/diag"
TABLE_C = os.path.join(MAIN, "gi_diag_table.c")
WRAP_C = os.path.join(HERE, "misfit_wrap.c")
WRAP_LD = "-Wl,--wrap=diag_pack_message"


class DiagHostError(Exception):
    pass


def _board_ref():
    p = os.path.join(PROJECT, "test", "build_checks", "board_ref.py")
    spec = importlib.util.spec_from_file_location("gi_board_ref", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pinned_tag(manifest=MANIFEST):
    try:
        with open(manifest, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise DiagHostError("cannot read %s (%s)" % (manifest, e))
    tag = _board_ref().parse_pin(text, NAME)
    if not tag:
        raise DiagHostError("%s does not pin %s" % (manifest, NAME))
    return tag


def diag_dir(tag=None):
    """The pinned shared/diag folder. Raises DiagHostError when its checkout is missing.

    In ~/gi_host, the copy tools/bench/gi_host_run.sh builds in, there is no checkout
    beside the project folder: the script ships the pinned shared/diag to
    <project>/shared/diag at every sync, so that folder is taken when it is there. The
    repo's project folder has no shared/ of its own."""
    export = os.path.join(PROJECT, "shared", "diag")
    if tag is None and os.path.isfile(os.path.join(export, "diag_pack.c")):
        return export
    tag = tag or pinned_tag()
    d = os.path.join(os.path.dirname(WORKTREE), "shared-" + tag, "shared", "diag")
    if not os.path.isfile(os.path.join(d, "diag_pack.c")):
        raise DiagHostError("no %s: make it from the anchor with git worktree add --detach "
                            "../shared-%s %s, then chmod -R a-w ../shared-%s/shared" % (d, tag, tag, tag))
    return d


def args(wrap=False, diag=None, table_c=TABLE_C):
    """The sources, include flags and (with wrap) the link flag, as a list."""
    d = diag or diag_dir()
    out = [table_c, os.path.join(d, "diag_pack.c"), "-I" + os.path.join(d, "include")]
    if wrap:
        out += [WRAP_C, "-I" + HERE, WRAP_LD]
    return out


def main(argv):
    if not argv or argv[0] not in ("args", "dir"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[0] == "dir":
            print(diag_dir())
        else:
            rest = argv[1:]
            diag = rest[rest.index("--diag") + 1] if "--diag" in rest[:-1] else None
            print(" ".join(args(wrap="--wrap" in rest, diag=diag)))
    except DiagHostError as e:
        print("diag_host: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
