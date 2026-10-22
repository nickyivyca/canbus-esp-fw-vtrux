"""Harness test for mode_entry_analysis and e4's --arm-after poster.

    py -3.14 tools/gen_inhibit/bench/test_mode_entry_analysis.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mode_entry_analysis as M                                     # noqa: E402
import e4_device_calibration as E                                   # noqa: E402


def poster_ok():
    calls = []

    def fake_gi(path, method="GET", timeout=6.0):
        calls.append((path, method))
        time.sleep(0.02)
        return 200, "ok"
    real = E.gi
    E.gi = fake_gi
    try:
        E.OFFSET_US = 500
        p = E.ModePoster(3)
        p.base = time.perf_counter()
        p.start()
        p.join(2)
    finally:
        E.gi = real
    return (calls == [("/gen_inhibit_set?mode=3&offset=500", "POST")] and p.status == 200
            and p.t_end - p.t_start >= 0.019)


def main():
    cmds = [i * 0.01 for i in range(10)]                     # 0.00 .. 0.09
    ans = [c + 0.0004 for c in cmds[3:]]                     # answers from command 3 on
    un, first = M.unanswered_after(cmds, ans, 0.015)
    ans2 = [c + 0.0004 for c in cmds if c not in (0.05,)]    # one gap mid-run
    un2, _ = M.unanswered_after(cmds, ans2, 0.0)
    late = [c + 0.0004 for c in cmds[:4]] + [cmds[5] + 0.0002] + [c + 0.0004 for c in cmds[5:]]
    un3, _ = M.unanswered_after(cmds, sorted(late), 0.0)
    checks = [
        ("commands before t_from are not counted; the first after it unanswered until "
         "answers begin", [k for k, _ in un] == [2] and abs(first - 0.0304) < 1e-9),
        ("a gap mid-run is found", [k for k, _ in un2] == [5]),
        ("an answer arriving after the next command does not count for the earlier one",
         [k for k, _ in un3] == [4]),
        ("no answers at all -> every command after t_from unanswered, first None",
         (lambda r: len(r[0]) == 5 and r[1] is None)(M.unanswered_after(cmds, [], 0.05))),
        ("e4 --arm-after poster POSTs the mode with the offset and records start/end",
         poster_ok()),
        ("entry: --arm-after's post_index enters the run's own mode",
         M.entry_point("respond", {"post_index": 7, "post_schedule": [{"mode": 3, "index": 9}]})
         == (7, "arm-after", "respond")),
        ("entry: a --post-schedule entry into 3 is a re-arm into inhibit",
         M.entry_point("observe", {"post_index": None, "post_schedule": [
             {"at_s": 20.0, "mode": 3, "index": 47182}]})
         == (47182, "schedule 20.0:3 from observe", "inhibit")),
        ("entry: a scheduled POST into another mode, or one never sent, is not an entry",
         M.entry_point("inhibit", {"post_schedule": [{"at_s": 10, "mode": 1, "index": 5},
                                                     {"at_s": 20, "mode": 3, "index": None}]})
         is None),
        ("entry: neither -> None", M.entry_point("off", {}) is None),
        ("e4 records arm_after_s and post_index in the arm", "\"post_index\": post_index"
         in open(E.__file__, encoding="utf-8").read()),
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
