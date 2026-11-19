"""Burst length and the 5 ms close, measured on the PCAN's own stamps (condition 5).

Gen-inhibit tester, 2026-11-18, the gen-inhibit reviewer's condition 5 of that day and its ruling
on the bounds. B9's and B10's wrong-bit-rate bursts and NONODE's replayer moved from the Kvaser
to canmain, CAN1's Innomaker (gs_usb over libusb, kvaser_bus.innomaker_held). Whether gs_usb
holds a burst for the asked length and closes when asked is measured, not assumed: by the
committed check canmain_burst_check.py (its can-fails included), and in every B9, B10 and NONODE
run, from these same functions, on that run's own PCAN stamps. In a run a breach VOIDs the check;
it never FAILs it, and no judge is loosened (reviewer, 2026-11-18).

All times are seconds on one clock: the PCAN's device stamps mapped onto time.perf_counter by
witness_clock.device_fit, and the host's perf_counter for the close. A VOID fit means nothing here
is measured: the caller's check is VOID on the fit, as everywhere else.

  burst_window(starts, closes, k)  the witness error frames that belong to burst k: from its
                    start (just before the open: the error frames begin at bus-on, inside the
                    open) to the next burst's start or its close + TAIL_S, whichever is first.
                    TAIL_S is B9's per_burst tail.
  burst_measure(asked_ms, t_close, errors)   one burst: {asked_ms, n, first_s, last_s, span_ms,
                    last_after_close_ms, breaches}. Breaches (the reviewer's bounds):
                      - no witness error frame (a burst never sent);
                      - the last error frame more than CLOSE_EDGE_S (5 ms) after the host close;
                      - a span (first to last error frame) over asked + SPAN_OVER_S (5 ms);
                      - for asked >= SPAN_UNDER_FROM_S (5 ms), a span under asked - SPAN_UNDER_S
                        (2 ms): a burst cut short.
  close_measure(t_last, t_close, late, probe_acked)   NONODE's close: {gap_ms, late, probe,
                    breaches}. t_last is the last canmain frame on the wire, t_close the host
                    close, late the canmain frames heard from t_close + SILENCE_EDGE_S on, and
                    probe_acked whether a frame sent after that was ACKed (None: no probe sent).
                    Breaches: no last frame; the close not CLOSE_AFTER_S (5 ms) +/- CLOSE_TOL_S
                    (3 ms, twice the PCAN fit's worst p99 residual of 2026-11-12) after the last
                    frame; any late frame; the probe ACKed.
  breach_line(measures)   "" or the one line a check prints when any measure breached; the suite
                    judges read it (BURST_VOID_PREFIX) and make the check VOID.
"""

CLOSE_EDGE_S = 0.005        # the last error frame at most this after the host close
SPAN_OVER_S = 0.005         # a burst's span at most asked + this
SPAN_UNDER_S = 0.002        # ... and at least asked - this,
SPAN_UNDER_FROM_S = 0.005   # for an asked length of at least this
TAIL_S = 0.5                # wrong_bitrate_errors.per_burst's tail: a burst's window ends here
CLOSE_AFTER_S = 0.005       # nonode_bench.CLOSE_AFTER_S: the close this long after the last frame
CLOSE_TOL_S = 0.003         # +/- this (reviewer, 2026-11-18)
SILENCE_EDGE_S = 0.020      # nonode_bench.SILENCE_EDGE_S: from the close + this, canmain is silent
BURST_VOID_PREFIX = "burst timing VOID: "
EPS = 1e-9


def burst_window(starts, closes, k):
    """(from, to) of burst k's window: its start to the next start or its close + TAIL_S."""
    end = closes[k] + TAIL_S
    if k + 1 < len(starts):
        end = min(end, starts[k + 1])
    return starts[k], end


def in_window(times, window):
    lo, hi = window
    return [t for t in times if lo <= t < hi]


def burst_measure(asked_ms, t_close, errors):
    asked = asked_ms / 1000.0
    errors = sorted(errors)
    m = {"asked_ms": asked_ms, "n": len(errors), "first_s": None, "last_s": None, "span_ms": None,
         "last_after_close_ms": None, "breaches": []}
    if not errors:
        m["breaches"].append("burst %g ms: no witness error frame" % asked_ms)
        return m
    first, last = errors[0], errors[-1]
    span, after = last - first, last - t_close
    m.update(first_s=first, last_s=last, span_ms=round(span * 1000, 3), last_after_close_ms=round(after * 1000, 3))
    if after > CLOSE_EDGE_S + EPS:
        m["breaches"].append("burst %g ms: the last error frame %.3f ms after the close, over %g ms"
                             % (asked_ms, after * 1000, CLOSE_EDGE_S * 1000))
    if span > asked + SPAN_OVER_S + EPS:
        m["breaches"].append("burst %g ms: span %.3f ms, over %g ms" % (asked_ms, span * 1000,
                                                                       (asked + SPAN_OVER_S) * 1000))
    if asked >= SPAN_UNDER_FROM_S - EPS and span < asked - SPAN_UNDER_S - EPS:
        m["breaches"].append("burst %g ms: span %.3f ms, under %g ms" % (asked_ms, span * 1000,
                                                                        (asked - SPAN_UNDER_S) * 1000))
    return m


def close_measure(t_last, t_close, late, probe_acked=None):
    m = {"gap_ms": None, "late": len(late), "late_first_after_close_ms": None,
         "probe": "not sent" if probe_acked is None else ("ACKed" if probe_acked else "not ACKed"), "breaches": []}
    if t_last is None:
        m["breaches"].append("close: no last canmain frame on the witness")
    else:
        gap = t_close - t_last
        m["gap_ms"] = round(gap * 1000, 3)
        if abs(gap - CLOSE_AFTER_S) > CLOSE_TOL_S + EPS:
            m["breaches"].append("close: %.3f ms after the last canmain frame, not %g +/- %g ms"
                                 % (gap * 1000, CLOSE_AFTER_S * 1000, CLOSE_TOL_S * 1000))
    if late:
        m["late_first_after_close_ms"] = round((min(late) - t_close) * 1000, 3)
        m["breaches"].append("close: %d canmain frames from %g ms after the close (the first %.3f ms after)"
                             % (len(late), SILENCE_EDGE_S * 1000, m["late_first_after_close_ms"]))
    if probe_acked:
        m["breaches"].append("close: the probe after the close was ACKed")
    return m


def breach_line(measures):
    br = [b for m in measures for b in m["breaches"]]
    return BURST_VOID_PREFIX + "; ".join(br) if br else ""
