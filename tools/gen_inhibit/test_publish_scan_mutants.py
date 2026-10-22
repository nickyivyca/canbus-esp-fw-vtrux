#!/usr/bin/env python3
"""Mutants of test_publish_scan.py's 2026-10-22 widening; each must make it report a
FAIL by a check (a traceback is not a kill), and an edit that does not apply is an
error. Gen-inhibit tester, 2026-10-22.

Each mutant is a copy of test_publish_scan.py with one edit, in a folder of the
machine's temp folder that also holds this folder's top-level .py files (the scan runs
bench_flash.py and imports bench_ota_guard from beside itself), run with
PUBLISH_SCAN_ROOT naming this folder, so it scans the real tree. The control, the
unmutated copy run the same way, must pass.

    py -3.14 -B test_publish_scan_mutants.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "test_publish_scan.py"
MUTANTS = [
    (".sh not scanned", '".sh", ".ps1", ".c"', '".ps1", ".c"'),
    (".ps1 not scanned", '".sh", ".ps1", ".c"', '".sh", ".c"'),
    ("bench/ not scanned", 'SUBDIRS = ("bench", "x2_jammer")', 'SUBDIRS = ("x2_jammer",)'),
    ("x2_jammer/ not scanned", 'SUBDIRS = ("bench", "x2_jammer")', 'SUBDIRS = ("bench",)'),
    ("subfolders not recursed", "            dns[:] = sorted(d for d in dns if d != \"__pycache__\")\n",
     "            dns[:] = []\n"),
    ("the bare MAC pattern dropped",
     '    "bare MAC (a bench board\'s vendor prefix)": rb"(?i)(?<![0-9a-f])(?:d4f98d|3c0f02)[0-9a-f]{6}(?![0-9a-f])",\n', ""),
    ("the WiCAN subnet allowed whole", 'ALLOWED = [rb"\\b192\\.168\\.80\\.[12]\\b"', 'ALLOWED = [rb"\\b192\\.168\\.80\\.\\d{1,3}\\b"'),
    ("every MAC allowed as documentation", 'rb"(?i)00[:-]00[:-]5e[:-]00[:-]53[:-][0-9a-f]{2}"]',
     'rb"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}"]'),
    ("hits in the tree not counted", "        bad += 1\n        if not quiet:", "        if not quiet:"),
]


def run(d):
    r = subprocess.run([sys.executable, "-B", os.path.join(d, TARGET)], capture_output=True, text=True,
                       env=dict(os.environ, PUBLISH_SCAN_ROOT=HERE), cwd=d, timeout=300)
    fails = [l.strip() for l in r.stdout.splitlines() if "[FAIL]" in l]
    return fails, "Traceback" in r.stderr, r.returncode


def copy(base, name, text):
    d = os.path.join(base, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f in os.listdir(HERE):
        if f.endswith(".py") and os.path.isfile(os.path.join(HERE, f)):
            shutil.copy(os.path.join(HERE, f), d)
    open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
    return d


def main():
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "publish_scan")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    fails, crashed, rc = run(copy(base, "control", src))
    control_ok = rc == 0 and not fails and not crashed
    print("%s control: the unmutated scan  (exit %d, %d FAIL)" % ("PASS    " if control_ok else "FAILED  ", rc, len(fails)))
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        fails, crashed, rc = run(copy(base, "m%d" % n, src.replace(old, new)))
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
                                                 ", CRASHED" if crashed else "",
                                                 "".join("\n           " + f[:120] for f in fails[:2])))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
