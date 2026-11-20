#!/usr/bin/env python3
"""Tests ota_guard_raw_probe.py (--body, raw_post's ending).

    REVERSE_IT_DIR=<reverse-it root> python3.13 test_ota_guard_probe.py [--mutants]
    (py -3.14 on Windows)

raw_post is run against a local server that answers 403 and then either closes
cleanly after reading the whole request (FIN) or aborts with unread bytes and
SO_LINGER 0 (RST), so the client's "fin" / "rst" reading is tested on real
sockets. Added 2026-10-16.

Since 2026-10-16 (c6fdab5's refusal fix) also: multipart_file, check_image (the
full-image run's safety refusal) and raw_post's reader thread (a server that answers
403 at once and only then drains an 8 MB body through small socket buffers: the 403 must be timed before
the send completes). --mutants runs the probe's mutants; each must make a check fail.
The pcap join's checks and mutants went with ota_guard_pcap_join.py (topic (E)
follow-up, the gen-inhibit reviewer's answer to 8e2ed20c, 2026-11-19): B21 is judged at
the client (ota_guard_client_judge.py).

A mutant of one platform's code (a 5th field, "nt" or "posix") is printed N/A on the
other and not counted; its edit must still apply (2026-11-12, the reviewer's Linux
leftovers item 4). "One send of the whole request" is Windows only: raw_post sends in
SEND_CHUNK pieces because one big send() is taken whole on Windows, so t_sent would come
before the body is drained (killed there, 14 of 14 on 2026-10-16, VERIFICATION.md). On
Linux sendall() blocks while the socket's send buffer is full in either form, so t_sent
is when the last bytes enter the buffer, one chunk apart at most: the mutant is
equivalent there, and survived on madhouse-debian with the slowdrain check passing.
"""
import os
import shutil
import socket
import struct
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ota_guard_raw_probe as R     # noqa: E402

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
        if mode in ("fin", "short"):
            while len(rest) < cl:
                rest += c.recv(4096)
            c.sendall(REPLY if mode == "fin" else REPLY[:-5])     # short: the 403 cut off in its body
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
    check("raw_post: the whole reply recorded as the client holds it (spec 9.3, topic (E))",
          r["reply_complete"] is True and r["content_length"] == 9 and r["reply_body"] == "Forbidden", r)
    port, t = server("short")
    r = R.raw_post("/upload/ota.bin", body, ctype, timeout=5, host="127.0.0.1", port=port)
    t.join(3)
    check("raw_post: a 403 cut short in its body is recorded not whole, with what came",
          r["status_line"] == "HTTP/1.1 403 Forbidden" and r["reply_complete"] is False
          and r["content_length"] == 9 and r["reply_body"] == "Forb", r)
    port, t = server("rst")
    r = R.raw_post("/upload/ota.bin", body, ctype, timeout=5, host="127.0.0.1", port=port)
    t.join(3)
    check("raw_post: an abort reads 'rst'", r["ending"].startswith("rst"), r["ending"])

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
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("ota_guard_raw_probe.py", "reading after the send, not beside it",
     [('    th.start()\n    try:\n', '    try:\n'),
      ('        th.join(timeout + 5)\n', '        th.start()\n        th.join(timeout + 5)\n')]),
    ("ota_guard_raw_probe.py", "grace ignored (wait for the end)", "    if grace is None:", "    if True:"),
    ("ota_guard_raw_probe.py", "reply complete without its body",
     "    return m is not None and len(body) >= int(m.group(1))", "    return m is not None"),
    ("ota_guard_raw_probe.py", "one send of the whole request",
     "            s.sendall(req[k:k + SEND_CHUNK])", "            s.sendall(req) if k == 0 else None", "nt"),
    ("ota_guard_raw_probe.py", "check_image accepts anything",
     "    if have != image_sha:", "    if False:"),
    ("ota_guard_raw_probe.py", "multipart_file drops the closing boundary",
     'return head + data + ("\\r\\n--%s--\\r\\n" % boundary).encode(), \\',
     'return head + data, \\'),
]


def mutants():
    base = gi_paths.scratch("mutants-ota_guard_probe_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = na = 0
    for n, m in enumerate(MUTANTS, 1):
        fname, name = m[0], m[1]
        edits = m[2] if len(m) == 3 else [(m[2], m[3])]    # one edit, or a list of them
        where = m[4] if len(m) == 5 else None               # one platform's mutant
        src = open(os.path.join(HERE, fname), encoding="utf-8").read()
        bad = [old for old, _new in edits if src.count(old) != 1]
        if bad:
            print("ERROR    m%d %s: an edit matched %d times" % (n, name, src.count(bad[0])))
            errors += 1
            continue
        if where and where != os.name:                      # the edit still has to apply (above)
            print("N/A      m%d %s  (%s only; this run is on %s)"
                  % (n, name, "Windows" if where == "nt" else "POSIX", sys.platform))
            na += 1
            continue
        for old, new in edits:
            src = src.replace(old, new)
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        shutil.copy(os.path.join(HERE, "ota_guard_raw_probe.py"), d)
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
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors, %d not applicable on %s"
          % (killed, len(MUTANTS) - na, errors, na, sys.platform))
    return 0 if killed == len(MUTANTS) - na and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
