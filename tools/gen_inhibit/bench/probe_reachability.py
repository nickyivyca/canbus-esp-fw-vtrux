"""Poll the WiCAN's three network surfaces and record which of them answer.

WHY THREE. The open defect is "the device answers ping and nothing else on HTTP".
"Nothing on HTTP" has two completely different causes and a plain `curl` cannot
tell them apart, because both present as a timeout:

  * the LISTEN socket is gone  -- httpd was never started, or exited. The TCP
    handshake fails (refused, or no SYN-ACK).
  * the listen socket is fine but the HTTPD TASK is blocked or starved -- the
    handshake SUCCEEDS, the request is accepted into the backlog, and no
    response ever comes.

So each sample probes, in order: a bare TCP connect to 80, a real GET on 80, and
a bare TCP connect to 3333 (the SLCAN port, whose task is independent of httpd
and is the known recovery channel). A row where connect80 is up and get80 is
down localises the fault to the httpd task; a row where both are down but 3333 is
up localises it to httpd's socket; all three down means the stack or the whole
scheduler.

ICMP is deliberately not probed here -- it is answered inside lwIP's own thread
and so tells us only that the stack lives, which the 3333 connect already covers
with the added information that an application task ran.

    py -3.11 tools/gen_inhibit/bench/probe_reachability.py \
        --seconds 180 --out runs/reboot_hang_probe.csv

Prints a line per state CHANGE, so a long outage is one line rather than
hundreds, and writes every sample to the CSV for exact timing.
"""

import argparse
import datetime
import io
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = gi_paths.runs()
HOST = "192.168.80.1"


def tcp_up(host, port, timeout):
    t = time.time()
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True, time.time() - t, ""
    except Exception as exc:
        return False, time.time() - t, type(exc).__name__
    finally:
        try:
            s.close()
        except Exception:
            pass


def http_up(host, timeout):
    t = time.time()
    try:
        with urllib.request.urlopen("http://%s/check_status" % host,
                                    timeout=timeout) as r:
            body = r.read()
        return True, time.time() - t, "%d bytes" % len(body)
    except urllib.error.HTTPError as exc:
        return True, time.time() - t, "HTTP %d" % exc.code
    except Exception as exc:
        return False, time.time() - t, type(exc).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--seconds", type=float, default=180.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    fh = None
    if args.out:
        out = args.out
        if not os.path.isabs(out) and os.path.dirname(out) in ("", "runs"):
            out = os.path.join(OUT_DIR, os.path.basename(out))
        d = os.path.dirname(os.path.abspath(out))
        if not os.path.isdir(d):
            os.makedirs(d)
        fh = io.open(out, "w", encoding="ascii", newline="\n")
        fh.write("t_s,wall,connect80,get80,connect3333,get_detail,get_s\n")

    t0 = time.time()
    last = None
    print("probing %s for %.0f s (state changes only)" % (args.host, args.seconds))
    sys.stdout.flush()
    while time.time() - t0 < args.seconds:
        el = time.time() - t0
        c80, _, _ = tcp_up(args.host, 80, args.timeout)
        g80, gs, detail = http_up(args.host, args.timeout)
        c33, _, _ = tcp_up(args.host, 3333, args.timeout)
        state = (c80, g80, c33)
        if fh:
            fh.write("%.3f,%s,%d,%d,%d,%s,%.3f\n"
                     % (el, datetime.datetime.now().isoformat(
                            timespec="milliseconds"),
                        c80, g80, c33, detail, gs))
            fh.flush()
        if state != last:
            print("[%8.3f] connect80=%s get80=%s connect3333=%s  (%s, %.2fs)"
                  % (el, "UP" if c80 else "--", "UP" if g80 else "--",
                     "UP" if c33 else "--", detail, gs))
            sys.stdout.flush()
            last = state
        rem = args.interval - (time.time() - t0 - el)
        if rem > 0:
            time.sleep(rem)
    if fh:
        fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
