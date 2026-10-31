"""Spec 8.3 (a9a8c36c3c52bbb9, user 2026-10-14): bench and bring-up builds do
not hold output for the USB host -- output a host is not reading is dropped,
after at most 1 ms once each time a host stops reading, so an attached laptop
that stops reading cannot stall bridging or lengthen the power-up window.
Host side of the reviewer's approved no-hands plan (2026-10-14).

Every verdict is RELATIVE to a baseline taken in the same bench session
(reviewer): 8.3 gives no number, its test is "cannot stall or lengthen".

  baseline-boots  RTS reset with a host READING (bench_powerup_round's own
                  ack / noack boots); the boot window from the boot line and
                  the MODE line. Writes boot_baseline_<ack|noack>.json.
  noread-boots    the port held open and NOT read until the host side is
                  shown full (a hole in the board's t= sequence), then an RTS
                  reset, ~10 s more unread, then reads resume. boot= comes
                  from the first MODE line after resuming (on a fixed build
                  the boot line itself is dropped by design).
  close-boots     a plain open, then a close: the close resets the board with
                  NO host (bench 2026-10-14). The PCAN records across the
                  reboot (the 28-errored-attempts question). boot= from the
                  first MODE line after re-opening.
  bridge          the Kvaser injects 0x6A2 (DLC 8, 24-bit sequence) every
                  5 ms on the vehicle segment; the PCAN (device clock)
                  witnesses it on the wire, the Innomaker the forwarded
                  copies on the charger segment. --com3 absent writes the
                  baseline; --com3 cycles holds the port open throughout and
                  alternates read READ_S / stop >= STOP_FLOOR_S, 5 times.

WHY THE STOPS ARE LONG AND CHECKED. While a port is open, the Windows CDC
driver keeps pulling the board's output into its own buffer whether this
script reads or not, so the board meets a host that is not taking data only
once that buffer is full. A stop is therefore judged only if a HOLE in the
board's t= sequence (status lines missing) falls inside it; a stop with no
hole is VOID, never PASS. A hole is necessary, not sufficient: the host
driver could be the one dropping. The control build (6cacaf88) is what shows
the cases can fail.

WHY GAPS ARE DIFFERENCED. The injection is paced by the host; its jitter is on
the wire. Per sequence number, charger-side gap minus vehicle-side gap (the
PCAN's device clock) cancels it, leaving what the bridge added.

THE CHARGER-SIDE CLOCK is the Innomaker's own stamp only if it advertises
hardware timestamps; otherwise gs_usb returns 0 for every frame and the host
receipt time is used, which carries USB batching. A constant stamp is never
used as a clock. Which clock was used is printed with every result.

Reset over USB only (RTS pulse or a port close). No buttons, no replug.

    py -3.14 bench_usb_host.py --self-test
    py -3.14 bench_usb_host.py --can-fail
    py -3.14 bench_usb_host.py --port COM3 --case baseline-boots --ack --out D
    py -3.14 bench_usb_host.py --port COM3 --case bridge --com3 absent --out D
    py -3.14 bench_usb_host.py --port COM3 --case bridge --com3 cycles --out D

BASELINES AND EXIT CODES (tester, 2026-10-21, after the a25e chain run, in
which every case had its own folder):
  - --baseline-dir names the folder of the run that wrote the baseline
    (boot_baseline_<ack|noack>.json, bridge_baseline.json); without it the
    baseline is read from --out, as when every case shared one folder.
  - The exit code is verdict_rc over the case's items: 1 if any FAILED, 3
    if every one was VOID (a case that tested nothing never exits 0), 0
    otherwise. baseline-boots counts each boot's 2.3 verdict (for noack,
    bench_powerup_round's invariant), so a failing boot is no longer a
    PASS of the case.

CAPACITY AND THE WICAN'S USB (reviewer ruling, 2026-10-22, after 700f's
noread-ack VOID at 480 s: the host side held 12.6 kB on 2026-10-14 and
98.6 kB on 700f, both with "rx asked for 64 B"):
  - every not-reading boot prints a FIGURE line, not judged: the board's
    output rate during the stop (bytes per board second, text + CRLF, from
    the whole status intervals that arrived), the bytes that implies over
    the stop, where the hole began, and the bytes the host side held
    (rate x fill). With no hole, the host side held more than the stop's
    bytes. So capacity drift shows run by run.
  - every case prints, at its start, a FIGURE line with the WiCAN's USB
    state: the Espressif USB-JTAG ports (303A:1001) present besides
    --port, by name and location, never by serial number (the chip's MAC).

Tester, 2026-10-14.
"""

import argparse
import datetime
import json
import os
import re
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never
# from the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import scratch_dir                  # noqa: E402

INJECT_ID = 0x6A2          # clear of the truck, the WiCAN, the interposer
PERIOD_S = 0.005           # reviewer: 10 ms or faster
STATUS_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
READ_S = 2.0
STOP_FLOOR_S = 240.0       # reviewer: the hole is the criterion, 240 s floor
CYCLES = 5
BOOT_MARGIN_US = 5000      # reviewer: baseline max + 5 ms
GAP_MARGIN_S = {"device": 0.002, "host": 0.005}
STATUS_T = re.compile(r"^t=(\d+)s state=")
V2C_RE = re.compile(r"\bv2c=(\d+)")
DIAG_RE = re.compile(r"^diag st=")
HOLE_FACTOR = 1.5          # a hole: a step over 1.5 x the board's own period
LINE_END_B = 2             # each serial line counted as text + CRLF
ESP_USB_JTAG = (0x303A, 0x1001)   # the X2's and the WiCAN's USB port


# --- pure: parsing and scoring (--self-test covers these) -------------------

def verdict_rc(oks):
    """A case's exit code from its items' verdicts (True / False / None for
    VOID): 1 if any FAILED, 3 if there were none or every one was VOID, else
    0. A case that tested nothing must not exit 0 (a25e noread-boots)."""
    oks = list(oks)
    if any(o is False for o in oks):
        return 1
    if not oks or all(o is None for o in oks):
        return 3
    return 0


def baseline_boots_rc(vals):
    """baseline-boots: each boot's 2.3 verdict (bench_powerup_round's; for
    noack its no-ACKer invariant) decides the case, as in
    powerup-round-noack. a25e's usb-host-baseline-noack printed three
    False verdicts and exited 0."""
    return verdict_rc(v["ok_2p3"] for v in vals)


def baseline_dir(a, out):
    """Where the baseline files are read: --baseline-dir, else --out."""
    return getattr(a, "baseline_dir", None) or out


def bridge_baseline_path(a, out):
    """The absent run writes the bridge baseline into its own folder; the
    cycles run reads it from --baseline-dir (the absent run's folder), or
    without it from --out."""
    d = out if a.com3 == "absent" else baseline_dir(a, out)
    return os.path.join(d, "bridge_baseline.json")


def status_ts(lines):
    """-> [(index, t)] for the board's t=Ns status lines."""
    out = []
    for i, ln in enumerate(lines):
        m = STATUS_T.match(ln)
        if m:
            out.append((i, int(m.group(1))))
    return out


def own_period(ts):
    """The status period from the board's own steps (median of positive
    steps); None with too few to say."""
    d = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    return statistics.median(d) if len(d) >= 2 else None


def holes(ts, period):
    """-> [(t_before, t_after)] where consecutive status lines step by more
    than HOLE_FACTOR x period. A decrease (a reboot) is not a hole."""
    return [(a, b) for a, b in zip(ts, ts[1:])
            if b > a and (b - a) > HOLE_FACTOR * period]


def stop_premise(ts, period, stop_lo, stop_hi, ends_in_reset=False):
    """Was the host side full during a stop? ts: board t values received
    (in order, one boot); [stop_lo, stop_hi]: the stop in BOARD seconds.
    -> (met, fill_s, why). A hole that overlaps the stop meets it; fill_s is
    how long after the stop began the last line before the hole was
    printed. ends_in_reset: the stop ends at a reboot, so no later line of
    this boot can close the hole; the stop's end stands in for it."""
    if period is None:
        return False, None, "VOID: too few status lines to know the period"
    if ends_in_reset:
        ts = list(ts) + [stop_hi]
    # the fill is the hole with the LARGEST overlap with the stop; any
    # other hole is listed. Bench 2026-10-14 07:05 (fix build, not-reading
    # boot noack_02): a single dropped line at t=85 while the driver was
    # still absorbing came first, and taking the first hole reported a
    # 47 s fill where the host side filled at t=140 (107 s).
    hs = [(min(b, stop_hi) - max(a, stop_lo), a, b)
          for a, b in holes(ts, period) if b > stop_lo and a < stop_hi]
    if hs:
        _, a, b = max(hs)
        other = sorted((x, y) for _, x, y in hs if (x, y) != (a, b))
        return True, max(0.0, a - stop_lo), (
            "hole t=%d..%d s%s" % (a, b, "".join(
                "; also t=%d..%d" % o for o in other)))
    inside = [t for t in ts if stop_lo <= t <= stop_hi]
    return False, None, ("VOID: no hole in the board's t= sequence during the "
                         "stop (%d status line(s) inside it arrived) -- the "
                         "host side never filled, the stop tested nothing"
                         % len(inside))


WRAP_S = 2 ** 32 / 1e6     # gs_usb's device stamp: 32-bit microseconds


def unwrap_us32(stamps):
    """gs_usb's hardware stamp is a 32-bit microsecond counter: it wraps
    every 4,294.97 s. A backward step of more than half the range is a wrap
    and adds WRAP_S from there on; a small backward step (two frames out of
    order) is left alone. Bench 2026-10-14 06:06: the control's 20 min
    cycles run crossed one wrap (4294.96 -> 0.0002 s at row 132,225), the
    stamp's span no longer matched the host's, and the device clock was
    refused -- the run came back VOID for want of a host-clock baseline."""
    out, add, ref = [], 0.0, None
    for s in stamps:
        c = s + add
        if ref is not None and c < ref - WRAP_S / 2:
            add += WRAP_S                  # a wrap
            c += WRAP_S
        elif ref is not None and c > ref + WRAP_S / 2:
            out.append(c - WRAP_S)         # a late frame from before it
            continue
        out.append(c)
        ref = c
    return out


def choose_clock(stamps, hosts):
    """-> ('device'|'host', times). The adapter's stamps are used only if
    they move with the host clock: a constant (gs_usb without hardware
    timestamps returns 0) or near-constant stamp is refused."""
    if len(stamps) < 2:
        return "host", list(hosts)
    hs = max(hosts) - min(hosts)
    ds = max(stamps) - min(stamps)
    if hs > 0 and 0.5 * hs < ds < 2.0 * hs:
        return "device", list(stamps)
    return "host", list(hosts)


def gap_excess(veh, chg):
    """veh, chg: {seq: t} (vehicle on the PCAN device clock, charger on the
    chosen clock). -> [(seq, excess_s)] for every consecutive pair present on
    both sides: charger gap minus vehicle gap."""
    out = []
    for s in sorted(chg):
        n = s + 1
        if n in chg and s in veh and n in veh:
            out.append((n, (chg[n] - chg[s]) - (veh[n] - veh[s])))
    return out


def score_bridge(offered, veh, chg, clock, baseline_max, chg_host=None,
                 stops=()):
    """-> (verdict, lines). verdict True/False/None (None = VOID).
    offered: the sequence numbers sent. baseline_max: the COM3-absent run's
    largest excess on the same clock, or None when this IS the baseline.
    chg_host: {seq: host time} to place the largest gap against the stops
    [(k, host_start, host_end)]."""
    lines = []
    miss_v = [s for s in offered if s not in veh]
    if miss_v:
        return None, ["VOID: %d offered frame(s) never reached the vehicle "
                      "wire (first seq %d) -- an injection or witness fault, "
                      "not the board" % (len(miss_v), miss_v[0])]
    miss_c = [s for s in offered if s not in chg]
    ex = gap_excess(veh, chg)
    if not ex:
        return False, ["FAIL: no consecutive pair on both sides -- nothing "
                       "to measure"]
    seq_max, emax = max(ex, key=lambda e: e[1])
    lines.append("%d offered, %d on the vehicle wire, %d bridged; largest "
                 "excess %.3f ms at seq %d (%s clock)"
                 % (len(offered), len(offered) - len(miss_v),
                    len(offered) - len(miss_c), emax * 1000, seq_max, clock))
    if chg_host and seq_max in chg_host:
        th = chg_host[seq_max]
        where = [k for k, a, b in stops if a <= th <= b]
        rel = ["stop %d +%.3f s" % (k, th - a) for k, a, b in stops
               if a <= th <= b]
        lines.append("  largest gap at host %.3f: %s"
                     % (th, rel[0] if where else "outside every stop"))
    ok = True
    if miss_c:
        ok = False
        lines.append("FAIL: %d frame(s) not bridged (first seq %d)"
                     % (len(miss_c), miss_c[0]))
    if baseline_max is not None:
        lim = baseline_max + GAP_MARGIN_S[clock]
        lines.append("  limit: baseline max %.3f ms + %.0f ms (%s clock) = "
                     "%.3f ms" % (baseline_max * 1000,
                                  GAP_MARGIN_S[clock] * 1000, clock,
                                  lim * 1000))
        if emax > lim:
            ok = False
            lines.append("FAIL: the bridge added %.3f ms over the injected "
                         "spacing, past the limit" % (emax * 1000))
    return ok, lines


def score_boot(value_us, baseline_us, label):
    """-> (ok, line). value_us None (no MODE line after resuming) FAILS."""
    if not baseline_us:
        return False, "FAIL %s: no same-session baseline" % label
    lim = max(baseline_us) + BOOT_MARGIN_US
    if value_us is None:
        return False, ("FAIL %s: no boot= from a MODE line after reads "
                       "resumed" % label)
    ok = value_us <= lim
    return ok, ("%s %s: boot window %d us; baseline %s us, limit %d us; "
                "%+d us over the baseline max"
                % ("PASS" if ok else "FAIL", label, value_us,
                   "/".join(str(b) for b in baseline_us), lim,
                   value_us - max(baseline_us)))


def first_mode_boot(lines):
    """boot= from the first MODE line in lines, else None."""
    for ln in lines:
        m = re.search(r"\bboot=(\d+)us", ln)
        if m and "MODE" in ln:
            return int(m.group(1))
    return None


def split_at_reboot(st):
    """-> (pre, post): the lines before and from the reboot. The reboot is
    the first ROM banner, or -- a build that drops output may drop the
    banner too -- the first t= that goes backwards. Bench 2026-10-14 05:36
    (control, not-reading boot): no banner arrived, and scoring the premise
    on the unsplit record put the post-reset t=10..20 inside the stop and
    reported a hole t=20..276 with fill 0 s; the real hole was t=160..276."""
    cut = next((i for i, ln in enumerate(st) if "ESP-ROM:" in ln), None)
    if cut is None:
        ts = status_ts(st)
        for (i0, t0), (i1, t1) in zip(ts, ts[1:]):
            if t1 < t0:
                cut = i1
                break
    if cut is None:
        return st, []
    return st[:cut], st[cut:]


def score_noread(st, off, hs, hr, base, tag):
    """One not-reading boot from its serial record. st: the lines in order;
    off: host - board t from live lines; hs, hr: host times of the stop and
    the reset. -> (verdict True/False/None, lines)."""
    pre, post = split_at_reboot(st)
    pts = [t for _, t in status_ts(pre)]
    per = own_period(pts)
    if off is None:
        met, fill, why = (False, None, "VOID: no live status line to place "
                                       "the stop on the board clock")
    else:
        met, fill, why = stop_premise(pts, per, hs - off, hr - off,
                                      ends_in_reset=True)
    bl = any("at boot" in ln for ln in post)
    bv = first_mode_boot(post)
    ok, ln = score_boot(bv, base, tag)
    if not post:
        ok, ln = None, "VOID %s: no reboot found in the record" % tag
    elif not met:
        ok, ln = None, "VOID %s: %s (boot= %s us recorded)" % (tag, why, bv)
    out = ["%s: premise %s, fill %s s; boot line %s; ROM banner %s" % (
        tag, why if met else "NOT met",
        "%.0f" % fill if fill is not None else "-",
        "ARRIVED" if bl else "dropped",
        "ARRIVED" if any("ESP-ROM:" in x for x in post) else "dropped"),
        "  " + ln]
    if off is not None:
        out.append("  " + capacity_figure(pre, hs - off, hr - off,
                                          fill if met else None, tag))
    return ok, out


def capacity_figure(pre, stop_lo, stop_hi, fill, tag):
    """The host side's capacity, as a FIGURE line (reviewer 2026-10-22, not
    judged). pre: the boot's lines before the reboot; [stop_lo, stop_hi]:
    the stop in board seconds; fill: seconds from the stop to the hole, or
    None if there was none. The rate counts the bytes between consecutive
    t= lines on the interval (a, b] between them, for the intervals wholly
    inside the stop with no line missing: a step of at most HOLE_FACTOR x
    their median, so neither the hole nor a lone dropped line (bench
    2026-10-14 07:05, t=80..90) lowers it."""
    steps, acc, prev = [], 0, None
    for ln in pre:
        acc += len(ln) + LINE_END_B
        m = STATUS_T.match(ln)
        if m:
            t = int(m.group(1))
            if prev is not None and prev >= stop_lo and t <= stop_hi and \
                    t > prev:
                steps.append((t - prev, acc))
            prev, acc = t, 0
    if not steps:
        return ("FIGURE capacity %s: no whole status interval inside the "
                "stop -- no rate" % tag)
    per = statistics.median(s for s, _ in steps)
    whole = [(s, b) for s, b in steps if s <= HOLE_FACTOR * per]
    secs = sum(s for s, _ in whole)
    rate = sum(b for _, b in whole) / float(secs)
    stop_b = rate * (stop_hi - stop_lo)
    held = ("hole began %.0f s into the stop -> the host side held ~%.0f B"
            % (fill, rate * fill) if fill is not None else
            "no hole -> the host side held all of it, > %.0f B" % stop_b)
    return ("FIGURE capacity %s: the board printed %.1f B/s during the stop "
            "(%d s of whole status intervals, text + CRLF), ~%.0f B over the "
            "%.0f s stop; %s" % (tag, rate, secs, stop_b, stop_hi - stop_lo,
                                 held))


def wican_usb_figure(ports, x2_port):
    """The WiCAN's USB state at a case's start, as a FIGURE line (reviewer
    2026-10-22). ports: [(device, vid, pid, location)] -- the serial number
    is never taken, since on a USB-JTAG port it is the chip's MAC."""
    if ports is None:
        return "FIGURE WiCAN USB at case start: unknown (port list unavailable)"
    others = [p for p in ports if (p[1], p[2]) == ESP_USB_JTAG and
              p[0].upper() != (x2_port or "").upper()]
    if not others:
        return ("FIGURE WiCAN USB at case start: absent -- no Espressif "
                "USB-JTAG port besides %s" % x2_port)
    return ("FIGURE WiCAN USB at case start: present -- Espressif USB-JTAG "
            "port(s) besides %s: %s" % (x2_port, ", ".join(
                "%s (location %s)" % (p[0], p[3]) for p in others)))


def usb_ports():
    """[(device, vid, pid, location)] from pyserial, or None."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    return [(p.device, p.vid, p.pid, p.location)
            for p in list_ports.comports()]


def rescore_noread_file(path, base, tag):
    """Re-score a saved not-reading boot (host<TAB>line, '# ' markers)."""
    stamped = []
    for raw in open(path, encoding="utf-8", errors="replace"):
        h, _, ln = raw.rstrip("\n").partition("\t")
        stamped.append((float(h), ln))
    hs = next(h for h, ln in stamped if ln == "# READS STOPPED")
    hr = next(float(ln.split("host=")[1]) for h, ln in stamped
              if ln.startswith("# RESET SENT"))
    off = board_offset([(h, ln) for h, ln in stamped if h < hs])
    st = [ln for h, ln in stamped if not ln.startswith("# ")]
    return score_noread(st, off, hs, hr, base, tag)


def diag_rate_board(lines):
    """-> (diag lines per minute, board seconds covered), on the BOARD's
    clock: diag lines between two consecutive status lines one period apart
    (an unbroken interval), over those intervals' seconds. Bench 2026-10-14
    07:54: the host-time rate read 1783.8/min because each resume delivers
    the driver's backlog in one burst; host time cannot rate it."""
    ts = status_ts(lines)
    per = own_period([t for _, t in ts])
    if per is None:
        return None, 0
    n = secs = 0
    for (i0, t0), (i1, t1) in zip(ts, ts[1:]):
        if t1 - t0 == per:
            n += sum(1 for ln in lines[i0 + 1:i1] if DIAG_RE.match(ln))
            secs += per
    return (60.0 * n / secs if secs else None), secs


def diag_per_minute(stamped, phases):
    """stamped: [(host, line)]; phases: [(a, b)] host windows. -> rate of
    diag lines per minute over the windows, and the seconds covered."""
    n, secs = 0, 0.0
    for a, b in phases:
        secs += b - a
        n += sum(1 for h, ln in stamped if a <= h <= b and DIAG_RE.match(ln))
    return (60.0 * n / secs if secs else None), secs


# --- hardware ---------------------------------------------------------------

class HostPort(object):
    """The X2's USB port, read only while `reading` is set. lines_low opens
    with DTR and RTS low so neither the open nor the close resets the board
    (bench 2026-10-14); otherwise a plain pyserial open, whose close does
    reset it."""

    def __init__(self, port, path, lines_low=True, rx_size=None):
        import serial
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = port, 115200, 0.1
        if lines_low:
            s.dtr, s.rts = False, False
        s.open()
        self.rx_note = "driver default"
        if rx_size:
            try:
                s.set_buffer_size(rx_size=rx_size)
                self.rx_note = "asked for %d B" % rx_size
            except Exception as exc:                   # noqa: BLE001
                self.rx_note = "set_buffer_size refused: %s" % exc
        self.ser = s
        self.stamped = []
        self.fh = open(path, "a", encoding="utf-8", errors="replace")
        self.reading = threading.Event()
        self.reading.set()
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        buf = b""
        while not self.stop_ev.is_set():
            if not self.reading.is_set():
                time.sleep(0.01)
                continue
            with self.lock:
                try:
                    raw = self.ser.read(4096)
                except Exception as exc:               # noqa: BLE001
                    self.mark("SERIAL ERROR %s" % exc)
                    time.sleep(0.1)
                    continue
            if not raw:
                continue
            h = time.perf_counter()
            buf += raw
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                s = ln.decode("utf-8", errors="replace").rstrip("\r")
                self.stamped.append((h, s))
                self.fh.write("%.6f\t%s\n" % (h, s))
            self.fh.flush()

    def mark(self, text):
        h = time.perf_counter()
        self.stamped.append((h, "# " + text))
        self.fh.write("%.6f\t# %s\n" % (h, text))
        self.fh.flush()
        return h

    def stop_reading(self):
        self.reading.clear()
        time.sleep(0.05)                 # let an in-flight read finish
        return self.mark("READS STOPPED")

    def resume(self):
        h = self.mark("READS RESUMED")
        self.reading.set()
        return h

    def hard_reset(self):
        """esptool's USB-Serial-JTAG HardReset, as run_scenario.SerialTap
        sends it: DTR low, RTS high 0.2 s, low, 0.2 s, each RTS change
        followed by a DTR write so usbser.sys sends the line state."""
        with self.lock:
            s = self.ser
            s.dtr = False
            t0 = time.perf_counter()
            s.rts = True
            s.dtr = s.dtr
            time.sleep(0.2)
            s.rts = False
            s.dtr = s.dtr
            time.sleep(0.2)
        self.mark("RESET SENT host=%.6f" % t0)
        return t0

    def close(self):
        self.stop_ev.set()
        self.t.join(timeout=2.0)
        h = self.mark("PORT CLOSE")
        self.ser.close()
        self.fh.close()
        return h


class Rx(object):
    """A receive thread on a python-can bus: rows (stamp, host, id, err,
    data), host on time.perf_counter()."""

    def __init__(self, bus, name):
        self.bus, self.name = bus, name
        self.rows, self.lock = [], threading.Lock()
        self.stop_ev = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self.stop_ev.is_set():
            m = self.bus.recv(timeout=0.05)
            if m is None:
                continue
            if getattr(m, "is_rx", True) is False:
                continue                 # an echo of our own send
            with self.lock:
                self.rows.append((m.timestamp, time.perf_counter(),
                                  m.arbitration_id, bool(m.is_error_frame),
                                  bytes(m.data)))

    def take(self):
        with self.lock:
            r, self.rows = self.rows, []
        return r

    def close(self):
        self.stop_ev.set()
        self.t.join(timeout=2.0)
        self.bus.shutdown()


def open_pcan(acks):
    import can
    kw = dict(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    if acks:
        kw["auto_reset"] = True
    else:
        kw["state"] = can.BusState.PASSIVE
    return can.Bus(**kw)


def open_kvaser():
    import can
    return can.Bus(interface="kvaser", channel=0, bitrate=500000)


def open_innomaker():
    import can
    import libusb_package
    d = os.path.dirname(libusb_package.get_library_path())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    return can.Bus(interface="gs_usb", channel="can0", index=0,
                   bitrate=500000)


class Injector(object):
    """0x6A2 every PERIOD_S on the Kvaser, payload = 24-bit seq + 5 fixed
    bytes. Records (seq, host send time); send errors counted."""

    def __init__(self, bus):
        self.bus, self.sent, self.errs = bus, [], 0
        self.stop_ev = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.t.start()

    def _run(self):
        import can
        seq, nxt = 0, time.perf_counter()
        while not self.stop_ev.is_set():
            nxt += PERIOD_S
            while True:
                d = nxt - time.perf_counter()
                if d <= 0:
                    break
                if d > 0.0015:
                    time.sleep(d - 0.001)
            data = bytes([(seq >> 16) & 0xFF, (seq >> 8) & 0xFF, seq & 0xFF,
                          0xA5, 0x5A, 0x83, 0x00, 0x00])
            try:
                self.bus.send(can.Message(arbitration_id=INJECT_ID,
                                          is_extended_id=False, data=data),
                              timeout=0.05)
                self.sent.append((seq, time.perf_counter()))
            except Exception:                          # noqa: BLE001
                self.errs += 1
            seq += 1

    def stop(self):
        self.stop_ev.set()
        self.t.join(timeout=2.0)


def seq_of(data):
    return (data[0] << 16) | (data[1] << 8) | data[2]


def write_rows(path, rows, note):
    with open(path, "w") as fh:
        fh.write("# stamp host id err data -- %s\n" % note)
        for r in rows:
            fh.write("%.6f %.6f %03X %d %s\n"
                     % (r[0], r[1], r[2], int(r[3]), r[4].hex()))


def board_offset(stamped):
    """host - board t, from status lines read live (the smallest delay seen
    is the closest to the print)."""
    offs = []
    for h, ln in stamped:
        m = STATUS_T.match(ln)
        if m:
            offs.append(h - int(m.group(1)))
    return min(offs) if offs else None


# --- cases ------------------------------------------------------------------

def case_baseline_boots(a, out):
    import bench_powerup_round as B
    mode = "ack" if a.ack else "noack"
    vals = []
    lines = []
    for n in range(1, a.boots + 1):
        ok, ln = B.run_boot(a, out, n, mode)
        log = os.path.join(out, "serial_%s_%02d.log" % (mode, n))
        ser = open(log, encoding="utf-8", errors="replace").read().splitlines()
        ser = ser[next(i for i, x in enumerate(ser) if "RESET SENT" in x):]
        s = B.parse_serial(ser)
        lines += ln + ["  2.3 verdict for this boot: %s" % ok]
        vals.append({"boot": n, "bl_boot_us": s["bl_boot_us"],
                     "mode_boot_us": s["boot_us"], "ok_2p3": ok})
    rec = {"class": mode, "boots": vals,
           "baseline_us": [v["mode_boot_us"] for v in vals
                           if v["mode_boot_us"] is not None]}
    json.dump(rec, open(os.path.join(out, "boot_baseline_%s.json" % mode),
                        "w"), indent=1)
    lines.append("baseline %s: MODE boot= %s us (boot line %s)"
                 % (mode, rec["baseline_us"],
                    [v["bl_boot_us"] for v in vals]))
    return baseline_boots_rc(vals), lines


def _baseline(out, mode):
    p = os.path.join(out, "boot_baseline_%s.json" % mode)
    return json.load(open(p))["baseline_us"] if os.path.exists(p) else []


def case_noread_boots(a, out):
    mode = "ack" if a.ack else "noack"
    base = _baseline(baseline_dir(a, out), mode)
    lines, oks = [], []
    for n in range(1, a.boots + 1):
        tag = "noread_%s_%02d" % (mode, n)
        wit = Rx(open_pcan(acks=a.ack), "pcan")
        hp = HostPort(a.port, os.path.join(out, "serial_%s.stamped" % tag),
                      lines_low=True, rx_size=a.rx_size)
        time.sleep(8.0)
        off = board_offset(hp.stamped)
        hs = hp.stop_reading()
        time.sleep(max(a.stop_s, STOP_FLOOR_S))
        hr = hp.hard_reset()
        time.sleep(10.0)
        hp.resume()
        time.sleep(14.0)
        hp.close()
        rows = wit.take()
        wit.close()
        write_rows(os.path.join(out, "witness_%s.txt" % tag), rows,
                   "PCAN %s" % ("normal" if a.ack else "listen-only"))
        st = [ln for h, ln in hp.stamped if not ln.startswith("# ")]
        ok, ln = score_noread(st, off, hs, hr, base, tag)
        lines.append("%s: rx %s; stop host %.3f, reset host %.3f, board "
                     "offset %s" % (tag, hp.rx_note, hs, hr, off))
        lines += ln
        sf = sum(1 for r in rows if not r[3] and r[2] in STATUS_IDS)
        ef = sum(1 for r in rows if r[3])
        lines.append("  PCAN: %d status frame(s), %d error row(s)" % (sf, ef))
        oks.append(ok)
    return verdict_rc(oks), lines


def case_close_boots(a, out):
    mode = "ack" if a.ack else "noack"
    base = _baseline(baseline_dir(a, out), mode)
    lines, oks = [], []
    for n in range(1, a.boots + 1):
        tag = "close_%s_%02d" % (mode, n)
        path = os.path.join(out, "serial_%s.stamped" % tag)
        wit = Rx(open_pcan(acks=a.ack), "pcan")
        hp = HostPort(a.port, path, lines_low=False)
        time.sleep(3.0)
        hc = hp.close()                 # the close resets the board
        time.sleep(3.0)
        hp2 = HostPort(a.port, path, lines_low=True)
        time.sleep(14.0)
        hp2.close()
        rows = wit.take()
        wit.close()
        write_rows(os.path.join(out, "witness_%s.txt" % tag), rows,
                   "PCAN %s, recording across the close at host %.6f"
                   % ("normal" if a.ack else "listen-only", hc))
        st = [ln for h, ln in hp2.stamped]
        ts = [t for _, t in status_ts(st)]
        rebooted = bool(ts) and ts[0] <= 3.0 + 14.0 + 5
        bv = first_mode_boot(st)
        ok, ln = score_boot(bv, base, tag)
        if not rebooted:
            ok, ln = None, ("VOID %s: no reboot seen after the close (first "
                            "t=%s)" % (tag, ts[0] if ts else None))
        near = [r for r in rows if hc - 0.5 <= r[1] <= hc + 3.0]
        lines.append("%s: rebooted=%s; boot line %s" % (
            tag, rebooted, "ARRIVED" if any("at boot" in x for x in st)
            else "dropped"))
        lines.append("  " + ln)
        lines.append("  PCAN across the reboot (close -0.5..+3 s): %d status "
                     "frame(s) %s, %d error row(s)"
                     % (sum(1 for r in near if not r[3]
                            and r[2] in STATUS_IDS),
                        sorted(set("0x%03X" % r[2] for r in near
                                   if not r[3])),
                        sum(1 for r in near if r[3])))
        for x in st:
            if "LOSS" in x or "at boot" in x:
                lines.append("  serial> " + x)
        oks.append(ok)
    return verdict_rc(oks), lines


def case_bridge(a, out):
    tag = "bridge_%s" % a.com3
    pc = Rx(open_pcan(acks=True), "pcan")
    ino = Rx(open_innomaker(), "innomaker")
    kv = open_kvaser()
    inj = Injector(kv)
    hp, stops, reads = None, [], []
    if a.com3 == "cycles":
        hp = HostPort(a.port, os.path.join(out, "serial_%s.stamped" % tag),
                      lines_low=True, rx_size=a.rx_size)
        r0 = time.perf_counter()
        time.sleep(8.0)
        reads.append((r0, time.perf_counter()))
    inj.start()
    if a.com3 == "absent":
        time.sleep(a.absent_s)
    else:
        for k in range(1, a.cycles + 1):
            r0 = time.perf_counter()
            time.sleep(READ_S)
            reads.append((r0, time.perf_counter()))
            hs = hp.stop_reading()
            time.sleep(max(a.stop_s, STOP_FLOOR_S))
            he = hp.resume()
            stops.append((k, hs, he))
        r0 = time.perf_counter()
        time.sleep(8.0)
        reads.append((r0, time.perf_counter()))
    inj.stop()
    time.sleep(1.0)
    gap_end = time.perf_counter()
    if hp:
        r0 = time.perf_counter()
        time.sleep(8.0)
        reads.append((r0, time.perf_counter()))
    vrows, crows = pc.take(), ino.take()
    pc.close()
    ino.close()
    kv.shutdown()
    if hp:
        hp.close()                      # lines low: no reset
    write_rows(os.path.join(out, "vehicle_%s.txt" % tag), vrows,
               "PCAN normal, device stamp")
    write_rows(os.path.join(out, "charger_%s.txt" % tag), crows,
               "Innomaker normal")
    offered = [s for s, _ in inj.sent]
    stamped = hp.stamped if hp else []
    json.dump({"offered": len(offered), "send_errors": inj.errs,
               "stops": stops, "reads": reads, "gap_end": gap_end},
              open(os.path.join(out, "meta_%s.json" % tag), "w"), indent=1)
    v, lines = score_bridge_run(a.com3, vrows, crows, offered, stamped, stops,
                                reads, gap_end, bridge_baseline_path(a, out))
    lines.insert(0, "%s: injector %d sent, %d send error(s)"
                 % (tag, len(offered), inj.errs))
    return verdict_rc([v]), lines


def score_bridge_run(com3, vrows, crows, offered, stamped, stops, reads,
                     gap_end, bpath):
    """Score one bridging run from its rows. Writes bpath for the absent
    run, reads it for the cycles run. -> (verdict, lines)."""
    veh = {seq_of(r[4]): r[0] for r in vrows
           if r[2] == INJECT_ID and not r[3] and len(r[4]) == 8}
    cr = [r for r in crows if r[2] == INJECT_ID and not r[3]
          and len(r[4]) == 8 and r[1] <= gap_end]
    clock, ctimes = choose_clock(unwrap_us32([r[0] for r in cr]),
                                 [r[1] for r in cr])
    chg = {seq_of(r[4]): t for r, t in zip(cr, ctimes)}
    chg_host = {seq_of(r[4]): r[1] for r in cr}
    lines = ["charger clock %s" % clock]
    if com3 == "absent":
        v, ln = score_bridge(offered, veh, chg, clock, None)
        ex = gap_excess(veh, chg)
        json.dump({"clock": clock,
                   "max_excess_s": max(e for _, e in ex) if ex else None,
                   "verdict": v}, open(bpath, "w"), indent=1)
        return v, lines + ln
    b = json.load(open(bpath)) if os.path.exists(bpath) else None
    if not b or b["clock"] != clock or b["max_excess_s"] is None:
        return None, lines + ["VOID: no COM3-absent baseline on the %s clock "
                              "(%s)" % (clock, bpath)]
    v, ln = score_bridge(offered, veh, chg, clock, b["max_excess_s"],
                         chg_host, stops)
    lines += ln
    off = board_offset(stamped)
    pts = [t for _, t in status_ts([x for _, x in stamped])]
    per = own_period(pts)
    n_met = 0
    for k, hs, he in stops:
        met, fill, why = (stop_premise(pts, per, hs - off, he - off)
                          if off is not None else (False, None, "VOID"))
        n_met += 1 if met else 0
        ex_k = [e for s, e in gap_excess(veh, chg)
                if s in chg_host and hs <= chg_host[s] <= he]
        lines.append("  stop %d: %s; fill %s s; largest excess in it %.3f ms"
                     % (k, why, "%.0f" % fill if fill is not None else "-",
                        max(ex_k) * 1000 if ex_k else float("nan")))
    if n_met == 0 and v is True:
        v = None
        lines.append("VOID: no stop met its premise -- the run never showed "
                     "the board a host that was not taking data")
    v2 = [int(m.group(1)) for _, x in stamped
          for m in [V2C_RE.search(x)] if m]
    if len(v2) >= 2:
        lines.append("  board v2c %d -> %d: delta %d vs %d offered"
                     % (v2[0], v2[-1], v2[-1] - v2[0], len(offered)))
        if v2[-1] - v2[0] != len(offered):
            v = False
            lines.append("FAIL: the board's v2c delta disagrees with the "
                         "frames offered")
    rate, secs = diag_rate_board([x for _, x in stamped
                                  if not x.startswith("# ")])
    lines.append("  diag lines on the board clock (unbroken 5 s intervals): "
                 "%s per minute over %d board s (8.3: 1 Hz = 60)"
                 % ("%.1f" % rate if rate is not None else "-", secs))
    sf = [r for r in vrows if not r[3] and r[2] in STATUS_IDS]
    lines.append("  0x7F4-0x7F7 on the PCAN in the run (listen-only, no "
                 "boot): %d" % len(sf))
    return v, lines


def read_rows(path):
    out = []
    for ln in open(path):
        if ln.startswith("#"):
            continue
        st, h, fid, err, data = ln.split()
        out.append((float(st), float(h), int(fid, 16), err == "1",
                    bytes.fromhex(data)))
    return out


def rescore_bridge_dir(d, com3, offered_n=None):
    """Re-score a saved bridging run. The 05:46 control run predates the
    meta file: offered is then --offered, stops come from the serial
    markers, the read phases from the marker pairs, and gap_end is the last
    injected frame on the vehicle wire + 1 s (the injector stop + 1 s)."""
    tag = "bridge_%s" % com3
    vrows = read_rows(os.path.join(d, "vehicle_%s.txt" % tag))
    crows = read_rows(os.path.join(d, "charger_%s.txt" % tag))
    stamped = []
    sp = os.path.join(d, "serial_%s.stamped" % tag)
    if os.path.exists(sp):
        for raw in open(sp, encoding="utf-8", errors="replace"):
            h, _, ln = raw.rstrip("\n").partition("\t")
            stamped.append((float(h), ln))
    mp = os.path.join(d, "meta_%s.json" % tag)
    if os.path.exists(mp):
        m = json.load(open(mp))
        offered = list(range(m["offered"]))
        stops = [tuple(x) for x in m["stops"]]
        reads = [tuple(x) for x in m["reads"]]
        gap_end = m["gap_end"]
    else:
        offered = list(range(offered_n))
        hs = [h for h, ln in stamped if ln == "# READS STOPPED"]
        he = [h for h, ln in stamped if ln == "# READS RESUMED"]
        stops = [(k + 1, a, b) for k, (a, b) in enumerate(zip(hs, he))]
        edges = ([stamped[0][0]] if stamped else []) + he
        ends = hs + [h for h, ln in stamped if ln == "# PORT CLOSE"]
        reads = list(zip(edges, ends))
        gap_end = max(r[1] for r in vrows if r[2] == INJECT_ID) + 1.0
    return score_bridge_run(com3, vrows, crows, offered, stamped, stops,
                            reads, gap_end,
                            os.path.join(d, "bridge_baseline.json"))


# --- self-test: no hardware -------------------------------------------------

def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    ts = list(range(5, 300, 5))
    check(own_period(ts) == 5, "period: the board's own 5 s steps")
    check(holes(ts, 5) == [], "holes: an unbroken sequence has none")
    gap = [t for t in ts if not (100 < t < 200)]
    check(holes(gap, 5) == [(100, 200)], "holes: t=100 -> 200 is one hole")
    check(holes([5, 10, 15, 5, 10], 5) == [],
          "holes: a reboot (t goes back) is not a hole")
    met, fill, why = stop_premise(gap, 5, 40, 280)
    check(met and fill == 60, "premise: a hole inside the stop meets it; "
                              "fill = 100 - 40 = 60 s")
    met, fill, why = stop_premise(ts, 5, 40, 280)
    check(not met and why.startswith("VOID"),
          "CAN-FAIL premise: a stop with NO hole is VOID, not a pass")
    met, _, why = stop_premise(gap, 5, 220, 280)
    check(not met, "premise: a hole outside the stop does not count for it")
    check(not stop_premise([5], None, 0, 100)[0],
          "premise: unknown period is VOID")
    pre = [t for t in ts if t <= 100]
    met, fill, _ = stop_premise(pre, 5, 40, 280, ends_in_reset=True)
    check(met and fill == 60, "premise: an unread stop ending in a reset, "
                              "last line t=100 of 280 -> met, fill 60 s")
    pre = [t for t in ts if t <= 280]
    met, _, why = stop_premise(pre, 5, 40, 282, ends_in_reset=True)
    check(not met, "CAN-FAIL premise: every line up to the reset arrived -> "
                   "VOID")
    early = [t for t in ts if t != 85 and t <= 140]
    met, fill, why = stop_premise(early, 5, 33, 513, ends_in_reset=True)
    check(met and fill == 107 and "hole t=140..513" in why and
          "also t=80..90" in why,
          "CAN-FAIL premise: a lone dropped line (t=85) before the real "
          "fill (t=140) does not set the fill time; both are listed (%s)"
          % why)
    met, _, _ = stop_premise([t for t in ts if t <= 100], 5, 40, 280)
    check(not met, "premise: without ends_in_reset a missing tail is not "
                   "assumed to be a hole")

    hosts = [1000.0 + 0.005 * i for i in range(200)]
    check(choose_clock([0.0] * 200, hosts)[0] == "host",
          "CAN-FAIL clock: gs_usb's constant 0 stamp is refused as a device "
          "clock")
    check(choose_clock([5.0 + 0.0001 * i for i in range(200)], hosts)[0]
          == "host", "clock: a stamp moving 1/50 as fast is refused")
    check(choose_clock([77.0 + 0.005 * i for i in range(200)], hosts)[0]
          == "device", "clock: a stamp moving with the host is used")

    # the 32-bit wrap (bench 06:06): 1,200 s of stamps crossing 4294.97
    hw = [3800.0 + 0.005 * i for i in range(240000)]
    wrapped = [t % WRAP_S for t in hw]
    hh = [5000.0 + 0.005 * i for i in range(240000)]
    check(choose_clock(wrapped, hh)[0] == "host",
          "CAN-FAIL clock: a wrapped 32-bit stamp, NOT unwrapped, is refused "
          "(the 06:06 VOID)")
    uw = unwrap_us32(wrapped)
    check(choose_clock(uw, hh)[0] == "device" and
          max(abs(a - b) for a, b in zip(uw, hw)) < 1e-6,
          "clock: unwrapped, the same stamps are the device clock again and "
          "equal the unwrapped truth")
    check(unwrap_us32([10.0, 10.005, 10.003, 10.01]) ==
          [10.0, 10.005, 10.003, 10.01],
          "unwrap: a small backward step (frames out of order) is not a wrap")
    check(unwrap_us32([4294.9, 0.1, 4294.95]) == [4294.9, 0.1 + WRAP_S,
                                                   4294.95],
          "unwrap: a late frame from before the wrap is not a second wrap "
          "and not shifted")

    n = 2000
    veh = {s: 10.0 + 0.005 * s + (0.0004 if s % 7 == 0 else 0.0)
           for s in range(n)}
    chg = {s: t + 0.0003 for s, t in veh.items()}
    offered = list(range(n))
    v, ln = score_bridge(offered, veh, chg, "device", None)
    check(v is True and "largest excess" in ln[0],
          "bridge: injection jitter alone is cancelled by the difference "
          "(%s)" % ln[0])
    bmax = max(e for _, e in gap_excess(veh, chg))
    check(abs(bmax) < 1e-9, "bridge: excess ~0 when the bridge adds nothing")
    stall = dict(chg)
    for s in range(1000, 1020):
        stall[s] = chg[1000] + 0.100 + 0.0001 * (s - 1000)
    v, ln = score_bridge(offered, veh, stall, "device", bmax)
    check(v is False and any("past the limit" in x for x in ln),
          "CAN-FAIL bridge: a ~100 ms stall (the old failure) FAILS")
    one = dict(chg)
    for s in range(1000, n):
        one[s] = chg[s] + 0.001
    v, ln = score_bridge(offered, veh, one, "device", bmax)
    check(v is True, "bridge: a single 1 ms delay passes the 2 ms device "
                     "margin (the bound the user chose)")
    three = dict(chg)
    for s in range(1000, n):
        three[s] = chg[s] + 0.003
    v, _ = score_bridge(offered, veh, three, "device", bmax)
    check(v is False, "CAN-FAIL bridge: a 3 ms delay FAILS on the device "
                      "clock")
    v, _ = score_bridge(offered, veh, three, "host", bmax)
    check(v is True, "bridge: the same 3 ms is inside the 5 ms host margin "
                     "-- the host clock cannot judge 1 ms")
    lost = dict(chg)
    del lost[1500]
    v, ln = score_bridge(offered, veh, lost, "device", bmax)
    check(v is False and any("not bridged" in x for x in ln),
          "CAN-FAIL bridge: one frame not bridged FAILS")
    vl = dict(veh)
    del vl[3]
    v, ln = score_bridge(offered, vl, chg, "device", bmax)
    check(v is None and ln[0].startswith("VOID"),
          "bridge: a frame missing on the vehicle wire is VOID (injection), "
          "not a board verdict")
    v, ln = score_bridge(offered, veh, stall, "device", bmax,
                         {s: 500.0 + 0.005 * s for s in chg},
                         [(1, 503.0, 510.0)])
    check(any("stop 1 +2." in x for x in ln),
          "bridge: the largest gap is placed against its stop")

    base = [12154, 12218, 12200]
    ok, ln = score_boot(12218 + 4999, base, "x")
    check(ok, "boot: baseline max + 4,999 us PASSES (%s)" % ln)
    ok, ln = score_boot(12218 + 6000, base, "x")
    check(not ok, "CAN-FAIL boot: baseline max + 6 ms FAILS")
    ok, ln = score_boot(112383, base, "x")
    check(not ok, "CAN-FAIL boot: the E9 close-boot's 112,383 us FAILS")
    ok, ln = score_boot(None, base, "x")
    check(not ok and "no boot=" in ln,
          "CAN-FAIL boot: no MODE line after resuming FAILS, not skipped")
    ok, ln = score_boot(12200, [], "x")
    check(not ok, "boot: no same-session baseline FAILS")
    M = ("  MODE veh=listen-only core wants listen-only | switches=1 "
         "refused=0 suppressed=0 | round=4/4 in 849us | boot=12200us")
    check(first_mode_boot(["vehicle port -> LISTEN-ONLY", "spec 2.3: ... "
                           "boot=99us", M]) == 12200,
          "parse: boot= only from a MODE line")
    # the 05:36 control boot's shape: live t=30,35; stop; host buffer holds
    # t=40..160; reset at board t~276; no ROM banner; t=10,15,20 after
    def rec(post_mode=True, upto=160, banner=False):
        st = ["t=30s state=P", M.replace("12200", "61426"),
              "t=35s state=P"]
        st += ["t=%ds state=P" % t for t in range(40, upto + 1, 5)]
        if banner:
            st.append("ESP-ROM:esp32s3-20210327")
        st += ["t=10s state=P"]
        if post_mode:
            st.append(M.replace("12200", "12195"))
        st += ["t=15s state=P", "t=20s state=P"]
        return st
    base = [12162, 12218, 12168]
    ok, ln = score_noread(rec(), 543877.9, 543914.08, 544154.1, base, "x")
    check(ok is True and "hole t=160..276" in ln[0] and "fill 124 s" in ln[0],
          "noread: the 05:36 record (no banner) -> hole t=160..276, fill "
          "~123 s, boot 12195 PASS (%s)" % ln[0])
    pre, post = split_at_reboot(rec())
    check(post[0] == "t=10s state=P" and pre[-1] == "t=160s state=P",
          "CAN-FAIL split: with no ROM banner the record splits at the "
          "first t= that goes back, not at the end (the 05:36 defect)")
    ok, ln = score_noread(rec(banner=True), 543877.9, 543914.08, 544154.1,
                          base, "x")
    check(ok is True and "ROM banner ARRIVED" in ln[0],
          "noread: a ROM banner splits the record the same way")
    ok, ln = score_noread(rec(upto=275), 543877.9, 543914.08, 544154.1,
                          base, "x")
    check(ok is None and ln[1].strip().startswith("VOID"),
          "CAN-FAIL noread: every line up to the reset arrived -> VOID")
    ok, ln = score_noread(rec(post_mode=False), 543877.9, 543914.08,
                          544154.1, base, "x")
    check(ok is False, "CAN-FAIL noread: no MODE line after the reboot "
                       "FAILS")
    st = ["t=30s state=P", "t=35s state=P"] + ["t=%ds state=P" % t
                                               for t in range(40, 161, 5)]
    ok, ln = score_noread(st, 543877.9, 543914.08, 544154.1, base, "x")
    check(ok is None and "no reboot" in ln[1],
          "CAN-FAIL noread: no reboot in the record is VOID")
    # capacity figures (reviewer 2026-10-22): the 05:36 shape, one
    # "t=..s state=P" line per 5 s, 15 B with CRLF below t=100 and 16 B
    # from it; stop t=36.18..276.2 (240.0 s), hole at t=160 (fill 124 s).
    # Intervals (40,45]..(155,160]: 11 x 15 + 13 x 16 = 373 B in 120 s
    # -> 3.108 B/s; held 3.108 x 124 = 385 B; over the stop 746 B
    ok, ln = score_noread(rec(), 543877.9, 543914.08, 544154.1, base, "x")
    fig = [x for x in ln if "FIGURE capacity" in x]
    check(fig and "3.1 B/s" in fig[0] and "120 s of whole" in fig[0] and
          "~746 B over the 240 s stop" in fig[0] and
          "hole began 124 s into the stop -> the host side held ~385 B"
          in fig[0],
          "capacity: rate from the whole intervals inside the stop, bytes "
          "over the stop, held = rate x fill (%s)" % fig)
    # no hole: (40,45]..(270,275], 11 x 15 + 36 x 16 = 741 B in 235 s
    # -> 3.153 B/s, 757 B over the stop
    ok, ln = score_noread(rec(upto=275), 543877.9, 543914.08, 544154.1,
                          base, "x")
    fig = [x for x in ln if "FIGURE capacity" in x]
    check(ok is None and fig and "no hole -> the host side held all of "
          "it, > 757 B" in fig[0] and "235 s of whole" in fig[0],
          "capacity: a stop with no hole reports a lower bound, still with "
          "its rate (%s)" % fig)
    dense = (["t=30s state=P"] + ["x" * 98] * 4 + ["t=35s state=P"] +
             ["t=%ds state=P" % t for t in range(40, 161, 5)])
    f = capacity_figure(dense, 36.18, 276.2, 124.0, "x")
    check("3.1 B/s" in f and "120 s of whole" in f,
          "CAN-FAIL capacity: 400 B printed in (30,35], before the stop, "
          "are not in the rate (%s)" % f)
    # a lone dropped line (t=80) and lines after the hole (t=200..230):
    # whole intervals (40,45]..(70,75], (85,90]..(135,140], (200,205]..
    # (225,230] = 120 s; 7 x 15 + 2 x 15 + 9 x 16 + 6 x 16 = 375 B
    gaps = (["t=30s state=P", "t=35s state=P"] +
            ["t=%ds state=P" % t for t in range(40, 141, 5) if t != 80] +
            ["t=%ds state=P" % t for t in range(200, 231, 5)])
    f = capacity_figure(gaps, 36.18, 276.2, 104.0, "x")
    check("3.1 B/s" in f and "120 s of whole" in f,
          "CAN-FAIL capacity: a lone dropped line and the hole are not "
          "intervals of the rate; lines after the hole are (%s)" % f)
    check("no rate" in capacity_figure(dense, 300.0, 400.0, None, "x"),
          "capacity: no interval inside the stop -> no rate, not 0 B/s")
    # the WiCAN's USB state: never a serial number (none is taken), never
    # the X2's own port, never a non-Espressif port
    pl = [("COM3", 0x303A, 0x1001, "1-2.1"), ("COM4", 0x303A, 0x1001, "1-2"),
          ("COM9", 0x0403, 0x6001, "1-5")]
    w = wican_usb_figure(pl, "COM3")
    check("present" in w and "COM4 (location 1-2)" in w and
          "COM3 (" not in w and "COM9" not in w,
          "wican usb: the other Espressif USB-JTAG port, by name and "
          "location (%s)" % w)
    w = wican_usb_figure(pl[:1] + pl[2:], "com3")
    check("absent" in w and "COM9" not in w,
          "CAN-FAIL wican usb: only the X2 (any case) and a non-Espressif "
          "port -> absent (%s)" % w)
    check("unknown" in wican_usb_figure(None, "COM3"),
          "wican usb: no port list -> unknown, not absent")

    blk = []
    for t in range(5, 305, 5):
        blk += ["t=%ds state=P" % t] + ["diag st=P"] * 5
    r, s = diag_rate_board(blk)
    check(abs(r - 60.0) < 1e-9 and s == 295,
          "diag (board clock): 5 per 5 s block reads 60/min however the "
          "lines were delivered (the 1783.8/min burst defect)")
    slow = []
    for t in range(5, 305, 5):
        slow += ["t=%ds state=P" % t] + (["diag st=P"] if t % 25 == 0
                                          else [])
    r, _ = diag_rate_board(slow)
    check(r < 15, "CAN-FAIL diag (board clock): one per 25 s reads ~2/min, "
                  "not 1 Hz")
    holed = [x for x in blk if x not in ("t=100s state=P",)]
    r, s = diag_rate_board(holed)
    check(s == 285 and abs(r - 60.0) < 1e-9,
          "diag (board clock): an interval broken by a hole is left out of "
          "the seconds, never stretched")

    st = [(10.0 + i, "diag st=PASSTHROUGH") for i in range(60)]
    st += [(100.0 + i, "diag st=PASSTHROUGH") for i in range(0, 60, 5)]
    r, s = diag_per_minute(st, [(10.0, 69.5)])
    check(abs(r - 60.0 / 59.5 * 60) < 0.01, "diag: 60 in a minute ~60/min")
    r, s = diag_per_minute(st, [(100.0, 160.0)])
    check(r < 15, "diag: one per 5 s reads as ~12/min, not 1 Hz")

    # 2026-10-21 (a25e): exit codes and baseline folders
    check(verdict_rc([True, True, True]) == 0, "rc: every item PASS -> 0")
    check(verdict_rc([True, None, True]) == 0, "rc: some VOID, none FAIL -> 0")
    check(verdict_rc([None, None, None]) == 3,
          "rc: every item VOID -> 3, never 0 (a25e noread-boots)")
    check(verdict_rc([]) == 3, "rc: no item at all -> 3")
    check(verdict_rc([None, False, True]) == 1, "rc: any FAIL -> 1")
    check(verdict_rc(x for x in (True, False)) == 1, "rc: a generator works")
    check(baseline_boots_rc([{"ok_2p3": False}] * 3) == 1,
          "baseline-boots: three failing 2.3 verdicts FAIL the case (a25e "
          "usb-host-baseline-noack)")
    check(baseline_boots_rc([{"ok_2p3": True}] * 3) == 0,
          "baseline-boots: three passing 2.3 verdicts PASS")
    import types as _ty
    tdir = scratch_dir.make("usbhost-selftest")
    try:
        bdir, odir = os.path.join(tdir, "baseline"), os.path.join(tdir, "case")
        os.makedirs(bdir)
        os.makedirs(odir)
        json.dump({"baseline_us": [12163, 12238]},
                  open(os.path.join(bdir, "boot_baseline_ack.json"), "w"))
        a_with = _ty.SimpleNamespace(baseline_dir=bdir, com3="cycles")
        a_without = _ty.SimpleNamespace(baseline_dir=None, com3="cycles")
        check(_baseline(baseline_dir(a_with, odir), "ack") == [12163, 12238],
              "baseline: read from --baseline-dir (a25e close-ack)")
        check(_baseline(baseline_dir(a_without, odir), "ack") == [],
              "baseline: without --baseline-dir, --out (no file there)")
        check(bridge_baseline_path(a_with, odir) ==
              os.path.join(bdir, "bridge_baseline.json"),
              "bridge: cycles reads the absent run's folder (a25e cycles)")
        check(bridge_baseline_path(_ty.SimpleNamespace(baseline_dir=bdir,
                                                       com3="absent"), odir)
              == os.path.join(odir, "bridge_baseline.json"),
              "bridge: absent writes into its own folder")
        check(bridge_baseline_path(a_without, odir) ==
              os.path.join(odir, "bridge_baseline.json"),
              "bridge: cycles without --baseline-dir reads --out")
    finally:
        scratch_dir.remove(tdir)

    print("\nbench_usb_host self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("all-VOID exits 0",
     "    if not oks or all(o is None for o in oks):\n        return 3",
     "    if not oks or all(o is None for o in oks):\n        return 0"),
    ("FAIL not seen",
     "    if any(o is False for o in oks):\n        return 1",
     "    if False:\n        return 1"),
    ("baseline-boots ignores its 2.3 verdicts",
     "    return verdict_rc(v[\"ok_2p3\"] for v in vals)",
     "    return 0"),
    ("--baseline-dir ignored",
     "    return getattr(a, \"baseline_dir\", None) or out",
     "    return out"),
    ("cycles reads its own folder",
     "    d = out if a.com3 == \"absent\" else baseline_dir(a, out)",
     "    d = out"),
    ("absent writes to the baseline dir",
     "    d = out if a.com3 == \"absent\" else baseline_dir(a, out)",
     "    d = baseline_dir(a, out)"),
    ("no capacity figure on a noread boot",
     "    if off is not None:\n        out.append(",
     "    if False:\n        out.append("),
    ("held over the whole stop, not to the hole",
     "% (fill, rate * fill) if fill is not None else",
     "% (fill, stop_b) if fill is not None else"),
    ("intervals before the stop in the rate",
     "if prev is not None and prev >= stop_lo and t <= stop_hi and",
     "if prev is not None and t <= stop_hi and"),
    ("intervals with a missing line in the rate",
     "    whole = [(s, b) for s, b in steps if s <= HOLE_FACTOR * per]",
     "    whole = steps"),
    ("line ends not counted",
     "        acc += len(ln) + LINE_END_B\n", "        acc += len(ln)\n"),
    ("the X2's own port reported as the WiCAN",
     "              p[0].upper() != (x2_port or \"\").upper()]",
     "              True]"),
    ("any USB serial port reported as the WiCAN",
     "    others = [p for p in ports if (p[1], p[2]) == ESP_USB_JTAG and",
     "    others = [p for p in ports if True and"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--case", choices=("baseline-boots", "noread-boots",
                                       "close-boots", "bridge"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--ack", action="store_true", help="PCAN normal")
    g.add_argument("--noack", action="store_true", help="PCAN listen-only")
    ap.add_argument("--boots", type=int, default=3)
    ap.add_argument("--com3", choices=("absent", "cycles"))
    ap.add_argument("--stop-s", type=float, default=STOP_FLOOR_S)
    ap.add_argument("--absent-s", type=float, default=120.0)
    ap.add_argument("--cycles", type=int, default=CYCLES)
    ap.add_argument("--rx-size", type=int, default=64,
                    help="RX queue size asked of the driver (0: leave it)")
    ap.add_argument("--wican-state", default="mode 0, 12 V only, untouched")
    ap.add_argument("--out")
    ap.add_argument("--baseline-dir",
                    help="the folder the baseline run wrote "
                         "(boot_baseline_*.json, bridge_baseline.json); "
                         "default --out")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    ap.add_argument("--rescore-noread", metavar="STAMPED",
                    help="re-score a saved not-reading boot; baselines from "
                         "--out (boot_baseline_<ack|noack>.json)")
    ap.add_argument("--rescore-bridge", metavar="DIR",
                    help="re-score a saved bridging run (with --com3)")
    ap.add_argument("--offered", type=int,
                    help="frames offered, for a run without its meta file")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if a.rescore_bridge:
        v, lines = rescore_bridge_dir(a.rescore_bridge, a.com3, a.offered)
        for ln in lines:
            print(ln)
        print("verdict: %s" % {True: "PASS", False: "FAIL", None: "VOID"}[v])
        return 0 if v else 1
    if a.rescore_noread:
        mode = "ack" if a.ack else "noack"
        ok, lines = rescore_noread_file(a.rescore_noread,
                                        _baseline(baseline_dir(a, a.out), mode),
                                        os.path.basename(a.rescore_noread))
        for ln in lines:
            print(ln)
        return 0 if ok else 1
    if not (a.port and a.case and a.out):
        ap.error("--port, --case and --out are required for a bench run")
    if a.case != "bridge" and not (a.ack or a.noack):
        ap.error("--ack or --noack")
    if a.case == "bridge" and not a.com3:
        ap.error("--com3 absent|cycles")
    os.makedirs(a.out, exist_ok=True)
    fn = {"baseline-boots": case_baseline_boots,
          "noread-boots": case_noread_boots,
          "close-boots": case_close_boots, "bridge": case_bridge}[a.case]
    print("%s %s start %s" % (a.case, a.com3 or ("ack" if a.ack else
                                                  "noack"),
                              datetime.datetime.now().isoformat()))
    print(wican_usb_figure(usb_ports(), a.port))
    rc, lines = fn(a, a.out)
    for ln in lines:
        print(ln)
    print("end %s rc=%d" % (datetime.datetime.now().isoformat(), rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
