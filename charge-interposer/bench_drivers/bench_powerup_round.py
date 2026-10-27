"""Spec 2.3's power-up round on the board (4f5168ae, user 2026-10-13): the
switch to listen-only waits until all four status frames are sent or 50 ms,
and the board reports how many were sent (round=N/4 on its serial MODE and
boot lines, 8.3). Host side of the reviewer's no-hands plan (2026-10-14).

L2 already shows the arithmetic, through a read-only accessor; this checks
the PRINTED count against what a witness captured, and the parts only
silicon has:

  ack     PCAN NORMAL (it ACKs and witnesses) opened BEFORE the reset; the
          Kvaser and Innomaker closed. round= must be 4, the capture must
          hold 0x7F4-0x7F7 once each, and round= must equal the frames
          captured. Nothing more from the board afterwards (the charger is
          silent, so the port stays listen-only).
  noack   PCAN LISTEN-ONLY, Kvaser closed: nothing ACKs (the WiCAN in mode
          0 does not, bench-holder.md 2026-10-13). round= must be 0. The
          witness sees the head frame RETRIED until the switch -- those are
          unacknowledged attempts, not sent frames -- and their span, first
          attempt to last, is the wait measured independently of the board:
          it must sit near 50 ms. Then NOTHING from the board for at least
          10 s: the listen-only half, which the reviewer ranks above the
          zero count.
  com3    the boot set off by a host opening COM3 (the 111,388 us outlier
          of 2026-10-13 was such a boot, in unrecorded conditions): record
          the boot window, round= and the segment state for each, under
          (a) no adapter, (b) PCAN normal, (c) PCAN listen-only.

The board is RESET over USB only (USB-Serial/JTAG: esptool's RTS pulse, or
the port being opened). No buttons, no replug.

    py -3.14 bench_powerup_round.py --self-test       # no hardware
    py -3.14 bench_powerup_round.py --port COM3 --mode ack --boots 5

Tester, 2026-10-14. First bench run 02:57: every ack boot FAILED for the
harness (MODE-only parser, 5 s hold cut the first status block); now both
prints are parsed and required, the hold is 8 s. Later 2026-10-14 (reviewer):
the no-ACKer check is the error-count invariant -- board err == the
witness's error rows + attempts to the last valid one, and exactly 16
leading error rows (128/8) -- plus the run's span <= 55 ms and the quiet.
Later again (reviewer): one named slack, u in {0,1} uncounted final
attempts that finished on the wire, with u + t <= 1.
2026-10-22 (user ruling, relayed by the reviewer, after the u1t1
discriminator): u <= 1 and t <= 1 independently; u + t <= 1 is dropped.
--strict-port is the ported image's rule (port requirement (f)): u must be
0, t <= 1. Off by default on the Arduino baseline, and no suite line passes
it (the suite's own test pins that).

    py -3.14 bench_powerup_round.py --can-fail        # self-test vs MUTANTS
"""

import argparse
import datetime
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATUS_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
ROUND_RE = re.compile(r"\bround=(\d)/4\b")
# the wait, if the line carries it next to round= (README: "how many ...
# and how long the wait took"); its exact text is not published, so it is
# optional here and the raw line is always printed
WAIT_RE = re.compile(r"\bround=\d/4\D{0,12}?(\d+)\s*us")
BOOT_RE = re.compile(r"\bboot=(\d+)us")
# the BOOT line's own form of the same count (README: "the MODE line and the
# boot line"), seen on the bench 2026-10-14: "spec 2.3: vehicle port in
# normal mode for 12209 us at boot, round 4 of 4 sent in 849 us"
BOOTLINE_RE = re.compile(r"\bround (\d) of 4 sent in (\d+) us")
BOOTLINE_WIN_RE = re.compile(r"normal mode for (\d+) us at boot")
ROUND_WINDOW_S = 0.2       # the round's four frames, from the first
WAIT_MAX_S = 0.050         # spec 2.3
WAIT_SLACK_S = 0.005       # the switch itself and the witness's resolution
QUIET_S = 10.0             # reviewer: nothing from the board for >= 10 s
# The board's own error count, from its LOSS line ("veh(rx=0 tx=0 err=184"),
# cumulative since boot
LOSS_RE = re.compile(r"\bveh\(rx=\d+ tx=\d+ err=(\d+)")
# With nothing ACKing, each attempt is an ACK error (+8 TEC). The board is
# error-ACTIVE until TEC reaches 128, so its first 128 / 8 = 16 attempts end
# in an active error flag, which a listen-only witness records as an ERROR
# row; from then on it is error-passive, its flag is recessive, and the
# witness records each attempt as a valid frame. Reviewer, 2026-10-14, from
# witness_noack_01-03: 16 error rows, 168 valid 0x7F4, one trailing error
# row, board err=184 on every boot. A count that does not depend on the
# deadline, so nothing in it is fitted.
ERROR_ACTIVE_ATTEMPTS = 128 // 8


# --- parsing and scoring (pure; --self-test covers these) -------------------

def parse_serial(lines):
    """-> dict: the FIRST MODE-line round= / boot= after the reset (round,
    wait_us, boot_us) and the BOOT line's count (bl_round, bl_wait_us,
    bl_boot_us), plus the lines they came from. The two lines are separate
    prints of one count, so each is scored against the wire on its own; a
    line that never came is None, never 0."""
    out = {"round": None, "wait_us": None, "boot_us": None,
           "bl_round": None, "bl_wait_us": None, "bl_boot_us": None,
           "veh_err": None, "rebooted": False, "lines": []}
    for ln in lines:
        e = LOSS_RE.search(ln)
        if e and out["veh_err"] is None:
            out["veh_err"] = int(e.group(1))
            out["lines"].append(ln)
        if "ESP-ROM:" in ln:
            out["rebooted"] = True
        m = BOOTLINE_RE.search(ln)
        if m and out["bl_round"] is None:
            out["bl_round"] = int(m.group(1))
            out["bl_wait_us"] = int(m.group(2))
            w = BOOTLINE_WIN_RE.search(ln)
            out["bl_boot_us"] = int(w.group(1)) if w else None
            out["lines"].append(ln)
        m = ROUND_RE.search(ln)
        if m and out["round"] is None:
            out["round"] = int(m.group(1))
            w = WAIT_RE.search(ln)
            out["wait_us"] = int(w.group(1)) if w else None
            out["lines"].append(ln)
        b = BOOT_RE.search(ln)
        if b and out["boot_us"] is None:
            out["boot_us"] = int(b.group(1))
            if ln not in out["lines"]:
                out["lines"].append(ln)
    return out


def board_rows(rows):
    """Witness rows (t_device, id, is_error, data, t_host) that are the
    board's status frames -- data frames only. t_device (the adapter's
    stamp) times the round and the retries; t_host (time.time() at receipt)
    times the quiet window against the capture's end, which only the host
    clock knows. python-can's PCAN stamps are relative to the machine's
    BOOT when the `uptime` module is missing, so the two are not on one
    scale and must never be subtracted."""
    return [r for r in rows if not r[2] and r[1] in STATUS_IDS]


def _missing_lines(s):
    """FAIL lines for a count that is absent from either print."""
    out = []
    if s["round"] is None:
        out.append("FAIL: no MODE-line round=N/4 on the serial lines after "
                   "the reset -- the boot was not seen, the capture ended "
                   "before the first 5 s status block, or the line format "
                   "changed (raw lines are in the record)")
    if s["bl_round"] is None:
        out.append("FAIL: no boot line 'round N of 4 sent in X us' after the "
                   "reset (raw lines are in the record)")
    return out


def score_ack(rows, serial):
    """-> (ok, lines). With an ACKer: round=4, the four once each in the
    round, round= equal to the frames captured, nothing after."""
    lines, ok = [], True
    s = parse_serial(serial)
    b = board_rows(rows)
    miss = _missing_lines(s)
    if miss:
        return False, miss
    if not b:
        return False, ["FAIL: the witness captured no status frame at all"]
    t0 = b[0][0]
    rnd = [r for r in b if r[0] - t0 <= ROUND_WINDOW_S]
    after = [r for r in b if r[0] - t0 > ROUND_WINDOW_S]
    ids = sorted(r[1] for r in rnd)
    lines.append("MODE round=%d/4 wait %s us boot %s us; boot line %d of 4 "
                 "in %s us, window %s us; witness: %d frame(s) in the round "
                 "(%s), %d after"
                 % (s["round"], s["wait_us"], s["boot_us"], s["bl_round"],
                    s["bl_wait_us"], s["bl_boot_us"], len(rnd),
                    " ".join("0x%03X" % i for i in ids), len(after)))
    if ids != list(STATUS_IDS):
        ok = False
        lines.append("FAIL: the round on the wire is not 0x7F4-0x7F7 once "
                     "each")
    for name, v in (("MODE line", s["round"]), ("boot line", s["bl_round"])):
        if v != 4:
            ok = False
            lines.append("FAIL: the %s says %d with an ACKer; spec 2.3 wants "
                         "all four sent" % (name, v))
        if v != len(rnd):
            ok = False
            lines.append("FAIL: the %s's count %d disagrees with the %d "
                         "frame(s) the witness captured" % (name, v, len(rnd)))
    if after:
        ok = False
        lines.append("FAIL: %d status frame(s) after the round, first at "
                     "+%.3f s -- the port should be listen-only"
                     % (len(after), after[0][0] - t0))
    return ok, lines


def attempt_rows(rows):
    """The board's attempts as a listen-only witness records them, in
    device-time order: its status frames, and error rows (on this segment
    only the board transmits during the run: the WiCAN in mode 0 is silent,
    the Kvaser and Innomaker closed)."""
    return sorted((r for r in rows if r[2] or r[1] in STATUS_IDS),
                  key=lambda r: r[0])


def split_attempts(seq):
    """-> (lead, mid, trail): the error rows before the first valid frame,
    first valid to last valid inclusive, and what follows the last valid."""
    vi = [i for i, r in enumerate(seq) if not r[2]]
    if not vi:
        return seq, [], []
    return seq[:vi[0]], seq[vi[0]:vi[-1] + 1], seq[vi[-1] + 1:]


def score_noack(rows, serial, capture_end_t, strict=False):
    """-> (ok, lines). Nothing ACKing: round=0; the attempts the witness
    saw account for the board's own error count exactly, the first 16 of
    them as error rows (error-active); nothing past the wait; then nothing
    for at least QUIET_S.

    ONE INTERRUPTED FINAL ATTEMPT, IN EITHER FORM (reviewer, 2026-10-14,
    after 3 of 6 no-ACKer boots that day showed 16 + 169 valid, no trailing
    row, board err=184). The switch to listen-only cuts the attempt in
    flight. Either it is aborted mid-frame -- a trailing error row (PCAN
    error id 4, stuff error in the data field, ECC 0x0a), t = 1 -- or it
    finishes on the wire, the witness logs it as a valid frame, and the
    board never books its ACK error, u = 1. So: board err == 16 + (rows to
    the last valid) - u, u in {0, 1}; t <= 1. The board counting MORE than
    the witness saw (u < 0) fails. The wait that went with each u is
    printed, never judged.

    u AND t INDEPENDENTLY (user ruling 2026-10-22, relayed by the reviewer):
    the old u + t <= 1 is dropped; 6 of 21 RTS no-ACKer boots showed u=1
    t=1 (runs/u1t1_discriminator_20261022.txt). strict (--strict-port, the
    ported image, port requirement (f)): u must be 0, t <= 1."""
    lines, ok = [], True
    s = parse_serial(serial)
    b = board_rows(rows)
    miss = _missing_lines(s)
    if miss:
        return False, miss
    if not b:
        return False, ["FAIL: the witness saw not even an unacknowledged "
                       "attempt -- is it on the vehicle segment?"]
    seq = attempt_rows(rows)
    lead, mid, trail = split_attempts(seq)
    counted = len(lead) + len(mid)
    u = counted - s["veh_err"] if s["veh_err"] is not None else None
    t = len(trail)
    # the whole run, error rows included: nothing may be attempted past the
    # wait. The error-count invariant cannot see a stray late attempt (the
    # board and the witness would both count it), so this bound stays.
    t0, t1 = seq[0][0], seq[-1][0]
    span = t1 - t0
    quiet = capture_end_t - seq[-1][4]
    if not (-1.0 < quiet < 3600.0):
        return False, ["REFUSED: the capture end and the last attempt are "
                       "%.0f s apart -- two clock scales mixed, not a "
                       "measurement" % quiet]
    lines.append("MODE round=%d/4 wait %s us boot %s us; boot line %d of 4 "
                 "in %s us, window %s us; board err=%s; witness: %d error "
                 "row(s), then %d row(s) to the last valid attempt (%d valid "
                 "%s), %d trailing; %.1f ms first to last, then %.1f s with "
                 "nothing from the board; u=%s t=%d%s"
                 % (s["round"], s["wait_us"], s["boot_us"], s["bl_round"],
                    s["bl_wait_us"], s["bl_boot_us"], s["veh_err"],
                    len(lead), len(mid), len(b),
                    " ".join(sorted(set("0x%03X" % r[1] for r in b))),
                    len(trail), span * 1000, quiet, u, t,
                    " (strict-port)" if strict else ""))
    for name, v in (("MODE line", s["round"]), ("boot line", s["bl_round"])):
        if v != 0:
            ok = False
            lines.append("FAIL: the %s says %d with nothing acknowledging -- "
                         "frames counted as sent that nothing ACKed"
                         % (name, v))
    if span > WAIT_MAX_S + WAIT_SLACK_S:
        ok = False
        lines.append("FAIL: the board kept retrying for %.1f ms, past the "
                     "50 ms wait" % (span * 1000))
    if s["veh_err"] is None:
        ok = False
        lines.append("FAIL: no LOSS line with the board's veh err= count "
                     "after the reset")
    elif u < 0:
        ok = False
        lines.append("FAIL: the board counts %d errored attempts, the "
                     "witness saw %d (%d error rows + %d to the last valid; "
                     "the trailing cut row not counted) -- the board counts "
                     "MORE than happened"
                     % (s["veh_err"], counted, len(lead), len(mid)))
    elif u > 1:
        ok = False
        lines.append("FAIL: the board counts %d errored attempts, the "
                     "witness saw %d (%d error rows + %d to the last valid): "
                     "u=%d uncounted, only one final attempt can be"
                     % (s["veh_err"], counted, len(lead), len(mid), u))
    elif strict and u > 0:
        ok = False
        lines.append("FAIL: strict-port: the board counts %d errored "
                     "attempts, the witness saw %d: u=%d uncounted; the "
                     "ported image must count every attempt (u=0)"
                     % (s["veh_err"], counted, u))
    if len(lead) != ERROR_ACTIVE_ATTEMPTS:
        ok = False
        lines.append("FAIL: %d leading error row(s); an error-active "
                     "transmitter with no ACKer gives 128/8 = %d before it "
                     "turns error-passive"
                     % (len(lead), ERROR_ACTIVE_ATTEMPTS))
    if t > 1:
        ok = False
        lines.append("FAIL: %d rows after the last valid attempt; only one "
                     "(the attempt the stop cut) is explained" % t)
    for name, w in (("MODE line", s["wait_us"]),
                    ("boot line", s["bl_wait_us"])):
        if w is not None and not (WAIT_MAX_S * 1e6 <= w <=
                                  (WAIT_MAX_S + WAIT_SLACK_S) * 1e6):
            ok = False
            lines.append("FAIL: the %s reports a %d us wait; with nothing "
                         "ACKing it must run the full 50 ms" % (name, w))
    if quiet < QUIET_S:
        ok = False
        lines.append("FAIL: only %.1f s of capture after the last attempt; "
                     "the check needs %.0f s of silence to judge" %
                     (quiet, QUIET_S) if quiet >= 0 else
                     "FAIL: capture ended before the attempts did")
    return ok, lines


def score_boot(args, mode, rows, serial, end_t):
    """One ack or noack boot's verdict. strict only from this script's own
    --strict-port: bench_usb_host passes its own args to run_boot, which
    have no such switch, so its boots are always judged by the default."""
    if mode == "ack":
        return score_ack(rows, serial)
    return score_noack(rows, serial, end_t,
                       strict=bool(getattr(args, "strict_port", False)))


# --- hardware ---------------------------------------------------------------

class Witness(object):
    """The PCAN: NORMAL (ACKs) or LISTEN-ONLY (state=PASSIVE), every row
    with its device timestamp."""

    def __init__(self, acks):
        import can
        kw = dict(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
        if acks:
            kw["auto_reset"] = True
        else:
            kw["state"] = can.BusState.PASSIVE
        self.mode = "normal (ACKs)" if acks else "listen-only"
        self.bus = can.Bus(**kw)
        self.rows, self.lock = [], threading.Lock()
        self.stop_ev = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self.stop_ev.is_set():
            m = self.bus.recv(timeout=0.05)
            if m is not None:
                with self.lock:
                    self.rows.append((m.timestamp, m.arbitration_id,
                                      bool(m.is_error_frame), bytes(m.data),
                                      time.time()))

    def take(self):
        with self.lock:
            r, self.rows = self.rows, []
        return r

    def close(self):
        self.stop_ev.set()
        self.t.join(timeout=2.0)
        self.bus.shutdown()


def after_banner(lines):
    """-> (the lines from the reset's ROM banner on, found). Lines read
    after the reset was SENT can still be the earlier boot's: on the bench
    2026-10-15 05:55 the first MODE line after "RESET SENT" was an earlier
    boot's (round=0/4, no ACKer then), buffered by the host's CDC driver,
    and boot 1 was scored on it. The measured boot's lines start at its
    banner; without one, this boot's lines cannot be told apart."""
    for i, ln in enumerate(lines):
        if "ESP-ROM:" in ln:
            return lines[i:], True
    return lines, False


def serial_lines_since(path, start_line):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()[start_line:]


def run_boot(args, out_dir, n, mode):
    """One boot. Returns (ok_or_None, lines, record)."""
    import run_scenario as R
    tag = "%s_%02d" % (mode, n)
    log = os.path.join(out_dir, "serial_%s.log" % tag)
    wit = None
    adapters = "none"
    if mode in ("ack", "com3_pcan_normal"):
        wit = Witness(acks=True)
    elif mode in ("noack", "com3_pcan_listen"):
        wit = Witness(acks=False)
    if wit:
        adapters = "PCAN %s; Kvaser, Innomaker closed" % wit.mode
    time.sleep(1.0)
    pre = wit.take() if wit else []
    tap = R.SerialTap(args.port, 115200, log)
    tap.start()                      # the first open: itself a COM3 boot
    if mode in ("ack", "noack"):
        time.sleep(3.0)              # let that boot settle
        if wit:
            wit.take()
        lines0 = len(serial_lines_since(log, 0))
        tap.reset_board()            # the measured boot: an RTS pulse
    else:
        lines0 = 0
    # the MODE line comes in the 5 s status block: 8 s takes the first
    # block whole (a 5 s hold cut it off, bench 2026-10-14 02:57)
    hold = QUIET_S + 3.0 if mode == "noack" else 8.0
    time.sleep(hold)
    end_t = time.time()
    rows = wit.take() if wit else []
    tap.stop()
    if wit:
        wit.close()
    ser = serial_lines_since(log, lines0)
    banner = True
    if mode in ("ack", "noack"):
        ser, banner = after_banner(ser)
    # every witness row kept, so the round can be re-read and 0x7F7 (the
    # build page) decoded after the run
    with open(os.path.join(out_dir, "witness_%s.txt" % tag), "w") as fh:
        fh.write("# t_device t_host id err data -- %s\n" % adapters)
        for r in rows:
            fh.write("%.6f %.6f %03X %d %s\n"
                     % (r[0], r[4], r[1], int(r[2]), r[3].hex()))
    rec = ["boot %s: %s; segment: %s; WiCAN %s; %d frame(s) on the segment "
           "in the 1 s before" % (tag, datetime.datetime.now().isoformat(),
                                   adapters, args.wican_state, len(pre))]
    for ln in parse_serial(ser)["lines"]:
        rec.append("  serial> " + ln)
    if mode in ("ack", "noack"):
        # the quiet window is judged on the HOST clock at both ends
        ok, lines = score_boot(args, mode, rows, ser, end_t)
    if mode in ("ack", "noack") and not banner:
        ok = False
        lines.append("FAIL: no ROM banner after the reset: this boot's "
                     "serial lines cannot be told from an earlier boot's")
    if mode not in ("ack", "noack"):
        s = parse_serial(ser)
        ok = None
        lines = ["COM3-open: rebooted=%s; MODE boot=%s us round=%s wait=%s "
                 "us; boot line %s of 4 in %s us, window %s us; witness "
                 "status frames %d"
                 % (s["rebooted"], s["boot_us"], s["round"], s["wait_us"],
                    s["bl_round"], s["bl_wait_us"], s["bl_boot_us"],
                    len(board_rows(rows)))]
    return ok, rec + lines


def run_bench(args):
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = args.out or os.path.join(HERE, "runs",
                                       "bench_powerup_round_%s" % stamp)
    os.makedirs(out_dir, exist_ok=True)
    modes = {"ack": ["ack"], "noack": ["noack"],
             "com3": ["com3_none", "com3_pcan_normal", "com3_pcan_listen"]}
    verdicts = []
    for mode in modes[args.mode]:
        for n in range(1, args.boots + 1):
            ok, lines = run_boot(args, out_dir, n, mode)
            for ln in lines:
                print(ln)
            verdicts.append((mode, n, ok))
    print("\n== verdicts")
    rc = 0
    for mode, n, ok in verdicts:
        print("  %-18s %2d  %s" % (mode, n, {True: "PASS", False: "FAIL",
                                             None: "recorded"}[ok]))
        if ok is False:
            rc = 1
    return rc


# --- self-test: no hardware -------------------------------------------------

def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    def fr(t, fid):
        return (t, fid, False, bytes(8), t)

    def fe(t, fid=0x002):
        return (t, fid, True, bytes(4), t)

    four = [fr(100.0 + 0.00025 * i, f) for i, f in enumerate(STATUS_IDS)]
    BL4 = ("spec 2.3: vehicle port in normal mode for 12209 us at boot, "
           "round 4 of 4 sent in 849 us")
    BL0 = ("spec 2.3: vehicle port in normal mode for 61000 us at boot, "
           "round 0 of 4 sent in 50025 us")
    M4 = ("  MODE veh=listen-only core wants listen-only | switches=1 "
          "refused=0 suppressed=0 | round=4/4 in 849us | boot=12209us "
          "switch=293/293us(last/max) drained=0")
    M0 = M4.replace("round=4/4 in 849us", "round=0/4 in 50025us").replace(
        "boot=12209us", "boot=61000us")
    ser4 = ["ESP-ROM:esp32s3-20210327", BL4, "t=5s state=PASSTHROUGH", M4]
    LOSS0 = ("  LOSS veh(rx=0 tx=0 err=184 off=0 peak=0) chg(rx=0 tx=0 err=0 "
             "off=0 ring=0/0) bridge=0 emit=0")
    ser0 = [BL0, M0, LOSS0]

    s = parse_serial(ser4)
    check(s["round"] == 4 and s["wait_us"] == 849 and s["boot_us"] == 12209,
          "parse: the bench MODE line's round=4/4 in 849us and boot=")
    check(s["bl_round"] == 4 and s["bl_wait_us"] == 849 and
          s["bl_boot_us"] == 12209 and s["rebooted"],
          "parse: the bench boot line's 'round 4 of 4 sent in 849 us', its "
          "window, and the ROM banner")
    # 2026-10-15 05:55: an earlier boot's MODE line, buffered, read after
    # "RESET SENT" and before the reset's banner
    stale = ["# RESET SENT host=1.0", M0, LOSS0] + ser4
    ok, ln = score_ack(four, stale)
    check(not ok and any("count 0" in x for x in ln),
          "ack CAN-FAIL: the stale MODE line read whole FAILS the boot (the "
          "05:55 defect) -> %s" % [x for x in ln if "FAIL" in x][:1])
    cut, found = after_banner(stale)
    ok, ln = score_ack(four, cut)
    check(found and cut[0].startswith("ESP-ROM:") and ok,
          "after_banner: from the reset's banner on, the same boot PASSES "
          "-> %s" % [x for x in ln if "FAIL" in x])
    cut, found = after_banner([M0, LOSS0, BL4, M4])
    check(not found and len(cut) == 4,
          "after_banner: no banner -> not found, lines kept (run_boot then "
          "FAILS the boot rather than guess)")
    ok, ln = score_ack(four, [BL4])
    check(not ok and any("MODE-line" in x for x in ln),
          "ack CAN-FAIL: the boot line alone (capture cut before the 5 s "
          "block, the 02:57 bench defect) FAILS, not passes")
    ok, ln = score_ack(four, [M4])
    check(not ok and any("boot line" in x for x in ln),
          "ack CAN-FAIL: the MODE line alone FAILS -- both prints are "
          "required")
    ok, ln = score_ack(four, [BL4.replace("round 4 of 4", "round 3 of 4"),
                              M4])
    check(not ok and any("boot line's count 3" in x for x in ln),
          "ack CAN-FAIL: boot line 3 against MODE 4 and 4 captured FAILS on "
          "the boot line")
    check(parse_serial(["round=4/4"])["wait_us"] is None and
          parse_serial(["round=4/4"])["bl_round"] is None,
          "parse: a round= with no wait leaves the wait unknown, not 0")
    check(parse_serial(["nothing"])["round"] is None,
          "parse: no round= is None, not 0 -- a missed boot is not a pass")

    ok, ln = score_ack(four, ser4)
    check(ok, "ack: four once each, round=4 -> PASS (%s)" % ln[0])
    ok, ln = score_ack(four[:3], ser4)
    check(not ok and any("disagrees" in x for x in ln),
          "ack CAN-FAIL: round=4 but the witness caught 3 -> FAIL, count vs "
          "capture")
    ok, ln = score_ack(four, [BL4, "round=3/4 700us"])
    check(not ok and any("disagrees" in x for x in ln),
          "ack CAN-FAIL: 4 captured but round=3 -> FAIL")
    ok, ln = score_ack(four[:1], [BL4.replace("round 4 of", "round 1 of"),
                                  "round=1/4"])
    check(not ok and any("not 0x7F4-0x7F7" in x for x in ln),
          "ack: the E8 bench boot (0x7F4 alone, round=1) FAILS")
    ok, ln = score_ack(four + [fr(101.0, 0x7F4)], ser4)
    check(not ok and any("after the round" in x for x in ln),
          "ack: a status frame 1 s after the round FAILS (not listen-only)")
    ok, ln = score_ack(four, [])
    check(not ok and "no MODE-line" in ln[0], "ack: no serial round= FAILS")

    # nothing ACKing, the bench's shape: 16 error rows (error-active), 168
    # valid 0x7F4 (error-passive), one trailing error row (the cut attempt),
    # ~0.272 ms apart, 50.05 ms first to last
    def noack_seq(n_lead=16, n_valid=168, n_trail=1):
        out, i = [], 0
        for _ in range(n_lead):
            out.append(fe(200.0 + 0.000272 * i)); i += 1
        for _ in range(n_valid):
            out.append(fr(200.0 + 0.000272 * i, 0x7F4)); i += 1
        for _ in range(n_trail):
            out.append(fe(200.0 + 0.000272 * i, 0x004)); i += 1
        return out
    retries = noack_seq()
    end = retries[-1][0] + 12.0
    ok, ln = score_noack(retries, ser0, end)
    check(ok, "noack: retries over ~50 ms, round=0, 12 s quiet -> PASS (%s)"
          % ln[0])
    ok, ln = score_noack(retries + [fr(202.0, 0x7F4)], ser0, end)
    check(not ok and any("past the 50 ms" in x for x in ln),
          "noack CAN-FAIL: an attempt 2 s later (not listen-only) FAILS")
    ok, ln = score_noack(retries, [BL0, "round=1/4 50000us"], end)
    check(not ok and any("counted as sent" in x for x in ln),
          "noack CAN-FAIL: round=1 with nothing ACKing FAILS")
    ok, ln = score_noack(retries, [BL0, "round=0/4 900us"], end)
    check(not ok and any("full 50 ms" in x for x in ln),
          "noack: a reported 900 us wait with nothing ACKing FAILS")
    ok, ln = score_noack(retries, [BL0.replace("in 50025 us", "in 900 us"),
                                   M0], end)
    check(not ok and any("boot line reports a 900" in x for x in ln),
          "noack CAN-FAIL: the boot line's 900 us wait FAILS even when the "
          "MODE line is right")
    ok, ln = score_noack(retries, [M0], end)
    check(not ok and any("boot line" in x for x in ln),
          "noack CAN-FAIL: no boot line FAILS")
    ok, ln = score_noack(retries, ser0, retries[-1][0] + 5.0)
    check(not ok and any("silence to judge" in x for x in ln),
          "noack: 5 s of capture after the attempts is too short to judge "
          "silence -> FAIL, not a pass")
    ok, ln = score_noack([], ser0, end)
    check(not ok, "noack: a witness that saw nothing at all FAILS")
    # the error-count invariant (reviewer 2026-10-14) and its can-fails
    ok, ln = score_noack(noack_seq(15, 169), ser0, end)
    check(not ok and any("leading error row" in x for x in ln) and
          not any("errored attempts" in x for x in ln),
          "noack CAN-FAIL: 15 leading error rows (total still 184) FAILS on "
          "the 16 alone")
    ok, ln = score_noack(retries, [BL0, M0, LOSS0.replace("err=184",
                                                          "err=185")], end)
    check(not ok and any("counts 185" in x and "saw 184" in x for x in ln),
          "noack CAN-FAIL: board err=185 against 16+168 FAILS -- this is "
          "also what COUNTING the trailing cut row would require")
    ok, ln = score_noack(noack_seq(16, 167), ser0, end)
    check(not ok and any("counts 184" in x and "saw 183" in x for x in ln),
          "noack CAN-FAIL: one valid attempt missing from the witness FAILS")
    ok, ln = score_noack(noack_seq(16, 168, 2), ser0, end)
    check(not ok and any("rows after the last valid" in x for x in ln),
          "noack CAN-FAIL: two trailing rows FAIL; one is explained")
    ok, ln = score_noack(noack_seq(16, 168, 0), ser0, end)
    check(ok, "noack: no trailing row (the stop fell between attempts) "
              "PASSES -- at most one, not exactly one")
    ok, ln = score_noack(retries, [BL0, M0], end)
    check(not ok and any("no LOSS line" in x for x in ln),
          "noack CAN-FAIL: no LOSS line FAILS, not skipped")
    # the named slack u (reviewer 2026-10-14): one final attempt that
    # finished on the wire and was never booked
    ok, ln = score_noack(noack_seq(16, 169, 0), ser0, end)
    check(ok and "u=1 t=0" in ln[0],
          "noack: 16 + 169 valid, no trailing row, err=184 (u=1, t=0; the "
          "bench shape of 3 of 6 boots) PASSES")
    ok, ln = score_noack(noack_seq(16, 170, 0), ser0, end)
    check(not ok and any("u=2" in x for x in ln),
          "noack CAN-FAIL: u=2 (16 + 170 valid against err=184) FAILS")
    # u and t independently (user ruling 2026-10-22), and the strict mode
    ok, ln = score_noack(noack_seq(16, 169, 1), ser0, end)
    check(ok and "u=1 t=1" in ln[0] and "strict" not in ln[0],
          "noack: u=1 WITH t=1 (6 of 21 RTS boots) PASSES by default -- u "
          "and t are judged independently (%s)"
          % [x for x in ln if "FAIL" in x])
    ok, ln = score_noack(noack_seq(16, 169, 1), ser0, end, strict=True)
    check(not ok and any("strict-port" in x and "u=1" in x for x in ln),
          "noack strict CAN-FAIL: u=1 t=1 FAILS under --strict-port")
    ok, ln = score_noack(noack_seq(16, 169, 0), ser0, end, strict=True)
    check(not ok and any("strict-port" in x for x in ln),
          "noack strict CAN-FAIL: u=1 t=0 FAILS under --strict-port")
    ok, ln = score_noack(retries, ser0, end, strict=True)
    check(ok and "(strict-port)" in ln[0],
          "noack strict: u=0 t=1 PASSES under --strict-port (t <= 1 stays)")
    ok, ln = score_noack(noack_seq(16, 168, 0), ser0, end, strict=True)
    check(ok, "noack strict: u=0 t=0 PASSES under --strict-port")
    ok, ln = score_noack(noack_seq(16, 168, 2), ser0, end, strict=True)
    check(not ok and any("rows after the last valid" in x for x in ln),
          "noack strict CAN-FAIL: t=2 FAILS under --strict-port too")
    ok, ln = score_noack(noack_seq(16, 170, 0), ser0, end, strict=True)
    check(not ok and any("u=2" in x for x in ln),
          "noack strict CAN-FAIL: u=2 FAILS under --strict-port too")
    ok, ln = score_noack(noack_seq(16, 169, 2), ser0, end)
    check(not ok and any("rows after the last valid" in x for x in ln),
          "noack CAN-FAIL: u=1 with t=2 FAILS on t alone")
    ok, ln = score_noack(noack_seq(16, 168, 0), [BL0, M0, LOSS0.replace(
        "err=184", "err=185")], end, strict=True)
    check(not ok and any("MORE than happened" in x for x in ln),
          "noack strict CAN-FAIL: u=-1 FAILS under --strict-port too")
    # the switch reaches the scorer from this script's own options only
    u1t1 = noack_seq(16, 169, 1)
    ns = build_parser().parse_args(["--port", "X", "--mode", "noack"])
    ok, ln = score_boot(ns, "noack", u1t1, ser0, end)
    check(ok and ns.strict_port is False,
          "score_boot: --strict-port defaults OFF; u1t1 PASSES")
    ns = build_parser().parse_args(["--port", "X", "--mode", "noack",
                                    "--strict-port"])
    ok, ln = score_boot(ns, "noack", u1t1, ser0, end)
    check(not ok and any("strict-port" in x for x in ln),
          "score_boot CAN-FAIL: --strict-port on the command line reaches "
          "the scorer; u1t1 FAILS")
    ok, ln = score_boot(argparse.Namespace(port="X", ack=False), "noack",
                        u1t1, ser0, end)
    check(ok, "score_boot: bench_usb_host's args (no strict_port) are "
              "judged by the default; u1t1 PASSES")
    ok, ln = score_boot(argparse.Namespace(strict_port=True), "ack", four,
                        ser4, end)
    check(ok, "score_boot: an ack boot goes to score_ack whatever the "
              "switch")
    ok, ln = score_noack(noack_seq(16, 168, 0), [BL0, M0, LOSS0.replace(
        "err=184", "err=185")], end)
    check(not ok and any("MORE than happened" in x for x in ln),
          "noack CAN-FAIL: board err > witness (185 vs 184, t=0) FAILS")
    # device stamps on a boot-relative scale, host end on the epoch: the
    # mistake this guard exists for (python-can PCAN without `uptime`)
    boot_rel = [(r[0] - 199.0 + 3500.0, r[1], r[2], r[3], r[0] + 1.76e9)
                for r in retries]
    ok, ln = score_noack(boot_rel, ser0, boot_rel[-1][4] + 12.0)
    check(ok, "noack: boot-relative device stamps with host receipt times "
              "still judge the quiet window on the host clock -> PASS")
    ok, ln = score_noack(retries, ser0, 1.76e9)
    check(not ok and "REFUSED" in ln[0],
          "noack CAN-FAIL: an epoch capture end against boot-relative row "
          "times is REFUSED, not a 1.76e9 s 'quiet'")

    print("\nbench_powerup_round self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run --self-test against each MUTANTS edit")
    ap.add_argument("--strict-port", action="store_true",
                    help="the ported image's no-ACKer rule (port requirement "
                         "(f)): u must be 0. Off on the Arduino baseline; no "
                         "suite line passes it")
    ap.add_argument("--port", help="the X2's USB Serial/JTAG port")
    ap.add_argument("--mode", choices=("ack", "noack", "com3"))
    ap.add_argument("--boots", type=int, default=3)
    ap.add_argument("--wican-state", default="mode 0, 12 V only, untouched",
                    help="recorded per boot (the other board's state, "
                         "bench-holder rule 6)")
    ap.add_argument("--out", default=None)
    return ap


# --can-fail: each edit must make --self-test fail (script_can_fail.py)
MUTANTS = [
    ("the u + t <= 1 rule restored",
     "    elif strict and u > 0:\n",
     "    elif (strict and u > 0) or u + t > 1:\n"),
    ("strict not judged",
     "    elif strict and u > 0:\n", "    elif False:\n"),
    ("strict on by default",
     "def score_noack(rows, serial, capture_end_t, strict=False):",
     "def score_noack(rows, serial, capture_end_t, strict=True):"),
    ("strict also demands t=0",
     "    elif strict and u > 0:\n", "    elif strict and (u > 0 or t > 0):\n"),
    ("u > 1 not judged",
     "    elif u > 1:\n", "    elif False:\n"),
    ("u < 0 not judged",
     "    elif u < 0:\n", "    elif False:\n"),
    ("t > 1 not judged",
     "    if t > 1:\n", "    if False:\n"),
    ("the switch never reaches the scorer",
     "strict=bool(getattr(args, \"strict_port\", False)))",
     "strict=False)"),
    ("the switch on without the option",
     "strict=bool(getattr(args, \"strict_port\", False)))",
     "strict=bool(getattr(args, \"strict_port\", True)))"),
    ("--strict-port defaults on",
     "    ap.add_argument(\"--strict-port\", action=\"store_true\",",
     "    ap.add_argument(\"--strict-port\", action=\"store_false\","),
]


def main():
    ap = build_parser()
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if not a.port or not a.mode:
        ap.error("--port and --mode are required for a bench run")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
