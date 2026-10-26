"""Harness test for x2_jam_cases.py: max_in_window, judge_threshold,
state_transitions, control_ok,
pcan_bus_off_time, judge_b_busoff, live_totals, StoppableSender (on the real LF.play with a fake bus).

Synthetic, no hardware. Every verdict of judge_threshold has a case that must
come out the other way, including the one the reviewer cares most about: trip 6
latching before the 10th delivered error frame.

Run: py -3.14 tools/gen_inhibit/bench/test_x2_jam_cases.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import x2_jam_cases as C                                        # noqa: E402

T6 = "error-frame rate exceeded"
PH = {"arm_posted": 100.0, "pulses_start": 110.0, "pulses_end": 120.0,
      "mode0_posted": 135.0}


def errs(n, interval, start=110.001, per=None):
    out = []
    for k in range(n):
        for j in range((per or [1] * n)[k]):
            out.append(start + k * interval + j * 0.0003)
    return out


def sn(live=True, latched=False, reason="", tx=100, be=0, mode=3):
    return {"mode": mode, "inhibit_live": live and not latched, "abort_latched": latched,
            "abort_reason": reason, "tx_ok": tx, "drv_bus_error": be}


def req_at(ss, lo, hi, req):
    """ss with req_start set to req on the readings stamped in [lo, hi)."""
    return [(t, dict(x, req_start=req) if lo <= t < hi else x) for t, x in ss]


def samples(n, interval, latch_at=None, reason=T6, start=110.0, reset_at=None,
            live_before=True, prelatched=False):
    """Page readings every 0.1 s from 100.5 to 135; bus_error follows the pulses."""
    out, t, tx = [], 100.5, 100
    while t < 135.0:
        k = sum(1 for i in range(n) if start + 0.001 + i * interval < t)
        latched = prelatched or (latch_at is not None and t >= latch_at)
        tx += 10
        if reset_at is not None and t >= reset_at:
            out.append((round(t, 3), sn(live=False, tx=0, mode=0)))
        else:
            x = sn(live=live_before or t > start, latched=latched,
                   reason=reason if latched else "", tx=tx, be=k)
            if prelatched:
                x["inhibit_live"] = True   # reach the latched branch, not "not live"
            out.append((round(t, 3), x))
        t += 0.1
    return out


CASES = [
    ("10 at 1 s, trip 6 after the 10th", errs(10, 1.0), samples(10, 1.0, latch_at=119.15),
     10, 1.0, "PASS"),
    ("10 at 1 s, no trip", errs(10, 1.0), samples(10, 1.0), 10, 1.0, "FAIL"),
    ("10 at 1 s, trip 6 before the 10th (device over-counts)", errs(10, 1.0),
     samples(10, 1.0, latch_at=117.5), 10, 1.0, "FAIL"),
    ("9 at 1.1 s, no trip", errs(9, 1.1), samples(9, 1.1), 9, 1.1, "PASS"),
    ("9 at 1.1 s, trip 6 anyway", errs(9, 1.1), samples(9, 1.1, latch_at=119.5), 9, 1.1,
     "FAIL"),
    ("another trip latched", errs(10, 1.0),
     samples(10, 1.0, latch_at=115.0, reason="3 inhibit frames skipped within 1 s"),
     10, 1.0, "FINDING"),
    ("one pulse gave two error frames", errs(10, 1.0, per=[1] * 4 + [2] + [1] * 5),
     samples(10, 1.0, latch_at=119.15), 10, 1.0, "INCONCLUSIVE"),
    ("one pulse not delivered", errs(9, 1.0), samples(9, 1.0), 10, 1.0, "INCONCLUSIVE"),
    ("device reset while armed", errs(10, 1.0), samples(10, 1.0, reset_at=113.0), 10, 1.0,
     "INCONCLUSIVE"),
    ("not live before the pulses", errs(10, 1.0), samples(10, 1.0, live_before=False),
     10, 1.0, "INCONCLUSIVE"),
    ("abort already latched before the pulses", errs(10, 1.0),
     samples(10, 1.0, prelatched=True), 10, 1.0, "INCONCLUSIVE"),
    ("stray error frame before the pulses", [105.0] + errs(10, 1.0),
     samples(10, 1.0, latch_at=119.15), 10, 1.0, "INCONCLUSIVE"),
    # The latch bound (reviewer, 2026-10-20; spec 7: recorded within one worker pass,
    # at most 0.2 s): a page read whose request went out more than 0.2 s + 0.02 s after
    # the 10th that still shows no latch is a FAIL; one sent before then is not.
    ("10 at 1 s, trip 6 late: pages 0.3-0.4 s after the 10th unlatched", errs(10, 1.0),
     samples(10, 1.0, latch_at=119.45), 10, 1.0, "FAIL"),
    ("10 at 1 s, an unlatched page answered late but requested in time", errs(10, 1.0),
     req_at(samples(10, 1.0, latch_at=119.35), 119.221, 119.35, 119.2), 10, 1.0, "PASS"),
]

# Tests of the max_in_window helper's own edge (reviewer, 2026-10-20), not bus judgements:
# spec 7 lets a bus span within 0.25 s of 10 s trip or not at the 10th, and threshold
# arguments that put the judged pulses there are refused (band_refusal).
WINDOW = [
    ("10 spanning 9.0 s", [k * 1.0 for k in range(10)], 10),
    ("10 spanning 9.9 s", [k * 1.1 for k in range(10)], 10),
    ("10 spanning exactly 10.0 s (inclusive, spec 7 ruling: 10)",
     [k * (10.0 / 9) for k in range(10)], 10),
    ("10 spanning 10.01 s", [k * (10.01 / 9) for k in range(10)], 9),
    ("10 spanning 10.35 s", [k * 1.15 for k in range(10)], 9),
    ("9 only", [k * 0.5 for k in range(9)], 9),
]


def poller_stamp_cases():
    """timed_page() must stamp at the response, after a slow page has answered."""
    out = []
    real = C.LF.page
    import time as _t

    def slow_page():
        _t.sleep(0.08)
        return {"mode": 3}
    try:
        C.LF.page = slow_page
        t_call = _t.time()
        t, sn = C.timed_page()
        out.append(("poller stamps after the response (80 ms page)",
                    t - t_call >= 0.075 and sn is not None and sn["req_start"] <= t_call + 0.01,
                    "stamp %.3f s after the call, req_start %.3f s" % (t - t_call,
                                                                   sn["req_start"] - t_call)))
        C.LF.page = lambda: None
        t, sn = C.timed_page()
        out.append(("an unanswered page is (time, None)", sn is None, repr(sn)))
    finally:
        C.LF.page = real
    return out


class FakeBus:
    """Records send times; stands in for the PCAN under LF.play."""

    def __init__(self):
        self.sent = []

    def send(self, msg, timeout=None):
        import time as _t
        self.sent.append(_t.perf_counter())


def replay_stop_check(stop):
    """Run the real LF.play through StoppableSender on a fake bus.

    Returns (sends before the stop, sends from 50 ms after it to 650 ms).
    stop False = never stopped (the negative control).
    """
    import threading as _th
    import time as _t
    stim = [(k * 0.01, 0x051, bytes(8)) for k in range(10)]
    fb = FakeBus()
    tx = C.StoppableSender(fb)
    th = _th.Thread(target=C.LF.play, args=(tx, stim, 1.5), daemon=True)
    th.start()
    _t.sleep(0.4)
    if stop:
        tx.stop()
    t_stop = _t.perf_counter()
    _t.sleep(0.05)
    n0 = len(fb.sent)
    _t.sleep(0.6)
    return n0, sum(1 for x in fb.sent if x > t_stop + 0.05)


def bsn(lat=True, live=False, st="bus_off", mode=3):
    return {"mode": mode, "abort_latched": lat, "inhibit_live": live, "drv_state": st}


# 0x7F1 on HEAD's schema (byte 0; gi_diag refuses a schema no DBC in git states, and
# these read schema 0 until 2026-10-24, when byte 3 was read by hand).
_S = "%02x" % C.gi_diag.schemas_for().head_version
F_OK = _S + "00009500000000"      # diag_flags = 0x95: bit7 set, bit5 clear
F_LIVE = _S + "0000b500000000"    # bit5 set
F_NOLAT = _S + "00001500000000"   # bit7 clear
LATCH_SAMPLES = [(10.0, bsn(st="running")), (10.2, bsn()), (10.4, bsn()), (25.0, bsn(mode=0))]
B_CASES = [
    ("bus-off, latched, no 0x051 after (the case B shape)", LATCH_SAMPLES, [], [9.9], "PASS"),
    ("a device 0x051 after the latch", LATCH_SAMPLES, [], [9.9, 10.3], "FAIL"),
    ("a page reading live again", LATCH_SAMPLES[:2] + [(10.4, bsn(live=True))], [], [], "FAIL"),
    ("0x7F1 after the latch with bit5 set", LATCH_SAMPLES, [(10.1, 0x7F1, F_LIVE)], [], "FAIL"),
    ("0x7F1 after the latch with bit7 clear", LATCH_SAMPLES, [(10.1, 0x7F1, F_NOLAT)], [], "FAIL"),
    ("0x7F1 after the latch, flags right", LATCH_SAMPLES, [(10.1, 0x7F1, F_OK)], [], "PASS"),
    ("no 0x7F1 and never bus-off", [(10.0, bsn(st="running")), (10.4, bsn(st="running"))], [], [],
     "FAIL"),
]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def reset_case(pages, cc=None, post_raises=False):
    """Run C.reset_to_off against a scripted page sequence (the last repeats)."""
    clk = FakeClock()
    seq = list(pages)
    posts = []

    def page():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def post(path):
        posts.append(path)
        if post_raises:
            raise OSError("connection reset")
        return True
    try:
        out = C.reset_to_off(cc, page, post, clk.now, clk.sleep, timeout_s=10.0)
    except Exception as e:          # a crash is reported as a failed check, never a pass
        out = {"ok": "CRASHED", "why": "%s: %s" % (type(e).__name__, e), "how": "", "posted": None}
    return out, posts


class FakeCC:
    def __init__(self):
        self.ser = object()
        self.pulses = 0

    def pulse_rts(self, s):
        self.pulses += 1
        return True


def reset_cases():
    """(label, ok, detail): reset_to_off proves a reset or refuses (2026-10-16)."""
    pg = lambda up, mode=0, lat=False: {"uptime_ms": up, "mode": mode, "abort_latched": lat,
                                        "abort_reason": "error-frame rate exceeded" if lat else ""}
    out = []
    r, posts = reset_case([pg(5000000, 3, True), pg(5001000, 3, True), pg(900)])
    out.append(("no COM4: POSTs /system_reboot, ok on a lower uptime, mode 0, unlatched",
                r["ok"] and posts == ["/system_reboot"] and r["how"].startswith("POST"), r["why"]))
    r, _ = reset_case([pg(5000000), pg(5001000)])
    out.append(("no reset seen (uptime never drops), even unlatched: not ok", r["ok"] is False, r["why"]))
    r, _ = reset_case([pg(5000000, 0, True), pg(800, 0, True)])
    out.append(("reset but still latched: not ok", r["ok"] is False, r["why"]))
    r, _ = reset_case([None, pg(800)])
    out.append(("no page before: a reset cannot be shown, not ok", r["ok"] is False, r["why"]))
    r, _ = reset_case([pg(5000000), pg(800, 3)])
    out.append(("comes back in another mode: not ok", r["ok"] is False, r["why"]))
    r, _ = reset_case([pg(5000000, 0, True), pg(800)], post_raises=True)
    out.append(("the reboot POST's reply dropping is not a failure if the reset shows",
                r["ok"] is True and "OSError" in str(r["posted"]), r["why"]))
    cc = FakeCC()
    r, posts = reset_case([pg(5000000, 0, True), pg(800)], cc=cc)
    out.append(("COM4 open: USB-JTAG pulse, no POST", r["ok"] and cc.pulses == 1 and posts == [],
                r["how"]))
    # case_threshold must not arm when the reset was not shown.
    saved = (C.reset_wican_to_off, C.arm_live)
    called = []
    try:
        C.reset_wican_to_off = lambda: {"ok": False, "why": "scripted", "after": None}
        def armed(*a, **k):
            called.append(1)
            raise RuntimeError("armed although the reset was not shown")
        C.arm_live = armed
        args = C.build_parser().parse_args(["threshold", "--power", "x", "--arms", "2",
                                            "--reset-wican"])
        rec = {}
        try:
            C.case_threshold(args, rec, None, None)
            err = None
        except RuntimeError as e:
            err = str(e)
        out.append(("threshold: an unshown reset does not arm, arms INCONCLUSIVE",
                    err is None and called == [] and rec.get("verdict") == "INCONCLUSIVE"
                    and [x["judge"]["verdict"] for x in rec["arms"]] == ["INCONCLUSIVE"] * 2,
                    err or rec.get("verdict")))
    finally:
        C.reset_wican_to_off, C.arm_live = saved
    return out



def witness_state_cases():
    """(label, ok, detail): the witness's own controller state (2026-10-16, item 4)."""
    out = []
    out.append(("flags: 0x8 is error_active only", C.kvaser_flags(0x8) == ["error_active"], C.kvaser_flags(0x8)))
    out.append(("flags: 0x3 is bus_off and error_passive", C.kvaser_flags(0x3) == ["bus_off", "error_passive"],
                C.kvaser_flags(0x3)))
    out.append(("flags: an unknown bit is kept as hex", "0x10000" in C.kvaser_flags(0x10008), C.kvaser_flags(0x10008)))
    st = [(10.0, 0, 0, 0, 0x8, 0, 0), (10.1, 120, 128, 0, 0x5, 40, 0), (10.2, 255, 128, 0, 0x2, 900, 1),
          (10.3, 255, 128, 0, 0x2, 900, 1), (10.4, 0, 0, 0, 0x8, 900, 1)]
    w = C.witness_state_summary(st, 10.05, 10.35)
    out.append(("summary: bus_off seen, from the first bus_off sample",
                w["bus_off"] and w["bus_off_from"] == 10.2 and w["bus_off_last"] == 10.3, w))
    out.append(("summary: error_passive and the maxima", w["error_passive"] and w["tec_max"] == 255
                and w["rec_max"] == 128 and w["err_frames_stat_delta"] == 860, w))
    w2 = C.witness_state_summary(st, 10.35, 10.5)
    out.append(("summary: a clean span is not bus_off", w2 and not w2["bus_off"] and not w2["error_passive"], w2))
    out.append(("summary: an unsampled span is None, not healthy", C.witness_state_summary(st, 11, 12) is None, ""))
    return out


def main():
    bad = []
    for label, ok, detail in poller_stamp_cases():
        print("  %-4s poller   %-58s %s" % ("ok" if ok else "FAIL", label, detail))
        if not ok:
            bad.append(label)
    for label, ts, want in WINDOW:
        got = C.max_in_window(ts, 10.0)[0]
        ok = got == want
        print("  %-4s helper   max_in_window: %-43s %s" % ("ok" if ok else "FAIL", label[:43], got))
        if not ok:
            bad.append(label)
    for label, e, s, n, iv, want in CASES:
        got = C.judge_threshold(e, s, PH, n, iv)
        ok = got["verdict"] == want
        if label.startswith("abort already latched"):
            ok = ok and "already latched" in got.get("why", "")
        print("  %-4s judge    %-58s %s %s" % ("ok" if ok else "FAIL", label[:58],
                                              got["verdict"], got.get("why", "")[:40]))
        if not ok:
            bad.append(label)
    # the PASS case must also report bus_error +1 per pulse
    got = C.judge_threshold(errs(10, 1.0), samples(10, 1.0, latch_at=119.15), PH, 10, 1.0)
    ok = got["per_pulse"]["bus_error_delta_per_pulse"] == [1] * 10
    print("  %-4s judge    %-58s %s" % ("ok" if ok else "FAIL", "bus_error delta per pulse",
                                       got["per_pulse"]["bus_error_delta_per_pulse"]))
    if not ok:
        bad.append("bus_error deltas")
    st = [(0.0, {"drv_present": True, "drv_state": "running", "bus_on": True}),
          (0.1, {"drv_present": True, "drv_state": "running", "bus_on": True}),
          (0.5, {"drv_present": True, "drv_state": "bus_off", "bus_on": False}),
          (0.6, None),
          (4.0, {"drv_present": True, "drv_state": "recovering", "bus_on": False}),
          (5.0, {"drv_present": False, "drv_state": None, "bus_on": False})]
    got = C.state_transitions(st, 0.0)
    want = [(0.0, "running", True), (0.5, "bus_off", False), (4.0, "recovering", False),
            (5.0, "drv_null", False)]
    ok = got == want
    print("  %-4s trans    %-58s %s" % ("ok" if ok else "FAIL", "state transitions", got))
    if not ok:
        bad.append("transitions")
    for label, ch, want in [
            ("pcan: ok, then bus-heavy, then bus-off", [(1.0, 0, "ok"), (2.0, 0x8, None),
                                                       (3.0, 0x10, None)], 3.0),
            ("pcan: bus-heavy only", [(1.0, 0, "ok"), (2.0, 0x8, None)], None),
            ("pcan: status unreadable", [(1.0, "status read failed: X", None)], None)]:
        got = C.pcan_bus_off_time(ch)
        ok = got == want
        print("  %-4s pcan     %-58s %s" % ("ok" if ok else "FAIL", label, got))
        if not ok:
            bad.append(label)
    for label, jb, want in [
            ("control: running only", {"page_mismatch_count": 0,
                                       "page_states": {"running": 50}}, True),
            ("control: a bus_off reading", {"page_mismatch_count": 0,
                                            "page_states": {"running": 40, "bus_off": 3}},
             False),
            ("control: a mismatch", {"page_mismatch_count": 1,
                                     "page_states": {"running": 50}}, False)]:
        ok = C.control_ok(jb) is want
        print("  %-4s control  %-58s" % ("ok" if ok else "FAIL", label))
        if not ok:
            bad.append(label)
    n0, late = replay_stop_check(True)
    ok = n0 > 10 and late == 0
    print("  %-4s replay   %-58s before %d, after %d" % ("ok" if ok else "FAIL",
                                                       "stopped replay sends nothing", n0, late))
    if not ok:
        bad.append("replay stop")
    n0, late = replay_stop_check(False)
    ok = late > 10
    print("  %-4s replay   %-58s after %d" % ("ok" if ok else "FAIL",
                                             "control: an unstopped replay keeps sending", late))
    if not ok:
        bad.append("replay control")
    for label, smp, wire, dev, want in B_CASES:
        got = C.judge_b_busoff(smp, wire, dev, 10.0)
        ok = got["verdict"] == want
        if label.startswith("bus-off, latched"):
            ok = ok and got["wire_half"].startswith("not observable: controller bus-off from +0.200")
        print("  %-4s B        %-58s %s %s" % ("ok" if ok else "FAIL", label[:58], got["verdict"],
                                              got["wire_half"][:40]))
        if not ok:
            bad.append(label)
    lv = {"inhibit_live": True}
    nl = {"inhibit_live": False}
    arms = [{"samples": [(1.0, nl), (2.0, lv), (62.0, lv), (63.0, nl)]},
            {"samples": [(1.0, nl), (2.0, None), (3.0, nl)]},
            {"samples": [(5.0, lv), (9.5, lv), (10.0, None)]}]
    got = C.live_totals(arms)
    ok = got == {"arms": 3, "arms_live": 2, "live_s": 64.5}
    print("  %-4s totals   %-58s %s" % ("ok" if ok else "FAIL", "live arms and live seconds", got))
    if not ok:
        bad.append("live totals")
    # drv_summary (arm (i), 2026-10-12)
    def ds(t, live=True, reason="", tec=0, rec=0, txf=0, present=True, st="running"):
        return (t, {"inhibit_live": live, "abort_reason": reason, "drv_present": present,
                    "drv_tec": tec, "drv_rec": rec, "drv_tx_failed": txf, "drv_state": st})
    smp = [ds(1.0, txf=2), ds(1.9, txf=3), ds(2.1, live=False, reason=T6, tec=40, rec=90, txf=3),
           ds(2.5, live=False, reason=T6, tec=136, rec=127, txf=5, st="running"),
           ds(3.0, live=False, reason="bus lost", tec=0, txf=0, present=False, st=None),
           ds(4.0, live=False, reason="bus lost", txf=7, tec=8)]
    got = C.drv_summary(smp, 2.0)
    want = {"tx_failed_before": 3, "tx_failed_after": 7, "max_tec": 136, "max_rec": 127,
            "states": ["None", "running"], "abort_reasons_in_order": [T6, "bus lost"]}
    ok = got == want
    print("  %-4s drv      %-58s %s" % ("ok" if ok else "FAIL",
                                       "tx_failed before/after, worst TEC/REC, reasons in order", got))
    if not ok:
        bad.append("drv_summary")
    got = C.drv_summary([ds(1.0, present=False, st=None)], 2.0)
    ok = got["tx_failed_before"] is None and got["tx_failed_after"] is None and got["max_tec"] is None
    print("  %-4s drv      %-58s %s" % ("ok" if ok else "FAIL", "no driver ever: None, never 0", got))
    if not ok:
        bad.append("drv_summary none")
    # --power is required and recorded as given (2026-10-12)
    try:
        C.build_parser().parse_args(["livetrain"])
        ok = False
    except SystemExit:
        ok = True
    ok = ok and C.build_parser().parse_args(["livetrain", "--power", "12 V"]).power == "12 V"
    print("  %-4s args     %-58s" % ("ok" if ok else "FAIL", "--power required, kept as given"))
    if not ok:
        bad.append("power arg")
    for label, ok, detail in witness_state_cases():
        print("  %-4s witness  %-58s %s" % ("ok" if ok else "FAIL", label[:58], str(detail)[:60]))
        if not ok:
            bad.append(label)
    for label, ok, detail in reset_cases():
        print("  %-4s reset    %-58s %s" % ("ok" if ok else "FAIL", label[:58], detail))
        if not ok:
            bad.append(label)
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
