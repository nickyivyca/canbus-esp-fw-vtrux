#!/usr/bin/env python3
"""Scan a tree for /check_status config keys that reached a file (reviewer, 2026-10-20).

Prints FILE NAMES, KEY NAMES and COUNTS only. It never prints, stores or returns a
value: each match is reduced to its key name before it leaves the scanning function,
and the bytes after the key are not read into any output.

Patterns (bytes, case-insensitive):
  key      a JSON-style key "<name>": whose name holds "pass" or "pwd"
  bare     sta_pass / ap_pass anywhere (a form, a query string, a C name)
  shaped   a "wifi_mode" key with an "*ssid*" key within 4 KB after it: a
           check_status-shaped object (the SSID values are not read)

Scanned: text-like files (.json .jsonl .txt .log .csv .stamped .trace .sha256 .py),
.gz (decompressed in memory) and .pcapng (raw bytes: the OTA Wi-Fi captures, where an
HTTP answer would sit in the clear). Not opened: .bin (flash dumps; SENSITIVE, never
read), .scn and .tgz (generated CAN stimulus and host traces), listed by count.

    py -3.14 -B tools/gen_inhibit/bench/credential_key_scan.py <root> [...]

Gen-inhibit tester. Read-only.
"""
import collections
import gzip
import os
import re
import sys

TEXT = {".json", ".jsonl", ".txt", ".log", ".csv", ".stamped", ".trace", ".sha256", ".py"}
RAW = {".pcapng"}
GZ = {".gz"}
SKIP = {".bin", ".scn", ".tgz"}
KEY_RE = re.compile(rb'"([A-Za-z0-9_\-]*(?:pass|pwd)[A-Za-z0-9_\-]*)"\s*:', re.I)
BARE_RE = re.compile(rb"\b(sta_pass|ap_pass)\b", re.I)
WM_RE = re.compile(rb'"wifi_mode"\s*:', re.I)
SSID_KEY_RE = re.compile(rb'"[A-Za-z0-9_]*ssid[A-Za-z0-9_]*"\s*:', re.I)


def scan_bytes(b):
    """{label: count}; labels are key names or pattern names, never values."""
    out = collections.Counter()
    for m in KEY_RE.finditer(b):
        out["key:%s" % m.group(1).decode("ascii", "replace").lower()] += 1
    for m in BARE_RE.finditer(b):
        out["bare:%s" % m.group(1).decode("ascii", "replace").lower()] += 1
    for m in WM_RE.finditer(b):
        if SSID_KEY_RE.search(b, m.end(), m.end() + 4096):
            out["shaped:wifi_mode+ssid"] += 1
    return out


def read(path, ext):
    with open(path, "rb") as fh:
        b = fh.read()
    if ext in GZ:
        try:
            return gzip.decompress(b)
        except (OSError, EOFError):
            return b
    return b


def main(roots):
    hits, skipped, scanned, errors = [], collections.Counter(), 0, []
    for root in roots:
        for dp, _dn, fns in os.walk(root):
            for f in sorted(fns):
                p = os.path.join(dp, f)
                ext = os.path.splitext(f)[1].lower()
                if ext in SKIP or ext not in TEXT | RAW | GZ:
                    skipped[ext or "(none)"] += 1
                    continue
                try:
                    c = scan_bytes(read(p, ext))
                except OSError as e:
                    errors.append((p, type(e).__name__))
                    continue
                scanned += 1
                if c:
                    hits.append((os.path.relpath(p, root), c))
    total = collections.Counter()
    for _p, c in hits:
        total.update(c)
    print("scanned %d files; not opened: %s; read errors: %d" % (
        scanned, ", ".join("%s %d" % kv for kv in sorted(skipped.items())), len(errors)))
    for p, e in errors:
        print("  read error: %s (%s)" % (p, e))
    print("files with a match: %d" % len(hits))
    print("by label (all files): %s" % (", ".join("%s %d" % kv for kv in sorted(total.items())) or "none"))
    for p, c in sorted(hits):
        print("  %s: %s" % (p, ", ".join("%s %d" % kv for kv in sorted(c.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
