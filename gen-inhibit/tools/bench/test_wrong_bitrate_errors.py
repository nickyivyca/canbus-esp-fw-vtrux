"""Harness test for wrong_bitrate_errors: schedule parsing, the sliding 10 s window,
per-burst attribution.

Since 2026-11-20 (gen-inhibit reviewer's answer to the run (C) report, item 4(ii)) each burst's
count runs over burst_measures.burst_window, its start to the next start or its close + TAIL_S,
whichever is first: the last burst's window ran 0.5 s from its start, so with canmain's
1.2-1.5 s open inside the burst it counted none (re-run b840's last burst: 0, its measure n 3).

    python3.13 -B gen-inhibit/tools/bench/test_wrong_bitrate_errors.py   (from the repo root)
Mutants: test_wrong_bitrate_errors_mutants.py (WRONG_BITRATE_DIR runs another copy of the tool).
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("WRONG_BITRATE_DIR"):
    sys.path.insert(0, os.environ["WRONG_BITRATE_DIR"])
import wrong_bitrate_errors as W                                   # noqa: E402


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def b840_last_burst():
    """b840's shape: a 20 ms burst whose open took 1.2735 s, its error frames from bus-on
    (1.2724 s) to 29.8 ms later, the close 1.2998 s after its start. Counted: all 3."""
    t = 100.0
    errs = [t + 1.2724, t + 1.2860, t + 1.3022]
    return W.per_burst([90.0, t], [91.3, t + 1.2998], errs) == [0, 3]


def main_passes_closes():
    """main() counts each burst with its own close, from the same (start, ms, close) list the
    burst measures use."""
    src = inspect.getsource(W.main)
    return "per_burst(st, [c_ for _s, _ms, c_ in starts], errs)" in src


class FakeSrc:
    """A source bus whose recv hands out queued (delay, error?, can_id, data) frames on a fake clock."""

    def __init__(self, frames, clock):
        self.frames, self.clock, self.timeouts = list(frames), clock, []

    def recv(self, timeout=None):
        self.timeouts.append(timeout)
        if self.frames:
            dt, err, cid, data = self.frames.pop(0)
            self.clock[0] += dt
            import types
            return types.SimpleNamespace(is_error_frame=err, arbitration_id=cid, data=data)
        self.clock[0] += timeout
        return None


def source_cases():
    """The source's own state per burst (the gen-inhibit reviewer's ruling of 2026-11-20, item 4(ii)):
    hold_reading on a fake clock, source_state's classes (Linux can/error.h)."""
    out = []
    clock = [100.0]
    src = FakeSrc([(0.0002, True, 0x004, bytes([0, 0x08, 0, 0, 0, 0, 0, 0])),     # CRTL: tx_warning
                   (0.0003, True, 0x004, bytes([0, 0x20, 0, 0, 0, 0, 0, 0])),     # CRTL: tx_passive
                   (0.0004, False, 0x123, bytes(8)),
                   (0.0010, True, 0x040, bytes(8)),                               # BUSOFF at +1.9 ms
                   (0.0005, True, 0x100, bytes(8))], clock)                       # RESTARTED
    errs, other = W.hold_reading(src, 100.005, perf=lambda: clock[0])
    out.append(("hold_reading: the error frames with their times, the other frames counted, until the deadline",
                len(errs) == 4 and other == 1 and abs(errs[2][0] - 100.0019) < 1e-9 and clock[0] >= 100.005
                and all(t <= W.SOURCE_READ_S + 1e-12 for t in src.timeouts), (errs, other, clock[0])))
    s = W.source_state(errs, other, 99.999)
    out.append(("source_state: bus-off and when (2.9 ms from the burst's start), one restart, the states",
                s["bus_off"] and s["bus_off_at_ms"] == 2.9 and s["restarts"] == 1
                and s["ctrl_states"] == ["tx_passive", "tx_warning"] and s["error_frames"] == 4
                and s["other_frames"] == 1, s))
    q = W.source_state([], 0, 1.0)
    out.append(("source_state: nothing read -> not bus-off, no restart, no states",
                not q["bus_off"] and q["bus_off_at_ms"] is None and q["restarts"] == 0 and q["ctrl_states"] == []
                and q["error_frames"] == 0, q))
    out.append(("source_state: a bus error (not BUSOFF/RESTARTED/CRTL) is counted only",
                (lambda r: not r["bus_off"] and r["restarts"] == 0 and r["ctrl_states"] == [] and r["error_frames"] == 1)(
                    W.source_state([(1.0, 0x088, "00" * 8)], 0, 1.0)), ""))
    clock2 = [5.0]
    e2, o2 = W.hold_reading(FakeSrc([], clock2), 5.0, perf=lambda: clock2[0])
    out.append(("hold_reading: a deadline already past reads nothing", e2 == [] and o2 == 0 and clock2[0] == 5.0, ""))
    line = W.burst_detail_line({"source": s}, {"span_ms": 2.494, "first_s": 1.27, "last_s": 1.2725, "asked_ms": 20.0,
                                               "last_after_close_ms": -17.5, "n": 32})
    out.append(("burst_detail_line: the measure, recorded not judged, and the source's bus-off",
                "span 2.494 ms" in line and "recorded, not judged" in line and "bus_off True (at 2.9 ms)" in line
                and "restarts 1" in line, line))
    out.append(("burst_detail_line: no measure (clock VOID) says so",
                "not measured (the clock fit is VOID)" in W.burst_detail_line({"source": q}, None), ""))
    return out


def main():
    pb = W.per_burst
    checks = [
        ("bursts parse in time order", W.parse_bursts("3:5,2:1") == [(2.0, 1.0), (3.0, 5.0)]),
        ("a zero-length burst is refused", _raises(lambda: W.parse_bursts("2:0"))),
        ("10 errors 1 s apart: 10 within 10 s (inclusive edge)",
         W.window_max([float(i) for i in range(10)]) == 10),
        ("10 errors 1.2 s apart: at most 9 within 10 s",
         W.window_max([i * 1.2 for i in range(10)]) == 9),
        ("exactly 10.0 s between the 1st and 10th counts (spec 7: inclusive)",
         W.window_max([0.0] + [10.0 * k / 9 for k in range(1, 10)]) == 10),
        ("errors split by burst window: the next start ends a burst's window",
         pb([1.0, 2.0], [1.8, 2.1], [1.1, 1.95, 2.05, 2.7]) == [2, 1]),
        ("errors before the first burst are not attributed", pb([1.0], [1.4], [0.5, 1.2]) == [1]),
        ("the last burst counts to its close + 0.5 s, not 0.5 s from its start",
         pb([1.0], [2.3], [2.2, 2.79]) == [2]),
        ("the last burst's window ends at its close + 0.5 s (exclusive)", pb([1.0], [2.3], [2.8, 2.81]) == [0]),
        ("a burst's window ends at its close + 0.5 s when the next start is later",
         pb([1.0, 5.0], [1.3, 5.3], [1.79, 1.81, 4.9, 5.1]) == [1, 1]),
        ("the window starts at the burst's start (inclusive)", pb([1.0], [1.3], [1.0]) == [1]),
        ("b840's last burst (open 1.27 s inside it) counts its 3 error frames, not 0", b840_last_burst()),
        ("no bursts: no counts", pb([], [], [1.0]) == []),
        ("main counts each burst with its own close", main_passes_closes()),
    ] + [(label, ok) for label, ok, _d in source_cases()]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
