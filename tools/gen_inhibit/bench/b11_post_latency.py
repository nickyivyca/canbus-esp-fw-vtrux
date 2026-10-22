"""B11 quiesce: each mode-change POST timed against the wire (reviewer, 2026-10-17).

Gen-inhibit tester. Desk only, from the e4 records (no new run). For every POST in a
quiesce arm (--post-schedule):
  - send -> reply: the POST's own duration on the host clock (post_s). On the B11
    arms that is a urllib request: connect, send and reply together, so Wi-Fi and
    the device are not separable inside it. Arms that POSTed raw (post_raw, the
    887c000 mode_change runs) also give connect, send -> first byte and the body.
  - the wire: the POST's send time on the witness clock is the wire time of the
    replayed frame at the POST's replay index (as quiesce_analysis.py). The reply
    time is placed at send + the POST's duration (same rate on both clocks).
  - OBSERVE (mode 1): the device's last 0x051 and last frame of any of its ids after
    the send, before the next POST -> send -> last and reply -> last. Negative
    reply -> last means the device's last frame came before the reply.
  - back to INHIBIT (mode 3): the first device 0x051 after the send; PASSIVE (mode
    4): the first 0x7F1 page with inhibit_live (only as fine as the ~1.2 s diag page).
  - the device's 0x051 period just before the send (median gap of its last 20),
    so a "last frame" can be read against the frame it would have sent next.
  - the raw reply's page, where there is one: uptime_ms, mode, inhibit_live, arm_block.

    py -3.14 b11_post_latency.py runs/<e4 record>.json [...]
"""
import bisect
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
WICAN_IDS = (0x051, 0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)


def ms(x):
    return None if x is None else round(x * 1e3, 1)


def raw_timing(e):
    """connect, send -> first byte, send -> complete (ms) and the reply page fields,
    from a raw POST record; None where the POST was not raw."""
    r = e.get("raw")
    if not isinstance(r, dict) or r.get("t_sent") is None:
        return None
    page = {}
    body = r.get("raw") or ""
    i = body.find("\r\n\r\n")
    if i >= 0:
        try:
            p, _ = json.JSONDecoder().raw_decode(body[i + 4:].lstrip())
            page = {k: p.get(k) for k in ("uptime_ms", "mode", "inhibit_live", "arm_block", "worker")}
        except Exception:
            page = {}
    sub = lambda a, b: ms(r[a] - r[b]) if r.get(a) is not None and r.get(b) is not None else None
    return {"connect_ms": sub("t_connected", "t_start"), "send_to_first_byte_ms": sub("t_first_byte", "t_sent"),
            "send_to_complete_ms": sub("t_complete", "t_sent"), "reply_page": page}


def post_rows(posts, dev_cmd, dev_any, live_pages, end):
    """One row per POST from (wire send time, mode, schedule entry). Pure."""
    rows = []
    for k, (t0, mode, e) in enumerate(posts):
        t_next = posts[k + 1][0] if k + 1 < len(posts) else end
        ps = e.get("post_s") or [None, None]
        dur = ps[1] - ps[0] if None not in ps else None
        raw = raw_timing(e)
        # the raw POST's own reply is the first byte; urllib's is post_s[1]
        if raw and raw["send_to_first_byte_ms"] is not None:
            reply_after = raw["send_to_first_byte_ms"] / 1e3
        else:
            reply_after = dur
        before = dev_cmd[max(0, bisect.bisect_left(dev_cmd, t0) - 21):bisect.bisect_left(dev_cmd, t0)]
        period = statistics.median(b - a for a, b in zip(before, before[1:])) if len(before) > 2 else None
        row = {"at_s": e.get("at_s"), "mode": mode, "http": e.get("http"), "send_to_reply_ms": ms(dur),
               "raw": raw, "period_0x051_before_ms": ms(period)}
        if mode == 1:
            c = [t for t in dev_cmd if t0 <= t < t_next]
            a = [t for t in dev_any if t0 <= t < t_next]
            row["last_0x051_after_send_ms"] = ms(c[-1] - t0) if c else None
            row["last_frame_after_send_ms"] = ms(a[-1] - t0) if a else None
            row["n_0x051_after_send"] = len(c)
            row["reply_to_last_frame_ms"] = (ms(a[-1] - t0 - reply_after) if a and reply_after is not None else None)
        elif mode == 3:
            i = bisect.bisect_left(dev_cmd, t0)
            f = dev_cmd[i] if i < len(dev_cmd) and dev_cmd[i] < t_next else None
            row["first_0x051_after_send_ms"] = ms(f - t0) if f is not None else None
            row["reply_to_first_0x051_ms"] = (ms(f - t0 - reply_after) if f is not None and reply_after is not None
                                              else None)
        elif mode == 4:
            i = bisect.bisect_left(live_pages, t0)
            f = live_pages[i] if i < len(live_pages) and live_pages[i] < t_next else None
            row["first_live_page_after_send_ms"] = ms(f - t0) if f is not None else None
        rows.append(row)
    return rows


def analyse(run):
    import provenance_classifier as P
    d = json.load(open(run, encoding="utf-8"))
    arm = list(d["arms"])[0]
    a = d["arms"][arm]
    r = P.rejudge(run, arm, keep_frames=True)
    rep = r["replayed_wire_times"]
    wire = P.read_witness(os.path.join(os.path.dirname(run), a["witness_raw"]))
    dev_cmd = sorted(r["device_times"])
    dev_any = sorted(set(dev_cmd) | {t for t, i, _d in wire if i in WICAN_IDS and i != 0x051})
    live_pages = sorted(t for t, i, dd in wire if i == 0x7F1 and len(dd) > 3 and dd[3] & 0x20)
    posts = [(rep[e["index"]], e["mode"], e) for e in (a.get("post_schedule") or []) if e.get("index") is not None]
    return {"run": os.path.basename(run), "device": d.get("device"), "initial_mode": d.get("mode"),
            "posts": post_rows(posts, dev_cmd, dev_any, live_pages, rep[-1] + 0.5)}


def main():
    for run in sys.argv[1:]:
        res = analyse(run)
        print("== %s (%s, initial %s)" % (res["run"], res["device"], res["initial_mode"]))
        for p in res["posts"]:
            print("  " + json.dumps(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
