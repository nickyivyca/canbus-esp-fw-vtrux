"""CAN1's witness for slcan_spi's jamming checks (x2_test_images SPEC T13),
and the pure judges of what it saw. NOT FOR VEHICLE (SPEC T14).

The witness is the bench's Kvaser on CAN1, in NORMAL mode (a silent Kvaser
reports no error frames whatever happens). Since 2026-11-13 it is the
kernel's kvaser_usb, netdev kvaser, opened with python-can's socketcan
after its link reads up in sysfs (open_kvaser, link_up; the
charge-interposer reviewer's T13 and C). Until then it was python-can's
"kvaser" interface (linuxcan CANlib) through a can-tools pin's canbench.
Its stamps are software stamps on the host's epoch clock, which clock_ok
accepts as before. An error frame that reports only a controller state
change, by its CAN_ERR class (state_only: CRTL, BUSOFF or RESTARTED with no
wire class), is left out of counts and of the lag and counted apart
(state_changes); kvaser_usb gives one wire-class error frame, 0x288, per
jam pulse. As the gen-inhibit tester's jammer checks do
(x2_jammer.py, witness_clock.py, agreed 2026-11-13): delivery is judged
from what the witness saw, never from what was commanded; the times judged
are msg.timestamp (on kvaser_usb the kernel's receipt stamp: it has no
device stamps), accepted only when their median lag behind host receipt is
under MAX_LAG_S, else the check is VOID, and only in windows at least
MARGIN_MIN times the stamps' measured error (stamp_margin, 2026-11-13);
and state-only error frames stamped by the host, should a canbench backend
make them, are left out of counts and of the lag.

A witness that stops hearing must never read as a quiet bus (the gen-inhibit
tester's review, 2026-11-13): the collecting thread keeps any exception,
err_times() raises Dead if the thread failed, never ran or had ended before
finish(), and every check ends with one closing pulse the witness must
count exactly once (judge_closing), so each run shows its own witness heard.

Pure functions (test_witness.py, with its mutants):
  clock_ok(pairs)                (device, host) stamps -> (ok, median lag)
  clusters(ts, gap_s)            error-frame times -> groups, one per pulse
  judge_stop(err_ts, deadline, window_s, quiet_from, quiet_to)
  stop_windows(t_from, t_stop, shift_s, quiet_s)  judge_stop's windows
  judge_count(err_ts, t0, t1, want)
  judge_groups(err_ts, t0, t1, n, interval_s)
  judge_closing(err_ts, t0, t1)  the closing pulse: exactly one error frame
  stamp_margin(what, tol_s, stamps), margins_ok(entries)
                                 a judged tolerance against the host stamps'
                                 error (STAMP_ERR_S), at least MARGIN_MIN
  real_channel(info)             refuses CANlib's virtual channels (CANlib only)
  state_only(can_id)             a state-change error frame, by CAN_ERR class
  link_up(netdev, root)          IFF_UP in sysfs
Witness(bus) is tested on a fake bus there too; open_kvaser() opens the
bench's Kvaser for it.
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
GROUP_TOL_S = 0.05   # judge_groups: each interval within this of the asked one
LEAD_S = 0.05        # a count window opens this long before its command's send

# The stamps judged are host stamps: kvaser_usb has none of its own, and
# python-can's socketcan gives the kernel's receipt time. Their error, the
# charge-interposer tester's measurement of kvaser_usb on madhouse-debian
# (M3/M4, 2026-11-13): about 1.5 ms worst. The ruling (the gen-inhibit
# reviewer, relayed by the charge-interposer reviewer, 2026-11-13): a window
# judged on these stamps must be at least MARGIN_MIN times their error;
# anything tighter goes to the PCAN or to counts. Each check's detail records
# its margins (stamp_margin), and a check with one under MARGIN_MIN is not run.
STAMP_ERR_S = 0.0015
MARGIN_MIN = 10.0


class Void(Exception):
    """The witness's stamps are not on the host's epoch clock."""


class Dead(Exception):
    """The witness did not hear the whole check: its thread failed, never
    ran, or had ended before finish()."""


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


def stop_windows(t_from, t_stop, shift_s=0.0, quiet_s=3.0):
    """judge_stop's keywords {deadline, quiet_from, quiet_to} for delivery
    from t_from that must stop at t_stop: the quiet window runs quiet_s past
    t_stop, past slcan_spi_a's backstop at deadline + 2 s. The can-fail
    judges the same record against a deadline shift_s early."""
    return {"deadline": t_stop - shift_s,
            "quiet_from": t_from,
            "quiet_to": t_stop + quiet_s}


def judge_closing(err_ts, t0, t1):
    """The closing pulse, sent after a check's last window: exactly one
    error frame in [t0, t1] shows this run's witness heard CAN1. -> (ok, why)"""
    n = sum(1 for t in err_ts if t0 <= t <= t1)
    return n == 1, "%d error frames for the closing pulse" % n


def judge_count(err_ts, t0, t1, want):
    """Exactly want error frames in [t0, t1]? -> (ok, n)."""
    n = sum(1 for t in err_ts if t0 <= t <= t1)
    return n == want, n


def stamp_margin(what, tol_s, stamps=1):
    """One judged tolerance against the host stamps' error: tol_s, the
    narrowest distance the judgement rests on, read from `stamps` stamps (1:
    a stamp against an edge on the host's clock; 2: the gap between two
    stamps, each with its own error). -> the check detail's entry, with ok
    when the margin (tol_s over the error) is at least MARGIN_MIN."""
    err = stamps * STAMP_ERR_S
    margin = tol_s / err
    return {"what": what, "tolerance_s": tol_s, "stamps": stamps,
            "stamp_error_s": round(err, 4), "margin": round(margin, 1),
            "ok": margin >= MARGIN_MIN - 1e-9}


def margins_ok(entries):
    """-> (ok, why): every stamp_margin entry at least MARGIN_MIN; why names
    those under it. No entries is not ok: a check judged on stamps states
    what it judged."""
    if not entries:
        return False, "no stamp margins stated"
    low = ["%s: %.1fx" % (e["what"], e["margin"]) for e in entries if not e["ok"]]
    if low:
        return False, "stamp margin under %gx: %s" % (MARGIN_MIN, "; ".join(low))
    return True, "every stamp margin at least %gx (stamp error %.4f s)" % (MARGIN_MIN, STAMP_ERR_S)


def judge_groups(err_ts, t0, t1, n, interval_s, tol_s=GROUP_TOL_S):
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


def real_channel(info):
    """CANlib's channel info -> (ok, why). With the Leaf missing, CANlib
    still lists its 'Kvaser Virtual CAN' channels, and channel 0 then opens
    a virtual channel without a word (the gen-inhibit tester, on the bench,
    2026-11-13), which would see nothing and read as a silent bus. So a
    virtual or unnamed channel is refused. (CANlib only: since 2026-11-13
    open_kvaser opens the kernel's kvaser_usb, checked by link_up.)"""
    name = (info or {}).get("device_name") or ""
    if not name.strip():
        return False, "channel has no device name"
    if "virtual" in name.lower():
        return False, "channel is %r, not the bench's Kvaser" % name
    return True, name


# SocketCAN's error classes (linux/can/error.h), in an error frame's id
CAN_ERR_TX_TIMEOUT, CAN_ERR_LOSTARB, CAN_ERR_CRTL = 0x1, 0x2, 0x4
CAN_ERR_PROT, CAN_ERR_TRX, CAN_ERR_ACK = 0x8, 0x10, 0x20
CAN_ERR_BUSOFF, CAN_ERR_BUSERROR, CAN_ERR_RESTARTED = 0x40, 0x80, 0x100
# a controller STATE change: error warning / passive, bus-off, restarted
STATE_CLASSES = CAN_ERR_CRTL | CAN_ERR_BUSOFF | CAN_ERR_RESTARTED
# an error SEEN ON THE WIRE, what a jam pulse makes (kvaser_usb reports one
# 0x288, BUSERROR | PROT | CNT, per pulse: the gen-inhibit tester's probe,
# 2026-11-13, 11 of 11)
WIRE_CLASSES = CAN_ERR_LOSTARB | CAN_ERR_PROT | CAN_ERR_TRX | CAN_ERR_ACK | CAN_ERR_BUSERROR


def state_only(can_id):
    """An error frame that reports only a controller state change, by its
    CAN_ERR class (the charge-interposer reviewer's C, 2026-11-13): a state
    class and no wire class. It is not a pulse on the wire: counted, it
    would make one pulse two, or a pulse where a state only recovered."""
    return bool(can_id & STATE_CLASSES) and not (can_id & WIRE_CLASSES)


# ---------------------------------------------------------------- the Kvaser

NETDEV = "kvaser"
SYSFS_NET = "/sys/class/net"
IFF_UP = 0x1


def _read(path):
    with open(path) as f:
        return f.read()


def link_up(netdev=NETDEV, root=SYSFS_NET, read=_read):
    """-> (ok, why): the netdev's IFF_UP read in sysfs; a missing netdev or
    unreadable flags is not up. read: a stand-in (test_witness.py)."""
    p = os.path.join(root, netdev, "flags")
    try:
        flags = int(read(p).strip(), 16)
    except (OSError, ValueError) as e:
        return False, "%s's link state cannot be read (%s: %s)" % (netdev, p, e)
    if not flags & IFF_UP:
        return False, "%s's link reads down in sysfs" % netdev
    return True, "%s up (IFF_UP read back)" % netdev


def open_kvaser(canbench_folder=None, channel=NETDEV, bitrate=500000, root=SYSFS_NET,
                can_mod=None, read=_read):
    """The bench's Kvaser on CAN1 as a Witness: the kernel's kvaser_usb,
    netdev kvaser by name, python-can's socketcan (since 2026-11-13, when
    canctl's owner moved the Kvaser off linuxcan's leaf; the
    charge-interposer reviewer's T13: the same SocketCAN path as the
    interposer's harness). The caller brings the link up, normal mode at
    500 kbit/s (slcan_spi_bench: canctl up kvaser 500000); IFF_UP is read
    back here, and a link not up, or any netdev but kvaser, is refused.
    canbench_folder and bitrate are no longer read."""
    if channel != NETDEV:
        raise RuntimeError("CAN1 witness refused: netdev %r, not %r" % (channel, NETDEV))
    ok, why = link_up(channel, root, read)
    if not ok:
        raise RuntimeError("CAN1 witness refused: %s" % why)
    if can_mod is None:
        import can as can_mod
    bus = can_mod.Bus(interface="socketcan", channel=channel, receive_own_messages=False)
    return Witness(bus, {"device_name": "%s (kvaser_usb, socketcan)" % channel}, None)


class Witness(threading.Thread):
    """Collects a bus's frames on CAN1: errors [(device t, host t)] and
    frames [(device t, host t, id)]. bus: python-can's, recv() and
    shutdown()."""

    def __init__(self, bus, channel_info=None, canbench_file=None):
        super().__init__(daemon=True)
        self.bus = bus
        self.channel_info = channel_info
        self.canbench_file = canbench_file
        self.errors = []
        self.frames = []
        self.host_stamped = 0
        self.state_changes = 0          # error frames left out by CAN_ERR class
        self.failed = None
        self.alive_at_finish = None     # None until finish()
        self.lag = None
        self.stop_ev = threading.Event()

    def run(self):
        try:
            while not self.stop_ev.is_set():
                m = self.bus.recv(0.05)
                if m is None:
                    continue
                h = time.time()
                if getattr(m, "host_stamped", False):
                    self.host_stamped += 1
                    continue
                if m.is_error_frame and state_only(m.arbitration_id):
                    self.state_changes += 1
                    continue
                if m.is_error_frame:
                    self.errors.append((m.timestamp, h))
                else:
                    self.frames.append((m.timestamp, h, m.arbitration_id))
        except Exception as e:
            self.failed = "%s: %s" % (type(e).__name__, e)

    def finish(self):
        """Stops the thread, noting first whether it was still running."""
        self.alive_at_finish = self.is_alive()
        self.stop_ev.set()
        if self.alive_at_finish:
            self.join(2)
        self.bus.shutdown()
        return self

    def err_times(self):
        """The error frames' device times. Raises Dead if the witness did
        not hear the whole check, Void if its clock is not the host's epoch
        clock."""
        if self.failed:
            raise Dead("witness thread failed: %s" % self.failed)
        if not self.alive_at_finish:
            raise Dead("witness thread %s" % (
                "not finished yet" if self.alive_at_finish is None
                else "had ended before finish() (or never ran)"))
        ok, lag = clock_ok(self.errors + [(d, h) for d, h, _ in self.frames])
        self.lag = lag
        if not ok:
            raise Void("witness stamps lag host receipt by %.3f s (limit %.1f)" % (lag, MAX_LAG_S))
        return [d for d, _ in self.errors]

    def summary(self):
        """For every check's detail (gen-inhibit tester's review point 3)."""
        pairs = self.errors + [(d, h) for d, h, _ in self.frames]
        lag = clock_ok(pairs)[1]
        return {"median_lag_s": None if lag is None else round(lag, 4),
                "errors": len(self.errors), "frames": len(self.frames),
                "host_stamped": self.host_stamped,
                "state_changes": self.state_changes, "failed": self.failed,
                "alive_at_finish": self.alive_at_finish,
                "channel": (self.channel_info or {}).get("device_name")}
