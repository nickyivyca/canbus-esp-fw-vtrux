"""Spec 2.3's power-up round on the board (4f5168ae, user 2026-10-13): the
switch to listen-only waits until all four status frames are sent or 50 ms,
and the board reports how many were sent (round=N/4 on its serial MODE and
boot lines, 8.3). Host side of the reviewer's no-hands plan (2026-10-14).

L2 already shows the arithmetic, through a read-only accessor; this checks
the PRINTED count against what a witness captured, and the parts only
silicon has:

THE KVASER'S LINK (tester, 2026-11-13; the reviewer's A): an UP kvaser_usb
link acknowledges with no socket open, so each mode sets it and reads it
back before its boots (mode_link): UP for ack, DOWN for noack and the com3
boots, which is what "Kvaser closed" meant under CANlib. A link not as the
mode needs is a VOID line with the kernel's USB/CAN lines, and no boot
runs (exit 1).

  ack     PCAN NORMAL (it ACKs and witnesses) opened BEFORE the reset; the
          Kvaser's link up, the Innomaker closed. round= must be 4, the capture must
          hold 0x7F4-0x7F7 once each, and round= must equal the frames
          captured. Nothing more from the board afterwards (the charger is
          silent, so the port stays listen-only).
  noack   PCAN LISTEN-ONLY, the Kvaser's link DOWN: nothing ACKs (the WiCAN in mode
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

Since 2026-11-19 (the charge-interposer reviewer's PCAN ruling 1): the
witness reads under reader_death.guard, so a recv that raises (python-can
raises on most PCAN statuses) is kept with its text, and that boot is VOID
with it, never judged on the rows read before; the exit code is then 1
with a VOID line and no FAIL line, which the bench suite reads VOID. A FAIL
on a boot whose witness lived still stands.

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
# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never
# from the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import reader_death                                         # noqa: E402

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

# THE LEADING ATTEMPTS FROM THE WITNESS'S TIMING, ON LINUX (tester,
# 2026-11-18; the charge-interposer reviewer's ruling on run 8b8e). On
# madhouse the PCAN is PCAN-Basic on PEAK's own pcan driver (9.3.3), which
# passed on only the first of the 16 identical bus-error frames: 1 error
# row, then the first valid 0x7F4 4.156 ms later, the same 4.156 ms that
# NICKY-XPS's 16 error rows span (b467). There the leading attempts are
# counted from the time from the first error row to the first valid row,
# over one error-active attempt's length in bit times: the frame from SOF to
# the end of its CRC, stuffed, then the CRC delimiter and the ACK slot, the
# 6-bit active error flag, the 8-bit delimiter and the 3-bit intermission.
# For the board's 0x7F4 (04 09 00 00 c0 ff 00 00) that is 110 + 19 = 129
# bits, 258 us at 500 kbit/s; NICKY-XPS's error rows are 258 us apart. An
# error-passive attempt adds the 8-bit suspend: 137 bits, 274 us, the valid
# rows' spacing on both systems (printed beside the verdict as a check on
# the model, not judged). The two rows' stamps sit at different points of
# their attempts, so the time is n attempts plus that offset (28 us in b467)
# and n is the nearest whole number, which holds while the offset is under
# half an attempt. The error-row count is shown as a figure, and the total
# the board must account for is unchanged. Off Linux the rows are counted.
BITRATE = 500000
ACTIVE_TAIL_BITS = 2 + 6 + 8 + 3     # CRC delim, ACK slot, flag, delim, IFS
PASSIVE_TAIL_BITS = ACTIVE_TAIL_BITS + 8   # + suspend transmission


def _crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def stuffed_bits(fid, data):
    """Bits on the wire from SOF to the end of the CRC sequence of a classic
    standard-id data frame, stuff bits included."""
    bits = [0]
    bits += [(fid >> (10 - i)) & 1 for i in range(11)]
    bits += [0, 0, 0]                       # RTR, IDE, r0
    bits += [(len(data) >> (3 - i)) & 1 for i in range(4)]
    for byte in data:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    c = _crc15(bits)
    bits += [(c >> (14 - i)) & 1 for i in range(15)]
    out, run, last = 0, 0, None
    for b in bits:
        out += 1
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            out += 1                        # the stuff bit, opposite polarity
            last, run = 1 - b, 1
    return out


def attempt_s(fid, data, tail_bits, bitrate=BITRATE):
    """One attempt's length in seconds: the stuffed frame, then tail_bits."""
    return (stuffed_bits(fid, bytes(data)) + tail_bits) / float(bitrate)


def lead_from_timing(lead, mid):
    """-> (n, gap_s, active_s): the error-active attempts from the first
    error row to the first valid row, or (None, ...) if either is missing."""
    if not lead or not mid:
        return None, None, None
    v = mid[0]
    act = attempt_s(v[1], v[3], ACTIVE_TAIL_BITS)
    gap = v[0] - lead[0][0]
    return int(round(gap / act)), gap, act


def lead_by_default(platform=None):
    return "timing" if (platform or sys.platform).startswith("linux") else "rows"


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


def score_noack(rows, serial, capture_end_t, strict=False, lead_by="rows"):
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
    ported image, port requirement (f)): u must be 0, t <= 1.

    lead_by: "rows" counts the leading error rows (NICKY-XPS); "timing"
    counts the leading attempts from the first error row to the first valid
    row in bit times (Linux, where the driver passes on one error row for
    the 16; see ACTIVE_TAIL_BITS). score_boot picks it by platform."""
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
    if lead_by == "timing":
        n_lead, gap, act = lead_from_timing(lead, mid)
        if n_lead is None:
            return False, ["FAIL: %s -- nothing to time the leading attempts "
                           "by (lead by timing)"
                           % ("no error row before the first valid attempt"
                              if not lead else "no valid attempt")]
        pas = attempt_s(mid[0][1], mid[0][3], PASSIVE_TAIL_BITS)
        vsp = ((mid[-1][0] - mid[0][0]) / (len(mid) - 1)) if len(mid) > 1 else None
        how = ("by timing: %.3f ms from the first error row to the first valid "
               "over a %.0f us error-active attempt (%d bits); error rows %d; "
               "valid rows %s us apart, the model's error-passive attempt "
               "%.0f us" % (gap * 1000, act * 1e6, round(act * BITRATE),
                            len(lead), "%.1f" % (vsp * 1e6) if vsp else "-",
                            pas * 1e6))
    else:
        n_lead = len(lead)
        how = "by rows: %d error rows" % n_lead
    counted = n_lead + len(mid)
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
                 "in %s us, window %s us; board err=%s; witness: %d leading "
                 "attempt(s) (%s), then %d row(s) to the last valid attempt "
                 "(%d valid %s), %d trailing; %.1f ms first to last, then "
                 "%.1f s with nothing from the board; u=%s t=%d%s"
                 % (s["round"], s["wait_us"], s["boot_us"], s["bl_round"],
                    s["bl_wait_us"], s["bl_boot_us"], s["veh_err"],
                    n_lead, how, len(mid), len(b),
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
                     "witness saw %d (%d leading + %d to the last valid; "
                     "the trailing cut row not counted) -- the board counts "
                     "MORE than happened"
                     % (s["veh_err"], counted, n_lead, len(mid)))
    elif u > 1:
        ok = False
        lines.append("FAIL: the board counts %d errored attempts, the "
                     "witness saw %d (%d leading + %d to the last valid): "
                     "u=%d uncounted, only one final attempt can be"
                     % (s["veh_err"], counted, n_lead, len(mid), u))
    elif strict and u > 0:
        ok = False
        lines.append("FAIL: strict-port: the board counts %d errored "
                     "attempts, the witness saw %d: u=%d uncounted; the "
                     "ported image must count every attempt (u=0)"
                     % (s["veh_err"], counted, u))
    if n_lead != ERROR_ACTIVE_ATTEMPTS:
        ok = False
        lines.append("FAIL: %d leading %s; an error-active "
                     "transmitter with no ACKer gives 128/8 = %d before it "
                     "turns error-passive"
                     % (n_lead, "error row(s)" if lead_by != "timing" else
                        "error-active attempt(s) by timing",
                        ERROR_ACTIVE_ATTEMPTS))
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
    have no such switch, so its boots are always judged by the default.
    The leading attempts are counted by timing on Linux and by rows
    elsewhere (lead_by_default; args.platform only for the self-test)."""
    if mode == "ack":
        return score_ack(rows, serial)
    return score_noack(rows, serial, end_t,
                       strict=bool(getattr(args, "strict_port", False)),
                       lead_by=lead_by_default(getattr(args, "platform", None)))


# --- hardware ---------------------------------------------------------------

class Witness(object):
    """The PCAN: NORMAL (ACKs) or LISTEN-ONLY (state=PASSIVE), every row
    with its device timestamp."""

    def __init__(self, acks):
        import can
        import adapter_roles
        kw = {}
        if acks:
            kw["auto_reset"] = True
        else:
            kw["state"] = can.BusState.PASSIVE
        self.mode = "normal (ACKs)" if acks else "listen-only"
        # role pcanfd (shared/bench SPEC section 8, 2026-11-18), bus states
        # reported (the reviewer's PCAN item 2, 2026-11-20): what it reads goes
        # through reader_death.screen, so a state row is not an error row and
        # an overrun row VOIDs the boot as lost frames (run_boot's DIED)
        self.bus = adapter_roles.pcanfd_report(500000, **kw)
        self.rows, self.lock = [], threading.Lock()
        self.stop_ev = threading.Event()
        self.death = reader_death.Death("pcan witness")
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        # a raise ends the thread with the error kept (reader_death)
        reader_death.guard(self.death, self.stop_ev, self._step)

    def _step(self):
        m = reader_death.screen(self.bus, self.bus.recv(timeout=0.05), self.death)
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


def mode_acks(mode):
    """Whether the Kvaser's link is up for this mode: ack only. noack and
    the com3 boots keep it DOWN, as the closed CANlib Kvaser was (an UP
    kvaser_usb link acknowledges with no socket open)."""
    return mode == "ack"


def mode_link(mode, link):
    """The Kvaser's link for a mode, set and read back (2026-11-13, the
    reviewer's A). -> (ok, line); not ok is a VOID line with the kernel's
    USB/CAN lines, and the mode's boots do not run."""
    t0 = time.time()
    try:
        return True, link.set_link(mode_acks(mode))
    except link.LinkError as e:
        return False, link.link_void(e, t0, time.time())


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
        adapters = "PCAN %s; Kvaser link %s, Innomaker closed" % (
            wit.mode, "up" if mode_acks(mode) else "down")
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
    if wit and wit.death.error:
        # the witness died: VOID with its error, the rows never judged
        # (the charge-interposer reviewer's PCAN ruling 1, 2026-11-19)
        return reader_death.DIED, rec + [wit.death.void_line(tag)]
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
    import paths      # no --out: SeaDrive, as before the move, never the tree
    out_dir = args.out or os.path.join(paths.fixtures(), "runs",
                                       "bench_powerup_round_%s" % stamp)
    os.makedirs(out_dir, exist_ok=True)
    modes = {"ack": ["ack"], "noack": ["noack"],
             "com3": ["com3_none", "com3_pcan_normal", "com3_pcan_listen"]}
    verdicts = []
    import kvaser_link
    for mode in modes[args.mode]:
        ok, line = mode_link(mode, kvaser_link)
        print(line)
        if not ok:
            print("%s: no boot run, the Kvaser's link is not as the mode needs" % mode)
            return 1
        for n in range(1, args.boots + 1):
            ok, lines = run_boot(args, out_dir, n, mode)
            for ln in lines:
                print(ln)
            verdicts.append((mode, n, ok))
    print("\n== verdicts")
    for mode, n, ok in verdicts:
        print("  %-18s %2d  %s" % (mode, n, VERDICT_WORD[ok]))
    return bench_rc(v for _m, _n, v in verdicts)


VERDICT_WORD = {True: "PASS", False: "FAIL", None: "recorded",
                reader_death.DIED: "VOID (the witness died)"}


def bench_rc(oks):
    """The exit code from the boots' verdicts: 1 if any FAILED or any
    boot's witness died (reader_death.DIED: its VOID line and no FAIL line
    make bench_suite's script_judge read the check VOID), else 0."""
    oks = list(oks)
    return 1 if any(o is False or o == reader_death.DIED for o in oks) else 0


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

    # the leading attempts by timing (Linux; the reviewer's ruling on 8b8e,
    # 2026-11-18). The bit-time model for the board's 0x7F4:
    SD = bytes.fromhex("04090000c0ff0000")
    check(stuffed_bits(0x7F4, SD) == 110 and
          round(attempt_s(0x7F4, SD, ACTIVE_TAIL_BITS) * 1e6) == 258 and
          round(attempt_s(0x7F4, SD, PASSIVE_TAIL_BITS) * 1e6) == 274,
          "bits: 0x7F4 04 09 00 00 c0 ff 00 00 is 110 stuffed bits to its "
          "CRC's end, 258 us error-active and 274 us error-passive at 500 "
          "kbit/s (NICKY-XPS's error rows and both systems' valid rows)")
    check(stuffed_bits(0x7F4, bytes(8)) != 110,
          "bits: the stuffing depends on the data (all-zero data differs)")
    LOSS5 = LOSS0.replace("err=184", "err=185")
    ser5 = [BL0, M0, LOSS5]

    def real_seq(t_err, t_valid, n_valid=169, trail=True):
        """Rows at the stamps the witnesses gave: error rows at t_err,
        valid 0x7F4 from t_valid 274 us apart, then the cut row."""
        out = [(t, 0x002, True, bytes.fromhex("011b0000"), t) for t in t_err]
        out += [(t_valid + 0.000274 * i, 0x7F4, False, SD, t_valid + 0.000274 * i)
                for i in range(n_valid)]
        if trail:
            tt = out[-1][0] + 0.00014
            out.append((tt, 0x004, True, bytes.fromhex("010a0000"), tt))
        return out
    # b467 witness_noack_01 (NICKY-XPS): 16 error rows from 677126.333226,
    # 258 us apart; the first valid at 677126.337382; board err=185
    xps = real_seq([677126.333226 + 0.000258 * i for i in range(16)], 677126.337382)
    # 8b8e witness_noack_01 (madhouse): 1 error row at 75126.480917, the
    # first valid at 75126.485073; board err=185
    lin = real_seq([75126.480917], 75126.485073)
    xend, lend = xps[-1][0] + 12.0, lin[-1][0] + 12.0
    n, gap, act = lead_from_timing(*split_attempts(attempt_rows(xps))[:2])
    check(n == 16 and abs(gap - 0.004156) < 1e-6,
          "timing on b467's XPS stamps: 4.156 ms over 258 us -> 16, the row "
          "count there (%s)" % n)
    ok, ln = score_noack(xps, ser5, xend, lead_by="timing")
    check(ok and "16 leading attempt(s) (by timing" in ln[0] and "error rows 16" in ln[0],
          "noack timing: b467's XPS shape PASSES by timing, error rows shown "
          "as 16 (%s)" % [x for x in ln if "FAIL" in x])
    ok, ln = score_noack(xps, ser5, xend, lead_by="rows")
    check(ok, "noack rows: b467's XPS shape PASSES by rows too")
    ok, ln = score_noack(lin, ser5, lend, lead_by="timing")
    check(ok and "16 leading attempt(s) (by timing" in ln[0] and "error rows 1;" in ln[0],
          "noack timing: 8b8e's Linux shape (1 error row) PASSES by timing, "
          "error rows shown as 1, the 185 total kept (%s)"
          % [x for x in ln if "FAIL" in x])
    ok, ln = score_noack(lin, ser5, lend, lead_by="rows")
    check(not ok and any("1 leading error row" in x for x in ln),
          "noack rows CAN-FAIL: the Linux shape by rows FAILS (8b8e's verdict)")
    # can-fails of the timing method: an error-active run cut short, or
    # longer, moves the first valid row by whole attempts
    for k, what in ((15, "15 attempts"), (17, "17 attempts")):
        t_valid = 75126.480917 + 0.004156 + (k - 16) * 0.000258
        bad = real_seq([75126.480917], t_valid)
        ok, ln = score_noack(bad, ser5, bad[-1][0] + 12.0, lead_by="timing")
        check(not ok and any("%d leading error-active attempt(s) by timing" % k in x
                             for x in ln),
              "noack timing CAN-FAIL: the first valid row %s after the first "
              "error row FAILS on the 16" % what)
    ok, ln = score_noack(real_seq([], 75126.485073), ser5, lend, lead_by="timing")
    check(not ok and any("nothing to time" in x for x in ln),
          "noack timing CAN-FAIL: no error row at all FAILS, not counted as 0")
    # 185 kept by timing: err=184 against 16 + 169 is u=1 (passes), 186 fails
    ok, ln = score_noack(lin, [BL0, M0, LOSS0.replace("err=184", "err=186")],
                         lend, lead_by="timing")
    check(not ok and any("counts 186" in x and "saw 185" in x for x in ln),
          "noack timing CAN-FAIL: board err=186 against 16 + 169 FAILS")
    # score_boot picks by platform
    check(lead_by_default("linux") == "timing" and lead_by_default("win32") == "rows",
          "lead_by: timing on Linux, rows on Windows")
    import types as _ty
    ok_l, _ = score_boot(_ty.SimpleNamespace(platform="linux"), "noack", lin, ser5, lend)
    ok_w, _ = score_boot(_ty.SimpleNamespace(platform="win32"), "noack", lin, ser5, lend)
    check(ok_l and not ok_w,
          "score_boot: the Linux shape PASSES as judged on Linux and FAILS as "
          "judged on Windows")
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

    # 2026-11-13, the reviewer's A: the Kvaser's link per mode
    import kvaser_link

    class Link(object):
        LinkError = kvaser_link.LinkError
        calls, fail = [], None

        @classmethod
        def set_link(cls, up):
            cls.calls.append(up)
            if cls.fail:
                raise kvaser_link.LinkError(cls.fail)
            return "kvaser link %s" % ("up" if up else "down")

        @staticmethod
        def link_void(e, t0, t1):
            return kvaser_link.link_void(e, t0, t1, lambda s, u: (
                "2026-11-13T12:00:00.1-08:00 h kernel: usb 1-3.2.1: error -71\n"))
    for mode in ("ack", "noack", "com3_none", "com3_pcan_normal", "com3_pcan_listen"):
        mode_link(mode, Link)
    check(Link.calls == [True, False, False, False, False],
          "link: up for ack only; noack and every com3 boot down -> %s" % Link.calls)
    Link.fail = "the kvaser link reads up in sysfs, and this check needs it down"
    ok, ln = mode_link("noack", Link)
    check(not ok and ln.startswith("VOID: kvaser link: the kvaser link reads up")
          and "error -71" in ln,
          "link: an UP link under noack is caught, a VOID line with the kernel's lines")

    # 2026-11-19, the charge-interposer reviewer's PCAN ruling 1: a witness
    # whose recv raises mid-window makes the boot VOID with the error, never
    # judged on the rows it read before. run_boot end to end, with the real
    # Witness on a stand-in bus, the serial tap a stand-in, sleeps short.
    for line in reader_death_checks():
        check(*line)

    print("\nbench_powerup_round self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def reader_death_checks():
    """-> [(ok, what)]: run_boot with the PCAN witness on a stand-in bus.
    The bus gives three 0x7F4 attempts, then either raises a PCAN status
    (the boot is VOID with the error, no FAIL line, and the case's exit
    code VOID's) or goes quiet (the same three rows are judged: a FAIL, the
    short row list a dead reader would have left)."""
    import types
    import adapter_roles
    import run_scenario
    import scratch_dir
    real_time = time
    out = []
    serial = ["ESP-ROM:esp32s3-20210327",
              "spec 2.3: vehicle port in normal mode for 61000 us at boot, "
              "round 0 of 4 sent in 50025 us",
              "  MODE veh=listen-only core wants listen-only | switches=1 "
              "refused=0 suppressed=0 | round=0/4 in 50025us | boot=61000us"]

    class Tap(object):
        def __init__(self, port, baud, log):
            self.log = log
            open(log, "w").close()

        def start(self):
            pass

        def reset_board(self):
            with open(self.log, "a") as f:
                f.write("RESET SENT\n" + "\n".join(serial) + "\n")

        def stop(self):
            pass

    def frames():
        return [types.SimpleNamespace(timestamp=1.0 + i * 0.000258,
                                      arbitration_id=0x7F4, is_error_frame=False,
                                      data=bytes(8)) for i in range(3)]

    class QuietBus(reader_death.RaisingBus):
        def recv(self, timeout=None):
            if self.frames:
                return self.frames.pop(0)
            real_time.sleep(0.005)
            return None

    exc = RuntimeError("A PCAN status 0x40000 BUSPASSIVE")
    saved = (adapter_roles.pcanfd_report, run_scenario.SerialTap, globals()["time"])
    d = scratch_dir.make("powerup-reader-death")
    try:
        run_scenario.SerialTap = Tap
        globals()["time"] = types.SimpleNamespace(
            sleep=lambda s: real_time.sleep(0.05), time=real_time.time,
            perf_counter=real_time.perf_counter)
        args = types.SimpleNamespace(port="/dev/x2", wican_state="test")
        for mode in ("noack", "ack"):
            adapter_roles.pcanfd_report = lambda *a, **k: reader_death.RaisingBus(frames(), exc)
            ok, lines = run_boot(args, d, 1, mode)
            voids = [x for x in lines if x.startswith("VOID")]
            out.append((ok == reader_death.DIED and len(voids) == 1
                        and "VOID %s_01: the pcan witness reader died" % mode in voids[0]
                        and "BUSPASSIVE" in voids[0]
                        and not any(x.startswith("FAIL") for x in lines),
                        "%s CAN-FAIL: a witness that raises mid-window makes "
                        "the boot VOID with the error, no FAIL line: %s"
                        % (mode, voids or lines[-1:])))
            adapter_roles.pcanfd_report = lambda *a, **k: QuietBus(frames())
            ok, lines = run_boot(args, d, 2, mode)
            out.append((ok is False and any(x.startswith("FAIL") for x in lines),
                        "%s: the same three rows from a witness that lived are "
                        "judged, a FAIL (what a dead reader's rows were before)"
                        % mode))
            # the reviewer's PCAN item 2 (2026-11-20): an overrun row from the
            # PCAN opened with bus states reported VOIDs the boot as lost frames
            R = reader_death.report_rows()
            adapter_roles.pcanfd_report = lambda *a, **k: reader_death.report_bus(
                frames() + [R["passive"], R["overrun"]])
            ok, lines = run_boot(args, d, 3, mode)
            voids = [x for x in lines if x.startswith("VOID")]
            out.append((ok == reader_death.DIED and len(voids) == 1
                        and "VOID %s_03: the pcan witness reader lost frames" % mode in voids[0]
                        and not any(x.startswith("FAIL") for x in lines),
                        "%s CAN-FAIL: an overrun row makes the boot VOID as lost "
                        "frames, no FAIL line: %s" % (mode, voids or lines[-1:])))
        # the Witness itself: a state row is not an error row, a bus error is
        R = reader_death.report_rows()
        got = []
        adapter_roles.pcanfd_report = lambda *a, **k: got.append(k) or reader_death.report_bus(
            [R["frame"], R["passive"], R["ack"]])
        w = Witness(acks=False)
        real_time.sleep(0.2)
        rows = w.take()
        w.close()
        out.append(([(r[1], r[2]) for r in rows] == [(0x7F4, False),
                                                    (R["ack"].arbitration_id, True)]
                    and w.death.states == 1 and w.death.error is None,
                    "Witness: the frame and the bus error are rows (err only on "
                    "the bus error), the state row kept out and counted: %s"
                    % [(hex(r[1]), r[2]) for r in rows]))
        out.append((len(got) == 1 and "state" in got[0] and "auto_reset" not in got[0],
                    "Witness: listen-only opens pcanfd_report with state=PASSIVE: %s" % got))
        out.append((bench_rc([True, reader_death.DIED]) == 1
                    and bench_rc([True, None]) == 0
                    and bench_rc([False, True]) == 1,
                    "the exit code: a dead witness's boot is not a pass (1, "
                    "VOID by its line), recorded and PASS boots 0, a FAIL 1"))
        out.append((VERDICT_WORD[reader_death.DIED].startswith("VOID"),
                    "its verdict word is VOID"))
    finally:
        adapter_roles.pcanfd_report, run_scenario.SerialTap, globals()["time"] = saved
        scratch_dir.remove(d)
    return out


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
    ap.add_argument("--lead-timing", nargs="+", metavar="WITNESS",
                    help="saved witness_noack_NN.txt files: print each one's "
                         "leading attempts by timing and its error rows")
    return ap


def read_witness(path):
    """Rows (t_device, id, err, data, t_host) from a saved witness file."""
    rows = []
    for ln in open(path, encoding="utf-8"):
        if ln.startswith("#") or not ln.strip():
            continue
        t, h, fid, err, data = ln.split()[:5]
        rows.append((float(t), int(fid, 16), err == "1", bytes.fromhex(data),
                     float(h)))
    return rows


def lead_timing_report(paths):
    for p in paths:
        lead, mid, _ = split_attempts(attempt_rows(read_witness(p)))
        n, gap, act = lead_from_timing(lead, mid)
        print("%s: leading attempts by timing %s (%.3f ms over %.0f us), "
              "error rows %d" % (p, n, (gap or 0) * 1000, (act or 0) * 1e6,
                                 len(lead)))
    return 0


# --can-fail: each edit must make --self-test fail (script_can_fail.py)
MUTANTS = [
    ("the u + t <= 1 rule restored",
     "    elif strict and u > 0:\n",
     "    elif (strict and u > 0) or u + t > 1:\n"),
    ("strict not judged",
     "    elif strict and u > 0:\n", "    elif False:\n"),
    ("strict on by default",
     "def score_noack(rows, serial, capture_end_t, strict=False, lead_by=\"rows\"):",
     "def score_noack(rows, serial, capture_end_t, strict=True, lead_by=\"rows\"):"),
    ("strict also demands t=0",
     "    elif strict and u > 0:\n", "    elif strict and (u > 0 or t > 0):\n"),
    ("u > 1 not judged",
     "    elif u > 1:\n", "    elif False:\n"),
    ("u < 0 not judged",
     "    elif u < 0:\n", "    elif False:\n"),
    ("t > 1 not judged",
     "    if t > 1:\n", "    if False:\n"),
    ("the switch never reaches the scorer",
     "strict=bool(getattr(args, \"strict_port\", False)),",
     "strict=False,"),
    ("the switch on without the option",
     "strict=bool(getattr(args, \"strict_port\", False)),",
     "strict=bool(getattr(args, \"strict_port\", True)),"),
    ("--strict-port defaults on",
     "    ap.add_argument(\"--strict-port\", action=\"store_true\",",
     "    ap.add_argument(\"--strict-port\", action=\"store_false\","),
    # 2026-11-13, the reviewer's A
    ("link: up for every mode", "    return mode == \"ack\"", "    return True"),
    ("link: up for the com3 boots too", "    return mode == \"ack\"",
     "    return mode == \"ack\" or mode.startswith(\"com3\")"),
    ("link: a failure not VOID", "        return False, link.link_void(e, t0, time.time())",
     "        return True, str(e)"),
    # 2026-11-18, the leading attempts by timing on Linux (ruling on 8b8e)
    ("timing: rows on Linux too",
     "    return \"timing\" if (platform or sys.platform).startswith(\"linux\") else \"rows\"",
     "    return \"rows\""),
    ("timing: score_boot never passes it",
     "lead_by=lead_by_default(getattr(args, \"platform\", None)))",
     "lead_by=\"rows\")"),
    ("timing: no stuff bits", "        if run == 5:\n", "        if False:\n"),
    ("timing: the passive attempt's length used",
     "ACTIVE_TAIL_BITS = 2 + 6 + 8 + 3 ", "ACTIVE_TAIL_BITS = 2 + 6 + 8 + 3 + 8 "),
    ("timing: from the last error row", "    gap = v[0] - lead[0][0]",
     "    gap = v[0] - lead[-1][0]"),
    ("timing: rounded up", "    return int(round(gap / act)), gap, act",
     "    return int(gap / act) + 1, gap, act"),
    ("timing: the 16 judged on rows", "    if n_lead != ERROR_ACTIVE_ATTEMPTS:",
     "    if len(lead) != ERROR_ACTIVE_ATTEMPTS:"),
    # 2026-11-19, the charge-interposer reviewer's PCAN ruling 1
    ("reader death: the witness's recv unguarded",
     "        reader_death.guard(self.death, self.stop_ev, self._step)",
     "        while not self.stop_ev.is_set():\n            self._step()"),
    ("reader death: a dead witness's rows judged",
     "    if wit and wit.death.error:", "    if False:"),
    ("reader death: a dead witness's boot exits 0",
     "    return 1 if any(o is False or o == reader_death.DIED for o in oks) else 0",
     "    return 1 if any(o is False for o in oks) else 0"),
    # 2026-11-20, the reviewer's PCAN item 2
    ("PCAN item 2: the witness reads unscreened",
     "        m = reader_death.screen(self.bus, self.bus.recv(timeout=0.05), self.death)",
     "        m = self.bus.recv(timeout=0.05)"),
]


def main():
    ap = build_parser()
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if a.lead_timing:
        return lead_timing_report(a.lead_timing)
    if not a.port or not a.mode:
        ap.error("--port and --mode are required for a bench run")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
