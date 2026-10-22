#!/usr/bin/env python3
"""Spec 8.1 row 11, bench half (reviewer, 2026-10-16, pre-port batch 3): a failed
OTA upload from IDLE, and what the device does after it. Gen-inhibit tester.

Two kinds, both run (row 11 covers every error exit of the handler):
  short      the headers declare the whole image's multipart length; only the
             first --cut bytes of the body are sent, then a --stall (s) with the
             connection open, then this side half-closes (FIN). Expected: no HTTP
             status and no restart, bench_flash.py verdict a1.
  truncated  a complete, well-formed request whose image is cut to --cut bytes.
             Expected: a 4xx/5xx, verdict b. Accepted instead is a finding (the
             image's own validation must refuse it): stop and report.

A second connection reads /gen_inhibit throughout, from before the upload to
--after s past its end (30 s at least, for an a1 to be confirmed), and keeps each
read's request and reply times beside the upload's, so it shows whether any read
was answered INSIDE the upload (8.1 row 12: the web server runs one request at a
time, so reads may queue behind it). It records; it does not judge, apart from
bench_flash.classify's verdict on the same readings. The arm that must work
afterwards is a separate e4_device_calibration.py run.

SAFETY: refused unless the device is in mode 0 and runs the image given (its
page's elf_sha256), and unless --cut is strictly inside the image, so neither kind
can carry anything but a part of the running build.

    GI_FW_TOOLS=<firmware repo>/tools/gen_inhibit py -3.14 failed_upload_probe.py --kind short|truncated --image <running .bin> [--cut N] [--stall 3] [--after 35]
Writes runs/failed_upload_<kind>_<stamp>.json. Tested by test_failed_upload_probe.py.
"""
import argparse
import hashlib
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, gi_paths.fw_tools())
import ota_guard_raw_probe as P   # noqa: E402
import bench_flash as BF          # noqa: E402

HOST, PORT = P.HOST, P.PORT
OTA_PATH = "/upload/ota.bin"


def refuse(page, image_elf, cut, size):
    """None if this run may go ahead, else why not."""
    why = P.check_image(page, image_elf)
    if why:
        return why
    if page.get("mode") != 0:
        return "the device is in mode %s, not 0 (a failed upload is tested from IDLE)" % page.get("mode")
    if not 0 < cut < size:
        return "--cut %d is not strictly inside the %d-byte image" % (cut, size)
    return None


def truncated_body(data, cut):
    """(body, content type): a complete multipart request carrying data[:cut]."""
    return P.multipart_file(data[:cut])


def image_offset():
    """Where the file's bytes start in P.multipart_file's body: after the part's
    headers, which end in a blank line."""
    empty = P.multipart_file(b"")[0]
    return empty.index(b"\r\n\r\n") + 4


def raw_post_epochs(rec):
    """epoch_start / epoch_end for a P.raw_post record, whose times run from just
    before its connect (t_connected after it, epoch_connected at it)."""
    t0 = rec["epoch_connected"] - rec["t_connected"]
    return {"epoch_start": rec["epoch_connected"],
            "epoch_end": t0 + (rec["t_end"] if rec.get("t_end") is not None else rec["t_connected"])}


def parse_status(status_line):
    """The HTTP status code in a status line, or None."""
    parts = (status_line or "").split()
    if len(parts) >= 2 and parts[0].startswith("HTTP/") and parts[1].isdigit():
        return int(parts[1])
    return None


def short_post(path, body, ctype, cut, stall, timeout=40.0, host=None, port=None):
    """Headers declaring len(body), then body[:cut], `stall` s with the connection
    open, then a half-close; reads whatever the device sends until it ends the
    connection or `timeout`. Epoch times throughout."""
    host, port = host or HOST, port or PORT
    req = P.build_request(path, body, ctype, host)
    head_len = len(req) - len(body)
    rec = {"declared_len": len(body), "sent_body_len": cut, "stall_s": stall}
    s = socket.create_connection((host, port), timeout=timeout)
    rec["epoch_start"] = time.time()
    rec["local_port"] = s.getsockname()[1]
    raw = b""
    box = {}

    def reader():
        nonlocal raw
        ending = None
        while ending is None:
            try:
                b = s.recv(4096)
                if not b:
                    ending = "fin"
                else:
                    raw += b
                    box.setdefault("epoch_first_byte", time.time())
            except (ConnectionResetError, ConnectionAbortedError) as e:
                ending = "rst (%s)" % type(e).__name__
            except socket.timeout:
                ending = "timeout"
            except OSError as e:
                ending = "error (%s)" % type(e).__name__
        box["ending"], box["epoch_end"] = ending, time.time()

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    part = req[:head_len + cut]
    try:
        for k in range(0, len(part), P.SEND_CHUNK):
            s.sendall(part[k:k + P.SEND_CHUNK])
        rec["epoch_sent"] = time.time()
        time.sleep(stall)
        # Stamped before the shutdown: the reader thread can see the peer's FIN
        # and stamp epoch_end before shutdown() returns to this thread.
        rec["epoch_halfclosed"] = time.time()
        s.shutdown(socket.SHUT_WR)
        rec["send_error"] = None
    except OSError as e:
        rec["send_error"] = "%s: %s" % (type(e).__name__, e)
    th.join(timeout + 5)
    s.close()
    rec["ending"] = box.get("ending", "reader still running")
    rec["epoch_end"] = box.get("epoch_end", time.time())
    rec["epoch_first_byte"] = box.get("epoch_first_byte")
    rec["bytes"] = len(raw)
    rec["status_line"] = raw.split(b"\r\n", 1)[0].decode("latin-1") if raw else None
    rec["raw_head"] = raw[:300].decode("latin-1")
    return rec


def poll_pages(stop, rows, base_url, period=0.25, read_timeout=60.0):
    """Read base_url + /gen_inhibit until `stop` is set; one row per read with its
    epoch request and reply times. A read held behind the upload simply answers
    late, so read_timeout is long."""
    while not stop.is_set():
        t_req = time.time()
        row = {"t_req": t_req}
        try:
            txt = urllib.request.urlopen(base_url + "/gen_inhibit", timeout=read_timeout).read().decode()
            row["page"], _ = json.JSONDecoder().raw_decode(txt.lstrip())
        except Exception as e:
            row["error"] = "%s: %s" % (type(e).__name__, e)
        row["t_resp"] = time.time()
        rows.append(row)
        stop.wait(max(0.0, period - (row["t_resp"] - t_req)))


def bf_readings(rows):
    """Page rows in the form bench_flash.classify reads (identity + t_req/t_resp)."""
    out = []
    for r in rows:
        ident = BF.identity(r.get("page"))
        if ident:
            out.append(dict(ident, t_req=r["t_req"], t_resp=r["t_resp"]))
    return out


def inside_upload(rows, start, end):
    """The page rows whose read was requested after the upload connected and answered
    while it was open. A read already in flight at the connect is not inside it
    (reviewer, 2026-10-16)."""
    return [r for r in rows if isinstance(r.get("page"), dict) and r["t_req"] >= start and start < r["t_resp"] < end]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=("short", "truncated"), required=True)
    ap.add_argument("--image", required=True, help="the .bin the device is running")
    ap.add_argument("--cut", type=int, default=None, help="image bytes sent / kept (default: half)")
    ap.add_argument("--stall", type=float, default=3.0, help="short: s held open after the partial body")
    ap.add_argument("--after", type=float, default=35.0, help="s of page reads after the upload's end")
    args = ap.parse_args()
    data = open(args.image, "rb").read()
    image = {"file": os.path.basename(args.image), "bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "elf_sha256": BF.image_elf_sha256(args.image)}
    cut = args.cut if args.cut is not None else len(data) // 2
    t_a = time.time()
    page0, err = P.G.gi_status(HOST)
    t_b = time.time()
    why = refuse(page0 or {}, image["elf_sha256"], cut, len(data))
    if why:
        raise SystemExit(why if page0 else err)
    if args.after < BF.A1_CONFIRM_S:
        raise SystemExit("--after must be at least %s s, so an a1 can be confirmed" % BF.A1_CONFIRM_S)
    out = {"when": time.strftime("%Y%m%dT%H%M%S"), "kind": args.kind, "image": image, "cut": cut,
           "stall_s": args.stall if args.kind == "short" else None, "after_s": args.after}
    rows, stop = [], threading.Event()
    poller = threading.Thread(target=poll_pages, args=(stop, rows, "http://%s" % HOST), daemon=True)
    poller.start()
    time.sleep(1.5)
    if args.kind == "short":
        body, ctype = P.multipart_file(data)
        up = short_post(OTA_PATH, body, ctype, image_offset() + cut, args.stall)
    else:
        body, ctype = truncated_body(data, cut)
        up = P.raw_post(OTA_PATH, body, ctype, timeout=120.0, grace=2.0)
        up.update(raw_post_epochs(up))
    out["upload"] = up
    t_end = up["epoch_end"]
    while time.time() < t_end + args.after:
        time.sleep(0.5)
    stop.set()
    poller.join(65)
    out["pages"] = rows
    code = parse_status(up.get("status_line"))
    before = dict(BF.identity(page0), t_req=t_a, t_resp=t_b)
    out["bench_flash_verdict"] = BF.classify(code, t_end, before, bf_readings(rows), image["elf_sha256"])
    ins = inside_upload(rows, up["epoch_start"], t_end)
    out["reads_answered_inside_upload"] = len(ins)
    path = gi_paths.runs("failed_upload_%s_%s.json" % (args.kind, out["when"]))
    json.dump(out, open(path, "w"), indent=1)
    print("%s: status %s, ending %s; bench_flash %s; %d page reads, %d answered inside the upload; wrote %s"
          % (args.kind, up.get("status_line"), up.get("ending"), out["bench_flash_verdict"][0],
             len(rows), len(ins), path))


if __name__ == "__main__":
    main()
