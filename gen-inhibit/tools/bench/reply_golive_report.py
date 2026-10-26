#!/usr/bin/env python3
"""Per-arm report for the missing INHIBIT reply (reviewer, 2026-10-14).

    py -3.14 reply_golive_report.py runs/reply_golive_<stamp>_*.json [--pcap runs/<x>.pcapng]

For each scheduled POST in each e4_device_calibration.py run made with
--post-raw-timeout:

  reply     send time, first byte, completion, how it ended, what urllib would have
            raised, Content-Length against the body received, whether the body
            parses as JSON, body size against the page size read before the replay
  follow-up the fresh GET after a failed reply: answered or not, and when
  wire      the device's last 0x051 before the send and first after it, on the
            Kvaser's clock: the go-live latency and how many VCM commands passed
            first, which says whether it went live at once or waited for fresh
            signals. A device 0x051 is one that trails the previous 0x051 by under
            TRAIL_MAX_US, as e4_device_calibration.py attributes them.
  chain     the OFF dwell: the mode-0 reply's last byte to the mode-3 send
  uptime    before the replay and at the end read; a reboot shows as a drop
  sockets   the host's connections to the device before and after the POST

With --pcap (a tshark capture of the WiCAN's TCP traffic): per POST connection, the
device's SYN-ACK, data, FIN and RST times against the last body byte, and every
other connection's SYN within 2 s before the device's FIN or RST, which is what the
device's LRU purge would follow. Streams to port 80 still open at each POST are
counted from the capture, which sees only connections it saw start.
"""

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

TRAIL_MAX_US = 3000.0
TSHARK = r"C:\Program Files\Wireshark\tshark.exe"
DEVICE = "192.168.80.1"
LINE = re.compile(r"^\((\d+\.\d+)\) \S+ 051#([0-9A-Fa-f]*)")


def device_frames(path):
    """[(t, is_device)] for every 0x051 in a witness log, in time order."""
    out = []
    with gzip.open(path, "rt") as f:
        for ln in f:
            m = LINE.match(ln)
            if m:
                out.append(float(m.group(1)))
    out.sort()
    res, prev = [], None
    for t in out:
        res.append((t, prev is not None and (t - prev) * 1e6 < TRAIL_MAX_US))
        prev = t
    return res


X2_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7, 0x18EFC0E1, 0x18FFD7E1)   # vtrux-interposer-diag.dbc
X2_LINE = re.compile(r"^\((\d+\.\d+)\) \S+ ([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")


def x2_on_wire(path):
    """The X2's frames in a witness log (bench-holder rule 6): count per DBC ID, and
    the build ids its 0x7F7 carried (B0-B3 as a little-endian u32, spec 8.2)."""
    counts, builds = defaultdict(int), defaultdict(int)
    with gzip.open(path, "rt") as f:
        for ln in f:
            m = X2_LINE.match(ln)
            if not m:
                continue
            i = int(m.group(2), 16)
            if i not in X2_IDS:
                continue
            counts["0x%X" % i] += 1
            if i == 0x7F7 and len(m.group(3)) >= 8:
                b = bytes.fromhex(m.group(3)[:8])
                builds["%08x" % int.from_bytes(b, "little")] += 1
    return dict(counts), dict(builds)


def wire_around(frames, t_send):
    last_dev = None
    for t, dev in frames:
        if t >= t_send:
            break
        if dev:
            last_dev = t
    first_dev, vcm_between = None, 0
    for t, dev in frames:
        if t < t_send:
            continue
        if dev:
            first_dev = t
            break
        vcm_between += 1
    return last_dev, first_dev, vcm_between


def pcap_events(pcap):
    fields = ["frame.time_epoch", "ip.src", "tcp.srcport", "tcp.dstport", "tcp.flags.syn",
              "tcp.flags.ack", "tcp.flags.fin", "tcp.flags.reset", "tcp.len",
              "tcp.analysis.retransmission"]
    cmd = [TSHARK, "-r", pcap, "-Y", "ip.addr==%s && tcp.port==80" % DEVICE, "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    txt = subprocess.run(cmd, capture_output=True, text=True).stdout
    ev = []
    for ln in txt.splitlines():
        p = ln.split("\t")
        if len(p) < 9:
            continue
        dev = p[1] == DEVICE
        cport = int(p[3] if dev else p[2])
        ev.append({"t": float(p[0]), "from_device": dev, "port": cport,
                   "syn": p[4] in ("1", "True"), "ack": p[5] in ("1", "True"),
                   "fin": p[6] in ("1", "True"), "rst": p[7] in ("1", "True"),
                   "len": int(p[8] or 0), "retx": bool(p[9]) if len(p) > 9 else False})
    return ev


def stream_summary(ev, port):
    s = [e for e in ev if e["port"] == port]
    d = [e for e in s if e["from_device"]]
    c = [e for e in s if not e["from_device"]]
    first = lambda xs: xs[0]["t"] if xs else None
    return {
        "client_syn": first([e for e in c if e["syn"]]),
        "device_synack": first([e for e in d if e["syn"]]),
        "client_data": first([e for e in c if e["len"] > 0]),
        "device_first_data": first([e for e in d if e["len"] > 0]),
        "device_last_data": ([e for e in d if e["len"] > 0] or [{"t": None}])[-1]["t"],
        "device_bytes": sum(e["len"] for e in d),
        "device_fin": first([e for e in d if e["fin"]]),
        "device_rst": first([e for e in d if e["rst"]]),
        "client_fin": first([e for e in c if e["fin"]]),
        "client_rst": first([e for e in c if e["rst"]]),
        "retransmissions": sum(1 for e in s if e["retx"]),
    }


def open_streams_at(ev, t):
    """Ports whose client SYN the capture saw before t and whose close it had not."""
    syn, closed = {}, set()
    for e in ev:
        if e["t"] >= t:
            break
        if e["syn"] and not e["from_device"]:
            syn[e["port"]] = e["t"]
        if e["fin"] or e["rst"]:
            closed.add(e["port"])
    return sorted(p for p in syn if p not in closed)


def body_fields(raw):
    """mode, inhibit_live, uptime_ms and abort_reason from a reply body that parses:
    whether a late reply's page is a snapshot from after going live."""
    if not raw or "\r\n\r\n" not in raw:
        return None
    body = raw.split("\r\n\r\n", 1)[1]
    try:
        d, _ = json.JSONDecoder().raw_decode(body.lstrip())
    except ValueError:
        return None
    if not isinstance(d, dict):
        return {"json": type(d).__name__}
    return {k: d.get(k) for k in ("mode", "inhibit_live", "uptime_ms", "abort_reason", "arm_block")}


def fmt(x, ref=None, unit=1000.0):
    if x is None:
        return "-"
    return "%+.1f ms" % ((x - ref) * unit) if ref is not None else "%.4f" % x


def report(path, ev):
    d = json.load(open(path, encoding="utf-8"))
    a = d["arms"].get("idle") or next(iter(d["arms"].values()))
    name = os.path.basename(path)
    pre = a.get("pre") or {}
    dev = a.get("device") or {}
    rows = []
    print("\n=== %s  (mode %s, schedule %s, chain %s)" % (name, d.get("mode"), d.get("post_schedule"),
                                                        a.get("post_chain")))
    print("  uptime before %s ms (page %s bytes, mode %s), at the end read %s ms; reboot: %s"
          % (pre.get("uptime_ms"), pre.get("page_len"), pre.get("mode"), dev.get("uptime_ms"),
             "YES" if (pre.get("uptime_ms") and dev.get("uptime_ms") is not None
                       and dev["uptime_ms"] < pre["uptime_ms"]) else "no"))
    print("  host sockets to the device before the replay: %s" % (pre.get("device_sockets"),))
    wpath = os.path.join(os.path.dirname(path), a.get("witness_raw") or "")
    frames = device_frames(wpath) if a.get("witness_raw") and os.path.exists(wpath) else None
    if frames is not None:
        xc, xb = x2_on_wire(wpath)
        print("  X2 on the wire (rule 6): frames %s, 0x7F7 build ids %s" % (xc, xb))
    prev_raw = None
    for i, e in enumerate(a.get("post_schedule") or []):
        r = e.get("raw")
        if r is None:
            print("  POST %d mode %s: no raw record (exception: %s)" % (i, e.get("mode"), e.get("exception")))
            continue
        ts = r["t_sent"]
        row = {"run": name, "i": i, "mode": e["mode"], "ending": r["ending"],
               "http_client": r["http_client"]["result"], "status": e.get("http"),
               "first_byte_ms": None if r["t_first_byte"] is None else round((r["t_first_byte"] - ts) * 1e3, 1),
               "complete_ms": None if r["t_complete"] is None else round((r["t_complete"] - ts) * 1e3, 1),
               "end_ms": None if r["t_end"] is None else round((r["t_end"] - ts) * 1e3, 1),
               "content_length": r["content_length"], "body_len": r["body_len"],
               "json_ok": r["body_json_ok"], "failed": e.get("reply_failed")}
        print("  POST %d mode %d at %.3f s: %s, first byte %s, complete %s, end %s, ending %s, "
              "urllib %s" % (i, e["mode"], ts, r["status_line"], fmt(r["t_first_byte"], ts),
                             fmt(r["t_complete"], ts), fmt(r["t_end"], ts), r["ending"],
                             r["http_client"]["result"]))
        print("    Content-Length %s, body %s bytes (page before %s), JSON %s, chunks %s%s"
              % (r["content_length"], r["body_len"], pre.get("page_len"), r["body_json_ok"],
                 r["chunks"], "   REPLY FAILED" if e.get("reply_failed") else ""))
        bj = body_fields(r.get("raw"))
        if bj:
            row["reply_page"] = bj
            print("    reply body: %s" % (bj,))
        if prev_raw is not None and a.get("post_chain"):
            last_byte = prev_raw["chunks"][-1][0] if prev_raw["chunks"] else prev_raw["t_end"]
            row["dwell_ms"] = round((ts - last_byte) * 1e3, 1)
            print("    dwell since the previous reply's last byte: %.1f ms" % row["dwell_ms"])
        f = e.get("followup")
        if f is not None:
            print("    follow-up GET at %+.1f ms: %s, first byte %s, ending %s, %s bytes, JSON %s"
                  % ((f["t_sent"] - ts) * 1e3 if f["t_sent"] is not None else float("nan"),
                     f["status_line"], fmt(f["t_first_byte"], f["t_sent"]), f["ending"],
                     f["raw_len"], f["body_json_ok"]))
            row["followup"] = f["ending"]
        print("    sockets before %s after %s" % (e.get("sockets_before"), e.get("sockets_after")))
        if frames is not None and r.get("epoch_sent"):
            last_dev, first_dev, nv = wire_around(frames, r["epoch_sent"])
            row["golive_ms"] = None if first_dev is None else round((first_dev - r["epoch_sent"]) * 1e3, 1)
            row["vcm_before_live"] = nv
            print("    wire: last device 0x051 %s, first after the send %s (%d VCM 0x051 before it)"
                  % (fmt(last_dev, r["epoch_sent"]), fmt(first_dev, r["epoch_sent"]), nv))
            if r["t_first_byte"] is not None and first_dev is not None:
                fb_epoch = r["epoch_sent"] + (r["t_first_byte"] - ts)
                print("    reply's first byte is %s the first device 0x051"
                      % ("AFTER" if fb_epoch > first_dev else "before"))
        if ev is not None and r.get("local_port"):
            ss = stream_summary(ev, r["local_port"])
            ref = ss["device_last_data"] or r["epoch_sent"]
            print("    pcap port %s: synack %s, device data %s..%s (%d bytes), device FIN %s, RST %s, "
                  "client FIN %s, retx %d  [vs send]"
                  % (r["local_port"], fmt(ss["device_synack"], r["epoch_sent"]),
                     fmt(ss["device_first_data"], r["epoch_sent"]), fmt(ss["device_last_data"], r["epoch_sent"]),
                     ss["device_bytes"], fmt(ss["device_fin"], r["epoch_sent"]),
                     fmt(ss["device_rst"], r["epoch_sent"]), fmt(ss["client_fin"], r["epoch_sent"]),
                     ss["retransmissions"]))
            close = ss["device_fin"] or ss["device_rst"]
            if close is not None:
                others = sorted({x["port"] for x in ev if x["syn"] and not x["from_device"]
                                 and x["port"] != r["local_port"] and close - 2.0 <= x["t"] <= close})
                print("    device %s %s after its last data byte; other SYNs in the 2 s before it: %s"
                      % ("FIN" if ss["device_fin"] else "RST", fmt(close, ss["device_last_data"]), others))
            print("    other open streams in the capture at the send: %s"
                  % ([x for x in open_streams_at(ev, r["epoch_sent"]) if x != r["local_port"]],))
            row["pcap"] = ss
        rows.append(row)
        prev_raw = r
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--pcap")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    ev = pcap_events(a.pcap) if a.pcap else None
    if ev is not None:
        print("capture: %d packets to/from %s:80" % (len(ev), DEVICE))
    rows = []
    for p in a.runs:
        rows += report(p, ev)
    print("\n== summary")
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[(r["run"].split("_")[2], r["mode"])][0] += 1
        by[(r["run"].split("_")[2], r["mode"])][1] += 1 if r["failed"] else 0
    for k, (n, f) in sorted(by.items()):
        print("  %-10s POST mode %d: %d, failed %d" % (k[0], k[1], n, f))
    if a.json_out:
        json.dump(rows, open(a.json_out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
