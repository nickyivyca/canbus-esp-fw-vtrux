#!/usr/bin/env python3
"""Build scenarios/replay-longarm-300s-all.scn, the stimulus of cases 33 and 53.

The file is generated and never committed: it is every frame of a 300 s capture
that lives in the project folder (reverse-it, on SeaDrive), and that capture is
not published, so neither is anything derived from it. What keeps a rebuilt
copy honest is stimulus.fnv, which IS committed -- each replay case checks the
stimulus it ran on against it (spec 12.4).

The Makefile runs this before shim_test, every time. It rebuilds only when the
file is missing or older than any of its inputs: the capture,
../gen_inhibit_host/from_capture.py, or this script. Rebuilding on a newer
converter is the point -- a changed converter must not leave a stale file
passing (reviewer, 2026-10-20).

The parser is an input too. Since the canre-pin step (2026-10-26) it is canre
at the commit ../../tools/bench/pins.json pins, which from_capture.py takes
through canre_pin.py and nowhere else. A pin move leaves every file's mtime
alone except pins.json's, which also changes for its other entries, so the
pin entry itself is the input: each build writes the entry it was built with
beside the file (STAMP), and a missing stamp, or one that differs from the
entry now, rebuilds (reviewer, 2026-10-26).

The project folder comes from REVERSE_IT_DIR and nowhere else. Unset is an
error, never a guess (methodology, 2026-10-20). Its canre/ still marks it as
the reverse-it root, as in ../gen_inhibit_host/project_dir.py, but canre is no
longer read from it. It is passed to from_capture.py explicitly
(--project-dir), so the capture found here is the one converted, whatever
from_capture.py's own default is (reviewer, 2026-10-20).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROM_CAPTURE = os.path.normpath(
    os.path.join(HERE, "..", "gen_inhibit_host", "from_capture.py"))
OUT = os.path.join(HERE, "scenarios", "replay-longarm-300s-all.scn")
# The canre pin entry OUT was built with, as JSON. Beside OUT, so in scenarios/,
# which is not in git.
STAMP = OUT + ".canre"
CAPTURE = os.path.join("projects", "vtrux", "notes", "artifacts", "gen-inhibit",
                       "runs", "scottsvalley_armable_300s.log")

sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "tools", "bench")))
import canre_pin  # noqa: E402


def project_dir():
    v = os.environ.get("REVERSE_IT_DIR", "").strip()
    if not v:
        sys.exit(
            "REVERSE_IT_DIR is not set, so the long-arm stimulus of cases 33 "
            "and 53 cannot be built and shim_test is not run.\n"
            "  Set it to the absolute path of the reverse-it project folder on "
            "this machine, e.g.\n"
            "    REVERSE_IT_DIR=/path/to/reverse-it make\n"
            "  Under WSL pass a Windows value through with "
            "WSLENV=REVERSE_IT_DIR/p.")
    v = os.path.normpath(v)
    if not os.path.isdir(os.path.join(v, "canre")):
        sys.exit("REVERSE_IT_DIR=%s has no canre/, so it is not the reverse-it "
                 "project folder." % v)
    return v


def canre_entry():
    """pins.json's canre entry, as canre_pin reads it. Exits on a bad entry."""
    try:
        return canre_pin.pin()
    except (OSError, ValueError, canre_pin.CanrePinError) as e:
        sys.exit("build_longarm: %s" % e)


def built_with():
    """The canre entry STAMP records, or None if it is missing or unreadable."""
    try:
        with open(STAMP, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def stale(capture, entry):
    if not os.path.exists(OUT):
        return "missing"
    built = os.path.getmtime(OUT)
    for p in (capture, FROM_CAPTURE, os.path.abspath(__file__)):
        if os.path.getmtime(p) > built:
            return "older than %s" % os.path.basename(p)
    was = built_with()
    if was is None:
        return "no record of the canre pin it was built with"
    if was != entry:
        return "built with another canre pin entry than %s" % entry["commit"][:7]
    return None


def write_stamp(entry):
    tmp = STAMP + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(entry, f, sort_keys=True)
        f.write("\n")
    os.replace(tmp, STAMP)


def main():
    root = project_dir()
    capture = os.path.join(root, CAPTURE)
    if not os.path.isfile(capture):
        sys.exit("the capture %s is not in REVERSE_IT_DIR=%s" % (CAPTURE, root))
    entry = canre_entry()
    why = stale(capture, entry)
    if why is None:
        print("%s is up to date" % os.path.relpath(OUT, HERE))
        return 0
    print("building %s (%s)" % (os.path.relpath(OUT, HERE), why))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # Written under a temporary name and renamed once complete, so an
    # interrupted build cannot leave a truncated file that is newer than its
    # inputs and would therefore never be rebuilt. The stamp is written after
    # the rename: a build stopped between the two leaves the old stamp, which
    # either names this same entry (and the file was built with it) or differs
    # (and the next run rebuilds).
    tmp = OUT + ".tmp"
    rc = subprocess.call(
        [sys.executable, FROM_CAPTURE, capture, "--project-dir", root,
         "--channel", "0", "--at", "0", "--for", "300", "--all-ids",
         "--out", tmp],
        cwd=os.path.dirname(FROM_CAPTURE))
    if rc != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.exit("from_capture.py failed (exit %d); %s not built"
                 % (rc, os.path.relpath(OUT, HERE)))
    os.replace(tmp, OUT)
    write_stamp(entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
