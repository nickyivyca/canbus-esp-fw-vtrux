"""A3: skipped inhibit frames and trip 7, by the spec 12.4 bench method.

Gen-inhibit tester, 2026-10-07. Written from spec 12.4's "skipped inhibit
frames" row (5.2 item 5, section 7 trip 7), not from product source:

  "the stimulus sends VCM N and N+1 back to back, then ~2 ms of filler below
  0x051, so our answer to N loses arbitration and is withdrawn. With no filler
  it goes out late and must count as a late skip. Three events within 1 s must
  trip 7 (reason 15); two, or a third at ~1.1 s, must not. The witness
  classifies each event by wire order, and the device's counts must agree."

ARMS (each a fresh arm: mode 3, live, events, read, mode 0):
  w3   filler,    3 events at 0 / 0.4 / 0.8 s   -> 3 withdrawn, trip 7
  w2   filler,    2 events at 0 / 0.4 s         -> 2 withdrawn, no trip
  w3s  filler,    3 events at 0 / 0.55 / 1.1 s  -> 3 withdrawn, no trip
  l3   no filler, 3 events at 0 / 0.4 / 0.8 s   -> 3 late, trip 7
  l2   no filler, 2 events                      -> 2 late, no trip
  l3s  no filler, 3 events at 0 / 0.55 / 1.1 s  -> 3 late, no trip

ADAPTERS (one segment, firmware/bench-holder.md):
  PCAN 500 kbit    replays the interlock slice lowid_flood_skip_bench.py arms
                   with, and makes each event: VCM N, the stimulus's next 0x051
                   (N+1) queued straight behind it, then (filler arms) 9 x
                   0x050 (~2 ms at 222 us each). One adapter's frames leave in
                   queue order, so N+1 follows N back to back and the filler
                   follows N+1. The N+1 so used is dropped from its normal slot.
  Kvaser 500 kbit  INDEPENDENT witness (it transmits nothing). Records every
                   0x050 / 0x051 / WiCAN diag frame in arrival order with its
                   hardware timestamp, and every error frame.

CLASSIFYING AN EVENT FROM THE WIRE. Each event is preceded on the PCAN by a
unique marker frame (0x6A5, the event number in B0-B3), so the witness can
find it without relying on counters, which repeat every 16 frames. The device's answer to N carries N+1's
counter and is byte-identical to the VCM's N+1 (spec 7, echo detection), so
payload cannot say whose frame is whose; ORDER and COUNT can:
  * find N on the wire (its payload, after the event was injected);
  * the frame straight after it must be the VCM's N+1, within one frame time
    plus slack (BACK_TO_BACK_S) -- otherwise the event is DISPLACED and is not
    classified, because then the device could have got in first;
  * every further 0x051 carrying N+1's counter before N+2's counter appears is
    the device's late answer to N.
  0 such frames -> WITHDRAWN, >= 1 -> LATE. The filler is counted between N+1
  and the next 0x051, so a filler arm whose filler did not reach the wire in
  that gap is visible.

JUDGED PER ARM: every event classified (none displaced); witness classes as
expected; the device's skips_withdrawn / skips_late deltas EQUAL the witness's
counts (a late frame the device did not count is the failure the witness is
for); trip 7 latched exactly when expected; and every abort the device
latches is attributed by its reason -- any abort other than trip 7 makes the
arm INCONCLUSIVE for the trip-7 question and is reported (reviewer,
2026-10-07: an error-rate trip under this method is a result in itself).

  py -3.14 tools/gen_inhibit/bench/skip_method_bench.py [--arms w3,w2,...] [--dry-run]
Writes runs/skip_method_<stamp>.json (never overwrites).
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

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
BITRATE = 500000
VCM_ID = 0x051
FILLER_ID = 0x050
FILLER_FRAMES = 9
BACK_TO_BACK_S = 0.0015     # N -> N+1 gap ceiling; catches a PCAN queue hiccup
MARKER_ID = 0x6A5           # unused on the truck's powertrain bus slice here
KEEP_IDS = {0x050, 0x051, MARKER_ID, 0x7F1, 0x7F2, 0x7F8, 0x7F9}
TRIP7_TEXT = "skipped within 1 s"

ARMS = {
    "w3":  (True,  (0.0, 0.4, 0.8),  "withdrawn", True),
    "w2":  (True,  (0.0, 0.4),       "withdrawn", False),
    "w3s": (True,  (0.0, 0.55, 1.1), "withdrawn", False),
    "l3":  (False, (0.0, 0.4, 0.8),  "late",      True),
    "l2":  (False, (0.0, 0.4),       "late",      False),
    "l3s": (False, (0.0, 0.55, 1.1), "late",      False),
}


def snap(p):
    if p is None:
        return None
    return {k: p.get(k) for k in ("mode", "inhibit_live", "abort_latched",
                                  "abort_reason", "arm_block", "tx_ok",
                                  "skips_withdrawn", "skips_late")}


class Witness(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_flag = threading.Event()
        self.frames = []        # (seq, hw_ts, arb, hex)
        self.errors = []        # hw_ts
        self.lock = threading.Lock()
        self.seq = 0

    def run(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                with self.lock:
                    if m.is_error_frame:
                        self.errors.append(m.timestamp)
                    elif m.arbitration_id in KEEP_IDS:
                        self.frames.append((self.seq, m.timestamp,
                                            m.arbitration_id,
                                            bytes(m.data).hex()))
                    self.seq += 1
                m = self.bus.recv(timeout=0.0)

    def mark(self):
        with self.lock:
            return len(self.frames)


class Player(threading.Thread):
    """Loops the interlock slice on the PCAN; performs requested events."""

    def __init__(self, bus, stim):
        super().__init__(daemon=True)
        self.bus, self.stim = bus, stim
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.pending = []       # (perf_due, filler, tag)
        self.done = []          # dicts per performed event
        self.sent = self.fail = 0

    def request(self, perf_due, filler, tag):
        with self.lock:
            self.pending.append((perf_due, filler, tag))
            self.pending.sort()

    def _send(self, arb, data):
        try:
            self.bus.send(can.Message(arbitration_id=arb, data=data,
                                      is_extended_id=False))
            self.sent += 1
            return True
        except Exception:
            self.fail += 1
            return False

    def run(self):
        t0 = time.perf_counter()
        base = 0.0
        span = self.stim[-1][0] if self.stim else 1.0
        while not self.stop_flag.is_set():
            skip_next_vcm = False
            for i, (rel, arb, data) in enumerate(self.stim):
                if self.stop_flag.is_set():
                    break
                target = t0 + base + rel
                while True:
                    left = target - time.perf_counter()
                    if left <= 0:
                        break
                    if left > 0.002:
                        time.sleep(left - 0.002)
                if arb == VCM_ID and skip_next_vcm:
                    skip_next_vcm = False
                    continue
                ev = None
                if arb == VCM_ID:
                    with self.lock:
                        if self.pending and self.pending[0][0] <= time.perf_counter():
                            ev = self.pending.pop(0)
                if ev is None:
                    self._send(arb, data)
                    continue
                nxt = next((d for r, a, d in self.stim[i + 1:] if a == VCM_ID),
                           None)
                if nxt is None:
                    self._send(arb, data)
                    with self.lock:
                        self.pending.insert(0, ev)
                    continue
                t_ev = time.perf_counter()
                self.n_marker = getattr(self, "n_marker", 0) + 1
                mk = self.n_marker.to_bytes(4, "big") + b"ZZZZ"
                self._send(MARKER_ID, mk)
                self._send(VCM_ID, data)
                self._send(VCM_ID, nxt)
                if ev[1]:
                    for _ in range(FILLER_FRAMES):
                        self._send(FILLER_ID, bytes([0xA5] * 8))
                skip_next_vcm = True
                self.done.append({"tag": ev[2], "filler": ev[1],
                                  "marker": mk.hex(),
                                  "perf": round(t_ev, 5),
                                  "n": data.hex(), "n1": nxt.hex()})
            base += span


# ------------------------------------------------------------- classifying --

def errors_per_event(marker_ts, error_ts, tail_s=1.0):
    """Witness error frames in each event's window: from its marker to the next
    event's marker (the last: tail_s after its own). marker_ts may hold None for an
    event whose marker was not seen; that event gets None. (Reviewer, 2026-10-10.)"""
    out = []
    seen = [t for t in marker_ts if t is not None]
    for t in marker_ts:
        if t is None:
            out.append(None)
            continue
        later = [u for u in seen if u > t]
        end = min(later) if later else t + tail_s
        out.append(sum(1 for e in error_ts if t <= e < end))
    return out


def classify_event(frames, ev, start_idx=0):
    """Classify one event from the witness frames (list of (seq,ts,arb,hex))."""
    n, n1 = bytes.fromhex(ev["n"]), bytes.fromhex(ev["n1"])
    out = {"tag": ev["tag"], "filler": ev["filler"]}
    if len(n) < 6 or len(n1) < 6:
        out["class"] = "unclassifiable_short_frame"
        return out, start_idx
    out["counter_step_ok"] = ((n[5] + 1) & 0xFF) == n1[5] or \
        ((n[5] & 0xF0) == (n1[5] & 0xF0) and ((n[5] + 1) & 0x0F) == (n1[5] & 0x0F))
    # ANCHOR ON THE MARKER, not on N's payload: counters repeat every 16
    # frames, and the witness's timestamps cannot separate a back-to-back
    # N+1 from a normal device answer. The marker is queued straight ahead of
    # N on the same adapter, so it precedes N on the wire.
    mk = next((k for k in range(start_idx, len(frames))
               if frames[k][2] == MARKER_ID and frames[k][3] == ev["marker"]),
              None)
    if mk is None:
        out["class"] = "marker_not_seen"
        return out, start_idx
    out["marker_ts"] = frames[mk][1]
    idx = next((k for k in range(mk + 1, len(frames))
                if frames[k][2] == VCM_ID), None)
    if idx is None or frames[idx][3] != ev["n"]:
        out["class"] = "n_not_next_after_marker"
        return out, mk + 1
    rest = frames[idx + 1:]
    v = [f for f in rest if f[2] == VCM_ID]
    if not v:
        out["class"] = "n1_not_seen"
        return out, idx + 1
    first = v[0]
    gap = first[1] - frames[idx][1]
    out["n_to_next_0x051_s"] = round(gap, 6)
    if bytes.fromhex(first[3])[5] != n1[5]:
        out["class"] = "displaced_next_is_not_n1"
        return out, idx + 1
    if gap > BACK_TO_BACK_S:
        out["class"] = "displaced_not_back_to_back"
        return out, idx + 1
    after = rest[rest.index(first) + 1:]
    extra = 0
    filler_before_next = 0
    seen_vcm_after = False
    for f in after:
        if f[2] == FILLER_ID and not seen_vcm_after:
            filler_before_next += 1
            continue
        if f[2] != VCM_ID:
            continue
        seen_vcm_after = True
        c = bytes.fromhex(f[3])[5]
        if c == n1[5]:
            extra += 1
        else:
            break
    out["filler_between"] = filler_before_next
    out["device_answers_to_n"] = extra
    # In a filler arm the filler must sit between the VCM's N+1 and the next
    # 0x051. If it does not, the frame taken for the VCM's N+1 may have been
    # the device's answer getting in first, and the event cannot be read.
    if ev["filler"] and filler_before_next == 0:
        out["class"] = "displaced_no_filler_after_n1"
        return out, idx + 1
    out["class"] = "late" if extra else "withdrawn"
    return out, idx + 1


def judge_arm(arm):
    filler, offs, want_cls, want_trip = ARMS[arm["name"]]
    evs = arm.get("events_classified") or []
    out = {"expected_class": want_cls, "expected_trip7": want_trip}
    problems, inconclusive = [], []
    if not arm.get("went_live"):
        return dict(out, verdict="INCONCLUSIVE", why=["never went live"])
    if len(evs) != len(offs):
        inconclusive.append("%d of %d events performed" % (len(evs), len(offs)))
    cls = [e.get("class") for e in evs]
    bad = [c for c in cls if c not in ("withdrawn", "late")]
    if bad:
        inconclusive.append("events not classifiable: %s" % bad)
    nw, nl = cls.count("withdrawn"), cls.count("late")
    out["witness_withdrawn"], out["witness_late"] = nw, nl
    pre, post = arm.get("pre_events") or {}, arm.get("post_events") or {}
    dw = (post.get("skips_withdrawn") or 0) - (pre.get("skips_withdrawn") or 0)
    dl = (post.get("skips_late") or 0) - (pre.get("skips_late") or 0)
    out["device_withdrawn_delta"], out["device_late_delta"] = dw, dl
    if not bad:
        if any(c != want_cls for c in cls):
            problems.append("witness classes %s, expected all %s" % (cls, want_cls))
        if (dw, dl) != (nw, nl):
            problems.append("device counted withdrawn %d / late %d, witness "
                            "saw %d / %d" % (dw, dl, nw, nl))
    latched = bool(post.get("abort_latched"))
    reason = post.get("abort_reason") or ""
    out["abort_reason"] = reason
    if latched and TRIP7_TEXT not in reason:
        inconclusive.append("a different abort latched: %r" % reason)
    elif want_trip and not latched:
        problems.append("trip 7 expected and no abort latched")
    elif not want_trip and latched:
        problems.append("trip 7 latched (%r) and must not" % reason)
    if pre.get("abort_latched"):
        inconclusive.append("abort already latched before the events: %r"
                            % pre.get("abort_reason"))
    if (pre.get("skips_withdrawn") or 0) or (pre.get("skips_late") or 0):
        inconclusive.append("skips already non-zero before the events")
    if problems:
        out["verdict"] = "FAIL"
    elif inconclusive:
        out["verdict"] = "INCONCLUSIVE"
    else:
        out["verdict"] = "PASS"
    out["why"] = problems + inconclusive
    return out


def judge_arm_v2(arm):
    """The reviewer's ruling of 2026-10-07: spec 7 and 5.2 govern the split.

    A frame still in the controller at the deadline "may have reached the
    wire late" and is booked LATE even if the witness saw it never go out; the
    device's "withdrawn" kind is a frame purged before it reached the
    controller. So the witness's never-on-wire and the device's withdrawn are
    not the same quantity, and 12.4's "counts must agree" is replaced by:
      1. device withdrawn + late == witness never-on-wire + witness late
      2. device late >= witness late (a frame seen late is never booked withdrawn)
      3. device withdrawn <= witness never-on-wire
      4. trip 7 exactly where 3 skips fall within 1 s
    Pending the user's approval of the corrected 12.4 wording.
    """
    filler, offs, _, want_trip = ARMS[arm["name"]]
    evs = arm.get("events_classified") or []
    out = {"rule": "reviewer 2026-10-07, spec 7 / 5.2 govern the split",
           "expected_trip7": want_trip}
    problems, inconclusive = [], []
    if not arm.get("went_live"):
        return dict(out, verdict="INCONCLUSIVE", why=["never went live"])
    if len(evs) != len(offs):
        inconclusive.append("%d of %d events performed" % (len(evs), len(offs)))
    cls = [e.get("class") for e in evs]
    bad = [c for c in cls if c not in ("withdrawn", "late")]
    if bad:
        inconclusive.append("events not classifiable: %s" % bad)
    wn, wl = cls.count("withdrawn"), cls.count("late")
    pre, post = arm.get("pre_events") or {}, arm.get("post_events") or {}
    dw = (post.get("skips_withdrawn") or 0) - (pre.get("skips_withdrawn") or 0)
    dl = (post.get("skips_late") or 0) - (pre.get("skips_late") or 0)
    out.update(witness_never_on_wire=wn, witness_late=wl,
               device_withdrawn_delta=dw, device_late_delta=dl)
    if not bad:
        if dw + dl != wn + wl:
            problems.append("rule 1: device skips %d != witness skips %d"
                            % (dw + dl, wn + wl))
        if dl < wl:
            problems.append("rule 2: device late %d < witness late %d"
                            % (dl, wl))
        if dw > wn:
            problems.append("rule 3: device withdrawn %d > witness "
                            "never-on-wire %d" % (dw, wn))
    latched = bool(post.get("abort_latched"))
    reason = post.get("abort_reason") or ""
    out["abort_reason"] = reason
    if latched and TRIP7_TEXT not in reason:
        inconclusive.append("a different abort latched: %r" % reason)
    elif want_trip and not latched:
        problems.append("rule 4: trip 7 expected and no abort latched")
    elif not want_trip and latched:
        problems.append("rule 4: trip 7 latched (%r) and must not" % reason)
    if pre.get("abort_latched"):
        inconclusive.append("abort already latched before the events: %r"
                            % pre.get("abort_reason"))
    if (pre.get("skips_withdrawn") or 0) or (pre.get("skips_late") or 0):
        inconclusive.append("skips already non-zero before the events")
    out["verdict"] = ("FAIL" if problems else
                      "INCONCLUSIVE" if inconclusive else "PASS")
    out["why"] = problems + inconclusive
    return out


# --------------------------------------------------------------------- main --

def run_arm(name, player, wit, rec_arm):
    filler, offs, _, _ = ARMS[name]
    LF.dev_post("/gen_inhibit_set?mode=3")
    t0 = time.perf_counter()
    live = False
    while time.perf_counter() - t0 < 10.0:
        s = snap(LF.page())
        if s and s.get("inhibit_live"):
            live = True
            break
        time.sleep(0.2)
    rec_arm["went_live"] = live
    if not live:
        return
    time.sleep(2.0)
    rec_arm["pre_events"] = snap(LF.page())
    mark = wit.mark()
    err_mark = len(wit.errors)
    n_done = len(player.done)
    base = time.perf_counter() + 0.3
    for k, off in enumerate(offs):
        player.request(base + off, filler, "%s#%d" % (name, k))
    time.sleep(offs[-1] + 2.5)
    rec_arm["post_events"] = snap(LF.page())
    rec_arm["events"] = player.done[n_done:]
    frames = wit.frames[mark:]
    # 0x7F9 (skips page): every one seen during the arm, raw, so the CAN-side
    # skip counts can be read against the page's (spec 10: skips go on both).
    rec_arm["wire_0x7F9"] = [(ts, hx) for _, ts, arb, hx in frames
                             if arb == 0x7F9]
    out, idx = [], 0
    for ev in rec_arm["events"]:
        c, idx = classify_event(frames, ev, idx)
        out.append(c)
    rec_arm["events_classified"] = out
    errs = wit.errors[err_mark:]
    rec_arm["witness_error_frames_arm"] = len(errs)
    for c, n_err in zip(out, errors_per_event([c.get("marker_ts") for c in out], errs)):
        c["witness_error_frames"] = n_err
    LF.dev_post("/gen_inhibit_set?mode=0")
    time.sleep(1.0)
    rec_arm["after_off"] = snap(LF.page())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="w3,w2,w3s,l3,l2,l3s")
    ap.add_argument("--dry-run", action="store_true",
                    help="arm, go live, no events")
    a = ap.parse_args()
    names = [x for x in a.arms.split(",") if x]
    for n in names:
        if n not in ARMS:
            print("unknown arm %s" % n)
            return 1
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = os.path.join(RUNS, "skip_method_%s.json" % stamp)
    p = LF.page()
    if p is None or p.get("mode") != 0:
        print("FAIL: device must be in mode 0")
        return 1
    rec = {"when": stamp, "build": LF.build_id(), "stimulus": LF.CAPTURE,
           "filler_frames": FILLER_FRAMES, "back_to_back_s": BACK_TO_BACK_S,
           "arms": []}
    print("build %s" % rec["build"])
    stim = LF.build_stimulus()
    kv = pc = player = wit = None
    try:
        kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
        wit = Witness(kv)
        wit.start()
        pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
        player = Player(pc, stim)
        player.start()
        time.sleep(2.0)
        for name in (["dry"] if a.dry_run else names):
            arm = {"name": name}
            rec["arms"].append(arm)
            if a.dry_run:
                LF.dev_post("/gen_inhibit_set?mode=3")
                time.sleep(4.0)
                arm["page"] = snap(LF.page())
                LF.dev_post("/gen_inhibit_set?mode=0")
                print("dry: %s" % arm["page"])
                continue
            run_arm(name, player, wit, arm)
            arm["judge_12_4_original"] = judge_arm(arm)
            arm["judge"] = judge_arm_v2(arm)
            j = arm["judge"]
            print("%-4s %-12s witness never/late %s/%s device w/l %s/%s "
                  "abort %r -- %s"
                  % (name, j["verdict"], j.get("witness_never_on_wire"),
                     j.get("witness_late"), j.get("device_withdrawn_delta"),
                     j.get("device_late_delta"), j.get("abort_reason"),
                     "; ".join(j.get("why") or [])))
            for e in arm.get("events_classified") or []:
                print("      %s" % e)
    finally:
        try:
            LF.dev_post("/gen_inhibit_set?mode=0")
        except Exception:
            pass
        if player is not None:
            player.stop_flag.set()
            player.join(timeout=15.0)
            rec["player_sent"], rec["player_fail"] = player.sent, player.fail
        if wit is not None:
            wit.stop_flag.set()
            wit.join(timeout=3.0)
            rec["witness_error_frames"] = len(wit.errors)
        for b in (pc, kv):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass
        rec["post"] = snap(LF.page())
        print("restored: %s; player sent %s fail %s; witness error frames %s"
              % (rec["post"], rec.get("player_sent"), rec.get("player_fail"),
                 rec.get("witness_error_frames")))
    os.makedirs(RUNS, exist_ok=True)
    with open(path, "x", encoding="ascii") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
