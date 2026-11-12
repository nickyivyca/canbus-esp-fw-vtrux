"""A stand-in for esptool.py, for the runner's own tests. No hardware.

Every call is appended to $FAKE_ESPTOOL_LOG as one line of argv. The board
is the file $FAKE_BOARD: a successful write_flash copies the LAST file
written into it, so an identify hook that reads $FAKE_BOARD sees the image
just flashed.

$FAKE_ESPTOOL_FORM, the esptool it stands in for:
  v4 (default) esptool v4, ESP-IDF v5.4.x's: only the underscore command
               names (chip_id, write_flash); a dashed one (chip-id,
               write-flash) is refused as argparse refuses it, on stderr,
               exit 2. chip_id prints "Chip is ..." after a preamble.
  v5           esptool v5: both names. An underscore name first prints
               deprecation warnings on STDOUT, as v5.0.2 does (the
               gen-inhibit tester on NICKY-XPS, 2026-11-11, help only; the
               wording here is made up, the place is not), then the
               output; chip_id prints "Chip type: ...".
$FAKE_ESPTOOL_MODE:
  ok           everything succeeds
  chipid-fail  every chip_id fails (auto-reset broken)
  write-fail   write_flash fails without "Hash of data verified"
  after-fail   chip_id fails once any write_flash has happened
$FAKE_ESPTOOL_CHIP / $FAKE_ESPTOOL_MAC: what chip_id reports (default
ESP32-S3, 02:00:00:00:00:01).
"""

import os
import sys

argv = sys.argv[1:]
form = os.environ.get("FAKE_ESPTOOL_FORM", "v4")
mode = os.environ.get("FAKE_ESPTOOL_MODE", "ok")
log = os.environ["FAKE_ESPTOOL_LOG"]
board = os.environ["FAKE_BOARD"]
with open(log, "a") as f:
    f.write(" ".join(argv) + "\n")
wrote = os.path.exists(board + ".written")
chip = os.environ.get("FAKE_ESPTOOL_CHIP", "ESP32-S3")
mac = os.environ.get("FAKE_ESPTOOL_MAC", "02:00:00:00:00:01")

COMMANDS = ("chip_id", "write_flash")
if form not in ("v4", "v5"):
    print("fake esptool: unknown FAKE_ESPTOOL_FORM %r" % form)
    sys.exit(1)
cmd = None
for a in argv:
    if a in COMMANDS:
        cmd = a
        break
    if a.replace("-", "_") in COMMANDS and "-" in a:
        if form == "v4":
            sys.stderr.write(
                "usage: esptool [-h] ...\nesptool: error: argument operation: "
                "invalid choice: '%s' (choose from 'chip_id', 'write_flash', "
                "...)\n" % a)
            sys.exit(2)
        cmd = a.replace("-", "_")
        break
if cmd is None:
    print("fake esptool: unknown command %r" % argv)
    sys.exit(1)
if form == "v5" and cmd in argv:
    print("Warning: Deprecated: esptool.py is deprecated (made-up wording)")
    print("Warning: Deprecated: Command '%s' is deprecated. Use '%s' instead."
          % (cmd, cmd.replace("_", "-")))
if form == "v4":
    print("esptool.py v4.13.0")
    print("Serial port %s" % (argv[argv.index("--port") + 1]
                              if "--port" in argv else "?"))
    print("Connecting...")

if cmd == "chip_id":
    if mode == "chipid-fail" or (mode == "after-fail" and wrote):
        print("A fatal error occurred: Failed to connect")
        sys.exit(2)
    if form == "v4":
        print("Detecting chip type... %s" % chip)
        print("Chip is %s (QFN56) (revision v0.2)" % chip)
        print("Features: WiFi, BLE")
        print("Crystal is 40MHz")
        print("MAC: %s" % mac)
    else:
        print("Chip type:          %s (QFN56) (revision v0.2)" % chip)
        print("MAC:                %s" % mac)
    sys.exit(0)

if cmd == "write_flash":
    if mode == "write-fail":
        print("A fatal error occurred: write failed")
        sys.exit(2)
    i = [n for n, a in enumerate(argv)
         if a.replace("-", "_") == "write_flash"][0] + 1
    pairs = argv[i:]
    files = pairs[1::2]
    for p in files:
        print("Wrote %s" % p)
        print("Hash of data verified.")
    with open(files[-1], "rb") as src, open(board, "wb") as dst:
        dst.write(src.read())
    open(board + ".written", "w").close()
    sys.exit(0)
