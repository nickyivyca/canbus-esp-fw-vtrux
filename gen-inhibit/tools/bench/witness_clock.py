"""Which clock the Kvaser witness's times are on, and the guard that keeps a check
from judging on receipt time.

Gen-inhibit tester, 2026-11-11, the gen-inhibit reviewer's A2 ruling (b) of that day:
spec 12 requires the timing witness to timestamp at the wire, in hardware. The Kvaser
witnesses in `interlock_bench.py`, `x2_jam_cases.py` and `x2_jammer.py` keep each
frame's hardware stamp and its host receipt time. They use the hardware stamp when
its median lag behind receipt is under `MAX_LAG_S`, so that it is on the epoch clock
the page samples and the posts use. Otherwise they fell back to receipt time, and
the checks went on to judge on it. Now a check is never judged on receipt time:
it is VOID, with the clock note.

  hw_note(lag)        the note for hardware stamps, with the median lag
  RECEIPT             the note for receipt time (the stamps are not on the epoch clock)
  NONE                the note when the witness has no time to place (nothing heard)
  judgeable(note)     True for hw_note's notes and NONE; False for anything else
  require(note)       raises ReceiptClock(note) unless judgeable(note)
  void_verdict(note)  the verdict string a check gets instead of a judgement
  void(rec, note)     puts that verdict, and the note, in a record

The data on either clock is still recorded (the witness file, the counts), labelled
by its note, so the run can be read again. Only the judgement is withheld.

THE PCAN'S OWN STAMPS (A2 (a), the gen-inhibit reviewer's design OK of 2026-11-11).
B9 (`wrong_bitrate_errors.py`) and B10 (`trip6_rearm_check.py`) are witnessed by the
PCAN, and were judged on host receipt time (perf_counter at recv). With libpcanbasic in
RAW mode, msg.timestamp is the PCAN's device time: an uptime base, not the epoch, 1 us
granularity, monotonic (CAN replay tooling, 2026-11-11: residual against the Kvaser's
hardware stamps of the same frames p50 50 / p99 94 / max 101 us; rate +0.8 ppm against
perf_counter). Its times are mapped onto the host's perf_counter clock, which the page
polls, the posts and the bursts use, by the median offset over every message the PCAN
received, as nonode_bench.clock_offset does. In COOKED mode msg.timestamp is host receipt
time on the epoch clock, which spec 12 does not allow, and the stamps themselves are the
only evidence of the mode, so that is VOID whatever is believed installed.

  device_fit(points)  points [(perf_rx, epoch_rx, ts)] -> the fit: n, offset_s, epoch_lag_s,
                      the residual (receipt minus mapped) p50/p99/max and its p10-p90
                      spread, slope_ppm, the bounds, and `void` (None, or why it is VOID:
                      no message; the stamps on the epoch clock; fewer than MIN_POINTS;
                      |slope| over SLOPE_MAX_PPM; residual p99 over RESIDUAL_P99_MAX_S)
  mapped(fit, ts)     a PCAN stamp on the perf_counter clock
  gap_agrees(a, b)    a judged gap from mapped stamps and the receipt-time gap the live
                      sequence paced on are within RESIDUAL_P99_MAX_S
  fit_line(fit)       one line for the tool's output, the bounds beside the figures
The bounds are harness bounds, well clear of the measured figures and well inside the
tightest threshold judged on them (B10's 0.5 s) (reviewer, 2026-11-11).
"""
import math

MAX_LAG_S = 2.0
HW = "kvaser_hw"
RECEIPT = "receipt (hw not epoch)"
NONE = "none"
WHY = ("spec 12: the timing witness timestamps at the wire, in hardware; the Kvaser's "
       "hardware stamps were not on the epoch clock, so its times are host receipt times "
       "and nothing is judged on them (gen-inhibit reviewer, A2 (b), 2026-11-11)")


class ReceiptClock(Exception):
    """A check reached a judgement with the witness on receipt time."""

    def __init__(self, note):
        super().__init__("witness clock is %r: not judged on receipt time" % (note,))
        self.note = note


def hw_note(lag, digits=4):
    return "%s (median receipt lag %.*f s)" % (HW, digits, lag)


def judgeable(note):
    return isinstance(note, str) and (note == NONE or note.startswith(HW + " "))


def require(note):
    if not judgeable(note):
        raise ReceiptClock(note)


def void_verdict(note):
    return "VOID: witness clock %r, not judged (%s)" % (note, WHY)


def void(rec, note):
    rec["verdict"] = void_verdict(note)
    rec["why"] = "witness on receipt time: %s" % (note,)
    rec["witness_clock_void"] = note
    return rec


# ------------------------------------------------------------ the PCAN's stamps

EPOCH_LAG_S = 2.0            # stamps this close to time.time() at receipt are host time
RESIDUAL_P99_MAX_S = 0.050   # host delivery latency measured p99 ~3 ms, max ~9 ms
SLOPE_MAX_PPM = 100.0        # measured +0.8 ppm against perf_counter
MIN_POINTS = 20
BOUNDS = {"residual_p99_max_s": RESIDUAL_P99_MAX_S, "slope_max_ppm": SLOPE_MAX_PPM,
          "min_points": MIN_POINTS, "epoch_lag_min_s": EPOCH_LAG_S}
PCAN_WHY = ("spec 12: the timing witness timestamps at the wire, in hardware; the PCAN's "
            "stamps do not map onto the host clock within the harness bounds, so nothing is "
            "judged on them (gen-inhibit reviewer, A2 (a), 2026-11-11)")


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _rank(s, q):
    """The nearest-rank q quantile of the sorted list s."""
    return s[min(len(s) - 1, max(0, int(math.ceil(q * len(s))) - 1))]


def device_fit(points):
    fit = {"n": len(points), "bounds": dict(BOUNDS), "void": None}
    if not points:
        fit["void"] = "the PCAN received no message to fit its clock on"
        return fit
    epoch_lag = _median(e - ts for _p, e, ts in points)
    off = _median(p - ts for p, _e, ts in points)
    signed = sorted(p - (ts + off) for p, _e, ts in points)
    res = sorted(abs(r) for r in signed)
    xs = [ts for _p, _e, ts in points]
    ys = [p for p, _e, _ts in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    slope_ppm = None if var == 0 else (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var - 1.0) * 1e6
    fit.update(epoch_lag_s=round(epoch_lag, 4), offset_s=off,
               residual_p50_s=round(_rank(res, 0.50), 6), residual_p99_s=round(_rank(res, 0.99), 6),
               residual_max_s=round(res[-1], 6),
               residual_spread_10_90_s=round(_rank(signed, 0.90) - _rank(signed, 0.10), 6),
               slope_ppm=None if slope_ppm is None else round(slope_ppm, 3))
    if abs(epoch_lag) < EPOCH_LAG_S:
        fit["void"] = ("the PCAN's stamps are on the host's epoch clock (median %.4f s behind "
                       "receipt): host receipt time, not the PCAN's device time" % epoch_lag)
    elif len(points) < MIN_POINTS:
        fit["void"] = "%d PCAN messages, fewer than %d to fit its clock on" % (len(points), MIN_POINTS)
    elif slope_ppm is None or abs(slope_ppm) > SLOPE_MAX_PPM:
        fit["void"] = "slope %s ppm against perf_counter, over %g" % (fit["slope_ppm"], SLOPE_MAX_PPM)
    elif fit["residual_p99_s"] > RESIDUAL_P99_MAX_S:
        fit["void"] = "residual p99 %.4f s, over %g s" % (fit["residual_p99_s"], RESIDUAL_P99_MAX_S)
    return fit


def mapped(fit, ts):
    return ts + fit["offset_s"]


def gap_agrees(a, b):
    return a is not None and b is not None and abs(a - b) <= RESIDUAL_P99_MAX_S


def fit_line(fit):
    if fit.get("offset_s") is None:
        return "pcan clock: n %d, no fit" % fit["n"]
    return ("pcan clock: n %d, offset %.6f s, epoch lag %.4f s (VOID under %g), residual p50 %.6f / "
            "p99 %.6f s (bound %g) / max %.6f s, p10-p90 %.6f s, slope %s ppm (bound %g)"
            % (fit["n"], fit["offset_s"], fit["epoch_lag_s"], EPOCH_LAG_S, fit["residual_p50_s"],
               fit["residual_p99_s"], RESIDUAL_P99_MAX_S, fit["residual_max_s"],
               fit["residual_spread_10_90_s"], fit["slope_ppm"], SLOPE_MAX_PPM))


def pcan_void_verdict(why):
    return "VOID: PCAN clock %s, not judged (%s)" % (why, PCAN_WHY)
