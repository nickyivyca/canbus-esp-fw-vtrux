"""A stand-in for esptool.py, for the runner's own tests. No hardware.

Every call is appended to $FAKE_ESPTOOL_LOG as one line of argv. The board
is the file $FAKE_BOARD: a successful write-flash copies the LAST file
written into it, so an identify hook that reads $FAKE_BOARD sees the image
just flashed.

$FAKE_ESPTOOL_MODE:
  ok           everything succeeds
  chipid-fail  every chip-id fails (auto-reset broken)
  write-fail   write-flash fails without "Hash of data verified"
  after-fail   chip-id fails once any write-flash has happened
$FAKE_ESPTOOL_CHIP / $FAKE_ESPTOOL_MAC: what chip-id reports (default
ESP32-S3, 02:00:00:00:00:01), in esptool v5's form.
"""

import os
import sys

argv = sys.argv[1:]
mode = os.environ.get("FAKE_ESPTOOL_MODE", "ok")
log = os.environ["FAKE_ESPTOOL_LOG"]
board = os.environ["FAKE_BOARD"]
with open(log, "a") as f:
    f.write(" ".join(argv) + "\n")
wrote = os.path.exists(board + ".written")

if "chip-id" in argv:
    if mode == "chipid-fail" or (mode == "after-fail" and wrote):
        print("A fatal error occurred: Failed to connect")
        sys.exit(2)
    print("Chip type:          %s (QFN56) (revision v0.2)"
          % os.environ.get("FAKE_ESPTOOL_CHIP", "ESP32-S3"))
    print("MAC:                %s"
          % os.environ.get("FAKE_ESPTOOL_MAC", "02:00:00:00:00:01"))
    sys.exit(0)

if "write-flash" in argv:
    if mode == "write-fail":
        print("A fatal error occurred: write failed")
        sys.exit(2)
    i = argv.index("write-flash") + 1
    pairs = argv[i:]
    files = pairs[1::2]
    for p in files:
        print("Wrote %s" % p)
        print("Hash of data verified.")
    with open(files[-1], "rb") as src, open(board, "wb") as dst:
        dst.write(src.read())
    open(board + ".written", "w").close()
    sys.exit(0)

print("fake esptool: unknown command %r" % argv)
sys.exit(1)
