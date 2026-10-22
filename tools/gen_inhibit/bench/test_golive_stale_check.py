"""Harness test for golive_stale_check: which device 0x051 are stale, and the verdict.

    py -3.14 tools/gen_inhibit/bench/test_golive_stale_check.py   (from the project root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import golive_stale_check as G                                      # noqa: E402

# classifier records: [t, device hex, front t, front hex, us, ("delta N")]
LATE = [10.0, "080080ff7f04", 9.99, "080080ff7f04", 10000]
BEHIND14 = [10.0, "080080ff7f02", 9.99, "080080ff7f04", 8020, "delta 14"]     # the OBSERVE burst
BEHIND15 = [10.001, "080080ff7f03", 9.99, "080080ff7f04", 8700, "delta 15"]
AHEAD3 = [11.0, "080080ff7f07", 10.99, "080080ff7f04", 500, "delta 3"]
NOFRONT = [0.5, "080080ff7f01", "no replayed command in front"]
CLEAN = {"verdict": "as specified", "late": [], "unexplained": [], "double": [], "unresolved": []}


def P(**kw):
    return dict(dict(CLEAN, verdict="NOT as specified"), **kw)


def main():
    checks = [
        ("delta parsed from the classifier's record", G.delta_of(BEHIND14) == 14),
        ("a late frame (delta 0) is stale", len(G.stale_frames(P(late=[LATE]))) == 1),
        ("delta 14 and 15 (behind by 2 and 1) are stale",
         len(G.stale_frames(P(unexplained=[BEHIND14, BEHIND15]))) == 2),
        ("delta 9 (behind by 7) is stale; delta 8 is not",
         [x[3] for x in G.stale_frames(P(unexplained=[
             BEHIND14[:5] + ["delta 9"], BEHIND14[:5] + ["delta 8"]]))] == [9]),
        ("a counter ahead (delta 3) is not stale but is reported as ahead",
         not G.stale_frames(P(unexplained=[AHEAD3])) and len(G.ahead_frames(P(unexplained=[AHEAD3]))) == 1),
        ("clean, device frames present -> as specified", G.judge(CLEAN, 1500) == "as specified"),
        ("stale -> NOT", G.judge(P(unexplained=[BEHIND14]), 1500).startswith("NOT")),
        ("late -> NOT", G.judge(P(late=[LATE]), 1500).startswith("NOT")),
        ("ahead only -> NOT (not stale, but the arm fails)", G.judge(P(unexplained=[AHEAD3]), 1500).startswith("NOT")),
        ("a double -> NOT", G.judge(P(double=[[LATE, AHEAD3]]), 1500).startswith("NOT")),
        ("no command in front -> NOT", G.judge(P(unexplained=[NOFRONT]), 1500).startswith("NOT")),
        ("never live -> VOID", G.judge(CLEAN, 0).startswith("VOID")),
        ("classifier UNRESOLVED -> UNRESOLVED, even with stale frames",
         G.judge(P(verdict="UNRESOLVED", unresolved=["count"], late=[LATE]), 1500).startswith("UNRESOLVED")),
        ("only frames after the entry are judged: a stale frame before it does not fail the boot",
         G.judge(P(unexplained=[BEHIND14]), 900, after=10.5) == "as specified"),
        ("a stale frame after the entry fails it", G.judge(P(unexplained=[AHEAD3[:5] + ["delta 15"]]), 900,
                                                          after=10.5).startswith("NOT")),
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
