"""The error frames at every WiCAN restart: only when its controller is active, or always?

Gen-inhibit tester, 2026-10-12. Reviewer's request after the user's ruling that the
burst be characterised before the truck. Seen first on dace947: every auto-arm restart
put 131-138 error frames on the segment, the first ~0.24 s before the computed boot
instant. Without changing the image (dace947 _man), compare /system_reboot restarts:
  off   the device in mode 0, CAN driver not installed (drv null);
  live  the device live in INHIBIT (mode 3 POSTed --arm-at s into the replay).
Both under the same full-bus replay, so the only difference is the controller's state.

Per cycle: the PCAN replays --seconds of --capture (auto_reset on, every send and every
failed send recorded); the Kvaser witnesses (error frames by hardware timestamp); the
page is polled at 5 Hz; /system_reboot is POSTed --post-at s into the replay. The
manual image boots OFF, so after the restart the device is silent in both conditions.

Recorded and summarised per cycle (nothing judged; the question is the reviewer's):
  - the error frames: count, first and last relative to the reboot POST and to the
    computed boot instant (median of read time - uptime over the reads after the
    restart), and their shape: clumps split at gaps over CLUMP_GAP_S, with the largest
    gap inside the burst;
  - replayed frames lost: a MULTISET comparison of what the PCAN sent against what the
    witness saw, per (id, payload), excluding 0x051 (the device answers on 0x051 when
    live, and its frames equal the VCM's) -- an in-order walk misaligns once a frame is
    lost (2026-10-12, autoarm_20261012T180826). 0x051 is reported as a lower bound.
    Also the count lost inside the restart window, by the sent frames' times;
  - the PCAN's send failures and its status at the end;
  - the page before the POST and the first and last reads after the restart.
The two clocks (host epoch; the Kvaser's timestamps) are tied by the replay: the
minimum of (wire time - send time) over replayed frames (as autoarm_boot_to_live).

    GI_WICAN_SSID=... py -3.14 restart_error_frames.py --condition off|live --cycles 3 --power "..."
Writes runs/restart_error_frames_<condition>_<stamp>.json (one per cycle) and the
witness log beside it.

2026-10-13, the restart-hook trial (reviewer's plan): --hook on|off sets the trial switch
in mode 0 before each cycle (--hook-set, --hook-values, --hook-field name the endpoint and
the page field, from the build message) and refuses the cycle unless the page reads it
back as requested. The field is polled with the rest, and the cycle records the last
readback before the restart POST plus every distinct reading after it (the switch may
not survive a restart). --restart crash POSTs --crash-path instead of /system_reboot.
Each cycle also records the build and the reset reasons read after the restart. Files
are then named restart_error_frames_<condition>[_hookon|_hookoff][_crash]_<stamp>.
"""
import argparse
import gzip
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
CLUMP_GAP_S = 0.001          # error frames closer than this are one clump
CMD_ID = 0x051
WINDOW_BEFORE_S = 1.0        # the restart window: POST - 1 s ...
WINDOW_AFTER_BOOT_S = 3.0    # ... to boot + 3 s


def clumps(times, gap=CLUMP_GAP_S):
    """Sorted error times -> [(start, end, n)], a new clump wherever the gap exceeds gap."""
    out = []
    for t in sorted(times):
        if out and t - out[-1][1] <= gap:
            s, _e, n = out[-1]
            out[-1] = (s, t, n + 1)
        else:
            out.append((t, t, 1))
    return out


def burst_summary(times, post_wire, boot_wire, gap=CLUMP_GAP_S):
    """Error frames inside the restart window, relative to the POST and to the boot."""
    lo = post_wire - WINDOW_BEFORE_S
    hi = (boot_wire if boot_wire is not None else post_wire) + WINDOW_AFTER_BOOT_S
    e = sorted(t for t in times if lo <= t <= hi)
    out = {"in_window": len(e), "outside_window": len(times) - len(e)}
    if not e:
        return out
    cl = clumps(e, gap)
    gaps = [b - a for a, b in zip(e, e[1:])]
    out.update({
        "first_rel_post_s": round(e[0] - post_wire, 4), "last_rel_post_s": round(e[-1] - post_wire, 4),
        "first_rel_boot_s": None if boot_wire is None else round(e[0] - boot_wire, 4),
        "last_rel_boot_s": None if boot_wire is None else round(e[-1] - boot_wire, 4),
        "span_ms": round((e[-1] - e[0]) * 1000, 2),
        "clumps": len(cl),
        "clump_sizes": [n for _s, _e, n in cl],
        "clump_starts_rel_boot_s": (None if boot_wire is None
                                    else [round(s - boot_wire, 4) for s, _e, _n in cl]),
        "largest_gap_ms": round(max(gaps) * 1000, 3) if gaps else 0.0,
        "median_gap_us": round(statistics.median(gaps) * 1e6, 1) if gaps else None})
    return out


def multiset_loss(sent, seen, exclude=(CMD_ID,)):
    """sent, seen: [(t, id, data bytes)]. Per (id, payload), how many sent frames the
    witness never saw. Ids in exclude are left out (reported separately)."""
    s = Counter((i, bytes(d)) for _t, i, d in sent if i not in exclude)
    w = Counter((i, bytes(d)) for _t, i, d in seen if i not in exclude)
    lost = {k: n - w.get(k, 0) for k, n in s.items() if n > w.get(k, 0)}
    return {"sent": sum(s.values()), "lost": sum(lost.values()),
            "lost_keys": ["%03X#%s x%d" % (i, d.hex(), n) for (i, d), n in sorted(lost.items())][:20]}


def cmd_loss_lower_bound(sent, seen):
    """0x051 only: per payload, sent minus seen where positive. A lower bound -- a device
    answer equal to a lost VCM frame hides it."""
    s = Counter(bytes(d) for _t, i, d in sent if i == CMD_ID)
    w = Counter(bytes(d) for _t, i, d in seen if i == CMD_ID)
    return {"sent": sum(s.values()), "seen": sum(w.values()),
            "lost_lower_bound": sum(n - w.get(k, 0) for k, n in s.items() if n > w.get(k, 0))}


def hook_state(v):
    """A page reading of the trial hook switch -> True / False, or None when it is absent
    or unreadable. Accepts a bool, 0/1, or on/off/true/false text."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("1", "on", "true", "0", "off", "false"):
        return v.strip().lower() in ("1", "on", "true")
    return None


def hook_judged(reads, reboot_epoch, want):
    """reads: [(t, ..., hook reading last)]. The cycle is judged by the LAST readback taken
    before the restart POST (the switch may not survive a restart). Returns the readback,
    whether it equals want, and every distinct reading after the POST."""
    before = [r for r in reads if reboot_epoch is not None and r[0] < reboot_epoch]
    after = [r for r in reads if reboot_epoch is not None and r[0] >= reboot_epoch]
    last = hook_state(before[-1][-1]) if before else None
    seen_after = []
    for r in after:
        s = hook_state(r[-1])
        if s not in seen_after:
            seen_after.append(s)
    return {"readback_before_restart": last, "as_requested": last is not None and last == want,
            "readings_after_restart": seen_after}


def trigger_post(name, repo=None):
    """The POST path of a test-only switch's trigger, from the firmware repo's table
    (vtrux_checks.json, "inert_triggers"). One table, so the test runs and the 12.4
    inert check send the same request."""
    repo = repo or os.environ.get("GI_FW_REPO") or gi_paths.repo_root()
    with open(os.path.join(repo, "vtrux_checks.json"), encoding="utf-8") as fh:
        probes = json.load(fh)["inert_triggers"][name]["probes"]
    posts = [p for m, p in probes if m == "POST"]
    if len(posts) != 1:
        raise SystemExit("trigger %s has %d POST probes, expected 1" % (name, len(posts)))
    return posts[0]


def record_get(lf, path):
    """The JSON a GET path returns, or why it could not be read. Recorded as read."""
    if not path:
        return None
    try:
        d, _ = json.JSONDecoder().raw_decode(lf.dev_get(path).lstrip())
        return d
    except Exception as e:
        return "unread: %s" % repr(e)[:80]


def window_loss(sent_epoch, seen, offset, lo_wire, hi_wire, exclude=(CMD_ID,)):
    """Multiset loss restricted to sent frames whose send time (on the wire clock) falls in
    [lo_wire, hi_wire], against the witness frames in that window widened by 0.1 s."""
    s = [(t + offset, i, d) for t, i, d in sent_epoch if lo_wire <= t + offset <= hi_wire]
    w = [(t, i, d) for t, i, d in seen if lo_wire - 0.1 <= t <= hi_wire + 0.1]
    return multiset_loss(s, w, exclude)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=("off", "live"))
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--capture", default="runs/scottsvalley_armable_300s.log")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--arm-at", type=float, default=5.0)
    ap.add_argument("--post-at", type=float, default=20.0)
    ap.add_argument("--power", required=True)
    # the 2026-10-13 restart-hook trial: endpoints come from the build message
    ap.add_argument("--hook", choices=("on", "off"), default=None,
                    help="set the trial hook switch before each cycle (needs --hook-set, --hook-field)")
    ap.add_argument("--hook-set", default=None, help="POST path with {v} for the value, e.g. /x?on={v}")
    ap.add_argument("--hook-values", default="1,0", help="the on,off values put in {v}")
    ap.add_argument("--hook-field", default=None, help="the /gen_inhibit page field reporting the switch")
    ap.add_argument("--restart", choices=("reboot", "crash"), default="reboot")
    ap.add_argument("--crash-path", default=None, help="POST path of the test-only crash trigger")
    ap.add_argument("--trigger", default=None,
                    help="take the crash path from the firmware repo's trigger table (vtrux_checks.json inert_triggers), "
                         "e.g. GI_TEST_RESTART_QUIESCE; the repo is $GI_FW_REPO or this one")
    ap.add_argument("--page-keys", default="", help="extra /gen_inhibit fields recorded before and after")
    ap.add_argument("--record-get", default=None, help="a GET path whose JSON is recorded after the restart")
    a = ap.parse_args()
    if a.hook and not (a.hook_set and a.hook_field):
        ap.error("--hook needs --hook-set and --hook-field")
    if a.trigger:
        if a.crash_path:
            ap.error("--trigger and --crash-path are alternatives")
        a.crash_path = trigger_post(a.trigger)
    if a.restart == "crash" and not a.crash_path:
        ap.error("--restart crash needs --crash-path")
    want = None if a.hook is None else (a.hook == "on")
    extra_keys = tuple(k for k in a.page_keys.split(",") if k)
    restart_path = "/system_reboot" if a.restart == "reboot" else a.crash_path
    import can
    import lowid_flood_skip_bench as LF
    import e4_device_calibration as E
    import autoarm_boot_to_live as AB
    E.CAPTURE = gi_paths.verify(a.capture if os.path.isabs(a.capture) else gi_paths.data_dir(a.capture))
    E.WINDOW_START = 0.0
    frames = E.load_window(a.seconds)
    msgs = [can.Message(arbitration_id=i, data=d, is_extended_id=x) for _t, i, d, x in frames]
    offs = [t for t, _i, _d, _x in frames]
    rc = 0
    for cyc in range(a.cycles):
        p0 = LF.page() or {}
        if p0.get("mode") != 0:
            print("FAIL: the device must start each cycle in mode 0 (read %r)" % p0.get("mode"))
            return 1
        if want is not None:
            v = a.hook_values.split(",")[0 if want else 1]
            try:
                LF.dev_post(a.hook_set.replace("{v}", v))
            except Exception as e:
                print("FAIL: setting the hook: %s" % repr(e)[:80])
                return 1
            time.sleep(0.5)
            got_hook = hook_state((LF.page() or {}).get(a.hook_field))
            if got_hook != want:
                print("FAIL: the hook reads %r on the page, %r was requested" % (got_hook, want))
                return 1
        stamp = time.strftime("%Y%m%dT%H%M%S")
        tag = a.condition + ("" if want is None else "_hook" + a.hook) + ("" if a.restart == "reboot" else "_crash")
        rec = {"when": stamp, "condition": a.condition, "hook_requested": a.hook,
               "hook_field": a.hook_field, "restart": a.restart, "restart_path": restart_path, "trigger": a.trigger, "cycle": cyc, "power": a.power,
               "capture": os.path.basename(E.CAPTURE), "seconds": a.seconds, "arm_at": a.arm_at,
               "post_at": a.post_at, "build_before": LF.build_id(),
               "page_before": {k: p0.get(k) for k in ("mode", "drv", "uptime_ms", "reset_reason") + extra_keys}}
        tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000, auto_reset=True)
        kv = can.Bus(interface="kvaser", channel=0, bitrate=500000)
        got, errs, fails, sent_epoch, reads = [], [], [], [], []
        stop = threading.Event()
        ev = {}

        def wit():
            while not stop.is_set():
                m = kv.recv(timeout=0.1)
                if m is None:
                    continue
                if m.is_error_frame:
                    errs.append(m.timestamp)
                else:
                    got.append((m.timestamp, m.arbitration_id, bytes(m.data)))

        def poll():
            while not stop.is_set():
                p = LF.page()
                t = time.time()
                if p is not None:
                    drv = p.get("drv") or {}
                    reads.append((t, p.get("uptime_ms"), p.get("mode"), p.get("inhibit_live"),
                                  p.get("reset_reason"), drv.get("state"), drv.get("bus_error"),
                                  drv.get("tec"), p.get(a.hook_field) if a.hook_field else None))
                stop.wait(0.2)

        def at(due, what):
            while perf() < due:
                time.sleep(0.0005)
            ev[what + "_epoch"] = time.time()
            try:
                if what == "arm":
                    LF.dev_post("/gen_inhibit_set?mode=3")
                else:
                    LF.dev_post(restart_path, timeout=5.0)
                ev[what + "_http"] = "ok"
            except Exception as e:                # the device may reset before answering
                ev[what + "_http"] = repr(e)[:80]
        perf = time.perf_counter
        wt = threading.Thread(target=wit, daemon=True)
        pt = threading.Thread(target=poll, daemon=True)
        wt.start()
        pt.start()
        time.sleep(0.5)
        base = perf() + 0.5
        epoch_base = time.time() + (base - perf())
        th = [threading.Thread(target=at, args=(base + a.post_at, "reboot"), daemon=True)]
        if a.condition == "live":
            th.append(threading.Thread(target=at, args=(base + a.arm_at, "arm"), daemon=True))
        for x in th:
            x.start()
        try:
            for k in range(len(msgs)):
                due = base + offs[k]
                while perf() < due:
                    pass
                try:
                    tx.send(msgs[k], timeout=0.5)
                    sent_epoch.append((epoch_base + (perf() - base), frames[k][1], frames[k][2]))
                except can.CanError:
                    fails.append(offs[k])
            for x in th:
                x.join(30)
            time.sleep(1.0)
        finally:
            stop.set()
            wt.join(3)
            pt.join(3)
            try:
                rec["pcan_status_end"] = tx.status_string()
            except Exception as e:
                rec["pcan_status_end"] = "unread: %s" % type(e).__name__
            tx.shutdown()
            kv.shutdown()
        got.sort(key=lambda f: f[0])
        # the clock tie: replayed non-0x051 frames matched to their first wire copy by
        # (id, payload) occurrence order; the min of (wire - send) is the offset
        occ, pairs = {}, []
        for t, i, d in got:
            occ.setdefault((i, d), []).append(t)
        used = Counter()
        for t, i, d in sent_epoch:
            if i == CMD_ID:
                continue
            k = (i, bytes(d))
            lst = occ.get(k, [])
            j = used[k]
            if j < len(lst):
                pairs.append((t, lst[j]))
                used[k] += 1
        off = min((w - h for h, w in pairs), default=None)
        post_wire = (ev["reboot_epoch"] + off) if (off is not None and "reboot_epoch" in ev) else None
        post_reads = AB.after_last_reboot([(t, u) + tuple(r) for t, u, *r in reads])
        boot = AB.boot_instant([(x[0], x[1]) for x in post_reads])
        boot_wire = (boot + off) if (boot is not None and off is not None) else None
        before = [r for r in reads if "reboot_epoch" in ev and r[0] < ev["reboot_epoch"]]
        rec.update({
            "events": ev, "clock_offset_s": off, "clock_pairs": len(pairs),
            "boot_epoch": boot, "boot_rel_post_s": (round(boot - ev["reboot_epoch"], 4)
                                                    if boot is not None and "reboot_epoch" in ev else None),
            "last_read_before_post": before[-1] if before else None,
            "first_read_after_boot": post_reads[0] if post_reads else None,
            "last_read_after_boot": post_reads[-1] if post_reads else None,
            "reads_after_boot": len(post_reads),
            "witness_error_frames_total": len(errs),
            "burst": (burst_summary(errs, post_wire, boot_wire) if post_wire is not None
                      else "no POST time on the wire clock"),
            "replay_sends": {"sent": len(sent_epoch), "failed": len(fails),
                             "first_failure_s": round(min(fails), 4) if fails else None,
                             "failures_rel_post_s": [round(x - a.post_at, 4) for x in fails][:20]},
            "loss_excluding_0x051": multiset_loss(sent_epoch, got),
            "loss_0x051": cmd_loss_lower_bound(sent_epoch, got),
            "loss_in_restart_window_excluding_0x051": (
                window_loss(sent_epoch, got, off, post_wire - WINDOW_BEFORE_S,
                            (boot_wire or post_wire) + WINDOW_AFTER_BOOT_S)
                if (off is not None and post_wire is not None) else None),
            "build_after": LF.build_id(),
            "page_after": {k: (LF.page() or {}).get(k) for k in ("mode", "uptime_ms", "reset_reason") + extra_keys},
            "record_get_after": record_get(LF, a.record_get),
            "reset_reasons_after": sorted({str(r[4]) for r in post_reads}),
            "hook": (hook_judged(reads, ev.get("reboot_epoch"), want) if want is not None else None)})
        if a.condition == "live":
            lb = rec["last_read_before_post"]
            rec["live_at_post"] = bool(lb and lb[3])
        wl = os.path.join(RUNS, "restart_error_frames_%s_%s_witness.log.gz" % (tag, stamp))
        with gzip.open(wl, "wt") as fh:
            for t, i, d in got:
                fh.write("(%.6f) can0 %03X#%s\n" % (t, i, d.hex().upper()))
            for t in sorted(errs):
                fh.write("(%.6f) can0 ERROR\n" % t)
        rec["witness_raw"] = os.path.basename(wl)
        path = os.path.join(RUNS, "restart_error_frames_%s_%s.json" % (tag, stamp))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        b = rec["burst"]
        print("cycle %d %s: hook %s, reset %s, build %s, live_at_post %s, boot %.3f s after POST, errors %s, burst %s, sends failed %d, "
              "lost (non-0x051) %d, in window %s, wrote %s" % (
                  cyc, tag, (rec["hook"] or {}).get("readback_before_restart"), rec["reset_reasons_after"],
                  (rec["build_after"] or {}).get("elf_sha256"), rec.get("live_at_post"), rec["boot_rel_post_s"] or -1,
                  len(errs), {k: b.get(k) for k in ("in_window", "first_rel_boot_s", "last_rel_boot_s",
                                                    "first_rel_post_s", "clumps", "largest_gap_ms")}
                  if isinstance(b, dict) else b,
                  len(fails), rec["loss_excluding_0x051"]["lost"],
                  (rec["loss_in_restart_window_excluding_0x051"] or {}).get("lost"),
                  os.path.basename(path)))
        for k in extra_keys:
            print("    %s: before %r, after %r" % (k, rec["page_before"].get(k), rec["page_after"].get(k)))
        # wait for the device to answer in mode 0 again before the next cycle
        t_end = time.time() + 30
        while time.time() < t_end:
            p = LF.page()
            if p is not None and p.get("mode") == 0:
                break
            time.sleep(1.0)
        time.sleep(3.0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
