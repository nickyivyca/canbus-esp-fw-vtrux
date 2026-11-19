"""Error frames from an adapter at the wrong bit rate, on a live INHIBIT arm (spec 12.4).

Gen-inhibit tester, 2026-10-10. Spec 12.4's error-frame row (7, D4, 14 q8): "error
frames from a spare adapter at the wrong bit rate, in three regimes (error-active,
error-passive, and at the trip threshold), with the device's state, TEC and REC, its
bus_error count and the witness's error-frame count read throughout". The threshold
regime already has jammer evidence (bench case C, 2026-10-09); this tool is for the
wrong-bit-rate method, with a calibration first.

Adapters on the WiCAN segment (bench-holder.md): only two, so
  PCAN 500 kbit   plays the interlock slice (skip_method_bench.Player) so the device
                  is armed and live, AND witnesses: a separate thread reads it and
                  counts every error frame it reports, with the PCAN's own stamp
                  mapped onto the host clock (since 2026-11-11, A2 (a): before, its
                  host receipt time; witness_clock.device_fit, and a fit outside
                  its bounds makes the run VOID, printed as "witness clock VOID");
  canmain 250 kbit the error source (CAN1's Innomaker, gs_usb over libusb, by its canctl role;
                  until 2026-11-18 the Kvaser: condition 5 below). A frame sent at half the
                  bus rate is garbage to the 500 kbit nodes and fails at the source too,
                  which retransmits; a BURST is one send, held for --burst-ms, then the
                  channel is closed so the retransmission stops.
  Kvaser          DOWN, read back at the start (kvaser_bus.link_down): on SocketCAN an up
                  link ACKs with no socket open, so it would be one more node on the bus.
  X2              held off the bus in ROM download mode (x2_hold.py; approved) by
                  the operator before this runs -- --x2 records which.
The status page is polled at --poll-hz throughout (state, TEC, REC, bus_error,
inhibit_live, abort_reason), which is part of the condition.

Since 2026-10-25 (reviewer): the WiCAN is armed on a quiet bus and the slice starts once
its driver reports running, and the slice stops before the disarm (the order
trip6_rearm_check.py uses), because an unACKed slice is itself an error source. The record
keeps pre_burst_window: the witness's error frames, with times, between the last live page
sample without an abort and the first burst; the B9 judge needs 0 there.

  calibrate: bursts of each --burst-ms, --gap-s apart, a few each: how many error
             frames one burst makes at the witness and in the device's bus_error,
             and what it does to TEC/REC. No verdict.
  run:       a schedule of bursts, e.g. --bursts "1:5,2:5,3:5" (time s : burst ms).

    py -3.14 wrong_bitrate_errors.py calibrate --x2 held --power "..." [--bursts-ms 1,5,20]
    py -3.14 wrong_bitrate_errors.py run --x2 held --power "..." --bursts "2:5,3:5"
Writes runs/wrong_bitrate_<cmd>_<stamp>.json.

CONDITION 5 (the gen-inhibit reviewer, 2026-11-18). The bursts come from canmain at 250 kbit,
handed to libusb once for the run (kvaser_bus.innomaker_held: the per-open hand-back would sit
inside the burst times), each burst a gs_usb open, one send, the hold, and a close. Every burst is
measured on the PCAN's mapped stamps by burst_measures (the same functions as the committed check
canmain_burst_check.py): at least one error frame, the last no later than the close + 5 ms, the
span at most the asked length + 5 ms and, from 5 ms up, at least the asked length - 2 ms. A breach
VOIDs the run: the record's verdict, and a "burst timing VOID: ..." line the suite's B9 judge
reads. It is never a FAIL, and no judge is loosened.
"""
import argparse
import json
import os
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import witness_clock as WC  # noqa: E402  the PCAN's own stamps, mapped (A2 (a), 2026-11-11)
import burst_measures as BM  # noqa: E402  burst length and close on those stamps (condition 5, 2026-11-18)
import proc_witness as PW  # noqa: E402  the witness out of the Player's process (2026-11-19)
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
SOURCE_ID = 0x6A7
SOURCE_ROLE = "canmain"      # the burst source by its canctl role (condition 5, 2026-11-18)
SOURCE_BITRATE = 250000


def parse_bursts(text):
    """'2:5,3:5' -> [(2.0, 5.0), (3.0, 5.0)] (time s, burst ms), in time order."""
    out = []
    for part in (text or "").split(","):
        if part.strip():
            t, ms = part.split(":")
            if float(ms) <= 0:
                raise ValueError("burst length must be > 0 ms")
            out.append((float(t), float(ms)))
    return sorted(out)


def window_max(times, span=10.0):
    """The most error frames within any `span` s (inclusive), sliding."""
    t = sorted(times)
    best, j = 0, 0
    for i in range(len(t)):
        while t[i] - t[j] > span:
            j += 1
        best = max(best, i - j + 1)
    return best


def per_burst(burst_starts, errors, tail_s=0.5):
    """Witness error frames attributed to each burst: from its start to the next
    burst's start (the last: tail_s after it)."""
    out = []
    for k, t in enumerate(burst_starts):
        end = burst_starts[k + 1] if k + 1 < len(burst_starts) else t + tail_s
        out.append(sum(1 for e in errors if t <= e < end))
    return out


def pre_burst_window(series, errors, first_burst):
    """The witness's error frames between the last live page sample without an abort before
    the first burst and the burst itself: {"from_s", "errors", "error_times_s"}, times on the
    burst clock (first burst 0). With no such sample, from_s and errors are None. The B9 judge
    needs errors == 0, so a latch in that gap is never credited to the bursts (reviewer,
    2026-10-25). `series` and `errors` are on the host's perf_counter clock: the poller's
    receipt times, and the witness's error frames as the PCAN stamped them, mapped onto that
    clock (judged_errors, since 2026-11-11)."""
    clean = [s["t"] for s in series
             if s["t"] < first_burst and s.get("live") is True and not s.get("abort")]
    if not clean:
        return {"from_s": None, "errors": None, "error_times_s": []}
    t0 = clean[-1]
    times = [round(e - first_burst, 4) for e in errors if t0 <= e < first_burst]
    return {"from_s": round(t0 - first_burst, 4), "errors": len(times), "error_times_s": times}


class PcanWitness(PW.ThreadWitness):
    """Every message the PCAN delivers: its receipt on perf_counter and the epoch, and its own
    stamp (points, for the clock fit); each error frame's receipt (errors) and stamp (error_ts).
    Since 2026-11-11 (A2 (a)) the judged times are the stamps, mapped (judged_errors). Read in a
    thread beside the Player (at the slice's rate its pacer did not starve a thread witness,
    test_busywait_witnesses); a loop that dies is recorded and the run VOID, with the "witness
    VOID" line (proc_witness.ThreadWitness, gen-inhibit reviewer 2026-11-19)."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.errors, self.error_ts, self.points, self.frames = bus, [], [], [], 0
        self.stop = self.event()

    def run(self):
        while not self.stop.is_set():
            m = self.recv(timeout=0.1)
            if m is None:
                continue
            p, e = time.perf_counter(), time.time()
            self.points.append((p, e, m.timestamp))
            if m.is_error_frame:
                self.errors.append(p)
                self.error_ts.append(m.timestamp)
            else:
                self.frames += 1


def judged_errors(wit):
    """(error-frame times on the perf_counter clock, the clock fit, which clock the figures are on).
    The PCAN's own stamps, mapped by WC.device_fit; if the fit is VOID, the receipt times, for the
    record only: the run is then VOID and never judged on them (A2 (a), 2026-11-11)."""
    fit = WC.device_fit(wit.points)
    if fit["void"]:
        return list(wit.errors), fit, "receipt time (PCAN clock VOID): recorded, not judged"
    return [WC.mapped(fit, ts) for ts in wit.error_ts], fit, "PCAN device stamps, mapped onto perf_counter"


class Poller(threading.Thread):
    def __init__(self, LF, hz):
        super().__init__(daemon=True)
        self.LF, self.period, self.series = LF, 1.0 / hz, []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            t0 = time.perf_counter()
            p = self.LF.page()
            t1 = time.perf_counter()
            if p is not None:
                drv = p.get("drv") or {}
                self.series.append({"t": round(t1, 4), "mode": p.get("mode"),
                                    "live": p.get("inhibit_live"), "abort": p.get("abort_reason"),
                                    "state": drv.get("state"), "tec": drv.get("tec"),
                                    "rec": drv.get("rec"), "bus_error": drv.get("bus_error")})
            self.stop.wait(max(0.0, self.period - (time.perf_counter() - t0)))


def burst(ms, inno):
    """One burst: open canmain at 250 kbit on the held role (inno, kvaser_bus.innomaker_held),
    send one frame, and CLOSE the channel after ms, which takes its controller off the bus and
    ends the retransmission. Returns (start, seconds the open took, close): the close is taken
    as the shutdown is called (condition 5's measures, 2026-11-18). Until 2026-11-18 the source
    was the Kvaser, whose history follows.
    The first version kept the channel open and called flush_tx_buffer(), which does
    not stop a frame the controller is already retransmitting: its first 1 ms burst
    became ~30,000 error frames and drove the PCAN bus-off (2026-10-10).

    The burst starts when the channel opens (2026-10-29): can.Bus() ends with the
    controller going bus-on at 250 kbit on the 500 kbit segment, and the witness's error
    frames begin there, before the send. Until then t was taken after can.Bus() returned,
    and step 1a's acceptance run counted 36 and 43 witness error frames, all within 5 ms
    before that t, as "before the first burst"."""
    import can
    # The pinned can-tools' Linux fixes (2026-11-11), applied before t, so loading them is not
    # counted in the open's time.
    from kvaser_bus import fixes  # noqa: E402
    fixes()
    t = time.perf_counter()
    src = inno.open(bitrate=SOURCE_BITRATE)
    opened = time.perf_counter() - t
    try:
        src.send(can.Message(arbitration_id=SOURCE_ID, is_extended_id=False, data=bytes(8)),
                 timeout=0.05)
        time.sleep(ms / 1000.0)
    except can.CanError:
        pass
    finally:
        t_close = time.perf_counter()
        src.shutdown()
    return t, opened, t_close


def burst_measures(starts, errs):
    """starts: [(start, asked ms, close)]; errs: the mapped error-frame times. One
    burst_measures.burst_measure per burst, over its burst_window."""
    st = [s for s, _ms, _c in starts]
    cl = [c for _s, _ms, c in starts]
    return [BM.burst_measure(ms, c, BM.in_window(errs, BM.burst_window(st, cl, k)))
            for k, (_s, ms, c) in enumerate(starts)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["calibrate", "run"])
    ap.add_argument("--x2", required=True, choices=["held", "app"])
    ap.add_argument("--power", required=True)
    ap.add_argument("--bursts-ms", default="1,5,20")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--gap-s", type=float, default=3.0)
    ap.add_argument("--bursts", default="")
    ap.add_argument("--poll-hz", type=float, default=5.0)
    ap.add_argument("--tail-s", type=float, default=12.0)
    a = ap.parse_args()
    import can
    import lowid_flood_skip_bench as LF
    import skip_method_bench as S
    if a.cmd == "calibrate":
        plan, t = [], 2.0
        for ms in [float(x) for x in a.bursts_ms.split(",")]:
            for _ in range(a.repeats):
                plan.append((t, ms))
                t += a.gap_s
    else:
        plan = parse_bursts(a.bursts)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "cmd": a.cmd, "x2": a.x2, "power": a.power, "build": LF.build_id(),
           "plan": plan, "source": "%s (gs_usb over libusb, by its canctl role) at %d bit/s, id 0x%03X"
                                   % (SOURCE_ROLE, SOURCE_BITRATE, SOURCE_ID)}
    pc = player = wit = poll = None
    starts, opens = [], []
    import kvaser_bus as KB  # noqa: E402  the adapters by their canctl roles
    # the Kvaser down, read back: an up kvaser link ACKs with no socket open (condition 5)
    rec["kvaser_link_start"] = KB.link_down()
    held = KB.innomaker_held(SOURCE_ROLE)
    inno = held.__enter__()
    try:
        stim = LF.build_stimulus()
        from kvaser_bus import pcan_bus  # noqa: E402  the PCAN by its canctl role (2026-11-18)
        pc = pcan_bus(bitrate=500000)
        wit = PcanWitness(pc)
        wit.start()
        # THE SLICE PLAYS ONLY WHILE THE WICAN IS ARMED (trip6_rearm_check.py, 2026-10-10; here
        # since 2026-10-25, reviewer). With the X2 held, the Kvaser closed and the WiCAN in mode
        # 0, nothing on the segment ACKs the PCAN, and its frames retransmit as ACK-error frames
        # that the WiCAN counts the moment it arms. This started the slice 2 s before arming,
        # and no B9 record from 2026-10-10 to 2026-10-25 was ever live before its first burst.
        # So: arm on a quiet bus, start the slice once the driver reports running.
        LF.dev_post("/gen_inhibit_set?mode=3")
        t0 = time.perf_counter()
        running = False
        while time.perf_counter() - t0 < 3.0:
            if ((LF.page() or {}).get("drv") or {}).get("state") == "running":
                running = True
                break
            time.sleep(0.05)
        rec["driver_running_before_slice"] = running
        player = S.Player(pc, stim)
        player.start()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 10 and not (LF.page() or {}).get("inhibit_live"):
            time.sleep(0.2)
        rec["live_before"] = bool((LF.page() or {}).get("inhibit_live"))
        poll = Poller(LF, a.poll_hz)
        poll.start()
        time.sleep(2.0)
        base = time.perf_counter()
        for at, ms in plan:
            while time.perf_counter() < base + at:
                time.sleep(0.001)
            t_b, opened, t_c = burst(ms, inno)
            starts.append((t_b, ms, t_c))
            opens.append(opened)
        time.sleep(a.tail_s)
    finally:
        if poll is not None:
            poll.stop.set()
            poll.join(3)
        # Stop the slice while the WiCAN still ACKs, let the PCAN's queue drain, THEN disarm
        # (trip6_rearm_check.py's order): disarmed first, the slice's frames go unACKed.
        if player is not None:
            player.stop_flag.set()
            player.join(15)
        time.sleep(0.15)
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        if wit is not None:
            wit.stop.set()
            wit.join(3)
            rec["witness_proc"] = wit.proc_info
        pc_status = None
        if pc is not None:
            try:
                pc_status = pc.status_string()
            except Exception:
                pass
            pc.shutdown()
        held.__exit__(*sys.exc_info())      # canmain back to socketcan, down, read back
    st = [s for s, _ms, _c in starts]
    # the bus states the witness PCAN raised and read on past (kvaser_bus.pcan_bus; run cb08's witness
    # thread died of the first, "error passive", after burst 1; F6, 2026-11-19), on the perf clock
    rec["pcan_states"] = [[round(p, 6), s] for p, s in (getattr(pc, "vtrux_states", None) or [])]
    errs, fit, figures_clock = judged_errors(wit)
    rec["witness_clock"] = fit
    rec["witness_clock_figures"] = figures_clock
    # the witness loop died before the run's end: a short witness, never judged (its verdict set
    # below, over the clock's and the bursts', which a short witness also spoils)
    proc_void, void_line = PW.void_line(wit) if wit is not None else (None, None)
    if fit["void"]:
        rec["witness_clock_void"] = fit["void"]
        rec["verdict"] = WC.pcan_void_verdict(fit["void"])
        burst_line = ""
    else:
        rec["burst_measures"] = [dict(m, first_s=None if m["first_s"] is None else round(m["first_s"] - s, 6),
                                      last_s=None if m["last_s"] is None else round(m["last_s"] - s, 6))
                                 for m, (s, _ms, _c) in zip(burst_measures(starts, errs), starts)]
        burst_line = BM.breach_line(rec["burst_measures"])
        if burst_line:
            rec["burst_timing_void"] = burst_line
            rec["verdict"] = "VOID: %s" % burst_line
    if proc_void:
        rec["witness_proc_void"] = proc_void
        rec["verdict"] = PW.void_verdict(proc_void)
    series = poll.series if poll else []
    t_ref = st[0] if st else 0.0
    # open_s: how long the Kvaser's open took, from the burst's start (2026-10-29)
    rec["bursts"] = [{"t": round(s - t_ref, 4), "ms": ms, "open_s": round(o, 4), "close_s": round(c - s, 4),
                      "witness_error_frames": n}
                     for (s, ms, c), o, n in zip(starts, opens, per_burst(st, errs))]
    rec["witness_error_frames"] = len(errs)
    rec["pcan_status_end"] = pc_status
    rec["witness_window_max_10s"] = window_max(errs)
    rec["series"] = [dict(x, t=round(x["t"] - t_ref, 4)) for x in series]
    rec["pre_burst_window"] = (pre_burst_window(series, errs, st[0]) if st else
                               {"from_s": None, "errors": None, "error_times_s": []})
    rec["witness_errors_before_first_burst"] = sum(1 for e in errs if st and e < st[0])
    be = [x["bus_error"] for x in series if x.get("bus_error") is not None]
    rec["summary"] = {"bus_error_delta": (be[-1] - be[0]) if be else None,
                      "tec_max": max([x["tec"] or 0 for x in series], default=None),
                      "rec_max": max([x["rec"] or 0 for x in series], default=None),
                      "states": sorted({x["state"] for x in series if x["state"]}),
                      "aborts": sorted({x["abort"] for x in series if x["abort"]}),
                      "live_at_end": series[-1]["live"] if series else None}
    path = os.path.join(RUNS, "wrong_bitrate_%s_%s.json" % (a.cmd, stamp))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    print(WC.fit_line(fit))
    if void_line:
        # the suite's B9 judge reads this line before the others: VOID, never judged (2026-11-19)
        print(void_line)
    if fit["void"]:
        # the suite's B9 judge reads this line first: VOID, never judged (A2 (a))
        print("witness clock VOID: %s" % fit["void"])
    if burst_line:
        # the suite's B9 judge reads this line too: VOID, never judged (condition 5)
        print(burst_line)
    for b in rec["bursts"]:
        print("  burst at %.3f s, %g ms -> witness error frames %d" % (b["t"], b["ms"], b["witness_error_frames"]))
    print("summary", rec["summary"], "witness total", rec["witness_error_frames"],
          "max in 10 s", rec["witness_window_max_10s"])
    w = rec["pre_burst_window"]
    print("before the first burst: driver running before the slice %s; witness error frames %d, %s since the last "
          "clean live sample (%s)" % (rec.get("driver_running_before_slice"), rec["witness_errors_before_first_burst"],
                                      w["errors"], "%+.3f s" % w["from_s"] if w["from_s"] is not None else "none"))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
