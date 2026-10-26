"""Harness test for reset_rate_arms.find_reset(): post-state only.

The key case is the last-but-one: unanswered pages (an HTTP stall) must NOT
count as a reset, which is the reviewer's 2026-10-07 rule.

Run: py -3.14 gen-inhibit/tools/bench/test_reset_rate_find_reset.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reset_rate_arms as R                                     # noqa: E402


def s(mode=3, tx=100, drv_null=False):
    return {"mode": mode, "tx_ok": tx, "drv_null": drv_null}


CASES = [
    ("steady live, no reset", [(1, s(tx=10)), (2, s(tx=20)), (3, s(tx=30))],
     None),
    ("mode 0 appears", [(1, s(tx=10)), (2, s(mode=0, tx=0, drv_null=True))],
     2),
    ("drv null while armed", [(1, s()), (2, s(drv_null=True))], 2),
    ("tx_ok falls, mode still 3 (a reset and an auto re-arm)",
     [(1, s(tx=500)), (2, s(tx=3))], 2),
    ("HTTP stall only: unanswered pages are not a reset",
     [(1, s(tx=10)), (2, None), (3, None), (4, s(tx=60))], None),
    ("stall, then mode 0", [(1, s(tx=10)), (2, None), (3, s(mode=0, tx=0))],
     3),
]


def main():
    bad = []
    for label, samples, want in CASES:
        r = R.find_reset(samples)
        got = None if r is None else r[0]
        ok = got == want
        print("  %-4s %-58s %s" % ("ok" if ok else "FAIL", label, r))
        if not ok:
            bad.append(label)
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
