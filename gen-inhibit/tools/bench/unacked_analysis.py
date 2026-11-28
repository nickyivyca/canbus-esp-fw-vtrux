#!/usr/bin/env python3
"""The analysis of the probe's no-acknowledge modes (shared/twai_port T13, the 2026-11-27
addition; gen-inhibit tracker F5). Pure functions: no bench, no device. Gen-inhibit tester,
2026-11-27.

THE INPUT. The probe's answer to POST /gi_txabort?phase=m1|m2|m3, as test_app/README.md
("The no-acknowledge modes") gives it, and the witness's copies of the trial frame (ID
0x7FE, bytes 2-3 AB 7E), each (witness time in us, data hex). Times in a trial are ns from
the send. Each change record is [t_ns, st, tec, gap]; the status bits are the answer's
`st_bits` (ts 32, tbs 4, tcs 8).

WHAT IT GIVES, per trial:
  attempts    each TS rise (0 -> 1): its time, the TEC then, and whether a gap came before it;
              and a first sample that reads TS = 1 (the first attempt began inside
              twai_transmit()), marked `first_sample`: it counts for the witness's order, but
              it has no rise time, so it is left out of the join's offset and its mark
  idles       each TS = 0 span between two attempts: from the TS fall to the next rise, its
              length, the TEC in it and its error state (TEC below 128: active; 128 or more:
              passive), and whether either edge came after a gap (its length is then a bound,
              not a reading)
  held        each pair of attempts with no TS fall seen between them cannot occur: a rise
              needs a fall first. So TS staying 1 across resends shows only on the witness:
              more copies on the wire than TS rises (`witness_vs_rises`)
  abort       m2 and m3: the abort's time, the status just before and after it, its TEC and
              error state, whether the buffer came free and how, and the witness copies
              after the abort

THE WITNESS JOIN. A trial's copies are joined to its TS rises by order: copy k to rise k.
The device's clock and the witness's are lined up by the median of (copy time - rise time)
over the pairs, and each pair's residual from that median is reported. A copy needs about
220-280 us on the wire at 500 kbit/s; a residual over RESIDUAL_US makes the join unsure,
and the trial says so. The offset takes up the copy's stamp delay, so a lined-up copy sits at
its TS rise. A later attempt is counted by order: each copy after the first `ab_attempt` (the
attempts the device saw up to the abort). By time, a copy whose lined-up time is after the
abort is a later attempt too; it is the cross-check, and a trial where the two counts differ is
listed. The trace's copies are those lined up no later than its end plus RESIDUAL_US; the rest
are the hold's resends. More copies in the trace than rises is reported.
"""

import statistics

TS, TBS, TCS = 32, 4, 8
PASSIVE_TEC = 128
RESIDUAL_US = 50.0      # a pair further than this from the median offset makes the join unsure
ALERT_TX_SUCCESS = 0x00000002   # TWAI_ALERT_TX_SUCCESS (wican_txabort_bench.ALERT_NAMES)


def state_of(tec):
    if tec is None or tec < 0:
        return None
    return "passive" if tec >= PASSIVE_TEC else "active"


def ts_profile(trial):
    """The attempts and the TS = 0 spans between them, from the trial's change records."""
    ev = trial.get("events") or []
    attempts, idles = [], []
    prev_ts = None
    fall = None             # (t_ns, tec, gap) of the last TS fall
    for e in ev:
        t, st, tec, gap = e[0], e[1], e[2], bool(e[3]) if len(e) > 3 else False
        ts = 1 if st & TS else 0
        if prev_ts is None and ts == 1:
            # the first attempt started inside twai_transmit() (README: tx_call_ns): it is an attempt, and the
            # witness has its copy, but its rise was not seen, so its time is not a rise time
            attempts.append({"t_ns": t, "tec": tec, "state": state_of(tec), "gap": gap, "first_sample": True})
        elif prev_ts is not None and ts != prev_ts:
            if ts == 1:
                if fall is not None:
                    idles.append({"from_ns": fall[0], "to_ns": t, "ns": t - fall[0], "tec": fall[1],
                                  "state": state_of(fall[1]), "gap": fall[2] or gap})
                attempts.append({"t_ns": t, "tec": tec, "state": state_of(tec), "gap": gap})
                fall = None
            else:
                fall = (t, tec, gap)
        elif fall is not None and ts == 0 and tec != fall[1]:
            fall = (fall[0], tec, fall[2] or gap)       # the TEC that holds in the idle: the latest seen in it
        prev_ts = ts
    return attempts, idles


def held_resends(trial):
    """Resends across which TS stayed 1, per error state: each TEC step (each failed attempt raises it, up to
    128) recorded with TS = 1 and no TS = 0 recorded since the last step; its state is the TEC before the step.
    Bench run 403a (2026-11-27): in the error-active part TS never read 0 between resends, so the attempts there
    show only as TEC steps, never as TS rises."""
    out = {"active": 0, "passive": 0}
    ev = trial.get("events") or []
    prev_tec, zero_since = None, False
    for e in ev:
        st, tec = e[1], e[2]
        ts = 1 if st & TS else 0
        if ts == 0:
            zero_since = True
        if prev_tec is not None and tec > prev_tec:
            if ts == 1 and not zero_since:
                out[state_of(prev_tec)] += 1
            zero_since = False
        prev_tec = tec
    return out


def span_stats(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return {"n": len(xs), "min": xs[0], "median": statistics.median(xs), "max": xs[-1]}


def witness_join(attempts, copies):
    """Copy k to rise k. Returns the offset (us, witness minus device), each pair's residual,
    the unpaired counts, and whether the join is sure."""
    n = min(len(attempts), len(copies))
    out = {"rises": len(attempts), "copies": len(copies), "paired": n, "offset_us": None,
           "residuals_us": [], "max_residual_us": None, "sure": False}
    if n == 0:
        return out
    d = [copies[k][0] - attempts[k]["t_ns"] / 1000.0 for k in range(n)]
    rise = [k for k in range(n) if not attempts[k].get("first_sample")]   # a first-sample attempt has no rise time
    if not rise:
        return out
    off = statistics.median([d[k] for k in rise])
    res = [round(x - off, 3) for x in d]
    out.update(offset_us=round(off, 3), residuals_us=res, max_residual_us=max(abs(res[k]) for k in rise))
    out["sure"] = out["max_residual_us"] <= RESIDUAL_US
    return out


def copies_after(copies, attempt):
    """The later attempts, by order: the copies after the first `attempt` (the attempts the device saw
    up to the abort, `ab_attempt`)."""
    if attempt is None or attempt < 0:
        return None
    return sorted(copies)[attempt:]


def copies_after_by_time(copies, offset_us, abort_ns):
    """The cross-check, by time: the copies whose lined-up time (copy time minus the offset, so the
    copy's TS rise) is after the abort. A residual near the abort can move a copy across it."""
    if offset_us is None or abort_ns is None or abort_ns < 0:
        return None
    return [c for c in copies if c[0] - offset_us > abort_ns / 1000.0]


def trial_summary(trial, copies):
    """One trial: its profile, the witness join, and for m2 and m3 the abort."""
    attempts, idles = ts_profile(trial)
    j = witness_join(attempts, copies)
    end_ns = trial.get("end_ns")
    if j["offset_us"] is not None and end_ns is not None and end_ns >= 0:
        # a copy is stamped at its end, up to a frame's time after its rise; copies after that are the
        # hold's resends, not the trace's
        in_trace = [c for c in copies if c[0] - j["offset_us"] <= end_ns / 1000.0 + RESIDUAL_US]
    else:
        in_trace = copies
    s = {"t": trial.get("t"), "skip": trial.get("skip") or "", "attempts": attempts, "idles": idles,
         "device_attempts": trial.get("attempts"), "witness": j, "copies_in_trace": len(in_trace),
         "held_resends": held_resends(trial),
         "witness_vs_rises": len(in_trace) - j["rises"], "gaps": trial.get("gaps"),
         "max_gap_ns": trial.get("max_gap_ns"), "ev_lost": trial.get("ev_lost"),
         "cleared": trial.get("cleared"), "clear_ns": trial.get("clear_ns")}
    ab = trial.get("abort_ns")
    if ab is not None and ab >= 0:
        after = copies_after(copies, trial.get("ab_attempt"))
        by_time = copies_after_by_time(copies, j["offset_us"], ab)
        s["abort"] = {"ns": ab, "issued": trial.get("ab_issued"), "st_before": trial.get("ab_st_before"),
                      "st_after": trial.get("ab_st_after"), "tec": trial.get("ab_tec"),
                      "state": state_of(trial.get("ab_tec")), "attempt": trial.get("ab_attempt"),
                      "free_after_ns": trial.get("free_after_abort_ns"),
                      "ts_before": None if trial.get("ab_st_before") is None else int(bool(trial["ab_st_before"] & TS)),
                      "copies_after": None if after is None else len(after),
                      "copies_after_by_time": None if by_time is None else len(by_time),
                      "freed": trial.get("cleared") in ("tbs", "tbs_tcs"),
                      "cleanup": bool(trial.get("cleanup"))}
    else:
        s["abort"] = None
        s["no_abort_reason"] = ("wait_timeout" if trial.get("wait_timeout") else
                                "freed_before" if trial.get("freed_before") else "")
    return s


def hold_check(summary, trial, copies, node_up_us, node_down_us, node_rx_us):
    """The acknowledging node's return inside the hold (`hold_ms`; the gen-inhibit reviewer's
    answer (b), 2026-11-27). Times are on the witness clock (us): the node's up and down, and
    each copy of the trial frame the node itself received while up (a frame it received is a
    frame it acknowledged, so it went out).
      void      the node came up before the trace ended (the trace's end, lined up, at or after
                node_up), or the join has no offset to line it up with
      held      the frame was still in the buffer at the hold's start: the trace did not see the
                buffer come free (`cleared` neither tbs nor tbs_tcs)
      expect    held: it must go out (m1 is the positive control: never aborted, so always held);
                not held: nothing may go out
      out       the node received the trial frame while up; the witness copies after node_up
                are given beside it
    """
    off = summary["witness"]["offset_us"]
    end_ns = trial.get("end_ns")
    r = {"node_up_us": node_up_us, "node_down_us": node_down_us, "void": None, "held": None,
         "expect": None, "out": None, "as_expected": None}
    if off is None or end_ns is None or end_ns < 0:
        r["void"] = "no witness offset or no trace end to line the node's return up with"
        return r
    trace_end_us = end_ns / 1000.0 + off
    r["trace_end_us"] = round(trace_end_us, 3)
    if node_up_us <= trace_end_us:
        r["void"] = "the node came up %.1f us before the trace ended" % (trace_end_us - node_up_us)
        return r
    held = trial.get("cleared") not in ("tbs", "tbs_tcs")
    rx = [t for t in node_rx_us if node_up_us <= t <= node_down_us]
    seen = [c for c in copies if node_up_us <= c[0] <= node_down_us]
    r.update(held=held, expect="goes out" if held else "nothing goes out", node_rx=len(rx),
             witness_copies_while_up=len(seen), out=bool(rx))
    r["as_expected"] = r["out"] == held
    # the device's own view of the hold, beside the node's (README step 9): TX_SUCCESS in alerts_hold, and
    # TBS and TCS at the hold's end; recorded, not judged. A difference from the node's view is listed
    ah = trial.get("alerts_hold")
    r["device"] = {"tx_success": None if ah is None else bool(ah & ALERT_TX_SUCCESS),
                   "hold_tbs": trial.get("hold_tbs"), "hold_tcs": trial.get("hold_tcs"),
                   "hold_ns": trial.get("hold_ns"), "cleanup": trial.get("cleanup")}
    r["device_differs"] = r["device"]["tx_success"] is not None and r["device"]["tx_success"] != r["out"]
    return r


def hold_table(rows):
    """Per case (held or not), the trials as expected and not, and the void ones with why."""
    out = {}
    for case, held in (("held: must go out", True), ("freed: nothing may go out", False)):
        rs = [x for x in rows if x["hold"]["void"] is None and x["hold"]["held"] is held]
        out[case] = {"trials": len(rs), "as_expected": sum(1 for x in rs if x["hold"]["as_expected"]),
                     "not_as_expected": [x["t"] for x in rs if not x["hold"]["as_expected"]]}
    out["void"] = [(x["t"], x["hold"]["void"]) for x in rows if x["hold"]["void"] is not None]
    out["device_differs"] = [x["t"] for x in rows if x["hold"].get("device_differs")]
    return out


def group_copies(copies, gap_us=5000.0):
    """The witness's trial-frame copies, split into bursts at a quiet gap: one burst per trial."""
    bursts, cur, last = [], [], None
    for c in sorted(copies):
        if last is not None and c[0] - last > gap_us:
            bursts.append(cur)
            cur = []
        cur.append(c)
        last = c[0]
    if cur:
        bursts.append(cur)
    return bursts


def m1_table(summaries):
    """M1: per error state, the TS = 0 spans between attempts (lengths in us), how many had a gap,
    and the trials where the witness saw more copies than TS rises (TS held at 1 across a resend,
    or a rise hidden in a gap)."""
    out = {}
    for st in ("active", "passive"):
        clean = [i["ns"] / 1000.0 for s in summaries for i in s["idles"] if i["state"] == st and not i["gap"]]
        gapped = [i for s in summaries for i in s["idles"] if i["state"] == st and i["gap"]]
        out[st] = {"idles_us": span_stats(clean), "idles_with_gap": len(gapped),
                   "resends_ts_held": sum((s.get("held_resends") or {}).get(st, 0) for s in summaries)}
    out["trials"] = len(summaries)
    out["more_copies_than_rises"] = [s["t"] for s in summaries if s["witness_vs_rises"] > 0]
    out["joins_unsure"] = [s["t"] for s in summaries if not s["witness"]["sure"]]
    return out


def abort_table(summaries):
    """M2 and M3: per error state, the trials with an abort: freed (in the trace), not freed, and
    those with witness copies after the abort; and the trials with no abort, by reason."""
    out = {}
    for st in ("active", "passive"):
        rows = [s for s in summaries if s["abort"] and s["abort"]["state"] == st]
        out[st] = {"trials": len(rows),
                   "freed": sum(1 for s in rows if s["abort"]["freed"]),
                   "not_freed": [s["t"] for s in rows if not s["abort"]["freed"]],
                   "copies_after_abort": [s["t"] for s in rows if s["abort"]["copies_after"]],
                   "order_vs_time_differ": [s["t"] for s in rows
                                            if s["abort"]["copies_after"] != s["abort"]["copies_after_by_time"]],
                   "joins_unsure": [s["t"] for s in rows if not s["witness"]["sure"]],
                   "free_after_us": span_stats([s["abort"]["free_after_ns"] / 1000.0 for s in rows
                                                if s["abort"]["freed"] and s["abort"]["free_after_ns"] is not None
                                                and s["abort"]["free_after_ns"] >= 0]),
                   "ts_at_abort": sorted({s["abort"]["ts_before"] for s in rows if s["abort"]["ts_before"] is not None})}
    out["no_abort"] = [(s["t"], s.get("no_abort_reason") or s["skip"]) for s in summaries if not s["abort"]]
    return out
