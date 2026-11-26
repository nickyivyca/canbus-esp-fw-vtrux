#!/usr/bin/env python3
"""This folder is published (the firmware repo is public): nothing private in it.

Gen-inhibit tester, 2026-10-08; widened 2026-10-22 when the bench harness moved in
from SeaDrive (goal 20): the scan now covers bench/ and x2_jammer/ as well, every
text kind the harness has (.sh, .ps1, .c, .cpp, .h too), and a MAC written bare.

  1. Every text file in this folder, and everything under bench/ and x2_jammer/, is
     scanned (bytes, like grep -a) for: MAC addresses, written with colons or dashes
     or bare on either bench board's vendor prefix (as in a dump file's name); the
     WiCAN SoftAP SSID form (it embeds the MAC); private IPv4 addresses; and absolute
     paths into a user's home. Allowed: 192.168.80.1 and .2, the WiCAN's own AP and
     its client (user, 2026-10-21), and the RFC 7042 documentation MACs
     00:00:5e:00:53:xx that the tests use as made-up values.
     Hostnames are NOT matched here, because naming them in a published file
     to search for them would publish them; check those by hand against the
     machine table before a push.
  2. Positive control: each pattern must match a planted sample, so a scan
     that finds nothing is a scan that could have found something.
  3. The OTA has no committed SSID: bench/gi_ota.py (the pinned core's flasher; it
     replaced bench_flash.py at port step 4 item 2, 2026-11-25), with the variable
     settings.ota.ssid_env names unset, or with no GI_OTA_SETTINGS, must refuse (exit 2)
     before touching the Wi-Fi; and bench_flash.py is gone.
  4. The guards' runs folder outside reverse-it.
  5. The scan's reach: in a temporary tree laid out like this folder, a bare MAC in a
     bench/ .py, a home path in a bench/ .sh and in a .ps1, a MAC in x2_jammer/src/'s
     .cpp and an SSID in a top-level .md must each be found, and a documentation MAC
     must not.

    py -3.14 test_publish_scan.py      # exit 0 only if every check passes
Its mutants: test_publish_scan_mutants.py (PUBLISH_SCAN_ROOT scans another tree).
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _bench_gi_paths():
    """bench/gi_paths (scratch folders and their removal, AGENTS.md "Removing scratch"),
    from the worktree that holds this file, found by walking up to its .git: a mutant's
    copy of this file in scratch/ has no bench/ beside it."""
    if "gi_paths" in sys.modules:
        return sys.modules["gi_paths"]
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            raise RuntimeError("%s is in no git worktree" % __file__)
        d = os.path.dirname(d)
    sys.path.append(os.path.join(d, "gen-inhibit", "tools", "bench"))
    import gi_paths
    return gi_paths


gi_paths = _bench_gi_paths()


def scratch_dir(name):
    """This run's own folder in the worktree's gitignored scratch/ (AGENTS.md "Files and
    folders"), never the system temp folder: bench/gi_paths.scratch()'s, removed with its
    owner file when the run ends, pass or fail (AGENTS.md "Removing scratch")."""
    return gi_paths.scratch(name, start=__file__)
TEXT_EXT = (".py", ".md", ".txt", ".json", ".ini", ".cfg", ".toml", ".csv", ".sh", ".ps1", ".c", ".cpp", ".h")
SUBDIRS = ("bench", "x2_jammer")          # scanned recursively; this folder's own files are not

PATTERNS = {
    "MAC address": rb"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b",
    "bare MAC (a bench board's vendor prefix)": rb"(?i)(?<![0-9a-f])(?:d4f98d|3c0f02)[0-9a-f]{6}(?![0-9a-f])",
    "WiCAN SSID with MAC": rb"(?i)wican_[0-9a-f]{6,}",
    "private IPv4": rb"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
    "home path": rb"(?i)(?:[a-z]:[\\/]+users[\\/]+[^\\/\s\"']+|/home/[^/\s\"']+|/Users/[^/\s\"']+)",
}
ALLOWED = [rb"\b192\.168\.80\.[12]\b", rb"(?i)00[:-]00[:-]5e[:-]00[:-]53[:-][0-9a-f]{2}"]
PLANTED = {
    "MAC address": b"x = 'a0:b1:c2:d3:e4:f5'",
    "bare MAC (a bench board's vendor prefix)": b"f = 'canx2_s3_3c0f02a1b2c3_full_x.bin'",
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


def files(root):
    """The files the scan reads under root: its own text files, and every text file
    under SUBDIRS (recursively, __pycache__ left out)."""
    out = [os.path.join(root, f) for f in sorted(os.listdir(root))
           if os.path.isfile(os.path.join(root, f)) and f.endswith(TEXT_EXT)]
    for sub in SUBDIRS:
        for dp, dns, fns in os.walk(os.path.join(root, sub)):
            dns[:] = sorted(d for d in dns if d != "__pycache__")
            out += [os.path.join(dp, f) for f in sorted(fns) if f.endswith(TEXT_EXT)]
    return out


def scan(root, own=None):
    """[(relative path, line, pattern name)] for every hit under root. own is the path
    of this script: below PATTERNS it is all patterns and planted samples, which would
    match themselves, so only its docstring and imports are scanned."""
    found = []
    for p in files(root):
        with open(p, "rb") as fh:
            data = fh.read()
        if own and os.path.basename(p) == os.path.basename(own):
            data = data.split(b"PATTERNS = {")[0]
        for name, line in hits(data):
            found.append((os.path.relpath(p, root).replace("\\", "/"), line, name))
    return found


def report(root, own=None, quiet=False):
    """Scan root, print one FAIL line per hit (never the match: it may be the private
    value) unless quiet, and return the number of hits, which is what fails the run."""
    bad = 0
    for rel, line, name in scan(root, own):
        bad += 1
        if not quiet:
            print("  [FAIL] %s:%d %s" % (rel, line, name))
    return bad


def reach_check():
    """Section 5: a planted tree, each plant in a place or kind the scan must reach."""
    d = scratch_dir("publish_scan_reach")
    gi_paths.remove_tree(d)
    plants = {
        "bench/x2_tool.py": (b"DUMP = 'canx2_s3_3c0f02a1b2c3_full_%s.bin'\n", "bare MAC (a bench board's vendor prefix)"),
        "bench/run.sh": (b'FW="/home/someone/Git/fw"\n', "home path"),
        "bench/build.ps1": (b'$env:IDF_PATH = "C:\\Users\\someone\\esp"\n', "home path"),
        "x2_jammer/src/main.cpp": (b"// board a0:b1:c2:d3:e4:f5\n", "MAC address"),
        "notes.md": (b"SoftAP WiCAN_a0b1c2d3e4f5\n", "WiCAN SSID with MAC"),
    }
    for rel, (b, _) in plants.items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "wb") as fh:
            fh.write(b)
    with open(os.path.join(d, "bench", "test_x.py"), "wb") as fh:
        fh.write(b"FAKE = '00:00:5e:00:53:01'  # a documentation MAC\n")
    found = scan(d)
    rows = []
    for rel, (_, name) in plants.items():
        rows.append(("%s in %s is found" % (name, rel), any(r == rel and n == name for r, _, n in found)))
    rows.append(("a documentation MAC is not", not any(r == "bench/test_x.py" for r, _, _ in found)))
    rows.append(("the report counts each planted hit, so the run fails on them", report(d, quiet=True) == len(plants)))
    gi_paths.remove_tree(d)
    return rows


def main():
    bad = 0
    print("2. positive control")
    for name, sample in PLANTED.items():
        ok = any(n == name for n, _ in hits(sample))
        bad += not ok
        print("  [%s] planted %s is found" % ("PASS" if ok else "FAIL", name))
    for host in (b"192.168.80.1", b"192.168.80.2"):
        ok = not hits(b"host = '" + host + b"'")
        bad += not ok
        print("  [%s] the WiCAN AP subnet's %s is allowed" % ("PASS" if ok else "FAIL", host.decode()))
    ok = bool(hits(b"host = '192.168.80.10'"))
    bad += not ok
    print("  [%s] another address on that subnet is not" % ("PASS" if ok else "FAIL"))

    root = os.environ.get("PUBLISH_SCAN_ROOT") or HERE
    print("1. scan of %s and its %s" % (os.path.basename(root), ", ".join(s + "/" for s in SUBDIRS)))
    n = len(files(root))
    bad += report(root, own=__file__)
    ok = n > 0
    bad += not ok
    print("  [%s] %d files scanned" % ("PASS" if ok else "FAIL", n))

    print("5. the scan's reach")
    for label, ok in reach_check():
        bad += not ok
        print("  [%s] %s" % ("PASS" if ok else "FAIL", label))

    # gi_ota.py, the harness's OTA through the pinned core's flasher, since bench_flash.py
    # retired (port step 4 item 2, 2026-11-25). The rule is the same: no SSID is committed,
    # so with the variable settings.ota.ssid_env names unset, and with no GI_OTA_SETTINGS at
    # all, it refuses (exit 2) before any Wi-Fi step or upload. From the scanned tree's bench/.
    print("3. the OTA (gi_ota.py) without an SSID")
    gi_ota = os.path.join(root, "bench", "gi_ota.py")
    ota_block = ('{"host": "192.168.80.1", "iface": "wlan0", "profile": "made-up", '
                 '"ssid_env": "GI_WICAN_SSID", "wifi": "system"}')
    no_ssid = {k: v for k, v in os.environ.items() if k not in ("GI_WICAN_SSID", "GI_OTA_SETTINGS")}
    for label, env, says in (("ssid_env's variable unset", dict(no_ssid, GI_OTA_SETTINGS=ota_block),
                              "GI_WICAN_SSID, named by settings.ota.ssid_env, is unset"),
                             ("no GI_OTA_SETTINGS", no_ssid, "GI_OTA_SETTINGS is not set")):
        try:
            r = subprocess.run([sys.executable, "-B", gi_ota, "--bin", os.path.join(root, "no-such.bin"),
                                "--record", os.path.join(root, "no-such-record.json")],
                               capture_output=True, text=True, env=env, timeout=60)
            rc, out = r.returncode, r.stdout + r.stderr
        except (OSError, subprocess.SubprocessError) as e:
            rc, out = None, "%s: %s" % (type(e).__name__, e)
        ok = (rc == 2 and "REFUSED" in out and says in out and "nothing sent" in out
              and "posting" not in out and "joined" not in out
              and not os.path.exists(os.path.join(root, "no-such-record.json")))
        bad += not ok
        print("  [%s] %s: refused before the Wi-Fi (rc=%s): %s" % (
            "PASS" if ok else "FAIL", label, rc, (out.strip().splitlines() or ["(no output)"])[-1]))
    ok = not os.path.exists(os.path.join(root, "bench_flash.py"))
    bad += not ok
    print("  [%s] bench_flash.py is gone from the scanned folder" % ("PASS" if ok else "FAIL"))

    # bench_ota_guard's runs_dir is the pinned core's wican_http.runs_dir since port step 4
    # item 2, and its legacy folder that module's _LEGACY_RUNS
    print("4. the guards' runs folder outside reverse-it")
    sys.path.insert(0, HERE)
    import bench_ota_guard as g
    w = getattr(g, "_WH", g)
    saved_env, saved_legacy = os.environ.pop(g.RUNS_ENV, None), w._LEGACY_RUNS
    w._LEGACY_RUNS = os.path.join(HERE, "no-such-runs-folder")
    try:
        g.runs_dir()
        ok, why = False, "returned a folder"
    except SystemExit as e:
        ok, why = g.RUNS_ENV in str(e), str(e)
    finally:
        w._LEGACY_RUNS = saved_legacy
        if saved_env is not None:
            os.environ[g.RUNS_ENV] = saved_env
    bad += not ok
    print("  [%s] with no %s and no legacy folder it refuses: %s"
          % ("PASS" if ok else "FAIL", g.RUNS_ENV, why))

    print("\n=== %s ===" % ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
