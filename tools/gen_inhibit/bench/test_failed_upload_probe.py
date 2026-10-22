#!/usr/bin/env python3
"""Tests failed_upload_probe.py. No device: loopback servers and synthetic pages.

    GI_FW_TOOLS=<firmware repo>/tools/gen_inhibit py -3.14 test_failed_upload_probe.py [--mutants]

short_post runs against a local server that counts the body bytes it receives
and when the client's FIN arrives, and either replies after it or just closes.
The truncated body, image_offset, the safety refusal, parse_status, the raw_post
epoch conversion, the page poller (a local HTTP server) and the feed into
bench_flash.classify (a1 for no status, b for a 500) are each checked. --mutants
runs the mutants; each must make a check fail. Added 2026-10-16.
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import failed_upload_probe as U    # noqa: E402

FAILS = []
REPLY = b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 6\r\nConnection: close\r\n\r\nfailed"


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:160]))
    if not ok:
        FAILS.append(label)


def counting_server(reply):
    """One-shot server: reads headers, then body bytes until the client's FIN
    (or 6 s), records how many and when; then sends `reply` (or nothing) and closes."""
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    seen = {}

    def run():
        c, _ = ls.accept()
        c.settimeout(6.0)
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        head, _, rest = buf.partition(b"\r\n\r\n")
        seen["declared"] = int([l.split(b":")[1] for l in head.split(b"\r\n")
                                if l.lower().startswith(b"content-length")][0])
        n, t_last = len(rest), time.time()
        try:
            while True:
                b = c.recv(65536)
                if not b:
                    seen["fin"] = time.time()
                    break
                n += len(b)
                t_last = time.time()
        except socket.timeout:
            seen["fin"] = None
        seen["body"], seen["t_last_data"] = n, t_last
        if reply:
            c.sendall(reply)
        c.close()
        ls.close()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return ls.getsockname()[1], t, seen


class PageHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({"mode": 0, "elf_sha256": "ab" * 32, "uptime_ms": 5000, "reset_reason": "POWERON",
                        "git_version": "x"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def main():
    data = bytes(range(256)) * 40           # a 10,240-byte "image"
    body, ctype = U.P.multipart_file(data)
    off = U.image_offset()
    check("image_offset: the file's bytes start there", body[off:off + len(data)] == data, off)

    port, t, seen = counting_server(None)
    cut = 3000
    r = U.short_post("/upload/ota.bin", body, ctype, off + cut, stall=1.0, timeout=10.0,
                     host="127.0.0.1", port=port)
    t.join(10)
    check("short_post: Content-Length declares the whole body", seen.get("declared") == len(body),
          (seen.get("declared"), len(body)))
    check("short_post: only the cut is sent", seen.get("body") == off + cut, (seen.get("body"), off + cut))
    check("short_post: then a FIN from the client", seen.get("fin") is not None, seen)
    check("short_post: the FIN comes after the stall", seen.get("fin") and
          seen["fin"] - seen["t_last_data"] >= 0.8, (seen.get("fin"), seen.get("t_last_data")))
    check("short_post: no reply reads as no status, ending fin", r["status_line"] is None
          and r["ending"] == "fin" and U.parse_status(r["status_line"]) is None, (r["status_line"], r["ending"]))
    check("short_post: epoch times in order", r["epoch_start"] <= r["epoch_sent"] <= r["epoch_halfclosed"]
          <= r["epoch_end"], r)

    port, t, seen = counting_server(REPLY)
    r = U.short_post("/upload/ota.bin", body, ctype, off + cut, stall=0.2, timeout=10.0,
                     host="127.0.0.1", port=port)
    t.join(10)
    check("short_post: a reply after the half-close is still read", r["status_line"] ==
          "HTTP/1.1 500 Internal Server Error" and U.parse_status(r["status_line"]) == 500, r["status_line"])

    tb, _ = U.truncated_body(data, cut)
    check("truncated_body: a complete request carrying exactly the cut",
          tb[off:off + cut] == data[:cut] and tb.endswith(b"--\r\n") and len(tb) == len(body) - len(data) + cut,
          (len(tb), len(body) - len(data) + cut))

    pg = {"mode": 0, "elf_sha256": "ab" * 32}
    check("refuse: mode 0, the running image, a cut inside: go", U.refuse(pg, "ab" * 32, 10, 100) is None)
    check("refuse: not in mode 0", U.refuse(dict(pg, mode=3), "ab" * 32, 10, 100) is not None)
    check("refuse: another image", U.refuse(pg, "cd" * 32, 10, 100) is not None)
    check("refuse: a cut of the whole image", U.refuse(pg, "ab" * 32, 100, 100) is not None)
    check("refuse: a cut of nothing", U.refuse(pg, "ab" * 32, 0, 100) is not None)

    check("parse_status: 500 and none", U.parse_status("HTTP/1.1 500 Internal Server Error") == 500
          and U.parse_status(None) is None and U.parse_status("garbage") is None)
    e = U.raw_post_epochs({"epoch_connected": 1000.5, "t_connected": 0.5, "t_end": 3.0})
    check("raw_post_epochs: start at connect, end on the same clock", e == {"epoch_start": 1000.5,
          "epoch_end": 1003.0}, e)

    srv = http.server.HTTPServer(("127.0.0.1", 0), PageHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rows, stop = [], threading.Event()
    th = threading.Thread(target=U.poll_pages, args=(stop, rows, "http://127.0.0.1:%d" % srv.server_port, 0.1, 5))
    th.start()
    time.sleep(0.55)
    stop.set()
    th.join(5)
    srv.shutdown()
    check("poll_pages: several reads, each a page with t_req <= t_resp", len(rows) >= 3 and
          all(isinstance(x.get("page"), dict) and x["t_req"] <= x["t_resp"] for x in rows), len(rows))

    t_end = 2000.0
    sha = "ab" * 32
    before = {"elf_sha256": sha, "uptime_ms": 100000, "reset_reason": "POWERON", "mode": 0,
              "git_version": "x", "t_req": t_end - 10.0, "t_resp": t_end - 9.99}
    pages = [{"t_req": t_end + k, "t_resp": t_end + k + 0.01,
              "page": {"elf_sha256": sha, "uptime_ms": 110000 + k * 1000, "reset_reason": "POWERON", "mode": 0}}
             for k in range(0, 36, 5)]
    v = U.BF.classify(None, t_end, before, U.bf_readings(pages), sha)
    check("bf_readings into classify: no status, one boot, 35 s on: a1", v[0] == "a1", v)
    v = U.BF.classify(500, t_end, before, U.bf_readings(pages), sha)
    check("classify: a 500 is b", v[0] == "b", v)
    up = [dict(pages[0], t_resp=t_end - 0.5), dict(pages[1], t_resp=t_end + 0.5)]
    ins = U.inside_upload(up, t_end - 1.0, t_end)
    check("inside_upload: only a read answered while the upload was open", len(ins) == 1
          and ins[0]["t_resp"] == t_end - 0.5, ins)
    up.append(dict(pages[0], t_req=t_end - 1.01, t_resp=t_end - 0.8))
    ins = U.inside_upload(up, t_end - 1.0, t_end)
    check("inside_upload: a read sent before the connect is not inside, though answered inside",
          len(ins) == 1 and ins[0]["t_resp"] == t_end - 0.5, ins)
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("the whole body sent", "    part = req[:head_len + cut]", "    part = req"),
    ("no half-close", "        s.shutdown(socket.SHUT_WR)\n", "        pass\n"),
    ("no stall", "        time.sleep(stall)", "        pass"),
    ("the reply after the half-close not read", "                b = s.recv(4096)\n                if not b:",
     "                b = b''\n                if not b:"),
    ("the truncated image carries it all", "    return P.multipart_file(data[:cut])", "    return P.multipart_file(data)"),
    ("image_offset at the part's start", "    return empty.index(b\"\\r\\n\\r\\n\") + 4", "    return 0"),
    ("refuse ignores the mode", "    if page.get(\"mode\") != 0:", "    if False:"),
    ("refuse allows the whole image", "    if not 0 < cut < size:", "    if not 0 < cut <= size:"),
    ("refuse allows another image", "    why = P.check_image(page, image_elf)\n", "    why = None\n"),
    ("parse_status always None", "        return int(parts[1])", "        return None"),
    ("raw_post end on the wrong clock", "    t0 = rec[\"epoch_connected\"] - rec[\"t_connected\"]",
     "    t0 = rec[\"epoch_connected\"]"),
    ("bf_readings drops the times", "            out.append(dict(ident, t_req=r[\"t_req\"], t_resp=r[\"t_resp\"]))",
     "            out.append(dict(ident))"),
    ("inside_upload counts every read", "and start < r[\"t_resp\"] < end]", "]"),
    ("inside_upload counts a read in flight at the connect", 'and r["t_req"] >= start and', "and"),
]


def mutants():
    src0 = open(os.path.join(HERE, "failed_upload_probe.py"), encoding="utf-8").read()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "failed_upload_mutants")   # outside SeaDrive
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src0.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src0.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        shutil.copy(os.path.join(HERE, "ota_guard_raw_probe.py"), d)
        open(os.path.join(d, "failed_upload_probe.py"), "w", encoding="utf-8").write(src0.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        try:
            r = subprocess.run([sys.executable, "-B", os.path.join(d, os.path.basename(__file__))],
                               capture_output=True, text=True, cwd=os.getcwd(), timeout=120,
                               env=dict(os.environ, PYTHONPATH=HERE))   # gi_paths, after the copies
            out, crashed = r.stdout, "Traceback" in r.stderr
        except subprocess.TimeoutExpired:
            out, crashed = "", True
        fails = [l for l in out.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:90]) if fails else (", CRASHED or hung" if crashed else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
