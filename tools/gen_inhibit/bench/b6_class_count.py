#!/usr/bin/env python3
"""The B6 stop rule's class count (reviewer's plan, 2026-10-20): pulses whose next frame
on the Kvaser after the error frame is an 0x051 starting 200-320 us later, split by the
device's bus_error count for that pulse (1 = single-count, >1 = over-count by the page).
Uses b6_multicount_landing.arm_rows, so the grouping and the frame lookup are that
tool's. Reads records only. Gen-inhibit tester.

    py -3.14 -B tools/gen_inhibit/bench/b6_class_count.py ROOT [ROOT ...]
"""
import collections
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b6_multicount_landing as L    # noqa: E402

LO_US, HI_US = 200.0, 320.0
CLASS_ID = 0x051


def in_class(e, f):
    # Rounded to 1 ns: the times are float seconds near the epoch, so a gap of exactly
    # 200 us can come out 199.99999; the witness's clock is microseconds.
    return f is not None and f[1] == CLASS_ID and LO_US <= round((f[0] - e) * 1e6, 3) <= HI_US


def main(roots):
    paths = sorted({p for r in roots for p in glob.glob(os.path.join(r, "**", "x2_jam_threshold_*.json"), recursive=True)
                    if "_witness" not in p})
    by = collections.Counter()
    nxt_ids = collections.Counter()
    for p in paths:
        for row in L.arm_rows(p):
            if len(row) == 2:
                print("no witness: %s" % os.path.basename(row[1]))
                continue
            _path, k, i, d, e, _b, f, _tr = row
            if d is None or e is None:
                by["no delta or no error frame"] += 1
                continue
            if f is not None:
                nxt_ids["0x%03X" % f[1]] += 1
            c = in_class(e, f)
            key = ("class" if c else "other") + (" single" if d == 1 else " zero" if d == 0 else " over")
            by[key] += 1
            if c and d != 1:
                print("  class pulse not single-count: %s arm %d pulse %d delta %d" % (os.path.basename(p), k, i + 1, d))
    print("records %d" % len(paths))
    for k in sorted(by):
        print("  %-28s %d" % (k, by[k]))
    print("  next-frame ids after the error frame: %s" % ", ".join("%s %d" % kv for kv in nxt_ids.most_common(8)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
