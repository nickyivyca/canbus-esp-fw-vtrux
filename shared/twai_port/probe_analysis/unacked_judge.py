#!/usr/bin/env python3
"""The judgement of a bench run of the probe's no-acknowledge modes (shared/twai_port T13, the
2026-11-27 addition; gen-inhibit tracker F5; the gen-inhibit reviewer's request and answer (b),
2026-11-27), with no transport: the plan of calls, each call's arguments and the check of their
echo, each answer processed into its trial's summary and hold check, the tables, and the positive
control. A measurement: it gives figures, not a verdict, apart from the positive control.

MOVED to shared/twai_port (probe_analysis/, the shared probe-analysis move; charge-interposer
reviewer and gen-inhibit reviewer, 2026-11-27) by the charge-interposer tester, 2026-11-28, from
gen-inhibit/tools/bench/unacked_bench.py at gen-inhibit/main 74555175 (gen-inhibit tester,
2026-11-27). Each function here has that file's code unchanged, except call_args(), which is
query()'s argument list without the URI. What stayed in the bench driver is the transport and the
bench: the HTTP POST and its timeout, the hotspot rejoin and the status page, the acknowledging
node's open and close (Node, run_call), the start refusals (the X2 held, the Kvaser's link, the
quiet), the record's file and the console line. The charge interposer's driver sends the same
arguments as the T19 app's serial command, `txabort <key>=<value> ...`, and takes the probe's
answer from that command's "answer" field.

THE CALLS. One call per trial (reps=1), fresh=1 (each trial starts error active, TEC 0), so the
error state is chosen by the attempt the abort is aimed at (`wait_att`): attempts 1-15 are error
active (TEC below 128), 17-31 error passive. The state is read back from `ab_tec`.
  M1        phase=m1, window_us=10000, irq=off and irq=on, no hold: the TS trace through both states
  control   phase=m1, irq=off, with the hold: never aborted, so the frame must go out when the
            node returns (the positive control: it shows the hold check can fail)
  M2        phase=m2, wait_att from ACTIVE_AIMS or PASSIVE_AIMS, with the hold
  M3        phase=m3, delay_us 20, 100 and 200, the same aims, with the hold
The plan's order is shuffled (seed), so no state or kind runs as one block, except that one call of
each kind (M1 irq off, M1 irq on, control, M2 active, M2 passive, M3 active, M3 passive) runs first.

THE STACK AND THE TIMING (the gen-inhibit reviewer's go for the rerun, 2026-11-27). Each row
records the answer's `stack_free_min`, and a call under STACK_LOW_B, or with none, is listed in the
tables' `stack_low` (recorded, not judged). Each row's `timing` gives what the witness's copies are
lined up against: cpu_hz, cpu_hz_end, clk_us, clk_cyc, tx_call_ns, end_ns, the device's attempts
and ab_attempt, the rises counted here and whether the first sample read TS = 1, the copies, and
the first change records.

NO ANSWER. restart_seen() tells from two readings of the device's uptime whether it restarted
between them; stop_now() stops the plan after MAX_FAILED_CALLS calls with no answer.

THE HOLD (test_app/README.md step 9; the reviewer's answer (b)). hold_ms=HOLD_MS. The driver
brings the acknowledging node up NODE_UP_S after the call's send. Its open takes up to NODE_OPEN_S
(0.73-1.20 s in gen-inhibit bench run f8d5), so it closes NODE_MIN_UP_S after its open returns, and
not before NODE_DOWN_S after the send (node_down_at()); a trial whose node was up less than
NODE_MIN_UP_S is void. The open's call time and the close's return time bound the node's window,
put on the witness clock by the witness's stamps (host receipt minus witness stamp, the median
over the call). unacked_analysis.hold_check voids a trial when the node came up before its trace
ended, and process_call() voids one when the node was still up after the hold ended (the cleanup
would then let a held frame out). With no witness copy to line the device up with, the hold is
checked on the host clock from the send's and the answer's times (hold_check_host()).

THE NODE RECORD process_call() takes: {"open_called", "opened", "closed": host perf s, "rx": [(host
perf s, data hex)] of the frames the node received while up, "error"}. THE WITNESS MESSAGES: (host
perf s, witness us, ID, DLC, data hex, is_error, epoch) per frame; a saved record keeps the first
six fields.
"""
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import unacked_analysis as U  # noqa: E402

TRIAL_ID = 0x7FE
TAG = "ab7e"                 # bytes 2-3 of the trial frame (README step 3)
HOLD_MS = 2000               # the probe's limit
NODE_UP_S = 0.25             # canmain's open is called this long after the POST's send
NODE_OPEN_S = 1.2            # canmain's open took 0.73-1.20 s from its call (bench run f8d5, 2026-11-27)
NODE_MIN_UP_S = 0.3          # it stays up at least this long after its open returns
NODE_DOWN_S = 1.6            # and is not closed before this long after the POST's send
MAX_FAILED_CALLS = 10        # the plan stops after this many calls with no answer
STACK_LOW_B = 512            # a call whose stack_free_min is below this, or missing, is flagged
RESTART_SLACK_MS = 5000      # an uptime this far below the elapsed time is a restart
ACTIVE_AIMS = tuple(range(1, 16, 2))      # 1, 3, ..., 15
PASSIVE_AIMS = tuple(range(17, 32, 2))    # 17, 19, ..., 31
M3_DELAYS = (20, 100, 200)
PLANS = {   # kind -> trials: the reviewer's minimums (M1 >= 5, M2 >= 50 per state, M3 >= 20 per delay and state)
    "full": {"m1_off": 6, "m1_on": 6, "control": 6, "m2": 50, "m3": 20},
    "smoke": {"m1_off": 1, "m1_on": 1, "control": 1, "m2": 1, "m3": 1},
}
ECHO = ("phase", "reps", "settle", "fresh", "irq", "window_us", "wait_att", "delay_us", "hold_ms")
# the answer's name for an argument where it differs (bench run 403a, 2026-11-27: `settle` is echoed as `settle_ms`,
# as the older phases do; txabort_analysis.ECHO_KEYS)
ECHO_AS = {"settle": "settle_ms"}


def plan(name="full", seed=0):
    """The calls, each a dict of the query's arguments plus its kind and state. Shuffled by `seed`."""
    n = PLANS[name]
    calls = []
    base = {"reps": 1, "settle": 20, "fresh": 1, "window_us": 10000}
    for k in range(n["m1_off"]):
        calls.append(dict(base, phase="m1", irq="off", kind="M1", state="both", label="M1off%02d" % k))
    for k in range(n["m1_on"]):
        calls.append(dict(base, phase="m1", irq="on", kind="M1", state="both", label="M1on%02d" % k))
    for k in range(n["control"]):
        calls.append(dict(base, phase="m1", irq="off", hold_ms=HOLD_MS, kind="control", state="both",
                          label="CTL%02d" % k))
    for st, aims in (("active", ACTIVE_AIMS), ("passive", PASSIVE_AIMS)):
        for k in range(n["m2"]):
            calls.append(dict(base, phase="m2", irq="off", wait_att=aims[k % len(aims)], hold_ms=HOLD_MS,
                              kind="M2", state=st, label="M2%s%02d" % (st[0], k)))
        for d in M3_DELAYS:
            for k in range(n["m3"]):
                calls.append(dict(base, phase="m3", irq="off", wait_att=aims[k % len(aims)], delay_us=d,
                                  hold_ms=HOLD_MS, kind="M3", state=st, label="M3%s%d_%02d" % (st[0], d, k)))
    random.Random(seed).shuffle(calls)
    # one call of each kind first (bench run 797b, 2026-11-27: a failing kind must not hide the others behind
    # the stop rule), in this order, then the rest as shuffled
    key = lambda x: (x["kind"], x["state"] if x["kind"] in ("M2", "M3") else x["irq"])   # noqa: E731
    first = []
    for want in (("M1", "off"), ("M1", "on"), ("control", "off"), ("M2", "active"), ("M2", "passive"),
                 ("M3", "active"), ("M3", "passive")):
        c = next((x for x in calls if x not in first and key(x) == want), None)
        if c is not None:
            first.append(c)
    return first + [x for x in calls if x not in first]


def restart_seen(before, t_before, after, t_after):
    """Whether the device restarted between two page reads: its uptime_ms after is more than RESTART_SLACK_MS
    below the uptime before plus the time between the reads (host perf s). None when either read is missing."""
    try:
        b, a = float(before["uptime_ms"]), float(after["uptime_ms"])
    except (TypeError, KeyError, ValueError):
        return None
    return a < b + (t_after - t_before) * 1000.0 - RESTART_SLACK_MS


def stop_now(failed_calls, limit=MAX_FAILED_CALLS):
    """The plan stops once this many calls had no answer."""
    return failed_calls >= limit


def call_args(call):
    """The call's arguments as key=value: phase first, then each argument the call gives, in ECHO's order,
    then the label. Gen-inhibit's query() joins them with "&" after "/gi_txabort?"; the T19 app's serial
    command takes them after "txabort ", joined with spaces."""
    args = ["phase=%s" % call["phase"]]
    args += ["%s=%s" % (k, call[k]) for k in ECHO[1:] if k in call]
    args.append("label=%s" % call["label"])
    return args


def echo_mismatch(call, ans):
    """The arguments the answer does not echo as sent (README: "the arguments as run")."""
    bad = []
    for k in ECHO:
        a = ECHO_AS.get(k, k)
        if k in call and str(ans.get(a)) != str(call[k]):
            bad.append("%s: sent %s, answered %s as %s" % (k, call[k], ans.get(a), a))
    if ans.get("label") != call["label"]:
        bad.append("label: sent %s, answered %s" % (call["label"], ans.get("label")))
    return bad


def trial_copies(msgs):
    """The witness's copies of the trial frame among (perf, hw_us, id, dlc, hex, is_error, epoch) messages:
    (hw_us, hex)."""
    return [(m[1], m[4]) for m in msgs if not m[5] and m[2] == TRIAL_ID and m[4][4:8] == TAG]


def clock_offset(msgs):
    """Host perf (s) minus witness time (s): the median over the messages."""
    d = sorted(p - hw / 1e6 for p, hw, *_ in msgs)
    return statistics.median(d) if d else None


def node_down_at(t_send, opened):
    """When canmain closes (host perf s): NODE_MIN_UP_S after its open returned, and not before NODE_DOWN_S
    after the POST's send."""
    return max(t_send + NODE_DOWN_S, opened + NODE_MIN_UP_S)


def node_short(node):
    """The node's time up, from its open's return to its close, when shorter than NODE_MIN_UP_S; else None."""
    up = node["closed"] - node["opened"]
    return up if up < NODE_MIN_UP_S else None


def node_window_w(node, off_s):
    """The node's window and its frames on the witness clock (us), from host perf times."""
    if node is None or off_s is None:
        return None
    w = lambda p: (p - off_s) * 1e6   # noqa: E731
    return {"up_us": w(node["open_called"]), "down_us": w(node["closed"]),
            "rx_us": [w(p) for p, hexd in node["rx"] if hexd[4:8] == TAG]}


def hold_check_host(s, tr, call, node, t_send, t_recv):
    """The hold check on the host clock, for a trial with no witness copy to line the device up with (bench run
    403a: 9 trials that ended in the error-active part, which the witness does not deliver). The trace ended no
    later than the answer's arrival minus hold_ms (the hold lasts at least hold_ms, and the answer comes after it),
    so the node is up after the trace when its open was called after that. The hold ended no earlier than the
    POST's send plus hold_ms, so the node is down in time when its close returned before that."""
    hold_s = call["hold_ms"] / 1000.0
    s_host = dict(s, witness=dict(s["witness"], offset_us=(t_recv - hold_s) * 1e6 - tr["end_ns"] / 1000.0))
    rx = [p * 1e6 for p, hexd in node["rx"] if hexd[4:8] == TAG]
    h = U.hold_check(s_host, tr, [], node["open_called"] * 1e6, node["closed"] * 1e6, rx)
    h["clock"] = "host (no witness copy): the trace's end at the latest, the hold's end at the earliest"
    if h["void"] is None and node["closed"] >= t_send + hold_s:
        h["void"] = "the node was up %.3f s after the hold's earliest end" % (node["closed"] - t_send - hold_s)
    return h


def process_call(call, ans, msgs, node, t_send=None, t_recv=None):
    """One call: its trial's summary, and for a call with a hold, the hold check (on the witness clock, or on the
    host clock when the witness has no copy and the send and arrival times are known). Returns the row."""
    row = {"label": call["label"], "kind": call["kind"], "state_aimed": call["state"], "call": call}
    # the httpd task's least free stack (README: the answer's last field, from tag 2026.11.27.3; the panics of
    # bench run f8d5 were that stack overflowing), flagged under STACK_LOW_B (the gen-inhibit reviewer, 2026-11-27)
    sfm = ans.get("stack_free_min")
    row["stack_free_min"] = sfm
    row["stack_low"] = sfm is None or (isinstance(sfm, (int, float)) and sfm < STACK_LOW_B)
    row["clock"] = {k: ans.get(k) for k in ("cpu_hz", "cpu_hz_end")}
    trials = ans.get("trials") or []
    if len(trials) != 1:
        row["error"] = "the answer has %d trials, want 1" % len(trials)
        return row
    tr = trials[0]
    copies = trial_copies(msgs)
    s = U.trial_summary(tr, copies)
    row["summary"] = s
    # what lines the witness's copies up against the device's count (the reviewer's request, 2026-11-27): the
    # clock, the trial's length both ways, the send's own time, the device's attempts and the first change records
    row["timing"] = dict(row["clock"], **{k: tr.get(k) for k in ("clk_us", "clk_cyc", "tx_call_ns", "end_ns", "samples",
                                                                   "max_gap_ns", "gaps", "attempts", "ab_attempt",
                                                                   "met_ns", "anchor_ns", "abort_ns")})
    row["timing"]["rises"] = len(s["attempts"])
    row["timing"]["first_sample_ts"] = bool(s["attempts"] and s["attempts"][0].get("first_sample"))
    row["timing"]["copies"] = len(copies)
    row["timing"]["copies_in_trace"] = s["copies_in_trace"]
    row["timing"]["events_head"] = (tr.get("events") or [])[:6]
    row["t"] = call["label"]
    if call.get("hold_ms"):
        off = clock_offset(msgs)
        nw = node_window_w(node, off)
        if node is not None and s["witness"]["offset_us"] is None and t_send is not None and t_recv is not None:
            row["hold"] = hold_check_host(s, tr, call, node, t_send, t_recv)
            short = node_short(node)
            if row["hold"]["void"] is None and short is not None:
                row["hold"]["void"] = "the node was up only %.3f s from its open's return" % short
        elif nw is None:
            row["hold"] = {"void": "no node window or no witness clock", "held": None, "as_expected": None}
        else:
            h = U.hold_check(s, tr, copies, nw["up_us"], nw["down_us"], nw["rx_us"])
            # the hold's end at its earliest: the trace's end plus hold_ms (README step 9: the hold lasts at least
            # hold_ms). Not hold_ns: in bench run 403a it read 98.5 ms for a 2000 ms hold, with clk_us 2.01 s
            hold_ms = call.get("hold_ms")
            h["hold_ns_answered"] = tr.get("hold_ns")
            if h["void"] is None and s["witness"]["offset_us"] is not None and hold_ms:
                hold_end_w = tr["end_ns"] / 1000.0 + hold_ms * 1000.0 + s["witness"]["offset_us"]
                h["hold_end_us"] = round(hold_end_w, 3)
                if nw["down_us"] >= hold_end_w:
                    h["void"] = "the node was still up %.1f us after the hold ended" % (nw["down_us"] - hold_end_w)
            short = node_short(node)
            if h["void"] is None and short is not None:
                h["void"] = "the node was up only %.3f s from its open's return" % short
            row["hold"] = h
    return row


def tables(rows):
    ok = [r for r in rows if "summary" in r]
    m1 = [r["summary"] for r in ok if r["kind"] == "M1"]
    out = {"M1_irq_off": U.m1_table([r["summary"] for r in ok if r["kind"] == "M1" and r["call"]["irq"] == "off"]),
           "M1_irq_on": U.m1_table([r["summary"] for r in ok if r["kind"] == "M1" and r["call"]["irq"] == "on"]),
           "M1_all": U.m1_table(m1),
           "M2": U.abort_table([r["summary"] for r in ok if r["kind"] == "M2"]),
           "control_hold": U.hold_table([{"t": r["label"], "hold": r["hold"]} for r in ok
                                         if r["kind"] == "control" and "hold" in r]),
           "M2_hold": U.hold_table([{"t": r["label"], "hold": r["hold"]} for r in ok if r["kind"] == "M2" and "hold" in r]),
           "errors": [(r["label"], r["error"]) for r in rows if "error" in r],
           "stack_low": [(r["label"], r.get("stack_free_min")) for r in rows if r.get("stack_low")],
           "stack_free_min": U.span_stats([r["stack_free_min"] for r in rows
                                           if isinstance(r.get("stack_free_min"), (int, float))])}
    for d in M3_DELAYS:
        sel = [r for r in ok if r["kind"] == "M3" and r["call"].get("delay_us") == d]
        out["M3_%d" % d] = U.abort_table([r["summary"] for r in sel])
        out["M3_%d_hold" % d] = U.hold_table([{"t": r["label"], "hold": r["hold"]} for r in sel if "hold" in r])
    # aimed state against the state read back at the abort
    out["aim_missed"] = [r["label"] for r in ok if r["kind"] in ("M2", "M3") and r["summary"]["abort"]
                         and r["summary"]["abort"]["state"] != r["state_aimed"]]
    return out


def control_failed(t):
    """The positive control: every control trial that is not void went out."""
    c = t["control_hold"]["held: must go out"]
    return bool(c["not_as_expected"]) or c["trials"] == 0


def rejudge_record(rec):
    """Every answered row judged again from its saved answer, witness messages and node record (the harness's
    judging as it now is); a row with no answer kept as it was. Returns the new record."""
    out = dict(rec, rows=[])
    for row in rec.get("rows") or []:
        if "answer" not in row:
            out["rows"].append(row)
            continue
        nr = process_call(row["call"], row["answer"], row.get("witness") or [], row.get("node"),
                          row.get("t_send"), row.get("t_recv"))
        bad = echo_mismatch(row["call"], row["answer"])
        if bad:
            nr["error"] = "echo: " + "; ".join(bad)
        for k in ("answer", "t_send", "t_recv", "witness", "node", "recovery"):
            if k in row:
                nr[k] = row[k]
        out["rows"].append(nr)
    out["tables"] = tables(out["rows"])
    out["control"] = "FAILED" if control_failed(out["tables"]) else "passed"
    return out
