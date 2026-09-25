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
    blob = open(elf, "rb").read()
    shas = sorted(set(re.findall(rb"\b[0-9a-f]{7}(?:-dirty)?\b", blob)))
    print("ELF:      %s" % os.path.basename(elf))
    print("symbols:  %d" % len(syms))
    if shas:
        print("rev-like strings in the image: %s"
              % ", ".join(s.decode() for s in shas[:6]))

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
