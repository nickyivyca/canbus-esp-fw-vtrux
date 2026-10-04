#!/usr/bin/env python3
"""Review E3: assert things about the built image that no source test can.

Run it against a freshly built ELF:

    python3 elf_checks.py ../../build/wican-fw_obd_<rev>.elf

TWO TRAPS, BOTH HIT WHILE WRITING THIS, AND BOTH MAKE A CHECK LIE:

1. `nm`, NOT `grep`. Searching the ELF for the string "slcan_parse_str" finds a
   hit even in an image where the symbol is gone -- the name survives in debug
   info. The reviewing session's negative control used `grep -a` and reported
   the symbol present in a post-A3 image where `nm` shows it absent. A check
   built on grep would pass whatever A3 did.

2. THE FILENAME IS NOT THE CONTENT. ESP-IDF fixes the output name at CONFIGURE
   time, so `build/wican-fw_obd_<rev>.elf` carries whatever rev was current
   when cmake last ran -- not the code that was compiled. On this machine
   `wican-fw_obd_37addf4-dirty.elf` contains post-A3 code, because it was
   rebuilt after A3 landed without reconfiguring. So this script reports the
   GIT_SHA it finds INSIDE the image and never infers a rev from the path.
"""

import os
import re
import subprocess
import sys

NM_CANDIDATES = (
    "riscv32-esp-elf-nm",
    os.path.expanduser("~/.espressif/tools/riscv32-esp-elf/"
                       "esp-14.2.0_20241119/riscv32-esp-elf/bin/"
                       "riscv32-esp-elf-nm"),
)

# Symbols that must NOT be linked in, with the reason and what to do about it.
FORBIDDEN = (
    ("slcan_parse_str",
     "spec 3.2 / review A3: the SLCAN COMMAND PARSER. Its only callers are the "
     "three disabled branches in main.c, and the linker drops it with "
     "-ffunction-sections -Wl,--gc-sections. If it is back, a branch was "
     "re-enabled -- any client could transmit an arbitrary 0x051 and open or "
     "close the bus."),
)


def find_nm():
    for c in NM_CANDIDATES:
        try:
            subprocess.run([c, "--version"], capture_output=True, check=True)
            return c
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def head_rev(elf):
    """The revision git would stamp, read from the tree the ELF was built in.

    Returned so the caller can ask whether the image actually carries it. Any failure
    gives None and the caller says it cannot tell, rather than printing something that
    looks like an answer.
    """
    d = os.path.dirname(os.path.abspath(elf))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, ".git")):
            try:
                r = subprocess.run(
                    ["git", "-C", d, "describe", "--tags", "--always", "--dirty"],
                    capture_output=True, text=True)
                v = r.stdout.strip()
                return v or None
            except OSError:
                return None
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    elf = sys.argv[1]
    if not os.path.exists(elf):
        print("no such file: %s" % elf)
        return 2

    nm = find_nm()
    if nm is None:
        print("FAIL: no riscv32-esp-elf-nm on PATH. This check CANNOT fall back "
              "to grep -- see the header; grep finds the name in debug info and "
              "would pass an image that still links the symbol out or in.")
        return 2

    out = subprocess.run([nm, elf], capture_output=True, text=True).stdout
    syms = set()
    for line in out.splitlines():
        p = line.split()
        if p:
            syms.add(p[-1])

    # What rev is actually IN the image, since the filename cannot say.
    #
    # THIS USED TO PRINT sorted(...)[:6] AND SO NEVER SHOWED THE REVISION. On the
    # ae7b3cb ELF the six alphabetically-first 7-hex strings are 0573f0d..56b5fed and
    # 'ae7b3cb' sorts after all of them -- the one rev the line existed to show was the
    # one it could not show, while six strings that are NOT the image's revision read
    # like provenance. Found by the reviewing session, 2026-10-03.
    #
    # It now answers the actual question: is the EXPECTED revision in this image, and
    # how many times does it appear. The expectation comes from argv[2] if given, else
    # from git HEAD; if neither is available it says so rather than guessing.
    #
    # IT IS NOT A GATE and must not be read as one. A 7-character hex string in a binary
    # is weak evidence -- the regex matches any 7-hex run, so most hits are noise. The
    # check that actually holds is build_output.py reading the version out of
    # esp_app_desc and comparing it against HEAD.
    blob = open(elf, "rb").read()
    print("ELF:      %s" % os.path.basename(elf))
    print("symbols:  %d" % len(syms))

    expect = sys.argv[2] if len(sys.argv) > 2 else head_rev(elf)
    if expect:
        #
        # THE REVISION AND THE TREE'S STATE ARE TWO FACTS, reported separately. Asking
        # for 'ae7b3cb-dirty' in an image built from a clean tree correctly finds
        # nothing, and printing that as "NOT FOUND IN THE IMAGE" reads as a provenance
        # failure on the image when all it means is that the tree moved after the
        # build. The first run of this code did exactly that.
        #
        dirty = expect.endswith("-dirty")
        rev = expect[: -len("-dirty")] if dirty else expect
        hits = len(re.findall(re.escape(rev.encode()), blob))
        print("revision %s: %s (%d hit%s) -- corroboration only; "
              "build_output.py's esp_app_desc row is the gate"
              % (rev,
                 "present in the image" if hits else "NOT FOUND IN THE IMAGE",
                 hits, "" if hits == 1 else "s"))
        if dirty:
            print("working tree: DIRTY now, so it has moved since this image was "
                  "built -- the image itself is not implicated, but it is no longer "
                  "the current sources")
    else:
        print("revision: no expectation available (pass one as argv[2], or run "
              "inside the git tree) -- cannot say what rev this image carries")

    shas = sorted(set(re.findall(rb"\b[0-9a-f]{7}(?:-dirty)?\b", blob)))
    if shas and len(shas) <= 12:
        print("all rev-like strings: %s" % ", ".join(s.decode() for s in shas))
    elif shas:
        print("rev-like strings: %d distinct (not listed; most are not revisions)"
              % len(shas))

    bad = 0
    for name, why in FORBIDDEN:
        present = name in syms
        print("\n%-22s %s" % (name, "PRESENT -- FAIL" if present else "absent"))
        if present:
            print("    " + why)
            bad += 1

    print()
    if bad:
        print("%d forbidden symbol(s) linked in" % bad)
    else:
        print("no forbidden symbol is linked in")
        print("NEGATIVE CONTROL: re-enable one of main.c's disabled SLCAN")
        print("branches and rebuild; slcan_parse_str must come back.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
