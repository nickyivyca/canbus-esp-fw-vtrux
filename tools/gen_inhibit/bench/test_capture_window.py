#!/usr/bin/env python3
"""Test capture_window.py with a fake capture in place of dumpcap.

    py -3.14 -B tools/gen_inhibit/bench/test_capture_window.py

The fake ticks until it gets CTRL_BREAK (then prints "broke" and exits 0), or ignores it
(--deaf), or exits by itself (--die-after S --rc N). Cases: stopped by the stop file;
a deaf child killed; the max bound; a child that exits early; a stop file left over
before the start refused with no child started. CAPTURE_WINDOW overrides the script
under test (the mutant runner). Scratch in the machine's temp folder. Gen-inhibit
tester, 2026-10-20.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("CAPTURE_WINDOW") or os.path.join(HERE, "capture_window.py")
FAKE = r'''
import signal, sys, time, argparse
ap = argparse.ArgumentParser(); ap.add_argument("--deaf", action="store_true")
ap.add_argument("--die-after", type=float); ap.add_argument("--rc", type=int, default=0)
ap.add_argument("--mark"); a = ap.parse_args()
if a.mark: open(a.mark, "w").write("started")
def on_break(*_):
    if a.deaf: return
    print("broke"); sys.stdout.flush(); sys.exit(0)
signal.signal(getattr(signal, "SIGBREAK", signal.SIGINT), on_break)
t0 = time.time()
while True:
    if a.die_after is not None and time.time() - t0 >= a.die_after:
        print("died"); sys.exit(a.rc)
    time.sleep(0.05)
'''
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def cw(tmp, name, fake_args, extra=(), stop_after=None, timeout=60):
    stopfile = os.path.join(tmp, name + ".stop")
    rec = os.path.join(tmp, name + ".json")
    argv = [sys.executable, "-B", SCRIPT, "--stopfile", stopfile, "--record", rec] + list(extra) + \
           ["--", sys.executable, "-B", os.path.join(tmp, "fake.py")] + list(fake_args)
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.time()
    if stop_after is not None:
        time.sleep(stop_after)
        open(stopfile, "w").close()
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The whole tree: killing only capture_window.py leaves its fake child running
        # (found 2026-10-20, after a mutant that never stops).
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.kill()
        out, _ = p.communicate()
        return None, None, "TIMED OUT " + out, t0
    r = json.load(open(rec)) if os.path.exists(rec) else None
    return p.returncode, r, out, t0


def main():
    tmp = os.path.join(tempfile.gettempdir(), "gen-inhibit-capture-window-test", "t%d" % os.getpid())
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    open(os.path.join(tmp, "fake.py"), "w").write(FAKE)
    try:
        rc, r, out, t0 = cw(tmp, "a", [], stop_after=1.5)
        check("stop file: exit 0", rc == 0, (rc, out))
        check("stop file: how is stopfile", r and r["how"] == "stopfile", r)
        check("stop file: stopped cleanly, child rc 0", r and r["stopped_cleanly"] is True and r["rc"] == 0, r)
        check("stop file: the child saw the break", r and "broke" in r["output_tail"], r and r["output_tail"])
        check("stop file: start before the stop, stop about 1.5 s after the start",
              r and r["t_start"] < r["t_stop_requested"] <= r["t_exit"]
              and 1.0 <= r["t_stop_requested"] - r["t_start"] <= 2.5, r)
        rc, r, out, t0 = cw(tmp, "b", ["--deaf"], extra=["--grace", "1"], stop_after=1.0)
        check("deaf child: killed, recorded as not clean, exit 1",
              rc == 1 and r and r["how"] == "stopfile" and r["stopped_cleanly"] is False, (rc, r))
        rc, r, out, t0 = cw(tmp, "c", [], extra=["--max", "1.5"])
        check("no stop file: the max bound stops it, exit 1",
              rc == 1 and r and r["how"] == "max" and r["stopped_cleanly"] is True, (rc, r))
        check("max: stopped about 1.5 s after the start",
              r and 1.0 <= r["t_stop_requested"] - r["t_start"] <= 3.0, r)
        rc, r, out, t0 = cw(tmp, "d", ["--die-after", "0.5", "--rc", "3"], stop_after=3.0)
        check("a child that exits early: recorded, exit 1",
              rc == 1 and r and r["how"] == "exited by itself" and r["rc"] == 3 and r["t_stop_requested"] is None,
              (rc, r))
        mark = os.path.join(tmp, "e.mark")
        open(os.path.join(tmp, "e.stop"), "w").close()
        rc, r, out, t0 = cw(tmp, "e", ["--mark", mark])
        check("a stop file left over: refused, no child started, no record",
              rc == 2 and not os.path.exists(mark) and r is None, (rc, out))
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
