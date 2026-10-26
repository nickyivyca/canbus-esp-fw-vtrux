"""Harness test for wrong_bitrate_errors: schedule parsing, the sliding 10 s window,
per-burst attribution.

    py -3.14 gen-inhibit/tools/bench/test_wrong_bitrate_errors.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wrong_bitrate_errors as W                                   # noqa: E402


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def main():
    checks = [
        ("bursts parse in time order", W.parse_bursts("3:5,2:1") == [(2.0, 1.0), (3.0, 5.0)]),
        ("a zero-length burst is refused", _raises(lambda: W.parse_bursts("2:0"))),
        ("10 errors 1 s apart: 10 within 10 s (inclusive edge)",
         W.window_max([float(i) for i in range(10)]) == 10),
        ("10 errors 1.2 s apart: at most 9 within 10 s",
         W.window_max([i * 1.2 for i in range(10)]) == 9),
        ("exactly 10.0 s between the 1st and 10th counts (spec 7: inclusive)",
         W.window_max([0.0] + [10.0 * k / 9 for k in range(1, 10)]) == 10),
        ("errors split by burst window", W.per_burst([1.0, 2.0], [1.1, 1.2, 2.05, 2.7]) == [2, 1]),
        ("errors before the first burst are not attributed", W.per_burst([1.0], [0.5, 1.2]) == [1]),
    ]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
