#!/usr/bin/env python3
"""Test of unacked_judge.py (F5 on the bench, 2026-11-27), with no bench: the plan meets
the gen-inhibit reviewer's minimums (M1 >= 5 with irq off and on, the control >= 5, M2 >= 50 per
error state, M3 >= 20 per delay and state), aims each state by its attempt, puts a hold on every
call that brings the node back and none on M1, and is shuffled by its seed; the call's arguments
and the echo check; the witness's trial copies; one call processed into its summary and hold check,
voided when the node was up after the hold ended, an error for an answer that is not one trial; the
positive control failing on a control trial that did not go out, or on none; an aim that missed its
state listed.

    python3.13 -B test_unacked_judge.py [--mutants]
--mutants applies each edit in MUTANTS to a copy in scratch/ (with unacked_analysis.py beside it)
and runs this test on it: each must fail a check (mutants.py runs them). Gen-inhibit tester,
2026-11-27.

MOVED to shared/twai_port with unacked_judge.py by the charge-interposer tester, 2026-11-28, from
gen-inhibit/tools/bench/test_unacked_bench.py at gen-inhibit/main 74555175. Every check and mutant
of the moved code is unchanged, except that the query's checks are made on call_args(), its
argument list. What stayed with the bench driver's code stays with its test: the check that a call
that cannot connect gives no status (http_post), the four start-refusal checks (start_refusal), and
their three mutants ("a frame in the quiet not refused", "the Kvaser not checked", "a refused call
raises").
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("UNACKED_JUDGE_DIR") or HERE)
import unacked_judge as B  # noqa: E402
import unacked_analysis as U  # noqa: E402

fails = []
oks = 0
TS = 32
HOST_OFF_S = 10.0           # host perf minus witness time
DEV_OFF_US = 1_000_240.0    # witness time minus device time


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[-300:]))


def trial(n=30, cleared="cleanup", hold_ns=1_500_000_000, **kw):
    """n unacknowledged attempts (6 us active idles, 22 us passive), as test_unacked_analysis builds them."""
    ev, tec, now, rises = [[0, 0, 0, 0]], 0, 20_000, []
    for _ in range(n):
        rises.append(now)
        ev.append([now, TS, tec, 0])
        tec = min(tec + 8, 128)
        ev.append([now + 230_000, 0, tec, 0])
        now += 230_000 + (22_000 if tec >= 128 else 6_000)
    t = {"t": 0, "skip": "", "events": ev, "attempts": n, "end_ns": now, "gaps": 0, "max_gap_ns": 1000, "ev_lost": 0,
         "cleared": cleared, "clear_ns": -1, "abort_ns": -1, "hold_ns": hold_ns}
    t.update(kw)
    return t, rises


def witness_msgs(rises, extra_hw=()):
    """(perf, hw_us, id, dlc, hex, is_error, epoch) for each copy, plus a frame of another ID and an error frame."""
    out = []
    for r in rises:
        hw = r / 1000.0 + DEV_OFF_US
        out.append((hw / 1e6 + HOST_OFF_S, hw, 0x7FE, 8, "0000ab7e00000000", False, 0.0))
    for hw in extra_hw:
        out.append((hw / 1e6 + HOST_OFF_S, hw, 0x7FE, 8, "0000ab7e00000000", False, 0.0))
    out.append((out[0][0], out[0][1] + 5, 0x123, 8, "0000ab7e00000000", False, 0.0))   # another ID
    out.append((out[0][0], out[0][1] + 7, 0x7FE, 8, "0000ab7e00000000", True, 0.0))    # an error frame
    out.append((out[0][0], out[0][1] + 9, 0x7FE, 8, "0000ab7f00000000", False, 0.0))   # another tag (AB 7F)
    return out


def node_at(up_w_us, down_w_us, rx_w_us=()):
    """A node record (host perf) whose window and frames sit at the given witness times."""
    p = lambda w: w / 1e6 + HOST_OFF_S   # noqa: E731
    return {"open_called": p(up_w_us), "opened": p(up_w_us) + 0.002, "closed": p(down_w_us),
            "rx": [(p(w), "0000ab7e00000000") for w in rx_w_us], "error": None}


def main():
    # 1. the plan
    calls = B.plan("full", 1)
    c = collections.Counter((x["kind"], x.get("irq"), x["state"], x.get("delay_us")) for x in calls)
    check("M1: at least 5 with irq off and 5 with irq on", c[("M1", "off", "both", None)] >= 5
          and c[("M1", "on", "both", None)] >= 5, c)
    check("the control: at least 5", c[("control", "off", "both", None)] >= 5, c)
    check("M2: at least 50 per error state", c[("M2", "off", "active", None)] >= 50
          and c[("M2", "off", "passive", None)] >= 50, c)
    check("M3: at least 20 per delay (20, 100, 200 us) and state",
          all(c[("M3", "off", st, d)] >= 20 for st in ("active", "passive") for d in (20, 100, 200)), c)
    act = {x["wait_att"] for x in calls if x["kind"] in ("M2", "M3") and x["state"] == "active"}
    pas = {x["wait_att"] for x in calls if x["kind"] in ("M2", "M3") and x["state"] == "passive"}
    check("active aims at attempts 1-15, passive at 17 or later", max(act) <= 15 and min(pas) >= 17, (act, pas))
    check("a hold on every control, M2 and M3 call; none on M1",
          all(x.get("hold_ms") == B.HOLD_MS for x in calls if x["kind"] != "M1")
          and not any("hold_ms" in x for x in calls if x["kind"] == "M1"), "hold")
    check("one fresh trial per call", all(x["reps"] == 1 and x["fresh"] == 1 for x in calls), "reps/fresh")
    check("the node's window inside the hold", 0 < B.NODE_UP_S < B.NODE_DOWN_S < B.HOLD_MS / 1000.0,
          (B.NODE_UP_S, B.NODE_DOWN_S, B.HOLD_MS))
    check("the slowest open measured (1.2 s) plus the time up fits the hold, with 0.1 s for the POST's way",
          B.NODE_OPEN_S >= 1.2 and B.NODE_UP_S + B.NODE_OPEN_S + B.NODE_MIN_UP_S <= B.HOLD_MS / 1000.0 - 0.1,
          (B.NODE_UP_S, B.NODE_OPEN_S, B.NODE_MIN_UP_S, B.HOLD_MS))
    check("an open back early: the close at the send plus NODE_DOWN_S",
          abs(B.node_down_at(10.0, 10.5) - (10.0 + B.NODE_DOWN_S)) < 1e-9, B.node_down_at(10.0, 10.5))
    check("an open back late: the close NODE_MIN_UP_S after it",
          abs(B.node_down_at(10.0, 11.45) - (11.45 + B.NODE_MIN_UP_S)) < 1e-9, B.node_down_at(10.0, 11.45))
    k2 = lambda x: (x["kind"], x["state"] if x["kind"] in ("M2", "M3") else x["irq"], x.get("delay_us"))  # noqa: E731
    kinds10 = {k2(x) for x in calls[7:17]}
    check("shuffled: the ten calls after the first seven are of at least four kinds", len(kinds10) >= 4, kinds10)
    check("the same seed gives the same order", [x["label"] for x in B.plan("full", 1)] == [x["label"] for x in calls],
          "order")

    key = lambda x: (x["kind"], x["state"] if x["kind"] in ("M2", "M3") else x["irq"])   # noqa: E731
    check("one call of each kind first, in order", [key(x) for x in calls[:7]] ==
          [("M1", "off"), ("M1", "on"), ("control", "off"), ("M2", "active"), ("M2", "passive"),
           ("M3", "active"), ("M3", "passive")], [key(x) for x in calls[:7]])
    check("no call lost or doubled by the reorder", sorted(x["label"] for x in calls)
          == sorted(x["label"] for x in B.plan("full", 2)) and len({x["label"] for x in calls}) == len(calls), "labels")

    # 1b. no answer: a restart from the uptime, the stop rule
    check("uptime below the last read plus the time between: a restart",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 3_000}, 140.0) is True, "restart")
    check("uptime grown 10 s in 100 s: a restart (it restarted 10 s ago)",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 610_000}, 200.0) is True, "short growth")
    check("uptime grown with the time: no restart",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 639_500}, 140.0) is False, "no restart")
    check("uptime 4 s short of the time between (inside the slack): no restart",
          B.restart_seen({"uptime_ms": 600_000}, 100.0, {"uptime_ms": 636_000}, 140.0) is False, "slack")
    check("no page after: unknown, not a restart", B.restart_seen({"uptime_ms": 1}, 0.0, None, 1.0) is None, "none")
    check("the plan stops at 10 calls with no answer, not at 9",
          B.stop_now(10) and not B.stop_now(9), (B.stop_now(9), B.stop_now(10)))

    # 2. the call's arguments and the echo
    m3 = next(x for x in calls if x["kind"] == "M3")
    q = B.call_args(m3)
    check("the arguments: phase first, the label last, the hold, aim and delay in them",
          q[0] == "phase=m3" and q[-1] == "label=" + m3["label"]
          and "hold_ms=%d" % B.HOLD_MS in q and "wait_att=%d" % m3["wait_att"] in q
          and any(a.startswith("delay_us=") for a in q), q)
    m1 = next(x for x in calls if x["kind"] == "M1")
    check("an M1 call's arguments have no hold", not any(a.startswith("hold_ms=") for a in B.call_args(m1)),
          B.call_args(m1))
    check("each argument the call gives, once, and nothing it does not give (kind, state)",
          sorted(a.split("=")[0] for a in q) == sorted([k for k in B.ECHO if k in m3] + ["label"]), q)
    ans = {k: m3[k] for k in B.ECHO if k in m3}
    ans["label"] = m3["label"]
    ans["settle_ms"] = ans.pop("settle")      # the answer's name for it (bench run 403a)
    check("an answer echoing every argument, settle as settle_ms: no mismatch", B.echo_mismatch(m3, ans) == [],
          B.echo_mismatch(m3, ans))
    check("settle_ms missing: a mismatch", B.echo_mismatch(m3, {k: v for k, v in ans.items() if k != "settle_ms"}) != [],
          "settle")
    check("a different hold_ms is a mismatch", B.echo_mismatch(m3, dict(ans, hold_ms=0)) != [], "hold")
    check("a different label is a mismatch", B.echo_mismatch(m3, dict(ans, label="x")) != [], "label")

    # 3. the trial copies
    t, rises = trial(5)
    cps = B.trial_copies(witness_msgs(rises))
    check("trial copies: the five 0x7FE AB 7E frames, not another ID, an error frame or another tag",
          len(cps) == 5, cps)

    # 4. one call: the control, held, received by the node inside the hold
    ctl = dict(next(x for x in calls if x["kind"] == "control"))
    t, rises = trial(30)
    end_w = t["end_ns"] / 1000.0 + DEV_OFF_US
    msgs = witness_msgs(rises)
    row = B.process_call(ctl, {"trials": [t]}, msgs, node_at(end_w + 400_000, end_w + 1_100_000,
                                                             [end_w + 400_300]))
    h = row.get("hold") or {}
    check("the control: summary and hold, as expected", "summary" in row and "hold" in row and h.get("void") is None
          and h.get("held") and h.get("as_expected"), h)
    row2 = B.process_call(ctl, {"trials": [t]}, msgs, node_at(end_w + 400_000, end_w + 2_100_000,
                                                              [end_w + 400_300]))
    h2 = row2.get("hold") or {}
    check("the node still up after the hold ended (hold_ms 2000): void", "after the hold ended" in str(h2.get("void")), h2)
    t98 = dict(t, hold_ns=98_548_450)    # bench run 403a: hold_ns 98.5 ms for a 2000 ms hold
    h98 = B.process_call(ctl, {"trials": [t98]}, msgs, node_at(end_w + 400_000, end_w + 1_600_000,
                                                               [end_w + 400_300])).get("hold") or {}
    check("the hold's end from hold_ms, not hold_ns: a node down at +1.6 s is inside it",
          h98.get("void") is None and h98.get("hold_ns_answered") == 98_548_450, h98)
    short_node = node_at(end_w + 400_000, end_w + 1_100_000, [end_w + 1_099_000])
    short_node["opened"] = short_node["closed"] - 0.05      # the open came back 50 ms before the close
    h5 = B.process_call(ctl, {"trials": [t]}, msgs, short_node).get("hold") or {}
    check("the node up only 50 ms from its open's return: void", "up only" in str(h5.get("void")), h5)
    # the stack and the timing (the reviewer's go for the rerun, 2026-11-27)
    rs = {v: B.process_call(ctl, {"trials": [t], "stack_free_min": v, "cpu_hz": 160_000_000,
                                  "cpu_hz_end": 160_000_000}, msgs, None) for v in (400, 512, 1500)}
    r_none = B.process_call(ctl, {"trials": [t]}, msgs, None)
    check("stack_free_min recorded; under 512 B flagged, 512 and over not",
          rs[400]["stack_free_min"] == 400 and rs[400]["stack_low"] and not rs[512]["stack_low"]
          and not rs[1500]["stack_low"], [(v, r["stack_low"]) for v, r in rs.items()])
    check("no stack_free_min in the answer is flagged too", r_none["stack_low"] is True, r_none["stack_low"])
    tb_s = B.tables([rs[400], rs[1500], r_none])
    check("the tables list the low and missing calls, and the figures",
          [x[1] for x in tb_s["stack_low"]] == [400, None] and tb_s["stack_free_min"]["min"] == 400, tb_s["stack_low"])
    tm = rs[1500].get("timing") or {}
    check("timing: the clock, the trial's lengths, the send, the counts both sides and the first records",
          tm.get("cpu_hz") == 160_000_000 and tm.get("rises") == 30 and tm.get("copies") == 30
          and tm.get("attempts") == 30 and tm.get("first_sample_ts") is False and len(tm.get("events_head") or []) == 6
          and "clk_us" in tm and "tx_call_ns" in tm, tm)
    row3 = B.process_call(ctl, {"trials": [t, t]}, msgs, None)
    check("an answer of two trials is an error", "error" in row3, row3)
    row4 = B.process_call(ctl, {"trials": [t]}, msgs, node_at(end_w + 400_000, end_w + 1_100_000, []))
    check("the control, nothing received: not as expected", (row4.get("hold") or {}).get("as_expected") is False,
          row4.get("hold"))

    # 5. the tables and the positive control
    rows = [dict(row, kind="control"), dict(row4, label="CTL99", kind="control")]
    tb = B.tables(rows)
    check("a control trial that did not go out fails the control", B.control_failed(tb), tb["control_hold"])
    check("every control trial out: the control passes", not B.control_failed(B.tables([row])), "control")
    check("no control trial: the control fails", B.control_failed(B.tables([])), "none")
    m2 = dict(next(x for x in calls if x["kind"] == "M2" and x["state"] == "passive"))
    ta, ra = trial(8, abort_ns=7 * 236_000 + 20_000 + 232_000, ab_issued=True, ab_st_before=0, ab_st_after=4,
                   ab_tec=40, ab_attempt=7, free_after_abort_ns=900, cleared="tbs")
    end_a = ta["end_ns"] / 1000.0 + DEV_OFF_US
    r_m2 = B.process_call(m2, {"trials": [ta]}, witness_msgs(ra[:7]), node_at(end_a + 400_000, end_a + 1_100_000))
    check("an M2 aimed passive whose abort read TEC 40 is listed as a missed aim",
          B.tables([r_m2])["aim_missed"] == [m2["label"]], B.tables([r_m2])["aim_missed"])

    # 5a. the hold on the host clock when the witness has no copy (bench run 403a: trials ending in error active)
    ta1, _ = trial(1)
    ta1["cleared"] = "cleanup"
    def hnode(open_called, opened, closed, rx=()):
        return {"open_called": open_called, "opened": opened, "closed": closed,
                "rx": [(p, "0000ab7e00000000") for p in rx], "error": None}
    hh = B.process_call(ctl, {"trials": [ta1]}, [], hnode(100.25, 101.2, 101.6, [101.3]), 100.0, 102.06).get("hold") or {}
    check("no witness copy, arrival known: the hold checked on the host clock, held and out as expected",
          hh.get("void") is None and hh.get("held") and hh.get("as_expected") and "host" in str(hh.get("clock")), hh)
    hv = B.process_call(ctl, {"trials": [ta1]}, [], hnode(100.03, 101.0, 101.4), 100.0, 102.06).get("hold") or {}
    check("host clock: the open called before the trace's latest end (arrival minus hold): void",
          hv.get("void") is not None and "before the trace ended" in str(hv.get("void")), hv)
    hl = B.process_call(ctl, {"trials": [ta1]}, [], hnode(100.25, 101.2, 102.1, [101.3]), 100.0, 102.2).get("hold") or {}
    check("host clock: the node up past the hold's earliest end (send plus hold): void",
          "earliest end" in str(hl.get("void")), hl)
    hn = B.process_call(ctl, {"trials": [ta1]}, [], hnode(100.25, 101.2, 101.6, [101.3]), 100.0, None).get("hold") or {}
    check("no witness copy and no arrival time: void as before", "no node window" in str(hn.get("void")), hn)

    # 5b. the rejudge: answered rows judged again from the saved answer, witness and node; the rest kept
    saved_w = [m[:6] for m in msgs]                       # the record keeps six fields per message
    ans_ok = {"trials": [t], "stack_free_min": 900, "label": ctl["label"],
              **{B.ECHO_AS.get(k, k): ctl[k] for k in B.ECHO if k in ctl}}
    rec = {"rows": [{"label": ctl["label"], "kind": "control", "call": ctl, "answer": ans_ok, "witness": saved_w,
                     "node": node_at(end_w + 400_000, end_w + 1_100_000, [end_w + 400_300]), "t_send": 1.0,
                     "error": "echo: settle: sent 20, answered None"},
                    {"label": "M2x", "kind": "M2", "call": m2, "error": "HTTP None: timed out", "recovery": {"x": 1}}]}
    nr = B.rejudge_record(rec)
    check("rejudge: both rows kept", len(nr["rows"]) == 2, len(nr["rows"]))
    r0, r1 = (nr["rows"] + [{}, {}])[:2]
    check("rejudge: the answered row judged again, its old echo error gone, the hold as expected",
          "summary" in r0 and "error" not in r0 and (r0.get("hold") or {}).get("as_expected") is True
          and r0.get("t_send") == 1.0, (r0.get("error"), r0.get("hold")))
    check("rejudge: the row with no answer kept as it was", r1 == rec["rows"][1], r1)
    check("rejudge: tables and the control from the rows", nr["control"] == "passed"
          and nr["tables"]["control_hold"]["held: must go out"]["as_expected"] == 1, nr.get("control"))

    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("M2 at 49 per state", '"full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 50, "m3": 20}',
     '"full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 49, "m3": 20}'),
    ("the control at 4", '"full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 50, "m3": 20}',
     '"full": {"m1_off": 6, "m1_on": 6, "control": 4, "m2": 50, "m3": 20}'),
    ("active aims up to 17", "ACTIVE_AIMS = tuple(range(1, 16, 2))", "ACTIVE_AIMS = tuple(range(1, 18, 2))"),
    ("the control with no hold", '        calls.append(dict(base, phase="m1", irq="off", hold_ms=HOLD_MS, kind="control"',
     '        calls.append(dict(base, phase="m1", irq="off", kind="control"'),
    ("not shuffled", "    random.Random(seed).shuffle(calls)\n", ""),
    ("the node down after the hold", "NODE_DOWN_S = 1.6 ", "NODE_DOWN_S = 2.1 "),
    ("the hold of run f8d5 (1500 ms)", "HOLD_MS = 2000 ", "HOLD_MS = 1500 "),
    ("the open's time taken as fast", "NODE_OPEN_S = 1.2 ", "NODE_OPEN_S = 0.1 "),
    ("the close not waiting for the open", "    return max(t_send + NODE_DOWN_S, opened + NODE_MIN_UP_S)\n",
     "    return t_send + NODE_DOWN_S\n"),
    ("no host-clock hold check", '        if node is not None and s["witness"]["offset_us"] is None and t_send is not None and t_recv is not None:\n',
     "        if False:\n"),
    ("the trace's latest end without the hold", '    s_host = dict(s, witness=dict(s["witness"], offset_us=(t_recv - hold_s) * 1e6 - tr["end_ns"] / 1000.0))\n',
     '    s_host = dict(s, witness=dict(s["witness"], offset_us=t_send * 1e6 - tr["end_ns"] / 1000.0))\n'),
    ("the hold's earliest end from the arrival", '    if h["void"] is None and node["closed"] >= t_send + hold_s:\n',
     '    if h["void"] is None and node["closed"] >= t_recv:\n'),
    ("settle echoed under its own name", 'ECHO_AS = {"settle": "settle_ms"}', "ECHO_AS = {}"),
    ("the hold's end from hold_ns", "                hold_end_w = tr[\"end_ns\"] / 1000.0 + hold_ms * 1000.0 + s[\"witness\"][\"offset_us\"]\n",
     "                hold_end_w = (tr[\"end_ns\"] + (tr.get(\"hold_ns\") or 0)) / 1000.0 + s[\"witness\"][\"offset_us\"]\n"),
    ("the rejudge keeps the old error", '                          row.get("t_send"), row.get("t_recv"))\n',
     '                          row.get("t_send"), row.get("t_recv"))\n        nr["error"] = row.get("error")\n'),
    ("the rejudge drops unanswered rows", '        if "answer" not in row:\n            out["rows"].append(row)\n            continue\n',
     '        if "answer" not in row:\n            continue\n'),
    ("the stack mark at 256 B", "STACK_LOW_B = 512 ", "STACK_LOW_B = 256 "),
    ("a missing stack figure not flagged", '    row["stack_low"] = sfm is None or (', '    row["stack_low"] = sfm is not None and ('),
    ("the low calls not listed", '"stack_low": [(r["label"], r.get("stack_free_min")) for r in rows if r.get("stack_low")],',
     '"stack_low": [],'),
    ("the first sample's TS not given", '    row["timing"]["first_sample_ts"] = bool(s["attempts"] and s["attempts"][0].get("first_sample"))\n',
     ""),
    ("a short node window not voided", "            if h[\"void\"] is None and short is not None:\n",
     "            if False:\n"),
    ("the hold not echoed", 'ECHO = ("phase", "reps", "settle", "fresh", "irq", "window_us", "wait_att", "delay_us", "hold_ms")',
     'ECHO = ("phase", "reps", "settle", "fresh", "irq", "window_us", "wait_att", "delay_us")'),
    ("the label not checked", '    if ans.get("label") != call["label"]:\n', "    if False:\n"),
    ("copies of any tag", 'm[2] == TRIAL_ID and m[4][4:8] == TAG]', "m[2] == TRIAL_ID]"),
    ("error frames counted as copies", "if not m[5] and m[2] == TRIAL_ID", "if m[2] == TRIAL_ID"),
    ("no void after the hold's end", "                if nw[\"down_us\"] >= hold_end_w:\n", "                if False:\n"),
    ("two trials accepted", "    if len(trials) != 1:\n", "    if not trials:\n"),
    ("the control passes on no trials", "    return bool(c[\"not_as_expected\"]) or c[\"trials\"] == 0\n",
     "    return bool(c[\"not_as_expected\"])\n"),
    ("missed aims not listed", "and r[\"summary\"][\"abort\"][\"state\"] != r[\"state_aimed\"]]", "and False]"),
    ("no call of each kind first", "    return first + [x for x in calls if x not in first]\n", "    return calls\n"),
    ("the restart slack ignored", "    return a < b + (t_after - t_before) * 1000.0 - RESTART_SLACK_MS\n",
     "    return a < b + (t_after - t_before) * 1000.0\n"),
    ("the time between ignored", "    return a < b + (t_after - t_before) * 1000.0 - RESTART_SLACK_MS\n",
     "    return a < b - RESTART_SLACK_MS\n"),
    ("the stop one call late", "    return failed_calls >= limit\n", "    return failed_calls > limit\n"),
    # call_args(), new with the move (query()'s list without the URI)
    ("an argument the call gives left out", '    args += ["%s=%s" % (k, call[k]) for k in ECHO[1:] if k in call]\n',
     '    args += ["%s=%s" % (k, call[k]) for k in ECHO[1:] if k in call and k != "hold_ms"]\n'),
    ("the label not last", '    args.append("label=%s" % call["label"])\n', '    args.insert(1, "label=%s" % call["label"])\n'),
]


def mutants():
    sys.path.insert(0, HERE)
    import mutants as MR
    return MR.run(os.path.abspath(__file__), "unacked_judge.py", MUTANTS, "UNACKED_JUDGE_DIR",
                  beside=("unacked_analysis.py",))


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
