"""Auto-arm build: boot-to-live time on an SW (OTA) boot, and condition 5 (spec 12.4).

Gen-inhibit tester, 2026-10-10 (reviewer-approved, with conditions). Spec 12.4's
auto-arm row: "power-on straight into the gate, waiting for the contactors, going
live, and the boot-to-live time; and a reset asserted on the WiCAN in the middle of
a log replay with the generator running comes up armed and never goes live
(condition 5), with the witness showing no 0x051 beyond what the replay sent".
This measures the SW-boot figure only (an OTA restart); the power-on figure needs
the 12 V supply cut and is parked for the user. The SW figure is recorded as such
and is not carried over as the power-on one.

ONE CYCLE: the PCAN replays a window at its recorded timing (queue order, as e4),
the Kvaser witnesses every frame; --ota-at s into it, bench_flash.py OTAs the
image (mode 0 first: OTA is refused while armed, spec 9.3). The device restarts
(SW), boots into the auto-armed mode and, if the gate passes, goes live. Then:
  - boot instant (host epoch) = a status read's time - its uptime_ms, the median
    over the reads (each read's own time is its response time);
  - live = the first device 0x051 by provenance (provenance_classifier: queue
    order), in the witness clock;
  - the witness clock against the host epoch: python-can stamps Kvaser frames on
    the epoch, and the offset is MEASURED from the PCAN send times of the replayed
    frames against their wire times (provenance), median and spread reported;
  - boot-to-live = live - boot, with that offset applied.
CONDITION 5 (--expect-no-live): the window has the generator requested (B0 0x0B)
across the restart; the device must come up armed and never go live: 0 device
0x051 after the boot, inhibit_live never true.

    GI_WICAN_SSID=... py -3.14 autoarm_boot_to_live.py --bin <image> --capture runs/<log>
        [--window-start 0] --seconds 70 --ota-at 5 [--disarm-first] [--expect-no-live]
Writes runs/autoarm_<stamp>.json (+ the witness log).
"""
import argparse
import gzip
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RUNS = gi_paths.runs()
BENCH_FLASH = os.path.join(gi_paths.fw_tools(), "bench_flash.py")


def boot_instant(reads):
    """reads: [(host epoch of the response, uptime_ms)]. Median of t - uptime."""
    v = [t - u / 1000.0 for t, u in reads if u is not None]
    return statistics.median(v) if v else None


def clock_offset(pairs):
    """pairs: [(host epoch at send(), wire time)] for replayed frames. The wire time
    trails the send by the adapter's queueing, so the MIN of (wire - send) is the
    offset estimate (a frame sent on an idle queue), and the spread is reported."""
    d = sorted(w - h for h, w in pairs)
    return (d[0], d[len(d) // 2], d[-1]) if d else (None, None, None)


def gate_closed_windows(frames, pt_db, epri_db):
    """Where the arm gate cannot pass on the VCM's and GENE's signals, in s from the
    replay's first frame: condition 6 (gen_rpm_ref >= 0 or gen_torque_cmd != 0, i.e. the
    engine is requested) or condition 5 (GENE_RotSpd >= 300 rpm). frames: [(t, id, data,
    ext)]. Returns (windows [(start, end)], q): q = the index, among the replayed 0x051,
    of the first one carrying a request (None if none). Decoded with cantools.
    Going live inside a window is NOT as specified; a device already live before it
    keeps inhibiting through it (reviewer, 2026-10-10: judge the first live time against
    where the request stands, not "never live" over the whole capture)."""
    flags, q, n051 = [], None, 0
    state = {"req": False, "gen": False}
    for t, i, d, _x in frames:
        if i == 0x051:
            m = pt_db.decode_message(0x051, bytes(d), decode_choices=False)
            state["req"] = m["gen_rpm_ref"] >= 0 or m["gen_torque_cmd"] != 0
            if state["req"] and q is None:
                q = n051
            n051 += 1
        elif i == 0x054:
            state["gen"] = epri_db.decode_message(0x054, bytes(d), decode_choices=False)["GENE_RotSpd"] >= 300
        else:
            continue
        flags.append((t, state["req"] or state["gen"]))
    out, s, prev = [], None, None
    for t, f in flags:
        if f and s is None:
            s = t
        if not f and s is not None:
            out.append((s, prev))
            s = None
        prev = t
    if s is not None:
        out.append((s, prev))
    return out, q


def live_in_closed(t_live, windows):
    """True if the first live instant (s from the first frame) falls inside a window."""
    return t_live is not None and any(a <= t_live <= b for a, b in windows)


def held_first_request(vcm_times, device_times, q):
    """A device live before the request must hold it from its first frame: a device
    0x051 on the wire between the replayed command before the first request (q-1) and
    the request itself (q). None if there is no request, or it is the first command."""
    if q is None or q < 1 or q >= len(vcm_times):
        return None
    a, b = vcm_times[q - 1], vcm_times[q]
    return any(a < t < b for t in device_times)


# A CHOSEN bound, not a measurement: derived from the SW boot-to-live measured at
# 0.41-0.58 s (2026-10-10). The open spans it is compared with are measured against
# conditions 5 and 6 only; conditions 1-4 and the contactors pass ~1.24 s after this
# capture's key-on, so spans starting before then overstate by up to 1.24 s (reviewer,
# 2026-10-10: state both in the evidence entry).
NEVER_LIVE_MARGIN_S = 1.0


def open_spans(windows, t0, t1):
    """The gaps between gate-closed windows inside [t0, t1]: when conditions 5 and 6
    allow going live. (Only those two; the capture's contactors close ~1.24 s after
    its key-on edge, so an open span starting before that is open from there.)"""
    out, t = [], t0
    for a, b in sorted(windows):
        if b < t:
            continue
        if a > t:
            out.append((t, min(a, t1)))
        t = max(t, b)
        if t >= t1:
            break
    if t < t1:
        out.append((t, t1))
    return [(a, b) for a, b in out if b > a]


def restart_verdict(golive, live_rel, windows, held, request_at, boot_rel, end_rel):
    """One restart timed to a key-on. golive: golive_stale_check's verdict on the frames
    after the boot (a VOID there just means never live, judged here); live_rel: first
    live, s from the key-on frame, or None; windows: the gate-closed windows; held:
    held_first_request; request_at: the first request, s from key-on; boot_rel: the
    reset instant, s from key-on; end_rel: the replay's last frame, s from key-on."""
    if golive.startswith(("NOT", "UNRESOLVED")):
        return golive
    if live_in_closed(live_rel, windows):
        return "NOT as specified: went live at %.3f s, inside a gate-closed window" % live_rel
    if live_rel is not None:
        if request_at is not None and live_rel < request_at and held is not True:
            return ("NOT as specified: live at %.3f s but did not hold the request at %.3f s from its "
                    "first frame" % (live_rel, request_at))
        return "as specified"
    if boot_rel is None:
        return "VOID: no boot instant"
    usable = [(a, b) for a, b in open_spans(windows, max(boot_rel, 0.0), end_rel)
              if b - a > NEVER_LIVE_MARGIN_S]
    if usable:
        return ("NOT as specified: never live, though the gate signals allowed it for %.3f s from %.3f s "
                "after the boot at %.3f s" % (usable[0][1] - usable[0][0], usable[0][0], boot_rel))
    return "as specified: never live; booted at %.3f s, no gate-open span over %.1f s after it" % (
        boot_rel, NEVER_LIVE_MARGIN_S)


def after_last_reboot(reads):
    """reads: [(t, uptime_ms, ...)] in time order. The reads after the last drop in
    uptime (the reset), or [] if no drop was seen. Selecting by "uptime < 120 s" let
    reads from BEFORE the reset in when restarts are under 2 min apart, which the boot
    median then follows (2026-10-10)."""
    u = [(k, x[1]) for k, x in enumerate(reads) if x[1] is not None]
    cut = None
    for (k0, u0), (k1, u1) in zip(u, u[1:]):
        if u1 < u0:
            cut = k1
    return [x for x in reads[cut:] if x[1] is not None] if cut is not None else []


def first_after(times, t0):
    after = [t for t in sorted(times) if t >= t0]
    return after[0] if after else None


# /system_reboot starts a 2000 ms timer and only then resets (wican_autoarm_bench.py,
# measured 2026-09-26). It sets WHEN the POST goes out for a wanted reset instant; the
# instant itself is never taken from it -- it is read from uptime (boot_instant).
REBOOT_DELAY_S = 2.0


def send_summary(fail_offs):
    """Replay sends that raised, by offset (s) from the replay's first frame. Until
    2026-10-12 they were swallowed: 5 of 20 dace947 runs lost the replay at the restart
    and only the provenance count showed it, with nothing to say why."""
    return {"send_failures": len(fail_offs),
            "first_send_failure_s": round(min(fail_offs), 4) if fail_offs else None}


def sent_frames(frames, sent_k):
    """The frames that reached the bus, in order: provenance judges what was sent, not
    the capture (a send that failed during a bus-off is not on the wire to be found)."""
    return [frames[k] for k in sent_k]


def error_summary(err_times, boot_wire, window=1.0):
    """The witness's error frames, against the boot on the wire (same clock)."""
    out = {"witness_error_frames": len(err_times), "near_boot": None, "first_rel_boot_s": None}
    if boot_wire is not None:
        out["near_boot"] = sum(1 for t in err_times if abs(t - boot_wire) <= window)
        if err_times:
            out["first_rel_boot_s"] = round(min(err_times) - boot_wire, 4)
    return out


def reboot_plan(reboot_at, delay=REBOOT_DELAY_S, margin=1.0):
    """Restart timed to a key-on (12.4 auto-arm row, user 2026-10-10). The replay starts
    at its key-on edge, so `reboot_at` is the wanted reset instant in s from the edge
    (negative: before it, on a silent bus). Returns (lead, post_at): how long the bus
    stays silent before the replay's first frame, and when the POST goes out, in s from
    the edge. The lead covers a POST before the edge plus `margin`."""
    post_at = reboot_at - delay
    return max(0.0, -post_at) + margin, post_at


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=None, help="the image to OTA (the --ota-at restart)")
    ap.add_argument("--capture", required=True)
    ap.add_argument("--window-start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=70.0)
    ap.add_argument("--ota-at", type=float, default=5.0)
    ap.add_argument("--reboot-at", type=float, default=None,
                    help="instead of an OTA: a /system_reboot whose RESET lands this many s from the "
                         "replay's first frame (negative: before it, on a silent bus). For a capture "
                         "cut to start at its key-on edge, this is the offset from key-on")
    ap.add_argument("--disarm-first", action="store_true")
    ap.add_argument("--expect-no-live", action="store_true")
    ap.add_argument("--power", required=True)
    a = ap.parse_args()
    if a.reboot_at is None and not a.bin:
        ap.error("--bin is required for the OTA restart (or give --reboot-at)")
    import can
    import lowid_flood_skip_bench as LF
    import e4_device_calibration as E
    import provenance_classifier as P
    E.CAPTURE = gi_paths.verify(a.capture if os.path.isabs(a.capture) else gi_paths.data_dir(a.capture))
    E.WINDOW_START = a.window_start
    frames = E.load_window(a.seconds)
    msgs = [can.Message(arbitration_id=i, data=d, is_extended_id=x) for _t, i, d, x in frames]
    offs = [t for t, _i, _d, _x in frames]
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "power": a.power, "bin": a.bin, "capture": os.path.basename(E.CAPTURE),
           "window_start": a.window_start, "seconds": a.seconds,
           "ota_at": a.ota_at if a.reboot_at is None else None, "reboot_at": a.reboot_at,
           "expect_no_live": a.expect_no_live, "build_before": LF.build_id()}
    if a.disarm_first:
        LF.dev_post("/gen_inhibit_set?mode=0")
        time.sleep(1.0)
    rec["mode_before"] = (LF.page() or {}).get("mode")
    # AUTO-RESET (2026-10-12): every WiCAN restart puts a burst of error frames on the
    # segment (32-138 seen on the witness, from ~0.24 s before the boot instant), and in
    # 7 of 25 dace947 runs the PCAN went bus-off inside it and never sent again, which
    # left the rest of the run on a silent bus (UNRESOLVED by provenance). With auto-reset
    # it recovers; the frames it failed to send are recorded and left out of provenance.
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000, auto_reset=True)
    kv = can.Bus(interface="kvaser", channel=0, bitrate=500000)
    got = []
    errs = []                       # witness error frames (wire clock), kept since 2026-10-12
    fails = []                      # replay sends that raised, by offset
    stop = threading.Event()

    def wit():
        while not stop.is_set():
            m = kv.recv(timeout=0.1)
            if m is None:
                continue
            if m.is_error_frame:
                errs.append(m.timestamp)
            else:
                got.append((m.timestamp, m.arbitration_id, bytes(m.data)))
    wt = threading.Thread(target=wit, daemon=True)
    wt.start()
    ota = {}

    def do_ota():
        ota["start"] = time.time()
        r = subprocess.run([sys.executable, BENCH_FLASH, "--bin", a.bin, "--flash"],
                           capture_output=True, text=True)
        ota["end"] = time.time()
        ota["rc"] = r.returncode
        ota["tail"] = [ln for ln in r.stdout.splitlines() if "[" in ln and "]" in ln][-6:]

    def do_reboot(due):
        """POST /system_reboot at perf time `due`; the reset follows ~REBOOT_DELAY_S later."""
        while perf() < due:
            time.sleep(0.0005)
        ota["start"] = time.time()
        try:
            LF.dev_post("/system_reboot", timeout=5.0)
            ota["http"] = "ok"
        except Exception as e:                       # the device may reset before answering
            ota["http"] = repr(e)[:80]
        ota["end"] = time.time()
    reads = []

    def poll():
        while not stop.is_set():
            p = LF.page()
            t = time.time()
            if p is not None:
                # appended last so earlier indices keep their meaning: the skip counters
                # and rx_missed, so a stale or late frame at a boot can be checked against
                # what the device counted (reviewer, 2026-10-10)
                drv = p.get("drv") or {}
                reads.append((t, p.get("uptime_ms"), p.get("mode"), p.get("inhibit_live"),
                              p.get("arm_block"), p.get("reset_reason"), p.get("abort_reason"),
                              p.get("skips_late"), p.get("skips_withdrawn"), drv.get("rx_missed")))
            stop.wait(0.2)
    send_epoch = []
    sent_k = []                     # indices of the frames that were sent
    perf = time.perf_counter
    ota_t = None
    pt = None
    if a.reboot_at is not None:
        # the bus stays SILENT until the replay's first frame (the key-on edge of a cut
        # capture); the POST may go out during that silence
        lead, post_at = reboot_plan(a.reboot_at)
        rec["reboot_plan"] = {"silent_lead_s": lead, "post_at_s": post_at, "delay_s": REBOOT_DELAY_S}
        base = perf() + lead
        ota_t = threading.Thread(target=do_reboot, args=(base + post_at,), daemon=True)
        ota_t.start()
        pt = threading.Thread(target=poll, daemon=True)
        pt.start()
    else:
        base = perf()
    epoch_base = time.time() + (base - perf())
    try:
        for k in range(len(msgs)):
            due = base + offs[k]
            if ota_t is None and offs[k] >= a.ota_at:
                ota_t = threading.Thread(target=do_ota, daemon=True)
                ota_t.start()
                pt = threading.Thread(target=poll, daemon=True)
                pt.start()
            while perf() < due:
                pass
            try:
                tx.send(msgs[k], timeout=0.5)
                sent_k.append(k)
            except can.CanError:
                fails.append(offs[k])
                continue
            if len(sent_k) % 200 == 1:
                # indexed by position among the SENT frames, which is how provenance's
                # replayed_wire_times are indexed
                send_epoch.append((len(sent_k) - 1, epoch_base + (perf() - base)))
        if ota_t is not None:
            ota_t.join(120)
        time.sleep(1.0)
    finally:
        stop.set()
        wt.join(3)
        if pt is not None:
            pt.join(3)
        try:
            rec["pcan_status_end"] = tx.status_string()
        except Exception as e:              # not every backend has it
            rec["pcan_status_end"] = "unread: %s" % type(e).__name__
        tx.shutdown()
        kv.shutdown()
    got.sort(key=lambda x: x[0])
    wl = os.path.join(RUNS, "autoarm_%s_witness.log.gz" % stamp)
    with gzip.open(wl, "wt") as fh:
        for t, i, d in got:
            fh.write("(%.6f) can0 %03X#%s\n" % (t, i, d.hex().upper()))
    sent = sent_frames(frames, sent_k)
    src = [(t, i, d) for t, i, d, _x in sent]
    r = P.classify(src, got, None, keep_frames=True)
    rep = r.get("replayed_wire_times") or []
    pairs = [(h, rep[k]) for k, h in send_epoch if k < len(rep)]
    off_min, off_med, off_max = clock_offset(pairs)
    post = after_last_reboot(reads)
    boot = boot_instant([(t, u) for t, u, *_ in post])
    live_wire = first_after(r.get("device_times") or [], (boot + off_min) if (boot and off_min is not None) else 0)
    boot_wire = (boot + off_min) if (boot and off_min is not None) else None
    keyon_wire = rep[0] if rep else None         # the replay's first frame on the wire
    import golive_stale_check as G
    n_after = len([t for t in (r.get("device_times") or []) if boot_wire is not None and t >= boot_wire])
    rec.update({"ota": ota, "provenance": {k: r.get(k) for k in ("verdict", "replayed_found",
                                                               "replayed_frames", "device_frames",
                                                               "late", "double", "unresolved",
                                                               "unexplained", "on_time")},
                "boot_wire": boot_wire,
                # restart timed to a key-on: for a capture cut at its key-on edge the
                # replay's first frame IS the edge, so these are from key-on
                "first_replayed": ("%03X#%s" % (frames[0][1], frames[0][2].hex())) if frames else None,
                "restart_from_first_frame_s": (round(boot_wire - keyon_wire, 4)
                                               if (boot_wire is not None and keyon_wire) else None),
                "live_from_first_frame_s": (round(live_wire - keyon_wire, 4)
                                            if (live_wire and keyon_wire) else None),
                # spec 12.4 "no stale answers on going live" / condition 9, on the device
                # frames after the boot only
                "golive_verdict": (G.judge(r, n_after, boot_wire) if boot_wire is not None
                                   else "VOID: no boot instant"),
                "clock_offset_s": {"min": off_min, "median": off_med, "max": off_max, "pairs": len(pairs)},
                "boot_epoch": boot, "reads_after_boot": len(post),
                "first_read": post[0] if post else None, "last_read": post[-1] if post else None,
                # every read after the boot (time, uptime_ms, mode, live, arm_block, reset_reason,
                # abort, skips_late, skips_withdrawn, rx_missed): the first runs kept only the
                # first and last, so condition 5's block
                # reason DURING the generator request was not recorded (2026-10-10)
                "reads": post,
                "first_live_read": next((x for x in post if x[3]), None),
                "live_wire": live_wire,
                "boot_to_live_s": (round(live_wire - off_min - boot, 4)
                                   if (live_wire and boot and off_min is not None) else None),
                "device_0x051_after_boot": len([t for t in (r.get("device_times") or [])
                                                if boot and off_min is not None and t >= boot + off_min]),
                "build_after": LF.build_id(), "witness_raw": os.path.basename(wl),
                "replay_sends": send_summary(fails),
                "witness_errors": error_summary(errs, boot_wire)})
    if a.expect_no_live:
        rec["verdict"] = ("as specified: armed after the restart, never live, 0 device 0x051"
                          if rec["device_0x051_after_boot"] == 0 and not rec["first_live_read"]
                          and post and post[-1][2] == 3 else "NOT as specified")
    if a.reboot_at is not None and frames:
        # restart timed to a key-on: judge the first live time against where the gate's
        # conditions 5 and 6 stand in this capture (reviewer, 2026-10-10)
        import cantools
        win, q = gate_closed_windows(
            sent, cantools.database.load_file(gi_paths.vehicle_dbc("vtrux-powertrain-experimental.dbc"),
                                                strict=False),
            cantools.database.load_file(gi_paths.vehicle_dbc("epri-pt-bus.dbc"), strict=False))
        f0 = frames[0][0]               # the capture's first frame: the key-on edge
        win = [(round(x - f0, 4), round(y - f0, 4)) for x, y in win]
        cmd_offs = [t for t, i, _d, _x in sent if i == 0x051]   # indexed as vcm_times is
        request_at = round(cmd_offs[q] - f0, 4) if q is not None else None
        after_boot = [t for t in (r.get("device_times") or []) if boot_wire is not None and t >= boot_wire]
        held = held_first_request(r.get("vcm_times") or [], after_boot, q)
        rec.update({"gate_closed_windows_s": win, "first_request_s": request_at, "held_first_request": held,
                    "live_inside_closed_window": live_in_closed(rec["live_from_first_frame_s"], win),
                    "restart_verdict": restart_verdict(rec["golive_verdict"], rec["live_from_first_frame_s"], win,
                                                       held, request_at, rec["restart_from_first_frame_s"],
                                                       round(frames[-1][0] - f0, 4))})
    path = os.path.join(RUNS, "autoarm_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    for k in ("ota", "reboot_plan", "build_after", "boot_epoch", "first_read", "first_live_read", "boot_to_live_s",
              "restart_from_first_frame_s", "live_from_first_frame_s", "device_0x051_after_boot",
              "golive_verdict", "first_request_s", "held_first_request", "restart_verdict", "clock_offset_s",
              "verdict"):
        print(k, rec.get(k))
    print("replay_sends", rec.get("replay_sends"), "witness_errors", rec.get("witness_errors"),
          "pcan_status_end", rec.get("pcan_status_end"))
    print("provenance", rec["provenance"]["verdict"], "wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
