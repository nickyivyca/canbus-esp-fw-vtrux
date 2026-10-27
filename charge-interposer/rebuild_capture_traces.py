"""Rebuild the capture-derived differential traces and check them against
their pins (spec 9.1; tester, 2026-10-16, coverage gap 6).

`above83` and `charging45` had no recorded source window, so nothing showed
they could be rebuilt from their captures (evidence 9.1). Found 2026-10-16 by
frame content (each trace's first frames searched for across
projects/vtrux/logs and CANLogs/VtruxLogs on madhouse-debian, with a control
pattern that must match): each is the WHOLE capture, on the powertrain bus
the anchor discriminator names, rebased to the first frame by replay.iter_pt.

    trace       capture (under projects/vtrux/logs/)                 bus  window
    above83     unidentified-bus/vtrux_partstruck_chargeagain3.log   2    whole
    charging45  chargingafterturningaroundandnotusingextension.log   3    whole

`evap80` is not rebuilt here: its recipe (a 1.8 GB capture through
charge_cmd_filter.sh, window 7000-7600 s, bus 3) is in the repo's
firmware/README.md.

Each trace is rebuilt with make_golden.py, the bus passed explicitly, into
the machine temp folder (regenerable output stays out of SeaDrive), and its
sha256 compared with capture_pins.TRACE_PINS -- the pin, never the fixture
file. --can-fail rebuilds each with the window ending 1 s early and with the
wrong bus, and requires every one of those to MISS its pin.

Run from anywhere, with REVERSE_IT_DIR set (the captures); the code is
the tree this file is in (moved into git from reverse-it's
interposer-firmware/ folder, port step 0, 2026-10-26):
  py -3.14 -B charge-interposer/rebuild_capture_traces.py [--can-fail]
"""

import hashlib
import os
import subprocess
import sys
import tempfile

# this file is in charge-interposer/ itself; paths.code_dir() refuses an
# INTERPOSER_CODE naming another tree (port step 0, 2026-10-26)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                       # noqa: E402
CODE = paths.code_dir()
import capture_pins as CP           # noqa: E402

MAKE_GOLDEN = os.path.join(CODE, "firmware", "test", "host_diff",
                           "make_golden.py")
LOGS = paths.logs()                 # was relative to the reverse-it root
OUT = os.path.join(tempfile.gettempdir(), "interposer-tester",
                   "rebuild_traces")

# name -> (capture under logs/, bus, end_s or None for the whole capture,
#          the last frame's time in the pinned trace, ms)
RECIPES = {
    "above83": (os.path.join("unidentified-bus",
                             "vtrux_partstruck_chargeagain3.log"),
                2, None, 62221),
    "charging45": ("chargingafterturningaroundandnotusingextension.log",
                   3, None, 467155),
}
WRONG_BUS = {"above83": 3, "charging45": 2}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rebuild(name, capture, bus, end_s, tag):
    base = "%s_%s" % (name, tag)
    cmd = [sys.executable, "-B", MAKE_GOLDEN, os.path.join(LOGS, capture),
           "--name", base, "--bus", str(bus), "--outdir", OUT]
    if end_s is not None:
        cmd += ["--end", "%.3f" % end_s]
    r = subprocess.run(cmd, capture_output=True, text=True)
    path = os.path.join(OUT, base + ".trace")
    if r.returncode != 0 or not os.path.isfile(path):
        print("    make_golden rc=%d: %s" % (r.returncode,
                                             (r.stderr or r.stdout)[-400:]))
        return None, 0
    with open(path, "rb") as f:
        lines = sum(1 for _ in f)
    return sha256(path), lines


def main():
    can_fail = "--can-fail" in sys.argv[1:]
    os.makedirs(OUT, exist_ok=True)
    bad = 0
    for name, (capture, bus, end_s, last_ms) in RECIPES.items():
        pin = CP.TRACE_PINS[name + ".trace"]
        print("%s: %s, bus %d, %s; pin %s" % (
            name, capture, bus, "whole capture" if end_s is None
            else "to %.3f s" % end_s, pin[:16]))
        if not can_fail:
            got, n = rebuild(name, capture, bus, end_s, "rebuild")
            ok = got == pin
            print("  %s  rebuilt %s (%d lines) %s the pin" % (
                "ok  " if ok else "FAIL", (got or "nothing")[:16], n,
                "MATCHES" if ok else "does NOT match"))
            bad += 0 if ok else 1
            continue
        for tag, b, e in (("short1s", bus, last_ms / 1000.0 - 1.0),
                          ("wrongbus", WRONG_BUS[name], end_s)):
            got, n = rebuild(name, capture, b, e, tag)
            missed = got != pin
            print("  %s  %s: rebuilt %s (%d lines) %s the pin" % (
                "ok  " if missed else "FAIL", tag, (got or "nothing")[:16], n,
                "misses" if missed else "MATCHES"))
            bad += 0 if missed else 1
    if bad:
        print("\n%d FAILED" % bad)
        return 1
    print("\nrebuild_capture_traces%s: all OK" % (
        " --can-fail" if can_fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
