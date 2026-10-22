"""Harness test for skip_method_bench: classify_event() and judge_arm().

Synthetic witness streams, no bus. Each classification and each judge outcome
has a case where it must come out the other way, so neither can pass by
construction.

Run from the project root:
  py -3.14 tools/gen_inhibit/bench/test_skip_method_judge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skip_method_bench as S                                   # noqa: E402

MK = "000000015a5a5a5a"


def p(c):
    return "080080ff7f%02x" % c


EV = {"tag": "t", "filler": True, "marker": MK, "n": p(4), "n1": p(5)}


def stream(seq):
    """seq: list of (dt_s, arb, hex) -> witness frames (seq, ts, arb, hex)."""
    out, t = [], 0.0
    for k, (dt, arb, hx) in enumerate(seq):
        t += dt
        out.append((k, t, arb, hx))
    return out


FILL = [(0.00022, 0x050, "a5" * 8)] * 9
BEFORE = [(0.01, 0x051, p(4)), (0.0005, 0x051, p(5))]   # an ordinary pair, same counters


def withdrawn():
    return stream(BEFORE + [(0.002, S.MARKER_ID, MK), (0.0002, 0x051, p(4)),
                            (0.00023, 0x051, p(5))] + FILL +
                  [(0.0003, 0x051, p(6)), (0.008, 0x051, p(6))])


def late_nofill():
    return stream(BEFORE + [(0.002, S.MARKER_ID, MK), (0.0002, 0x051, p(4)),
                            (0.00023, 0x051, p(5)), (0.0004, 0x051, p(5)),
                            (0.0003, 0x051, p(6)), (0.008, 0x051, p(6))])


def late_with_fill():
    return stream(BEFORE + [(0.002, S.MARKER_ID, MK), (0.0002, 0x051, p(4)),
                            (0.00023, 0x051, p(5))] + FILL +
                  [(0.0003, 0x051, p(5)), (0.0003, 0x051, p(6))])


def device_first_with_fill():
    return stream(BEFORE + [(0.002, S.MARKER_ID, MK), (0.0002, 0x051, p(4)),
                            (0.00023, 0x051, p(5)), (0.00023, 0x051, p(5))] +
                  FILL + [(0.0003, 0x051, p(6))])


def slow_n1():
    return stream(BEFORE + [(0.002, S.MARKER_ID, MK), (0.0002, 0x051, p(4)),
                            (0.003, 0x051, p(5))] + FILL +
                  [(0.0003, 0x051, p(6))])


def no_marker():
    return stream(BEFORE + [(0.0002, 0x051, p(4)), (0.00023, 0x051, p(5))])


CLASS_CASES = [
    ("withdrawn (filler, one N+1, ordinary pair before is ignored)",
     withdrawn(), EV, "withdrawn"),
    ("late, no filler arm", late_nofill(), dict(EV, filler=False), "late"),
    ("late after the filler", late_with_fill(), EV, "late"),
    ("device first in a filler arm -> displaced", device_first_with_fill(),
     EV, "displaced_no_filler_after_n1"),
    ("N+1 not back to back -> displaced", slow_n1(), EV,
     "displaced_not_back_to_back"),
    ("marker absent", no_marker(), EV, "marker_not_seen"),
]


def arm(name, classes, dw, dl, latched=False, reason="", pre_latched=False,
        live=True):
    return {"name": name, "went_live": live,
            "events_classified": [{"class": c} for c in classes],
            "pre_events": {"skips_withdrawn": 0, "skips_late": 0,
                           "abort_latched": pre_latched,
                           "abort_reason": "x" if pre_latched else ""},
            "post_events": {"skips_withdrawn": dw, "skips_late": dl,
                            "abort_latched": latched, "abort_reason": reason}}


T7 = "3 inhibit frames skipped within 1 s"
JUDGE_CASES = [
    ("w3 clean", arm("w3", ["withdrawn"] * 3, 3, 0, True, T7), "PASS"),
    ("w3 no trip", arm("w3", ["withdrawn"] * 3, 3, 0), "FAIL"),
    ("w2 clean", arm("w2", ["withdrawn"] * 2, 2, 0), "PASS"),
    ("w2 tripped", arm("w2", ["withdrawn"] * 2, 2, 0, True, T7), "FAIL"),
    ("w3s clean", arm("w3s", ["withdrawn"] * 3, 3, 0), "PASS"),
    ("l3 clean", arm("l3", ["late"] * 3, 0, 3, True, T7), "PASS"),
    ("l3 device missed a late frame", arm("l3", ["late"] * 3, 0, 2, True, T7),
     "FAIL"),
    ("w3 witness saw late, device withdrawn",
     arm("w3", ["late", "withdrawn", "withdrawn"], 3, 0, True, T7), "FAIL"),
    ("l2 other abort latched", arm("l2", ["late"] * 2, 0, 2, True,
                                   "error-frame rate exceeded"),
     "INCONCLUSIVE"),
    ("displaced event", arm("w2", ["withdrawn", "displaced_not_back_to_back"],
                            2, 0), "INCONCLUSIVE"),
    ("already latched before", arm("w2", ["withdrawn"] * 2, 2, 0,
                                   pre_latched=True), "INCONCLUSIVE"),
    ("never live", arm("w3", [], 0, 0, live=False), "INCONCLUSIVE"),
]


# judge_arm_v2 (reviewer ruling 2026-10-07): spec 7 / 5.2 govern the split.
V2_CASES = [
    ("v2 w3 booked late (the 23:47 shape)",
     arm("w3", ["withdrawn"] * 3, 0, 3, True, T7), "PASS"),
    ("v2 w2 booked late", arm("w2", ["withdrawn"] * 2, 0, 2), "PASS"),
    ("v2 w2 booked withdrawn (also allowed)",
     arm("w2", ["withdrawn"] * 2, 2, 0), "PASS"),
    ("v2 l3 clean", arm("l3", ["late"] * 3, 0, 3, True, T7), "PASS"),
    ("v2 rule 1: device under-counts", arm("w2", ["withdrawn"] * 2, 0, 1),
     "FAIL"),
    ("v2 rule 1: device over-counts", arm("w2", ["withdrawn"] * 2, 1, 2),
     "FAIL"),
    ("v2 rule 2: a wire-late frame booked withdrawn",
     arm("l2", ["late"] * 2, 1, 1), "FAIL"),
    ("v2 rule 3: withdrawn exceeds never-on-wire",
     arm("w3", ["late", "withdrawn", "withdrawn"], 3, 0, True, T7), "FAIL"),
    ("v2 rule 4: trip missing", arm("w3", ["withdrawn"] * 3, 0, 3), "FAIL"),
    ("v2 rule 4: trip where it must not", arm("w3s", ["withdrawn"] * 3, 0, 3,
                                              True, T7), "FAIL"),
    ("v2 foreign abort", arm("l2", ["late"] * 2, 0, 2, True,
                             "error-frame rate exceeded"), "INCONCLUSIVE"),
]


def main():
    failures = []
    for label, a, want in V2_CASES:
        got = S.judge_arm_v2(a)
        ok = got["verdict"] == want
        print("  %-4s judge v2 %-58s %s" % ("ok" if ok else "FAIL", label,
                                           got["verdict"]))
        if not ok:
            failures.append(label)
            print("       %s" % got)
    for label, fr, ev, want in CLASS_CASES:
        got, _ = S.classify_event(fr, ev)
        ok = got.get("class") == want
        print("  %-4s classify %-58s %s" % ("ok" if ok else "FAIL", label,
                                           got.get("class")))
        if not ok:
            failures.append(label)
            print("       %s" % got)
    for label, a, want in JUDGE_CASES:
        got = S.judge_arm(a)
        ok = got["verdict"] == want
        print("  %-4s judge    %-58s %s" % ("ok" if ok else "FAIL", label,
                                           got["verdict"]))
        if not ok:
            failures.append(label)
            print("       %s" % got)
    ERR_CASES = [
        ("errors split by event window",
         S.errors_per_event([1.0, 1.4, 1.8], [1.1, 1.5, 1.6, 2.0, 2.9]), [1, 2, 1]),
        ("an error before the first marker is not an event's",
         S.errors_per_event([1.0], [0.9, 1.2]), [1]),
        ("an event whose marker was not seen gets None, others unaffected",
         S.errors_per_event([1.0, None, 2.0], [1.5, 2.5]), [1, None, 1]),
        ("the last event's window is tail_s long",
         S.errors_per_event([1.0], [1.5, 2.5], tail_s=1.0), [1]),
    ]
    for label, got, want in ERR_CASES:
        ok = got == want
        print("  %-4s errors   %-58s %s" % ("ok" if ok else "FAIL", label, got))
        if not ok:
            failures.append(label)
    print("\n=== %s ===" % ("PASS" if not failures else
                            "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
