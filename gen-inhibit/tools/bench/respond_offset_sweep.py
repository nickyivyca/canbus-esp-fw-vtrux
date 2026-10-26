"""RESPOND offset sweep: does the probe offset drive the receive-queue drops? (2026-10-10)

Reviewer's request. The implementor reads the RESPOND rx_missed (13 and 16 per 30 s
in the presence arms) as the probe's busy-wait for `offset_us` at worker priority
(RESPOND only), and pinned a prediction before the run:
    offset 100: rx_missed 0 or low single digits
    offset 500: about 10-20 in 30 s
    offset 1000: clearly worse than 500
falsified if rx_missed is roughly flat across the offsets, or if 100 still drops.

Nine 30 s RESPOND arms of e4_device_calibration.py (same replay window, PCAN
replay, Kvaser witness, X2 app running), offsets interleaved so drift cannot line
up with offset. Per arm it records the device's offset_us as read back, rx_missed,
rx_overrun, ctr_ok, ctr_bad, the replay and witness rates and the probes on the
wire. An arm whose replay rate is more than 1 % off the median, or whose device did
not read back the requested offset, is flagged NOT COMPARABLE and kept in the table.
Judged on rx_missed, never ctr_bad (one step per run of consecutive drops).

--plan wifi (reviewer, same day): at offset 500, RESPOND idle / loaded / loaded /
idle, then INHIBIT the same. "loaded" is e4's own --arm loaded, which polls the
status page throughout the arm (the 2026-09 WiFi-flood method); each arm records
hammer_requests. Says whether drops follow the mode, the WiFi load, or neither.

--plan poll (reviewer, same day): six RESPOND idle arms at offset 500, unpolled and
polled at 1 Hz (`e4 --poll-hz 1`, which records drv.msgs_to_rx from every answered
page) interleaved. Polled arms are their own condition and are never pooled with the
unpolled ones.

BURSTINESS. A mean replay rate cannot see bunching: on 2026-10-10 the same window
came out with 81-91 % of frames back to back (gap < 300 us) and unbroken runs of up
to 199 frames for five minutes, against 74-77 % and runs of 37-120 in the 17 arms
either side, and the low-priority probes starved while the mean rate read normal.
Each arm's witness log is therefore measured, and an arm whose back-to-back fraction
exceeds BURST_B2B_MAX (0.79, CHOSEN from those 17 clean arms, not derived) is NOT
COMPARABLE. It is an absolute bound on purpose: a plan that is bursty throughout
has a bursty median.

Before every arm the laptop must be on the WiCAN's SoftAP and the device must
answer; if not, it rejoins (netsh) and waits, and stops the run rather than
recording a string of arms that never started. The first attempt on 2026-10-10 ran
all nine arms into a dropped laptop WLAN, each refusing at its first status read,
and then crashed taking a median of nothing.

    GI_WICAN_SSID=... py -3.14 gen-inhibit/tools/bench/respond_offset_sweep.py [--plan offsets|wifi]
    py -3.14 .../respond_offset_sweep.py --summarise-only STAMP [--plan ...]
Writes runs/respond_offset_sweep_<plan>_<stamp>.json and one e4 run file per arm.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()
ORDER = [100, 500, 1000, 500, 1000, 100, 1000, 100, 500]
PLANS = {
    "offsets": [("respond", "idle", off, 0) for off in ORDER],
    "wifi": [("respond", "idle", 500, 0), ("respond", "loaded", 500, 0),
             ("respond", "loaded", 500, 0), ("respond", "idle", 500, 0),
             ("inhibit", "idle", 500, 0), ("inhibit", "loaded", 500, 0),
             ("inhibit", "loaded", 500, 0), ("inhibit", "idle", 500, 0)],
    "poll": [("respond", "idle", 500, 0), ("respond", "idle", 500, 1),
             ("respond", "idle", 500, 1), ("respond", "idle", 500, 0),
             ("respond", "idle", 500, 0), ("respond", "idle", 500, 1)],
}
BURST_B2B_MAX = 0.79
B2B_GAP_US = 300.0


def burstiness(times):
    """(fraction of inter-frame gaps under B2B_GAP_US, longest unbroken run of them)."""
    t = sorted(times)
    gaps = [(b - a) * 1e6 for a, b in zip(t, t[1:])]
    if not gaps:
        return None, None
    run = best = 0
    for g in gaps:
        run = run + 1 if g < B2B_GAP_US else 0
        best = max(best, run)
    return round(sum(1 for g in gaps if g < B2B_GAP_US) / len(gaps), 4), best


def witness_times(path):
    import gzip
    pat = re.compile(r"\((\d+\.\d+)\)")
    with gzip.open(path, "rt") as f:
        return [float(m.group(1)) for m in (pat.match(ln) for ln in f) if m]


def field(dev, key):
    m = re.search(r'"%s": ([^,}\]]+)' % re.escape(key), json.dumps(dev))
    if not m:
        return None
    v = m.group(1).strip()
    try:
        return int(v)
    except ValueError:
        return v


def row_of(path, offset, arm="idle", mode="respond", poll=0):
    d = json.load(open(path, encoding="utf-8"))
    a = d["arms"][arm]
    wl = path[:-5] + "_%s_witness.log.gz" % arm
    b2b, run = burstiness(witness_times(wl)) if os.path.exists(wl) else (None, None)
    dev = a.get("device") or {}
    return {"offset": offset, "mode": mode, "arm": arm, "poll_hz": poll,
            "b2b": b2b, "longest_b2b_run": run,
            "polls": len(a.get("queue_series") or []),
            "queue_max": max([q[1] for q in (a.get("queue_series") or []) if q[1] is not None],
                             default=None),
            "file": os.path.basename(path),
            "hammer_requests": a.get("hammer_requests"),
            "offset_read": field(dev, "offset_us"),
            "rx_missed": field(dev, "rx_missed"), "rx_overrun": field(dev, "rx_overrun"),
            "ctr_ok": field(dev, "ctr_ok"), "ctr_bad": field(dev, "ctr_bad"),
            "tx_ok": field(dev, "tx_ok"), "tx_fail": field(dev, "tx_fail"),
            "send_fps": a.get("send_fps"), "witness_fps": a.get("witness_fps"),
            "probes_on_wire": a.get("probes_on_wire"), "void": a.get("void"),
            "void_reasons": a.get("void_reasons")}


def comparability(rows, tol=0.01):
    """Flag rows whose replay rate is off the median by more than tol, or whose
    device did not read back the requested offset."""
    fps = [r["send_fps"] for r in rows if r["send_fps"]]
    if not fps:
        for r in rows:
            r["not_comparable"] = ["no replay rate"]
        return None
    med = statistics.median(fps)
    for r in rows:
        why = []
        if not r["send_fps"] or abs(r["send_fps"] - med) > tol * med:
            why.append("replay %.1f fps vs median %.1f" % (r["send_fps"] or 0, med))
        if r["offset_read"] != r["offset"]:
            why.append("device read offset %r" % r["offset_read"])
        if r["void"]:
            why.append("arm VOID: %s" % r["void_reasons"])
        if r.get("b2b") is None or r["b2b"] > BURST_B2B_MAX:
            why.append("replay bunched: %r of frames back to back (max %.2f), longest run %r"
                       % (r.get("b2b"), BURST_B2B_MAX, r.get("longest_b2b_run")))
        r["not_comparable"] = why
    return med


def by_offset(rows):
    out = {}
    for r in rows:
        if not r["not_comparable"]:
            out.setdefault(r["offset"], []).append(r["rx_missed"])
    return out


def by_condition(rows):
    out = {}
    for r in rows:
        if not r["not_comparable"]:
            key = "%s/%s" % (r["mode"], r["arm"])
            if r.get("poll_hz"):
                key += "/poll%gHz" % r["poll_hz"]
            out.setdefault(key, []).append(r["rx_missed"])
    return out


def ensure_device(tries=20):
    """On the SoftAP and answering, or False. Rejoins with netsh if needed."""
    import lowid_flood_skip_bench as LF
    ssid = os.environ.get("GI_WICAN_SSID")
    for i in range(tries):
        if LF.page() is not None:
            return True
        if ssid and i % 5 == 0:
            subprocess.run(["netsh", "wlan", "connect", "name=%s" % ssid],
                           capture_output=True, text=True, timeout=15)
        time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summarise-only", default=None, metavar="STAMP")
    ap.add_argument("--plan", default="offsets", choices=sorted(PLANS))
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    stamp = a.summarise_only or time.strftime("%Y%m%dT%H%M%S")
    plan = PLANS[a.plan]
    rows = []
    for k, (mode, arm, off, poll) in enumerate(plan):
        out = os.path.join(RUNS, "e4_sweep_%s_%s_%s_offset%d_poll%d_%s_arm%d.json"
                           % (a.plan, mode, arm, off, poll, stamp, k + 1))
        if not a.summarise_only:
            print("== arm %d/%d: %s %s offset %d us poll %g Hz (%s)"
                  % (k + 1, len(plan), mode, arm, off, poll, time.strftime("%H:%M:%S")),
                  flush=True)
            if not ensure_device():
                print("   STOPPED: the device does not answer and a rejoin did not help")
                break
            r = subprocess.run([sys.executable, os.path.join(HERE, "e4_device_calibration.py"),
                                "--arm", arm, "--mode", mode, "--offset-us", str(off),
                                "--poll-hz", str(poll), "--out", out],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(out):
                print("   e4 failed rc %d: %s" % (r.returncode, r.stdout[-400:] + r.stderr[-400:]))
                continue
        rows.append(row_of(out, off, arm, mode, poll))
        x = rows[-1]
        print("   offset read %s, rx_missed %s, rx_overrun %s, ctr_ok %s, ctr_bad %s, "
              "replay %.1f fps, tx_ok %s, hammer %s, b2b %s run %s, polls %s queue max %s"
              % (x["offset_read"], x["rx_missed"], x["rx_overrun"], x["ctr_ok"], x["ctr_bad"],
                 x["send_fps"] or 0, x["tx_ok"], x["hammer_requests"], x["b2b"],
                 x["longest_b2b_run"], x["polls"], x["queue_max"]), flush=True)
    med = comparability(rows)
    groups = by_offset(rows) if a.plan == "offsets" else by_condition(rows)
    rec = {"when": stamp, "plan": a.plan, "arms": plan, "median_send_fps": med, "rows": rows,
           "rx_missed_by_group": {str(k): v for k, v in sorted(groups.items())}}
    path = os.path.join(RUNS, "respond_offset_sweep_%s_%s.json" % (a.plan, stamp))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    print("\nrx_missed by group (comparable arms):",
          {k: v for k, v in sorted(groups.items())})
    for r in rows:
        if r["not_comparable"]:
            print("NOT COMPARABLE:", r["file"], r["not_comparable"])
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
