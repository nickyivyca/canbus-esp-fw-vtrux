"""Harness test for respond_offset_sweep: comparability flags, grouping, plans.

    py -3.14 gen-inhibit/tools/bench/test_respond_offset_sweep.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import respond_offset_sweep as W                                 # noqa: E402


def R(off, fps=2364.6, read=None, missed=0, void=False, mode="respond", arm="idle",
      b2b=0.75, poll=0):
    return {"offset": off, "offset_read": off if read is None else read, "send_fps": fps,
            "rx_missed": missed, "void": void, "void_reasons": ["x"] if void else [],
            "mode": mode, "arm": arm, "b2b": b2b, "longest_b2b_run": 60, "poll_hz": poll}


def main():
    rows = [R(100, missed=0), R(500, missed=14), R(1000, missed=30), R(500, fps=2300.0, missed=99),
            R(100, read=500, missed=1), R(1000, void=True)]
    med = W.comparability(rows)
    flags = [bool(r["not_comparable"]) for r in rows]
    checks = [
        ("median of the replay rates", abs(med - 2364.6) < 0.01),
        ("a replay 2.7 % slow is NOT COMPARABLE, and kept", flags[3] and len(rows) == 6),
        ("device reading back another offset is NOT COMPARABLE", flags[4]),
        ("a VOID arm is NOT COMPARABLE", flags[5]),
        ("normal arms are comparable", flags[:3] == [False, False, False]),
        ("grouping uses comparable arms only",
         W.by_offset(rows) == {100: [0], 500: [14], 1000: [30]}),
        ("no rows with a rate -> no median, every row flagged, no crash",
         (lambda rs: W.comparability(rs) is None and all(r["not_comparable"] for r in rs))(
             [R(100, fps=None)])),
        ("offsets plan: nine RESPOND idle arms, three per offset, interleaved",
         [o for _, _, o, _ in W.PLANS["offsets"]] == [100, 500, 1000, 500, 1000, 100, 1000, 100, 500]
         and all(m == "respond" and a == "idle" and p == 0 for m, a, _, p in W.PLANS["offsets"])),
        ("wifi plan: idle/loaded/loaded/idle in RESPOND then INHIBIT, all at 500",
         [(m, a) for m, a, _, _ in W.PLANS["wifi"]] ==
         [("respond", "idle"), ("respond", "loaded"), ("respond", "loaded"), ("respond", "idle"),
          ("inhibit", "idle"), ("inhibit", "loaded"), ("inhibit", "loaded"), ("inhibit", "idle")]
         and all(o == 500 for _, _, o, _ in W.PLANS["wifi"])),
        ("poll plan: six RESPOND idle at 500, unpolled and 1 Hz interleaved, 3 each",
         [p for _, _, _, p in W.PLANS["poll"]] == [0, 1, 1, 0, 0, 1]
         and all(m == "respond" and a == "idle" and o == 500 for m, a, o, _ in W.PLANS["poll"])),
        ("polled arms are their own group, never pooled with unpolled",
         W.by_condition([dict(R(500, missed=2, poll=1), not_comparable=[]),
                         dict(R(500, missed=0), not_comparable=[])])
         == {"respond/idle/poll1Hz": [2], "respond/idle": [0]}),
        ("a bunched replay (b2b 0.85) is NOT COMPARABLE even when the mean rate is normal",
         (lambda rs: (W.comparability(rs), bool(rs[0]["not_comparable"]))[1])([R(500, b2b=0.85)])),
        ("a whole plan bunched is flagged arm by arm (absolute bound, not the median)",
         (lambda rs: (W.comparability(rs), all(r["not_comparable"] for r in rs))[1])(
             [R(500, b2b=0.9), R(500, b2b=0.89), R(500, b2b=0.91)])),
        ("burstiness: gaps 100/100/1000 us -> 2/3 back to back, run 2",
         W.burstiness([0, 0.0001, 0.0002, 0.0012]) == (0.6667, 2)),
        ("wifi grouping is by mode/arm",
         W.by_condition([dict(R(500, missed=3, arm="loaded"), not_comparable=[]),
                         dict(R(500, missed=0), not_comparable=[])])
         == {"respond/loaded": [3], "respond/idle": [0]}),
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
