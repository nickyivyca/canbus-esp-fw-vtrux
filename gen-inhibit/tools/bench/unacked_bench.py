#!/usr/bin/env python3
"""F5 on the bench: the C3's TS bit between unacknowledged resends, and what the abort does during
an attempt and in the idle (shared/twai_port T13, the 2026-11-27 addition; gen-inhibit tracker F5;
the gen-inhibit reviewer's request and answer (b), 2026-11-27). A measurement: it gives figures,
not a verdict, apart from the positive control. Gen-inhibit tester, 2026-11-27.

    GI_RUNS_DIR=<dir> python3.13 -B unacked_bench.py [--plan full|smoke] [--seed N]

Needs the TWAI_PORT_TXABORT_PROBE measurement build (`_txabort`) with the no-acknowledge modes
and `hold_ms` (shared/twai_port test_app/README.md, "The no-acknowledge modes"), the X2 held in
ROM download mode, and the bench lock (the chain runner's).

THE BUS (as nonode_bench.py; bench-holder.md, canctl roles). On CAN1: the WiCAN, the X2's CAN1,
the Kvaser, the PCAN and canmain. canchg is on the X2's CAN2 and is never opened.
  Kvaser   link DOWN, read back at the start: an up kvaser link ACKs with no socket open
  canmain  held for libusb for the run (kvaser_bus.innomaker_held). Closed, it is silent; opened
           (gs_usb has no listen-only), it ACKs: it is the acknowledging node of the hold. Each
           frame it receives while open is recorded: a frame it received went out
  PCAN     PASSIVE (listen-only), the witness for the whole run; it never ACKs
  X2       held in ROM download mode, CAN1 TX not driven (x2_hold.py pins, start and end)
START REFUSALS (exit 2, nothing sent): the X2 not shown held; the Kvaser's link not down; any frame
on the witness in QUIET_S of quiet before the first call (another node is sending).

THE CALLS. One POST /gi_txabort per trial (reps=1), fresh=1 (each trial starts error active,
TEC 0), so the error state is chosen by the attempt the abort is aimed at (`wait_att`): attempts
1-15 are error active (TEC below 128), 17-31 error passive. The state is read back from `ab_tec`.
  M1        phase=m1, window_us=10000, irq=off and irq=on, no hold: the TS trace through both states
  control   phase=m1, irq=off, with the hold: never aborted, so the frame must go out when the
            node returns (the positive control: it shows the hold check can fail)
  M2        phase=m2, wait_att from ACTIVE_AIMS or PASSIVE_AIMS, with the hold
  M3        phase=m3, delay_us 20, 100 and 200, the same aims, with the hold
The plan's order is shuffled (--seed), so no state or kind runs as one block, except that one call of
each kind (M1 irq off, M1 irq on, control, M2 active, M2 passive, M3 active, M3 passive) runs first.

THE STACK AND THE TIMING (the gen-inhibit reviewer's go for the rerun, 2026-11-27). Run f8d5's
panics were the answer overflowing the httpd task's 5120-byte stack (fixed at shared tag
2026.11.27.3). Each row records the answer's `stack_free_min`, and a call under STACK_LOW_B, or
with none, is listed in the tables' `stack_low` (recorded, not judged). Each row's `timing` gives
what the witness's copies are lined up against: cpu_hz, cpu_hz_end, clk_us, clk_cyc, tx_call_ns,
end_ns, the device's attempts and ab_attempt, the rises counted here and whether the first sample
read TS = 1, the copies, and the first change records.

THE HOTSPOT (bench run 797b, 2026-11-27: the first call's trial went on the wire, its answer never
came, and the host had no route to the hotspot for the rest of the run, so 236 calls failed). The
run joins the hotspot at its start through gi_wifi (GI_OTA_SETTINGS, the core's Wi-Fi backend that
settings.ota chooses) and reads the status page. After a call with no HTTP answer (CALL_TIMEOUT_S),
it joins again and reads the page: the row's `recovery` gives the join, the page's uptime_ms,
reset_reason and mode, and whether the device restarted (restart_seen: the uptime more than
RESTART_SLACK_MS below the last read's uptime plus the time between). The plan stops after
MAX_FAILED_CALLS calls with no answer (`stopped`, exit 1).

THE HOLD (README step 9; the reviewer's answer (b)). hold_ms=HOLD_MS. The POST is sent from a
thread; canmain's open is called NODE_UP_S after the POST's send. The open takes up to NODE_OPEN_S
(0.73-1.20 s in bench run f8d5, where the node was planned up at 0.5 s and was up only from about
1.36 s), so canmain closes NODE_MIN_UP_S after its open returns, and not before NODE_DOWN_S after the
send; a trial whose node was up less than NODE_MIN_UP_S is void. The open's call time and the
close's return time bound the node's window, put on the witness clock by the
PCAN's stamps (host receipt minus witness stamp, the median over the call). unacked_analysis.hold_check
voids a trial when the node came up before its trace ended, and this driver voids one when the
node was still up after the hold ended (the cleanup would then let a held frame out).

OUTPUT: unacked_bench_<stamp>.json in GI_RUNS_DIR (every answer, every copy, the node's windows
and frames, the summaries and the tables) and a text summary on stdout. Exit 0 when the plan ran
and every control trial that is not void went out; 1 when a control trial did not, or a call
failed; 2 refused.
"""
import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import unacked_analysis as U  # noqa: E402

HOST = "192.168.80.1"
TRIAL_ID = 0x7FE
TAG = "ab7e"                 # bytes 2-3 of the trial frame (README step 3)
QUIET_S = 1.0
HOLD_MS = 2000               # the probe's limit
NODE_UP_S = 0.25             # canmain's open is called this long after the POST's send
NODE_OPEN_S = 1.2            # canmain's open took 0.73-1.20 s from its call (bench run f8d5, 2026-11-27)
NODE_MIN_UP_S = 0.3          # it stays up at least this long after its open returns
NODE_DOWN_S = 1.6            # and is not closed before this long after the POST's send
CALL_TIMEOUT_S = 10.0       # a call answers in about 2 s with the hold
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


def page_brief(p):
    return None if not p else {k: p.get(k) for k in ("uptime_ms", "reset_reason", "mode", "elf_sha256")}


def stop_now(failed_calls, limit=MAX_FAILED_CALLS):
    """The plan stops once this many calls had no answer."""
    return failed_calls >= limit


def query(call):
    """The POST's path: phase first, then each argument the call gives, in ECHO's order, then the label."""
    args = ["phase=%s" % call["phase"]]
    args += ["%s=%s" % (k, call[k]) for k in ECHO[1:] if k in call]
    args.append("label=%s" % call["label"])
    return "/gi_txabort?" + "&".join(args)


def echo_mismatch(call, ans):
    """The arguments the answer does not echo as sent (README: "the arguments as run")."""
    bad = []
    for k in ECHO:
        if k in call and str(ans.get(k)) != str(call[k]):
            bad.append("%s: sent %s, answered %s" % (k, call[k], ans.get(k)))
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


def process_call(call, ans, msgs, node):
    """One call: its trial's summary, and for a call with a hold, the hold check. Returns the row."""
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
        if nw is None:
            row["hold"] = {"void": "no node window or no witness clock", "held": None, "as_expected": None}
        else:
            h = U.hold_check(s, tr, copies, nw["up_us"], nw["down_us"], nw["rx_us"])
            hold_ns = tr.get("hold_ns")
            if h["void"] is None and s["witness"]["offset_us"] is not None and hold_ns is not None and hold_ns >= 0:
                hold_end_w = (tr["end_ns"] + hold_ns) / 1000.0 + s["witness"]["offset_us"]
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


def start_refusal(rec):
    """Nothing is sent when the X2 is not shown held, the Kvaser's link is not down, or the quiet heard a frame."""
    import nonode_bench as NB
    if not NB.x2_held(rec.get("x2_pins_start") or ""):
        return "the X2 is not shown held (x2_hold.py pins: %s)" % rec.get("x2_pins_start")
    kl = rec.get("kvaser_link_start") or {}
    if kl.get("down") is not True:
        return "the Kvaser's link is not shown down (%s)" % kl.get("error", kl)
    if rec.get("quiet_frames"):
        return "%d frame(s) on the witness in %.1f s of quiet: another node is sending" % (rec["quiet_frames"], QUIET_S)
    return None


def http_post(path, timeout=CALL_TIMEOUT_S):
    req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:     # no route, refused, timed out: recorded, no status
        return None, ("%s: %s" % (type(e).__name__, getattr(e, "reason", e))).encode()


class Node(threading.Thread):
    """canmain open for the hold: reads every frame it receives (host perf, hex)."""

    def __init__(self, inno):
        super().__init__(daemon=True)
        self.inno, self.bus, self.rx = inno, None, []
        self.stop = threading.Event()
        self.open_called = self.opened = self.closed = None
        self.error = None

    def run(self):
        while not self.stop.is_set():
            try:
                m = self.bus.recv(timeout=0.01)
            except Exception as e:      # noqa: BLE001  recorded; the hold's check then has no frames from it
                self.error = "%s: %s" % (type(e).__name__, e)
                return
            if m is not None and not m.is_error_frame and m.arbitration_id == TRIAL_ID:
                self.rx.append((time.perf_counter(), bytes(m.data).hex()))

    def up(self):
        self.open_called = time.perf_counter()
        self.bus = self.inno.open(bitrate=500000)
        self.opened = time.perf_counter()
        self.start()

    def down(self):
        self.stop.set()
        self.join(1.0)
        self.bus.shutdown()
        self.closed = time.perf_counter()
        return {"open_called": self.open_called, "opened": self.opened, "closed": self.closed, "rx": self.rx,
                "error": self.error}


def run_call(call, wit, inno):
    """One POST; for a call with a hold, canmain up and down inside it. Returns (answer or None, status,
    body head, the witness's messages for the call, the node record or None)."""
    mark = wit.mark()
    box = {}
    t_send = time.perf_counter()
    th = threading.Thread(target=lambda: box.update(r=http_post(query(call))), daemon=True)
    th.start()
    node = None
    if call.get("hold_ms"):
        time.sleep(max(0.0, t_send + NODE_UP_S - time.perf_counter()))
        n = Node(inno)
        n.up()
        time.sleep(max(0.0, node_down_at(t_send, n.opened) - time.perf_counter()))
        node = n.down()
    th.join(CALL_TIMEOUT_S + 5)
    time.sleep(0.05)                     # the witness's last frames
    msgs = wit.since(mark)
    st, body = box.get("r", (None, b""))
    ans = None
    if st == 200:
        try:
            ans = json.loads(body)
        except ValueError:
            ans = None
    return ans, st, body[:300].decode("utf-8", "replace"), msgs, node, t_send


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", choices=sorted(PLANS), default="full")
    ap.add_argument("--seed", type=int, default=20261127)
    a = ap.parse_args()
    import can
    import kvaser_bus as KB
    import nonode_bench as NB
    runs = os.environ.get("GI_RUNS_DIR") or "."
    os.makedirs(runs, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    calls = plan(a.plan, a.seed)
    rec = {"when": stamp, "plan": a.plan, "seed": a.seed, "calls": len(calls),
           "nodes": {"WiCAN": "on CAN1 (the device, the _txabort measurement build)",
                     "canmain": "CAN1's Innomaker, held for libusb; opened only inside each hold (the ACKing node)",
                     "Kvaser": "CAN1, link down (kvaser_link_start)", "PCAN": "CAN1, PASSIVE, the witness",
                     "X2": "CAN1, held in ROM (x2_pins_start, x2_pins_end)", "canchg": "the X2's CAN2; never opened"},
           "hold": {"hold_ms": HOLD_MS, "node_up_s": NODE_UP_S, "node_open_s": NODE_OPEN_S,
                    "node_min_up_s": NODE_MIN_UP_S, "node_down_s": NODE_DOWN_S},
           "x2_pins_start": NB.x2_pins(runs), "rows": []}
    try:    # canctl down, read back not up; it raises otherwise
        rec["kvaser_link_start"] = {"down": True, "readback": str(KB.link_down())}
    except KB.KvaserLinkError as e:
        rec["kvaser_link_start"] = {"down": False, "error": str(e)}
    held = KB.innomaker_held("canmain")
    inno = held.__enter__()
    pc = None
    rc = 0
    try:
        pc = KB.pcan_bus(bitrate=500000, state=can.BusState.PASSIVE)
        wit = NB.Witness(pc)
        wit.start()
        m0 = wit.mark()
        time.sleep(QUIET_S)
        rec["quiet_frames"] = len(wit.since(m0))
        refused = start_refusal(rec)
        if refused:
            print("REFUSED: %s" % refused)
            rec["verdict"] = "REFUSED: %s" % refused
            rc = 2
        else:
            import gi_wifi as GW                 # the hotspot through the core's Wi-Fi, as settings.ota chooses
            import lowid_flood_skip_bench as LF  # the status page (uptime_ms, reset_reason)
            try:
                wcfg = GW.from_env()
                wbe = GW.backend(wcfg)
            except GW.Refused as e:
                wcfg = wbe = None
                rec["wifi"] = "no rejoin: %s" % e
            if wcfg is not None:
                ok, why = GW.join(wcfg, be=wbe)
                rec["wifi"] = "join at the start: %s, %s" % ("ok" if ok else "FAILED", why)
            last_page, t_page = LF.page(), time.perf_counter()
            rec["page_start"] = page_brief(last_page)
            failed_calls = 0
            for i, call in enumerate(calls):
                ans, st, head, msgs, node, t_send = run_call(call, wit, inno)
                if ans is None:
                    row = {"label": call["label"], "kind": call["kind"], "call": call,
                           "error": "HTTP %s: %s" % (st, head)}
                    if st is None:
                        # no answer at all (bench run 797b: the answer never came and the hotspot link was gone):
                        # rejoin, then read the page to see whether the device restarted
                        failed_calls += 1
                        rv = {}
                        if wcfg is not None:
                            ok, why = GW.join(wcfg, be=wbe)
                            rv["join"] = "%s, %s" % ("ok" if ok else "FAILED", why)
                        p, tp = LF.page(), time.perf_counter()
                        rv["page"] = page_brief(p)
                        rv["restart"] = restart_seen(last_page, t_page, p, tp)
                        row["recovery"] = rv
                        if p:
                            last_page, t_page = p, tp
                else:
                    row = process_call(call, ans, msgs, node)
                    bad = echo_mismatch(call, ans)
                    if bad:
                        row["error"] = "echo: " + "; ".join(bad)
                    row["answer"] = ans
                row["t_send"] = t_send
                row["witness"] = [(round(p, 6), round(hw, 1), i_, d, h, e) for p, hw, i_, d, h, e, _ep in msgs]
                row["node"] = node
                rec["rows"].append(row)
                print("%3d/%d %-12s %s%s" % (i + 1, len(calls), call["label"], row.get("error") or _line(row),
                                             ("; recovery %s" % row["recovery"]) if "recovery" in row else ""))
                sys.stdout.flush()
                if stop_now(failed_calls):
                    rec["stopped"] = "after %d calls with no answer, at call %d of %d" % (failed_calls, i + 1, len(calls))
                    print("STOPPED: %s" % rec["stopped"])
                    break
                time.sleep(0.05)
            wit.stop.set()
            wit.join(3)
            rec["witness_states"] = [(round(t, 6), s) for t, s in wit.states]
            rec["tables"] = tables(rec["rows"])
            failed = control_failed(rec["tables"])
            rec["control"] = "FAILED" if failed else "passed"
            rec["page_end"] = page_brief(LF.page())
            rc = 1 if failed or rec["tables"]["errors"] or rec.get("stopped") else 0
    finally:
        if pc is not None:
            try:
                rec["pcan_status_end"] = pc.status_string()
            except Exception:
                pass
            pc.shutdown()
        held.__exit__(*sys.exc_info())
    rec["x2_pins_end"] = NB.x2_pins(runs)
    path = os.path.join(runs, "unacked_bench_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, default=str)
    if "tables" in rec:
        print(json.dumps(rec["tables"], indent=1, default=str))
        print("control: %s" % rec["control"])
    print("wrote %s; exit %d" % (path, rc))
    return rc


def _line(row):
    s = row.get("summary") or {}
    ab = s.get("abort")
    h = row.get("hold")
    bits = ["attempts %s (device %s), copies %s, stack %s%s" % (
        len(s.get("attempts") or []), s.get("device_attempts"), s.get("copies_in_trace"), row.get("stack_free_min"),
        " LOW" if row.get("stack_low") else "")]
    if ab:
        bits.append("abort %s TEC %s freed %s after %s" % (ab["state"], ab["tec"], ab["freed"], ab["copies_after"]))
    if h:
        bits.append("hold %s" % ("VOID " + h["void"] if h.get("void") else
                                 "%s out %s" % (h.get("expect"), h.get("out"))))
    return "; ".join(bits)


if __name__ == "__main__":
    sys.exit(main())
