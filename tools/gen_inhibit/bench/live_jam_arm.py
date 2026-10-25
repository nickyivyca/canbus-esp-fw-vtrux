"""Live INHIBIT arm under a wrong-bitrate jam: does trip 6 end it, and does it stay ended?

Gen-inhibit tester, 2026-10-07. The reviewer's item 2, arm (i) as ruled
2026-10-07: a FIXED-RATE jam on a live arm (the PCAN at 250 kbit delivers
~300 error frames/s from its first instant whatever rate it is offered --
runs/jammer_characterize_20261007T233010.json -- so a slow ramp cannot be
made). Judged on:

  (A) spec 7 trip 6: once 10 or more error frames fall within 10 s while the
      inhibit is live, the device latches an abort for the error-frame rate.
      If a different trip latches first, that is reported as a finding (trip 6
      then cannot latch); if nothing latches, FAIL.
  (B) after the latched abort the inhibit is not live: the next 0x7F1 on the
      wire has diag_flags bit5 (inhibit_live) clear and bit7 (abort_latched)
      set, every later page reads inhibit_live false, and the witness sees no
      device 0x051 afterwards.
  (C) if the controller goes bus-off, the device never resumes a live inhibit
      without a re-arm. Recorded "not reached" if bus-off never happens.

Also recorded, as the reviewer asked: which trip latched and when; the
error-frame count at that moment from the device (drv.bus_error delta) and
from the witness; when drv.state entered bus_off; the last device 0x051.
The delivered jam rate is the WITNESS error-frame rate, never the offered one.

ADAPTERS (one segment, see firmware/bench-holder.md):
  Kvaser 500 kbit, normal      replays the interlock slice of
                               vtrux_20260719_190019_T4.log (the stimulus
                               lowid_flood_skip_bench.py arms with) AND
                               witnesses: it ACKs, and records 0x051, the
                               WiCAN diag pages and every error frame with
                               host time. It does not receive its own frames,
                               so every 0x051 it records is the device's.
  PCAN 250 kbit                the jammer, as characterised above.

  WHY THIS ARRANGEMENT, and its cost. Tried first (2026-10-07): the
  Innomaker replaying -- 256 frames in 42 s, 0x051 at ~1.3 Hz, too slow to
  arm (live_jam_arm_20261007T233445); then the Innomaker jamming at 250 kbit
  -- 256 sends accepted and ZERO error frames at the witness, in the live run
  (live_jam_arm_20261007T233635) and again with the WiCAN off, so it is not a
  usable jammer. The cost: spec 12 asks for a witness that is neither the
  transmitter nor the device. Here the witness is the replay's transmitter.
  What it witnesses (the device's 0x051 and diag, and error frames) is not
  produced by the replay, but the deviation is recorded in every run file
  ("witness_sees_replay": false) and reported.

OURS VERSUS THE VCM, when the witness also sees the replay (not the current
arrangement): the device's answer carries the VCM's next counter, so in
wire order it and the VCM's own next frame form a pair of adjacent 0x051 with
equal B5 (the discriminator validated in lowid_flood_skip_bench.py; payload
and trail timing cannot work). A pre-arm window with the device OFF is the
negative control: equal pairs there are the discriminator's false-positive
floor, and the run is INCONCLUSIVE for (B) if that floor is not zero.

THIS JAMS THE SHARED SEGMENT. Warn the interposer tester first.

  py -3.14 tools/gen_inhibit/bench/live_jam_arm.py [--jam-seconds 5] [--dry-run]
Writes runs/live_jam_arm_<stamp>.json (never overwrites).
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
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()
NOMINAL = 500000
WRONG = 250000
JAM_ID = 0x123
JAM_AIR_US = 444.0
KEEP_IDS = {0x051, 0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9}
TRIP6_TEXT = "error-frame rate"
B_MARGIN_S = 0.05     # host-clock slack between a page sample and the wire


def snap(p):
    if p is None:
        return None
    d = p.get("drv") or {}
    return {"mode": p.get("mode"), "inhibit_live": p.get("inhibit_live"),
            "abort_latched": p.get("abort_latched"),
            "abort_reason": p.get("abort_reason"),
            "arm_block": p.get("arm_block"), "bus_on": p.get("bus_on"),
            "tx_ok": p.get("tx_ok"), "drv_present": bool(p.get("drv")),
            "drv_state": d.get("state"), "drv_tec": d.get("tec"),
            "drv_rec": d.get("rec"), "drv_bus_error": d.get("bus_error"),
            "drv_tx_failed": d.get("tx_failed")}


class Witness(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_flag = threading.Event()
        self.frames = []
        self.errors = []
        self.lock = threading.Lock()

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                now = time.perf_counter()
                with self.lock:
                    if m.is_error_frame:
                        self.errors.append(round(now, 5))
                    elif m.arbitration_id in KEEP_IDS:
                        self.frames.append((round(now, 5), m.arbitration_id,
                                            bytes(m.data).hex()))
                m = self.bus.recv(timeout=0.0)


class Jammer(threading.Thread):
    def __init__(self, bus, period):
        super().__init__(daemon=True)
        self.bus, self.period = bus, period
        self.stop_flag = threading.Event()
        self.sent = self.errors = 0

    def run(self):
        msg = can.Message(arbitration_id=JAM_ID, data=bytes([0x55] * 8),
                          is_extended_id=False)
        nxt = time.perf_counter()
        while not self.stop_flag.is_set():
            try:
                self.bus.send(msg)
                self.sent += 1
            except Exception:
                self.errors += 1
            nxt += self.period
            while True:
                left = nxt - time.perf_counter()
                if left <= 0:
                    break
                if left > 0.002:
                    time.sleep(left - 0.002)
            if nxt < time.perf_counter() - self.period:
                nxt = time.perf_counter()


def poll(seconds, samples, every=0.1):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        samples.append({"pt": round(time.perf_counter(), 5),
                        "snap": snap(LF.page())})
        time.sleep(every)


# ------------------------------------------------------------------ judging --

# A VCM FRAME RECEIVED TWICE IS NOT A DEVICE ANSWER (reviewer's ruling, 2026-10-12,
# on runs/x2_jam_livetrain_20261012T185212.json arm 2). A dominant bit at the
# transmitter's last EOF bit is an overload to receivers, who keep the frame, and an
# error to the transmitter, which sends it again: error flag + delimiter (~14-20 bits)
# + intermission (3) + the frame again (~92 bits + stuffing for 080080ff7fxx), about
# 115-125 bits, 0.23-0.25 ms at 500 kbit (about 0.27 ms with worst-case stuffing and
# a superposed error flag), which DUP_MAX_S covers. So a pair counts as a duplicate
# only when ALL hold: identical payloads, spaced <= DUP_MAX_S, and tx_ok unchanged
# across it on the page. Anything else stays a device frame.
# TX_OK IS THE DISCRIMINATOR, NOT THE SPACING (reviewer, 2026-10-12): the 0.34 ms
# closest device answer seen in that arm is an observation, not a firmware bound. A
# late device answer can land back to back with the VCM's next frame, about one frame
# time, inside DUP_MAX_S; its own frame moves tx_ok, and that is what separates it.
DUP_MAX_S = 0.30e-3


def equal_b5_pairs(frames):
    """(t0, t1, d0, d1) for each adjacent 0x051 pair with equal B5 (counter)."""
    seq = [(pt, bytes.fromhex(hx)) for pt, arb, hx in frames if arb == 0x051]
    return [(t0, t1, d0, d1) for (t0, d0), (t1, d1) in zip(seq, seq[1:])
            if len(d0) >= 6 and len(d1) >= 6 and d0[5] == d1[5]]


def device_0x051(frames):
    """pt of each device 0x051: the first of an adjacent equal-B5 pair."""
    return [p[0] for p in equal_b5_pairs(frames)]


def tx_ok_flat(samples, t0, t1):
    """True when the last page reading at or before t0 and the first at or after t1
    both carry tx_ok and read the same: the device completed no transmit between."""
    def tx(s):
        return s["snap"].get("tx_ok") if s.get("snap") else None
    before = [tx(s) for s in samples if s["pt"] <= t0 and tx(s) is not None]
    after = [tx(s) for s in samples if s["pt"] >= t1 and tx(s) is not None]
    return bool(before) and bool(after) and before[-1] == after[0]


def vcm_duplicates(frames, samples):
    """pt of each equal-B5 pair that is a VCM frame received twice ("VCM duplicate,
    not judged"); see DUP_MAX_S. samples: [{"pt", "snap"}] on the frames' clock."""
    return [t0 for t0, t1, d0, d1 in equal_b5_pairs(frames)
            if d0 == d1 and t1 - t0 <= DUP_MAX_S and tx_ok_flat(samples, t0, t1)]


def judge(rec):
    ph = rec["phases"]
    samples = sorted(rec["samples"], key=lambda x: x["pt"])
    frames = sorted(rec["wire"], key=lambda x: x[0])
    errs = sorted(rec["witness_error_pts"])
    j0, j1 = ph.get("jam_start"), ph.get("jam_stop")
    out = {}

    dups = []
    if rec.get("witness_sees_replay", True):
        dups = vcm_duplicates(frames, samples)
        dev = [pt for pt in device_0x051(frames) if pt not in dups]
    else:
        # The witness is the replaying adapter and does not receive its own
        # frames, so every 0x051 it sees is the device's.
        dev = [pt for pt, arb, _ in frames if arb == 0x051]
    pre = [pt for pt in dev if ph["replay_start"] <= pt < ph["arm_posted"]]
    out["negative_control_equal_pairs"] = len(pre)
    out["vcm_duplicates_not_judged"] = [round(pt - (j0 if j0 is not None else 0.0), 4)
                                        for pt in dups]
    # A DEVICE RESET IS DETECTED, NOT ASSUMED AWAY. Added after
    # live_jam_arm_20261007T233908: the page went unanswered ~4 s into the
    # live arm, before any jam, and came back in mode 0 with tx_ok 0. The
    # first judge only asked whether ANY pre-jam sample was live, so it scored
    # a jam on a disarmed device as an (A) FAIL. Now: tx_ok falling, or the
    # mode leaving 3 before the harness posts mode 0, is a reset, and the
    # device must read live on the last answered sample before the jam.
    armed = [s for s in samples if s["pt"] >= ph["arm_posted"]
             and s["pt"] < ph.get("mode0_posted", float("inf"))]
    resets, unanswered, last_tx = [], 0, None
    for s in armed:
        sn = s["snap"]
        if sn is None:
            unanswered += 1
            continue
        tx = sn.get("tx_ok")
        if (last_tx is not None and tx is not None and tx < last_tx) or                 (sn.get("mode") not in (3, None) and s["pt"] > ph["arm_posted"] + 1.0):
            resets.append(round(s["pt"] - (j0 or s["pt"]), 3))
        if tx is not None:
            last_tx = tx
    out["unanswered_samples_while_armed"] = unanswered
    out["device_reset_seen_rel_jam_s"] = resets[:5]
    answered_pre = [s for s in samples if s["snap"] and j0 is not None
                    and ph["arm_posted"] < s["pt"] < j0]
    # Fresh, not merely last: one unanswered page request blocks for up to
    # 8 s (the HTTP timeout), so the last answered sample can be long stale.
    live_at_jam = (bool(answered_pre)
                   and answered_pre[-1]["pt"] >= j0 - 1.0
                   and bool(answered_pre[-1]["snap"].get("inhibit_live")))
    out["live_before_jam"] = live_at_jam
    if j0 is None or not live_at_jam or resets:
        out["verdict"] = "INCONCLUSIVE"
        out["summary"] = ("the device was not live at the start of the jam"
                          + ("; device reset seen at %s s rel jam" % resets[:3]
                             if resets else ""))
        out["A"] = out["B"] = out["C"] = "NOT EXERCISED"
        return out
    live_before = answered_pre

    jam_errs = [t for t in errs if j0 <= t <= j1]
    out["jam_seconds"] = round(j1 - j0, 3)
    out["witness_error_frames_in_jam"] = len(jam_errs)
    out["delivered_error_rate_per_s"] = round(len(jam_errs) / max(j1 - j0, 1e-9), 1)
    t10 = jam_errs[9] if len(jam_errs) >= 10 else None
    out["t_10th_error_rel_jam_s"] = None if t10 is None else round(t10 - j0, 4)

    lat = next((s for s in samples if s["pt"] >= j0 and s["snap"]
                and s["snap"].get("abort_latched")), None)
    base = live_before[-1]["snap"].get("drv_bus_error") or 0
    if lat is not None:
        ls = lat["snap"]
        out["latched_reason"] = ls.get("abort_reason")
        out["latched_seen_rel_jam_s"] = round(lat["pt"] - j0, 4)
        out["device_bus_error_delta_at_latch_sample"] = (
            (ls.get("drv_bus_error") or 0) - base)
        out["witness_errors_before_latch_sample"] = len(
            [t for t in errs if j0 <= t <= lat["pt"]])
    boff = next((s for s in samples if s["snap"]
                 and s["snap"].get("drv_state") == "bus_off"), None)
    out["busoff_rel_jam_s"] = None if boff is None else round(boff["pt"] - j0, 4)
    out["device_0x051_total"] = len(dev)
    out["last_device_0x051_rel_jam_s"] = (round(dev[-1] - j0, 4) if dev
                                          else None)

    # (A)
    if t10 is None:
        out["A"] = "NOT EXERCISED (fewer than 10 witness error frames)"
    elif lat is None:
        out["A"] = "FAIL: >= 10 error frames while live and no abort latched"
    elif TRIP6_TEXT in (lat["snap"].get("abort_reason") or ""):
        out["A"] = "PASS: trip 6 latched"
    else:
        out["A"] = ("FINDING: another trip latched first (%r); trip 6 could "
                    "not then latch" % lat["snap"].get("abort_reason"))

    # (B)
    if lat is None:
        out["B"] = "NOT EXERCISED (no abort latched)"
    else:
        problems = []
        # diag_flags by name, every diag page decoded in order by gi_diag (reading Q7,
        # 2026-10-24; byte 3 by hand before). An unknown schema raises.
        pages, _dec = gi_diag.decode_pages(frames, identity=rec.get("decoder"), decode_choices=False)
        nxt = next(((pt, pg) for pt, arb, pg in pages if arb == 0x7F1 and pt > lat["pt"]), None)
        if nxt is None:
            problems.append("no 0x7F1 on the wire after the latch")
        elif isinstance(nxt[1], gi_diag.Undecodable):
            problems.append("ERROR: the next 0x7F1 after the latch is undecodable (%s)" % nxt[1].reason)
        else:
            fl = int(nxt[1]["diag_flags"])
            out["next_0x7F1_flags"] = "0x%02X" % fl
            if fl & 0x20:
                problems.append("next 0x7F1 has bit5 inhibit_live set")
            if not fl & 0x80:
                problems.append("next 0x7F1 has bit7 abort_latched clear")
        relive = [s for s in samples if s["pt"] > lat["pt"] and s["snap"]
                  and s["snap"].get("inhibit_live")]
        if relive:
            problems.append("%d later page(s) read inhibit_live true"
                            % len(relive))
        late = [pt for pt in dev if pt > lat["pt"] + B_MARGIN_S]
        out["device_0x051_after_latch"] = len(late)
        if late:
            problems.append("%d device 0x051 after the latch" % len(late))
        if out["negative_control_equal_pairs"]:
            out["B"] = ("INCONCLUSIVE: the discriminator found %d equal pairs "
                        "with the device OFF" % len(pre))
        else:
            out["B"] = "FAIL: " + "; ".join(problems) if problems else "PASS"

    # (C)
    if boff is None:
        out["C"] = "NOT REACHED (no bus_off sample)"
    else:
        relive = [s for s in samples if s["pt"] > boff["pt"] and s["snap"]
                  and s["snap"].get("inhibit_live")]
        out["C"] = ("FAIL: live again after bus-off without a re-arm"
                    if relive else "PASS")

    fails = [k for k in ("A", "B", "C") if str(out[k]).startswith("FAIL")]
    out["verdict"] = "FAIL" if fails else "SEE A/B/C"
    out["summary"] = "A: %s | B: %s | C: %s" % (out["A"], out["B"], out["C"])
    return out


# --------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jam-seconds", type=float, default=5.0)
    ap.add_argument("--jam-offered", type=float, default=0.2,
                    help="offered occupancy for the jammer's pacing; the "
                         "DELIVERED rate is what the witness counts")
    ap.add_argument("--dry-run", action="store_true",
                    help="arm and go live, then stop without jamming")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = os.path.join(RUNS, "live_jam_arm_%s.json" % stamp)
    p = LF.page()
    build = LF.build_id()
    if p is None or p.get("mode") != 0:
        print("FAIL: device must answer and be in mode 0 (got %s)"
              % (None if p is None else p.get("mode")))
        return 1
    print("build %s" % build)
    stim = LF.build_stimulus()
    rec = {"when": stamp, "build": build, "pre": snap(p),
           "decoder": gi_diag.run_identity(),
           "jam_offered": a.jam_offered, "jam_bitrate": WRONG,
           "stimulus": LF.CAPTURE, "phases": {}, "samples": []}
    ph = rec["phases"]
    kv = rp = jm = None
    wit = jam = None
    replay = None
    try:
        kv = can.Bus(interface="kvaser", channel=0, bitrate=NOMINAL)
        wit = Witness(kv)
        wit.start()
        try:
            import libusb_package
            import usb.backend.libusb1 as lb
            lb.get_backend(find_library=libusb_package.find_library)
        except Exception:
            pass
        rp = kv
        rec["witness_sees_replay"] = False
        total = 3 + 12 + 5 + a.jam_seconds + 12 + 5
        rec["replay_stats"] = {}
        replay = threading.Thread(target=LF.play, args=(rp, stim, total),
                                  kwargs={"stats": rec["replay_stats"]},
                                  daemon=True)
        ph["replay_start"] = round(time.perf_counter(), 5)
        replay.start()
        poll(3.0, rec["samples"])
        ph["arm_posted"] = round(time.perf_counter(), 5)
        LF.dev_post("/gen_inhibit_set?mode=3")
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 12.0:
            poll(0.2, rec["samples"])
            s = rec["samples"][-1]["snap"] or {}
            if s.get("inhibit_live"):
                break
        live = (rec["samples"][-1]["snap"] or {}).get("inhibit_live")
        print("live: %s  (arm_block %r)" % (
            live, (rec["samples"][-1]["snap"] or {}).get("arm_block")))
        rec["went_live"] = bool(live)
        if not live:
            print("INCONCLUSIVE: never went live")
        else:
            poll(5.0, rec["samples"])
        if not live:
            pass
        elif a.dry_run:
            print("DRY RUN: live for 5 s, no jam")
        else:
            jm = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                         bitrate=WRONG, auto_reset=True)
            jam = Jammer(jm, (JAM_AIR_US / 1e6) / a.jam_offered)
            ph["jam_start"] = round(time.perf_counter(), 5)
            jam.start()
            poll(a.jam_seconds, rec["samples"])
            jam.stop_flag.set()
            jam.join(timeout=5.0)
            jm.shutdown()
            jm = None
            ph["jam_stop"] = round(time.perf_counter(), 5)
            rec["jam_sent"], rec["jam_send_exceptions"] = jam.sent, jam.errors
            print("jam: %d offered (%d exc) over %.1f s"
                  % (jam.sent, jam.errors, a.jam_seconds))
            poll(12.0, rec["samples"])
    finally:
        if jam is not None:
            jam.stop_flag.set()
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        ph["mode0_posted"] = round(time.perf_counter(), 5)
        time.sleep(1.0)
        rec["post"] = snap(LF.page())
        if replay is not None:
            replay.join(timeout=60.0)
        if wit is not None:
            wit.stop_flag.set()
            wit.join(timeout=3.0)
            rec["wire"] = list(wit.frames)
            rec["witness_error_pts"] = list(wit.errors)
        for b in (jm, kv):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass
        print("restored mode 0: %s" % rec.get("post"))
        ids = {}
        for _, arb, _ in rec.get("wire") or []:
            ids["0x%03X" % arb] = ids.get("0x%03X" % arb, 0) + 1
        rec["witness_id_counts"] = ids
        print("replay stats %s; witness kept ids %s; witness error frames %d"
              % (rec.get("replay_stats"), ids,
                 len(rec.get("witness_error_pts") or [])))

    if not a.dry_run and rec.get("went_live"):
        rec["judge"] = judge(rec)
        print("\n" + rec["judge"]["summary"])
        for k in ("delivered_error_rate_per_s", "t_10th_error_rel_jam_s",
                  "latched_reason", "latched_seen_rel_jam_s",
                  "device_bus_error_delta_at_latch_sample",
                  "witness_errors_before_latch_sample", "busoff_rel_jam_s",
                  "last_device_0x051_rel_jam_s", "device_0x051_after_latch",
                  "negative_control_equal_pairs", "next_0x7F1_flags"):
            print("  %-42s %s" % (k, rec["judge"].get(k)))
    os.makedirs(RUNS, exist_ok=True)
    with open(path, "x", encoding="ascii") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
