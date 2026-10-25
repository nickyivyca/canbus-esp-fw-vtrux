#!/usr/bin/env python3
"""Can this bench drive the WiCAN's controller to bus-off, and does bus-off
raise TWAI_ALERT_TX_FAILED on the ESP32-C3?

SUPERSEDES THE INTERFERENCE HALF OF `txfailed_busoff_probe.py`, which is kept
as the record of the 2026-10-05 run. That run read `drv.bus_error` 0 ->
1,012,036 in 40 s with `tx_failed` 0 and concluded, correctly,
`inconclusive_busoff_not_reached`. It could not say WHY, because the status
page carried no controller state and no error counters -- only `bus_error`,
which counts error EVENTS and says nothing about whether any of them moved the
controller towards bus-off. Spec 11 gained `state`, `tec` and `rec` on
2026-10-06 for exactly this; this script is the first thing to use them.

WHAT CHANGED IN THE METHOD, and why a single burst cannot answer the question.

The first run faced a dilemma it could not see its way out of. Bus-off keys on
TEC, and TEC rises ONLY on transmit errors -- a receive error moves REC, which
has no bus-off threshold. In PASSIVE with nothing replaying `0x051`, the device
transmits only its own diagnostic pages, about 3 frames per second. That is the
entire transmit-attempt budget. Each failed attempt adds 8 to TEC; each
SUCCESSFUL one subtracts 1. So:

    interferer too sparse  -> the device's frames mostly get through, each
                              success decrements TEC, and TEC sits at 0
    interferer too dense   -> ... is the half nobody had measured

and with no TEC on the page there was no way to tell which side of that line a
run had landed on. The 2026-10-05 run asked for 0.5 ms and achieved 1.91 ms
(~24 % occupancy) and so was certainly on the sparse side, but that was worked
out afterwards from the frame count, not observed.

So this walks a LADDER of interferer occupancies and watches TEC at each step.
That turns the unknown into a measurement: whichever way TEC moves at each
rung is the answer to "is this stimulus loading the transmit path or not",
available while the run is happening rather than inferred from it later.

THE LADDER CAN FAIL AND SAY SO. Three outcomes are expected, and only the
first is the question actually being asked:

  TEC crosses 255 -> bus-off. Then `state` reads `bus_off` and we can finally
     see whether `tx_failed` moved with it. That is the answer.
  TEC climbs and PLATEAUS near 128 -> error-passive, and stuck there. A
     plateau ON ITS OWN settles nothing, because two mechanisms produce one
     (raised by the interposer session, 2026-10-06, before this first ran):
     the ISO 11898-1 acknowledgement-error exemption, under which an
     error-passive transmitter stops incrementing TEC on an acknowledgement
     error when it sees no dominant bit during its own passive error flag;
     and plain equilibrium, since TEC rises 8 per transmit error and falls 1
     per success, so any error rate matched by a 1:8 success rate parks TEC
     at a stable value with no exemption involved. 128 is additionally the
     error-passive threshold itself, so a value near it may be a boundary
     rather than a plateau. THE LADDER IS WHAT SEPARATES THEM: under the
     exemption the increment has stopped, so the plateau should not move as
     occupancy rises; under equilibrium successes get rarer, so it should
     climb rung by rung. `plateau_discriminator()` scores that, and refuses
     on fewer than two loaded rungs.
  TEC never leaves 0 -> the interferer is not colliding with the device's
     transmissions at all, at any rung. The bench cannot produce the
     stimulus, and that is a finding about the bench, not about the C3.

THE DRAIN RUNS ON ITS OWN THREAD, and this is not a detail. Measured on
`NICKY-XPS` 2026-10-05: a single-threaded loop interleaving `send()` and
`recv()` reaches ~970 fps against a 4,505 fps wire, 22 % occupancy, with zero
exceptions to announce the cap. Moving the drain to a thread reached 4,424 fps,
98 %. Every sparse stimulus in this investigation traces to that loop shape.
Here the SENDER is the thread and the main loop drains and polls, which is the
same split the other way round. See `tools-reference.md`, "Throughput traps
common to both dongles".

SUB-MILLISECOND PACING IS BUSY-WAITED, deliberately. `time.sleep()` on Windows
has millisecond granularity and silently oversleeps a sub-millisecond request
-- that is half of why the first run achieved 1.91 ms against a requested
0.5 ms. Below SPIN_THRESHOLD_S the sender spins on `perf_counter()` instead.
It costs a core for the length of the run and it is the only way to hit the
top rungs.

THE BURST STOP COMES FROM THE WITNESS, not from the host clock. The
2026-10-05 record's `burst_stop` was the moment the host stopped calling
`send()`; `drv.bus_error` then rose another 15,032 counts before freezing,
about 0.6 s of further interference at the rate then current, because the
PCAN's transmit queue was still draining. (Found by the reviewing session,
2026-10-06.) So each rung records the witness timestamp of the last interferer
frame actually seen on the wire, and that is what bounds the window.

ACKNOWLEDGERS MUST BE PRESENT BEFORE THE DEVICE TRANSMITS. A segment with
nothing to acknowledge makes every transmitter retry at wire rate. The Kvaser
is opened and settled BEFORE the device is put into PASSIVE, and the time it
came up is recorded so the other session can align its own counters.

THIS TRANSMITS ON THE SHARED VEHICLE SEGMENT. The interposer session's board
is on this wire and its error counters will move. Tell them before running.

    py -3.14 tools/gen_inhibit/bench/tec_busoff_sweep.py
    py -3.14 ... --step-seconds 20 --ladder 0.2,0.5,0.8,0.95
    py -3.14 ... --dry-run            # preconditions only, nothing transmitted
"""

import argparse
import collections
import json
import os
import sys
import threading
import time
import urllib.request


import can                                                     # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

HOST = "192.168.80.1"
NOMINAL = 500000
WRONG = 250000
MODE_PASSIVE = 4
MODE_OFF = 0

WICAN_DIAG = (0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)
BOARD_DIAG = (0x7F4, 0x7F5, 0x7F6, 0x7F7)    # the other session's board
INTERFERE_ID = 0x123                          # ours; not a truck id, not diag

# An 8-byte standard frame at 250 kbit is ~115 bits plus IFS, about 460 us of
# air. Occupancy of the 500 kbit segment is what matters to the victim, and
# the interferer's bits occupy the wire for the same wall-clock time whatever
# the victim thinks the bitrate is.
AIR_US = 460.0

# Below this, sleep() is useless and the sender spins instead.
SPIN_THRESHOLD_S = 0.002

# The device must be seen transmitting in the baseline or nothing afterwards
# can be read as "it stopped".
MIN_BASELINE_DIAG = 4

# CAN error-state boundaries, for labelling only -- the device reports `state`
# itself and that is what the verdict uses.
TEC_ERROR_PASSIVE = 128
TEC_BUS_OFF = 256


def page():
    """The whole /gen_inhibit JSON, or None."""
    try:
        with urllib.request.urlopen("http://%s/gen_inhibit" % HOST,
                                    timeout=5.0) as r:
            d, _ = json.JSONDecoder().raw_decode(
                r.read().decode("utf-8", "replace").lstrip())
        return d
    except Exception:
        return None


def build_id():
    """The running image, from /check_status.

    NOT from /gen_inhibit -- that page has no git_version field, so reading it
    there yields null and the run record silently fails to identify the build.
    `txfailed_busoff_probe.py` does exactly that, which is why its record's
    `git_version` is null.
    """
    try:
        with urllib.request.urlopen("http://%s/check_status" % HOST,
                                    timeout=5.0) as r:
            d, _ = json.JSONDecoder().raw_decode(
                r.read().decode("utf-8", "replace").lstrip())
        return {"git_version": d.get("git_version"),
                "fw_version": d.get("fw_version"),
                "hw_version": d.get("hw_version")}
    except Exception:
        return None


def snap(p):
    """The fields this question turns on, flattened."""
    if p is None:
        return None
    drv = p.get("drv") or {}
    sch = p.get("sched") or {}
    cls = sch.get("cls") or {}
    out = {
        "mode": p.get("mode"),
        "bus_on": p.get("bus_on"),
        "bus_ours": p.get("bus_ours"),
        "rx_errors": p.get("rx_errors"),
        "abort_reason": p.get("abort_reason"),
        "abort_latched": p.get("abort_latched"),
        # Arm state (2026-10-07, reviewer): a trip only ends a LIVE (or, in
        # PASSIVE, would-be-live) inhibit, so whether trip 6 is expected to
        # latch depends on these, and a record without them cannot say.
        "inhibit_live": p.get("inhibit_live"),
        "arm_block": p.get("arm_block"),
        "drv_present": bool(p.get("drv")),
        # The three spec 11 added 2026-10-06. `state` and the two counters are
        # INSTANTANEOUS; everything else in `drv` is cumulative.
        "drv_state": drv.get("state"),
        "drv_tec": drv.get("tec"),
        "drv_rec": drv.get("rec"),
        "drv_bus_error": drv.get("bus_error"),
        "drv_tx_failed": drv.get("tx_failed"),
        "drv_arb_lost": drv.get("arb_lost"),
        "drv_msgs_to_tx": drv.get("msgs_to_tx"),
        "sched_tx_failed": sch.get("tx_failed"),
        "sched_tx_refused": sch.get("tx_refused"),
        "sched_aborts": sch.get("aborts"),
        "sched_skipped": sch.get("skipped"),
    }
    for k in ("inhibit", "probe", "telemetry"):
        c = cls.get(k) or {}
        out["refused_" + k] = c.get("refused")
        out["sent_" + k] = c.get("sent")
    return out


def set_mode(mode):
    try:
        req = urllib.request.Request(
            "http://%s/gen_inhibit_set?mode=%d" % (HOST, mode),
            data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=8.0) as r:
            return r.status == 200
    except Exception:
        return False


def diag_total(counts, ids):
    return sum(counts[i] for i in ids)


class Sender(threading.Thread):
    """Paces the interferer on its own thread at a target wire occupancy.

    The main thread must NOT send: see the module docstring. Sleeps above
    SPIN_THRESHOLD_S, spins below it, because Windows cannot sleep for less
    than about a millisecond and silently oversleeps when asked to.
    """

    def __init__(self, bus, period_s):
        super().__init__(daemon=True)
        self.bus = bus
        self.period = period_s
        self.stop_flag = threading.Event()
        self.sent = 0
        self.errors = 0
        self.last_send_perf = None

    def run(self):
        msg = can.Message(arbitration_id=INTERFERE_ID,
                          data=bytes([0x55] * 8), is_extended_id=False)
        nxt = time.perf_counter()
        while not self.stop_flag.is_set():
            try:
                # No timeout: on the Kvaser a timeout routes to the blocking
                # canWriteWait and caps at ~988 fps. The PCAN was measured not
                # to care (975 with, 970 without) but the habit is the point.
                self.bus.send(msg)
                self.sent += 1
                self.last_send_perf = time.perf_counter()
            except Exception:
                self.errors += 1
            nxt += self.period
            while True:
                left = nxt - time.perf_counter()
                if left <= 0:
                    break
                if left > SPIN_THRESHOLD_S:
                    time.sleep(left - SPIN_THRESHOLD_S)
            # If we have fallen behind by more than a whole period, do not try
            # to catch up -- that produces a burst that misrepresents the rung.
            now = time.perf_counter()
            if nxt < now - self.period:
                nxt = now


# Every 0x7F1 the witness sees, as (perf_counter, payload hex). Added
# 2026-10-07 by the gen-inhibit tester for spec 11's bus_on check: the bit on
# the wire is judged against the page, so each frame is kept, not just counted.
# perf_counter is the one clock shared with the samples' "pt".
WIRE_7F1 = []


def drain_and_poll(kv, seconds, counts, poll_every=0.25, t_origin=None):
    """Drain the witness for `seconds`, polling the device page as we go.

    Returns (error_frames, samples, last_interferer_perf, last_any_perf).
    The two timestamps are what the burst stop is taken from, rather than the
    host's "I stopped calling send()" moment.
    """
    errs = 0
    samples = []
    t0 = time.perf_counter()
    base = t_origin if t_origin is not None else t0
    next_poll = 0.0
    last_intf = None
    last_any = None
    while True:
        el = time.perf_counter() - t0
        if el >= seconds:
            break
        m = kv.recv(timeout=0.0)
        while m is not None:
            now = time.perf_counter()
            last_any = now
            if m.is_error_frame:
                errs += 1
            else:
                counts[m.arbitration_id] += 1
                if m.arbitration_id == INTERFERE_ID:
                    last_intf = now
                if m.arbitration_id == 0x7F1:
                    WIRE_7F1.append((round(now, 4), bytes(m.data).hex()))
            m = kv.recv(timeout=0.0)
        if el >= next_poll:
            s = snap(page())
            samples.append({"t": round(time.perf_counter() - base, 2),
                            "pt": round(time.perf_counter(), 4),
                            "snap": s})
            next_poll = el + poll_every
        time.sleep(0.001)
    return errs, samples, last_intf, last_any


def tec_track(samples):
    """Peak and final TEC/REC and whether bus-off was ever reported."""
    tecs = [s["snap"]["drv_tec"] for s in samples
            if s.get("snap") and s["snap"].get("drv_tec") is not None]
    recs = [s["snap"]["drv_rec"] for s in samples
            if s.get("snap") and s["snap"].get("drv_rec") is not None]
    states = [s["snap"]["drv_state"] for s in samples
              if s.get("snap") and s["snap"].get("drv_state")]
    return {
        "tec_peak": max(tecs) if tecs else None,
        "tec_final": tecs[-1] if tecs else None,
        # THE TRAJECTORY, not only its maximum. A peak alone cannot separate
        # the two mechanisms that both produce a plateau -- see
        # plateau_discriminator() below.
        "tec_series": tecs,
        "rec_peak": max(recs) if recs else None,
        "rec_final": recs[-1] if recs else None,
        "states_seen": sorted(set(states)),
        "busoff_seen": "bus_off" in states,
        "recovering_seen": "recovering" in states,
        "samples_with_counters": len(tecs),
    }


# One transmit error adds 8 to TEC. Two rungs whose peaks differ by less than
# a single increment have not been shown to differ at all.
PIN_TOLERANCE = 8


def plateau_discriminator(rungs):
    """Does a TEC plateau MOVE as the interferer gets denser?

    A plateau is not self-interpreting, and this is the project's recurring
    failure shape: a number that looks like a result while two mechanisms sit
    behind it. Raised by the interposer session, 2026-10-06, before this run.

      exemption  -- ISO 11898-1 stops an error-passive transmitter from
                    incrementing TEC on an acknowledgement error when it sees
                    no dominant bit during its own passive error flag. The
                    increment has STOPPED, so the plateau should not move when
                    occupancy rises.
      equilibrium -- TEC rises 8 per transmit error and falls 1 per success,
                    so any error rate matched by a 1:8 success rate parks TEC
                    at a stable value with no exemption involved. Successes
                    get rarer as occupancy rises, so the plateau should CLIMB
                    rung by rung.

    Both read as "TEC sat around 128" if only the peak is kept. 128 is also
    the error-passive threshold itself, so a value near it may be a boundary
    rather than a plateau at all.

    This refuses to choose on fewer than two rungs that reached a plateau,
    rather than returning the only candidate -- a selection among one is not
    evidence (AGENTS.md, "A discriminator that cannot fail").
    """
    #
    # A RUN THAT REACHED BUS-OFF HAS NO PLATEAU TO DISCRIMINATE. The 2026-10-06
    # run tripped this: the ladder broke out at bus-off with peaks 103 and 201,
    # and this function dutifully reported "climbs with occupancy, therefore
    # equilibrium" -- a mechanism claim derived from two points, one of which
    # was a pre-bus-off peak rather than any sort of plateau, against
    # occupancies that were both recorded as 0.0 because of the witness defect
    # above. Three independent reasons the answer was meaningless, and it was
    # printed as a finding. Refuse instead.
    #
    if any(r.get("tec", {}).get("busoff_seen") for r in rungs):
        return {"verdict": "not_applicable_busoff_reached",
                "why": "TEC crossed into bus-off, so no rung plateaued and "
                       "there is no plateau to attribute to a mechanism"}
    pts = [(r.get("occupancy_achieved"), r["tec"]["tec_peak"])
           for r in rungs
           if r.get("tec", {}).get("tec_peak") is not None]
    pts = [(o, t) for o, t in pts if t and t > 0]
    occs = {o for o, _ in pts}
    if len(occs) < len(pts):
        return {"verdict": "undetermined_occupancies_not_distinct",
                "why": "two or more rungs report the same occupancy, so the "
                       "ladder did not vary the quantity the discriminator "
                       "turns on",
                "points": pts}
    if len(pts) < 2:
        return {"verdict": "undetermined_too_few_loaded_rungs",
                "why": "fewer than two rungs moved TEC at all, and a "
                       "discriminator scored on one candidate separates "
                       "nothing",
                "points": pts}
    peaks = [t for _, t in pts]
    spread = max(peaks) - min(peaks)
    rising = all(b >= a for a, b in zip(peaks, peaks[1:])) and peaks[-1] > peaks[0]
    out = {"points": pts, "spread": spread, "monotonic_rising": rising,
           "pin_tolerance": PIN_TOLERANCE}
    if spread <= PIN_TOLERANCE:
        out["verdict"] = "pinned"
        out["why"] = ("the plateau did not move across rungs (spread %d, "
                      "under one TEC increment), which is what the "
                      "acknowledgement-error exemption predicts and what "
                      "equilibrium does not" % spread)
    elif rising:
        out["verdict"] = "climbs_with_occupancy"
        out["why"] = ("the plateau rose monotonically with occupancy "
                      "(%s), which is equilibrium between +8 per error and "
                      "-1 per success, NOT the exemption"
                      % " -> ".join(str(p) for p in peaks))
    else:
        out["verdict"] = "moves_without_trend"
        out["why"] = ("the plateau moved (spread %d) but not monotonically "
                      "(%s), so neither mechanism is evidenced and the rungs "
                      "may not be comparable"
                      % (spread, " -> ".join(str(p) for p in peaks)))
    return out


def windows_in_order(rec):
    """(name, window) chronologically: baseline, each rung, recover.

    ORDER MATTERS AND GOT THIS WRONG ONCE. An earlier version listed the
    rungs first and the baseline after, so a scan for "did the device come
    back after bus-off" found the BASELINE's healthy samples sitting after
    the bus-off ones and reported a recovery that never happened. Each
    window's `t` restarts at 0, so position in this list is the only ordering
    there is -- which is the same trap as AGENTS.md's "file order is not time
    order", one level up.
    """
    out = []
    if rec.get("baseline"):
        out.append(("baseline", rec["baseline"]))
    for i, r in enumerate(rec.get("rungs", [])):
        out.append(("rung%d" % i, r))
    if rec.get("recover"):
        out.append(("recover", rec["recover"]))
    return out


def live_samples(rec):
    """Every sample from every window that caught the driver installed."""
    out = []
    for _, w in windows_in_order(rec):
        for s in w.get("samples", []):
            if s.get("snap") and s["snap"].get("drv_present"):
                out.append(s)
    return out


def judge(rec):
    """Score a finished record. Pure: reads `rec`, writes verdict fields.

    Module level and argument-free of the bench so a STORED run can be
    re-scored by this exact code rather than by a copy of it. The
    2026-10-06 run needed that: its measurements were sound and three
    separate defects in this scoring were not.
    """
    #
    # Occupancy is recomputed here rather than trusted from the rung, because
    # the first version of this script derived it from WITNESSED interferer
    # frames and the witness is structurally blind to this aggressor (500 kbit
    # listener, 250 kbit transmitter). Every rung of the 2026-10-06 run is
    # stored with occupancy_achieved 0.0 for that reason while demonstrably
    # jamming the bus into bus-off. Recomputing keeps a re-scored old record
    # and a fresh run on the same basis.
    #
    for r in rec.get("rungs", []):
        delivered = max(r.get("pcan_offered", 0)
                        - r.get("pcan_send_exceptions", 0), 0)
        secs = r.get("seconds_requested") or 1.0
        r["occupancy_achieved"] = round(
            (delivered * rec.get("air_us_per_frame", AIR_US) / 1e6) / secs, 4)
        r["occupancy_basis"] = ("offered minus queue-full exceptions; the "
                                "witness cannot decode a wrong-bitrate "
                                "aggressor so witnessed occupancy is "
                                "structurally 0 and is not used")
    # ---------------------------------------------------------------- verdict
    live = live_samples(rec)
    tec_vals = [s["snap"]["drv_tec"] for s in live
                if s["snap"].get("drv_tec") is not None]
    txf = [s["snap"]["drv_tx_failed"] for s in live
           if s["snap"].get("drv_tx_failed") is not None]
    states = [s["snap"]["drv_state"] for s in live if s["snap"].get("drv_state")]
    refused = [s["snap"]["sched_tx_refused"] for s in live
               if s["snap"].get("sched_tx_refused") is not None]

    peak_tec = max(tec_vals) if tec_vals else None
    busoff = "bus_off" in states
    txf_moved = bool(txf) and (max(txf) > min(txf))
    refused_moved = bool(refused) and (max(refused) > min(refused))
    best_occ = max((r["occupancy_achieved"] for r in rec["rungs"]), default=0.0)
    rec["plateau"] = plateau_discriminator(rec["rungs"])

    # What the device did WHILE in bus-off. The question "did tx_failed move"
    # is only meaningful against evidence that a transmit was outstanding.
    bo_samples = [s for s in live if s["snap"].get("drv_state") == "bus_off"]
    mt = [s["snap"].get("drv_msgs_to_tx") for s in bo_samples
          if s["snap"].get("drv_msgs_to_tx") is not None]
    st = [s["snap"].get("sent_telemetry") for s in bo_samples
          if s["snap"].get("sent_telemetry") is not None]
    #
    # Duration is summed PER WINDOW from that window's own `t`, because every
    # window restarts its clock at 0 and multiplying a sample count by the
    # nominal poll interval ignores page-fetch latency. The first version did
    # that and reported 30.5 s for what was 43 s of bus-off.
    #
    bo_seconds = 0.0
    bo_windows = []
    for name, w in windows_in_order(rec):
        ts = [s["t"] for s in w.get("samples", [])
              if s.get("snap") and s["snap"].get("drv_state") == "bus_off"]
        if ts:
            bo_seconds += (max(ts) - min(ts))
            bo_windows.append({"window": name, "first_t": min(ts),
                               "last_t": max(ts), "samples": len(ts)})
    # Did it ever come back? Only windows AFTER the first bus-off count, and
    # `windows_in_order` is what makes "after" mean anything.
    seen_bo = False
    came_back = False
    for _, w in windows_in_order(rec):
        for s in w.get("samples", []):
            sn = s.get("snap") or {}
            if sn.get("drv_state") == "bus_off":
                seen_bo = True
            elif seen_bo and sn.get("drv_state") in ("running", "recovering"):
                came_back = True
    rec["busoff_detail"] = {
        "samples_in_busoff": len(bo_samples),
        "seconds_in_busoff": round(bo_seconds, 1),
        "windows": bo_windows,
        "msgs_to_tx_first": mt[0] if mt else None,
        "msgs_to_tx_last": mt[-1] if mt else None,
        "msgs_to_tx_values": sorted(set(mt)),
        # A non-zero queue that never changes is a frame that neither
        # completed nor failed.
        "queue_pinned": bool(mt) and len(set(mt)) == 1 and mt[0] > 0,
        "sent_tel_first": st[0] if st else None,
        "sent_tel_last": st[-1] if st else None,
        "sent_telemetry_advanced": bool(st) and st[-1] > st[0],
        "recovered_before_run_ended": came_back if bo_samples else None,
    }

    rec["summary"] = {
        "peak_tec": peak_tec,
        "peak_rec": max((s["snap"]["drv_rec"] for s in live
                         if s["snap"].get("drv_rec") is not None), default=None),
        "states_seen": sorted(set(states)),
        "busoff_seen": busoff,
        "tx_failed_moved": txf_moved,
        "tx_refused_moved": refused_moved,
        "best_occupancy_achieved": best_occ,
        "rungs_run": len(rec["rungs"]),
    }

    print("\n" + "=" * 74)
    print("peak TEC %s   peak REC %s   states %s   best occupancy %.0f %%"
          % (peak_tec, rec["summary"]["peak_rec"],
             ",".join(rec["summary"]["states_seen"]) or "-", best_occ * 100))

    if peak_tec is None:
        rec["verdict"] = "inconclusive_no_tec_readings"
        print("INCONCLUSIVE: no sample carried a TEC reading, so nothing was "
              "measured. Check the image and the polling path.")
    elif busoff and txf_moved:
        rec["verdict"] = "busoff_raises_tx_failed"
        print("ANSWERED: the controller reached bus-off AND tx_failed moved. "
              "Spec 7 trip 7's failed-transmit half has a reachable path on "
              "this silicon.")
    elif busoff and not txf_moved:
        #
        # "tx_failed did not move" is NOT by itself evidence that a failed
        # transmit goes unreported. It is only that if a transmit was
        # ATTEMPTED and neither completed nor failed. If the scheduler stopped
        # attempting, there was nothing to report and the silence says nothing
        # about the alert. Separate the two before saying anything -- this is
        # the "establish the population is non-empty" rule from AGENTS.md
        # applied to an absence.
        #
        bo = rec["busoff_detail"]
        rec["verdict"] = ("busoff_frame_stuck_neither_sent_nor_failed"
                          if bo["queue_pinned"]
                          else "busoff_without_tx_failed_no_attempt")
        print("ANSWERED: the controller reached bus-off and tx_failed did NOT "
              "move.")
        print("  during bus-off: msgs_to_tx %s -> %s, sent_telemetry %s -> "
              "%s over %.0f s"
              % (bo["msgs_to_tx_first"], bo["msgs_to_tx_last"],
                 bo["sent_tel_first"], bo["sent_tel_last"],
                 bo["seconds_in_busoff"]))
        if bo["queue_pinned"]:
            print("  A FRAME IS STUCK. msgs_to_tx held at %s for the whole "
                  "bus-off period and sent_telemetry never advanced, so the "
                  "queued frame neither completed nor failed. NEITHER half of "
                  "spec 7 trip 7 fires: tx_failed stays 0 because nothing is "
                  "reported failed, and tx_refused stays 0 because the "
                  "scheduler never gets the frame back to re-attempt it and "
                  "so never calls twai_transmit() again to be refused."
                  % bo["msgs_to_tx_last"])
            print("  The device is silent on the bus and the status page "
                  "still reports bus_on true, because bus_on is "
                  "can_is_enabled() -- driver installed, not bus-on.")
        else:
            print("  The queue drained, so the frame did go somewhere without "
                  "being counted failed. tx_refused %s."
                  % ("did move" if refused_moved else "did NOT move either"))
    elif peak_tec >= TEC_ERROR_PASSIVE:
        #
        # DO NOT NAME A MECHANISM HERE. A plateau near 128 is consistent with
        # the ACK exemption AND with a +8/-1 equilibrium, and 128 is also the
        # error-passive boundary itself. The ladder is what separates them,
        # and plateau_discriminator() is the only thing entitled to say which.
        #
        pl = rec["plateau"]
        rec["verdict"] = "error_passive_no_busoff_" + pl["verdict"]
        print("TEC reached %d -- past error-passive (%d), short of bus-off "
              "(%d), at up to %.0f %% occupancy."
              % (peak_tec, TEC_ERROR_PASSIVE, TEC_BUS_OFF, best_occ * 100))
        print("  plateau test: %s" % pl["verdict"])
        print("  %s" % pl["why"])
        if pl["verdict"] == "pinned":
            print("  A hard pin is the acknowledgement-error exemption and "
                  "not equilibrium. Note the exemption's textbook "
                  "precondition is a transmitter effectively alone on the "
                  "segment, and this one is not -- the interposer board is "
                  "on the same wire -- so a pin here wants explaining rather "
                  "than citing.")
    elif peak_tec > 0:
        rec["verdict"] = "tec_moved_but_stayed_error_active"
        print("INCONCLUSIVE: TEC moved (peak %d) but never reached "
              "error-passive. The interferer is colliding with some device "
              "transmissions and not enough of them. Try more rungs above "
              "%.0f %%, or a longer step." % (peak_tec, best_occ * 100))
    elif best_occ < 0.50:
        rec["verdict"] = "inconclusive_stimulus_too_sparse"
        print("INCONCLUSIVE, and it is the HARNESS: TEC never left 0 and the "
              "best rung only reached %.0f %% occupancy. The interferer was "
              "not there, so this says nothing about the device."
              % (best_occ * 100))
    else:
        rec["verdict"] = "tec_never_moved_at_full_occupancy"
        print("INCONCLUSIVE, and this one is interesting: the interferer "
              "reached %.0f %% occupancy and TEC never left 0. Either the "
              "device's few transmissions are threading the gaps, or they are "
              "failing in a way that does not charge TEC. Look at whether the "
              "WiCAN diag frames kept reaching the witness."
              % (best_occ * 100))



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-seconds", type=float, default=10.0)
    ap.add_argument("--step-seconds", type=float, default=25.0)
    ap.add_argument("--recover-seconds", type=float, default=15.0)
    ap.add_argument("--ladder", default="0.20,0.45,0.70,0.90",
                    help="interferer wire occupancies to walk, comma "
                         "separated (default 0.20,0.45,0.70,0.90)")
    ap.add_argument("--settle-seconds", type=float, default=3.0,
                    help="quiet gap between rungs, so TEC can be seen to "
                         "decay or not")
    ap.add_argument("--dry-run", action="store_true",
                    help="check preconditions and exit; transmits nothing")
    ap.add_argument("--out", default=None,
                    help="run record (default: a new timestamped file under "
                         "runs/; an existing file is never overwritten)")
    ap.add_argument("--rejudge", metavar="RUN.json",
                    help="re-score a stored run with the CURRENT judge() and "
                         "file the result beside the original verdict rather "
                         "than over it; transmits nothing")
    args = ap.parse_args()

    if args.rejudge:
        #
        # The original verdict is never overwritten. A run's verdict is part
        # of its record -- what was concluded at the time, by the code of the
        # time. A later re-score is a separate, dated claim, and conflating
        # the two makes the record unable to show that the scoring changed.
        # (Standard set by the reviewing session, 2026-10-06, for the probe.)
        #
        with open(args.rejudge, encoding="ascii", errors="replace") as fh:
            stored = json.load(fh)
        original = {k: stored.get(k) for k in
                    ("verdict", "summary", "plateau", "busoff_detail")}
        judge(stored)
        stored["rejudged"] = {
            "when": time.strftime("%Y-%m-%d"),
            "by": "tec_busoff_sweep.py --rejudge, same judge() the bench uses",
            "original": original,
            "verdict": stored["verdict"],
            "summary": stored["summary"],
            "plateau": stored["plateau"],
            "busoff_detail": stored.get("busoff_detail"),
        }
        for k, v in original.items():
            if v is not None:
                stored[k] = v
            else:
                stored.pop(k, None)
        with open(args.rejudge, "w", encoding="ascii", errors="replace") as fh:
            json.dump(stored, fh, indent=2, sort_keys=True)
        print("\nre-scored %s" % args.rejudge)
        print("  original verdict: %s" % original["verdict"])
        print("  re-scored:        %s" % stored["rejudged"]["verdict"])
        return 0

    if args.out is None:
        args.out = os.path.join(RUNS, "tec_busoff_sweep_%s.json"
                                % time.strftime("%Y%m%dT%H%M%S"))
    if os.path.exists(args.out):
        print("FAIL: %s exists; a run record is never overwritten" % args.out)
        return 1
    print("record will be written to %s" % args.out)

    ladder = [float(x) for x in args.ladder.split(",") if x.strip()]
    if not ladder:
        print("FAIL: empty ladder")
        return 1

    rec = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "decoder": gi_diag.run_identity(),
        "question": "can this bench drive the C3 to bus-off, and does bus-off "
                    "raise TWAI_ALERT_TX_FAILED",
        "supersedes": "the interference half of txfailed_busoff_probe.py "
                      "(run 2026-10-05, inconclusive_busoff_not_reached)",
        "nominal_bitrate": NOMINAL,
        "wrong_bitrate": WRONG,
        "ladder_requested": ladder,
        "air_us_per_frame": AIR_US,
        "rungs": [],
    }

    # Wall clock tied to the monotonic clock once, so a witness-derived stop
    # (which is a perf_counter value) can be reported as a time the other
    # session can align its own 5 s status prints against.
    perf_ref = time.perf_counter()
    wall_ref = time.time()

    def wall(perf_t):
        if perf_t is None:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(wall_ref + (perf_t - perf_ref)))

    print("=" * 74)
    print("TEC-tracked bus-off sweep. Device to PASSIVE; a %d kbit interferer"
          % (WRONG // 1000))
    print("walks a ladder of wire occupancies while TEC is read directly.")
    print("Shared vehicle segment -- the interposer board is on this wire and")
    print("its error counters will move. Burst windows are recorded.")
    print("=" * 74)

    # ------------------------------------------------- precondition: the page
    #
    # REFUSE ON AN OLD IMAGE. Without `tec` on the page this script measures
    # nothing and would record a column of nulls that reads like a device that
    # never errored. That is the same shape as the 2026-10-05 null-snapshot
    # defect: a harness fault indistinguishable from a device result.
    #
    pre = page()
    if pre is None:
        print("FAIL: device did not answer /gen_inhibit")
        return 1
    rec["build"] = build_id()
    rec["pre"] = snap(pre)
    print("\npre: mode %s  bus_on %s  build %s"
          % (pre.get("mode"), pre.get("bus_on"),
             (rec["build"] or {}).get("git_version")))
    if not rec["build"] or not rec["build"].get("git_version"):
        print("FAIL: could not read the running build from /check_status. A "
              "run that cannot name its image is not evidence about an image.")
        return 1

    if pre.get("mode") != MODE_OFF:
        print("FAIL: device is in mode %s, not OFF. Put it in a known state "
              "deliberately rather than having this script do it."
              % pre.get("mode"))
        return 1

    kv = None
    tx = None
    sender = None
    try:
        # Witness up and acknowledging BEFORE the device transmits.
        kv = can.Bus(interface="kvaser", channel=0, bitrate=NOMINAL)
        time.sleep(2.0)
        while kv.recv(timeout=0.0) is not None:
            pass
        rec["acknowledger_up"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        print("Kvaser up and acknowledging at %s" % rec["acknowledger_up"])

        if not set_mode(MODE_PASSIVE):
            print("FAIL: could not set mode %d" % MODE_PASSIVE)
            return 1
        time.sleep(2.0)
        p = page()
        rec["after_mode_set"] = snap(p)
        if not p or p.get("mode") != MODE_PASSIVE:
            print("FAIL: device did not enter PASSIVE")
            return 1
        drv = p.get("drv") or {}
        if drv.get("tec") is None:
            rec["verdict"] = "inconclusive_image_lacks_tec"
            print("\nFAIL: the status page carries no drv.tec, so this image "
                  "predates the 2026-10-06 spec 11 addition. Every TEC "
                  "reading would be null and the run would look like a "
                  "device that never errored. Flash a build that reports it.")
            return 1
        print("in PASSIVE: state %s  tec %s  rec %s"
              % (drv.get("state"), drv.get("tec"), drv.get("rec")))

        if args.dry_run:
            rec["verdict"] = "dry_run_preconditions_only"
            print("\nDRY RUN: preconditions hold, nothing transmitted.")
            return 0

        # --------------------------------------------------------- baseline
        print("\n[0] baseline %.0f s, no interference" % args.baseline_seconds)
        b_counts = collections.Counter()
        b_errs, b_samp, _, _ = drain_and_poll(kv, args.baseline_seconds,
                                              b_counts)
        b_wican = diag_total(b_counts, WICAN_DIAG)
        rec["baseline"] = {
            "seconds": args.baseline_seconds,
            "wican_diag": b_wican,
            "board_diag": diag_total(b_counts, BOARD_DIAG),
            "error_frames": b_errs,
            "by_id": {"0x%03X" % i: n for i, n in sorted(b_counts.items())},
            "tec": tec_track(b_samp),
            "samples": b_samp,
        }
        print("    WiCAN diag %d   board diag %d   error frames %d   "
              "tec peak %s"
              % (b_wican, diag_total(b_counts, BOARD_DIAG), b_errs,
                 rec["baseline"]["tec"]["tec_peak"]))

        if b_wican < MIN_BASELINE_DIAG:
            rec["verdict"] = "inconclusive_device_not_transmitting"
            print("\nINCONCLUSIVE: the device emitted only %d diag frames in "
                  "the baseline (need %d). With no transmissions there are no "
                  "transmit errors, TEC cannot move, and a flat TEC would say "
                  "nothing about the device." % (b_wican, MIN_BASELINE_DIAG))
            return 1

        # ------------------------------------------------------- the ladder
        tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=WRONG,
                     auto_reset=True)

        for occ in ladder:
            period = (AIR_US / 1e6) / occ
            print("\n[rung %.2f] target occupancy %.0f %%, period %.0f us, "
                  "%.0f s" % (occ, occ * 100, period * 1e6, args.step_seconds))
            counts = collections.Counter()
            t_origin = time.perf_counter()
            burst_start = time.strftime("%Y-%m-%dT%H:%M:%S")
            sender = Sender(tx, period)
            sender.start()

            errs, samples, last_intf, last_any = drain_and_poll(
                kv, args.step_seconds, counts, t_origin=t_origin)

            sender.stop_flag.set()
            sender.join(timeout=5.0)
            host_stop = time.perf_counter()
            sent, send_errs = sender.sent, sender.errors
            sender = None

            # THE WIRE STOP, not the host stop. Keep draining until the
            # interferer has actually gone quiet, and record both so the gap
            # between them is visible rather than assumed away.
            tail_counts = collections.Counter()
            tail_errs, tail_samples, tail_intf, _ = drain_and_poll(
                kv, args.settle_seconds, tail_counts, t_origin=t_origin)
            wire_last = tail_intf or last_intf

            #
            # THE WITNESS CANNOT SEE THIS AGGRESSOR, and that is not a fault
            # -- it is the whole point of the stimulus. The Kvaser is at
            # 500 kbit and the interferer transmits at 250 kbit, so its frames
            # are not decodable and arrive as ERROR FRAMES, not as 0x123.
            # Measured 2026-10-06: 10,840 frames offered, 0 witnessed, 8,201
            # error frames.
            #
            # So the project's standing rule -- occupancy from witnessed
            # frames, never from offered ones, because send() returns at
            # queueing -- CANNOT BE APPLIED HERE. The first version of this
            # script applied it anyway and reported 0 % occupancy for a rung
            # that was demonstrably jamming the bus into bus-off. Had TEC not
            # moved, the verdict would have blamed the harness on the strength
            # of a number that was structurally incapable of being non-zero.
            #
            # What is used instead, and why it is defensible here: the sender
            # is PACED, not free-running, and the queue-full exception count
            # is the check on backlog. 11 exceptions in 10,840 means the
            # offered rate and the wire rate cannot have diverged materially.
            # A run with a large exception count must NOT trust this figure.
            # Error frames at the witness corroborate that the aggressor was
            # present, without quantifying it.
            #
            seen = counts[INTERFERE_ID] + tail_counts[INTERFERE_ID]
            span = ((wire_last - t_origin) if wire_last
                    else args.step_seconds)
            delivered = max(sent - send_errs, 0)
            achieved = (delivered * AIR_US / 1e6) / max(args.step_seconds, 1e-9)

            allsamp = samples + tail_samples
            tr = tec_track(allsamp)
            rung = {
                "occupancy_target": occ,
                "period_s": period,
                "seconds_requested": args.step_seconds,
                "burst_start": burst_start,
                "pcan_offered": sent,
                "pcan_send_exceptions": send_errs,
                "interferer_frames_witnessed": seen,
                "witness_can_see_interferer": False,
                "occupancy_basis": "offered minus queue-full exceptions; the "
                                   "500 kbit witness cannot decode a 250 kbit "
                                   "aggressor, so witnessed-frame occupancy "
                                   "is structurally 0 and must not be used",
                "witness_span_s": round(span, 3),
                # For the interposer session's per-rung alignment: the wire
                # stop is the LAST INTERFERER FRAME THE WITNESS SAW, not the
                # moment the host stopped calling send().
                "burst_stop_wire": wall(wire_last),
                "burst_stop_host": wall(host_stop),
                "occupancy_achieved": round(achieved, 4),
                "host_stop_minus_wire_stop_s": (
                    round(host_stop - wire_last, 3) if wire_last else None),
                "wican_diag": diag_total(counts, WICAN_DIAG)
                              + diag_total(tail_counts, WICAN_DIAG),
                "board_diag": diag_total(counts, BOARD_DIAG)
                              + diag_total(tail_counts, BOARD_DIAG),
                "error_frames": errs + tail_errs,
                "tec": tr,
                "samples": allsamp,
            }
            rec["rungs"].append(rung)

            print("    offered %d (exc %d)   witnessed %d   occupancy %.0f %%"
                  % (sent, send_errs, seen, achieved * 100))
            print("    tec peak %s final %s   rec peak %s   states %s"
                  % (tr["tec_peak"], tr["tec_final"], tr["rec_peak"],
                     ",".join(tr["states_seen"]) or "-"))
            print("    WiCAN diag %d   error frames %d   host stop led the "
                  "wire by %s s"
                  % (rung["wican_diag"], rung["error_frames"],
                     rung["host_stop_minus_wire_stop_s"]))

            if tr["busoff_seen"]:
                print("    BUS-OFF REACHED at this rung. Stopping the ladder; "
                      "the remaining rungs would add nothing.")
                break

        try:
            tx.shutdown()
        except Exception:
            pass
        tx = None

        # --------------------------------------------------------- recovery
        print("\n[r] recovery %.0f s, interference removed"
              % args.recover_seconds)
        time.sleep(1.0)
        while kv.recv(timeout=0.0) is not None:
            pass
        r_counts = collections.Counter()
        r_errs, r_samp, _, _ = drain_and_poll(kv, args.recover_seconds,
                                              r_counts)
        rec["recover"] = {
            "seconds": args.recover_seconds,
            "wican_diag": diag_total(r_counts, WICAN_DIAG),
            "board_diag": diag_total(r_counts, BOARD_DIAG),
            "error_frames": r_errs,
            "by_id": {"0x%03X" % i: n for i, n in sorted(r_counts.items())},
            "tec": tec_track(r_samp),
            "samples": r_samp,
        }
        print("    WiCAN diag %d   error frames %d   tec peak %s final %s"
              % (rec["recover"]["wican_diag"], r_errs,
                 rec["recover"]["tec"]["tec_peak"],
                 rec["recover"]["tec"]["tec_final"]))

        # ------------------------------------------------------ reinstall
        #
        # Added 2026-10-07 (gen-inhibit tester, spec 11 bus_on). The device
        # does not leave bus-off by itself, so the only way back to RUNNING is
        # a mode change, which reinstalls the driver. OFF first (drv null,
        # bus_on false), then PASSIVE again with the witness capturing every
        # 0x7F1, so a page built before the reinstall and sent after it would
        # be seen (the reviewer's item 3: reported, not failed).
        #
        print("\n[p] reinstall: OFF, then PASSIVE again")
        set_mode(MODE_OFF)
        o_counts = collections.Counter()
        _, o_samp, _, _ = drain_and_poll(kv, 2.0, o_counts)
        set_mode(MODE_PASSIVE)
        p_counts = collections.Counter()
        p_errs, p_samp, _, _ = drain_and_poll(kv, 6.0, p_counts)
        rec["reinstall"] = {
            "off_samples": o_samp,
            "passive_samples": p_samp,
            "passive_wican_diag": diag_total(p_counts, WICAN_DIAG),
            "passive_error_frames": p_errs,
        }
        print("    states after reinstall: %s   WiCAN diag %d"
              % (sorted({(x["snap"] or {}).get("drv_state") for x in p_samp
                         if x["snap"]} - {None}),
                 rec["reinstall"]["passive_wican_diag"]))
    finally:
        if sender is not None:
            sender.stop_flag.set()
            sender.join(timeout=5.0)
        for b in (tx, kv):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:
                    pass
        set_mode(MODE_OFF)
        time.sleep(1.0)
        # Taken for the record only. `drv` is null here because the driver is
        # uninstalled by mode 0 -- do NOT difference anything against it. That
        # mistake voided the arithmetic of the 2026-10-05 run.
        rec["post_after_mode_off"] = snap(page())
        print("\nrestored mode 0")

    judge(rec)
    rec["wire_0x7F1"] = list(WIRE_7F1)
    rec["bus_on_check"] = judge_bus_on(rec)
    bc = rec["bus_on_check"]
    print("\nspec 11 bus_on: %s -- %s" % (bc["verdict"], bc["summary"]))

    os.makedirs(RUNS, exist_ok=True)
    with open(args.out, "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print("wrote %s" % args.out)
    return 0


def all_samples(rec):
    """Every timestamped page sample in the record, in time order."""
    out = list((rec.get("baseline") or {}).get("samples") or [])
    for r in rec.get("rungs") or []:
        out += r.get("samples") or []
    out += (rec.get("recover") or {}).get("samples") or []
    ri = rec.get("reinstall") or {}
    out += (ri.get("off_samples") or []) + (ri.get("passive_samples") or [])
    return sorted((x for x in out if x.get("pt") is not None),
                  key=lambda x: x["pt"])


def judge_bus_on(rec):
    """Spec 10/11 bus_on, as the reviewer ruled 2026-10-07.

    PAGE (pass/fail): bus_on is true exactly when drv.state is "running";
    false for every other state; false when drv is null (driver not
    installed). Checked on every sample, plus the untimed pre / after-mode /
    post snapshots.
    WIRE (reported): every 0x7F1 is expected to carry bit1 set, because a
    controller that is not running transmits nothing. A bit-1-clear frame is
    NOT a failure under the spec; it is listed with its time relative to the
    bus-off interval. The 0x7F1 gap across bus-off is reported as "not
    observable on the wire by construction", with its length.
    INCONCLUSIVE if no sample read bus_off: then the half of the check this
    run exists for was never exercised, and a clean page column would be a
    check that could not fail.
    """
    timed = all_samples(rec)
    snaps = [x["snap"] for x in timed if x.get("snap")]
    for k in ("pre", "after_mode_set", "post_after_mode_off"):
        if rec.get(k):
            snaps.append(rec[k])
    bad, states = [], {}
    for sn in snaps:
        st = sn.get("drv_state") if sn.get("drv_present") else None
        key = st if st is not None else "drv_null"
        states[key] = states.get(key, 0) + 1
        want = (st == "running")
        if sn.get("bus_on") is not want:
            bad.append({"drv_state": key, "bus_on": sn.get("bus_on")})
    off_pts = [x["pt"] for x in timed if x.get("snap")
               and x["snap"].get("drv_state") == "bus_off"]
    wire = rec.get("wire_0x7F1") or []
    clear = []
    # bus_on is diag_flags bit1, decoded by gi_diag (each 0x7F1 carries its schema byte;
    # reading Q7, 2026-10-24: byte 3 by hand before). An unknown schema raises.
    pages, _dec = gi_diag.decode_pages([(pt, 0x7F1, hx) for pt, hx in wire], identity=rec.get("decoder"),
                                       decode_choices=False)
    for (pt, _i, pg), (_pt, hx) in zip(pages, sorted(wire, key=lambda w: w[0])):
        if isinstance(pg, gi_diag.Undecodable):
            clear.append({"pt": pt, "data": hx, "undecodable": pg.reason})
        elif not int(pg["diag_flags"]) & 0x02:
            clear.append({"pt": pt, "data": hx})
    out = {"page_samples": len(snaps), "page_states": states,
           "page_mismatches": bad[:20], "page_mismatch_count": len(bad),
           "wire_0x7F1": len(wire), "wire_bit1_clear": clear,
           "wire_gap_across_busoff_s": None}
    if off_pts:
        b0, b1 = min(off_pts), max(off_pts)
        inside = [pt for pt, _ in wire if b0 <= pt <= b1]
        before = [pt for pt, _ in wire if pt < b0]
        after = [pt for pt, _ in wire if pt > b1]
        out["busoff_sampled_s"] = round(b1 - b0, 2)
        out["wire_0x7F1_inside_busoff"] = len(inside)
        out["wire_gap_across_busoff_s"] = (
            round(min(after) - max(before), 2) if before and after else None)
        for c in clear:
            c["rel_busoff_first_s"] = round(c["pt"] - b0, 3)
            c["rel_busoff_last_s"] = round(c["pt"] - b1, 3)
    if bad:
        out["verdict"] = "FAIL"
        out["summary"] = ("%d of %d page readings disagree with drv.state "
                          "(states %s)" % (len(bad), len(snaps), states))
    elif not off_pts:
        out["verdict"] = "INCONCLUSIVE"
        out["summary"] = ("no sample read bus_off, so the bus-off half was "
                          "not exercised (states %s)" % states)
    else:
        out["verdict"] = "PASS"
        out["summary"] = ("page agrees with drv.state on all %d readings "
                          "(states %s); wire 0x7F1 %d, bit1 clear on %d; gap "
                          "across bus-off %s s, not observable on the wire by "
                          "construction" % (len(snaps), states, len(wire),
                                            len(clear),
                                            out["wire_gap_across_busoff_s"]))
    return out


if __name__ == "__main__":
    sys.exit(main())
