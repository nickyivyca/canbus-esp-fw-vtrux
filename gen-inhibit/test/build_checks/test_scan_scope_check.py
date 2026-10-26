#!/usr/bin/env python3
"""Tests scan_scope_check.py's judgements (no configure, no ESP-IDF).

    python3 test_scan_scope_check.py

The real-configure half is the run itself: the planted option must fail the
scan, and the same name set first must pass it.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan_scope_check as S                                       # noqa: E402
import build_switches as BS                                        # noqa: E402

REC = ["CMakeLists.txt", S.PLANTED, "main/CMakeLists.txt"]
ON = "-- " + S.SAYS_ON + "\n"
HIT = {S.NAME: [S.PLANTED + ":2"]}


def planted(rc=0, log=ON, scanned=REC, found=HIT, unlisted=(S.NAME,)):
    return S.judge_planted(rc, log, scanned, found, list(unlisted))[0]


def set_first(rc=0, log="", scanned=REC, found=None, unlisted=(), stale=(), bad=()):
    return S.judge_set_first(rc, log, scanned, found or {}, list(unlisted), list(stale), list(bad))[0]


def main():
    checks = [
        ("planted: the -D reached the test, the record names the file, the scan reports it there",
         planted() is True),
        ("planted: a failed configure fails", planted(rc=1) is False),
        ("planted: no record fails", planted(scanned=None) is False),
        ("planted: the planted file missing from the record fails", planted(scanned=REC[:1]) is False),
        ("planted: the -D not reaching the test fails (it would not be shown to be an option)",
         planted(log="") is False),
        ("planted: the scan not reporting it fails", planted(found={}, unlisted=()) is False),
        ("planted: reported as unlisted, but only from another file, fails",
         planted(found={S.NAME: ["main/CMakeLists.txt:9"]}) is False),
        ("planted: found but listed (not unlisted) fails", planted(unlisted=()) is False),
        ("set-first: the -D ignored, the file in the record, the name not reported, the scan clean",
         set_first() is True),
        ("set-first: a failed configure fails", set_first(rc=2) is False),
        ("set-first: no record fails", set_first(scanned=None) is False),
        ("set-first: the planted file missing from the record fails (the scan never read it)",
         set_first(scanned=REC[:1]) is False),
        ("set-first: the test still true fails", set_first(log=ON) is False),
        ("set-first: the name still reported fails", set_first(found=HIT) is False),
        ("set-first: any other finding fails the passing half",
         set_first(unlisted=["X"]) is False and set_first(stale=["Y"]) is False
         and set_first(bad=["Z: no reason"]) is False),
        ("the planted file, scanned for real, reports the name at line 2",
         [s for s, _ in BS.cmake_symbols(S.PLANTED_TEXT)] == [S.NAME]
         and BS.cmake_symbols(S.PLANTED_TEXT)[0][1] == 2),
        ("the set-first file, scanned for real, reports nothing",
         BS.cmake_symbols(S.SET_FIRST_TEXT) == []),
        ("the planted file's message is the marker the judgements look for",
         S.SAYS_ON in S.PLANTED_TEXT and S.SAYS_ON in S.SET_FIRST_TEXT),
        ("the include names the planted file", S.PLANTED in S.INCLUDE),
    ]
    fails = 0
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
