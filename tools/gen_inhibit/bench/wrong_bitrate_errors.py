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
                  counts every error frame it reports, with its host time;
  Kvaser 250 kbit the error source. A frame sent at half the bus rate is garbage to
                  the 500 kbit nodes and fails at the Kvaser too, which retransmits
                  until its controller gives up; a BURST is one send, held for
                  --burst-ms, then the Kvaser's transmit buffer is flushed (and the
                  bus reset) so the retransmission stops.
  X2              held off the bus in ROM download mode (x2_hold.py; approved) by
                  the operator before this runs -- --x2 records which.
The status page is polled at --poll-hz throughout (state, TEC, REC, bus_error,
inhibit_live, abort_reason), which is part of the condition.

  calibrate: bursts of each --burst-ms, --gap-s apart, a few each: how many error
             frames one burst makes at the witness and in the device's bus_error,
             and what it does to TEC/REC. No verdict.
  run:       a schedule of bursts, e.g. --bursts "1:5,2:5,3:5" (time s : burst ms).

    py -3.14 wrong_bitrate_errors.py calibrate --x2 held --power "..." [--bursts-ms 1,5,20]
    py -3.14 wrong_bitrate_errors.py run --x2 held --power "..." --bursts "2:5,3:5"
Writes runs/wrong_bitrate_<cmd>_<stamp>.json.
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
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
SOURCE_ID = 0x6A7


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


class PcanWitness(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.errors, self.frames = bus, [], 0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.1)
            if m is None:
                continue
            if m.is_error_frame:
                self.errors.append(time.perf_counter())
            else:
                self.frames += 1


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


def burst(ms):
    """One burst: open the Kvaser at 250 kbit, send one frame, and CLOSE the channel
    after ms, which takes its controller off the bus and ends the retransmission.
    The first version kept the channel open and called flush_tx_buffer(), which does
    not stop a frame the controller is already retransmitting: its first 1 ms burst
    became ~30,000 error frames and drove the PCAN bus-off (2026-10-10)."""
    import can
    kv = can.Bus(interface="kvaser", channel=0, bitrate=250000)
    t = time.perf_counter()
    try:
        kv.send(can.Message(arbitration_id=SOURCE_ID, is_extended_id=False, data=bytes(8)),
                timeout=0.05)
        time.sleep(ms / 1000.0)
    except can.CanError:
        pass
    finally:
        kv.shutdown()
    return t


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
           "plan": plan, "source": "Kvaser at 250 kbit, id 0x%03X" % SOURCE_ID}
    pc = kv = player = wit = poll = None
    try:
        pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
        wit = PcanWitness(pc)
        wit.start()
        player = S.Player(pc, LF.build_stimulus())
        player.start()
        time.sleep(2.0)
        LF.dev_post("/gen_inhibit_set?mode=3")
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 10 and not (LF.page() or {}).get("inhibit_live"):
            time.sleep(0.2)
        rec["live_before"] = bool((LF.page() or {}).get("inhibit_live"))
        poll = Poller(LF, a.poll_hz)
        poll.start()
        time.sleep(2.0)
        base = time.perf_counter()
        starts = []
        for at, ms in plan:
            while time.perf_counter() < base + at:
                time.sleep(0.001)
            starts.append((burst(ms), ms))
        time.sleep(a.tail_s)
    finally:
        if poll is not None:
            poll.stop.set()
            poll.join(3)
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        if player is not None:
            player.stop_flag.set()
            player.join(15)
        if wit is not None:
            wit.stop.set()
            wit.join(3)
        pc_status = None
        if pc is not None:
            try:
                pc_status = pc.status_string()
            except Exception:
                pass
            pc.shutdown()
    st = [s for s, _ in starts]
    errs = wit.errors
    series = poll.series if poll else []
    t_ref = st[0] if st else 0.0
    rec["bursts"] = [{"t": round(s - t_ref, 4), "ms": ms, "witness_error_frames": n}
                     for (s, ms), n in zip(starts, per_burst(st, errs))]
    rec["witness_error_frames"] = len(errs)
    rec["pcan_status_end"] = pc_status
    rec["witness_window_max_10s"] = window_max(errs)
    rec["series"] = [dict(x, t=round(x["t"] - t_ref, 4)) for x in series]
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
    for b in rec["bursts"]:
        print("  burst at %.3f s, %g ms -> witness error frames %d" % (b["t"], b["ms"], b["witness_error_frames"]))
    print("summary", rec["summary"], "witness total", rec["witness_error_frames"],
          "max in 10 s", rec["witness_window_max_10s"])
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
