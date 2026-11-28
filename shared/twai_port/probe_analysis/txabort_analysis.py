#!/usr/bin/env python3
"""The analysis of the transmit-abort probe's older phases (control, noabort, abort; shared/twai_port
T13; gen-inhibit spec 5.2 item 9), with no transport: the arguments and the check of their echo,
the witness's rows by trial number, each trial's outcome, and the phase's summary with its stop
conditions.

MOVED to shared/twai_port (probe_analysis/, the shared probe-analysis move; charge-interposer
reviewer and gen-inhibit reviewer, 2026-11-27) by the charge-interposer tester, 2026-11-28, from
gen-inhibit/tools/bench/wican_txabort_bench.py at gen-inhibit/main 74555175. Each function here has
that file's code unchanged, and so have the witness's four reading methods, now in WitnessRows.
New with the move: probe_args() and answer_record(), which are run_probe()'s argument string and
its echo check without the HTTP call. What stayed in the bench driver is the transport and the
bench: the HTTP calls and the status page, the witness's capture thread, the flood, the adapters'
open order and the record's file. That file's docstring gives the measurement's reasons: the two
predictions, why the device cannot be its own witness, the RS gate, and why an ACKing node is
needed.

THE WITNESS. A trial frame carries its trial number in bytes 0-1 (little-endian) and the tag AB 7E
in bytes 2-3; the follow-up transmit carries AB 7F. A trial is joined to the witness's rows by that
number, never by timing or order (join_phase()). A witness row is (timestamp s, ID, data bytes,
is_error, is_extended). WitnessRows holds one adapter's rows and its name; a project's capture
class extends it.

THE OUTCOME (classify()). Not collapsed to three cases: TS = 1 covers the arbitration field as well
as the data phase, so "was transmitting" does not imply "reached the wire". Rows are classified by
the achieved abort delay the device reports, never by the requested one.
"""

import collections

TRIAL_ID = 0x7FE            # matches TXAB_ID in vtrux/twai_port's test_app/twai_port_txabort_probe.c
TAG_HI = 0xAB
TAG_TRIAL = 0x7E
TAG_FOLLOW = 0x7F

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


def probe_args(phase, **kw):
    """The arguments to send, as run_probe() made them: (sent, args), where sent is every argument with
    its phase, and args the key=value list, phase first, then the rest in the order given. Gen-inhibit's
    driver joins args with "&" after "/gi_txabort?"; the T19 app's serial command takes them after
    "txabort ", joined with spaces."""
    sent = dict(kw)
    sent["phase"] = phase
    return sent, ["phase=%s" % phase] + ["%s=%s" % (k, v) for k, v in kw.items()]


def answer_record(sent, rec):
    """The probe's answer (the parsed JSON object), checked against what was sent (verify_echo(), which
    raises SystemExit on any difference) and given the arguments sent as rec["sent"]. Returns rec."""
    verify_echo(sent, rec)
    rec["sent"] = sent
    return rec


class WitnessRows(object):
    """One witness adapter's rows, (timestamp s, ID, data bytes, is_error, is_extended), and its name.

    Kept as raw rows rather than pre-filtered: the error-frame count and the
    "what else was on the bus" survey both need everything, and a filter applied
    at capture time is a filter nobody can check afterwards.
    """

    def __init__(self, name, rows=None):
        self.name = name
        self.rows = [] if rows is None else rows

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
