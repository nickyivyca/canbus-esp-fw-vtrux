"""Trip 6 across a re-arm: an expired burst must not trip, a recent one must (spec 7).

Gen-inhibit tester, 2026-10-10 (reviewer's request, the trip 6 fix check). Spec 7,
"How the window moves" (user, 2026-10-08):
  - "Only a key-on (7.1) clears the error history. Arming, an explicit re-arm and
    going live do not."
  - "Error frames are recorded whenever the device is armed ... if 10 error frames
    fell in the 10 s before going live, it fires at the first live check."
  - "Only a recent burst trips ... After a trip 6 latch, a re-arm can therefore go
    live once the bus has been free of a qualifying burst for 10 s."

Replaces the inline check of 19:22 (runs/trip6_rearm_check_20261010T192221.json),
which found the first re-arm after a latch tripping on an expired burst.

Adapters (bench-holder.md): the PCAN loops the interlock slice (key on throughout,
so no key-off -> key-on edge can clear the history) and witnesses: it counts error
frames and the device's 0x051 (the PCAN does not receive its own frames, and the
slice holds no 0x051). The Kvaser at 250 kbit is the error source
(wrong_bitrate_errors.burst). The X2 is held off the bus by the operator (--x2).

SEQUENCE, one process:
  A0 flood:   arm (mode 3), wait live, one burst -> trip 6 latches; mode 0.
  A1 expired: re-arm --expired-gap s after the last witness error frame. Must go
              live with no trip. This is the arm that tripped on cc34c80: the arm
              before it saw error frames, which is the defect's precondition.
  A2 flood:   mode 0, re-arm, wait live, one burst -> latches; mode 0 at once.
  A3 recent:  re-arm --recent-gap s after the last error frame, so the burst is
              well inside 10 s at the first live check. Must trip at it.
  A4 expired: mode 0, re-arm --expired-gap s after A2's last error frame. Must go
              live with no trip (A3's latch cleared by the re-arm, burst expired).
Each arm is polled at --poll-hz from the arm POST for --arm-s s.

    py -3.14 trip6_rearm_check.py --x2 held --power "..."
Writes runs/trip6_rearm_check_<stamp>.json.
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
TRIP6 = "error-frame rate exceeded"
RECENT_MAX_S = 8.0     # an A3 arm later than this after the burst is too near the 10 s edge


def judge_expired(reads, errors_since_arm):
    """An arm whose last burst has expired: live, and never the trip 6 abort.
    A new error frame since arming would make a trip legitimate -> VOID."""
    if errors_since_arm:
        return "VOID: %d witness error frames since arming" % errors_since_arm
    if any(r.get("abort") == TRIP6 for r in reads):
        return "NOT as specified: trip 6 on an expired burst"
    if not any(r.get("live") for r in reads):
        return "NOT as specified: never live"
    return "as specified"


def judge_recent(reads, arm_after_last_error_s, device_0x051):
    """An arm made inside 10 s of a qualifying burst. Spec 7, "Checked before going live"
    (user, 2026-10-10): trip 6 is checked each time the gate passes, BEFORE going live,
    so it latches there, the device never goes live and no inhibit frame is sent.
    device_0x051: the witness's device 0x051 since the arm POST. (Until 2026-10-10 the
    check came after the first answer, and one frame before the latch was accepted.)"""
    if arm_after_last_error_s is None or arm_after_last_error_s > RECENT_MAX_S:
        return "VOID: armed %s s after the burst (needs <= %g)" % (arm_after_last_error_s, RECENT_MAX_S)
    if not any(r.get("abort") == TRIP6 for r in reads):
        return "NOT as specified: no trip 6"
    if device_0x051:
        return "NOT as specified: %d inhibit frame(s) sent before the latch" % device_0x051
    if any(r.get("live") for r in reads):
        return "NOT as specified: went live before the latch"
    return "as specified"


# Spec 11, "Trip 6's error history is reported" (user, 2026-10-12): err_n (error frames
# recorded in the 10 s before the reading), err_age_ms (since the newest, null if none
# since key-on), err_burst (trip 6's test evaluated at the reading, "so it shows ahead of
# time that going live would latch trip 6"). Read once just before each re-arm.
AGE_TOL_S = 0.5        # test tolerance: witness host timestamps vs the device's own clock
ERR_FIELDS = ("err_n", "err_age_ms", "err_burst")


def judge_history(pre, gap_s, expect_recent):
    """The history fields on the page read just before a re-arm, gap_s after the last
    witness error frame. A recent arm (A3) follows a burst that tripped, so all of it is
    inside 10 s: err_burst true and err_n >= 10. An expired arm (A1, A4) is 15 s after:
    err_burst false and err_n 0. err_age_ms within AGE_TOL_S of the witness's gap."""
    if pre is None or any(k not in pre for k in ERR_FIELDS):
        return "UNRESOLVED: the page carries no %s" % "/".join(ERR_FIELDS)
    if gap_s is None:
        return "VOID: no witness error frame to measure the age against"
    bad = []
    if pre["err_age_ms"] is None:
        bad.append("err_age_ms null after a burst since key-on")
    elif abs(pre["err_age_ms"] / 1000.0 - gap_s) > AGE_TOL_S:
        bad.append("err_age_ms %s against the witness's %.3f s" % (pre["err_age_ms"], gap_s))
    if expect_recent:
        if pre["err_burst"] is not True:
            bad.append("err_burst %r %.3f s after a qualifying burst" % (pre["err_burst"], gap_s))
        if not isinstance(pre["err_n"], int) or pre["err_n"] < 10:
            bad.append("err_n %r, a burst of >= 10 inside the last 10 s" % pre["err_n"])
    else:
        if pre["err_burst"] is not False:
            bad.append("err_burst %r %.3f s after the last error" % (pre["err_burst"], gap_s))
        if pre["err_n"] != 0:
            bad.append("err_n %r with no error in the last 10 s" % pre["err_n"])
    return "NOT as specified: " + "; ".join(bad) if bad else "as specified"


def judge_prediction(pre, reads):
    """err_burst before the arm must say whether going live latches trip 6."""
    if pre is None or "err_burst" not in pre:
        return "UNRESOLVED: no err_burst on the page"
    latched = any(r.get("abort") == TRIP6 for r in reads)
    if bool(pre["err_burst"]) == latched:
        return "as specified"
    return "NOT as specified: err_burst %r before the arm, trip 6 %s" % (
        pre["err_burst"], "latched" if latched else "did not latch")


def wire_errors(detail):
    """The witness's error frames that report a bus error, from (t, arbitration_id, hex).
    PCAN-Basic error-frame messages carry the error TYPE in the id (bit, form, stuff,
    other: non-zero) and the RX/TX error counters in the data. After a flood the PCAN also
    delivers id-0 reports with error code 0 and a counter stepping down by one each time
    (00 00 7a 00, 00 00 79 00, ...): its own counters recovering, not error frames on the
    wire -- the WiCAN's bus_error stays 0 through them (2026-10-10 20:03). Those are not
    counted here; they are kept in the record."""
    return [d for d in detail if d[1] != 0]


class Witness(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.errors, self.dev051, self.frames = bus, [], [], 0
        self.error_detail = []      # (perf, arbitration_id, data hex): what the PCAN reported
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.1)
            if m is None:
                continue
            t = time.perf_counter()
            if m.is_error_frame:
                self.errors.append(t)
                self.error_detail.append((t, m.arbitration_id, bytes(m.data).hex()))
            else:
                self.frames += 1
                if m.arbitration_id == 0x051:
                    self.dev051.append(t)


def read(LF, t0):
    p = LF.page()
    t = time.perf_counter()
    if p is None:
        return {"t": round(t - t0, 3), "page": None}
    drv = p.get("drv") or {}
    hist = {k: p[k] for k in ERR_FIELDS if k in p}     # absent stays absent
    return {"t": round(t - t0, 3), **hist, "mode": p.get("mode"), "live": p.get("inhibit_live"),
            "block": p.get("arm_block"), "abort": p.get("abort_reason"),
            "tx_ok": p.get("tx_ok"), "state": drv.get("state"), "tec": drv.get("tec"),
            "rec": drv.get("rec"), "bus_error": drv.get("bus_error"),
            "uptime_ms": p.get("uptime_ms"), "reset_reason": p.get("reset_reason")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x2", required=True, choices=["held", "app"])
    ap.add_argument("--power", required=True)
    ap.add_argument("--burst-ms", type=float, default=5.0)
    ap.add_argument("--expired-gap", type=float, default=15.0)
    ap.add_argument("--recent-gap", type=float, default=2.0)
    ap.add_argument("--arm-s", type=float, default=12.0)
    ap.add_argument("--poll-hz", type=float, default=5.0)
    a = ap.parse_args()
    import can
    import lowid_flood_skip_bench as LF
    import skip_method_bench as S
    import wrong_bitrate_errors as W
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "x2": a.x2, "power": a.power, "build": LF.build_id(),
           "args": vars(a), "arms": []}
    pc = wit = None
    pl = {"p": None}
    stim = LF.build_stimulus()
    t0 = time.perf_counter()

    def post(mode):
        LF.dev_post("/gen_inhibit_set?mode=%d" % mode)

    # THE SLICE PLAYS ONLY WHILE THE WICAN IS ARMED. With the X2 held, the Kvaser closed
    # and the WiCAN in mode 0 (driver not installed), nothing on the segment ACKs the
    # PCAN: its frames retransmit as a continuous stream of ACK-error frames, which the
    # WiCAN then counts the moment it re-arms. The first version kept the slice playing
    # through mode 0 and saw 220-342 witness error frames in every arm (19:58, VOID).
    # So: stop the slice while the WiCAN still ACKs, let the PCAN's queue drain, THEN
    # disarm; and on a re-arm, start the slice only once the driver reports running.
    def play():
        pl["p"] = S.Player(pc, stim)
        pl["p"].start()

    def silence():
        if pl["p"] is not None:
            pl["p"].stop_flag.set()
            pl["p"].join(15)
            pl["p"] = None
        time.sleep(0.15)

    def disarm():
        silence()
        post(0)

    def poll_arm(name, seconds):
        """Arm (mode 3) on a quiet bus, start the slice once the driver runs, and poll;
        returns the arm record (times from t0)."""
        n_err, n_051 = len(wit.errors), len(wit.dev051)
        pre = read(LF, t0)          # the trip 6 history the arm is about to act on
        ta = time.perf_counter()
        post(3)
        reads = []
        while time.perf_counter() - ta < 3.0:
            r = read(LF, t0)
            reads.append(r)
            if r.get("state") == "running":
                break
            time.sleep(0.05)
        play()
        slice_at = time.perf_counter()
        while time.perf_counter() - ta < seconds:
            reads.append(read(LF, t0))
            time.sleep(1.0 / a.poll_hz)
        kinds = {}
        for _t, i, h in wit.error_detail[n_err:]:
            kinds["%X:%s" % (i, h)] = kinds.get("%X:%s" % (i, h), 0) + 1
        arm = {"arm": name, "armed_at": round(ta - t0, 3), "slice_at": round(slice_at - t0, 3),
               "pre_arm_read": pre, "reads": reads, "error_kinds_since_arm": kinds,
               "device_bus_error_max": max([r.get("bus_error") or 0 for r in reads], default=None),
               "error_times_since_arm": [round(t - ta, 4) for t, _i, _h in wit.error_detail[n_err:]][:100],
               "witness_reports_since_arm": len(wit.errors) - n_err,
               "errors_since_arm": len(wire_errors(wit.error_detail[n_err:])),
               "device_0x051_since_arm": len(wit.dev051) - n_051,
               "first_0x051_after_arm_s": (round(wit.dev051[n_051] - ta, 3)
                                           if len(wit.dev051) > n_051 else None)}
        rec["arms"].append(arm)
        return arm

    def flood(name):
        arm = poll_arm(name, 4.0)
        n_err = len(wit.errors)
        tb, opened = W.burst(a.burst_ms)      # from the Kvaser's open since 2026-10-29 (recorded only)
        time.sleep(1.0)
        arm["burst_at"] = round(tb - t0, 3)
        arm["burst_open_s"] = round(opened, 4)
        burst = wit.error_detail[n_err:]
        wire = wire_errors(burst)
        arm["burst_reports"] = len(burst)
        arm["burst_error_frames"] = len(wire)
        by_id = {}
        for _t, i, _h in burst:
            by_id["%X" % i] = by_id.get("%X" % i, 0) + 1
        arm["burst_reports_by_id"] = by_id
        last = wire[-1][0] if wire else None
        arm["reads"].append(read(LF, t0))
        disarm()
        arm["mode0_at"] = round(time.perf_counter() - t0, 3)
        arm["errors_after_burst_to_mode0"] = len(wire_errors(wit.error_detail[n_err:])) - len(wire)
        return last

    def wait_until(t_abs):
        while time.perf_counter() < t_abs:
            time.sleep(0.05)

    def history(arm, last, expect_recent):
        """The spec 11 history verdicts for a re-arm; last = the last witness error (perf)."""
        pre = arm["pre_arm_read"]
        gap = round(pre["t"] - (last - t0), 3) if (last and pre and "t" in pre) else None
        arm["pre_arm_after_last_error_s"] = gap
        arm["history_verdict"] = judge_history(pre, gap, expect_recent)
        arm["prediction_verdict"] = judge_prediction(pre, arm["reads"])

    try:
        pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
        wit = Witness(pc)
        wit.start()
        post(0)
        time.sleep(2.0)
        rec["errors_quiet_before"] = len(wit.errors)
        last = flood("A0 flood")
        rec["A0_last_error_at"] = round(last - t0, 3) if last else None
        if last is not None:
            wait_until(last + a.expired_gap)
        arm = poll_arm("A1 expired", a.arm_s)
        arm["armed_after_last_error_s"] = round(arm["armed_at"] - (last - t0), 3) if last else None
        arm["verdict"] = judge_expired(arm["reads"], arm["errors_since_arm"])
        history(arm, last, False)
        disarm()
        time.sleep(1.0)
        last = flood("A2 flood")
        rec["A2_last_error_at"] = round(last - t0, 3) if last else None
        if last is not None:
            wait_until(last + a.recent_gap)
        arm = poll_arm("A3 recent", a.arm_s)
        gap = round(arm["armed_at"] - (last - t0), 3) if last else None
        arm["armed_after_last_error_s"] = gap
        arm["verdict"] = judge_recent(arm["reads"], gap, arm["device_0x051_since_arm"])
        history(arm, last, True)
        disarm()
        if last is not None:
            wait_until(last + a.expired_gap)
        arm = poll_arm("A4 expired", a.arm_s)
        arm["armed_after_last_error_s"] = round(arm["armed_at"] - (last - t0), 3) if last else None
        arm["verdict"] = judge_expired(arm["reads"], arm["errors_since_arm"])
        history(arm, last, False)
    finally:
        try:
            disarm()
        except Exception:
            pass
        if wit is not None:
            wit.stop.set()
            wit.join(3)
        if pc is not None:
            try:
                rec["pcan_status_end"] = pc.status_string()
            except Exception:
                pass
            pc.shutdown()
    time.sleep(1.0)
    rec["after"] = read(LF, t0)
    rec["witness_error_frames"] = len(wit.errors) if wit else None
    path = os.path.join(RUNS, "trip6_rearm_check_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    for arm in rec["arms"]:
        aborts = sorted({r.get("abort") for r in arm["reads"] if r.get("abort")})
        lives = [r["t"] for r in arm["reads"] if r.get("live")]
        print("%-11s armed %7.3f  after-burst %-7s errs %-5d devbe %-5s burst %-5s dev0x051 %-5d live-from %-7s aborts %s  %s"
              % (arm["arm"], arm["armed_at"], arm.get("armed_after_last_error_s"), arm["errors_since_arm"], arm.get("device_bus_error_max"),
                 arm.get("burst_error_frames"), arm["device_0x051_since_arm"],
                 (round(lives[0] - arm["armed_at"], 3) if lives else None), aborts, arm.get("verdict", "")))
        if "history_verdict" in arm:
            print("            history %s | prediction %s" % (arm["history_verdict"], arm["prediction_verdict"]))
    print("build", rec["build"], "after", rec["after"].get("mode"), "wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
