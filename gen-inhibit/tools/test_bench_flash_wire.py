#!/usr/bin/env python3
"""Tests bench_flash_wire.py: the failed-upload line read from a capture, and the
capture's start refusing (never raising) on a host without what it needs. Rows
are faked; no capture, no network, no device.

Gen-inhibit tester, 2026-10-17 (reviewer, approved by the user). The first
fixture has the shape of the 2026-10-17 00:21 upload reset: the device ACKs
20,391 with a 90-byte window and then nothing on the upload until a 408.
Addresses are documentation ones (RFC 5737).

    py -3.14 test_bench_flash_wire.py     # exit 0 only if every check passes
Its mutants: test_bench_flash_wire_mutants.py.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_flash_wire as W      # noqa: E402

FAILS = []
DEV, LAP = "192.0.2.1", "192.0.2.2"


def check(label, fn, detail=""):
    """fn is called here, so a crash in the code under test is this check's FAIL."""
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def line_of(s):
    """W.wire_line(s) for a check's detail or a line read by several checks: both are worked out
    outside the check, so a crash there reads as its text, failing the checks that read it,
    instead of ending the test (bench_flash_wire mutant m18, 2026-10-30)."""
    try:
        return W.wire_line(s)
    except Exception as e:      # noqa: BLE001
        return "%s: %s" % (type(e).__name__, e)


def pkt(t, src, stream, ack=0, win=0, ln=0, syn=False, ackf=True, fin=False, rst=False, payload=b""):
    return {"t": t, "src": src, "stream": stream, "syn": syn, "ackf": ackf, "fin": fin, "rst": rst,
            "ack": ack, "win": win, "len": ln or len(payload), "payload": payload.hex()}


def header(content_length):
    return (b"POST /upload/ota.bin HTTP/1.1\r\nHost: device\r\nContent-Length: %d\r\n"
            b"Content-Type: multipart/form-data\r\n\r\n" % content_length)


HDR = header(1113226)


T_START = 100.0
SYN = 100.2


def stalled():
    """The 00:21 shape: a page read on stream 2, the upload on stream 3."""
    rows = [pkt(99.0, LAP, 2, syn=True, ackf=False), pkt(99.1, LAP, 2, ln=127),
            pkt(99.2, DEV, 2, ack=128, win=5760, ln=900), pkt(112.0, DEV, 2, ack=128, win=5760),
            pkt(SYN, LAP, 3, syn=True, ackf=False), pkt(100.21, DEV, 3, syn=True, ack=1, win=5760),
            pkt(100.25, LAP, 3, payload=HDR)]
    rows += [pkt(100.3 + 0.01 * k, LAP, 3, ln=1440) for k in range(14)]
    rows += [pkt(100.4, DEV, 3, ack=2881, win=5760), pkt(101.0, DEV, 3, ack=10000, win=5760),
             pkt(106.3, DEV, 3, ack=20391, win=90),
             pkt(212.89, DEV, 3, ack=20391, win=5760, payload=b"HTTP/1.1 408 Request Timeout\r\n\r\n"),  # data, not an ACK
             pkt(213.0, DEV, 3, ack=20391, win=5760, fin=True)]             # its bare FIN, not an ACK
    return rows


def link_lost():
    rows = [pkt(SYN, LAP, 3, syn=True, ackf=False)]
    rows += [pkt(100.3 + 0.01 * k, LAP, 3, ln=1440) for k in range(14)]
    rows += [pkt(100.4, DEV, 3, ack=2881, win=5760), pkt(100.9, DEV, 3, ack=8641, win=5760),
             pkt(103.0, DEV, 3, ack=14401, win=4320)]
    return rows


def main():
    s = W.wire_summary(stalled(), DEV, T_START, 116.69)
    check("the upload is the stream the laptop sent most payload on", lambda: s["stream"] == 3, s.get("stream"))
    check("highest ack and the bytes it covers (relative ack minus the SYN)",
          lambda: s["highest_ack"] == 20391 and s["bytes_acked"] == 20390, s)
    check("times are from the laptop's SYN", lambda: s["from_syn"] and abs(s["last_ack_s"] - 6.1) < 1e-6, s.get("last_ack_s"))
    check("a 90-byte window at the last ACK is closed (under one 1,440-byte segment)",
          lambda: s["last_window"] == 90 and s["window_closed"] and s["segment"] == 1440, s)
    check("the device's FIN and its data are not ACKs", lambda: s["device_acks"] == 3, s.get("device_acks"))
    check("silent 10.39 s after the last ACK until the upload ended, a figure of its own",
          lambda: abs(s["silent_after_last_ack_s"] - 10.39) < 1e-6 and abs(s["longest_gap_s"] - 5.3) < 1e-6, s)
    check("the request's size is its header plus Content-Length; 20,390 bytes is not the whole of it",
          lambda: s["request_bytes"] == len(HDR) + 1113226 and s["whole_acked"] is False, s)
    check("the device's reply status is read off the wire", lambda: s["device_status"] == 408, s.get("device_status"))
    check("longest gap between device ACKs: 5.3 s after ack 10000 at 0.8 s",
          lambda: abs(s["longest_gap_s"] - 5.3) < 1e-6 and s["gap_after_ack"] == 10000 and abs(s["gap_at_s"] - 0.8) < 1e-6, s)
    check("the upload's end and the device's first reply data, from the SYN",
          lambda: abs(s["upload_end_s"] - 16.49) < 1e-6 and abs(s["device_data_s"] - 112.69) < 1e-6, s)
    line = line_of(s)
    check("the line: stopped reading, with the numbers",
          lambda: line.startswith("wire: the device ACKed 20390 bytes (highest ack 20391)")
          and "advertised 90 bytes, under one 1440-byte segment: the device stopped reading (window closed)" in line
          and "longest gap between device ACKs 5.30 s, after ack 10000 at 0.80 s" in line
          and "silent 10.39 s after the last ACK until the upload ended" in line
          and "the upload ended 16.49 s after the SYN" in line and "RST" not in line, line)
    check("the line is one line", lambda: "\n" not in line, line)

    s = W.wire_summary(link_lost(), DEV, T_START, 116.69)
    check("an open window at the last ACK is the link lost",
          lambda: not s["window_closed"] and "the ACKs stopped with the window open (link lost)" in W.wire_line(s),
          line_of(s))
    for win, closed in ((1440, False), (1439, True)):
        rows = link_lost()
        rows[-1]["win"] = win
        s = W.wire_summary(rows, DEV, T_START, 116.69)
        check("a last window of exactly %d against a 1440 segment is %s" % (win, "closed" if closed else "open"),
              lambda: s["window_closed"] == closed, s.get("window_closed"))

    rows = [pkt(SYN, LAP, 3, syn=True, ackf=False), pkt(100.3, LAP, 3, ln=1000), pkt(100.31, LAP, 3, ln=1000),
            pkt(100.4, DEV, 3, ack=1001, win=4000), pkt(100.5, DEV, 3, ack=2001, win=1200)]
    s = W.wire_summary(rows, DEV, T_START, 105.0)
    check("one segment is the laptop's largest on the upload: 1200 against 1000-byte segments is open",
          lambda: s["segment"] == 1000 and not s["window_closed"], s)
    h2 = header(2000)

    def small(last_ack, status=b"HTTP/1.1 403 Forbidden\r\n\r\n"):
        rows = [pkt(SYN, LAP, 3, syn=True, ackf=False), pkt(100.25, LAP, 3, payload=h2),
                pkt(100.3, LAP, 3, ln=1000), pkt(100.31, LAP, 3, ln=1000),
                pkt(100.4, DEV, 3, ack=1 + len(h2), win=5760), pkt(100.5, DEV, 3, ack=1 + len(h2) + 1000, win=5760),
                pkt(100.6, DEV, 3, ack=last_ack, win=5760)]
        return rows + ([pkt(100.7, DEV, 3, ack=last_ack, win=5760, payload=status)] if status else [])
    s = W.wire_summary(small(1 + len(h2) + 2000), DEV, T_START, 101.0)
    line = line_of(s)
    check("the whole request ACKed: its own label, with the device's answer",
          lambda: s["whole_acked"] and ("the whole %d-byte request: whole upload acknowledged; device answered HTTP 403"
                                        % (len(h2) + 2000)) in line and "link lost" not in line, line)
    s = W.wire_summary(small(1 + len(h2) + 1990), DEV, T_START, 101.0)
    check("10 bytes short of the whole request is not the whole upload: link lost",
          lambda: not s["whole_acked"] and "(link lost)" in W.wire_line(s), line_of(s))
    s = W.wire_summary(small(1 + len(h2) + 2000, status=None), DEV, T_START, 101.0)
    check("the whole request ACKed and no reply on the wire is said so",
          lambda: "device answered nothing with a status on the wire" in W.wire_line(s), line_of(s))
    check("request_size: no Content-Length, or the header cut short, is unknown",
          lambda: W.request_size(b"POST / HTTP/1.1\r\nHost: x\r\n\r\n") is None
          and W.request_size(HDR[:30]) is None and W.request_size(HDR) == len(HDR) + 1113226)
    check("reply_status: only an HTTP status line", lambda: W.reply_status(b"HTTP/1.1 303 See Other") == 303
          and W.reply_status(b"\x00\x01 303") is None)

    rows = [r for r in link_lost() if not r["syn"]]
    s = W.wire_summary(rows, DEV, T_START, 116.69)
    check("no SYN in the capture: times from the upload's start, and the line says so",
          lambda: not s["from_syn"] and abs(s["last_ack_s"] - 3.0) < 1e-6
          and "after the upload's start" in W.wire_line(s), line_of(s))
    rows = link_lost() + [pkt(103.5, DEV, 3, ack=14401, win=0, rst=True)]
    s = W.wire_summary(rows, DEV, T_START, 116.69)
    check("an RST is named by who sent it, and is not an ACK",
          lambda: s["rst_from"] == ["device"] and s["device_acks"] == 3 and W.wire_line(s).endswith("RST from the device"),
          line_of(s))
    rows = [pkt(SYN, LAP, 3, syn=True, ackf=False), pkt(100.3, LAP, 3, ln=1440)]
    s = W.wire_summary(rows, DEV, T_START, 105.0)
    check("no device ACK at all is said so", lambda: W.wire_line(s).startswith("wire: the device sent no ACK"), line_of(s))
    check("no upload in the capture is said so",
          lambda: W.wire_line(W.wire_summary([], DEV, T_START, 105.0)) == "wire: no upload connection in the capture")
    rows = link_lost() + [pkt(100.0 + 0.1 * k, LAP, 5, ln=1) for k in range(60)]
    check("the upload is chosen by payload, not by packet count",
          lambda: W.upload_stream(rows, DEV) == 3, W.upload_stream(rows, DEV))
    s = W.wire_summary([pkt(SYN, LAP, 3, syn=True, ackf=False), pkt(100.3, LAP, 3, ln=1440),
                        pkt(100.4, DEV, 3, ack=1441, win=5760)], DEV, T_START, 105.0)
    check("one ACK: no gap, and the line says so", lambda: s["longest_gap_s"] is None
          and "longest gap between device ACKs none (one ACK)" in W.wire_line(s), line_of(s))

    # -- parsing ---------------------------------------------------------------------
    text = ("1760000000.5\t%s\t3\t0\t1\t0\t0\t20391\t90\t0\n"
            "1760000001.5\t%s\t3\tFalse\tTrue\tFalse\tFalse\t20391\t5760\t0\n"
            "\t\t\t\t\t\t\t\t\t\n"
            "1760000002.5\t%s\t3\t0\t1\t0\t0\t20391\t5760\t5\t48:54:54:50:2f\n" % (DEV, DEV, DEV))
    rows = W.parse_rows(text)
    check("parse_rows: 1/0 and True/False flags both read, blank rows skipped",
          lambda: len(rows) == 3 and all(r["ackf"] and not r["syn"] for r in rows)
          and rows[0]["win"] == 90 and rows[1]["ack"] == 20391, rows)
    check("parse_rows: a colon-separated payload reads as its bytes",
          lambda: W._bytes(rows[2]["payload"]) == b"HTTP/" and rows[0]["payload"] == "", rows[2])
    listing = ("1. \\Device\\NPF_{A} (Local Area Connection* 9)\n"
               "2. \\Device\\NPF_{B} (Wi-Fi 2)\n"
               "3. \\Device\\NPF_{C} (Wi-Fi)\n")
    check("pick_device: the exact interface name, not a prefix of another",
          lambda: W.pick_device(listing, "Wi-Fi") == "\\Device\\NPF_{C}", W.pick_device(listing, "Wi-Fi"))
    check("pick_device: an absent name is None", lambda: W.pick_device(listing, "Ethernet") is None)

    # -- the capture refuses, never raises -------------------------------------------
    real_find, real_run = W.find_tool, W.subprocess.run
    try:
        W.find_tool = lambda name: None
        c = W.Capture(DEV, os.path.join(HERE, "_never_written.pcapng"), "Wi-Fi", 60)
        check("no Wireshark: start refuses with the reason", lambda: c.start() == (False, "no dumpcap (Wireshark not installed)"))
        check("... and stop says it never started", lambda: c.stop()[0] is False)
        W.find_tool = lambda name: "dumpcap"

        class R:
            stdout = listing.replace("(Wi-Fi)", "(Ethernet)")
        W.subprocess.run = lambda *a, **k: R()
        check("no interface of that name: start refuses with the reason",
              lambda: c.start() == (False, "no capture interface named 'Wi-Fi'"))
        W.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("denied"))
        check("dumpcap -D failing: start refuses, not raises", lambda: c.start()[0] is False)
        check("nothing was written", lambda: not os.path.exists(os.path.join(HERE, "_never_written.pcapng")))
    finally:
        W.find_tool, W.subprocess.run = real_find, real_run

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
