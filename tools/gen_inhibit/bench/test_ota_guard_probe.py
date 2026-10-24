#!/usr/bin/env python3
"""Tests ota_guard_raw_probe.py (--body, raw_post's ending) and ota_guard_pcap_join.py.

    REVERSE_IT_DIR=<reverse-it root> py -3.14 test_ota_guard_probe.py

raw_post is run against a local server that answers 403 and then either closes
cleanly after reading the whole request (FIN) or aborts with unread bytes and
SO_LINGER 0 (RST), so the client's "fin" / "rst" reading is tested on real
sockets. The pcap join is tested on synthetic tshark rows. Added 2026-10-16.

Since 2026-10-16 (c6fdab5's refusal fix) also: multipart_file, check_image (the
full-image run's safety refusal), raw_post's reader thread (a server that answers
403 at once and only then drains an 8 MB body through small socket buffers: the 403 must be timed before
the send completes), and pcap_join's no-RST expectation. --mutants runs the
mutants of both files; each must make a check fail.
"""
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ota_guard_raw_probe as R     # noqa: E402
import ota_guard_pcap_join as J     # noqa: E402

FAILS = []
REPLY = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 9\r\nConnection: close\r\n\r\nForbidden"


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:120]))
    if not ok:
        FAILS.append(label)


def server(mode):
    """One-shot local server. mode 'fin': read headers + body, reply, close.
    mode 'rst': read only the headers, reply, close with SO_LINGER 0 (abort)."""
    ls = socket.socket()
    if mode == "slowdrain":
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    port = ls.getsockname()[1]

    def run():
        c, _ = ls.accept()
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        head, _, rest = buf.partition(b"\r\n\r\n")
        cl = int([l.split(b":")[1] for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0])
        if mode == "fin":
            while len(rest) < cl:
                rest += c.recv(4096)
            c.sendall(REPLY)
            c.shutdown(socket.SHUT_WR)
            c.recv(10)
            c.close()
        elif mode == "slowdrain":
            c.sendall(REPLY)
            time.sleep(0.5)
            n = len(rest)
            while n < cl:
                n += len(c.recv(65536))
            c.shutdown(socket.SHUT_WR)
            c.recv(10)
            c.close()
        elif mode == "holdopen":
            while len(rest) < cl:
                rest += c.recv(4096)
            c.sendall(REPLY)
            try:
                while c.recv(4096):      # hold the connection until the client ends it
                    pass
            except OSError:
                pass
            c.close()
        else:
            c.sendall(REPLY)
            c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            c.close()
        ls.close()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return port, t


def main():
    body, ctype = R.G.multipart_bogus()
    req = R.build_request("/upload/ota.bin", body, ctype, "h")
    check("build_request: Content-Length is the body's", b"Content-Length: %d\r\n" % len(body) in req
          and req.endswith(body), len(body))
    req0 = R.build_request("/upload/ota.bin", b"", ctype, "h")
    check("build_request: an empty body is Content-Length 0 and nothing after the headers",
          b"Content-Length: 0\r\n" in req0 and req0.endswith(b"\r\n\r\n"))
    port, t = server("fin")
    r = R.raw_post("/upload/ota.bin", body, ctype, timeout=5, host="127.0.0.1", port=port)
    t.join(3)
    check("raw_post: a clean close reads 'fin' with the 403", r["ending"] == "fin"
          and r["status_line"] == "HTTP/1.1 403 Forbidden" and r["body_len"] == len(body)
          and isinstance(r["local_port"], int), r)
    port, t = server("rst")
    r = R.raw_post("/upload/ota.bin", body, ctype, timeout=5, host="127.0.0.1", port=port)
    t.join(3)
    check("raw_post: an abort reads 'rst'", r["ending"].startswith("rst"), r["ending"])

    D = "192.168.80.1"
    p403 = (b"HTTP/1.1 403 Forbidden\r\n").hex()
    seg = lambda src, sp, dp, fin=False, rst=False, pl="": (0, src, sp, dp, fin, rst, len(pl) // 2, pl)
    segs = [seg("192.168.80.2", 50001, 80, pl="aa" * 10), seg(D, 80, 50001, pl=p403), seg(D, 80, 50001, rst=True),
            seg("192.168.80.2", 50002, 80), seg(D, 80, 50002, pl=p403), seg(D, 80, 50002, fin=True),
            seg(D, 80, 50003, fin=True),
            seg(D, 80, 50004, rst=True)]
    c = J.per_connection(segs)
    check("join: 403 then RST", c[50001]["wire_403"] and c[50001]["device_end"] == "rst", c[50001])
    check("join: 403 then FIN", c[50002]["wire_403"] and c[50002]["device_end"] == "fin", c[50002])
    check("join: FIN with no 403 on the wire", not c[50003]["wire_403"] and c[50003]["device_end"] == "fin",
          c[50003])
    rows = J.join([{"mode": "OBSERVE", "rep": 0, "local_port": 50001, "body_len": 184,
                    "status_line": "HTTP/1.1 403 Forbidden", "ending": "rst"},
                   {"mode": "OBSERVE", "rep": 1, "local_port": 50004, "body_len": 0,
                    "status_line": None, "ending": "rst"},
                   {"mode": "OBSERVE", "rep": 2, "local_port": 50003, "body_len": 0,
                    "status_line": None, "ending": "fin"},
                   {"mode": "OBSERVE", "rep": 3, "local_port": 59999, "body_len": 0}], c)
    v = [J.against_prediction(r) for r in rows]
    check("prediction: a body with RST is consistent", v[0] == "consistent", v[0])
    check("prediction: a body-less RST is AGAINST", v[1].startswith("AGAINST"), v[1])
    check("prediction: a FIN with no 403 on the wire is AGAINST", v[2].startswith("AGAINST"), v[2])
    check("prediction: a request missing from the capture says so", v[3] == "not in capture", v[3])

    # c6fdab5 additions
    fb, fct = R.multipart_file(b"\x01IMAGE\x02")
    check("multipart_file: the data whole between the part head and the closing boundary",
          b"\r\n\r\n\x01IMAGE\x02\r\n------vtruxbenchboundary--\r\n" in fb and fb.endswith(b"--\r\n")
          and fct.endswith("----vtruxbenchboundary") and b'filename="ota.bin"' in fb, fb[:120])
    check("check_image: the running image passes", R.check_image({"elf_sha256": "ab" * 32}, "ab" * 32) is None)
    check("check_image: another image is refused", R.check_image({"elf_sha256": "ab" * 32}, "cd" * 32))
    check("check_image: a page without elf_sha256 is refused", R.check_image({}, "cd" * 32))
    big = b"x" * (8 * 1024 * 1024)
    port, t = server("slowdrain")
    r = R.raw_post("/upload/ota.bin", big, "application/octet-stream", timeout=20, host="127.0.0.1", port=port,
                   sndbuf=8192)
    t.join(5)
    check("raw_post: a 403 sent before the body is drained is timed before the send completes",
          r["t_first_byte"] is not None and r["t_sent"] is not None and r["t_first_byte"] < r["t_sent"]
          and r["ending"] == "fin" and r["status_line"] == "HTTP/1.1 403 Forbidden",
          {k: r.get(k) for k in ("t_first_byte", "t_sent", "t_end", "ending")})
    base = {"in_capture": True, "wire_403": True, "device_end": "fin", "client_got_403": True, "body_len": 184}
    nr = lambda **k: J.against_no_rst(dict(base, **k))
    check("no-rst: 403 then FIN, read by the client, is consistent", nr() == "consistent", nr())
    check("no-rst: an RST is AGAINST, and says RST", nr(device_end="rst").startswith("AGAINST")
          and "RST" in nr(device_end="rst"), nr(device_end="rst"))
    check("no-rst: FIN then RST is AGAINST, and says RST", nr(device_end="fin+rst").startswith("AGAINST")
          and "RST" in nr(device_end="fin+rst"), nr(device_end="fin+rst"))
    check("no-rst: no close at all is AGAINST", nr(device_end="none").startswith("AGAINST"))
    check("no-rst: FIN with no 403 on the wire is AGAINST", nr(wire_403=False).startswith("AGAINST"))
    check("no-rst: the client missing the 403 is AGAINST", nr(client_got_403=False).startswith("AGAINST"))
    check("no-rst: a full-image body is judged the same", nr(body_len=1278300) == "consistent")
    check("no-rst: missing from the capture says so", nr(in_capture=False) == "not in capture")

    # the held-open connection (c6fdab5 keeps a refused connection open after its 403)
    check("reply_complete: headers alone, a short body, no Content-Length are not complete; a full one is",
          not R.reply_complete(REPLY[:30]) and not R.reply_complete(REPLY[:-3])
          and not R.reply_complete(b"HTTP/1.1 403 Forbidden\r\n\r\nx") and R.reply_complete(REPLY))
    port, t = server("holdopen")
    r = R.raw_post("/upload/ota.bin", body, ctype, timeout=4, host="127.0.0.1", port=port, grace=0.3)
    t.join(3)
    check("raw_post grace: a device holding the connection is closed by the client after the grace",
          r["ending"] == "open: client closed" and r["t_reply"] is not None
          and 0.25 <= r["t_end"] - max(r["t_reply"], r["t_sent"]) < 1.5,
          {k: r.get(k) for k in ("t_reply", "t_sent", "t_end", "ending")})
    port, t = server("fin")
    r = R.raw_post("/upload/ota.bin", body, ctype, timeout=4, host="127.0.0.1", port=port, grace=1.0)
    t.join(3)
    check("raw_post grace: a device that closes within the grace reads 'fin'", r["ending"] == "fin", r["ending"])
    fc = J.per_connection([seg("192.168.80.2", 50010, 80, pl="aa"), seg(D, 80, 50010, pl=p403),
                           seg("192.168.80.2", 50010, 80, fin=True), seg(D, 80, 50010, fin=True),
                           seg(D, 80, 50011, pl=p403), seg(D, 80, 50011, fin=True),
                           seg("192.168.80.2", 50011, 80, fin=True)])
    ws = J.per_connection([seg(D, 80, 50020, pl=(b"HTTP/1.1 200 OK\r\n").hex()), seg(D, 80, 50020, pl="41" * 4),
                           seg(D, 80, 50021, pl=p403)])
    check("join: wire_status is the first response's code (200 for a success, 403 for a refusal)",
          ws[50020]["wire_status"] == "200" and not ws[50020]["wire_403"] and ws[50021]["wire_status"] == "403",
          ws)
    check("join: first_close names the side that closed first",
          fc[50010]["first_close"] == "client" and fc[50010]["device_end"] == "fin"
          and fc[50011]["first_close"] == "device", fc)
    check("join: client_got_403 from the probe's status line", rows[0]["client_got_403"]
          and not rows[1]["client_got_403"], rows[:2])
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("ota_guard_pcap_join.py", "403 never seen on the wire",
     'startswith(b"HTTP/1.1 403")', 'startswith(b"HTTP/1.1 999")'),
    ("ota_guard_pcap_join.py", "RST read as FIN",
     '                c["device_end"] = "rst" if c["device_end"] == "none" else (',
     '                c["device_end"] = "fin" if c["device_end"] == "none" else ('),
    ("ota_guard_pcap_join.py", "a body-less RST is not flagged",
     'if row["body_len"] == 0 and row["device_end"] in ("rst", "fin+rst"):',
     'if False:'),
    ("ota_guard_pcap_join.py", "a FIN with no 403 is not flagged",
     'if row["device_end"] == "fin" and not row["wire_403"]:', 'if False:'),
    ("ota_guard_pcap_join.py", "no-rst accepts an RST",
     'if row["device_end"] in ("rst", "fin+rst"):\n        return "AGAINST: the device ended with RST"',
     'if False:\n        return "AGAINST: the device ended with RST"'),
    ("ota_guard_pcap_join.py", "no-rst ignores the client's read",
     'if not row["client_got_403"]:', 'if False:'),
    ("ota_guard_raw_probe.py", "reading after the send, not beside it",
     [('    th.start()\n    try:\n', '    try:\n'),
      ('        th.join(timeout + 5)\n', '        th.start()\n        th.join(timeout + 5)\n')]),
    ("ota_guard_raw_probe.py", "grace ignored (wait for the end)", "    if grace is None:", "    if True:"),
    ("ota_guard_raw_probe.py", "reply complete without its body",
     "    return m is not None and len(body) >= int(m.group(1))", "    return m is not None"),
    ("ota_guard_pcap_join.py", "wire_status reads the wrong bytes",
     'bytes.fromhex(payload)[9:12].decode("latin-1")', 'bytes.fromhex(payload)[0:3].decode("latin-1")'),
    ("ota_guard_pcap_join.py", "first close always the device",
     'c["first_close"] = "device" if src == device else "client"', 'c["first_close"] = "device"'),
    ("ota_guard_raw_probe.py", "one send of the whole request",
     "            s.sendall(req[k:k + SEND_CHUNK])", "            s.sendall(req) if k == 0 else None"),
    ("ota_guard_raw_probe.py", "check_image accepts anything",
     "    if have != image_sha:", "    if False:"),
    ("ota_guard_raw_probe.py", "multipart_file drops the closing boundary",
     'return head + data + ("\\r\\n--%s--\\r\\n" % boundary).encode(), \\',
     'return head + data, \\'),
]


def mutants():
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "ota_guard_probe_mutants")   # outside SeaDrive (AGENTS.md)
    killed = errors = 0
    for n, m in enumerate(MUTANTS, 1):
        fname, name = m[0], m[1]
        edits = m[2] if len(m) == 3 else [(m[2], m[3])]    # one edit, or a list of them
        src = open(os.path.join(HERE, fname), encoding="utf-8").read()
        bad = [old for old, _new in edits if src.count(old) != 1]
        if bad:
            print("ERROR    m%d %s: an edit matched %d times" % (n, name, src.count(bad[0])))
            errors += 1
            continue
        for old, new in edits:
            src = src.replace(old, new)
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f in ("ota_guard_pcap_join.py", "ota_guard_raw_probe.py"):
            shutil.copy(os.path.join(HERE, f), d)
        open(os.path.join(d, fname), "w", encoding="utf-8").write(src)
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True, cwd=os.getcwd(), timeout=120,
                           env=dict(os.environ, PYTHONPATH=HERE))   # gi_paths, after the copies
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               (" (%d FAIL)" % len(fails)) if fails else
                               (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
