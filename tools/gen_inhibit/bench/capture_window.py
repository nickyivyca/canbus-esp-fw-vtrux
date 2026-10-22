#!/usr/bin/env python3
"""Run a capture (dumpcap) until a stop file appears, then stop it cleanly, and record
when it started and stopped. For B21 and B11raw (reviewer, 2026-10-20): each capture
stops a few seconds after its probe or arm exits, instead of running a fixed 600 s / 75 s.

    py -3.14 -B capture_window.py --stopfile F --record R.json [--max 900] [--grace 20] -- <dumpcap argv...>

The child runs in its own process group. When F exists (the calling script creates it
after its probe or arm), the child gets CTRL_BREAK_EVENT, which dumpcap treats as a stop
and closes its file cleanly (measured 2026-10-20 on NICKY-XPS: exit 0, file read by
capinfos). If it has not exited within --grace s it is killed, and the record says so.
--max bounds the whole capture whatever happens. The record holds the argv, the start
(after the child was created), the stop request, the exit, how it ended
("stopfile", "max", "exited by itself"), the return code and the child's output tail.
Exit status: 0 only if the child stopped on the stop file and exited 0 by itself.
Gen-inhibit tester.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

POLL_S = 0.2


def stop(p, grace):
    """CTRL_BREAK to the child's group; kill it if it is still running after grace s.
    Returns True if it exited by itself."""
    try:
        if os.name == "nt":
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(p.pid, signal.SIGINT)
    except OSError:
        pass
    try:
        p.wait(timeout=grace)
        return True
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        return False


def run(argv, stopfile, max_s, grace):
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace", **kw)
    rec = {"argv": argv, "t_start": time.time(), "t_stop_requested": None, "how": None,
           "stopped_cleanly": None}
    while True:
        if p.poll() is not None:
            rec["how"] = "exited by itself"
            break
        if os.path.exists(stopfile):
            rec["how"] = "stopfile"
            break
        if time.time() - rec["t_start"] >= max_s:
            rec["how"] = "max"
            break
        time.sleep(POLL_S)
    if p.poll() is None:
        rec["t_stop_requested"] = time.time()
        rec["stopped_cleanly"] = stop(p, grace)
    out = p.stdout.read() if p.stdout else ""
    rec["t_exit"] = time.time()
    rec["rc"] = p.returncode
    rec["output_tail"] = out[-600:]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stopfile", required=True)
    ap.add_argument("--record", required=True)
    ap.add_argument("--max", type=float, default=900.0)
    ap.add_argument("--grace", type=float, default=20.0)
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    if not argv:
        ap.error("no capture command after --")
    if os.path.exists(a.stopfile):
        ap.error("the stop file exists before the capture started: %s" % a.stopfile)
    rec = run(argv, a.stopfile, a.max, a.grace)
    rec.update({"stopfile": a.stopfile, "max_s": a.max, "grace_s": a.grace})
    with open(a.record, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    print("capture %s after %.1f s (%s, rc %s)" % (
        rec["how"], rec["t_exit"] - rec["t_start"],
        "stopped cleanly" if rec["stopped_cleanly"] else ("KILLED" if rec["stopped_cleanly"] is False else "not stopped"),
        rec["rc"]))
    return 0 if rec["how"] == "stopfile" and rec["stopped_cleanly"] and rec["rc"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
