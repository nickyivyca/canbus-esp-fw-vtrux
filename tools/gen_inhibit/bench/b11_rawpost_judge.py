"""B11 quiesce with raw POSTs and a Wi-Fi capture: spec 3's "the change takes effect at
the next frame", per OBSERVE POST (reviewer's readings, 2026-10-17).

Gen-inhibit tester. Input: an e4 record from `baseline_b_noX2.sh B11raw`
(--post-raw-timeout, so each POST keeps its raw record and local port) and the
unelevated capture of the laptop's Wi-Fi taken around it.

Reviewer's readings (2026-10-17):
  - ARRIVAL is judged strictly on the lower bound: the laptop puts the request's last
    segment on the air. The device's TCP ACK of it is reported beside it. A POST that
    fails strictly but passes with the ACK as arrival is its own category,
    "strict FAIL, PASS on ACK", never folded into either.
  - BY COMMAND COUNT, not by a clock. Allowed after arrival: answers to VCM 0x051s
    that reached the wire before arrival, and the answer to at most ONE VCM 0x051
    after arrival (the wake frame, processed in full before the update point takes
    the request). An answer to a second VCM 0x051 after arrival FAILS.
  - NOT APPLICABLE where the device sent no 0x051 in the 100 ms before arrival (a
    PASSIVE arm): nothing to count, so never a pass.
  - Reported, no verdict: arrival -> the device's last 0x051, the device's 0x051 period
    (median of its last 20 gaps before the arrival), whether another HTTP request was
    in progress at the device when the POST arrived (the server runs one at a time),
    and the back-POSTs with the same timings.

Clocks: capture epoch -> replay time by base_epoch (exact: epoch_sent = base_epoch +
t_sent in the records), replay time -> witness time by the replay's own frames near
the POST (median of wire time minus scheduled time over +/-200 frames).

    py -3.14 b11_rawpost_judge.py <e4 record>.json <capture>.pcapng [...pairs]
"""
import bisect
import json
import os
import statistics
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TSHARK = r"C:\Program Files\Wireshark\tshark.exe"
DEVICE_IP = "192.168.80.1"
STRICT_PASS, STRICT_FAIL, ACK_ONLY, NO_DATA = ("PASS", "FAIL", "strict FAIL, PASS on ACK", "UNDETERMINED")
NOT_APPLICABLE = "NOT APPLICABLE (no device 0x051 in the 100 ms before arrival)"
# A command count needs a device that was answering: PASSIVE sends no 0x051, and an
# OBSERVE POST into it would otherwise pass with nothing to count (2026-10-17).
LIVE_BEFORE_S = 0.100


def ms(x):
    return None if x is None else round(x * 1e3, 2)


def answers_after(arrival, commands, answers, t_end):
    """For each device 0x051 in (arrival, t_end): the number of VCM 0x051 commands that
    reached the wire after arrival and before it -- 0 answers a command from before the
    arrival (held), 1 the wake frame's, 2 or more a later command's. Pure."""
    out = []
    for t in answers:
        if arrival < t < t_end:
            out.append(bisect.bisect_left(commands, t) - bisect.bisect_right(commands, arrival))
    return out


def verdict(arrival, ack, commands, answers, t_end):
    """Per OBSERVE POST, by command count (the reviewer's reading). Pure."""
    if arrival is None:
        return NO_DATA, []
    if not any(arrival - LIVE_BEFORE_S <= t <= arrival for t in answers):
        return NOT_APPLICABLE, []
    k = answers_after(arrival, commands, answers, t_end)
    strict_ok = all(n <= 1 for n in k)
    if strict_ok:
        return STRICT_PASS, k
    if ack is not None and all(n <= 1 for n in answers_after(ack, commands, answers, t_end)):
        return ACK_ONLY, k
    return STRICT_FAIL, k


def request_times(packets, local_port):
    """From the capture rows of one connection (laptop port <local_port>): the epoch of
    the laptop's last request segment before the device's first data, and of the
    device's first ACK covering it. Rows are dicts with t, src, sport, dport, seq, ack,
    len. Pure."""
    conn = [p for p in packets if local_port in (p["sport"], p["dport"])]
    first_reply = min((p["t"] for p in conn if p["src"] == DEVICE_IP and p["len"] > 0), default=None)
    req = [p for p in conn if p["src"] != DEVICE_IP and p["len"] > 0
           and (first_reply is None or p["t"] < first_reply)]
    if not req:
        return None, None
    last = max(req, key=lambda p: (p["seq"] + p["len"], p["t"]))
    end = last["seq"] + last["len"]
    t_req = min(p["t"] for p in req if p["seq"] + p["len"] == end)      # first time on the air
    ack = min((p["t"] for p in conn if p["src"] == DEVICE_IP and p["ack"] >= end and p["t"] >= t_req), default=None)
    return t_req, ack


def in_progress(packets, local_port, t):
    """Other connections with a request on the air before <t> and the device's reply not
    yet finished at <t> (its last data or FIN after t): their laptop ports. Pure."""
    by = {}
    for p in packets:
        port = p["sport"] if p["src"] != DEVICE_IP else p["dport"]
        if port != local_port:
            by.setdefault(port, []).append(p)
    out = []
    for port, ps in sorted(by.items()):
        sent = [p["t"] for p in ps if p["src"] != DEVICE_IP and p["len"] > 0 and p["t"] < t]
        done = [p["t"] for p in ps if p["src"] == DEVICE_IP and (p["len"] > 0 or p["fin"])]
        if sent and (not done or max(done) > t):
            out.append(port)
    return out


def read_capture(pcap):
    cmd = [TSHARK, "-r", pcap, "-Y", "tcp.port==80", "-T", "fields", "-e", "frame.time_epoch", "-e", "ip.src",
           "-e", "tcp.srcport", "-e", "tcp.dstport", "-e", "tcp.seq", "-e", "tcp.ack", "-e", "tcp.len",
           "-e", "tcp.flags.fin"]
    rows = []
    for ln in subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines():
        f = ln.split("\t")
        if len(f) < 8 or not f[0]:
            continue
        rows.append({"t": float(f[0]), "src": f[1], "sport": int(f[2]), "dport": int(f[3]), "seq": int(f[4] or 0),
                     "ack": int(f[5] or 0), "len": int(f[6] or 0), "fin": f[7] in ("1", "True")})
    return rows


def judge_run(run, pcap):
    import e4_device_calibration as E
    import provenance_classifier as P
    d = json.load(open(run, encoding="utf-8"))
    arm = list(d["arms"])[0]
    a = d["arms"][arm]
    r = P.rejudge(run, arm, keep_frames=True)
    rep = r["replayed_wire_times"]
    E.CAPTURE = gi_paths.runs(d["capture"])
    E.WINDOW_START = d["window_start"]
    src = E.load_window(d["seconds"])
    commands = sorted(rep[i] for i, (_t, cid, _d, _x) in enumerate(src) if cid == 0x051 and rep[i] is not None)
    answers = sorted(r["device_times"])
    packets = read_capture(pcap)
    base = a["base_epoch"]
    sched = a.get("post_schedule") or []
    posts = []
    for k, e in enumerate(sched):
        raw = e.get("raw") or {}
        idx = e.get("index")
        lo, hi = max(0, idx - 200), min(len(src), idx + 200)
        off = statistics.median(rep[i] - src[i][0] for i in range(lo, hi) if rep[i] is not None)
        wire = lambda epoch: None if epoch is None else epoch - base + off
        t_req, t_ack = request_times(packets, raw.get("local_port"))
        arr, ack = wire(t_req), wire(t_ack)
        t_end = (wire(base + sched[k + 1]["post_s"][0]) if k + 1 < len(sched) else rep[-1] + 0.5)
        before = [t for t in answers if arr is not None and t < arr][-21:]
        period = statistics.median(y - x for x, y in zip(before, before[1:])) if len(before) > 2 else None
        row = {"at_s": e.get("at_s"), "mode": e.get("mode"), "http": e.get("http"), "local_port": raw.get("local_port"),
               "send_to_first_byte_ms": ms(raw["t_first_byte"] - raw["t_sent"]) if raw.get("t_first_byte") else None,
               "arrival_to_ack_ms": ms(ack - arr) if None not in (arr, ack) else None,
               "period_0x051_ms": ms(period),
               "other_requests_in_progress": in_progress(packets, raw.get("local_port"), t_req) if t_req else None}
        after = [t for t in answers if arr is not None and arr < t < t_end]
        if e.get("mode") == 1:
            v, kk = verdict(arr, ack, commands, answers, t_end)
            row.update({"verdict": v, "answers_after_arrival_by_command": kk,
                        "arrival_to_last_0x051_ms": ms(after[-1] - arr) if after else None,
                        "ack_to_last_0x051_ms": ms(after[-1] - ack) if after and ack is not None else None})
        else:
            row["arrival_to_first_0x051_ms"] = ms(after[0] - arr) if after else None
        posts.append(row)
    return {"run": os.path.basename(run), "capture": os.path.basename(pcap), "device": d.get("device"),
            "initial_mode": d.get("mode"), "packets": len(packets), "posts": posts}


def main():
    args = sys.argv[1:]
    worst = 0
    for run, pcap in zip(args[0::2], args[1::2]):
        res = judge_run(run, pcap)
        print("== %s (%s, initial %s; %d capture packets)" % (res["run"], res["device"], res["initial_mode"],
                                                               res["packets"]))
        for p in res["posts"]:
            print("  " + json.dumps(p))
            if p.get("verdict") in (STRICT_FAIL, NO_DATA):
                worst = 1
            elif p.get("verdict") == ACK_ONLY:
                worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
