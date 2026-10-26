"""Harness test for quiesce_analysis and e4's --post-schedule.

    py -3.14 gen-inhibit/tools/bench/test_quiesce_analysis.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quiesce_analysis as Q                                       # noqa: E402


def _live_wire(gd):
    v = gd.schemas_for().head_version
    return [(0.0, 0x7F1, bytes([v, 1, 3, 0x26, 0, 0, 0, 0])), (1.0, 0x7F1, bytes([v, 1, 3, 0x06, 0, 0, 0, 0])),
            (1.5, 0x7F2, bytes(8)), (2.0, 0x7F1, bytes([v, 1, 3, 0x66, 0, 0, 0, 0])), (2.5, 0x051, bytes(6))]


def _raises_cls(fn, cls):
    try:
        fn()
    except cls:
        return True
    except Exception:       # noqa: BLE001
        return False
    return False
import e4_device_calibration as E                                  # noqa: E402


def main():
    dev = [i * 0.01 + 0.0004 for i in range(1000)]          # answers every 10 ms, 0-10 s
    quiet = [t for t in dev if not (3.0 <= t < 6.0)]        # silent 3-6 s
    late = quiet + [4.5]                                    # one frame mid-OBSERVE
    span_ok = Q.span_report(3.0, 6.0, quiet, [])
    span_bad = Q.span_report(3.0, 6.0, sorted(late), [])
    span_tail = Q.span_report(3.0, 6.0, [3.0004, 3.0104], [3.02])
    LIVE_WIRE = _live_wire(Q.gi_diag)
    checks = [
        # live pages via gi_diag (toolkit item 10, reading Q7, 2026-10-24)
        ("live pages: 0x7F1 with diag_flags bit5 set, decoded on its schema",
         Q.live_page_times(LIVE_WIRE)[0] == [0.0, 2.0]),
        ("live pages: a 0x7F1 on a schema no DBC states raises, never read by hand",
         _raises_cls(lambda: Q.live_page_times(LIVE_WIRE + [(3.0, 0x7F1, bytes([0xEE, 1, 3, 0x26, 0, 0, 0, 0]))]),
                     Q.gi_diag.UnknownSchema)),
        ("schedule parses in time order", E.parse_schedule("20:3,10:1") == [(10.0, 1), (20.0, 3)]),
        ("schedule refuses a mode outside 0-4",
         _raises(lambda: E.parse_schedule("10:7"))),
        ("empty schedule is []", E.parse_schedule("") == []),
        ("OBSERVE span with no device frame: silent", Q.observe_verdict(span_ok) == "silent from the POST"),
        ("a device frame 1.5 s into OBSERVE is a VIOLATION",
         Q.observe_verdict(span_bad).startswith("VIOLATION")),
        ("frames only in the first 20 ms (the switch completing) are not a violation",
         Q.observe_verdict(span_tail).startswith("silent 20.0 ms")),
        ("span counts split 0x051 and other device frames",
         span_tail["device_0x051"] == 2 and span_tail["device_other"] == 1),
    ]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


if __name__ == "__main__":
    sys.exit(main())
