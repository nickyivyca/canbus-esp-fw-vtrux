"""B11raw: spec 3's mode change judged in the request-to-reply window, per raw POST (spec 3, user
2026-11-19, f7865461: on a live bus a mode change is applied between frames, before the setter
replies, and no frame received before it is left queued for the new mode; checked between the
request leaving the client and the reply reaching it, with the witness and the device's receive
queue showing no backlog; the exact frame it lands on is not judged).

Gen-inhibit tester, topic (E), 2026-11-19, on the gen-inhibit reviewer's readings (m)-(r) (answer to
b07f7984). Until then this judged the request's arrival on the air from a Wi-Fi capture (the
reviewer's readings of 2026-10-17); that judgement and the capture are gone. Input: e4 records from
`baseline_b_noX2.sh B11raw` (--post-raw-timeout, so each POST keeps raw_http_probe's record, and a
page read right after a whole reply, `page_after`).

Clocks. raw_http_probe's times are on the replay's clock (s from its base). They are placed on the
witness by the replay's own frames near the POST: the median of (wire time - scheduled time) over
+/- MAP_FRAMES frames (`offset`). A frame waits behind whatever the adapter has queued, never leaves
early, so the mapping's uncertainty is the median minus the smallest of those (`margin`, recorded per
POST; reading (n)).

Per POST (the window: t_sent, the request handed to the socket, to t_complete, the whole reply in;
its length recorded; reading (m)):
  - ERROR when the reply was cut short or never came: no window.
  - Into OBSERVE (reading (n), and (r): OBSERVE never transmits): a device 0x051 later than
    t_complete + margin, up to the next POST's t_sent, is FAIL; one within the margin after
    t_complete is ERROR. No device 0x051 in the LIVE_BEFORE_S before t_sent: nothing to see on
    the witness, NOT APPLICABLE (the witness half; the mode still judges).
  - Into any other mode (reading (o)): a stale answer is FAIL, a device 0x051 later than
    t_complete + margin answering a VCM 0x051 that reached the wire earlier than t_sent - margin
    (provenance_classifier's device_answers); one within either margin is ERROR. The first answer
    after t_sent and its delay from t_complete are recorded, not judged: going live is the gate's
    (GATE_HELD_S). So is the count of answers inside the window to a command from before t_sent.
  - The mode (reading (p)): the page read right after the reply must show the POSTed mode, else
    FAIL. The queue (reading (q)): that read's drv.msgs_to_rx must be at most QUEUE_BOUND, set from
    quiet data on the same replay with no mode change (the test plan); none set yet is ERROR.
  - A read the harness never sent (a record with no page read after the reply) is VOID; one sent
    and unanswered, or answered without the field, is ERROR (reading (t)).
A POST's verdict is FAIL, then ERROR, then VOID, then PASS over its halves (the witness half's NOT
APPLICABLE counts as neither). An arm void for any spec 12.1 reason (provenance_classifier.rejudge's
arm_void) judges no POST: each reads "VOID: arm void: <reason>"; a reason on no entry ERROR.

    python3.13 -B b11_rawpost_judge.py <e4 record>.json [...]
One line per arm, then one JSON line per POST (`{"at_s": ...}`), which the suite's B11raw reads.
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LIVE_BEFORE_S = 0.100          # a device that was answering before the request (reading (n))
MAP_FRAMES = 200               # replayed frames each side of the POST that place it on the witness
QUEUE_BOUND = None             # drv.msgs_to_rx after the reply, from quiet data (reading (q)); None: not set
MODE_NUMBER = {"off": 0, "observe": 1, "respond": 2, "inhibit": 3, "passive": 4}
PASS, FAIL, ERROR, VOID, NA = "PASS", "FAIL", "ERROR", "VOID", "NOT APPLICABLE"
RANK = [FAIL, ERROR, VOID]


def ms(x):
    return None if x is None else round(x * 1e3, 2)


def worst(vs):
    """FAIL, then ERROR, then VOID, else PASS (NOT APPLICABLE counts as neither)."""
    return next((v for v in RANK if v in vs), PASS)


def mapping(rep, src_t, idx, n=MAP_FRAMES):
    """(offset, margin) placing the replay's clock on the witness near replay index idx: the median
    of wire minus scheduled over +/- n found frames, and that median minus their smallest. Pure."""
    lo, hi = max(0, idx - n), min(len(src_t), idx + n)
    late = [rep[i] - src_t[i] for i in range(lo, hi) if i < len(rep) and rep[i] is not None]
    if not late:
        return None, None
    off = statistics.median(late)
    return off, off - min(late)


def observe_half(ts, tc, t_end, margin, device):
    """Into OBSERVE: (verdict, why) from the device 0x051 times on the witness. Pure."""
    late = [t for t in device if tc + margin < t < t_end]
    if late:
        return FAIL, "a device 0x051 %s ms after the reply (OBSERVE never transmits)" % ms(late[0] - tc)
    near = [t for t in device if tc < t <= tc + margin]
    if near:
        return ERROR, "a device 0x051 %s ms after the reply, within the clock margin %s ms" % (
            ms(near[0] - tc), ms(margin))
    if not any(ts - LIVE_BEFORE_S <= t <= ts for t in device):
        return NA, "no device 0x051 in the %d ms before the request: nothing to see" % (LIVE_BEFORE_S * 1e3)
    return PASS, "no device 0x051 after the reply"


def stale_half(ts, tc, t_end, margin, answers):
    """Into any other mode: (verdict, why, first answer) from [(device t, answered command t)].
    Pure."""
    after = [(t, a) for t, a in answers if t < t_end]
    stale = [(t, a) for t, a in after if t > tc + margin and a is not None and a < ts - margin]
    first = next(((t, a) for t, a in after if t > ts), None)
    rec = {"first_t_rel_reply_ms": None if first is None else ms(first[0] - tc),
           "first_answers_rel_request_ms": None if first is None or first[1] is None else ms(first[1] - ts),
           # recorded, not judged (the reviewer's rule is after the reply): answers inside the window
           # to a command from before the request
           "in_window_answers_to_commands_before_request":
               sum(1 for t, a in after if ts < t <= tc and a is not None and a < ts)}
    if stale:
        t, a = stale[0]
        return FAIL, "a stale answer: a device 0x051 %s ms after the reply answering a VCM 0x051 %s ms before " \
                     "the request" % (ms(t - tc), ms(ts - a)), rec
    near = [(t, a) for t, a in after if t > tc and a is not None and a < ts
            and (t <= tc + margin or a >= ts - margin)]
    if near:
        t, a = near[0]
        return ERROR, "a device 0x051 %s ms after the reply answering a VCM 0x051 %s ms before the request, " \
                      "within the clock margin %s ms" % (ms(t - tc), ms(ts - a), ms(margin)), rec
    return PASS, "no stale answer", rec


def page_half(page, mode):
    """(mode verdict, queue verdict, why) from the page read after the reply (readings (p), (q),
    (t)). Pure."""
    if page is None:
        return VOID, VOID, ["no page read after the reply in the record"]
    if page.get("http") != 200 or page.get("error"):
        return ERROR, ERROR, ["the page read after the reply did not answer (%s)" % (page.get("error") or page.get("http"))]
    why = []
    mv = PASS if page.get("mode") == mode else FAIL
    if mv == FAIL:
        why.append("the page after the reply reads mode %s, not %s" % (page.get("mode"), mode))
    q = page.get("msgs_to_rx")
    if q is None:
        qv = ERROR
        why.append("the page after the reply names no drv.msgs_to_rx")
    elif QUEUE_BOUND is None:
        qv = ERROR
        why.append("msgs_to_rx %s: no bound set from quiet data yet (the test plan)" % q)
    elif q > QUEUE_BOUND:
        qv = FAIL
        why.append("msgs_to_rx %s after the reply, over the bound %s" % (q, QUEUE_BOUND))
    else:
        qv = PASS
    return mv, qv, why


def judge_post(e, prev_mode, ts, tc, t_end, margin, device, answers):
    """One POST's row (the window already on the witness clock: ts, tc; t_end the next POST's ts).
    Pure."""
    raw = e.get("raw") or {}
    row = {"at_s": e.get("at_s"), "mode": e.get("mode"), "from_mode": prev_mode, "http": e.get("http"),
           "window_ms": None, "margin_ms": ms(margin)}
    if raw.get("t_complete") is None or raw.get("t_sent") is None or e.get("http") != 200:
        row.update(verdict=ERROR, why=["the reply was cut short or never came (ending %s, http %s): no window"
                                       % (raw.get("ending"), e.get("http"))])
        return row
    row["window_ms"] = ms(raw["t_complete"] - raw["t_sent"])
    if e.get("mode") == 1:
        wv, wwhy = observe_half(ts, tc, t_end, margin, device)
    else:
        wv, wwhy, first = stale_half(ts, tc, t_end, margin, answers)
        row["answers"] = first
    mv, qv, pwhy = page_half(e.get("page_after"), e.get("mode"))
    page = e.get("page_after") or {}
    row.update(witness=wv, mode_after=page.get("mode"), mode_verdict=mv, msgs_to_rx=page.get("msgs_to_rx"),
               queue_verdict=qv, verdict=worst([wv, mv, qv]), why=[wwhy] + pwhy)
    return row


def judge_run(run):
    import e4_device_calibration as E
    import provenance_classifier as P
    import gi_paths
    d = json.load(open(run, encoding="utf-8"))
    arm = list(d["arms"])[0]
    a = d["arms"][arm]
    sched = a.get("post_schedule") or []
    r = P.rejudge(run, arm, keep_frames=True)
    out = {"run": os.path.basename(run), "device": d.get("device"), "initial_mode": d.get("mode")}
    # void for any spec 12.1 reason (c5849685): nothing judged
    if r.get("arm_void") or r.get("arm_unlisted"):
        why = ("ERROR: a void reason on no spec 12.1 entry: %s" % "; ".join(r["arm_unlisted"])
               if r.get("arm_unlisted") else "VOID: arm void: %s" % "; ".join(r["arm_void"]))
        out.update(void=r.get("arm_void"), posts=[{"at_s": e.get("at_s"), "mode": e.get("mode"), "http": e.get("http"),
                                                   "verdict": why} for e in sched])
        return out
    rep = r["replayed_wire_times"]
    E.CAPTURE = gi_paths.runs(d["capture"])
    E.WINDOW_START = d["window_start"]
    src_t = [f[0] for f in E.load_window(d["seconds"])]
    device = sorted(r["device_times"])
    answers = sorted((t, a_) for t, a_, _d in r["device_answers"])
    end = (max(x for x in rep if x is not None) + 0.5) if any(x is not None for x in rep) else None
    prev = MODE_NUMBER.get(d.get("mode"))
    places = []
    for e in sched:
        off, margin = mapping(rep, src_t, e.get("index") or 0)
        raw = e.get("raw") or {}
        w = (lambda t: None if t is None or off is None else t + off)
        places.append((off, margin, w(raw.get("t_sent")), w(raw.get("t_complete"))))
    posts = []
    for k, e in enumerate(sched):
        off, margin, ts, tc = places[k]
        t_end = next((p[2] for p in places[k + 1:] if p[2] is not None), end)
        if off is None:
            row = {"at_s": e.get("at_s"), "mode": e.get("mode"), "verdict": ERROR,
                   "why": ["no replayed frame near the POST to place it on the witness"]}
        else:
            row = judge_post(e, prev, ts, tc, t_end, margin, device, answers)
        posts.append(row)
        prev = e.get("mode")
    out["posts"] = posts
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    worst_rc = 0
    for run in argv:
        res = judge_run(run)
        print("== %s (%s, initial %s)" % (res["run"], res["device"], res["initial_mode"]))
        for p in res["posts"]:
            print("  " + json.dumps(p))
            if p.get("verdict") != PASS:
                worst_rc = 1
    return worst_rc


if __name__ == "__main__":
    sys.exit(main())
