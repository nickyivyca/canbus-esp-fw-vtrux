"""The session anchor: a timing witness's device stamps placed on the host's epoch clock, when
nothing ties the two clocks together but the frames themselves.

Gen-inhibit tester, 2026-11-13, the gen-inhibit reviewer's ruling (C) of that day and its P1
spec. The PCAN's stamps on madhouse are its own device time (RAW libpcanbasic, sysfs ts_mode
3): an uptime base with no epoch tie, and the jammer checks put nothing on CAN1 but the
jammer's error frames, so the only points to place them by are the judged frames. Nothing in
this module knows which adapter the stamps came from: bench_witness.PcanWitness gives x2_jammer's
session the (device, receipt) pairs, and the session gives them here. Kept apart from the
adapter's own code by the ruling.

THE METHOD (P1 (1)-(3); the record and the run README state it, P1 (6)):
  anchor(points, ...)   points: [(device_s, receipt_epoch_s)] for every judged frame of the
                        session, in receipt order.
    offset   = min(receipt - device) over all of them: the frame that reached the host
               fastest is taken as delivered at once, so mapped(t) = t + offset and no judged
               frame maps after its own receipt.
    slope    = least squares of (receipt - device) on device, in ppm, over the lower envelope
               (envelope(): the fastest frame in each of ENVELOPE_BINS equal stretches of the
               session, so slow deliveries cannot tilt it); drift_s = |slope| x the session's
               device span, the error a constant offset leaves at the far end. VOID when
               drift_s is over DRIFT_MAX_S.
    spread   = receipt - mapped per frame (the delivery time beyond the fastest frame's);
               VOID unless its p99 is under SPREAD_P99_MAX_S. It replaces the Kvaser path's
               median-lag bound (x2_jammer.TIMING_LAG_S).
    VOID too: fewer than MIN_POINTS frames; stamps on the host's own epoch clock (median
    receipt - device under EPOCH_LAG_S: COOKED mode, host time, not the device's); any frame
    mapping after its own receipt (cannot happen with the offset above, so it means the
    offset was not taken as the method says).
    REFUSED (also VOID): the device clock went back between two frames in receipt order (a
    replug or a controller reset restarts it); the witness reports more than one open of
    its channel in the session, or a different device at the close than at the open.
  mapped(anc, t)        a device stamp on the epoch clock
  bracket(anc, frames, t_send, t_done, t_next)
                        P1 (2), per command, as widened by the reviewer on 2026-11-13: every
                        frame the host received from the command's send to the next command's
                        send, however late after its DONE, must map after the send and no later
                        than min(its own receipt, the DONE's receipt). x2_jammer.judge_session:
                        a breach VOIDs any verdict but a FAIL (a FAIL stays, with the breach
                        kept: a real frame after DONE is a finding), and so does a command with a
                        DONE but no frame. The margins go in the record.

The bounds are fixed here, before any run, and never set from the run they judge (reviewer,
2026-11-13). SPREAD_P99_MAX_S is the ruling's 0.25 s, under the judges' 0.3 s margins.
DRIFT_MAX_S is a fifth of that: the PCAN's rate against the host was measured at -6.0 to +0.8
ppm (CAN replay tooling, 2026-11-11 and 2026-11-13), 5 ms over a 15-minute session.
"""
import math

SPREAD_P99_MAX_S = 0.25
DRIFT_MAX_S = 0.05
MIN_POINTS = 10
EPOCH_LAG_S = 2.0
ENVELOPE_BINS = 10
METHOD = ("one anchor per session: offset = min(receipt - device) over every judged frame of the "
          "session's checks; mapped = device + offset; slope by least squares of (receipt - device) "
          "on device over the lower envelope (the fastest frame in each of %d equal stretches of the "
          "session), VOID when |slope| x span > %g s; VOID unless p99(receipt - mapped) < %g s; "
          "VOID with fewer than %d frames, stamps on the host epoch clock (median lag < %g s), the "
          "device clock going back, or a reopened or replugged channel; per command, every frame "
          "received from its send to the next command's send must map after the send and no later "
          "than min(its own receipt, the DONE's receipt), and a command with a DONE must have a "
          "frame (gen-inhibit reviewer, (C) / P1, 2026-11-13, the bracket widened the same day)"
          % (ENVELOPE_BINS, DRIFT_MAX_S, SPREAD_P99_MAX_S, MIN_POINTS, EPOCH_LAG_S))
BOUNDS = {"spread_p99_max_s": SPREAD_P99_MAX_S, "drift_max_s": DRIFT_MAX_S,
          "min_points": MIN_POINTS, "epoch_lag_min_s": EPOCH_LAG_S, "envelope_bins": ENVELOPE_BINS}


def _rank(s, q):
    """The nearest-rank q quantile of the sorted list s."""
    return s[min(len(s) - 1, max(0, int(math.ceil(q * len(s))) - 1))]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def mapped(anc, t):
    return t + anc["offset_s"]


def envelope(points):
    """The lower envelope the slope is fitted on: the session's device span cut into
    ENVELOPE_BINS equal stretches, and in each the frame with the smallest receipt - device
    (the fastest delivered), as (device, receipt - device). A burst of slow deliveries then
    cannot tilt the fit; only the clocks' own rate can."""
    lo = min(d for d, _r in points)
    width = (max(d for d, _r in points) - lo) / ENVELOPE_BINS or 1.0
    best = {}
    for d, r in points:
        b = min(ENVELOPE_BINS - 1, int((d - lo) / width))
        if b not in best or r - d < best[b][1]:
            best[b] = (d, r - d)
    return [best[b] for b in sorted(best)]


def anchor(points, opens=1, same_device=True):
    """The session's anchor (module doc). points in receipt order; opens: how many times the
    witness opened its channel in the session; same_device: the witness's device at the close
    is the one it opened."""
    anc = {"method": METHOD, "bounds": dict(BOUNDS), "n": len(points), "opens": opens,
           "same_device": same_device, "offset_s": None, "void": None}
    back = [i for i in range(1, len(points)) if points[i][0] < points[i - 1][0]]
    anc["device_clock_back_at"] = back[:5]
    if opens != 1:
        anc["void"] = "REFUSED: the witness opened its channel %s times in the session, not once" % opens
        return anc
    if not same_device:
        anc["void"] = "REFUSED: the witness's device at the close is not the one it opened (replugged)"
        return anc
    if back:
        anc["void"] = ("REFUSED: the device clock went back at frame %d (of %d): a replug or a reset"
                       % (back[0], len(points)))
        return anc
    if len(points) < MIN_POINTS:
        anc["void"] = "%d judged frames, fewer than %d to anchor the session on" % (len(points), MIN_POINTS)
        return anc
    lag = _median(r - d for d, r in points)
    anc["median_receipt_minus_device_s"] = round(lag, 6)
    if abs(lag) < EPOCH_LAG_S:
        anc["void"] = ("the stamps are on the host's epoch clock (median %.4f s behind receipt): host "
                       "time (COOKED), not the device's" % lag)
        return anc
    off = min(r - d for d, r in points)
    anc["offset_s"] = off
    env = envelope(points)
    anc["envelope_points"] = len(env)
    xs = [d for d, _y in env]
    ys = [y for _d, y in env]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    slope = None if var == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    span = max(d for d, _r in points) - min(d for d, _r in points)
    spread = sorted(r - mapped(anc, d) for d, r in points)
    anc.update(span_s=round(span, 3), slope_ppm=None if slope is None else round(slope * 1e6, 3),
               drift_s=None if slope is None else round(abs(slope) * span, 6),
               spread_p50_s=round(_rank(spread, 0.50), 6), spread_p99_s=round(_rank(spread, 0.99), 6),
               spread_max_s=round(spread[-1], 6))
    late = [i for i, (d, r) in enumerate(points) if mapped(anc, d) > r]
    if late:
        anc["void"] = ("%d frames map after their own receipt (first %d): the offset is not the "
                       "fastest frame's" % (len(late), late[0]))
    elif slope is None:
        anc["void"] = "no slope: every frame has the same device stamp"
    elif anc["drift_s"] > DRIFT_MAX_S:
        anc["void"] = "drift %.4f s over the session (slope %s ppm x %.1f s), over %g s" % (
            anc["drift_s"], anc["slope_ppm"], span, DRIFT_MAX_S)
    elif not anc["spread_p99_s"] < SPREAD_P99_MAX_S:
        anc["void"] = "spread p99 %.4f s, not under %g s" % (anc["spread_p99_s"], SPREAD_P99_MAX_S)
    return anc


def bracket(anc, frames, t_send, t_done, t_next):
    """P1 (2) for one command (widened by the reviewer, 2026-11-13). frames: [(device, receipt)]
    of the session; the command's are those the host received from its send up to the next
    command's send (t_next; the check's end for its last), whenever that was relative to its
    DONE: if the PCAN delivers slower than the console prints DONE, the frames still count.
    Each must map after the send and no later than min(its own receipt, the DONE's receipt): the
    pulse is on the wire before the jammer prints DONE, and the lower envelope maps late, never
    early, by the fastest delivery's delay. t_done None (no DONE): only its own receipt bounds
    it. -> {n, outside, margins, ok}; the caller decides what a breach or an empty command does
    to the check's verdict."""
    mine = [(d, r) for d, r in frames if t_send <= r < t_next]
    out = []
    after_send, before_rx, before_done = [], [], []
    for d, r in mine:
        m = mapped(anc, d)
        top = r if t_done is None else min(r, t_done)
        after_send.append(m - t_send)
        before_rx.append(r - m)
        if t_done is not None:
            before_done.append(t_done - m)
        if not (t_send < m <= top):
            out.append({"mapped": round(m, 6), "receipt": round(r, 6)})
    return {"t_send": round(t_send, 6), "t_done": None if t_done is None else round(t_done, 6),
            "t_next": round(t_next, 6), "n": len(mine), "outside": out[:10], "n_outside": len(out),
            "margin_after_send_min_s": round(min(after_send), 6) if mine else None,
            "margin_before_receipt_min_s": round(min(before_rx), 6) if mine else None,
            "margin_before_done_min_s": round(min(before_done), 6) if before_done else None,
            "ok": not out}
