#!/usr/bin/env python3
"""Tests reply_golive_report.py on a synthetic run, witness log and packet list.

    py -3.14 test_reply_golive_report.py <scratch dir>     # exits 1 on any failure

Staged: VCM 0x051 every 10 ms; the device trailing each by 0.5 ms only from a
known instant; a POST sent between two VCM frames. The report must place the
device's first frame at the right latency with the right VCM count before it, not
count a VCM frame as the device's, give the chained dwell from the previous reply's
last byte, and in the capture find the device's FIN, the SYN before it, and which
streams were open at the send.
"""

import gzip
import io
import json
import os
import sys
from contextlib import redirect_stdout

import reply_golive_report as R

FAILS = []


def check(label, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" else ""))
    if not cond:
        FAILS.append(label)


def raw(t_sent, epoch_sent, port, chunks, ending="fin", failed=False):
    return {"t_sent": t_sent, "epoch_sent": epoch_sent, "local_port": port, "chunks": chunks,
            "t_first_byte": chunks[0][0] if chunks else None,
            "t_complete": chunks[-1][0] if chunks and not failed else None,
            "t_end": (chunks[-1][0] if chunks else t_sent) + 0.01, "ending": ending,
            "status_line": "HTTP/1.1 200 OK" if chunks else None,
            "http_client": {"result": "IncompleteRead" if failed else "ok"},
            "content_length": 500, "body_len": 300 if failed else 500,
            "body_json_ok": not failed, "raw_len": 600 if chunks else 0}


def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    E0 = 1800000000.0                # witness epoch at replay base
    live_from = E0 + 20.0234         # device goes live: first trailing frame after this
    lines = []
    for k in range(3000):            # 30 s of VCM at 10 ms
        t = E0 + k * 0.010
        lines.append("(%.6f) can0 051#080080FF7F0F" % t)
        if t > live_from:
            lines.append("(%.6f) can0 051#080080FF7F0F" % (t + 0.0005))
        lines.append("(%.6f) can0 0AA#00" % (t + 0.001))      # not 0x051
        if k % 100 == 0:                                        # the X2's pages, 1/s
            lines.append("(%.6f) can0 7F7#FD7B197F04000000" % (t + 0.002))
            lines.append("(%.6f) can0 7F4#0000000000000000" % (t + 0.003))
    wl = os.path.join(scratch, "w.log.gz")
    with gzip.open(wl, "wt") as f:
        f.write("\n".join(lines) + "\n")

    fr = R.device_frames(wl)
    xc, xb = R.x2_on_wire(wl)
    check("x2: 0x7F7 build id read little-endian as 7f197bfd", xb == {"7f197bfd": 30}, xb)
    check("x2: counts per DBC ID, not 0x0AA", xc == {"0x7F7": 30, "0x7F4": 30}, xc)
    check("device_frames: only 0x051 rows", len(fr) == 3000 + sum(1 for k in range(3000)
                                                                 if E0 + k * 0.010 > live_from), len(fr))
    check("device_frames: no device frame before going live",
          all(not d for t, d in fr if t < live_from))
    send = E0 + 20.0005
    last_dev, first_dev, nv = R.wire_around(fr, send)
    check("wire_around: no device frame before the send", last_dev is None, last_dev)
    check("wire_around: first device frame is the one after 20.03 s",
          first_dev is not None and abs(first_dev - (E0 + 20.0305)) < 1e-6, first_dev and first_dev - E0)
    check("wire_around: 3 VCM frames before it (20.01, 20.02, 20.03)", nv == 3, nv)

    # A run with a chained pair (mode 0 then 3) and a failed single POST.
    p0 = raw(20.0, E0 + 20.0, 50001, [[20.030, 400], [20.040, 200]])
    p1 = raw(20.0453, E0 + 20.0453, 50002, [[20.1, 300]], failed=True)
    p1["chunks"] = [[20.1, 300]]
    run = {"mode": "inhibit", "post_schedule": [[20.0, 0], [20.0, 3]],
           "arms": {"idle": {"pre": {"uptime_ms": 100000, "page_len": 500, "mode": 3, "device_sockets": []},
                             "device": {"uptime_ms": 130000}, "post_chain": True,
                             "witness_raw": "w.log.gz",
                             "post_schedule": [
                                 {"mode": 0, "http": 200, "raw": p0, "reply_failed": False},
                                 {"mode": 3, "http": 200, "raw": p1, "reply_failed": True,
                                  "followup": raw(20.2, E0 + 20.2, 50003, [[20.25, 500]])}]}}}
    p0["raw"] = 'HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n{"mode":0,"inhibit_live":false,"uptime_ms":7}'
    p1["raw"] = 'HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n{"mode":3,"inhibit_li'
    rp = os.path.join(scratch, "reply_golive_off3_test_x_arm1.json")
    json.dump(run, open(rp, "w"))

    # Packets: 50001 clean; 50002 device data then FIN, after another SYN (50003).
    ev = [
        {"t": E0 + 20.0, "from_device": False, "port": 50001, "syn": True, "ack": False, "fin": False, "rst": False, "len": 0, "retx": False},
        {"t": E0 + 20.03, "from_device": True, "port": 50001, "syn": False, "ack": True, "fin": False, "rst": False, "len": 600, "retx": False},
        {"t": E0 + 20.041, "from_device": True, "port": 50001, "syn": False, "ack": True, "fin": True, "rst": False, "len": 0, "retx": False},
        {"t": E0 + 20.045, "from_device": False, "port": 50002, "syn": True, "ack": False, "fin": False, "rst": False, "len": 0, "retx": False},
        {"t": E0 + 20.046, "from_device": True, "port": 50002, "syn": True, "ack": True, "fin": False, "rst": False, "len": 0, "retx": False},
        {"t": E0 + 20.1, "from_device": True, "port": 50002, "syn": False, "ack": True, "fin": False, "rst": False, "len": 300, "retx": False},
        {"t": E0 + 20.15, "from_device": False, "port": 50009, "syn": True, "ack": False, "fin": False, "rst": False, "len": 0, "retx": False},
        {"t": E0 + 20.16, "from_device": True, "port": 50002, "syn": False, "ack": True, "fin": True, "rst": False, "len": 0, "retx": False},
        {"t": E0 + 20.2, "from_device": False, "port": 50003, "syn": True, "ack": False, "fin": False, "rst": False, "len": 0, "retx": False},
    ]
    ss = R.stream_summary(ev, 50002)
    check("stream: device FIN time", ss["device_fin"] is not None and abs(ss["device_fin"] - (E0 + 20.16)) < 1e-9,
          ss["device_fin"])
    check("stream: device last data", abs(ss["device_last_data"] - (E0 + 20.1)) < 1e-9)
    check("stream: device bytes 300", ss["device_bytes"] == 300, ss["device_bytes"])
    check("stream: no RST", ss["device_rst"] is None)
    check("open at 50002's send: itself only (50001 closed)", R.open_streams_at(ev, E0 + 20.0453) == [50002],
          R.open_streams_at(ev, E0 + 20.0453))
    check("open at 20.17: 50009 only", R.open_streams_at(ev, E0 + 20.17) == [50009],
          R.open_streams_at(ev, E0 + 20.17))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rows = R.report(rp, ev)
    out = buf.getvalue()
    check("report: two POST rows", len(rows) == 2, len(rows))
    check("report: dwell from the last byte of the mode-0 reply (20.040) to 20.0453 = 5.3 ms",
          rows[1].get("dwell_ms") == 5.3, rows[1].get("dwell_ms"))
    check("report: no dwell on the first POST", "dwell_ms" not in rows[0])
    check("report: go-live of the mode-3 POST: first device frame after 20.0453 is 20.0505",
          rows[1].get("golive_ms") == 5.2, rows[1].get("golive_ms"))
    check("report: reply page fields from a body that parses",
          rows[0].get("reply_page") == {"mode": 0, "inhibit_live": False, "uptime_ms": 7,
                                        "abort_reason": None, "arm_block": None}, rows[0].get("reply_page"))
    check("report: no page fields from a cut body", "reply_page" not in rows[1])
    check("report: failed flag carried", rows[1]["failed"] is True and rows[0]["failed"] is False)
    check("report: follow-up recorded", rows[1].get("followup") == "fin")
    check("report: names the SYN before the device FIN", "other SYNs in the 2 s before it: [50001, 50009]" in out
          or "other SYNs in the 2 s before it: [50009, 50001]" in out, [l for l in out.splitlines() if "SYNs" in l])
    check("report: reboot no", "reboot: no" in out)
    opn = [l.strip() for l in out.splitlines() if "other open streams" in l]
    check("report: other open streams at each send exclude the POST's own",
          opn == ["other open streams in the capture at the send: []"] * 2, opn)
    run["arms"]["idle"]["device"]["uptime_ms"] = 5000
    json.dump(run, open(rp, "w"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        R.report(rp, ev)
    check("report: a lower end uptime is a reboot", "reboot: YES" in buf.getvalue())

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
