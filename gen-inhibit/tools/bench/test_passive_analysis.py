"""Harness test for passive_analysis: interval judgement and diag decoding.

    py -3.14 gen-inhibit/tools/bench/test_passive_analysis.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import passive_analysis as A                                       # noqa: E402

# A 0x7F1 on HEAD's schema: the pages after it decode on that schema (gi_diag, 2026-10-24).
S7 = bytes([A.gi_diag.schemas_for().head_version, 1, 3, 0x06, 0, 0, 0, 0])


def raises(fn, cls):
    try:
        fn()
    except cls:
        return True
    except Exception:       # noqa: BLE001
        return False
    return False

CMDS = [i * 0.01 for i in range(1000)]          # a command every 10 ms for 10 s


def S(points):
    return [(t, c) for t, c in points]


def main():
    # idle until 2.0 s, live from 2.005 s (command 201 on) to 7.995 s, then latched
    def count(t):
        lo, hi = 2.005, 7.995
        return max(0, sum(1 for c in CMDS if lo <= c <= min(t, hi)))
    clean = S([(t, count(t)) for t in (1.0, 1.9, 3.0, 4.1, 5.0, 6.2, 7.3, 8.4, 9.5)])
    _r, ok = A.judge_intervals(clean, CMDS)
    over = list(clean)
    over[4] = (over[4][0], over[4][1] + 7)
    _r, bad = A.judge_intervals(over, CMDS)
    mid = S([(1.0, 0), (2.0, 0), (3.0, 100), (4.0, 150), (5.0, 250), (6.0, 350)])
    _r, midr = A.judge_intervals(mid, CMDS)
    _r, idle = A.judge_intervals(S([(1.0, 0), (2.0, 0), (3.0, 0)]), CMDS)
    one_off = S([(1.0, 0), (2.0, 100), (3.0, 201)])
    _r, oo = A.judge_intervals(one_off, CMDS)
    checks = [
        ("a clean live span: FULL in the middle, PARTIAL only at its two edges, ok",
         ok["ok"] and ok["over"] == 0 and ok["partial_not_at_an_edge"] == 0 and ok["full"] >= 3
         and ok["partial"] == 2),
        ("a counter rising more than the commands (OVER) fails",
         not bad["ok"] and bad["over"] >= 1),
        ("a short rise in the middle of a live span (PARTIAL not at an edge) fails",
         not midr["ok"] and midr["partial_not_at_an_edge"] == 1),
        ("never live at all: all IDLE is not ok (nothing was tested)",
         not idle["ok"] and idle["idle"] == 2),
        ("a page built one command early or late (+/-1) is still FULL",
         oo["full"] == 2 and oo["ok"]),
        ("from_zero: a counter that reset at the arm passes",
         A.from_zero(clean, CMDS, None)["ok"] is True),
        ("from_zero: the same counter carrying 700 from an earlier arm fails",
         A.from_zero([(t, c + 700) for t, c in clean], CMDS, None)["ok"] is False),
        ("from_zero: carried only at the first sample (reset late) fails",
         A.from_zero([(clean[0][0], 700)] + clean[1:], CMDS, None)["ok"] is False),
        ("from_zero: carried only at the end fails",
         A.from_zero(clean[:-1] + [(clean[-1][0], clean[-1][1] + 700)], CMDS, None)["ok"] is False),
        ("from_zero: one command over (a page built a command late) still passes",
         A.from_zero([(1.0, 0), (2.5, 252)], CMDS, None)["ok"] is True),
        ("from_zero: two over fails", A.from_zero([(1.0, 0), (2.5, 253)], CMDS, None)["ok"] is False),
        ("from_zero: an arm at 5 s counts commands from 5 s, and samples before it are ignored",
         A.from_zero([(1.0, 900), (5.5, 49), (6.5, 149)], CMDS, 5.0)["ok"] is True
         and A.from_zero([(5.5, 100)], CMDS, 5.0)["ok"] is False),
        ("from_zero: no sample after the arm -> None, not a pass",
         A.from_zero([(1.0, 0)], CMDS, 5.0)["ok"] is None),
        ("the diag decoder reads would_tx from 0x7F8 and tx_ok from 0x7F2 via the DBC (after a 0x7F1)",
         (lambda d: [s.get("diag_would_tx", s.get("diag_tx_ok")) for _t, i, s in d[0] if i != 0x7F1]
          == [0x1234, 77] and d[1]["undecodable"] == 0)(
             A.decode_diag([(-0.1, 0x7F1, S7),
                            (0.0, 0x7F8, bytes([0, 0, 0, 0x34, 0x12, 0, 0, 0])),
                            (0.1, 0x7F2, bytes([77, 0, 0, 0, 0, 0, 0, 0])),
                            (0.2, 0x123, bytes(8))]))),
        ("a 0x7F8 before the first schema page is counted undecodable, not decoded (reading Q2)",
         (lambda d: [i for _t, i, _s in d[0]] == [0x7F1] and d[1]["undecodable"] == 1)(
             A.decode_diag([(0.0, 0x7F8, bytes([0, 0, 0, 0x34, 0x12, 0, 0, 0])), (0.1, 0x7F1, S7)]))),
        # Reading Q9 (2026-10-25): "error" only when a judged figure cannot be computed.
        ("figure_error: a refused page with every figure computable -> no error",
         A.figure_error([(1.0, 0), (2.0, 100)], [(0.5, {})], {"undecodable": 1, "undecodable_by_reason": {"x": 1}},
                        "diag_tx_ok") is None),
        ("figure_error: one counter sample -> the intervals cannot be computed",
         (lambda e: e is not None and "intervals" in e and "from_zero" not in e)(
             A.figure_error([(1.0, 0)], [(0.5, {})], {"undecodable": 3, "undecodable_by_reason": {"x": 3}}, "diag_tx_ok"))),
        ("figure_error: no counter sample -> intervals and from_zero, naming the refused pages",
         (lambda e: e is not None and "from_zero" in e and "3 page(s) refused" in e)(
             A.figure_error([], [(0.5, {})], {"undecodable": 3, "undecodable_by_reason": {"x": 3}}, "diag_would_tx"))),
        ("figure_error: no 0x7F1 -> diag_modes",
         (lambda e: e is not None and "diag_modes" in e)(
             A.figure_error([(1.0, 0), (2.0, 100)], [], {"undecodable": 0, "undecodable_by_reason": {}}, "diag_tx_ok"))),
        ("an unknown schema byte raises (gi_diag.UnknownSchema)",
         raises(lambda: A.decode_diag([(0.0, 0x7F1, bytes([0xEE] + [0] * 7))]), A.gi_diag.UnknownSchema)),
    ]
    fails = []
    for label, okk in checks:
        print("  %-4s %s" % ("ok" if okk else "FAIL", label))
        if not okk:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
