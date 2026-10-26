"""Harness test for trip6_rearm_check: the expired-arm and recent-arm judges.

    py -3.14 gen-inhibit/tools/bench/test_trip6_rearm_check.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trip6_rearm_check as T                                      # noqa: E402

TRIP = T.TRIP6
CLEAN = [{"live": False, "abort": ""}, {"live": True, "abort": ""}, {"live": True, "abort": ""}]
# the cc34c80 shape (19:22): latched at the first live check, never live
PHANTOM = [{"live": False, "abort": ""}, {"live": False, "abort": TRIP}, {"live": False, "abort": TRIP}]
# live, then tripped: a trip on an expired burst after going live is still a failure
LATE_TRIP = [{"live": True, "abort": ""}, {"live": False, "abort": TRIP}]


def main():
    checks = [
        ("expired arm: live, no abort -> as specified", T.judge_expired(CLEAN, 0) == "as specified"),
        ("expired arm: trip 6 at the first live check -> NOT",
         T.judge_expired(PHANTOM, 0).startswith("NOT")),
        ("expired arm: live then trip 6 -> NOT", T.judge_expired(LATE_TRIP, 0).startswith("NOT")),
        ("expired arm: never live, no abort -> NOT",
         T.judge_expired([{"live": False, "abort": ""}], 0).startswith("NOT")),
        ("expired arm: a new error frame since arming -> VOID, even if it tripped",
         T.judge_expired(PHANTOM, 1).startswith("VOID")),
        ("expired arm: another abort (not trip 6) is not read as trip 6",
         T.judge_expired([{"live": True, "abort": ""}, {"live": False, "abort": "transmit failed"}], 0)
         == "as specified"),
        ("recent arm: trip 6 -> as specified", T.judge_recent(PHANTOM, 2.0, 0) == "as specified"),
        ("recent arm: live, no trip -> NOT", T.judge_recent(CLEAN, 2.0, 0).startswith("NOT")),
        ("recent arm: armed at the limit still judged", T.judge_recent(PHANTOM, T.RECENT_MAX_S, 0) == "as specified"),
        ("recent arm: armed past the limit -> VOID", T.judge_recent(CLEAN, T.RECENT_MAX_S + 0.1, 0).startswith("VOID")),
        ("recent arm: no burst seen -> VOID", T.judge_recent(PHANTOM, None, 0).startswith("VOID")),
        ("recent arm: the limit leaves margin under the spec's 10 s", T.RECENT_MAX_S < 10.0),
        ("recent arm: one inhibit frame before the latch -> NOT (spec 7, checked before going live)",
         T.judge_recent(PHANTOM, 2.0, 1).startswith("NOT")),
        ("recent arm: live, then trip 6 (the old order) -> NOT", T.judge_recent(LATE_TRIP, 2.0, 0).startswith("NOT")),
        ("recent arm: never live, no frames, but no trip 6 either -> NOT",
         T.judge_recent([{"live": False, "abort": ""}], 2.0, 0).startswith("NOT")),
        ("witness: a typed PCAN error frame (bit/form/stuff/other) is a wire error",
         len(T.wire_errors([(1.0, 1, "0012a000"), (1.1, 4, "00068000"), (1.2, 8, "00000100")])) == 3),
        ("witness: an id-0 counter report (00 00 7a 00) is not a wire error",
         T.wire_errors([(1.0, 0, "00007a00"), (1.2, 2, "00118000")]) == [(1.2, 2, "00118000")]),
    ]
    rec = {"err_n": 14, "err_age_ms": 2050, "err_burst": True}
    exp = {"err_n": 0, "err_age_ms": 15030, "err_burst": False}
    H = T.judge_history
    checks += [
        ("history, recent: burst true, n >= 10, age = witness gap -> as specified",
         H(rec, 2.0, True) == "as specified"),
        ("history, expired: burst false, n 0, age = gap -> as specified", H(exp, 15.0, False) == "as specified"),
        ("history: fields absent (a pre-2026-10-12 build) -> UNRESOLVED, never a pass",
         H({"mode": 0}, 2.0, True).startswith("UNRESOLVED") and H(None, 2.0, True).startswith("UNRESOLVED")),
        ("history, recent: err_burst false -> NOT", H(dict(rec, err_burst=False), 2.0, True).startswith("NOT")),
        ("history, recent: err_n 9 -> NOT", H(dict(rec, err_n=9), 2.0, True).startswith("NOT")),
        ("history, expired: err_burst true -> NOT", H(dict(exp, err_burst=True), 15.0, False).startswith("NOT")),
        ("history, expired: err_n 3 -> NOT", H(dict(exp, err_n=3), 15.0, False).startswith("NOT")),
        ("history: age off by 0.6 s -> NOT; by 0.4 s -> as specified",
         H(dict(rec, err_age_ms=2600), 2.0, True).startswith("NOT")
         and H(dict(rec, err_age_ms=2400), 2.0, True) == "as specified"
         and H(dict(rec, err_age_ms=1400), 2.0, True).startswith("NOT")),
        ("history: age null after a burst -> NOT", H(dict(rec, err_age_ms=None), 2.0, True).startswith("NOT")),
        ("history: err_burst as a string 'true' is not true -> NOT",
         H(dict(rec, err_burst="true"), 2.0, True).startswith("NOT")),
        ("history: no witness error -> VOID", H(rec, None, True).startswith("VOID")),
        ("prediction: burst true and trip 6 latched -> as specified",
         T.judge_prediction(rec, PHANTOM) == "as specified"),
        ("prediction: burst false and live without a trip -> as specified",
         T.judge_prediction(exp, CLEAN) == "as specified"),
        ("prediction: burst false but trip 6 latched (the 10-10 shape) -> NOT",
         T.judge_prediction(exp, PHANTOM).startswith("NOT")),
        ("prediction: burst true but no trip -> NOT", T.judge_prediction(rec, CLEAN).startswith("NOT")),
        ("prediction: another abort is not trip 6 -> burst true is NOT",
         T.judge_prediction(rec, [{"live": False, "abort": "transmit failed"}]).startswith("NOT")),
        ("prediction: no err_burst -> UNRESOLVED", T.judge_prediction({"mode": 0}, PHANTOM).startswith("UNRESOLVED")),
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
