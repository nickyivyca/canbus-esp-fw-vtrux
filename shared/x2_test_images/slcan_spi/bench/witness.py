"""CAN1's witness for slcan_spi's jamming checks (x2_test_images SPEC T13),
and the pure judges of what it saw. NOT FOR VEHICLE (SPEC T14).

The witness is the bench's Kvaser on CAN1, in NORMAL mode (a silent Kvaser
reports no error frames whatever happens), opened with python-can's
"kvaser" interface (linuxcan CANlib) after can-tools'
canbench.live.linux_fixes.apply(), from a can-tools pin's canbench/ loaded
by file (AGENTS.md "can-tools"; the charge-interposer tester runs it with
the interposer's pin). As the gen-inhibit tester's jammer checks do
(x2_jammer.py, witness_clock.py, agreed 2026-11-13): delivery is judged
from what the witness saw, never from what was commanded; the times judged
are the Kvaser's device stamps (msg.timestamp), accepted only when their
median lag behind host receipt is under MAX_LAG_S, else the check is VOID;
and state-only error frames stamped by the host, should a canbench backend
make them, are left out of counts and of the lag.

Pure functions (test_witness.py, with its mutants):
  clock_ok(pairs)                (device, host) stamps -> (ok, median lag)
  clusters(ts, gap_s)            error-frame times -> groups, one per pulse
  judge_stop(err_ts, deadline, window_s, quiet_from, quiet_to)
  judge_count(err_ts, t0, t1, want)
  judge_groups(err_ts, t0, t1, n, interval_s)
"""

import importlib.util
import os
import statistics
import sys
import threading
import time

MAX_LAG_S = 2.0     # witness_clock.py's MAX_LAG_S (gen-inhibit tester)
STOP_WINDOW_S = 0.5  # last error frame within this of the deadline
GROUP_GAP_S = 0.2    # error frames closer than this are one pulse's


class Void(Exception):
    """The witness's stamps are not on the host's epoch clock."""


def load_canbench(folder):
    """canbench from <folder>/canbench, loaded by file; refuses a canbench
    already imported from anywhere else (AGENTS.md "can-tools")."""
    pkg = os.path.join(os.path.abspath(folder), "canbench")
    init = os.path.join(pkg, "__init__.py")
    if not os.path.isfile(init):
        raise RuntimeError("no canbench/ in %s" % folder)
    have = sys.modules.get("canbench")
    if have is not None:
        if os.path.realpath(getattr(have, "__file__", "")) != os.path.realpath(init):
            raise RuntimeError("canbench already imported from %s" % have.__file__)
        return have
    spec = importlib.util.spec_from_file_location(
        "canbench", init, submodule_search_locations=[pkg])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["canbench"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- judges

def clock_ok(pairs):
    """pairs: [(device stamp, host receipt)], both epoch seconds. OK when
    the median of host - device is between 0 - 0.1 s and MAX_LAG_S. No
    pairs: nothing to judge the clock by, so OK (an empty witness is judged
    by the counts)."""
    if not pairs:
        return True, None
    lag = statistics.median(h - d for d, h in pairs)
    return -0.1 <= lag < MAX_LAG_S, lag


def clusters(ts, gap_s=GROUP_GAP_S):
    """Sorted times -> groups [(first, last, count)]: a gap over gap_s
    starts a new group."""
    out = []
    for t in sorted(ts):
        if out and t - out[-1][1] <= gap_s:
            f, _, n = out[-1]
            out[-1] = (f, t, n + 1)
        else:
            out.append((t, t, 1))
    return out


def judge_stop(err_ts, deadline, window_s=STOP_WINDOW_S, quiet_from=None, quiet_to=None):
    """Did delivery stop at the deadline? -> (verdict, why).
    PASS: the last error frame up to quiet_to is within window_s of the
    deadline, and none falls between deadline + window_s and quiet_to (the
    window from the end of the command to the next, gen-inhibit tester
    2026-11-13). FAIL: an error frame after deadline + window_s.
    INCONCLUSIVE: none, or the last over window_s before the deadline (the
    witness may have stopped seeing; the Kvaser reports dense trains in
    bursts)."""
    quiet_to = quiet_to if quiet_to is not None else deadline + 2 * window_s
    ts = [t for t in err_ts if (quiet_from is None or t >= quiet_from) and t <= quiet_to]
    if not ts:
        return "INCONCLUSIVE", "no error frame seen"
    late = [t for t in ts if t > deadline + window_s]
    if late:
        return "FAIL", "%d error frames after deadline + %.1f s, the last %.3f s after" % (
            len(late), window_s, late[-1] - deadline)
    last = max(ts)
    if last < deadline - window_s:
        return "INCONCLUSIVE", "last error frame %.3f s before the deadline" % (deadline - last)
    return "PASS", "last error frame %+.3f s from the deadline" % (last - deadline)


def judge_count(err_ts, t0, t1, want):
    """Exactly want error frames in [t0, t1]? -> (ok, n)."""
    n = sum(1 for t in err_ts if t0 <= t <= t1)
    return n == want, n


def judge_groups(err_ts, t0, t1, n, interval_s, tol_s=0.05):
    """n groups of one error frame each in [t0, t1], interval_s apart
    (within tol_s)? -> (ok, why)."""
    g = clusters([t for t in err_ts if t0 <= t <= t1])
    if len(g) != n:
        return False, "%d groups, want %d" % (len(g), n)
    if any(c != 1 for _, _, c in g):
        return False, "group sizes %s, want all 1" % [c for _, _, c in g]
    gaps = [b[0] - a[0] for a, b in zip(g, g[1:])]
    off = [round(x, 3) for x in gaps if abs(x - interval_s) > tol_s]
    if off:
        return False, "intervals off %.3f s: %s" % (interval_s, off[:5])
    return True, "%d groups %.3f s apart" % (n, interval_s)


# ---------------------------------------------------------------- the Kvaser

class Witness(threading.Thread):
    """Collects the Kvaser's frames on CAN1: errors [(device t, host t)] and
    frames [(device t, host t, id)]."""

    def __init__(self, canbench_folder, channel=0, bitrate=500000):
        super().__init__(daemon=True)
        cb = load_canbench(canbench_folder)
        from canbench.live import linux_fixes
        linux_fixes.apply()
        import can
        self.bus = can.Bus(interface="kvaser", channel=channel, bitrate=bitrate)
        self.canbench_file = cb.__file__
        self.errors = []
        self.frames = []
        self.host_stamped = 0
        self.stop_ev = threading.Event()

    def run(self):
        while not self.stop_ev.is_set():
            m = self.bus.recv(0.05)
            if m is None:
                continue
            h = time.time()
            if getattr(m, "host_stamped", False):
                self.host_stamped += 1
                continue
            if m.is_error_frame:
                self.errors.append((m.timestamp, h))
            else:
                self.frames.append((m.timestamp, h, m.arbitration_id))

    def finish(self):
        self.stop_ev.set()
        self.join(2)
        self.bus.shutdown()
        return self

    def err_times(self):
        """The error frames' device times; raises Void if their clock is not
        the host's epoch clock."""
        ok, lag = clock_ok(self.errors + [(d, h) for d, h, _ in self.frames])
        if not ok:
            raise Void("witness stamps lag host receipt by %.3f s (limit %.1f)" % (lag, MAX_LAG_S))
        return [d for d, _ in self.errors]
