"""Harness test for e4_device_calibration.vcm_commands_on_wire (the stimulus check), and
attribution_check (2026-10-29, gen-inhibit reviewer): the old trail-window count of our
frames voids no arm in INHIBIT, where provenance decides. Since spec 12.1 (c5849685,
2026-11-19) the RESPOND probe count over the whole arm voids nothing either: attribution
is counted over the same window from the arm, in attach_provenance, which voids the arm
on a disagreement.

    py -3.14 gen-inhibit/tools/bench/test_e4_stimulus_split.py
Mutants: test_e4_attribution_mutants.py (E4_DIR runs another copy of the tool).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("E4_DIR"):
    sys.path.insert(0, os.environ["E4_DIR"])
import e4_device_calibration as E                               # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)


def poll_rate_ok():
    """Run a Hammer at 10 Hz for 0.55 s against a fake gi(): ~6 polls, each recorded.
    The flood (period 0) against the same fake must make far more."""
    import time
    calls = []

    def fake_gi(path, method="GET", timeout=6.0):
        calls.append(path)
        return 200, '{"drv":{"msgs_to_rx":%d,"rx_missed":0}}' % len(calls)
    real = E.gi
    E.gi = fake_gi
    try:
        h = E.Hammer(period=0.1)
        h.start()
        time.sleep(0.55)
        h.stop.set()
        h.join(2)
        light = (h.n, len(h.series), h.series[-1][1] if h.series else None)
        calls.clear()
        f = E.Hammer(period=0.0)
        f.start()
        time.sleep(0.05)
        f.stop.set()
        f.join(2)
        flood = f.n
    finally:
        E.gi = real
    return 5 <= light[0] <= 7 and light[1] == light[0] and light[2] == light[0] and flood > 50


def hammer_row_fields():
    """F4 (2026-11-27): one Hammer poll against a fake page records [perf time, msgs_to_rx,
    rx_missed, mode, self_off, ctr_bad], the time being the request's send."""
    import time
    seen = []

    def fake_gi(path, method="GET", timeout=6.0):
        seen.append(time.perf_counter())
        time.sleep(0.02)          # the answer comes 20 ms after the send
        return 200, '{"mode":2,"self_off":null,"ctr_bad":3,"drv":{"msgs_to_rx":4,"rx_missed":17}}'
    real = E.gi
    E.gi = fake_gi
    try:
        h = E.Hammer(period=0.5)
        t_before = time.perf_counter()
        h.start()
        time.sleep(0.2)
        h.stop.set()
        h.join(2)
    finally:
        E.gi = real
    if not h.series or not seen:
        return False
    row = h.series[0]
    return (len(row) == 6 and row[1:] == [4, 17, 2, None, 3]
            and t_before <= row[0] <= seen[0] + 1e-3)


def run_arm_records_perf_base():
    """The arm record carries the replay's base on the perf clock, the same base the POSTs are
    timed from (F4, 2026-11-27). Read from the source: run_arm needs the bench."""
    import inspect
    src = inspect.getsource(E.run_arm)
    return ('"perf_base": round(base, 4),' in src and "    base = perf()\n" in src
            and "poster.base = base\n" in src)


def attach_failure_recorded():
    """A run record whose capture cannot be loaded: the arm must say NOT RUN, why, and
    still carry the superseded note. Writes a scratch record in runs/ and removes it."""
    import json
    path = gi_paths.runs("_test_attach_provenance_tmp.json")
    out = {"capture": "no_such_capture.log", "window_start": 0.0, "seconds": 1.0,
           "mode": "inhibit", "arms": {"idle": {"device": {"tx_ok": 1}}}}
    try:
        with open(path, "w") as fh:
            json.dump(out, fh)
        E.attach_provenance(path, out)
        rec = json.load(open(path))["arms"]["idle"]
        return (rec["provenance"]["verdict"] == "NOT RUN" and rec["provenance"].get("error")
                and "superseded" not in rec["provenance"] and "old trail-window" in
                rec["superseded_classifier"])
    finally:
        if os.path.exists(path):
            os.remove(path)


def attribution_case(mode, ours, tx_ok, want_reason, want_in_line):
    line, reason = E.attribution_check(mode, ours, tx_ok, "zero-torque 0x051")
    return reason == want_reason and want_in_line in line


def run_arm_uses_check():
    """The arm's code calls attribution_check and prints its line, and voids nothing on it
    (spec 12.1, c5849685: the whole-arm count is reported only). Read from the source:
    run_arm needs the bench."""
    import inspect
    src = inspect.getsource(E.run_arm)
    i = src.find("attribution_check(")
    return i > 0 and "print(line)" in src[i:i + 300] and "void_reasons" not in src[i:i + 300]


def attach_attribution(att):
    """attach_provenance with rejudge giving `att` as its same-window attribution: (the arm
    as written to the record, what it printed). A scratch record, removed after."""
    import contextlib
    import io
    import json
    import provenance_classifier as P
    path = os.path.join(gi_paths.scratch("e4-attach-attribution"), "rec.json")
    out = {"arms": {"idle": {"device": {"tx_ok": 5}, "void": False, "void_reasons": []}}}
    real = P.rejudge
    P.rejudge = lambda p, name: {"verdict": "as specified", "attribution": att}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            E.attach_provenance(path, out)
        return json.load(open(path))["arms"]["idle"], buf.getvalue()
    finally:
        P.rejudge = real
        gi_paths.remove_tree(os.path.dirname(path))


def attach_voids_on_disagreement():
    import arm_void as AV
    att = {"from": "scheduled POST 1 (at 20.0 s, mode 3)", "index": 7, "start_s": 1.0, "mode": "inhibit",
           "counted_as": "device 0x051 (provenance)", "witness": 4, "device_tx_ok": 5, "agree": False}
    arm, printed = attach_attribution(att)
    return (arm["void"] is True and len(arm["void_reasons"]) == 1
            and arm["void_reasons"][0] == AV.attribution_reason(att) and arm["attribution"] == att
            and "---- ARM VOID:" in printed and "       - " + AV.ATTRIBUTION in printed and "DISAGREE" in printed)


def attach_agreement_voids_nothing():
    base = {"from": "the start of the replay", "index": None, "start_s": None, "device_tx_ok": 5}
    ok = []
    for att in (dict(base, mode="respond", counted_as="probe 0x7F0", witness=5, agree=True),
                dict(base, mode="passive", counted_as=None, witness=None, agree=None), None):
        arm, printed = attach_attribution(att)
        ok.append(arm["void"] is False and arm["void_reasons"] == [] and "ARM VOID" not in printed)
    return all(ok)


def main():
    f = E.vcm_commands_on_wire
    checks = [
        ("INHIBIT: 3670 on the wire, 670 ours -> 3000 VCM", f(E.MODE_INHIBIT, 3670, 670) == 3000),
        ("RESPOND: tx_ok counts probes, so all 2999 are the VCM's (the 2026-10-10 arm)",
         f(E.MODE_RESPOND, 2999, 2993) == 2999),
        ("INHIBIT with tx_ok missing -> nothing subtracted", f(E.MODE_INHIBIT, 3000, None) == 3000),
        ("PASSIVE: the device sends no 0x051, so nothing is subtracted whatever tx_ok reads",
         f(E.MODE_PASSIVE, 3000, 12) == 3000),
        ("a real short in RESPOND is still seen: 2990 of 2999",
         2999 - f(E.MODE_RESPOND, 2990, 2990) == 9),
        ("setter path carries the offset given",
         E.set_mode_path(2, 100) == "/gen_inhibit_set?mode=2&offset=100"
         and E.set_mode_path(0, 1000) == "/gen_inhibit_set?mode=0&offset=1000"),
        ("no hard-coded offset left in the setter calls (the sweep would not vary it)",
         "offset=500" not in open(E.__file__, encoding="utf-8").read()),
        ("page_queue_fields reads drv.msgs_to_rx and rx_missed",
         E.page_queue_fields('{"mode":2,"drv":{"msgs_to_rx":7,"rx_missed":16}}') == [7, 16]),
        ("page_state_fields reads mode and self_off (the quiesce reason)",
         E.page_state_fields('{"mode":1,"self_off":"forced quiesce -- x","drv":null}')
         == [1, "forced quiesce -- x"] and E.page_state_fields("{cut") == [None, None]),
        ("page_queue_fields: drv null or a cut page -> [None, None], no exception",
         E.page_queue_fields('{"drv":null}') == [None, None]
         and E.page_queue_fields('{"drv":{"msgs_to') == [None, None]),
        ("a 1 Hz Hammer polls about once a second and records each answer", poll_rate_ok()),
        # F4 (2026-11-27): rx_missed and ctr_bad over time from a POST
        ("page_counter_fields reads ctr_bad; a page without it, or a cut page -> [None], no exception",
         E.page_counter_fields('{"ctr_ok":9,"ctr_bad":2}') == [2]
         and E.page_counter_fields('{"ctr_ok":9}') == [None] and E.page_counter_fields('{"ctr_b') == [None]),
        ("a Hammer row is [send time, msgs_to_rx, rx_missed, mode, self_off, ctr_bad]", hammer_row_fields()),
        ("the arm record carries perf_base, the replay's base on the perf clock", run_arm_records_perf_base()),
        ("provenance that cannot run is recorded NOT RUN with the reason, old figures marked "
         "superseded", attach_failure_recorded()),
        # the old trail-window count voids nothing (gen-inhibit reviewer, 2026-10-29)
        ("INHIBIT: the trail count 8982 against tx_ok 8983 (step 1a's B22 arms) voids nothing, "
         "and is printed as superseded", attribution_case(E.MODE_INHIBIT, 8982, 8983, None, "superseded")),
        ("INHIBIT: a count that agrees prints the wire agreeing and voids nothing",
         attribution_case(E.MODE_INHIBIT, 8983, 8983, None, "wire agrees")),
        ("PASSIVE: the trail count voids nothing either",
         attribution_case(E.MODE_PASSIVE, 0, 5, None, "superseded")),
        # spec 12.1 (c5849685): the whole-arm count voids nothing in any mode; attribution is counted
        # over the same window from the arm, in attach_provenance
        ("RESPOND: the whole-arm probe count 2990 against 2999 is reported and voids nothing",
         attribution_case(E.MODE_RESPOND, 2990, 2999, None, "whole-arm count (voids nothing")),
        ("RESPOND: a probe count that agrees voids nothing", attribution_case(E.MODE_RESPOND, 2999, 2999, None,
                                                                             "wire agrees")),
        ("run_arm prints attribution_check's line and voids nothing on it", run_arm_uses_check()),
        ("attach_provenance: the counts disagreeing over the same window from the arm void the arm, with the "
         "reason in the record and its own ARM VOID block", attach_voids_on_disagreement()),
        ("attach_provenance: counts that agree, nothing to compare (PASSIVE) or no count void nothing",
         attach_agreement_voids_nothing()),
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
