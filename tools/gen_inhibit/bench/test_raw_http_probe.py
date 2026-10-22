#!/usr/bin/env python3
"""Tests raw_http_probe.py against a local fake server, one misbehaviour per case.

    py -3.14 test_raw_http_probe.py          # prints each check, exits 1 on any failure

Every way the WiCAN's reply could go wrong is staged here, and the probe has to
name it: a late reply, a reset, a close with no reply, a body cut mid-JSON, a
Content-Length longer or shorter than the body (reviewer, 2026-10-14), a reply that
never comes, a malformed status line. For each, the http.client verdict must be
the exception urllib would really raise -- checked against urllib itself on the
same server, so the replay of recorded bytes cannot drift from the real client.

The request bytes are checked against urllib's on the wire, so the device sees
the same request it saw on 2026-10-12.
"""

import json
import socket
import struct
import sys
import threading
import time
import urllib.request

import raw_http_probe as P

GOOD = b'{"mode":3,"inhibit_live":true,"uptime_ms":1234}'


def reply(body, cl=None, extra=b""):
    cl = len(body) if cl is None else cl
    head = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\n" % cl) + extra + b"\r\n"
    return head + body


CASES = {
    # name: list of actions after reading the request
    "good":          [("send", reply(GOOD)), ("fin",)],
    "late_2s":       [("sleep", 2.0), ("send", reply(GOOD)), ("fin",)],
    "split_late":    [("send", reply(GOOD)[:30]), ("sleep", 1.0),
                      ("send", reply(GOOD)[30:]), ("fin",)],
    "rst_no_reply":  [("rst",)],
    "fin_no_reply":  [("fin",)],
    "cl_longer":     [("send", reply(GOOD, cl=len(GOOD) + 40)), ("fin",)],
    "cl_shorter":    [("send", reply(GOOD, cl=len(GOOD) - 10)), ("fin",)],
    "cut_mid_json_cl":   [("send", reply(GOOD)[:-20]), ("fin",)],
    "cut_mid_json_nocl": [("send", b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                           + GOOD[:20]), ("fin",)],
    # The pause lets the probe read the bytes first: on Windows a RST that lands
    # while data sits unread in the receive buffer discards it, so without the
    # pause the case races between "partial body, then RST" and "RST, no bytes".
    "cut_then_rst":  [("send", reply(GOOD)[:-20]), ("sleep", 0.3), ("rst",)],
    "no_reply_open": [("sleep", 3.0), ("fin",)],
    "garbage_status": [("send", b"HTTX/9 what\r\n\r\n"), ("fin",)],
    "complete_open": [("send", reply(GOOD)), ("sleep", 3.0), ("fin",)],
    "chunked_good":  [("send", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                           + b"%x\r\n" % len(GOOD) + GOOD + b"\r\n0\r\n\r\n"), ("fin",)],
    "chunked_cut":   [("send", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                           b"%x\r\n" % len(GOOD) + GOOD[:10]), ("fin",)],
}


class Fake(threading.Thread):
    def __init__(self, actions):
        threading.Thread.__init__(self, daemon=True)
        self.actions = actions
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(1)
        self.port = self.ls.getsockname()[1]
        self.request = b""

    def run(self):
        c, _ = self.ls.accept()
        while b"\r\n\r\n" not in self.request:
            d = c.recv(4096)
            if not d:
                break
            self.request += d
        for a in self.actions:
            if a[0] == "send":
                c.sendall(a[1])
            elif a[0] == "sleep":
                time.sleep(a[1])
            elif a[0] == "fin":
                c.shutdown(socket.SHUT_WR)
                time.sleep(0.3)
                c.close()
            elif a[0] == "rst":
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                c.close()
        self.ls.close()


def probe(name, timeout=5.0, **kw):
    f = Fake(CASES[name])
    f.start()
    r = P.raw_request("127.0.0.1", "/gen_inhibit_set?mode=3&offset=500", timeout=timeout,
                      port=f.port, base=time.perf_counter(), **kw)
    f.join(timeout=10)
    return r, f


def via_urllib(name, timeout=5.0):
    """The exception class urllib raises on this case, or 'ok'."""
    f = Fake(CASES[name])
    f.start()
    req = urllib.request.Request("http://127.0.0.1:%d/gen_inhibit_set?mode=3&offset=500" % f.port,
                                 data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        out = "ok"
    except Exception as e:
        # urlopen wraps OSError raised while SENDING in URLError, but not one
        # raised while reading the response; unwrap so both name the cause.
        out = type(getattr(e, "reason", None) or e).__name__ if type(e).__name__ == "URLError" \
            else type(e).__name__
    f.join(timeout=10)
    return out, f.request


FAILS = []


def check(label, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" else ""))
    if not cond:
        FAILS.append(label)


def main():
    # 1. The request bytes are urllib's, byte for byte (Host carries the port here,
    #    as urllib's does for a non-80 port).
    _, ureq = via_urllib("good")
    r, f = probe("good")
    # The two fake servers listen on different ports; only the Host line may differ.
    import re
    norm = lambda b: re.sub(rb"Host: 127\.0\.0\.1:\d+", b"Host: H", b)
    check("request bytes equal urllib's", norm(f.request) == norm(ureq) and b"Host: 127.0.0.1:" in f.request,
          "%r vs %r" % (f.request, ureq))

    # 2. Per-case expectations.
    exp = {
        # name: (ending, cl_matches, body_json_ok, http_client result, extra checks)
        "good":          ("fin", True, True, "ok"),
        "late_2s":       ("fin", True, True, "ok"),
        "split_late":    ("fin", True, True, "ok"),
        "rst_no_reply":  ("rst", None, None, "ConnectionResetError"),
        "fin_no_reply":  ("fin", None, None, "RemoteDisconnected"),
        "cl_longer":     ("fin", False, True, "IncompleteRead"),
        "cl_shorter":    ("fin", False, None, "ok"),
        "cut_mid_json_cl":   ("fin", False, False, "IncompleteRead"),
        "cut_mid_json_nocl": ("fin", None, False, "ok"),
        "cut_then_rst":  ("rst", False, False, "ConnectionResetError"),
        "no_reply_open": ("timeout", None, None, None),
        "garbage_status": ("fin", None, None, "BadStatusLine"),
        "complete_open": ("complete_open", True, True, "ok"),
        "chunked_good":  ("fin", None, True, "ok"),
        "chunked_cut":   ("fin", None, False, "IncompleteRead"),
    }
    for name, (ending, clm, jok, hc) in exp.items():
        to = 1.0 if name == "no_reply_open" else 5.0
        r, _ = probe(name, timeout=to)
        check("%s: ending %s" % (name, ending), r["ending"] == ending, r["ending"])
        check("%s: cl_matches %s" % (name, clm), r["cl_matches"] == clm, r["cl_matches"])
        if jok is not None or name in ("rst_no_reply", "fin_no_reply", "no_reply_open"):
            check("%s: body_json_ok %s" % (name, jok), r["body_json_ok"] == jok, r["body_json_ok"])
        # The replayed verdict must equal what urllib really raises on the same server.
        u, _ = via_urllib(name, timeout=to)
        check("%s: http_client verdict == urllib (%s)" % (name, u),
              r["http_client"]["result"] == u, r["http_client"])
        if hc is not None:
            check("%s: urllib raises %s" % (name, hc), u == hc, u)
        if name == "good":
            check("good: first byte within 0.5 s of the send", 0 <= r["t_first_byte"] - r["t_sent"] < 0.5,
                  r["t_first_byte"] - r["t_sent"])
            check("good: local_port is the client's port", isinstance(r["local_port"], int)
                  and r["local_port"] > 0 and r["local_port"] != r["host_port"], r["local_port"])
        if name == "late_2s":
            check("late_2s: first byte 1.9-2.5 s after send",
                  1.9 <= r["t_first_byte"] - r["t_sent"] < 2.5, r["t_first_byte"] - r["t_sent"])
        if name == "split_late":
            check("split_late: two chunks ~1 s apart", len(r["chunks"]) == 2
                  and r["chunks"][1][0] - r["chunks"][0][0] >= 0.9, r["chunks"])
            check("split_late: t_complete is the second chunk",
                  r["t_complete"] == r["chunks"][1][0], (r["t_complete"], r["chunks"]))
        if name == "no_reply_open":
            check("no_reply_open: no bytes, ends at the timeout",
                  r["raw_len"] == 0 and 0.9 <= r["t_end"] - r["t_sent"] < 2.0, r["t_end"] - r["t_sent"])
        if name == "complete_open":
            check("complete_open: stops AFTER_COMPLETE_S after the full reply, not at the timeout",
                  r["t_end"] - r["t_complete"] < P.AFTER_COMPLETE_S + 0.5, r["t_end"] - r["t_complete"])
        if name in ("rst_no_reply", "fin_no_reply"):
            check("%s: 0 bytes, no first byte" % name, r["raw_len"] == 0 and r["t_first_byte"] is None)
        if name.startswith("cl_") or name.startswith("cut_mid"):
            check("%s: body_len and content_length recorded" % name,
                  isinstance(r["body_len"], int) and r["content_length"] in (None, len(GOOD) + 40,
                                                                            len(GOOD) - 10, len(GOOD)))
        # caught_by_gi: an OSError would have been caught by gi() on 2026-10-12.
        if r["http_client"]["result"] not in ("ok", "connect_error"):
            want = r["http_client"]["result"] in ("ConnectionResetError", "RemoteDisconnected",
                                                  "TimeoutError", "timeout")
            check("%s: caught_by_gi %s" % (name, want), r["http_client"]["caught_by_gi"] == want,
                  r["http_client"])

    # 3. Connect refused.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = P.raw_request("127.0.0.1", "/x", timeout=2.0, port=port)
    check("refused: ending connect_error", r["ending"] == "connect_error", r["ending"])

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
