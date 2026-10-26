#!/usr/bin/env python3
"""Test credential_key_scan.py on a synthetic tree with made-up values: the counts and
names it reports, and that NO value it matched ever appears in its output.

    py -3.14 -B gen-inhibit/tools/bench/test_credential_key_scan.py [--mutants]

Gen-inhibit tester, 2026-10-20.
"""
import gzip
import importlib.util
import io
import os
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []
SECRET = "Zq7-made-up-value-91"
SSIDV = "MadeUpNet_77"


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def load(path):
    spec = importlib.util.spec_from_file_location("cks_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def tree(root):
    os.makedirs(os.path.join(root, "a", "b"))
    w = lambda p, b: open(os.path.join(root, p), "wb").write(b)
    status = ('{"wifi_mode":"AP","ap_ssid":"%s","sta_ssid":"%s","sta_pass":"%s","ap_pass":"%s"}'
              % (SSIDV, SSIDV, SECRET, SECRET)).encode()
    w("a/status.json", status)
    w("a/b/log.txt", b"POST /store_config sta_pass=" + SECRET.encode() + b"&x=1\n")
    w("a/b/cap.pcapng", b"\x0a\x0d\x0d\x0aHTTP/1.1 200 OK\r\n\r\n" + status + b"\x00\x01")
    w("a/w.json.gz", gzip.compress(b'{"mqtt_pwd":"' + SECRET.encode() + b'"}'))
    w("a/figures.json", b'{"pass_500_per_s": 9, "verdict": "PASS"}')
    w("a/clean.txt", b"nothing here, a wifi_mode word without a key\n")
    w("a/dump.bin", b'{"sta_pass":"' + SECRET.encode() + b'"}')
    w("a/x.scn", b'{"ap_pass":"' + SECRET.encode() + b'"}')
    w("a/notes.md", b'"sta_pass": "' + SECRET.encode() + b'"')


def checks(M):
    with tempfile.TemporaryDirectory(prefix="gi-cks-", dir=gi_paths.scratch("tmp", run=False)) as root:
        tree(root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            M.main([root])
        out = buf.getvalue()
    lines = out.splitlines()
    check("no matched value appears in the output", SECRET not in out and SSIDV not in out, out)
    check("the status object's keys are named and counted",
          any(l.strip().startswith(os.path.join("a", "status.json")) and "key:sta_pass 1" in l and
              "key:ap_pass 1" in l and "shaped:wifi_mode+ssid 1" in l for l in lines), out)
    check("a form field in a log counts as bare:sta_pass",
          any(os.path.join("a", "b", "log.txt") in l and "bare:sta_pass 1" in l for l in lines), out)
    check("a capture is scanned as raw bytes", any("cap.pcapng" in l and "key:sta_pass 1" in l and
                                                   "shaped:wifi_mode+ssid 1" in l for l in lines), out)
    check("a .gz is decompressed and scanned", any("w.json.gz" in l and "key:mqtt_pwd 1" in l for l in lines), out)
    check("a benign *pass* key is reported by name (pass_500_per_s), for the reader to judge",
          any("figures.json" in l and "key:pass_500_per_s 1" in l for l in lines), out)
    check("a clean file is not listed", not any("clean.txt" in l for l in lines), out)
    check("flash dumps and .scn are not opened, and are counted", not any("dump.bin" in l or "x.scn" in l
                                                                          for l in lines) and
          ".bin 1" in out and ".scn 1" in out, out)
    check("an unlisted type (.md) is not opened, and is counted", ".md 1" in out and "notes.md" not in out, out)
    check("files with a match: 5", "files with a match: 5" in out, out)


MUTANTS = [
    ("the value is captured as the key", rb'"([A-Za-z0-9_\-]*(?:pass|pwd)[A-Za-z0-9_\-]*)"\s*:',
     rb'"([A-Za-z0-9_\-]*(?:pass|pwd)[A-Za-z0-9_\-]*)"\s*:\s*"([^"]*)"'),
    ("bare matches print what follows", 'out["bare:%s" % m.group(1).decode("ascii", "replace").lower()] += 1',
     'out["bare:%s" % b[m.start():m.end() + 30].decode("ascii", "replace")] += 1'),
    ("pcapng not scanned", 'RAW = {".pcapng"}', 'RAW = set()'),
    ("gz not decompressed", '            return gzip.decompress(b)', '            return b'),
    ("flash dumps are read", 'SKIP = {".bin", ".scn", ".tgz"}', 'SKIP = {".scn", ".tgz"}\nTEXT_BIN = {".bin"}'),
    ("the shaped check is dropped", '        if SSID_KEY_RE.search(b, m.end(), m.end() + 4096):', '        if False:'),
    ("pwd keys are missed", '(?:pass|pwd)', '(?:pass)'),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        checks(load(os.path.join(HERE, "credential_key_scan.py")))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    src = open(os.path.join(HERE, "credential_key_scan.py"), encoding="utf-8").read()
    killed = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        old = old.decode() if isinstance(old, bytes) else old
        new = new.decode() if isinstance(new, bytes) else new
        if name == "flash dumps are read":
            new = 'SKIP = {".scn", ".tgz"}'
            mut = src.replace(old, new).replace('TEXT = {".json"', 'TEXT = {".bin", ".json"')
        else:
            mut = src.replace(old, new)
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            continue
        if name == "the value is captured as the key":
            mut = mut.replace('out["key:%s" % m.group(1).decode("ascii", "replace").lower()] += 1',
                              'out["key:%s=%s" % (m.group(1).decode("ascii", "replace").lower(), m.group(2).decode())] += 1')
        with tempfile.TemporaryDirectory(prefix="gi-cks-m%d-" % i, dir=gi_paths.scratch("tmp", run=False)) as tmp:
            mp = os.path.join(tmp, "credential_key_scan.py")
            open(mp, "w", encoding="utf-8").write(mut)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--module", mp],
                               capture_output=True, text=True, timeout=120)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
        crashed = r.returncode not in (0, 1) or "Traceback" in r.stderr
        if fails and not crashed:
            killed += 1
            print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(fails), fails[0][6:70]))
        else:
            print("%s m%d %s  (rc %d)" % ("CRASHED " if crashed else "SURVIVED", i, name, r.returncode))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--module"]:
        checks(load(sys.argv[2]))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
