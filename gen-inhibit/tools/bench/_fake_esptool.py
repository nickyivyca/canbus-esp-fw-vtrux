"""A stand-in esptool package, for the harness tests of gi_esptool, x2_hold and
x2_flash_custody. No hardware, no port. Gen-inhibit tester, 2026-11-11.

make(root) writes root/esptool/ (a package, run as `<python> -m esptool` with root on
PYTHONPATH) and returns the environment to run it with. It stands in for esptool 4.13.0
as the ESP-IDF v5.4.2 python env has it: only the underscore command names (a dashed
one is refused as argparse refuses it, on stderr, exit 2), `version` printing a banner
then the bare version, and the Python API x2_hold_inputs.py uses (esptool.__version__,
esptool.cmds.detect_chip). chip_id prints the RFC 7042 documentation MAC
00:00:5e:00:53:01, the only kind of made-up MAC test_publish_scan allows (2026-11-12;
it was a locally administered one, which the scan flags). Set by environment variable,
per call:

  FAKE_ESPTOOL_VERSION  the version it reports (default 4.13.0)
  FAKE_ESPTOOL_LOG      a file each call appends one line to: "cli <argv>" or "api <call>"
  FAKE_ESPTOOL_REGS     JSON {"0x60004020": value, ...}: what read_mem and read_reg read
                        (0 for an address not named)
  FAKE_ESPTOOL_FLASH    a file read_flash copies to its output path
  FAKE_ESPTOOL_FAIL     a command name that fails (exit 2, "A fatal error occurred")
  FAKE_ESPTOOL_CHIP_FLASH  a whole-flash image (2026-11-13, x2_flash_custody verify-row):
                        when set, read_flash <off> <size> <out> writes that slice of it
                        (ALL: the whole), and verify_flash compares each file with it at
                        its offset, printing 4.13.0's lines for each, "Verifying 0x<n> (<n>)
                        bytes @ 0x<off, 8 digits> in flash against <file>..." then "-- verify
                        OK (digest matched)" or "-- verify FAILED (digest mismatch)", and
                        "A fatal error occurred: Verify failed." with exit 2 if any differs.
                        Unset, verify_flash prints OK for every file, as before
  FAKE_ESPTOOL_RC       "<command>=<exit>": that command exits so after its output
`run` prints esptool's reset line and exits 0.
"""
import os

MAIN = r'''
import json, os, shutil, sys
import esptool

COMMANDS = ("version", "chip_id", "read_mem", "read_flash", "write_flash", "verify_flash", "run")
argv = sys.argv[1:]
log = os.environ.get("FAKE_ESPTOOL_LOG")
if log:
    with open(log, "a") as f:
        f.write("cli " + " ".join(argv) + "\n")
cmd, k = None, None
for i, a in enumerate(argv):
    if a in COMMANDS:
        cmd, k = a, i
        break
    if "-" in a and not a.startswith("-") and a.replace("-", "_") in COMMANDS:
        sys.stderr.write("esptool.py: error: argument operation: invalid choice: '%s'\n" % a)
        sys.exit(2)
if cmd is None:
    sys.stderr.write("esptool.py: error: no operation in %r\n" % argv)
    sys.exit(2)
print("esptool.py v%s" % esptool.__version__)
if cmd == "version":
    print(esptool.__version__)
    sys.exit(0)
if os.environ.get("FAKE_ESPTOOL_FAIL") == cmd:
    print("A fatal error occurred: %s failed" % cmd)
    sys.exit(2)
rest = argv[k + 1:]
regs = {int(a, 16): v for a, v in json.loads(os.environ.get("FAKE_ESPTOOL_REGS") or "{}").items()}
chip = open(os.environ["FAKE_ESPTOOL_CHIP_FLASH"], "rb").read() if os.environ.get("FAKE_ESPTOOL_CHIP_FLASH") else None
if cmd == "chip_id":
    print("Chip is ESP32-S3 (QFN56) (revision v0.2)")
    print("MAC: 00:00:5e:00:53:01")
elif cmd == "read_mem":
    a = int(rest[0], 16)
    print("0x%08x = 0x%08x" % (a, regs.get(a, 0)))
elif cmd == "read_flash" and chip is not None:
    off = int(rest[0], 0)
    data = chip[off:] if rest[1] == "ALL" else chip[off:off + int(rest[1], 0)]
    with open(rest[2], "wb") as f:
        f.write(data)
    print("Read %d bytes" % len(data))
elif cmd == "read_flash":
    shutil.copyfile(os.environ["FAKE_ESPTOOL_FLASH"], rest[2])
    print("Read %d bytes" % os.path.getsize(rest[2]))
elif cmd == "write_flash":
    for p in rest[1::2]:
        print("Wrote %s" % p)
        print("Hash of data verified.")
elif cmd == "verify_flash" and chip is not None:
    differ = False
    for off, p in zip(rest[0::2], rest[1::2]):
        data = open(p, "rb").read()
        off = int(off, 0)
        print("Verifying 0x%x (%d) bytes @ 0x%08x in flash against %s..." % (len(data), len(data), off, p))
        same = chip[off:off + len(data)] == data
        differ = differ or not same
        print("-- verify OK (digest matched)" if same else "-- verify FAILED (digest mismatch)")
    if differ:
        print("")
        print("A fatal error occurred: Verify failed.")
        sys.exit(2)
elif cmd == "verify_flash":
    for p in rest[1::2]:
        print("Verifying %s" % p)
        print("-- verify OK (digest matched)")
elif cmd == "run":
    print("Hard resetting via RTS pin...")
for spec in (os.environ.get("FAKE_ESPTOOL_RC") or "").split(","):
    if spec.partition("=")[0] == cmd:
        sys.exit(int(spec.partition("=")[2]))
'''

INIT = r'''
import os
__version__ = os.environ.get("FAKE_ESPTOOL_VERSION", "4.13.0")
'''

CMDS = r'''
import json, os


def _log(s):
    log = os.environ.get("FAKE_ESPTOOL_LOG")
    if log:
        with open(log, "a") as f:
            f.write("api " + s + "\n")


class _Port:
    def close(self):
        _log("close")


class _Esp:
    def __init__(self):
        self.regs = {int(a, 16): v for a, v in
                     json.loads(os.environ.get("FAKE_ESPTOOL_REGS") or "{}").items()}
        self._port = _Port()

    def run_stub(self):
        _log("run_stub")
        return self

    def read_reg(self, addr):
        return self.regs.get(addr, 0)

    def write_reg(self, addr, value, mask=0xFFFFFFFF):
        _log("write_reg 0x%08x 0x%x 0x%x" % (addr, value, mask))
        self.regs[addr] = (self.regs.get(addr, 0) & ~mask) | (value & mask)


def detect_chip(port, baud, before):
    import sys
    _log("detect_chip %s %s %s python=%s" % (port, baud, before, sys.executable))
    return _Esp()
'''


def make(root):
    """Writes root/esptool/ and returns {"PYTHONPATH": root, then this process's
    PYTHONPATH}, to add to a run's environment (with any FAKE_ESPTOOL_* settings). root
    comes first, so this esptool is the one found; what follows keeps the pinned core's
    shared/bench importable for gi_serial (2026-11-12)."""
    pkg = os.path.join(root, "esptool")
    os.makedirs(pkg, exist_ok=True)
    for name, text in (("__init__.py", INIT), ("__main__.py", MAIN), ("cmds.py", CMDS)):
        with open(os.path.join(pkg, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text.lstrip("\n"))
    rest = os.environ.get("PYTHONPATH")
    return {"PYTHONPATH": root + (os.pathsep + rest if rest else "")}
