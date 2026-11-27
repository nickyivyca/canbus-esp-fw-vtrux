#!/usr/bin/env python3
"""Test of unacked_analysis.py (the probe's no-acknowledge modes, shared/twai_port T13, the
2026-11-27 addition; gen-inhibit tracker F5), on synthetic probe answers and witness copies built
from test_app/README.md's answer format: attempts as TS rises; the TS = 0 spans between them by
error state (TEC below 128 active, 128 or more passive), a span after a gap kept out of the
readings and counted; TS held at 1 across resends shown by the witness (more copies than rises)
and an unsure join; the join's offset and residuals; the abort's state from its TEC, the buffer
freed or not, the copies after it; no abort named by its reason; the hold's node return (void
when the node came up before the trace ended; held must go out, freed must not); the hold's own
resends kept out of the trace's copy count; copies split into one burst per trial.

    python3.13 -B test_unacked_analysis.py [--mutants]
--mutants applies each edit in MUTANTS to a copy in scratch/ and runs this test on it: each must fail a
check (test_unacked_analysis_mutants.py runs it so). Gen-inhibit tester, 2026-11-27.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("UNACKED_ANALYSIS_DIR") or HERE)
import unacked_analysis as U  # noqa: E402

fails = []
oks = 0
OFFSET_US = 1_000_000.0     # witness clock minus device clock
COPY_US = 240.0             # a copy is stamped this long after its TS rise
TS, TBS, TCS = 32, 4, 8


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (str(name), str(detail)[-300:]))


def make_trial(n, t=1, first_ns=20_000, tx_us=230, idle_active_us=6, idle_passive_us=22, hold_ts_after=None,
               gap_at=None, tec0=0):
    """n attempts with no ACK: TS rises, then falls tx_us later with the TEC up by 8 (to 128 at most);
    the next rise after the state's idle. hold_ts_after: from this attempt on, TS does not fall between
    resends (no change recorded). gap_at: the fall after this attempt comes after a gap. Returns the trial
    and the rises (ns)."""
    ev = [[0, 0, tec0, 0]]
    tec, now, rises = tec0, first_ns, []
    for k in range(1, n + 1):
        rises.append(now)
        held = hold_ts_after is not None and k > hold_ts_after
        if not held:
            ev.append([now, TS, tec, 0])
        tec = min(tec + 8, 128)
        fall = now + tx_us * 1000
        if hold_ts_after is None or k < hold_ts_after:
            ev.append([fall, 0, tec, 1 if gap_at == k else 0])
        now = fall + (idle_passive_us if tec >= 128 else idle_active_us) * 1000
    trial = {"t": t, "skip": "", "events": ev, "attempts": n, "end_ns": now, "gaps": 0, "max_gap_ns": 1000,
             "ev_lost": 0, "cleared": "cleanup", "clear_ns": -1, "abort_ns": -1}
    return trial, rises


def copies_for(rises, jitter=None, extra=()):
    c = [(r / 1000.0 + COPY_US + OFFSET_US + ((jitter or {}).get(k, 0.0)), "0000ab7e00000000")
         for k, r in enumerate(rises)]
    return c + [(x, "0000ab7e00000000") for x in extra]


def main():
    # 1. 30 attempts through both states
    tr, rises = make_trial(30)
    att, idl = U.ts_profile(tr)
    check("30 TS rises are 30 attempts", len(att) == 30, len(att))
    check("29 TS = 0 spans between them", len(idl) == 29, len(idl))
    act = [i for i in idl if i["state"] == "active"]
    pas = [i for i in idl if i["state"] == "passive"]
    check("15 active spans (TEC 8-120), then passive from TEC 128", len(act) == 15 and len(pas) == 14,
          (len(act), len(pas)))
    check("an active span is 6 us, a passive one 22 us",
          {i["ns"] for i in act} == {6000} and {i["ns"] for i in pas} == {22000},
          ({i["ns"] for i in act}, {i["ns"] for i in pas}))
    s = U.trial_summary(tr, copies_for(rises))
    check("the join: offset as built, every residual 0, sure",
          abs(s["witness"]["offset_us"] - (OFFSET_US + COPY_US)) < 1e-6 and s["witness"]["max_residual_us"] == 0
          and s["witness"]["sure"], s["witness"])
    check("as many copies in the trace as rises", s["witness_vs_rises"] == 0, s["witness_vs_rises"])
    t1 = U.m1_table([s])
    check("M1 table: active 6 us x15, passive 22 us x14",
          t1["active"]["idles_us"] == {"n": 15, "min": 6.0, "median": 6.0, "max": 6.0}
          and t1["passive"]["idles_us"] == {"n": 14, "min": 22.0, "median": 22.0, "max": 22.0}, t1)

    # 2. a gap: the span is a bound, kept out of the readings and counted
    tr, rises = make_trial(10, gap_at=3)
    s = U.trial_summary(tr, copies_for(rises))
    t1 = U.m1_table([s])
    check("a span after a gap is kept out of the readings", t1["active"]["idles_us"]["n"] == 8, t1["active"])
    check("and counted as a span with a gap", t1["active"]["idles_with_gap"] == 1, t1["active"])

    # 3. TS held at 1 across resends: only the witness shows it
    tr, rises = make_trial(20, hold_ts_after=5)
    s = U.trial_summary(tr, copies_for(rises))
    t1 = U.m1_table([s])
    check("TS held at 1 from attempt 5: 5 rises seen", len(s["attempts"]) == 5, len(s["attempts"]))
    check("the witness shows more copies than rises", t1["more_copies_than_rises"] == [1], t1)

    # 4. an unsure join
    tr, rises = make_trial(10)
    s = U.trial_summary(tr, copies_for(rises, jitter={7: 120.0}))
    check("a copy 120 us off the line makes the join unsure", not s["witness"]["sure"], s["witness"])
    check("one copy off the line does not move the offset (the median)",
          abs(s["witness"]["offset_us"] - (OFFSET_US + COPY_US)) < 1e-6, s["witness"])
    s2 = U.trial_summary(tr, copies_for(rises, jitter={7: 30.0}))
    check("a copy 30 us off keeps it sure", s2["witness"]["sure"], s2["witness"])
    check("TEC 127 is active, 128 passive", U.state_of(127) == "active" and U.state_of(128) == "passive",
          (U.state_of(127), U.state_of(128)))

    # 4b. the first attempt began inside twai_transmit(): the first sample reads TS = 1
    tr, rises = make_trial(8)
    tr["events"] = [[0, TS, 0, 0], [180_000, 0, 8, 0]] + [[e[0] + 0, e[1], e[2] + 8, e[3]] for e in tr["events"][1:]]
    first_rise_ns = -120_000                # its true rise, before the send returned: 120 us off the line
    s = U.trial_summary(tr, copies_for([first_rise_ns] + rises))
    check("a first sample at TS = 1 is an attempt: 9 in all", len(s["attempts"]) == 9
          and s["attempts"][0].get("first_sample"), [a.get("first_sample") for a in s["attempts"]])
    check("the span after it is read: 8 spans", len(s["idles"]) == 8, len(s["idles"]))
    check("the join stays sure: the first-sample pair is out of the offset and its mark",
          s["witness"]["sure"] and abs(s["witness"]["offset_us"] - (OFFSET_US + COPY_US)) < 1e-6
          and s["witness_vs_rises"] == 0, s["witness"])

    # 5. aborts
    tr, rises = make_trial(4)
    ab = rises[3] + 240_000           # in the idle after attempt 4
    tr.update(abort_ns=ab, ab_issued=True, ab_st_before=0, ab_st_after=TBS, ab_tec=32, ab_attempt=4,
              free_after_abort_ns=800, cleared="tbs", end_ns=ab + 500_000)
    s_free = U.trial_summary(tr, copies_for(rises))
    check("an abort at TEC 32 is error active", s_free["abort"]["state"] == "active", s_free["abort"])
    check("TS read 0 before it", s_free["abort"]["ts_before"] == 0, s_free["abort"])
    check("freed, no copy after it", s_free["abort"]["freed"] and s_free["abort"]["copies_after"] == 0,
          s_free["abort"])
    tr2, rises2 = make_trial(24)
    ab2 = rises2[19] + 100_000         # during attempt 20, TS = 1
    tr2.update(abort_ns=ab2, ab_issued=True, ab_st_before=TS, ab_st_after=TS, ab_tec=128, ab_attempt=20,
               free_after_abort_ns=-1, cleared="cleanup")
    s_held = U.trial_summary(tr2, copies_for(rises2))
    check("an abort at TEC 128 is error passive", s_held["abort"]["state"] == "passive", s_held["abort"])
    check("not freed; the later attempts are copies after it",
          not s_held["abort"]["freed"] and s_held["abort"]["copies_after"] == 4, s_held["abort"])
    check("by time too: 4 copies after the abort", s_held["abort"]["copies_after_by_time"] == 4, s_held["abort"])
    # an abort in the idle that did not take: the next attempt rises 6 us later and is counted, by order
    tr4, rises4 = make_trial(6, t=4)
    ab4 = rises4[2] + 230_000 + 2_000      # 2 us into the idle after attempt 3
    tr4.update(abort_ns=ab4, ab_issued=True, ab_st_before=0, ab_st_after=0, ab_tec=24, ab_attempt=3,
               free_after_abort_ns=-1, cleared="cleanup")
    s4 = U.trial_summary(tr4, copies_for(rises4))
    check("an abort in the idle that did not take: the next 3 attempts are later ones",
          s4["abort"]["copies_after"] == 3, s4["abort"])
    # a copy pushed across the abort by its residual: order and time differ, and the table lists it
    s5 = U.trial_summary(tr4 | {"t": 5}, copies_for(rises4, jitter={3: -40.0}))
    tab5 = U.abort_table([s5])
    check("order and time differ: listed", tab5["active"]["order_vs_time_differ"] == [5]
          and s5["abort"]["copies_after"] == 3, (tab5["active"], s5["abort"]))
    tab = U.abort_table([s_free, s_held])
    check("abort table: active 1 freed; passive 1 not freed, with copies after",
          tab["active"]["freed"] == 1 and tab["passive"]["not_freed"] == [1]
          and tab["passive"]["copies_after_abort"] == [1] and tab["active"]["copies_after_abort"] == [], tab)
    check("abort table: TS at the abort per state", tab["active"]["ts_at_abort"] == [0]
          and tab["passive"]["ts_at_abort"] == [1], tab)
    tr3, _ = make_trial(3, t=7)
    tr3.update(wait_timeout=True)
    s3 = U.trial_summary(tr3, [])
    check("no abort, named by its reason", U.abort_table([s3])["no_abort"] == [(7, "wait_timeout")],
          U.abort_table([s3]))

    # 6. the hold: the node's return
    tr, rises = make_trial(30)
    tr["cleared"] = "cleanup"
    s = U.trial_summary(tr, copies_for(rises))
    end_w = tr["end_ns"] / 1000.0 + s["witness"]["offset_us"]
    h = U.hold_check(s, tr, copies_for(rises), end_w + 5000, end_w + 50000, [end_w + 5200])
    check("held, received by the node: goes out, as expected",
          h["void"] is None and h["held"] and h["out"] and h["as_expected"], h)
    h = U.hold_check(s, tr, copies_for(rises), end_w + 5000, end_w + 50000, [])
    check("held, not received: not as expected (the positive control can fail)",
          h["void"] is None and h["held"] and h["as_expected"] is False, h)
    h = U.hold_check(s, tr, copies_for(rises), end_w - 100, end_w + 50000, [end_w + 200])
    check("the node up before the trace ended: void", h["void"] is not None, h)
    h = U.hold_check(s, tr, copies_for(rises), end_w + 5000, end_w + 50000, [end_w + 60000])
    check("a frame received after the node went down does not count", h["out"] is False, h)
    s_free_h = U.trial_summary(tr.copy() | {"cleared": "tbs"}, copies_for(rises))
    h = U.hold_check(s_free_h, tr | {"cleared": "tbs"}, copies_for(rises), end_w + 5000, end_w + 50000, [])
    check("freed, nothing received: as expected", h["held"] is False and h["as_expected"], h)
    h = U.hold_check(s_free_h, tr | {"cleared": "tbs"}, copies_for(rises), end_w + 5000, end_w + 50000,
                     [end_w + 5200])
    check("freed, yet received: not as expected", h["as_expected"] is False, h)
    rows = [{"t": 1, "hold": U.hold_check(s, tr, copies_for(rises), end_w + 5000, end_w + 50000, [end_w + 5200])},
            {"t": 2, "hold": U.hold_check(s, tr, copies_for(rises), end_w - 1, end_w + 50000, [])}]
    ht = U.hold_table(rows)
    check("hold table: one held as expected, one void",
          ht["held: must go out"] == {"trials": 1, "as_expected": 1, "not_as_expected": []}
          and [v[0] for v in ht["void"]] == [2], ht)

    # 6b. the device's own view of the hold (README step 9), recorded beside the node's
    th = tr | {"cleared": "hold", "alerts_hold": 0x00000002 | 0x00000200, "hold_tbs": 1, "hold_tcs": 1,
               "hold_ns": 300_000_000, "cleanup": 0}
    h = U.hold_check(s, th, copies_for(rises), end_w + 5000, end_w + 50000, [end_w + 5200])
    check("cleared 'hold' is a frame held at the hold's start: must go out",
          h["held"] and h["as_expected"], h)
    check("TX_SUCCESS in alerts_hold read, and agreeing with the node", h["device"]["tx_success"] is True
          and h["device_differs"] is False, h)
    h = U.hold_check(s, th | {"alerts_hold": 0x00000001 | 0x00000200}, copies_for(rises), end_w + 5000,
                     end_w + 50000, [end_w + 5200])
    check("no TX_SUCCESS though the node received it: the views differ", h["device"]["tx_success"] is False
          and h["device_differs"], h)
    ht = U.hold_table([{"t": 9, "hold": h}])
    check("hold table lists the trial whose views differ", ht["device_differs"] == [9], ht)

    # 7. the hold's resends are not the trace's
    s = U.trial_summary(tr, copies_for(rises, extra=[end_w + 1000 + 270 * k for k in range(6)]))
    check("six resends in the hold leave the trace's count at the rises", s["witness_vs_rises"] == 0
          and s["copies_in_trace"] == 30, (s["witness_vs_rises"], s["copies_in_trace"]))

    # 8. bursts
    b = U.group_copies([(0.0, ""), (270.0, ""), (540.0, ""), (30_540.0, ""), (30_810.0, "")])
    check("copies split into one burst per trial at a quiet gap", [len(x) for x in b] == [3, 2], b)

    # 9. a TEC change inside an idle: the latest holds
    tr = {"t": 1, "events": [[0, 0, 0, 0], [1000, TS, 0, 0], [5000, 0, 120, 0], [5500, 0, 128, 0],
                             [30000, TS, 128, 0]], "end_ns": 40000}
    _, idl = U.ts_profile(tr)
    check("the TEC that holds in the idle is the latest seen in it", idl and idl[0]["state"] == "passive", idl)

    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("the passive mark at 127", "PASSIVE_TEC = 128\n", "PASSIVE_TEC = 127\n"),
    ("a span after a gap read as clean", "\"gap\": fall[2] or gap})", "\"gap\": False})"),
    ("a first sample at TS = 1 not an attempt", "        if prev_ts is None and ts == 1:\n",
     "        if False:\n"),
    ("the first-sample pair in the join's mark", "max_residual_us=max(abs(res[k]) for k in rise))",
     "max_residual_us=max(abs(r) for r in res))"),
    ("the TEC in the idle not updated", "            fall = (fall[0], tec, fall[2] or gap)",
     "            fall = (fall[0], fall[1], fall[2] or gap)"),
    ("the join's offset the mean", "    off = statistics.median([d[k] for k in rise])\n",
     "    off = statistics.mean([d[k] for k in rise])\n"),
    ("the residual mark ignored", "    out[\"sure\"] = out[\"max_residual_us\"] <= RESIDUAL_US\n",
     "    out[\"sure\"] = True\n"),
    ("later attempts counted from the abort's attempt plus one", "    return sorted(copies)[attempt:]\n",
     "    return sorted(copies)[attempt + 1:]\n"),
    ("later attempts by time with a frame's allowance", "    return [c for c in copies if c[0] - offset_us > abort_ns / 1000.0]\n",
     "    return [c for c in copies if c[0] - offset_us > abort_ns / 1000.0 + 300.0]\n"),
    ("order and time never compared", "if s[\"abort\"][\"copies_after\"] != s[\"abort\"][\"copies_after_by_time\"]]",
     "if False]"),
    ("freed taken from tbs_tcs only", "\"freed\": trial.get(\"cleared\") in (\"tbs\", \"tbs_tcs\"),",
     "\"freed\": trial.get(\"cleared\") == \"tbs_tcs\","),
    ("the hold's resends counted in the trace", "        in_trace = [c for c in copies if c[0] - j[\"offset_us\"] <= end_ns / 1000.0 + RESIDUAL_US]\n",
     "        in_trace = copies\n"),
    ("the node's return not voided before the trace ended", "    if node_up_us <= trace_end_us:\n",
     "    if False:\n"),
    ("a frame after the node went down counted", "    rx = [t for t in node_rx_us if node_up_us <= t <= node_down_us]\n",
     "    rx = [t for t in node_rx_us if node_up_us <= t]\n"),
    ("held decided by tbs_tcs only", "    held = trial.get(\"cleared\") not in (\"tbs\", \"tbs_tcs\")\n",
     "    held = trial.get(\"cleared\") != \"tbs_tcs\"\n"),
    ("as expected whatever went out", "    r[\"as_expected\"] = r[\"out\"] == held\n", "    r[\"as_expected\"] = True\n"),
    ("TX_SUCCESS read from TX_IDLE", "ALERT_TX_SUCCESS = 0x00000002", "ALERT_TX_SUCCESS = 0x00000001"),
    ("the device's view never compared", "r[\"device\"][\"tx_success\"] != r[\"out\"]\n", "False\n"),
    ("cleared 'hold' read as freed", "    held = trial.get(\"cleared\") not in (\"tbs\", \"tbs_tcs\")\n",
     "    held = trial.get(\"cleared\") not in (\"tbs\", \"tbs_tcs\", \"hold\")\n"),
    ("bursts never split", "        if last is not None and c[0] - last > gap_us:\n", "        if False:\n"),
    ("the abort's TS read from TBS", "int(bool(trial[\"ab_st_before\"] & TS))", "int(bool(trial[\"ab_st_before\"] & TBS))"),
    ("no-abort reason dropped", "    out[\"no_abort\"] = [(s[\"t\"], s.get(\"no_abort_reason\") or s[\"skip\"]) for s in summaries if not s[\"abort\"]]\n",
     "    out[\"no_abort\"] = [(s[\"t\"], s[\"skip\"]) for s in summaries if not s[\"abort\"]]\n"),
]


def mutants():
    import gi_paths
    src = open(os.path.join(HERE, "unacked_analysis.py"), encoding="utf-8", newline="").read()
    if subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True).returncode != 0:
        print("ERROR    control: the unmutated tool fails this test")
        return 1
    print("PASS     control: the unmutated tool passes this test")
    base = gi_paths.scratch("mutants-unacked_analysis")
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "unacked_analysis.py"), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, UNACKED_ANALYSIS_DIR=d, PYTHONDONTWRITEBYTECODE="1"))
            failed = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
            dead = bool(failed) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "  (%s)" % failed[0][:150] if failed else ""))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
