"""Spec 8.2 on the wire: the status-frame cadence and the immediate 0x7F4.

Spec 8.2 at 826925f461768701 (user, 2026-10-13): "Each of the four is sent
at ~1 Hz -- all four go out together once per second, back to back, ...
each within 10 % of 1 s after its last. Plus an immediate STATUS on every
state change, within 50 ms of the state change's event line on the serial
output (8.3)." Read per the reviewer's rulings the same day:

  1. the immediate 0x7F4 is NOT 0x7F4's "last" for the cadence: it is left
     out of the cadence gaps and judged only by the 50 ms rule. The +/-10 %
     applies between consecutive periodic bursts, frame by frame;
  2. no bound on the spread inside a burst (recorded); each burst carries
     all four frames, with no frame of another burst between them;
  3. |0x7F4 - line| <= 50 ms, either direction;
  4. a state change is an 8.3 event line whose STATE differs from the
     previous event line's STATE (other event lines, REPEAT start/end among
     them, are not); the matching 0x7F4 carries the NEW state in its state
     byte (the diag DBC's intp_state), and one in time with the old state
     fails.

THE CLOCKS (measurement plan accepted by the reviewer, 2026-10-13). A
passive PCAN on the vehicle segment logs every 0x7F4-0x7F7 with its device
timestamp and its host arrival time; run_scenario's SerialTap logs every
serial line with its host arrival time; both host times are this process's
time.perf_counter(). Cadence is judged on the PCAN's DEVICE clock. The 50 ms
rule is judged on the HOST clock: each frame's device time is mapped to host
time by the minimum host-minus-device offset of its 30 s window (USB delivery
only ever delays a frame, so the least-delayed one is the truest pairing;
windowed, so the adapter's drift over a 1.5 h run does not accumulate). The
serial line's own USB latency is IN that figure and is not removed; it is
stated in every result.

Passive (no ACK) on purpose: the witness must add nothing to the segment.
The Kvaser (vehicle_sim's adapter) acknowledges.

Tester, 2026-10-13. diag_cadence_test.py drives each rule to fail.
"""

import bisect
import os
import re
import threading
import time

STATUS_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
PERIOD_S = 1.0
PERIOD_TOL = 0.10
IMMEDIATE_S = 0.050
MAP_WINDOW_S = 30.0

# the board's 8.3 event line, "[t] STATE event a= b= c=", and
# interposer_sim's "HH:MM:SS intp [STATE] event" (the same events)
BOARD_EVENT = re.compile(r"^\[\s*\d+(?:\.\d+)?\]\s+([A-Z_]+)\s+(\S.*)$")
SIM_EVENT = re.compile(r"\[([A-Z_]+)\]\s+(\S.*)$")


def load_state_names():
    """{code: name} of intp_state from the diag DBC (decode with cantools,
    never by hand: the DBC is the published interface)."""
    import cantools
    import paths
    db = cantools.database.load_file(
        os.path.join(paths.data_dir(), "vtrux-interposer-diag.dbc"))
    sig = db.get_message_by_frame_id(0x7F4).get_signal_by_name("intp_state")
    return db, {int(k): str(v) for k, v in sig.choices.items()}


def state_of(db, data):
    v = db.decode_message(0x7F4, bytes(data), decode_choices=True)
    return str(v["intp_state"])


# --- capture ---------------------------------------------------------------

class DiagWitness(object):
    """A passive PCAN on the vehicle segment logging every 0x7F4-0x7F7:
    'host<TAB>device<TAB>id<TAB>hex' per line, host = time.perf_counter()
    at recv. Drained throughout."""

    def __init__(self, path, channel="PCAN_USBBUS1", bitrate=500000):
        self.path, self.channel, self.bitrate = path, channel, bitrate
        self._stop = threading.Event()
        self._t = None
        self.n = 0
        self.error = None

    def start(self):
        import can
        self.bus = can.Bus(interface="pcan", channel=self.channel,
                           bitrate=self.bitrate, state=can.BusState.PASSIVE)
        self.fh = open(self.path, "w")
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                m = self.bus.recv(timeout=0.2)
            except Exception as exc:                       # noqa: BLE001
                self.error = str(exc)
                return
            host = time.perf_counter()
            if m is None or m.arbitration_id not in STATUS_IDS:
                continue
            self.fh.write("%.6f\t%.6f\t%03X\t%s\n" % (
                host, m.timestamp, m.arbitration_id, bytes(m.data).hex()))
            self.n += 1

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=3.0)
        try:
            self.bus.shutdown()
        finally:
            self.fh.close()


def read_witness(path):
    rows = []
    for ln in open(path):
        f = ln.rstrip("\n").split("\t")
        if len(f) == 4:
            rows.append((float(f[0]), float(f[1]), int(f[2], 16),
                         bytes.fromhex(f[3])))
    return rows


def read_stamped(path):
    """SerialTap's stamped copy: 'host<TAB>line'."""
    out = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        h, _, line = ln.rstrip("\n").partition("\t")
        try:
            out.append((float(h), line))
        except ValueError:
            pass
    return out


# --- scoring: pure ---------------------------------------------------------

def group(rows):
    """rows sorted by device time -> (bursts, immediates, stray). A burst
    is four consecutive frames carrying all four IDs; a 0x7F4 that does
    not start one is an immediate; any other frame outside a burst is
    stray (a burst with a frame missing or another burst's frame inside)."""
    bursts, imm, stray = [], [], []
    i = 0
    while i < len(rows):
        four = rows[i:i + 4]
        if len(four) == 4 and sorted(r[2] for r in four) == list(STATUS_IDS):
            bursts.append(four)
            i += 4
        elif rows[i][2] == 0x7F4:
            imm.append(rows[i])
            i += 1
        else:
            stray.append(rows[i])
            i += 1
    return bursts, imm, stray


def host_map(rows):
    """-> f(device) -> host time, from each MAP_WINDOW_S window's minimum
    host-minus-device offset, interpolated between window centres."""
    if not rows:
        return lambda d: d
    d0 = rows[0][1]
    win = {}
    for h, d, _i, _b in rows:
        k = int((d - d0) // MAP_WINDOW_S)
        off = h - d
        if k not in win or off < win[k][1]:
            win[k] = (d, off)
    pts = sorted(win.values())
    xs = [p[0] for p in pts]

    def f(d):
        j = bisect.bisect_left(xs, d)
        if j <= 0:
            return d + pts[0][1]
        if j >= len(pts):
            return d + pts[-1][1]
        (x0, o0), (x1, o1) = pts[j - 1], pts[j]
        return d + o0 + (o1 - o0) * (d - x0) / (x1 - x0)
    return f


def state_changes(serial_rows, names):
    """-> [(host, old, new, line)] for each event line whose STATE differs
    from the previous event line's. Lines whose STATE token is not a
    state name are not event lines."""
    out, prev = [], None
    for h, line in serial_rows:
        m = BOARD_EVENT.search(line) or SIM_EVENT.search(line)
        if not m or m.group(1) not in names:
            continue
        st = m.group(1)
        if prev is not None and st != prev:
            out.append((h, prev, st, line))
        prev = st
    return out


def score(witness_rows, serial_rows, db=None, names=None):
    """-> (ok, why). `db` decodes 0x7F4's state; tests pass a stand-in."""
    if db is None:
        db, codes = load_state_names()
        names = set(codes.values())
    probs, notes = [], []
    rows = sorted(witness_rows, key=lambda r: r[1])
    if not rows:
        return (False, "8.2: no status frame on the witness")
    bursts, imm, stray = group(rows)
    if stray:
        probs.append("%d frame(s) outside a complete burst of four, first "
                     "0x%03X at device t=%.3f s" % (len(stray), stray[0][2],
                                                    stray[0][1]))
    bad_gaps = []
    gaps_all = []
    for a, b in zip(bursts, bursts[1:]):
        ta = {r[2]: r[1] for r in a}
        tb = {r[2]: r[1] for r in b}
        for fid in STATUS_IDS:
            g = tb[fid] - ta[fid]
            gaps_all.append(g)
            if abs(g - PERIOD_S) > PERIOD_TOL * PERIOD_S + 1e-9:
                bad_gaps.append((fid, ta[fid], g))
    if bad_gaps:
        probs.append("%d status-frame gap(s) outside %.1f s +/- %.0f %%, "
                     "first 0x%03X %.3f s after its last at device t=%.3f s"
                     % (len(bad_gaps), PERIOD_S, PERIOD_TOL * 100,
                        bad_gaps[0][0], bad_gaps[0][2], bad_gaps[0][1]))
    spreads = sorted((max(r[1] for r in bb) - min(r[1] for r in bb)) * 1000
                     for bb in bursts)
    if gaps_all:
        notes.append("%d bursts, gaps %.3f-%.3f s; spread inside a burst "
                     "p50 %.2f ms, max %.2f ms (recorded, not bounded)"
                     % (len(bursts), min(gaps_all), max(gaps_all),
                        spreads[len(spreads) // 2], spreads[-1]))
    to_host = host_map(rows)
    imm_host = [(to_host(r[1]), r) for r in imm]
    every7f4 = [(to_host(r[1]), r) for r in rows if r[2] == 0x7F4]
    late = []
    for h, old, new, line in state_changes(serial_rows, names):
        near = [(abs(th - h), th - h, state_of(db, r[3]))
                for th, r in imm_host if abs(th - h) <= IMMEDIATE_S + 1e-9]
        good = [x for x in near if x[2] == new]
        if good:
            continue
        nearest = min(((abs(th - h), th - h, state_of(db, r[3]))
                       for th, r in every7f4 if state_of(db, r[3]) == new),
                      default=None)
        late.append((h, old, new, near, nearest))
    n_chg = len(state_changes(serial_rows, names))
    if late:
        h, old, new, near, nearest = late[0]
        probs.append("%d of %d state change(s) without a 0x7F4 carrying the "
                     "new state within %.0f ms of the event line; first %s -> "
                     "%s at host t=%.3f s: %s" % (
                         len(late), n_chg, IMMEDIATE_S * 1000, old, new, h,
                         ("an immediate 0x7F4 in time carried %s"
                          % near[0][2]) if near else
                         ("the nearest 0x7F4 with %s was %+.1f ms away"
                          % (new, nearest[1] * 1000)) if nearest else
                         "no 0x7F4 ever carried %s" % new))
    else:
        notes.append("%d state change(s), each with a 0x7F4 carrying the new "
                     "state within %.0f ms (host clock; the serial line's "
                     "USB latency is in the figure)" % (n_chg,
                                                        IMMEDIATE_S * 1000))
    if probs:
        return (False, "8.2: " + "; ".join(probs))
    return (True, "8.2: " + "; ".join(notes))
