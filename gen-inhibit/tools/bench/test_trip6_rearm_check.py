"""Harness test for trip6_rearm_check: the expired-arm and recent-arm judges.

    py -3.14 gen-inhibit/tools/bench/test_trip6_rearm_check.py   (from the repo root)
Mutants: test_trip6_rearm_check_mutants.py (TRIP6_DIR runs another copy of the tool).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("TRIP6_DIR"):
    sys.path.insert(0, os.environ["TRIP6_DIR"])
import trip6_rearm_check as T                                      # noqa: E402

TRIP = T.TRIP6
CLEAN = [{"live": False, "abort": ""}, {"live": True, "abort": ""}, {"live": True, "abort": ""}]
# the cc34c80 shape (19:22): latched at the first live check, never live
PHANTOM = [{"live": False, "abort": ""}, {"live": False, "abort": TRIP}, {"live": False, "abort": TRIP}]
# live, then tripped: a trip on an expired burst after going live is still a failure
LATE_TRIP = [{"live": True, "abort": ""}, {"live": False, "abort": TRIP}]
# run C's B10 A4 (gen-inhibit-bench_20261120T122548_6846): live for less than a read's spacing,
# so no read live; the next read aborted, tx_ok 6 in mode 3
A4_RUNC = [{"live": False, "abort": "", "mode": 3, "tx_ok": 0, "block": "SoC not yet valid"},
           {"live": False, "abort": "0x617 went stale while live", "mode": 3, "tx_ok": 6}]
A4_NO_TX = [{"live": False, "abort": "", "mode": 3, "tx_ok": 0},
            {"live": False, "abort": "0x617 went stale while live", "mode": 3, "tx_ok": 0}]


def player_times():
    """skip_method_bench.Player with record_ids=(0x617,) on a fake bus that refuses every third
    0x617: a stamp for each 0x617 the bus took, none for a refused one or another id."""
    import time
    import skip_method_bench as S
    took, n617 = [], [0]

    class SendSpy:      # a fake with send() only: not a bus open (bench_scan, test_kvaser_bus)
        def send(self, m):
            if m.arbitration_id == 0x617:
                n617[0] += 1
                if n617[0] % 3 == 0:
                    raise OSError("refused (test)")
            took.append(m.arbitration_id)
    stim = [(0.0, 0x100, b"\x00"), (0.002, 0x617, b"\x00"), (0.004, 0x200, b"\x00"), (0.006, 0x617, b"\x01")]
    p = S.Player(SendSpy(), stim, record_ids=(0x617,))
    p.start()
    time.sleep(0.1)
    p.stop_flag.set()
    p.join(5)
    ts = p.sent_at[0x617]
    return (set(p.sent_at) == {0x617} and len(ts) == took.count(0x617) > 2 and ts == sorted(ts)
            and p.fail == n617[0] // 3 and S.Player(SendSpy(), stim).sent_at == {})


def arm_line_has_aborts():
    """main prints each arm's aborts (their reasons) on its line, before its verdict: where a
    live-then-aborted arm's abort is recorded (read from the source: main needs the bench)."""
    import inspect
    src = inspect.getsource(T.main)
    i = src.find("aborts = sorted({r.get(\"abort\") for r in arm[\"reads\"] if r.get(\"abort\")})")
    j = src.find("aborts %s  %s\"", i)
    k = src.find("aborts, arm.get(\"verdict\", \"\")))", j)
    return 0 < i < j < k


def bursts_per_ruling():
    """B10's bursts (the gen-inhibit reviewer's ruling of 2026-11-20, item 4(ii)): the run VOIDs only on
    run_void_line (a burst with no witness error frame), each burst is read with its source's own state,
    recorded, and printed under the arm (read from the source: main needs the bench)."""
    import inspect
    src = inspect.getsource(T.main)
    return ("burst_line = BM.run_void_line([arm[\"burst_measure\"]" in src and "BM.breach_line" not in src
            and "W.burst(a.burst_ms, inno, read_source=True)" in src and "arm[\"burst_source\"] = src_state" in src
            and "W.burst_detail_line({\"source\": arm[\"burst_source\"]}, arm.get(\"burst_measure\"))" in src)


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
        # Live, then aborted other than by trip 6: B10's property shown (live, no trip 6 latch), so as
        # specified, the abort in the arm line; told apart from never live (the gen-inhibit reviewer,
        # 2026-11-20; for a day it read UNCLEAR)
        ("expired arm: live then another abort (not trip 6) -> as specified",
         T.judge_expired([{"live": True, "abort": ""}, {"live": False, "abort": "transmit failed"}], 0)
         == "as specified"),
        # run C's B10 A4: no read live, but tx_ok 6 in mode 3 and device 0x051 since the arm
        ("expired arm: the A4 shape (no read live; 0x051 seen; aborted 0x617 stale) -> as specified, not never live",
         T.judge_expired(A4_RUNC, 0, 6) == "as specified"),
        ("expired arm: ... live from tx_ok in mode 3 alone -> as specified",
         T.judge_expired(A4_RUNC, 0, 0) == "as specified"),
        ("expired arm: ... live from the witness's 0x051 alone -> as specified",
         T.judge_expired(A4_NO_TX, 0, 3) == "as specified"),
        ("expired arm: the A4 shape with no 0x051 and no tx_ok in mode 3 -> never live",
         T.judge_expired(A4_NO_TX, 0, 0) == "NOT as specified: never live"),
        ("the arm line prints the arm's aborts beside its verdict", arm_line_has_aborts()),
        ("bursts: VOID only with no witness error frame; each read with its source state, recorded and printed",
         bursts_per_ruling()),
        ("expired arm: no read live, no 0x051, tx_ok only on a read in mode 0 (the last arm's) -> never live",
         T.judge_expired(A4_NO_TX + [{"live": False, "abort": "", "mode": 0, "tx_ok": 40}], 0, 0)
         == "NOT as specified: never live"),
        ("expired arm: live (0x051) and no abort -> as specified",
         T.judge_expired([{"live": False, "abort": "", "mode": 3, "tx_ok": 0}], 0, 2) == "as specified"),
        ("expired arm: live then trip 6 is still NOT, whatever else aborted",
         T.judge_expired(A4_RUNC + [{"live": False, "abort": TRIP, "mode": 3}], 0, 6).startswith("NOT")),
        ("expired arm: a new error frame since arming still VOIDs a live-then-aborted arm",
         T.judge_expired(A4_RUNC, 2, 6).startswith("VOID")),
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
    # The slice's 0x617 around a re-arm (2026-11-20): host send times, run C's A3 -> A4 shape (A3's
    # last 0x617 at 61.828, A4 armed 62.630, its slice's first 0x617 at 62.833, then every 0.25 s)
    sent = [61.328, 61.578, 61.828] + [62.833 + 0.25 * k for k in range(4)]
    g = T.host_0x617_gaps(sent, 62.630, 63.7)
    checks += [
        ("0x617: the gap across the re-arm (61.828 -> 62.833, 1.005 s) is over 1.0 s",
         g["gaps_over_s"] == [[61.828, 1.005]] and g["last_before_arm_s"] == -0.802
         and g["first_after_arm_s"] == 0.203),
        ("0x617: without A3's last send the gap is from 61.578 (1.255 s)",
         T.host_0x617_gaps([t for t in sent if t != 61.828], 62.630, 63.7)["gaps_over_s"] == [[61.578, 1.255]]),
        ("0x617: a gap of exactly 1.0 s is not over it; 1.001 s is",
         T.host_0x617_gaps([60.0, 61.0, 62.001], 60.5, 63.0)["gaps_over_s"] == [[61.0, 1.001]]),
        ("0x617: gaps before the last send ahead of the arm, or after the arm's end, are not the arm's",
         T.host_0x617_gaps([50.0, 60.0, 61.0, 61.2, 70.0], 61.1, 61.5)["gaps_over_s"] == []),
        ("0x617: no send at all -> nothing before, nothing after, no gap",
         T.host_0x617_gaps([], 1.0, 2.0) == {"last_before_arm_s": None, "first_after_arm_s": None,
                                              "gaps_over_s": []}),
        ("0x617: the re-arm quiet is longer than 0x617 takes to go stale",
         T.REARM_QUIET_S > T.STALE_0x617_S),
    ]
    import inspect
    src = inspect.getsource(T.main)
    i = src.find("def poll_arm(")
    body = src[i:src.find("post(3)", i)]
    checks += [
        ("re-arm: poll_arm waits REARM_QUIET_S after the slice stopped, before it posts mode 3",
         'wait_until(pl["stopped"] + REARM_QUIET_S)' in body),
        ("re-arm: silence records when the slice stopped, after the Player has joined",
         src.find('pl["p"].join(15)') < src.find('pl["stopped"] = time.perf_counter()')
         and src.find('pl["stopped"] = time.perf_counter()') > 0),
        ("re-arm: the slice's Player times 0x617, and silence keeps them",
         "record_ids=(ID_0x617,)" in src and 'rec["host_0x617_sent_s"] += ' in src),
    ]
    checks.append(("Player: record_ids times only those ids, one stamp per send the bus took", player_times()))
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
