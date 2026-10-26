#!/usr/bin/env python3
"""Record the WiCAN's whole /gen_inhibit page at a fixed rate, and pick the pages
either side of an instant. Gen-inhibit tester, 2026-10-16 (reviewer: the
implementor's account of the liveflag-mmode diag gap predicts sched.aborts +1 and
sched.cls.inhibit.withdrawn +1 at the M-mode latch, skips_withdrawn and skips_late
unchanged, and neither on a low-SoC latch). interlock_bench's own samples keep only
summary fields, so this runs beside it as a separate process.

  record   poll for --seconds at --hz; one JSON line per read: t_req, t_resp, page
           (or the error). Read-only: GET only.
  around   for a recording and an epoch instant, print FIELDS from the last page
           read wholly before it and the first read wholly after it + --settle s.

    py -3.14 .../full_page_recorder.py record --seconds 20 --hz 2 --out runs/<x>.jsonl
    py -3.14 .../full_page_recorder.py around runs/<x>.jsonl <epoch> [--settle 0.3]
Tested by test_full_page_recorder.py.
"""
import argparse
import json
import sys
import time
import urllib.request

HOST = "192.168.80.1"
FIELDS = ("tx_ok", "skips_withdrawn", "skips_late", "sched.aborts", "sched.cls.inhibit.withdrawn",
          "mode", "disabled", "disable_reason", "uptime_ms")


def field(page, dotted):
    """page["a"]["b"]... for "a.b...", or None if any step is missing."""
    o = page
    for k in dotted.split("."):
        if not isinstance(o, dict) or k not in o:
            return None
        o = o[k]
    return o


def around(rows, instant, settle=0.3):
    """(the last page whose read ENDED before instant, the first whose read
    STARTED at or after instant + settle). Rows without a page are skipped."""
    pages = [r for r in rows if isinstance(r.get("page"), dict)]
    before = [r for r in pages if r["t_resp"] < instant]
    after = [r for r in pages if r["t_req"] >= instant + settle]
    return (before[-1] if before else None), (after[0] if after else None)


def record(seconds, hz, out, host=HOST):
    period = 1.0 / hz
    end = time.time() + seconds
    with open(out, "w") as fh:
        while time.time() < end:
            t_req = time.time()
            row = {"t_req": t_req}
            try:
                txt = urllib.request.urlopen("http://%s/gen_inhibit" % host, timeout=2).read().decode()
                row["page"], _ = json.JSONDecoder().raw_decode(txt.lstrip())
            except Exception as e:
                row["error"] = "%s: %s" % (type(e).__name__, e)
            row["t_resp"] = time.time()
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            time.sleep(max(0.0, period - (time.time() - t_req)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("record", "around"))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--out")
    ap.add_argument("--settle", type=float, default=0.3)
    a = ap.parse_args()
    if a.action == "record":
        record(a.seconds, a.hz, a.out)
        return 0
    rows = [json.loads(l) for l in open(a.args[0])]
    b, f = around(rows, float(a.args[1]), a.settle)
    for name, r in (("before", b), ("after", f)):
        if r is None:
            print("%-6s none" % name)
            continue
        print("%-6s read %.3f-%.3f s from the instant: %s" % (
            name, r["t_req"] - float(a.args[1]), r["t_resp"] - float(a.args[1]),
            {k: field(r["page"], k) for k in FIELDS}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
