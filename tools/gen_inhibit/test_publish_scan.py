#!/usr/bin/env python3
"""This folder is published (the firmware repo is public): nothing private in it.

Gen-inhibit tester, 2026-10-08.

  1. Every text file in this folder is scanned (bytes, like grep -a) for:
     MAC addresses, the WiCAN SoftAP SSID form (it embeds the MAC), private
     IPv4 addresses, and absolute paths into a user's home. 192.168.80.x is
     allowed: it is the WiCAN's own AP subnet, hard-coded in the published
     firmware (main/wifi_network.c).
     Hostnames are NOT matched here, because naming them in a published file
     to search for them would publish them; check those by hand against the
     machine table before a push.
  2. Positive control: each pattern must match a planted sample, so a scan
     that finds nothing is a scan that could have found something.
  3. bench_flash.py has no committed SSID, so with neither --ssid nor
     $GI_WICAN_SSID it must refuse before touching the WiFi.

    py -3.14 test_publish_scan.py      # exit 0 only if every check passes
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_EXT = (".py", ".md", ".txt", ".json", ".ini", ".cfg", ".toml", ".csv")

PATTERNS = {
    "MAC address": rb"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b",
    "WiCAN SSID with MAC": rb"(?i)wican_[0-9a-f]{6,}",
    "private IPv4": rb"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
    "home path": rb"(?i)(?:[a-z]:[\\/]+users[\\/]+[^\\/\s\"']+|/home/[^/\s\"']+|/Users/[^/\s\"']+)",
}
ALLOWED = [rb"\b192\.168\.80\.\d{1,3}\b"]
PLANTED = {
    "MAC address": b"x = 'a0:b1:c2:d3:e4:f5'",
    "WiCAN SSID with MAC": b"ssid = 'WiCAN_a0b1c2d3e4f5'",
    "private IPv4": b"host = '10.1.2.3'",
    "home path": b"p = r'C:\\Users\\someone\\x'",
}


def hits(data):
    out = []
    for name, pat in PATTERNS.items():
        for m in re.finditer(pat, data):
            s = m.group(0)
            if any(re.fullmatch(a, s) for a in ALLOWED):
                continue
            line = data.count(b"\n", 0, m.start()) + 1
            out.append((name, line))
    return out


def main():
    bad = 0
    print("2. positive control")
    for name, sample in PLANTED.items():
        ok = any(n == name for n, _ in hits(sample))
        bad += not ok
        print("  [%s] planted %s is found" % ("PASS" if ok else "FAIL", name))
    ok = not hits(b"host = '192.168.80.1'")
    bad += not ok
    print("  [%s] the WiCAN AP address is allowed" % ("PASS" if ok else "FAIL"))

    print("1. scan of %s" % os.path.basename(HERE))
    n = 0
    for f in sorted(os.listdir(HERE)):
        p = os.path.join(HERE, f)
        if not (os.path.isfile(p) and f.endswith(TEXT_EXT)):
            continue
        n += 1
        with open(p, "rb") as fh:
            data = fh.read()
        if f == os.path.basename(__file__):
            # The patterns and the planted samples would match themselves.
            data = data.split(b"PATTERNS = {")[0] + data.split(b"def hits(")[1]
        for name, line in hits(data):
            bad += 1
            # The match itself is not printed: it may be the private value.
            print("  [FAIL] %s:%d %s" % (f, line, name))
    ok = n > 0
    bad += not ok
    print("  [%s] %d files scanned" % ("PASS" if ok else "FAIL", n))

    print("3. bench_flash.py without an SSID")
    env = {k: v for k, v in os.environ.items() if k != "GI_WICAN_SSID"}
    r = subprocess.run([sys.executable, os.path.join(HERE, "bench_flash.py"), "--status"],
                       capture_output=True, text=True, env=env, timeout=60)
    ok = (r.returncode == 2 and "GI_WICAN_SSID" in r.stderr
          and "preflight" not in r.stdout)
    bad += not ok
    print("  [%s] refuses before the WiFi (rc=%d): %s" % (
        "PASS" if ok else "FAIL", r.returncode,
        (r.stderr.strip().splitlines() or ["(no stderr)"])[-1]))

    print("4. the guards' runs folder outside reverse-it")
    sys.path.insert(0, HERE)
    import bench_ota_guard as g
    saved_env, saved_legacy = os.environ.pop(g.RUNS_ENV, None), g._LEGACY_RUNS
    g._LEGACY_RUNS = os.path.join(HERE, "no-such-runs-folder")
    try:
        g.runs_dir()
        ok, why = False, "returned a folder"
    except SystemExit as e:
        ok, why = g.RUNS_ENV in str(e), str(e)
    finally:
        g._LEGACY_RUNS = saved_legacy
        if saved_env is not None:
            os.environ[g.RUNS_ENV] = saved_env
    bad += not ok
    print("  [%s] with no %s and no legacy folder it refuses: %s"
          % ("PASS" if ok else "FAIL", g.RUNS_ENV, why))

    print("\n=== %s ===" % ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
