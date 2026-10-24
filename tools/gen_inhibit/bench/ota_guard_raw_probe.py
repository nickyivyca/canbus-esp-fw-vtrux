#!/usr/bin/env python3
"""B21 follow-up (2026-10-16): the OTA refusal over a raw socket, every byte kept.

bench_ota_guard.py (firmware repo) read OBSERVE and INHIBIT as a connection
reset with no status, and RESPOND and PASSIVE as 403, on c65c45e. urllib throws
away whatever arrived before a reset, so it cannot tell "no 403 was sent" from
"a 403 arrived and the reset then discarded it". This sends the guard's own
request (the same multipart body, from bench_ota_guard.multipart_bogus) and
records every recv() with its time, and how reading ended (fin / rst /
timeout), in each non-OFF mode, --reps times, then returns the device to OFF.
It records; it does not judge. The device's uptime is read before and after,
so a restart would show.

    REVERSE_IT_DIR=<reverse-it root> py -3.14 ota_guard_raw_probe.py [--reps 5] [--body multipart|empty]
Writes runs/ota_guard_raw_probe_<body>_<stamp>.json.

--body (2026-10-16, the B21 addendum, reviewer): `multipart` is the guard's 184-byte
body, `empty` the same request with Content-Length 0. The implementor's reading is
that a refused request with unread body bytes ends in RST and one without them in
FIN. Each row records the client's local port, so ota_guard_pcap_join.py can find
the request's TCP stream in a tshark capture and say from the WIRE whether the 403
was sent and how the device closed. Tested by test_ota_guard_probe.py.

--body image --image <bin> (2026-10-16, reviewer, on c6fdab5: refusals now return
ESP_OK, so httpd reads the rest of the body off after the reply): the whole OTA
image in the multipart wrapping curl -F gives it, to time how long a full-image
refusal takes to complete. Reading runs in its own thread beside sendall, so the
403's arrival time is real even while the upload is still going. SAFETY: refused
unless the image's elf_sha256 is the one the device's page reports, so a guard that
failed to refuse could only re-install the running build. --timeout bounds each
request (default 10 s; 120 s for an image).
"""
import argparse
import json
import os
import hashlib
import re
import socket
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, gi_paths.fw_tools())
import bench_ota_guard as G       # noqa: E402
import bench_flash as BF          # noqa: E402

HOST = "192.168.80.1"
PORT = 80
MODES = {1: "OBSERVE", 2: "RESPOND", 3: "INHIBIT", 4: "PASSIVE"}
SEND_CHUNK = 4096


def build_request(path, body, ctype, host=HOST):
    """The request bytes: headers with this body's Content-Length, then the body."""
    return ("POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            "Connection: close\r\n\r\n" % (path, host, ctype, len(body))).encode() + body


def multipart_file(data, name="ota.bin"):
    """(body, content type): data as one multipart file part, as curl -F sends it."""
    boundary = "----vtruxbenchboundary"
    head = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode()
    return head + data + ("\r\n--%s--\r\n" % boundary).encode(), \
        "multipart/form-data; boundary=%s" % boundary


def check_image(page, image_sha):
    """None if the device runs this image (its page's elf_sha256), else why not."""
    have = (page or {}).get("elf_sha256")
    if not have:
        return "the device's page has no elf_sha256"
    if have != image_sha:
        return "the device runs %s..., the image is %s...; refusing" % (have[:8], image_sha[:8])
    return None


def reply_complete(raw):
    """True once raw holds a whole HTTP response: headers and Content-Length bytes."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        return False
    m = re.search(rb"(?im)^content-length:\s*(\d+)\s*$", head)
    return m is not None and len(body) >= int(m.group(1))


def raw_post(path, body, ctype, timeout=10.0, host=None, port=None, sndbuf=None, grace=None):
    """sndbuf: the client socket's SO_SNDBUF (the test sets it small so a send
    really blocks on a loopback; the bench leaves the OS default).
    grace: None waits for the device to end the connection (or the timeout). A
    number: once the whole reply is in and the send is done, wait that long for the
    device to close, then close from this side; the ending reads "open: client
    closed" (c6fdab5 holds a refused connection open after its 403)."""
    host, port = host or HOST, port or PORT
    t0 = time.perf_counter()
    rec = {"chunks": [], "raw": b"", "body_len": len(body)}
    s = socket.create_connection((host, port), timeout=timeout)
    if sndbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    rec["t_connected"] = round(time.perf_counter() - t0, 4)
    rec["epoch_connected"] = time.time()
    rec["local_port"] = s.getsockname()[1]
    req = build_request(path, body, ctype, host)
    box = {}

    def reader():
        ending = None
        while ending is None:
            try:
                b = s.recv(4096)
                if not b:
                    ending = "fin"
                else:
                    rec["chunks"].append([round(time.perf_counter() - t0, 4), len(b)])
                    rec["raw"] += b
                    if "t_reply" not in box and reply_complete(rec["raw"]):
                        box["t_reply"] = round(time.perf_counter() - t0, 4)
            except (ConnectionResetError, ConnectionAbortedError) as e:
                ending = "rst (%s)" % type(e).__name__
            except socket.timeout:
                ending = "timeout"
            except OSError as e:
                ending = "error (%s)" % type(e).__name__
        box["ending"] = ending
        box["t_end"] = round(time.perf_counter() - t0, 4)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        for k in range(0, len(req), SEND_CHUNK):    # one big send() is taken whole on Windows
            s.sendall(req[k:k + SEND_CHUNK])
        rec["t_sent"] = round(time.perf_counter() - t0, 4)
        rec["send_error"] = None
    except OSError as e:
        rec["t_sent"] = None
        rec["send_error"] = "%s: %s" % (type(e).__name__, e)
    if grace is None:
        th.join(timeout + 5)
    else:
        # Wait for a complete reply (or the end), then give the device `grace` s to
        # close; if it has not, close from this side and say so.
        deadline = time.perf_counter() + timeout + 5
        while th.is_alive() and "t_reply" not in box and time.perf_counter() < deadline:
            th.join(0.05)
        if th.is_alive() and "t_reply" in box:
            th.join(grace)
            if th.is_alive():
                box["closed_by_client"] = round(time.perf_counter() - t0, 4)
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                th.join(2)
    s.close()
    rec["t_reply"] = box.get("t_reply")
    if "closed_by_client" in box:
        rec["ending"] = "open: client closed"
        rec["t_end"] = box["closed_by_client"]
    else:
        rec["ending"] = box.get("ending", "reader still running")
        rec["t_end"] = box.get("t_end")
    rec["t_first_byte"] = rec["chunks"][0][0] if rec["chunks"] else None
    raw = rec.pop("raw")
    rec["bytes"] = len(raw)
    rec["status_line"] = raw.split(b"\r\n", 1)[0].decode("latin-1") if raw else None
    rec["raw_head"] = raw[:300].decode("latin-1")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--body", choices=("multipart", "empty", "image"), default="multipart")
    ap.add_argument("--image", help="with --body image: the .bin the device is running")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--grace", type=float, default=2.0,
                    help="s to wait for the device to close after its whole reply (then the client closes)")
    args = ap.parse_args()
    body, ctype = G.multipart_bogus()
    if args.body == "empty":
        body = b""
    st0, _ = G.gi_status(HOST)
    image = None
    if args.body == "image":
        if not args.image:
            raise SystemExit("--body image needs --image")
        data = open(args.image, "rb").read()
        image = {"file": os.path.basename(args.image), "bytes": len(data),
                 "sha256": hashlib.sha256(data).hexdigest(),
                 "elf_sha256": BF.image_elf_sha256(args.image)}
        why = check_image(st0, image["elf_sha256"])
        if why:
            raise SystemExit(why)
        body, ctype = multipart_file(data)
    timeout = args.timeout or (120.0 if args.body == "image" else 10.0)
    out = {"when": time.strftime("%Y%m%dT%H%M%S"), "uptime_before_ms": (st0 or {}).get("uptime_ms"),
           "body_bytes": len(body), "body": args.body, "image": image, "timeout_s": timeout, "grace_s": args.grace,
           "rows": []}
    try:
        for mode in (1, 2, 3, 4):
            for k in range(args.reps):
                code, _ = G.set_mode(HOST, mode)
                got, _ = G.gi_status(HOST)
                row = {"mode": MODES[mode], "rep": k, "set_http": code,
                       "mode_read": (got or {}).get("mode")}
                if code == 200 and got and got.get("mode") == mode:
                    row.update(raw_post("/upload/ota.bin", body, ctype, timeout=timeout, grace=args.grace))
                print("%-8s rep %d: port %s body %s status %-24s ending %-28s bytes %s first %s reply %s sent %s end %s"
                      % (MODES[mode], k, row.get("local_port"), row.get("body_len"),
                         row.get("status_line"), row.get("ending"), row.get("bytes"),
                         row.get("t_first_byte"), row.get("t_reply"), row.get("t_sent"), row.get("t_end")))
                out["rows"].append(row)
                time.sleep(0.5)
    finally:
        G.set_mode(HOST, 0)
        st1, _ = G.gi_status(HOST)
        out["uptime_after_ms"] = (st1 or {}).get("uptime_ms")
        out["mode_after"] = (st1 or {}).get("mode")
        path = os.path.join(os.environ.get("GI_RUNS_DIR") or gi_paths.runs(),   # the chain runner, 2026-10-18
                            "ota_guard_raw_probe_%s_%s.json" % (args.body, out["when"]))
        json.dump(out, open(path, "w"), indent=1)
        print("uptime %s -> %s ms, mode after %s; wrote %s"
              % (out["uptime_before_ms"], out["uptime_after_ms"], out["mode_after"], path))


if __name__ == "__main__":
    main()
