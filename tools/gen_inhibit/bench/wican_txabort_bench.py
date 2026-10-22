#!/usr/bin/env python3
"""What does the C3's TWAI abort command actually DO? Spec 5.2 item 9.

    py -3.11 tools/gen_inhibit/bench/wican_txabort_bench.py

Needs the GI_INSTRUMENT_TXABORT measurement build on the device (build-txabort).
The shipping image has no /gi_txabort endpoint and E3 refuses any image that does.

WHY IT EXISTS. Spec 5.2 specifies a transmit scheduler that preempts a telemetry
frame by aborting it, and item 9 requires the hardware's behaviour to be measured
BEFORE the scheduler is built on it and before the emulator models it. Four
questions, in the spec's words: does an abort remove a frame awaiting
arbitration; is an abort during transmission a no-op with the frame completing;
what does the driver then report (alerts, `msgs_to_tx`, its internal count); and
how long does the buffer take to become free.

TWO PREDICTIONS, RECORDED BEFORE THE RUN so the measurement can refute them
rather than be read as confirming whatever it shows. Both come from
`twai_ll_set_cmd_abort_tx()` in ESP-IDF v5.4.1, which is documented as aborting
frames awaiting TX, leaving a frame already being transmitted alone, setting the
Transmission Complete Status bit AUTOMATICALLY, and not retrying on error:

  1. The driver's ISR decides TX_SUCCESS versus TX_FAILED from exactly that TCS
     bit, so an abort should be reported as a SUCCESSFUL transmission of a frame
     that never reached the wire. If that holds, the scheduler can never take a
     completion at face value -- which is what spec 5.2 item 5 exists for.
  2. "Will not automatically retry" means an abort latched while the frame is in
     its ARBITRATION field, where the frame then loses, should drop it with no
     retry. So "the controller was transmitting" and "the frame never reached the
     wire" can both be true at once. That is a legitimate fourth outcome, not the
     instrument disagreeing with the witness, and `fake_twai` will have to model
     it. Raised by the reviewing session, 2026-09-27.

THE DEVICE CANNOT BE ITS OWN WITNESS HERE. Whether a frame reached the wire is
precisely what is in question, and the device's own report is the thing under
test. So every trial frame carries its trial number in the payload (bytes 0-1,
little-endian, with the tag AB 7E in bytes 2-3; the follow-up transmit carries
AB 7F) and an independent adapter decides whether that number appeared. A trial
is joined to its witness row by that number and never by timing or order.

    PCAN     the arbitration flood, and an ACKing node
    Kvaser   the witness, hardware timestamps
    IXXAT    a second witness, optional (--no-ixxat to skip)

WHY THE PCAN FLOODS AND NOT THE IXXAT. `e4_rig_calibration` has the IXXAT
delivering 47,012 / 0 / 4,860 of 70,936 frames in three sustained arms, while the
PCAN sustained 2364.6 fps for 30 s with 0 refused. The IXXAT stays a witness.

AND WHY THE FLOOD IS NOT THE MECHANISM. Saturating 500 kbit/s with DLC-8 frames
takes roughly 3700-4300 frames a second, and nothing measured on this bench
establishes that the PCAN reaches it. The 2364.6 fps figure is the rate of a
*paced replay at a capture's recorded timing*, not the adapter's ceiling, so it
bounds neither direction -- which is precisely why bus load cannot be the thing
the case rests on. The `held` phase uses the firmware's RS gate instead: submit on
the RS 0 -> 1 edge, the START of another node's frame, so ours cannot begin before
that frame's EOF and the intermission. "Still awaiting at the abort" then follows
from CAN framing rather than from hitting a window, and the flood only has to be
dense enough for edges to come along. The reviewing session caught both errors,
2026-09-27: an earlier version of this script would have read "was pending" off
`arb_lost` after the fact, and the first gate tested the RS *level*, which passes
at the tail of a frame and would have mislabelled those trials as gated.

AT LEAST ONE NODE MUST ACK. With no acknowledging node the controller treats
every frame as a failed transmission and retransmits it, and then `free_us`,
`mtx0_us` and the alert word all describe retransmission rather than abort. The
adapters are opened in normal mode for that reason, and phase `quiet` records
what is actually on the segment before anything else runs.

PCAN IS OPENED FIRST, always. A bus already open while another is still opening
buffers traffic throughout and then delivers the backlog in a burst, which two
sessions independently mistook for the Kvaser duplicating frames (see
`diag_duplicate_crosscheck.py`). Open order, then a 2 s settle and drain.

THE RUN

    quiet    Nothing transmits. Every ID on the segment, so "the bus was idle" is
             a measurement rather than an assumption.
    control  phase=control. Abort an empty buffer.
    noabort  phase=noabort on an idle bus. A genuine completion's signature.
    idle     phase=abort, gate=off, sweep 0..400 us on an idle bus. The
             during-transmission case and the abort-versus-completion race.
    held     phase=abort, gate=rs, short fixed delay, PCAN flooding. Guaranteed
             awaiting arbitration.
    contend  phase=abort, gate=off, short sweep, PCAN flooding. Submits land at
             random phases relative to the flood, which is what produces the
             arbitration-loss outcome of prediction 2.

Rows are classified by the ACHIEVED abort delay the device reports, never by the
requested one: a trial that was preempted landed at a different delay and is
still a valid point there, whereas filing it under the request would put it in a
bin it never occupied.
"""

import argparse
import collections
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import can
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HOST = "192.168.80.1"
BITRATE = 500000
SETTLE_S = 2.0

TRIAL_ID = 0x7FE            # matches TXAB_ID in main/gi_txabort_probe.c
TAG_HI = 0xAB
TAG_TRIAL = 0x7E
TAG_FOLLOW = 0x7F

# The arbitration flood. Numerically far below 0x7FE, so every one of these beats
# the trial frame, and clear of every ID gen_inhibit reads (0x050, 0x051, 0x054,
# 0x411, 0x440, 0x471, 0x592, 0x617, 0x639) so nothing in the firmware reacts to
# the flood itself.
FLOOD_IDS = (0x300, 0x301, 0x302)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()

# TWAI_ALERT_* bit values, checked against
# components/driver/twai/include/driver/twai.h in ESP-IDF v5.4.1. Spelled out
# because the answer to "which alert does an abort raise" has to be readable in
# the report rather than as a hex word.
ALERT_NAMES = (
    (0x00000001, "TX_IDLE"),
    (0x00000002, "TX_SUCCESS"),
    (0x00000004, "RX_DATA"),
    (0x00000008, "BELOW_ERR_WARN"),
    (0x00000010, "ERR_ACTIVE"),
    (0x00000020, "RECOVERY_IN_PROGRESS"),
    (0x00000040, "BUS_RECOVERED"),
    (0x00000080, "ARB_LOST"),
    (0x00000100, "ABOVE_ERR_WARN"),
    (0x00000200, "BUS_ERROR"),
    (0x00000400, "TX_FAILED"),
    (0x00000800, "RX_QUEUE_FULL"),
    (0x00001000, "ERR_PASS"),
    (0x00002000, "BUS_OFF"),
    (0x00004000, "RX_FIFO_OVERRUN"),
    (0x00008000, "TX_RETRIED"),
    (0x00010000, "PERIPH_RESET"),
)


def alert_words(bits):
    out = [name for bit, name in ALERT_NAMES if bits & bit]
    left = bits
    for bit, _n in ALERT_NAMES:
        left &= ~bit
    if left:
        out.append("unknown:0x%08X" % left)
    return out


# ------------------------------------------------------------------ device ----

def http(path, method="GET", timeout=120.0):
    req = urllib.request.Request("http://%s%s" % (HOST, path), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def gi_status():
    _s, b = http("/gen_inhibit")
    return json.loads(b)


def check_status():
    _s, b = http("/check_status")
    return json.loads(b)


# What the header calls each thing we can send, and how to compare it. The
# firmware echoes every one; anything it does not echo cannot be verified and so
# must not be sendable.
ECHO_KEYS = {
    "phase":  ("phase", str),
    "gate":   ("gate", str),
    "label":  ("label", str),
    "reps":   ("reps", int),
    "d0":     ("d0", int),
    "d1":     ("d1", int),
    "step":   ("step", int),
    "settle": ("settle_ms", int),
    "retry":  ("retry", str),
}


def verify_echo(sent, rec):
    """Every argument we sent must come back unchanged. Raise if not.

    THIS IS THE CHECK THAT CLOSES THE SILENT-DEFAULT CLASS, and it belongs here
    rather than in the firmware because only this side knows what was intended.
    A misspelled key is not found by the handler and the default runs; a clamp
    rewrites a value the caller chose. Both are invisible from inside the
    request and both are obvious from the echo.
    """
    bad = []
    if rec.get("query_given") is not True:
        bad.append("query_given=%r (the device parsed no arguments at all)"
                   % rec.get("query_given"))
    for key, val in sent.items():
        if key not in ECHO_KEYS:
            bad.append("%s: nothing in the header echoes this argument, so it "
                       "cannot be verified -- do not send what cannot be "
                       "checked" % key)
            continue
        hk, cast = ECHO_KEYS[key]
        if hk not in rec:
            bad.append("%s: header has no %r" % (key, hk))
            continue
        try:
            got = cast(rec[hk])
            want = cast(val)
        except (TypeError, ValueError):
            got, want = rec[hk], val
        if got != want:
            bad.append("%s: sent %r, device ran %r" % (key, want, got))
    # The sweep the device says it will run must follow from those numbers.
    try:
        steps = ((int(rec["d1"]) - int(rec["d0"])) // int(rec["step"])) + 1
        if int(rec["steps"]) != steps:
            bad.append("steps: header says %r, d0/d1/step imply %d"
                       % (rec["steps"], steps))
        if int(rec["n"]) != steps * int(rec["reps"]):
            bad.append("n: header says %r, steps x reps is %d"
                       % (rec["n"], steps * int(rec["reps"])))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        bad.append("could not check the sweep arithmetic (%s)" % e)
    if bad:
        raise SystemExit(
            "THE DEVICE DID NOT RUN WHAT IT WAS ASKED FOR -- aborting before "
            "any of these numbers are written down:\n  " + "\n  ".join(bad))


def run_probe(phase, **kw):
    sent = dict(kw)
    sent["phase"] = phase
    q = "&".join(["phase=%s" % phase] + ["%s=%s" % (k, v) for k, v in kw.items()])
    try:
        _s, b = http("/gi_txabort?" + q, method="POST", timeout=300.0)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit("probe refused phase=%s: HTTP %d %s"
                         % (phase, e.code, body))
    except urllib.error.URLError as e:
        raise SystemExit("probe unreachable for phase=%s (%s). Is the "
                         "GI_INSTRUMENT_TXABORT build flashed?" % (phase, e))
    try:
        rec = json.loads(b)
    except ValueError:
        raise SystemExit("probe answer for phase=%s is not JSON:\n%s"
                         % (phase, b[:400].decode("utf-8", "replace")))
    verify_echo(sent, rec)
    rec["sent"] = sent
    return rec


# ----------------------------------------------------------------- witness ----

class Witness(object):
    """Collect frames off one adapter in a thread, with its own timestamps.

    Kept as raw rows rather than pre-filtered: the error-frame count and the
    "what else was on the bus" survey both need everything, and a filter applied
    at capture time is a filter nobody can check afterwards.
    """

    def __init__(self, bus, name):
        self.bus = bus
        self.name = name
        self.rows = []
        self._stop = threading.Event()
        self._t = None

    def start(self):
        self._stop.clear()
        self.rows = []
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.2)
            except can.CanError:
                continue
            if m is None:
                continue
            self.rows.append((m.timestamp, m.arbitration_id,
                              bytes(m.data), bool(m.is_error_frame),
                              bool(m.is_extended_id)))

    def stop(self):
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=3.0)

    def errors(self):
        return sum(1 for r in self.rows if r[3])

    def fps(self):
        """Frames a second over the span this witness actually observed.

        From the witness's own timestamps rather than from the device's run
        length, because the capture brackets the run by ~1 s and dividing by the
        wrong span is how a rate check stops being a check.
        """
        if len(self.rows) < 2:
            return 0.0
        span = self.rows[-1][0] - self.rows[0][0]
        return (len(self.rows) - 1) / span if span > 0 else 0.0

    def trials(self, tag):
        """{trial number: [timestamps]} for frames carrying `tag`."""
        out = collections.defaultdict(list)
        for ts, ident, data, err, _ext in self.rows:
            if err or ident != TRIAL_ID or len(data) < 4:
                continue
            if data[2] != TAG_HI or data[3] != tag:
                continue
            out[data[0] | (data[1] << 8)].append(ts)
        return out

    def id_census(self):
        c = collections.Counter()
        for _ts, ident, _d, err, ext in self.rows:
            c["error" if err else ("%03X%s" % (ident, "x" if ext else ""))] += 1
        return dict(c.most_common(12))


class Flood(object):
    """Hold the bus with back-to-back higher-priority frames.

    Sends as fast as the adapter accepts, with no pacing, and reports the rate it
    ACHIEVED -- which is NOT assumed to saturate the bus and is not what makes any
    phase valid. Its only job is to make RS = 1 windows frequent, so the
    firmware's gate has something to gate on, and to create contention for the
    arbitration-loss outcome.
    """

    def __init__(self, bus):
        self.bus = bus
        self._stop = threading.Event()
        self._t = None
        self.sent = 0
        self.refused = 0
        self.seconds = 0.0

    def start(self):
        self._stop.clear()
        self.sent = 0
        self.refused = 0
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        msgs = [can.Message(arbitration_id=i, is_extended_id=False,
                            data=bytes([0x00] * 8))
                for i in FLOOD_IDS]
        t0 = time.perf_counter()
        k = 0
        while not self._stop.is_set():
            try:
                self.bus.send(msgs[k % len(msgs)], timeout=0.5)
                self.sent += 1
            except can.CanError:
                self.refused += 1
            k += 1
        self.seconds = time.perf_counter() - t0

    def stop(self):
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=5.0)


# ---------------------------------------------------------- classification ----

def classify(t, wire_any):
    """The outcome of one trial, from the device's status word and the wire.

    Deliberately NOT collapsed to three cases. TS = 1 covers the arbitration
    field as well as the data phase, so "was transmitting" does not imply "must
    have reached the wire", and the combination that looks like a contradiction
    is a real outcome with a name.
    """
    if t["skip"]:
        return "skipped: " + t["skip"]
    if t.get("noabort"):
        return "completed (no abort)" if wire_any else "NOT ON THE WIRE (no abort)"
    if t.get("control"):
        return "abort on empty buffer"
    if t["tbs_b"]:
        return ("buffer already free, frame had gone" if wire_any
                else "buffer already free, NOTHING ON THE WIRE")
    if not t["ts_b"]:
        return ("awaiting, aborted, not on the wire" if not wire_any
                else "awaiting at abort BUT REACHED THE WIRE")
    if wire_any:
        return "transmitting, abort was a no-op, completed"
    # TS = 1 and never on the wire. There are TWO of these and they are not the
    # same thing at all -- the difference is whether the frame LEFT the buffer.
    #
    # Measured 2026-09-27 on 78 contend trials: of 19 aborts that landed with
    # TS = 1, FOURTEEN left msgs_to_tx at 1 with the buffer never freeing and no
    # completion alert for the whole 20 ms window. The frame was in the
    # arbitration field, lost arbitration, and went back to contending with the
    # abort having had NO EFFECT. Calling that "dropped without retry" -- as the
    # first version of this function did -- gets it exactly backwards: nothing
    # was dropped, and the scheduler would be waiting for a completion that never
    # comes.
    left_buffer = (t["mtx_a"] == 0) or (t["mtx0_us"] >= 0)
    if not left_buffer:
        return ("ABORT HAD NO EFFECT: still in the buffer, no completion, "
                "not on the wire")
    if t["d_arb_lost"] or "ARB_LOST" in t.get("alert_names", ()):
        return "in arbitration, lost, then aborted out; reported as success"
    return "transmitting, NOT ON THE WIRE, no arb_lost -- UNEXPLAINED"


def join_phase(rec, wits):
    """Attach each witness's verdict to each device trial, by trial number."""
    seen = {w.name: w.trials(TAG_TRIAL) for w in wits}
    follow = {w.name: w.trials(TAG_FOLLOW) for w in wits}
    for t in rec["trials"]:
        n = t["t"]
        t["control"] = rec["phase"] == "control"
        t["noabort"] = rec["phase"] == "noabort"
        t["wire"] = {name: len(seen[name].get(n, ())) for name in seen}
        t["wire_follow"] = {name: len(follow[name].get(n, ())) for name in follow}
        # The masked word is the measurement; the full one is context. Keeping
        # both means an unexpected RX_QUEUE_FULL or BUS_OFF is still visible
        # instead of being masked out of existence.
        t["alert_names"] = alert_words(t["txalerts"])
        t["all_alert_names"] = alert_words(t["alerts"])
        t["outcome"] = classify(t, any(t["wire"].values()))
    return rec


def summarise(rec, wits):
    """Print the table, and flag anything unexplained rather than resolving it."""
    print("  phase=%s gate=%s label=%s  trials=%d  run=%.2f s  git=%s"
          % (rec["phase"], rec["gate"], rec["label"], rec["n"],
             rec["run_us"] / 1e6, rec["git"]))
    names = [w.name for w in wits]
    print("   trial  req  act gate edge  TBS TS RS TCS  mtx  free  mtx0 txal "
          + " ".join("%-6s" % n[:6] for n in names) + " txalerts / outcome")
    for t in rec["trials"]:
        if t["skip"]:
            print("   %5d  %4s   -    -    SKIPPED: %s" % (t["t"], t["req"],
                                                           t["skip"]))
            continue
        print("   %5d %4d %4d %4d %4d   %d   %d  %d   %d   %d->%d %5d %5d "
              "%5d  %s %s"
              % (t["t"], t["req"], t["act"], t["gate_us"], t["edge_us"],
                 t["tbs_b"], t["ts_b"], t["rs_b"], t["tcs_b"],
                 t["mtx_b"], t["mtx_a"], t["free_us"], t["mtx0_us"],
                 t["txalert_us"],
                 " ".join("%-6d" % t["wire"][n] for n in names),
                 (",".join(t["alert_names"]) or "NONE") + " / " + t["outcome"]))

    live = [t for t in rec["trials"] if not t["skip"]]
    skipped = collections.Counter(t["skip"] for t in rec["trials"] if t["skip"])
    print("   %d trials, %d live%s"
          % (rec["n"], len(live),
             ("; skipped: " + ", ".join("%s x%d" % (k, v)
                                        for k, v in skipped.items()))
             if skipped else ""))
    if not live:
        return

    print("   outcomes:")
    for outcome, k in collections.Counter(t["outcome"] for t in live).most_common():
        print("     %3d  %s" % (k, outcome))

    # Did the requested abort delay actually happen? Preemption made visible
    # rather than buried in the bins.
    errs = [t["act"] - t["req"] for t in live if t["act"] >= 0]
    if errs:
        big = [e for e in errs if e > 50]
        print("   abort-delay error act-req: min %d, median %d, max %d us"
              % (min(errs), sorted(errs)[len(errs) // 2], max(errs)))
        if big:
            print("   %d of %d trials overshot the requested delay by > 50 us "
                  "(preemption): %s" % (len(big), len(errs), sorted(big)[-6:]))

    # THE GATE'S OWN CHECK. In an RS-gated phase every live row must show TS = 0
    # before the abort: that is what "submitted into another node's frame" means.
    # A TS = 1 row there is a FAILED GATE, and it must be read as an instrument
    # problem rather than as a finding about the abort -- which is exactly the
    # confusion the level-triggered version would have produced.
    if rec["gate"] == "rs":
        ungated = [t["t"] for t in live if t["ts_b"]]
        edges = [t["edge_us"] for t in live if t["edge_us"] >= 0]
        if edges:
            print("   RS edge to abort: min %d, median %d, max %d us"
                  % (min(edges), sorted(edges)[len(edges) // 2], max(edges)))
        # DOES THE ACCEPTANCE FILTER AFFECT THE GATE? Reasoning says no -- the
        # filter is applied after a frame has been received and only decides
        # whether it enters the RX FIFO, whereas RS reflects bus-level receive
        # activity. But this probe does not go through the arming path, so spec
        # 5.1 item 2's refusal of a narrowed filter never runs, and the header
        # records the filter state rather than assuming it. The check: if the rate
        # of RS edges tracks the witness's frame rate, the filter is irrelevant to
        # the gate whatever it is set to. Suggested by the reviewing session,
        # 2026-09-27, explicitly as something to test rather than take on trust.
        gates = [t["gate_us"] for t in live if t["gate_us"] > 0]
        if gates:
            mean_gate = sum(gates) / len(gates)
            est = 1e6 / mean_gate if mean_gate > 0 else 0.0
            print("   RS edge wait: mean %.0f us -> ~%.0f edges/s (from %d "
                  "sample%s%s)"
                  % (mean_gate, est, len(gates), "" if len(gates) == 1 else "s",
                     " -- TOO FEW TO READ AS A RATE" if len(gates) < 5 else ""))
            for w in wits:
                f = w.fps()
                if f > 0:
                    print("     %-7s witnessed %.0f frames/s; edges/frames = %.2f"
                          % (w.name, f, est / f))
            print("     (a ratio near 1 means RS tracks bus activity, so the "
                  "acceptance filter -- narrowed: %s -- does not gate the gate)"
                  % rec.get("filter_narrowed"))
        if ungated:
            print("   *** THE RS GATE FAILED on %d of %d rows (TS=1 before the "
                  "abort, so the frame had already started): %s -- these are "
                  "instrument failures, not results about the abort"
                  % (len(ungated), len(live), ungated[:12]))
        else:
            print("   RS gate held: TS=0 before the abort on all %d live rows"
                  % len(live))

    # THE STOP CONDITION, AND IT IS SCOPED. No TX-relevant alert is one of the two
    # answers that changes what fake_twai must reproduce -- but ONLY where a frame
    # was submitted and then aborted. In the `control` phase nothing is submitted,
    # so no alert is the EXPECTED result and the thing that makes every other
    # phase's alert attributable to the abort acting on a real frame. The first
    # version of this check fired on the control rows and called it a stop
    # condition, which is worse than not checking: a warning that cries wolf on
    # its own control is a warning nobody believes when it fires for real.
    # NO COMPLETION EVENT is the stop condition that matters, and it is not the
    # same as "no alert": ARB_LOST is in the TX mask, so a frame can raise an
    # alert and still never report completion. Spec 5.2 item 5 is about crediting
    # completions; a frame that never completes is the case that breaks the
    # scheduler's "abort, wait for the buffer, then hand over" order outright.
    nocomp = [t["t"] for t in live
              if not t["skip"] and t["mtx0_us"] < 0 and t["free_us"] < 0
              and not t.get("noabort") and not t.get("control")]
    if nocomp:
        print("   *** NO COMPLETION EVENT AND THE BUFFER NEVER FREED on %d of "
              "%d trials: %s" % (len(nocomp), len(live), nocomp[:16]))
        print("   *** the abort did not remove the frame and nothing reported "
              "it. This is a STOP condition (spec 5.2 items 4, 5 and 9): the "
              "scheduler cannot assume one abort frees the buffer.")

    noal = [t["t"] for t in live if t["txalert_us"] < 0]
    if rec["phase"] == "control":
        if noal:
            print("   the abort command RAISED NO TX ALERT on all %d empty-buffer "
                  "trials -- so an alert in any other phase is attributable to "
                  "the abort acting on a real frame, not to the command itself"
                  % len(noal))
        else:
            print("   *** the abort command raised a TX alert with an EMPTY "
                  "BUFFER on %d of %d trials -- every other phase's alert is "
                  "then ambiguous" % (len(live) - len(noal), len(live)))
    elif noal:
        print("   *** NO TX ALERT AT ALL in %d of %d trials: %s"
              % (len(noal), len(live), noal[:12]))
        print("   *** that is a STOP condition (spec 5.2 item 9) -- it changes "
              "what the emulator must model")
    # Anything outside the TX mask that a reader should know about.
    other = collections.Counter()
    for t in live:
        for nm in t["all_alert_names"]:
            if nm not in t["alert_names"]:
                other[nm] += 1
    if other:
        print("   other alerts seen (outside the TX mask): %s"
              % ", ".join("%s x%d" % (k, v) for k, v in other.most_common()))
        # EXPECTED under a flood, and said here so it cannot be read as a
        # finding: nothing drains the receive queue in this build (gen_inhibit is
        # OFF, so its worker is not running) and each trial busy-polls for up to
        # 20 ms, which overruns a 64-deep queue at flood rates.
        if rec.get("flood"):
            print("   (RX_DATA / RX_QUEUE_FULL / RX_FIFO_OVERRUN and a rising "
                  "rx_missed are EXPECTED here -- the flood fills a 64-deep "
                  "queue that nothing is draining, and they say nothing about "
                  "the abort. The TX mask keeps them out of the measurement.)")
    failed = [t["t"] for t in live if "TX_FAILED" in t["alert_names"]]
    if failed:
        print("   *** TX_FAILED raised in %d trials: %s"
              % (len(failed), failed[:12]))
    for key, label in (("d_tx_failed", "driver tx_failed"),
                       ("d_bus_err", "driver bus_error"),
                       ("d_arb_lost", "driver arb_lost")):
        tot = sum(t[key] for t in live)
        if tot:
            print("   %s counted %d over the phase" % (label, tot))
    # THE CLEANUP'S OWN OUTCOME, across skipped rows too -- a skip caused by a
    # failed cleanup is a fact about the instrument, and the skipped rows are
    # where it shows.
    allrows = rec["trials"]
    # THE RETRY LOOP'S OWN NUMBERS. The question item 4's bound turns on is
    # whether the loop converges inside about one arbitration field, or whether it
    # costs attempts x latency.
    if rec.get("retry") == "1":
        conv = [t for t in live if t.get("tbs_us", -1) >= 0]
        hit = [t["t"] for t in live if t.get("retry_bound_hit")]
        print("   RETRY LOOP: %d of %d trials reached a free buffer"
              % (len(conv), len(live)))
        if conv:
            us = sorted(t["tbs_us"] for t in conv)
            at = sorted(t["att"] for t in conv)
            ts = sorted(t["ts1"] for t in conv)
            print("     first abort to a free buffer: %d / %d / %d us "
                  "(min/median/max)" % (us[0], us[len(us) // 2], us[-1]))
            print("     abort commands issued:        %d / %d / %d"
                  % (at[0], at[len(at) // 2], at[-1]))
            print("     polls that found TS=1 (no re-abort issued): %d / %d / %d"
                  % (ts[0], ts[len(ts) // 2], ts[-1]))
            once = sum(1 for t in conv if t["att"] == 1)
            print("     the FIRST abort was enough on %d of %d" % (once, len(conv)))
        if hit:
            print("     *** the 2 ms bound was hit on %d trials: %s"
                  % (len(hit), hit[:12]))
        # A retry that lands after the frame won arbitration is a legitimate
        # branch -- the telemetry went out after all -- but it must be
        # distinguishable from a preemption.
        went = [t["t"] for t in live if sum(t["wire"].values()) > 0]
        print("     the frame reached the wire anyway on %d of %d trials%s"
              % (len(went), len(live),
                 (": " + str(went[:12])) if went else
                 " -- so the loop preempted every one"))

    cl = [t for t in allrows if t.get("cleanup") or t.get("pre_cleanup")]
    if cl:
        worked = [t for t in cl if t.get("cleanup_us", -1) >= 0]
        failed = [t for t in cl if t.get("cleanup_us", -1) < 0]
        print("   buffer cleanup ran on %d of %d rows (%d on entry): freed the "
              "buffer on %d, FAILED on %d"
              % (len(cl), len(allrows),
                 sum(1 for t in cl if t.get("pre_cleanup")),
                 len(worked), len(failed)))
        if worked:
            us = sorted(t["cleanup_us"] for t in worked)
            print("     when it worked: %d / %d / %d us (min/median/max)"
                  % (us[0], us[len(us) // 2], us[-1]))
        if failed:
            left = sorted(set(t.get("cleanup_mtx") for t in failed))
            print("     *** when it FAILED, msgs_to_tx was still %s after 5 ms "
                  "-- the abort did not remove the frame, and THAT is a result "
                  "about the abort, not just about the bus" % left)
    nx = [t["t"] for t in live if t["next_tx_err"]]
    if nx:
        print("   *** THE FOLLOW-UP TRANSMIT WAS REFUSED in %d trials: %s"
              % (len(nx), nx[:12]))
    for w in wits:
        if w.errors():
            print("   *** %s saw %d ERROR FRAMES in this phase"
                  % (w.name, w.errors()))
    # Witnesses that disagree with each other about the same trial number.
    if len(wits) > 1:
        dis = [t["t"] for t in live
               if len(set(bool(v) for v in t["wire"].values())) > 1]
        if dis:
            print("   *** THE WITNESSES DISAGREE on %d trials: %s"
                  % (len(dis), dis[:12]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ixxat", action="store_true")
    ap.add_argument("--reps", type=int, default=3,
                    help="repeats per sweep step (idle)")
    ap.add_argument("--d0", type=int, default=0)
    ap.add_argument("--d1", type=int, default=400)
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--held-delay", type=int, default=15,
                    help="abort delay for the RS-gated phase, us")
    ap.add_argument("--held-reps", type=int, default=20)
    ap.add_argument("--contend-d1", type=int, default=60)
    ap.add_argument("--contend-step", type=int, default=5)
    ap.add_argument("--contend-reps", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(RUNS, "txabort_bench.json"))
    ap.add_argument("--phases",
                    default="quiet,control,noabort,idle,held,contend,retry")
    args = ap.parse_args()

    st0 = check_status()
    gi0 = gi_status()
    print("device git_version %s, gen_inhibit mode %d, batt %s"
          % (st0["git_version"], gi0["mode"], st0.get("batt_voltage")))
    if gi0["mode"] != 0:
        raise SystemExit("gen_inhibit must be OFF (mode 0) -- it is %d. "
                         "POST /gen_inhibit_set?mode=0 first." % gi0["mode"])

    # PCAN FIRST, then the Kvaser, then the IXXAT -- the 2026-09-22 open-order
    # rule. Swapping two adjacent lines here looks harmless and is not.
    print("opening PCAN ...")
    tx = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
    print("opening Kvaser ...")
    kv = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    buses = [tx, kv]
    wits = [Witness(kv, "kvaser")]
    if not args.no_ixxat:
        try:
            print("opening IXXAT ...")
            ix = can.Bus(interface="ixxat", channel=0, bitrate=BITRATE)
            buses.append(ix)
            wits.append(Witness(ix, "ixxat"))
        except Exception as e:                                  # noqa: BLE001
            print("  IXXAT not opened (%s) -- continuing with one witness" % e)

    out = {
        "probe": "wican_txabort_bench",
        "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "device": {"git_version": st0["git_version"],
                   "batt_voltage": st0.get("batt_voltage")},
        "witnesses": [w.name for w in wits],
        "flood_ids": ["0x%03X" % i for i in FLOOD_IDS],
        "phases": {},
    }
    order = []

    try:
        dropped = 0
        time.sleep(SETTLE_S)
        for b in buses:
            while b.recv(timeout=0.0) is not None:
                dropped += 1
        out["settle_dropped"] = dropped
        print("settled, dropped %d buffered frames" % dropped)

        want = [p.strip() for p in args.phases.split(",") if p.strip()]

        if "quiet" in want:
            print("\n== quiet: what is on the segment with nothing transmitting")
            for w in wits:
                w.start()
            time.sleep(3.0)
            for w in wits:
                w.stop()
            out["phases"]["quiet"] = {
                w.name: {"frames": len(w.rows), "errors": w.errors(),
                         "ids": w.id_census()} for w in wits}
            for w in wits:
                print("  %-7s %d frames, %d error frames, ids %s"
                      % (w.name, len(w.rows), w.errors(), w.id_census() or "{}"))

        def phase(key, flood, **kw):
            print("\n== %s" % key)
            fl = Flood(tx) if flood else None
            for w in wits:
                w.start()
            if fl is not None:
                fl.start()
            time.sleep(0.5)
            rec = run_probe(kw.pop("phase"), label=key, **kw)
            if fl is not None:
                fl.stop()
            time.sleep(0.5)
            for w in wits:
                w.stop()
            join_phase(rec, wits)
            rec["witness_errors"] = {w.name: w.errors() for w in wits}
            rec["rx_queue_note"] = (
                "nothing drains the RX queue in this build (gen_inhibit OFF), "
                "so rx_missed and the RX alert bits are expected under a flood "
                "and are not findings about the abort")
            if fl is not None:
                rate = fl.sent / fl.seconds if fl.seconds > 0 else 0.0
                rec["flood"] = {"sent": fl.sent, "refused": fl.refused,
                                "seconds": round(fl.seconds, 3),
                                "frames_per_s": round(rate, 1)}
                # WHAT THIS NUMBER IS NOT. `sent` counts frames the adapter
                # ACCEPTED, not frames on the wire: python-can's send returns
                # once the frame is queued in the driver, so this rate can far
                # exceed the bit rate. The witnessed rate below is the wire.
                # Measured 2026-09-27: 28,480 accepted/s against 3,973
                # witnessed/s, and 3,973 x ~250 us is essentially full
                # occupancy.
                wf = [w.fps() for w in wits if w.fps() > 0]
                rec["flood"]["witnessed_fps"] = [round(f, 1) for f in wf]
                print("  flood: %d frames ACCEPTED by the adapter in %.2f s "
                      "(%.0f/s -- a queue-acceptance rate, NOT the wire), "
                      "%d refused; witnessed on the wire: %s frames/s"
                      % (fl.sent, fl.seconds, rate, fl.refused,
                         ", ".join("%.0f" % f for f in wf) or "nothing"))
            summarise(rec, wits)
            out["phases"][key] = rec
            order.append(key)
            return rec

        if "control" in want:
            phase("control", False, phase="control", reps=5, settle=20)
        if "noabort" in want:
            phase("noabort", False, phase="noabort", reps=10, settle=20)
        if "idle" in want:
            phase("idle", False, phase="abort", gate="off", reps=args.reps,
                  d0=args.d0, d1=args.d1, step=args.step, settle=5)
        if "held" in want:
            phase("held", True, phase="abort", gate="rs", reps=args.held_reps,
                  d0=args.held_delay, d1=args.held_delay, settle=10)
        if "contend" in want:
            phase("contend", True, phase="abort", gate="off",
                  reps=args.contend_reps, d0=0, d1=args.contend_d1,
                  step=args.contend_step, settle=5)
        if "retry" in want:
            # THE REMEDY, MEASURED. Same stimulus as contend -- flood on, ungated,
            # same sweep -- with the abort-and-recheck loop turned on, so the two
            # phases differ in exactly one thing and the comparison is a
            # difference rather than an argument.
            phase("retry", True, phase="abort", gate="off", retry=1,
                  reps=args.contend_reps, d0=0, d1=args.contend_d1,
                  step=args.contend_step, settle=5)

        st1 = check_status()
        gi1 = gi_status()
        out["device_after"] = {"git_version": st1["git_version"],
                               "batt_voltage": st1.get("batt_voltage")}
        out["gen_inhibit_after"] = {"mode": gi1["mode"], "drv": gi1.get("drv")}
        # Uptime bracket across every phase, so a reboot mid-run is legible
        # rather than showing up as a page of aborted frames.
        ups = [(k, out["phases"][k]["uptime_us"]) for k in order]
        if len(ups) >= 2:
            mono = all(b[1] > a[1] for a, b in zip(ups, ups[1:]))
            out["uptime_us_by_phase"] = dict(ups)
            out["uptime_monotonic"] = mono
            print("\ndevice uptime %.1f s -> %.1f s over %d phases, "
                  "monotonic: %s" % (ups[0][1] / 1e6, ups[-1][1] / 1e6,
                                     len(ups), mono))
            if not mono:
                print("*** THE DEVICE RESTARTED MID-RUN -- every number above "
                      "is suspect; re-run before reading any of it")

    finally:
        for w in wits:
            w.stop()
        for b in buses:
            try:
                b.shutdown()
            except Exception:                                   # noqa: BLE001
                pass

    os.makedirs(RUNS, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    root, ext = os.path.splitext(os.path.abspath(args.out))
    for p in ("%s_%s%s" % (root, stamp, ext), root + ext):
        with open(p, "w") as fh:
            json.dump(out, fh, indent=1)
        print("wrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
