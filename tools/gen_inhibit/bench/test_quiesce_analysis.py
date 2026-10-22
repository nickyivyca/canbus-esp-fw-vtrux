"""Harness test for quiesce_analysis and e4's --post-schedule.

    py -3.14 tools/gen_inhibit/bench/test_quiesce_analysis.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quiesce_analysis as Q                                       # noqa: E402
import e4_device_calibration as E                                  # noqa: E402


def main():
    dev = [i * 0.01 + 0.0004 for i in range(1000)]          # answers every 10 ms, 0-10 s
    quiet = [t for t in dev if not (3.0 <= t < 6.0)]        # silent 3-6 s
    late = quiet + [4.5]                                    # one frame mid-OBSERVE
    span_ok = Q.span_report(3.0, 6.0, quiet, [])
    span_bad = Q.span_report(3.0, 6.0, sorted(late), [])
    span_tail = Q.span_report(3.0, 6.0, [3.0004, 3.0104], [3.02])
    checks = [
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
