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
PCAN on the vehicle segment logs every 0x7F4-0x7F7 with its device
timestamp and its host arrival time; run_scenario's SerialTap logs every
serial line with its host arrival time; both host times are this process's
time.perf_counter(). Cadence is judged on the PCAN's DEVICE clock. The 50 ms
rule is judged on the HOST clock: each frame's device time is mapped to host
time by the minimum host-minus-device offset of its 30 s window (USB delivery
only ever delays a frame, so the least-delayed one is the truest pairing;
windowed, so the adapter's drift over a 1.5 h run does not accumulate). The
serial line's own USB latency is IN that figure and is not removed; it is
stated in every result.

It ACKNOWLEDGES by default (--diag-witness-mode acks; reviewer,
2026-10-14). It used to be passive so as to add nothing to the segment, on
the assumption that the Kvaser (vehicle_sim's adapter) acknowledged -- but
the Kvaser opens after the charger, and while the board is listen-only
nothing else on the bench's vehicle segment acknowledges. The board met the
first charger frame with no acknowledger and retried one frame ~6,700 times
in 2.2 s (step 4 item 2). An acknowledging witness stands in for the
truck's VCU and BMS and records the same frames; passive stays an option.

LISTEN-ONLY (spec 2.3 at d9f49ea6, user 2026-10-13). While the charger is
silent the vehicle port is listen-only and sends nothing: no status frames
except one round at power-up. It goes to normal on the first charger frame
(extended, source address 0xC0), switching BEFORE it forwards that frame,
and back after 2 s with no charger frame. So the witness, which also logs
the charger frames the board forwards onto the vehicle segment, can tell
the mode from the wire alone (ModeWindows): the 8.2 rules above are judged
inside normal-mode windows only (reviewer, 2026-10-13), and a status frame
outside them, other than the power-up round, is a failure of its own.

Tester, 2026-10-13. diag_cadence_test.py drives each rule to fail.
"""

import bisect
import os
import re
import threading
import time

STATUS_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
# The BMS frames the core times for staleness (spec 6), logged too so the
# vehicle stimulus premise and spec 9's hold on a board run read them off
# the wire (reviewer, 2026-10-15). Nothing that scores 8.2 reads them: score()
# keeps STATUS_IDS only.
BMS_IDS = (0x410, 0x420, 0x430, 0x440)
PERIOD_S = 1.0
PERIOD_TOL = 0.10
IMMEDIATE_S = 0.050
MAP_WINDOW_S = 30.0
# spec 2.3: "back to listen-only after 2 s with no charger frame". The
# slack covers the board's own 10 ms tick and a burst already going out as
# the 2 s ends; it is the harness's figure, not the spec's.
REVERT_S = 2.0
REVERT_SLACK_S = 0.1
CHARGER_SA = 0xC0


def is_charger_frame(fid):
    """Spec 2.3's valid charger frame, by identifier: extended (above the
    11-bit range) with source address 0xC0 in its low byte."""
    return fid > 0x7FF and (fid & 0xFF) == CHARGER_SA


class ModeWindows(object):
    """The vehicle port's mode as a witness on the vehicle segment sees it,
    fed in time order. charger(t) for each charger frame forwarded onto the
    segment; status(t, fid) -> 'normal' (within REVERT_S + slack after a
    charger frame), 'boot' (the first of each status ID before any charger
    frame: the power-up round) or 'outside'. Streaming, so vehicle_sim can
    run it on the live bus; `window` numbers the normal-mode windows.
    `pre_unjudged`: every status frame before the first charger frame is
    'prechg', counted and not judged (score()'s `prechg_unjudged`)."""

    def __init__(self, revert_s=REVERT_S, slack_s=REVERT_SLACK_S,
                 pre_unjudged=False):
        self.revert_s, self.slack_s = revert_s, slack_s
        self.pre_unjudged = pre_unjudged
        self.last_c = None
        self.window = 0
        self.boot_ids = set()
        self.counts = {"normal": 0, "boot": 0, "outside": 0, "prechg": 0}
        self.first_outside = None

    def charger(self, t):
        if self.last_c is None or t - self.last_c > self.revert_s:
            self.window += 1
        if self.last_c is None or t > self.last_c:
            self.last_c = t

    def is_normal(self, t):
        return (self.last_c is not None
                and 0 <= t - self.last_c <= self.revert_s + self.slack_s)

    def status(self, t, fid):
        if self.is_normal(t):
            kind = "normal"
        elif self.last_c is None and self.pre_unjudged:
            kind = "prechg"
        elif self.last_c is None and fid not in self.boot_ids:
            self.boot_ids.add(fid)
            kind = "boot"
        else:
            kind = "outside"
            if self.first_outside is None:
                self.first_outside = (t, fid)
        self.counts[kind] += 1
        return kind

# the board's 8.3 event line, "[t] STATE event a= b= c=", and
# interposer_sim's "HH:MM:SS intp [STATE] event" (the same events)
BOARD_EVENT = re.compile(r"^\[\s*\d+(?:\.\d+)?\]\s+([A-Z_]+)\s+(\S.*)$")
SIM_EVENT = re.compile(r"\[([A-Z_]+)\]\s+(\S.*)$")


def load_state_names():
    """{code: name} of intp_state from the diag DBC (decode with cantools,
    never by hand: the DBC is the published interface)."""
    import cantools
    import paths
    db = cantools.database.load_file(paths.diag_dbc())
    sig = db.get_message_by_frame_id(0x7F4).get_signal_by_name("intp_state")
    return db, {int(k): str(v) for k, v in sig.choices.items()}


def state_of(db, data):
    v = db.decode_message(0x7F4, bytes(data), decode_choices=True)
    return str(v["intp_state"])


# --- capture ---------------------------------------------------------------

# The VCU's page 00 (protocol.CMD_ID), logged by the witness too: its flow
# bit's fall, seen on CAN1 on the PCAN's clock, is the flow drop's time for
# a board run's fault-vs-flow-drop order (run_scenario.witness_fault_flow_
# order; reviewer, 2026-10-14). A board writes no flight trace.
VCU_PAGE00_ID = 0x18EFC000


def witness_bus_state(acks):
    """The PCAN witness's bus state: ACTIVE (it acknowledges) or PASSIVE.
    On the bench nothing else acknowledges on the vehicle segment while the
    board is listen-only, so a board run wants ACTIVE (reviewer,
    2026-10-14); either way it records the same frames."""
    import can
    return can.BusState.ACTIVE if acks else can.BusState.PASSIVE


class DiagWitness(object):
    """A PCAN on the vehicle segment logging every 0x7F4-0x7F7, every
    charger frame the board forwards (spec 2.3's mode), the VCU's
    0x18EFC000 (VCU_PAGE00_ID, every page) and the BMS frames BMS_IDS:
    'host<TAB>device<TAB>id<TAB>hex' per line, host = time.perf_counter()
    at recv. Drained throughout. acks=True opens it in normal mode, so it
    is the vehicle segment's acknowledger (witness_bus_state)."""

    def __init__(self, path, channel="PCAN_USBBUS1", bitrate=500000,
                 acks=True):
        self.path, self.channel, self.bitrate = path, channel, bitrate
        self.acks = acks
        self._stop = threading.Event()
        self._t = None
        self.n = 0
        self.error = None

    def start(self):
        import can
        self.bus = can.Bus(interface="pcan", channel=self.channel,
                           bitrate=self.bitrate,
                           state=witness_bus_state(self.acks))
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
            # the status frames, and the charger frames the board forwards:
            # spec 2.3's mode is read from the latter (ModeWindows)
            if m is None or not (m.arbitration_id in STATUS_IDS or (
                    not m.is_extended_id and m.arbitration_id in BMS_IDS) or (
                    m.is_extended_id and (
                        is_charger_frame(m.arbitration_id) or
                        m.arbitration_id == VCU_PAGE00_ID))):
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


RESET_ROUND_S = 2.0       # the round must follow a board reset within this


def reset_round(rows, reset_host, uptime_of):
    """A board reset in the middle of a capture (boot-mid-session's) is a
    power-up: the first of each status id after it is spec 2.3's round, and
    8.2's cadence restarts from there (reviewer, 2026-10-14). Anchored on the
    BOARD's evidence, not only the host's reset time: within RESET_ROUND_S
    of reset_host (the witness's own host clock) all four ids must arrive,
    and the first 0x7F7 after it must show the uptime reset -- 0, from a
    pre-reset uptime of at least 1 min (a board up under a minute reads 0
    either way and cannot show it). rows: status frames, (host, device, id,
    body). uptime_of(body) -> whole minutes. -> (round rows or None, why)."""
    after = sorted((r for r in rows if r[0] >= reset_host),
                   key=lambda r: r[1])
    before = [r for r in rows if r[0] < reset_host and r[2] == 0x7F7]
    # the round is the FIRST four status frames after the reset, all four
    # ids together -- not the first of each id wherever it falls, which
    # would borrow a missing id from the next 1 Hz burst (the can-fail
    # caught exactly that, 2026-10-14)
    four = [r for r in after[:4] if r[0] - reset_host <= RESET_ROUND_S]
    first = {r[2]: r for r in four}
    missing = sorted(set(STATUS_IDS) - set(first))
    if missing or len(four) < 4:
        return (None, "no power-up round of all four within %.0f s of the "
                "reset (the first status frames after it: %s; missing %s)"
                % (RESET_ROUND_S, " ".join("0x%03X" % r[2] for r in four)
                   or "none", " ".join("0x%03X" % f for f in missing)
                   or "none, but not four distinct ids"))
    up_pre = uptime_of(before[-1][3]) if before else None
    up_post = uptime_of(first[0x7F7][3])
    if up_pre is None or up_pre < 1 or up_post != 0:
        return (None, "0x7F7 uptime %s -> %s min across the reset: no uptime "
                "reset shown, so the gap is judged as cadence"
                % ("unseen" if up_pre is None else up_pre, up_post))
    rnd = sorted(first.values(), key=lambda r: r[1])
    return (rnd, "a board reset at host %.3f: the power-up round of all four "
            "%.3f s after it, 0x7F7 uptime %d -> 0 min; the cadence restarts "
            "from the round" % (reset_host, rnd[0][0] - reset_host, up_pre))


def score(witness_rows, serial_rows, db=None, names=None, listen_only=True,
          boot_in_capture=False, reset_host=None, prechg_unjudged=None):
    """-> (ok, why). `db` decodes 0x7F4's state; tests pass a stand-in.
    `listen_only` (spec 2.3, default): judge 8.2 inside the normal-mode
    windows only and fail a status frame outside them; False is the rule
    before d9f49ea6 (the port always normal).
    `boot_in_capture`: the caller KNOWS the capture contains the board's
    boot (it reset the board with the witness already open), so the
    power-up round must carry all four status frames. Without it a missing
    round is recorded, not failed, because a capture may start after the
    boot -- which also let a board that sent one of the four pass
    (implementor's bench boot, 2026-10-13; spec 2.3 at 4f5168ae).
    `prechg_unjudged`: why the window before the first charger frame cannot
    be judged (run_scenario.pre_charger_premise: the previous scenario's
    charger stopped too recently, so its tail may hold the port normal);
    its status frames are then counted and printed NOT JUDGED, never
    failed (reviewer, 2026-10-15, R2 and R4)."""
    if db is None:
        db, codes = load_state_names()
        names = set(codes.values())
    probs, notes = [], []
    every = sorted(witness_rows, key=lambda r: r[1])
    rows = [r for r in every if r[2] in STATUS_IDS]
    if not rows:
        return (False, "8.2: no status frame on the witness")
    # spec 2.3: each status frame's window (None = judged as one span)
    win_of = {}
    windows = []                       # [(first device t, last + REVERT_S)]
    if listen_only:
        mw = ModeWindows(pre_unjudged=bool(prechg_unjudged))
        for r in every:
            if r[2] in STATUS_IDS:
                kind = mw.status(r[1], r[2])
                if kind == "normal":
                    win_of[id(r)] = mw.window
            elif is_charger_frame(r[2]):
                if mw.last_c is None or r[1] - mw.last_c > mw.revert_s:
                    windows.append([r[1], r[1] + REVERT_S])
                else:
                    windows[-1][1] = r[1] + REVERT_S
                mw.charger(r[1])
        c = mw.counts
        if boot_in_capture and mw.boot_ids != set(STATUS_IDS):
            missing = sorted(set(STATUS_IDS) - mw.boot_ids)
            probs.append("2.3: the capture contains the boot, but the "
                         "power-up round carried %d of the four status "
                         "frames (missing %s)"
                         % (len(mw.boot_ids),
                            " ".join("0x%03X" % f for f in missing)))
        if c["outside"]:
            t, fid = mw.first_outside
            probs.append("2.3: %d status frame(s) while the vehicle port "
                         "should be listen-only (more than %.1f s after the "
                         "last charger frame, or beyond the one power-up "
                         "round), first 0x%03X at device t=%.3f s"
                         % (c["outside"], REVERT_S, fid, t))
        notes.append("2.3: %d normal-mode window(s); %d status frames in "
                     "them, %d in the power-up round, %d outside"
                     % (len(windows), c["normal"], c["boot"], c["outside"]))
        if prechg_unjudged:
            notes.append("2.3: the window before the first charger frame "
                         "NOT JUDGED (%s): %d status frame(s) in it"
                         % (prechg_unjudged, c["prechg"]))
        judged = [r for r in rows if id(r) in win_of]
    else:
        judged = rows
    if reset_host is not None:
        rnd, why = reset_round(
            rows, reset_host, lambda body: int(db.decode_message(
                0x7F7, body, decode_choices=False)["intp_uptime_min"]))
        notes.append("reset: " + why)
        if rnd is not None:
            # the round is 2.3's, not cadence; everything after it is a
            # fresh span, so the gap back to the previous boot is not judged
            drop = set(id(r) for r in rnd)
            judged = [r for r in judged if id(r) not in drop]
            for r in judged:
                if r[0] >= reset_host:
                    win_of[id(r)] = ("after reset", win_of.get(id(r)))
    spans = {}
    for r in judged:
        spans.setdefault(win_of.get(id(r)), []).append(r)
    bursts, imm, stray = [], [], []
    bad_gaps, gaps_all = [], []
    for _w, span in sorted(spans.items(), key=lambda kv: kv[1][0][1]):
        b, i_, s_ = group(span)
        bursts += b
        imm += i_
        stray += s_
        # the cadence inside one window only: a window starts afresh
        for a, bb in zip(b, b[1:]):
            ta = {r[2]: r[1] for r in a}
            tb = {r[2]: r[1] for r in bb}
            for fid in STATUS_IDS:
                g = tb[fid] - ta[fid]
                gaps_all.append(g)
                if abs(g - PERIOD_S) > PERIOD_TOL * PERIOD_S + 1e-9:
                    bad_gaps.append((fid, ta[fid], g))
    if stray:
        probs.append("%d frame(s) outside a complete burst of four, first "
                     "0x%03X at device t=%.3f s" % (len(stray), stray[0][2],
                                                    stray[0][1]))
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
    every7f4 = [(to_host(r[1]), r) for r in judged if r[2] == 0x7F4]
    host_windows = [(to_host(a), to_host(b)) for a, b in windows]

    def in_normal(h):
        return (not listen_only) or any(
            a <= h <= b + REVERT_SLACK_S for a, b in host_windows)
    changes = [c_ for c_ in state_changes(serial_rows, names)
               if in_normal(c_[0])]
    n_listen = len(state_changes(serial_rows, names)) - len(changes)
    # a state change whose 50 ms ran past the witness's last frame cannot be
    # judged: the witness stops when vehicle_sim exits, and a board then
    # trips on the BMS going quiet (restart-after-standby, item 6: the TRIP
    # line came 0.4 s after the witness's last frame) -- not judged, counted
    witness_end = max(r[0] for r in every)
    n_after = sum(1 for c_ in changes if c_[0] > witness_end - IMMEDIATE_S)
    changes = [c_ for c_ in changes if c_[0] <= witness_end - IMMEDIATE_S]
    if n_after:
        notes.append("%d state change(s) after the witness stopped, not "
                     "judged" % n_after)
    late = []
    for h, old, new, line in changes:
        near = [(abs(th - h), th - h, state_of(db, r[3]))
                for th, r in imm_host if abs(th - h) <= IMMEDIATE_S + 1e-9]
        good = [x for x in near if x[2] == new]
        if good:
            continue
        nearest = min(((abs(th - h), th - h, state_of(db, r[3]))
                       for th, r in every7f4 if state_of(db, r[3]) == new),
                      default=None)
        late.append((h, old, new, near, nearest))
    n_chg = len(changes)
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
    if listen_only and n_listen:
        notes.append("%d state change(s) in listen-only, not judged (no "
                     "frame may go out then)" % n_listen)
    if probs:
        # a reset the board did not show is context for the failure, not a
        # failure of its own (board_reset_proof judges the reset)
        ctx = [n for n in notes if n.startswith("reset: ")]
        return (False, "8.2: " + "; ".join(probs + ctx))
    return (True, "8.2: " + "; ".join(notes))
