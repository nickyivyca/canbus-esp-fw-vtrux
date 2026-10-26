"""How often does the WiCAN reset during a live INHIBIT arm with no jam?

Gen-inhibit tester, 2026-10-08, the reviewer's request after
live_jam_arm_20261007T233908.json: there the device stopped answering 3.96 s
after the arm was posted, before any jam, and came back in mode 0 with tx_ok 0
and drv null. That is consistent with a reset; it was not reproduced in the
next arm. The cause is for the implementor; this measures the RATE and keeps
any evidence that arrives.

Per arm: mode 3 with the interlock slice of vtrux_20260719_190019_T4.log
replayed on the PCAN (the stimulus the other arms use), wait for live, hold
live for --live-seconds polling the page at ~2 Hz, then mode 0. The Kvaser is
an independent witness counting the 0x051 on the wire. The console
(COM4, USB-Serial-JTAG) is captured throughout by console_capture.py, which
reopens the port if it drops.

A RESET IS COUNTED ONLY FROM POST-STATE (reviewer, 2026-10-07): while the
harness has the device armed, a page reading mode 0, or tx_ok lower than an
earlier reading, or drv null. A page that merely fails to answer is an HTTP
stall (the CAN worker and lwIP share priority 18) and is counted separately,
never as a reset. A quiet console is no evidence either way.

What the console self-test showed (runs/console_selftest_20261008T000900.json):
a USB-JTAG-initiated reset printed "rst:0x15 (USB_UART_CHIP_RESET)" 68 ms
later and the capture caught it, but the USB port did NOT drop in that reset,
so the self-test does not show that a reset which takes USB down (e.g. a
brownout) would be caught.

The WiCAN is powered from the PC's USB only (user, 2026-10-08); there is no
rail to meter on this rig.

  py -3.14 gen-inhibit/tools/bench/reset_rate_arms.py [--arms 10] [--live-seconds 60]
Writes runs/reset_rate_<stamp>.json (never overwrites).
"""
import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import can                                                      # noqa: E402
import lowid_flood_skip_bench as LF                             # noqa: E402
from console_capture import ConsoleCapture                      # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()
BITRATE = 500000


def snap(p):
    if p is None:
        return None
    return {"mode": p.get("mode"), "inhibit_live": p.get("inhibit_live"),
            "abort_latched": p.get("abort_latched"),
            "abort_reason": p.get("abort_reason"), "tx_ok": p.get("tx_ok"),
            "drv_null": p.get("drv") is None,
            "drv_state": (p.get("drv") or {}).get("state")}


def find_reset(samples):
    """First post-state reset in an armed window, or None.

    samples: [(perf, snap or None)] taken between arm_posted+1 s and the
    harness's own mode-0 post. Returns (perf, why) for the first sample that
    reads mode 0, drv null, or a tx_ok below an earlier reading.
    """
    last_tx = None
    for pt, s in samples:
        if s is None:
            continue
        tx = s.get("tx_ok")
        if s.get("mode") == 0:
            return pt, "mode 0 without the harness posting it"
        if s.get("drv_null"):
            return pt, "drv null while armed"
        if last_tx is not None and tx is not None and tx < last_tx:
            return pt, "tx_ok fell from %d to %d" % (last_tx, tx)
        if tx is not None:
            last_tx = tx
    return None


class Wit(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_flag = threading.Event()
        self.n051 = []         # perf of each 0x051 seen
        self.errors = 0
        self.lock = threading.Lock()

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                with self.lock:
                    if m.is_error_frame:
                        self.errors += 1
                    elif m.arbitration_id == 0x051:
                        self.n051.append(time.perf_counter())
                m = self.bus.recv(timeout=0.0)

    def count(self, a, b):
        with self.lock:
            return sum(1 for t in self.n051 if a <= t <= b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", type=int, default=10)
    ap.add_argument("--live-seconds", type=float, default=60.0)
    ap.add_argument("--poll-sleep", type=float, default=0.5,
                    help="sleep between page requests while live (0.5 ~ 2 Hz; "
                         "0.1 matches live_jam_arm.py, ~10 Hz)")
    ap.add_argument("--replayer", choices=("pcan", "kvaser"), default="pcan",
                    help="kvaser matches live_jam_arm.py: the Kvaser replays "
                         "AND counts 0x051 (only the device's, since it does "
                         "not receive its own), and the PCAN is never opened "
                         "-- in live_jam_arm_20261007T233908 it opened only at "
                         "the jam, 8 s after the device went quiet")
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = os.path.join(RUNS, "reset_rate_%s.json" % stamp)
    p = LF.page()
    if p is None or p.get("mode") != 0:
        print("FAIL: device must be in mode 0")
        return 1
    rec = {"when": stamp, "build": LF.build_id(), "power": "USB only (PC)",
           "stimulus": LF.CAPTURE, "live_seconds": a.live_seconds,
           "poll_sleep_s": a.poll_sleep, "replayer": a.replayer,
           "pcan_opened": a.replayer == "pcan",
           "witness_sees_replay": a.replayer == "pcan",
           "arms": []}
    print("build %s" % rec["build"])
    stim = LF.build_stimulus()
    total = a.arms * (a.live_seconds + 20) + 30
    cc = ConsoleCapture()
    cc.start()
    kv = pc = wit = replay = None
    try:
        kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
        wit = Wit(kv)
        wit.start()
        if a.replayer == "pcan":
            pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                         bitrate=BITRATE)
            tx = pc
        else:
            tx = kv
        rec["replay_stats"] = {}
        replay = threading.Thread(target=LF.play, args=(tx, stim, total),
                                  kwargs={"stats": rec["replay_stats"]},
                                  daemon=True)
        replay.start()
        time.sleep(2.0)
        for k in range(a.arms):
            arm = {"n": k}
            rec["arms"].append(arm)
            t_arm = time.perf_counter()
            LF.dev_post("/gen_inhibit_set?mode=3")
            t_live = None
            while time.perf_counter() - t_arm < 15.0:
                s = snap(LF.page())
                if s and s.get("inhibit_live"):
                    t_live = time.perf_counter()
                    break
                time.sleep(0.2)
            arm["went_live_after_s"] = (None if t_live is None
                                        else round(t_live - t_arm, 2))
            samples, unanswered = [], 0
            t0 = t_live or time.perf_counter()
            while time.perf_counter() - t0 < a.live_seconds:
                s = snap(LF.page())
                samples.append((time.perf_counter(), s))
                if s is None:
                    unanswered += 1
                if find_reset(samples):
                    break
                time.sleep(a.poll_sleep)
            t_end = time.perf_counter()
            LF.dev_post("/gen_inhibit_set?mode=0")
            time.sleep(1.0)
            arm["after_off"] = snap(LF.page())
            arm["unanswered_pages"] = unanswered
            arm["page_requests"] = len(samples)
            arm["live_s"] = round(t_end - t0, 2) if t_live else 0.0
            arm["last_live_page"] = next((s for _, s in reversed(samples)
                                          if s and s.get("inhibit_live")), None)
            r = find_reset(samples)
            arm["wire_0x051_while_live"] = wit.count(t0, t_end)
            if r:
                arm["reset_rel_live_s"] = round(r[0] - t0, 2)
                arm["reset_why"] = r[1]
                arm["console_banner_near_reset"] = [
                    (round(t - r[0], 3), x) for t, x in
                    cc.banner_lines(r[0] - 30.0, r[0] + 5.0)]
                arm["console_events_near_reset"] = [
                    (round(t - r[0], 3), e) for t, e in cc.events
                    if r[0] - 30.0 <= t <= r[0] + 5.0]
                arm["last_answered_before_reset_rel_live_s"] = next(
                    (round(t - t0, 2) for t, s in reversed(samples)
                     if s is not None and t < r[0] and s.get("mode") == 3),
                    None)
            print("arm %d: live after %s s, held %.1f s, unanswered %d, "
                  "0x051 on wire %d, reset %s %s"
                  % (k, arm["went_live_after_s"], arm["live_s"], unanswered,
                     arm["wire_0x051_while_live"],
                     arm.get("reset_rel_live_s"), arm.get("reset_why") or ""))
            for t, x in arm.get("console_banner_near_reset") or []:
                print("    console %+.3f %s" % (t, x[:110]))
    finally:
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        cc.stop_flag.set()
        cc.join(timeout=3.0)
        if wit is not None:
            wit.stop_flag.set()
            wit.join(timeout=3.0)
            rec["witness_error_frames"] = wit.errors
        for b in (pc, kv):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass
        rec["console_events"] = cc.events
        rec["console_banner_lines"] = cc.banner_lines()
        rec["console_line_count"] = len(cc.lines)
        rec["post"] = snap(LF.page())
    arms = rec["arms"]
    live_total = sum(x.get("live_s") or 0 for x in arms)
    resets = [x for x in arms if x.get("reset_rel_live_s") is not None]
    rec["summary"] = {"arms": len(arms),
                      "arms_went_live": sum(1 for x in arms
                                            if x.get("went_live_after_s")),
                      "total_live_s": round(live_total, 1),
                      "resets": len(resets),
                      "reset_times_rel_live_s": [x["reset_rel_live_s"]
                                                 for x in resets],
                      "unanswered_pages": sum(x.get("unanswered_pages", 0)
                                              for x in arms),
                      "page_requests": sum(x.get("page_requests", 0)
                                           for x in arms),
                      "poll_sleep_s": a.poll_sleep, "replayer": a.replayer}
    print("\nsummary %s" % rec["summary"])
    print("console: %d lines, events %s" % (rec["console_line_count"],
                                            rec["console_events"][:6]))
    os.makedirs(RUNS, exist_ok=True)
    with open(path, "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=1)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
