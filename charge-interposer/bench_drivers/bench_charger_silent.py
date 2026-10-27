"""Spec 2.2 on the bench, the single-adapter variant (step 4 prep).

The clean form wants a non-acknowledging witness on the charger segment
while nothing there ACKs, then a returning charger; the existing bench has
one charger-side adapter, so the reviewer ruled the single-adapter variant
(2026-10-13): the Innomaker is closed (nothing on the charger segment
acknowledges), then reopened as the returning charger. Three phases:

  A  baseline: the Innomaker open and ACKing, sending the charger's
     0x18FFD4C0 status page every 100 ms; the Kvaser sends the VCU's page
     00 burst with the flow bit set (the 2.2 counter counts only while
     flow is 1), then a tagged vehicle -> charger stream throughout;
  B  silent: the Innomaker CLOSED. The evidence that it really is not
     acknowledging is the board's own 0x7F6 intp_tx_fail_expected ("charger
     not acknowledging while expected") RISING -- if it does not rise, the
     phase proves nothing and the run FAILS;
  C  return: the Innomaker reopened, status pages again. Every tagged frame
     it receives must be CURRENT: younger than the 100 ms age-out, judged by
     its content (the sequence number carries its send time), so no burst of
     queued frames; and 0x7F5 intp_chg_silent_expected must clear.

All diag fields are decoded with cantools from the diag DBC. The board's TEC
is not exposed in any diag field (schema 4), so it is recorded as not
available.

CLOCKS. Ages are host clock: the send time is when the Kvaser accepted the
frame, the receive time when the Innomaker delivered it, both
time.perf_counter() in this process. Both USB paths' latency is inside every
age, which can only make a frame look OLDER, never younger: a pass is
conservative, and a fail can be FALSE. So phase A's tagged frames measure
the path's own latency, and any phase C frame that fails by less than it is
noted next to the verdict (reviewer, 2026-10-13). Phase B JUDGES the silent
flag: every 0x7F5 from 500 ms + one status period after the close until the
reopen must read 1 (spec 2.2).

SINGLE-ADAPTER, NOT CLEAN: no witness sees the charger wire while the
Innomaker is closed. MCP2515 path: YES.

    py -3.14 bench_charger_silent.py --self-test      # no hardware
    py -3.14 bench_charger_silent.py --port COM3 --seconds 20,10,10

Tester, 2026-10-13. Revised 2026-10-14 for spec 2.3 (see DIAG_RE): phases
judged from the serial diag line, the CAN frames cross-checked where they
exist; and phase B puts the Innomaker in LISTEN-ONLY on the same handle
(gs_usb stop + start) instead of closing it -- so it still witnesses the
charger wire while acknowledging nothing, and phase C restarts it in normal
mode without a USB reopen (an in-process gs_usb reopen fails on this host
with libusb "Access denied", bench_innomaker_clock 2026-10-14). The
judgment of phase B does not rest on the witness: the board's own count
rising is still the evidence nothing was acknowledging.
"""

import argparse
import datetime
import os
import re
import sys
import threading
import time

# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (paths, protocol, run_scenario): both found from this file, never from
# the working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

AGE_OUT_S = 0.100
SILENT_AFTER_S = 0.500        # spec 2.2: no status page for 500 ms
STATUS_PERIOD_S = 1.0         # 0x7F5's own cadence (8.2)
TAG_ID = 0x18FF5201
TAG_MAGIC = 0xA5
TAG_FPS = 50.0


def tag_payload(seq):
    return bytearray([TAG_MAGIC, (seq >> 16) & 0xFF, (seq >> 8) & 0xFF,
                      seq & 0xFF, 0, 0, 0, 0])


def tag_seq(data):
    if len(data) < 4 or data[0] != TAG_MAGIC:
        return None
    return (data[1] << 16) | (data[2] << 8) | data[3]


# Spec 2.3 (2026-10-13), after this tool was written: with the Innomaker off
# the bus, no charger frame arrives, and 2 s later the vehicle port is listen-
# only and the board sends NO diag frames on the vehicle segment -- so 0x7F5 /
# 0x7F6 cannot carry phase B. Spec 8.3's serial diag line carries the same
# content at the same 1 Hz in either mode ("txf" = 0x7F6's charger-not-
# acknowledging-while-expected count, "silent" = 0x7F5's silent flag), so the
# phases are judged from it, and the CAN frames, where they exist (A, C),
# are cross-checked against it. Only whole, well-formed diag lines count: a
# line that lost output mid-line (8.3, 2026-10-14) is skipped, not misread.
DIAG_RE = re.compile(r"^diag st=(\w+) trip=(\d+) flags=0x([0-9a-fA-F]+) "
                     r".* txf=(\d+) silent=(\d+) fw=(\d+) schema=(\d+)$")


def parse_diag(line):
    """-> dict(state, txf, silent) for a whole diag line, else None."""
    m = DIAG_RE.match(line.strip())
    if not m:
        return None
    return dict(state=m.group(1), txf=int(m.group(4)),
                silent=int(m.group(5)))


def crosscheck(can_vals, ser_vals, window_s=0.6):
    """Each CAN value against the serial values within window_s of it: a
    disagreement is a CAN value that NONE of those serial values equals,
    when there is at least one (counts move, so any one in the window may
    be the same moment). -> (compared, [(t, can_v, serial_vs)])."""
    bad, n = [], 0
    for t, v in can_vals:
        near = [sv for st, sv in ser_vals if abs(st - t) <= window_s]
        if not near:
            continue
        n += 1
        if v not in near:
            bad.append((t, v, near))
    return n, bad


def held_seq_of(witnessed_b):
    """The frame the board was retrying when the charger came back: the
    last one the listen-only witness saw in phase B (device clock)."""
    if not witnessed_b:
        return None
    return max(witnessed_b, key=lambda r: r[2])[1]


def diag_value(db, frame_id, data, signal):
    """One diag field as an int. decode_choices=False: 0x7F5's silent flag
    has a value table, and int() of cantools' NamedSignalValue raised in
    the reader thread, which died on the first 0x7F5 (2026-10-14)."""
    return int(db.decode_message(frame_id, bytes(data),
                                 decode_choices=False)[signal])


def judge(sent, received, reopen_t, counters, flags, phase_b, phase_c,
          received_a=(), burst_dev=None, held_seq=None):
    """-> (ok, lines). sent: {seq: host send t}; received: [(host t, seq)]
    on the Innomaker after the reopen; counters: [(host t, tx_fail)];
    flags: [(host t, silent)]; phase_b / phase_c: (t0, t1); received_a:
    [(host t, seq)] on the Innomaker in phase A, for the path's latency."""
    lines, ok = [], True
    b0, b1 = phase_b
    c0, c1 = phase_c
    before = [v for t, v in counters if t < b0]
    during = [v for t, v in counters if b0 <= t <= b1 + 1.5]
    if not before or not during:
        ok = False
        lines.append("FAIL: no 0x7F6 before / during the silent phase")
    elif before[-1] >= 255:
        ok = False
        lines.append("FAIL: 0x7F6 intp_tx_fail_expected was already "
                     "saturated at 255 before the silent phase (it counts "
                     "since boot): it cannot show a rise -- reset the board "
                     "and run again")
    elif max(during) <= before[-1]:
        ok = False
        lines.append("FAIL: 0x7F6 intp_tx_fail_expected did not rise while "
                     "the Innomaker was closed (%d before, max %d during): "
                     "no evidence nothing was acknowledging" % (
                         before[-1], max(during)))
    else:
        lines.append("0x7F6 intp_tx_fail_expected %d before -> %d during "
                     "the silent phase%s" % (before[-1], max(during),
                                             " (SATURATED at 255)" if
                                             max(during) == 255 else ""))
    # spec 2.2 (reviewer, 2026-10-13): set while flow is 1 and no status
    # page for 500 ms -- so from the first 0x7F5 sent more than 500 ms + one
    # status period after the close, every 0x7F5 until the reopen reads 1
    sil_b = [(t, v) for t, v in flags
             if b0 + SILENT_AFTER_S + STATUS_PERIOD_S <= t <= b1]
    # set THROUGH the phase, not on one frame: at the 1 Hz cadence the
    # window holds about one per second (2026-10-14: on a 2.3 port the CAN
    # frames stop 2 s in, and one frame would otherwise have passed)
    span = b1 - (b0 + SILENT_AFTER_S + STATUS_PERIOD_S)
    need = max(1, int(span / STATUS_PERIOD_S) - 1)
    if not sil_b:
        ok = False
        lines.append("FAIL: no 0x7F5 in the silent phase after %.1f s"
                     % (SILENT_AFTER_S + STATUS_PERIOD_S))
    elif len(sil_b) < need:
        ok = False
        lines.append("FAIL: only %d 0x7F5 in the %.1f s of the silent phase "
                     "judged (at least %d at 1 Hz): the flag is not shown "
                     "set through the phase" % (len(sil_b), span, need))
    elif any(v != 1 for _t, v in sil_b):
        ok = False
        bad = next(t for t, v in sil_b if v != 1)
        lines.append("FAIL: 0x7F5 intp_chg_silent_expected read 0 at %.1f s "
                     "into the silent phase (%d of %d frames from %.1f s set) "
                     "-- spec 2.2: set while flow is 1 and no status page "
                     "for 500 ms" % (bad - b0, sum(v for _t, v in sil_b),
                                     len(sil_b),
                                     SILENT_AFTER_S + STATUS_PERIOD_S))
    else:
        lines.append("0x7F5 intp_chg_silent_expected set on all %d frames "
                     "from %.1f s after the close until the reopen"
                     % (len(sil_b), SILENT_AFTER_S + STATUS_PERIOD_S))
    # the path's own latency, from phase A's tagged frames (both USB paths
    # and the board): what a host-clock age carries even when current
    base = sorted(t - sent[s] for t, s in received_a if s in sent)
    lat = base[-1] if base else None
    if base:
        lines.append("phase A latency, Kvaser accept -> Innomaker delivery: "
                     "p50 %.1f ms, max %.1f ms (%d frames)"
                     % (base[len(base) // 2] * 1000, lat * 1000, len(base)))
    ages = []
    for t, seq in received:
        if seq in sent:
            ages.append((t - sent[seq], seq, t))
    # Every frame after the return judged by its AGE since queued, against
    # 100 ms (reviewer, 2026-10-14: 2.2 defines "current" by age, not by
    # count -- the count cap this replaced judged the 50/s stimulus). The
    # frame the board was HOLDING in its transmit buffer at the return
    # (held_seq: the last frame the listen-only witness saw retried) is
    # judged like the rest: the user ruled 2.2's 100 ms counts from when
    # the frame was queued (spec 6bcefad7212c2746, 2026-10-14), so its
    # total age decides. It is still named, so a FAIL says which it was.
    stale = [a for a in ages if a[0] > AGE_OUT_S]
    for a in ages:
        if held_seq is not None and a[1] == held_seq:
            lines.append("the frame held in the board's transmit buffer at "
                         "the return, seq %d, was delivered %.1f ms after it "
                         "was queued (judged on that total age)"
                         % (a[1], a[0] * 1000))
    if not ages:
        ok = False
        lines.append("FAIL: no tagged frame reached the reopened Innomaker")
    elif stale:
        ok = False
        worst = max(stale)
        lines.append("FAIL: %d of %d tagged frame(s) after the reopen were "
                     "older than the %.0f ms age-out since queued, oldest "
                     "%.1f ms (seq %d%s, sent %.3f s before the reopen) -- "
                     "stale frames delivered"
                     % (len(stale), len(ages), AGE_OUT_S * 1000,
                        worst[0] * 1000, worst[1],
                        ", the held frame" if worst[1] == held_seq else "",
                        reopen_t - sent[worst[1]]))
        near = [a for a in stale if lat is not None
                and a[0] - AGE_OUT_S < lat]
        if near:
            lines.append("  NOTE: %d of those fail by LESS than phase A's "
                         "measured latency (%.1f ms): host-clock ages can "
                         "false-fail too -- read those with that in mind"
                         % (len(near), lat * 1000))
    else:
        lines.append("%d tagged frame(s) after the reopen, every one current "
                     "since queued: age p50 %.1f ms, max %.1f ms (host "
                     "clock, both USB paths inside)" % (
                         len(ages), sorted(a[0] for a in ages)[len(ages) // 2]
                         * 1000, max(a[0] for a in ages) * 1000))
    # how many arrived together: REPORTED, not judged (see above)
    times = (list(burst_dev) if burst_dev is not None
             else [t for t, _s in received])
    first = min(times, default=None)
    if first is not None:
        n100 = sum(1 for t in times if t - first <= 0.1)
        lines.append("%d tagged frame(s) in the first 100 ms after the "
                     "return (%s clock; reported, not judged)"
                     % (n100, "device" if burst_dev is not None else "host"))
    sil_c = [v for t, v in flags if c0 + 1.0 <= t <= c1]
    if not sil_c or sil_c[-1] != 0:
        ok = False
        lines.append("FAIL: 0x7F5 intp_chg_silent_expected did not clear "
                     "after the reopen (%s)" % (sil_c[-3:] or "no frame"))
    else:
        t_clear = next((t for t, v in flags if t >= c0 and v == 0), None)
        lines.append("0x7F5 intp_chg_silent_expected cleared %.1f s after "
                     "the reopen" % ((t_clear or c0) - c0))
    lines.append("X2 TEC: not exposed in any diag field (schema 4)")
    return ok, lines


# --- hardware ---------------------------------------------------------------

def run_bench(args):
    import can
    import cantools
    import protocol as P
    import paths
    import bench_charger_witness as bcw
    db = cantools.database.load_file(paths.diag_dbc())
    sa, sb, sc = (float(x) for x in args.seconds.split(","))
    print("run at %s; SINGLE-ADAPTER, NOT CLEAN; MCP2515 path: YES"
          % datetime.datetime.now().isoformat())
    from gs_usb.constants import (GS_CAN_MODE_LISTEN_ONLY,
                                  GS_CAN_MODE_HW_TIMESTAMP)
    import serial
    veh = can.Bus(interface="kvaser", channel=args.kvaser_channel,
                  bitrate=500000)
    # An acknowledger on the vehicle segment throughout, as on the truck
    # (the VCU and the rest never leave): the PCAN in NORMAL mode, drained
    # and otherwise unused. Without it, once the port goes listen-only 2 s
    # into phase B nothing on CAN1 ACKs the Kvaser, the vehicle side stops,
    # and phase B exercises nothing after its first 2 s (2026-10-14 run 3).
    acker = None
    if args.vehicle_acker == "pcan":
        acker = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                        bitrate=500000)
    sent, received, counters, flags = {}, [], [], []
    received_a, witnessed_b, errors, tag_errors = [], [], [], []
    ser_lines, ser_counters, ser_flags, damaged = [], [], [], [0]
    stop = threading.Event()
    state = {"chg": None, "reopen": None, "mode": "normal"}
    lock = threading.Lock()
    # lines held low: closing the port then does not reset the X2 (8.3 bench
    # facts, 2026-10-14)
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = args.port, 115200, 0.2
    ser.dtr, ser.rts = False, False
    ser.open()

    def serial_rx():
        buf = b""
        while not stop.is_set():
            try:
                buf += ser.read(512)
            except Exception:                              # noqa: BLE001
                return
            while b"\n" in buf:
                ln, _, buf = buf.partition(b"\n")
                h = time.perf_counter()
                text = ln.decode("ascii", "replace").rstrip()
                ser_lines.append((h, text))
                if "diag st=" in text:
                    d = parse_diag(text)
                    if d is None:
                        damaged[0] += 1
                        continue
                    ser_counters.append((h, d["txf"]))
                    ser_flags.append((h, d["silent"]))

    def vehicle_rx():
        while not stop.is_set():
            m = veh.recv(timeout=0.1)
            if m is None or not m.is_rx:
                continue
            h = time.perf_counter()
            try:
                if m.arbitration_id == 0x7F6:
                    counters.append((h, diag_value(db, 0x7F6, m.data,
                                                   "intp_tx_fail_expected")))
                elif m.arbitration_id == 0x7F5:
                    flags.append((h, diag_value(db, 0x7F5, m.data,
                                                "intp_chg_silent_expected")))
            except Exception as exc:                       # noqa: BLE001
                # never die silently: a dead reader reads as "no frames"
                errors.append("vehicle_rx: %r on 0x%X %s"
                              % (exc, m.arbitration_id, bytes(m.data).hex()))

    def tagger():
        seq = 0
        nxt = time.perf_counter()
        while not stop.is_set():
            try:
                veh.send(can.Message(arbitration_id=TAG_ID,
                                     is_extended_id=True,
                                     data=tag_payload(seq)), timeout=0.1)
                sent[seq] = time.perf_counter()
            except Exception as exc:                       # noqa: BLE001
                # not accepted by the Kvaser (e.g. nothing on the vehicle
                # segment acknowledging yet): counted, never recorded as
                # sent, and the tagger lives on (it died here 2026-10-14)
                tag_errors.append((time.perf_counter(), repr(exc)))
                # and drop it from the Kvaser's own queue: a timed-out frame
                # stays there, retried by the hardware, and the whole queue
                # went out at once when CAN1 next acknowledged -- a "burst"
                # that was the bench's, not the board's (2026-10-14 run 3)
                try:
                    veh.flush_tx_buffer()
                except Exception:                          # noqa: BLE001
                    pass
            seq += 1
            nxt += 1.0 / TAG_FPS
            time.sleep(max(0.0, nxt - time.perf_counter()))

    def charger():
        nxt = time.perf_counter()
        page = P.encode(P.BEL, 0x18FFD4C0, {
            "BELINV_keySwitch": 1, "BELINV_vehicleConnected": 1,
            "BELINV_evseConnected": 1, "BELINV_chargerEnergyFlowEnable": 1,
            "BELINV_state": 12}, "BELINV_statusMultiplexer", 0)
        # SEND only, 10 Hz, and only in normal mode. Receiving is its own
        # thread (charger_rx): draining inside this loop held the lock for
        # 52 s once the listen-only Innomaker saw the board's retries
        # (31,561 frames), and stamped every frame of a 100 ms batch with
        # one host time (2026-10-14 first run).
        while not stop.is_set():
            with lock:
                b = state["chg"] if state["mode"] == "normal" else None
                if b is not None:
                    try:
                        b.send(can.Message(arbitration_id=0x18FFD4C0,
                                           is_extended_id=True, data=page),
                               timeout=0.05)
                    except Exception:                      # noqa: BLE001
                        pass
            nxt += 0.1
            time.sleep(max(0.0, nxt - time.perf_counter()))

    def charger_rx():
        while not stop.is_set():
            b = state["chg"]
            if b is None:
                time.sleep(0.01)
                continue
            # under the lock: a libusb read in flight while the main thread
            # stops and restarts the device (gs_usb start() resets it)
            # segfaulted the process (2026-10-14 second run)
            with lock:
                try:
                    m = b.recv(timeout=0.02)
                except Exception:                          # noqa: BLE001
                    m = None
            if m is None or not m.is_rx or m.arbitration_id != TAG_ID:
                continue
            s = tag_seq(m.data)
            if s is None:
                continue
            h = time.perf_counter()
            row = (h, s, m.timestamp)
            if state["mode"] == "listen":
                witnessed_b.append(row)
            elif state["reopen"] is not None:
                received.append(row)
            else:
                received_a.append(row)

    def acker_rx():
        while acker is not None and not stop.is_set():
            try:
                acker.recv(timeout=0.1)
            except Exception:                              # noqa: BLE001
                return

    threads = [threading.Thread(target=f, daemon=True)
               for f in (serial_rx, vehicle_rx, tagger, charger, charger_rx,
                         acker_rx)]
    try:
        with lock:
            state["chg"] = bcw.open_gs_usb()
        for t in threads:
            t.start()
        for _i in range(20):          # the VCU's page-00 burst, flow 1
            veh.send(can.Message(arbitration_id=P.CMD_ID, is_extended_id=True,
                                 data=P.enc_master(1, P.MODE_CHARGER)))
            time.sleep(0.05)
        time.sleep(sa)
        b0 = time.perf_counter()
        with lock:
            gs = state["chg"].gs_usb
            gs.stop()
            gs.start(GS_CAN_MODE_LISTEN_ONLY | GS_CAN_MODE_HW_TIMESTAMP)
            state["mode"] = "listen"
        print("Innomaker LISTEN-ONLY at host t=%.3f (device flags 0x%x)"
              % (b0, gs.device_flags))
        time.sleep(sb)
        b1 = time.perf_counter()
        with lock:
            # everything the listen-only Innomaker already holds is phase
            # B's: drained HERE, before the switch, so a copy received in
            # listen-only but read after it is not counted as a phase C
            # delivery (run 5 showed seq 1543 twice at C's start, 2026-10-14)
            gs.stop()
            for _i in range(20000):
                try:
                    m = state["chg"].recv(timeout=0.0)
                except Exception:                          # noqa: BLE001
                    break
                if m is None:
                    break
                if m.is_rx and m.arbitration_id == TAG_ID and \
                        tag_seq(m.data) is not None:
                    witnessed_b.append((time.perf_counter(), tag_seq(m.data),
                                        m.timestamp))
            gs.start()
            state["mode"] = "normal"
            state["reopen"] = time.perf_counter()
        print("Innomaker NORMAL again at host t=%.3f (device flags 0x%x)"
              % (state["reopen"], gs.device_flags))
        time.sleep(sc)
        c1 = time.perf_counter()
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        with lock:
            if state["chg"] is not None:
                state["chg"].shutdown()
        veh.shutdown()
        if acker is not None:
            acker.shutdown()
        ser.close()
    if args.out:
        raw = os.path.splitext(args.out)[0] + "_rows.csv"
        with open(raw, "w") as f:
            f.write("phase,host_t,seq,device_t,in_sent,sent_host_t\n")
            for ph, rows in (("A", received_a), ("B", witnessed_b),
                             ("C", received)):
                for h, sq, d in rows:
                    f.write("%s,%.6f,%d,%.6f,%d,%s\n" % (
                        ph, h, sq, d, sq in sent,
                        "%.6f" % sent[sq] if sq in sent else ""))
        print("raw rows: %s" % raw)
        sraw = os.path.splitext(args.out)[0] + "_serial.txt"
        with open(sraw, "w", encoding="utf-8") as f:
            for h, x in ser_lines:
                f.write("%.6f\t%s\n" % (h, x))
        print("serial lines: %s" % sraw)
        # how fast txf climbed in phase B (spec 2.2: each failed
        # transmission counted ONCE): the serial values between the switch
        # and the first 255, against the offer of TAG_FPS frames/s
        climb = [(h - b0, v) for h, v in ser_counters if b0 <= h <= b1]
        if climb:
            print("txf in phase B (s from its start, value): %s"
                  % ", ".join("%.1f:%d" % c for c in climb))
    print("vehicle-side acknowledger: %s" % args.vehicle_acker)
    # the listen-only witness: how long the board kept retrying each frame
    # toward the charger while nothing acknowledged (spec 2.2: abandoned
    # once held longer than 100 ms). Device clock. Reported, not judged.
    span = {}
    for _h, sq, d in witnessed_b:
        lo, hi = span.get(sq, (d, d))
        span[sq] = (min(lo, d), max(hi, d))
    if span:
        sp = sorted((hi - lo) * 1000 for lo, hi in span.values())
        print("listen-only witness, phase B: %d distinct frames on the "
              "charger wire; each seen over p50 %.1f ms, p95 %.1f ms, max "
              "%.1f ms of device time (first to last copy)"
              % (len(sp), sp[len(sp) // 2], sp[int(len(sp) * 0.95)], sp[-1]))
    print("serial: %d lines, %d diag lines parsed, %d damaged diag lines "
          "skipped" % (len(ser_lines), len(ser_counters), damaged[0]))
    print("CAN 0x7F6 / 0x7F5 frames on the vehicle side: %d / %d (none "
          "expected while the port is listen-only, spec 2.3)"
          % (len(counters), len(flags)))
    print("tagged frames the Kvaser did not accept: %d%s"
          % (len(tag_errors), (" (first %s)" % (tag_errors[0],))
             if tag_errors else ""))
    for e in errors[:5]:
        print("READER ERROR: %s" % e)
    if errors:
        print("READER ERRORS: %d -- the CAN cross-check is incomplete"
              % len(errors))
    rc_pairs = [(h, s) for h, s, _d in received]
    ra_pairs = [(h, s) for h, s, _d in received_a]
    if sent:
        print("tagged frames sent: seq 0..%d" % max(sent))
    for name, rows in (("phase A", received_a), ("phase B (listen-only "
                       "witness)", witnessed_b), ("phase C", received)):
        ss = [s for _h, s, _d in rows]
        print("received %s: %d tagged, seq %s..%s, %d of them in the sent "
              "record" % (name, len(rows), min(ss, default=None),
                          max(ss, default=None),
                          sum(1 for s in ss if s in sent)))
    ok, lines = judge(sent, rc_pairs, state["reopen"], ser_counters,
                      ser_flags, (b0, b1), (state["reopen"], c1), ra_pairs,
                      burst_dev=[d for _h, _s, d in received],
                      held_seq=held_seq_of(witnessed_b))
    for ln in lines:
        print("  " + ln.replace("0x7F6 intp_tx_fail_expected",
                                "txf (serial diag line; 0x7F6 "
                                "intp_tx_fail_expected)")
              .replace("0x7F5 intp_chg_silent_expected",
                       "silent (serial diag line; 0x7F5 "
                       "intp_chg_silent_expected)")
              .replace("no 0x7F6", "no serial diag line")
              .replace("no 0x7F5", "no serial diag line"))
    for name, cv, sv in (("txf vs 0x7F6", counters, ser_counters),
                         ("silent vs 0x7F5", flags, ser_flags)):
        n, bad = crosscheck(cv, sv)
        print("  cross-check %s: %d CAN frames compared, %d disagree%s"
              % (name, n, len(bad), (" e.g. %s" % (bad[:3],)) if bad else ""))
        if bad:
            ok = False
    in_b = [s for t, s, _d in witnessed_b if b0 <= t <= b1]
    print("  listen-only witness in phase B: %d tagged frame(s) seen on the "
          "charger wire (not judged: what a listen-only receiver delivers of "
          "unacknowledged frames is the adapter's business)" % len(in_b))
    vp = [(h - b0, x) for h, x in ser_lines if "vehicle port ->" in x]
    for dt, x in vp:
        print("  serial %+.3f s from the phase B start: %s" % (dt, x))
    print("2.2 single-adapter: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# --- self-test --------------------------------------------------------------

def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    check(tag_seq(tag_payload(70000)) == 70000, "the tag round-trips")
    check(tag_seq(bytearray(8)) is None, "a frame without the magic is not ours")
    # host timeline: A 0-20 s, B 20-30 s (closed), C 30-40 s (reopened)
    sent = {i: i * 0.02 for i in range(2000)}
    counters = [(t, 3 if t < 20 else 3 + int(t - 20) * 25 if t < 30 else 255)
                for t in range(0, 40)]
    counters = [(t, min(v, 255)) for t, v in counters]
    flags = [(t + 0.5, 1 if 20.6 <= t + 0.5 < 30.4 else 0) for t in range(40)]
    fresh = [(s * 0.02 + 0.006, s) for s in range(1501, 2000)]
    ok, lines = judge(sent, fresh, 30.0, counters, flags, (20, 30), (30, 40))
    check(ok, "a counter that rises, current frames and a flag that clears "
          "pass -> %s" % lines)
    flat = [(t, 3) for t in range(40)]
    ok, lines = judge(sent, fresh, 30.0, flat, flags, (20, 30), (30, 40))
    check(not ok and any("did not rise" in x for x in lines),
          "a counter that does not rise FAILS: no evidence of silence")
    stale = [(30.005, s) for s in range(1490, 1500)] + fresh
    ok, lines = judge(sent, stale, 30.0, counters, flags, (20, 30), (30, 40))
    check(not ok and any("older than the 100 ms" in x for x in lines),
          "queued frames delivered at the reopen FAIL -> %s"
          % [x for x in lines if "FAIL" in x])
    never = [(t, 0) for t, _f in flags]
    ok, lines = judge(sent, fresh, 30.0, counters, never, (20, 30), (30, 40))
    check(not ok and any("read 0 at" in x for x in lines),
          "a silent flag that never sets in the silent phase FAILS (spec "
          "2.2) -> %s" % [x for x in lines if "FAIL" in x])
    drops = [(t, 0 if 26 <= t <= 27 else f) for t, f in flags]
    ok, lines = judge(sent, fresh, 30.0, counters, drops, (20, 30), (30, 40))
    check(not ok and any("read 0 at 6.5 s" in x for x in lines),
          "...and one that drops back to 0 before the reopen FAILS -> %s"
          % [x for x in lines if "FAIL" in x])
    early = [(t, 0 if t < 21.4 else f) for t, f in flags]
    ok, lines = judge(sent, fresh, 30.0, counters, early, (20, 30), (30, 40))
    check(ok, "a 0 inside the first 500 ms + one status period is not "
          "judged -> %s" % [x for x in lines if "FAIL" in x])
    base_a = [(s * 0.02 + 0.006, s) for s in range(100, 900)]
    edge = [(1495 * 0.02 + 0.104, 1495)] + fresh
    ok, lines = judge(sent, edge, 30.0, counters, flags, (20, 30), (30, 40),
                      base_a)
    check(not ok and any("LESS than phase A's measured latency" in x
                         for x in lines),
          "a frame 4 ms over the age-out with 6 ms of measured latency FAILS "
          "with the latency note -> %s" % [x for x in lines if "NOTE" in x])
    far = [(1495 * 0.02 + 0.150, 1495)] + fresh
    ok, lines = judge(sent, far, 30.0, counters, flags, (20, 30), (30, 40),
                      base_a)
    check(not ok and not any("NOTE" in x for x in lines),
          "...one 50 ms over gets no note")
    sat = [(t, 255) for t in range(40)]
    ok, lines = judge(sent, fresh, 30.0, sat, flags, (20, 30), (30, 40))
    check(not ok and any("already saturated" in x for x in lines),
          "a counter already at 255 cannot show a rise and FAILS")
    stuck = [(t, f if t < 30 else 1) for t, f in flags]
    ok, lines = judge(sent, fresh, 30.0, counters, stuck, (20, 30), (30, 40))
    check(not ok and any("did not clear" in x for x in lines),
          "a silent flag that never clears FAILS")
    ok, lines = judge(sent, [], 30.0, counters, flags, (20, 30), (30, 40))
    check(not ok and any("no tagged frame" in x for x in lines),
          "nothing reaching the reopened Innomaker FAILS")
    burst = [(30.01 + i * 0.001, s) for i, s in enumerate(range(1500, 1520))]
    sent2 = dict(sent)
    for s in range(1500, 1520):
        sent2[s] = 30.0                       # young, but all at once
    ok, lines = judge(sent2, burst, 30.0, counters, flags, (20, 30),
                      (30, 40))
    check(ok and any("reported, not judged" in x for x in lines),
          "20 YOUNG frames inside 100 ms PASS: 2.2 judges age, not count "
          "(reviewer, 2026-10-14) -> %s" % [x for x in lines if "FAIL" in x])
    # the run 7 shape: the held frame (188 ms) then five queued frames
    # 89..11 ms old, all within 2.2 ms, then current frames
    run7 = {1542: 29.812, 1547: 29.911, 1548: 29.931, 1549: 29.951,
            1550: 29.971, 1551: 29.989}
    sent3 = dict(sent)
    sent3.update(run7)
    rec = [(30.0 + k * 0.0004, s) for k, s in enumerate(sorted(run7))]
    rec += [(30.02 * 1 + i * 0.02, 1552 + i) for i in range(1, 300)]
    for _t, s_ in rec[6:]:
        sent3[s_] = _t - 0.0005
    ok, lines = judge(sent3, rec, 30.0, counters, flags, (20, 30), (30, 40),
                      held_seq=1542)
    check(not ok and any("FAIL" in x and "seq 1542, the held frame" in x
                         for x in lines)
          and not any(x.startswith("OPEN") for x in lines),
          "run 7's shape: the held frame's 188 ms since queued FAILS (the "
          "user's ruling: 2.2 counts from queueing) and is named as held "
          "-> %s" % [x for x in lines if "FAIL" in x])
    ok, lines = judge(sent3, rec, 30.0, counters, flags, (20, 30), (30, 40),
                      held_seq=None)
    check(not ok and any("older than the 100 ms" in x and "seq 1542" in x
                         for x in lines),
          "...and without the held frame named, its 188 ms FAILS the same")
    fixed = dict(sent3)
    del fixed[1542]
    rec_fixed = [r for r in rec if r[1] != 1542]
    ok, lines = judge(fixed, rec_fixed, 30.0, counters, flags, (20, 30),
                      (30, 40), held_seq=1542)
    check(ok, "the fix's shape: the held frame abandoned, the five queued "
          "frames (89..11 ms) and the rest current -> PASS %s"
          % [x for x in lines if "FAIL" in x])
    stale2 = dict(fixed)
    stale2[1549] = 29.85                       # queued 150 ms before
    ok, lines = judge(stale2, rec_fixed, 30.0, counters, flags, (20, 30),
                      (30, 40), held_seq=1542)
    check(not ok and any("seq 1549" in x for x in lines),
          "a QUEUED frame older than 100 ms FAILS")
    # the reader-thread defect: 0x7F5's flag has a value table
    try:
        import cantools
        import paths
        db = cantools.database.load_file(paths.diag_dbc())
        v = diag_value(db, 0x7F5, bytes.fromhex("450d3075b00ca001"),
                       "intp_chg_silent_expected")
        check(v in (0, 1), "0x7F5's silent flag decodes to an int (%r) "
              "despite its value table" % v)
        raised = False
        try:
            int(db.decode_message(0x7F5, bytes.fromhex("450d3075b00ca001"))
                ["intp_chg_silent_expected"])
        except TypeError:
            raised = True
        check(raised, "(the default decode's NamedSignalValue does raise in "
              "int() -- the defect diag_value avoids)")
    except ImportError:
        check(False, "cantools needed for the decode check")
    # 2026-10-14: the serial diag line (spec 2.3 / 8.3)
    good = ("diag st=MONITOR trip=0 flags=0xdf ilim=- vmax=3406 chgmax=30000 "
            "soc=88.0 evap=0 chg=12 mod=0 synth=0 ovf=0 txf=7 silent=1 fw=9 "
            "schema=4")
    check(parse_diag(good) == dict(state="MONITOR", txf=7, silent=1),
          "a whole diag line gives state, txf and silent")
    check(parse_diag(good[:120]) is None,
          "a diag line that lost its tail is skipped, not misread")
    check(parse_diag("       charger tx: abandon" + good) is None,
          "a diag line spliced onto another line is skipped")
    check(parse_diag(good.replace("txf=7", "txf=")) is None,
          "a diag line with an empty field is skipped")
    n, bad = crosscheck([(5.0, 7)], [(4.7, 6), (5.4, 7)])
    check(n == 1 and not bad, "a CAN value matching a serial value within "
          "0.6 s agrees")
    n, bad = crosscheck([(5.0, 9)], [(4.7, 6), (5.4, 7)])
    check(n == 1 and bad == [(5.0, 9, [6, 7])],
          "a CAN value no nearby serial value equals is a disagreement")
    n, bad = crosscheck([(5.0, 9)], [(9.0, 9)])
    check(n == 0, "a CAN value with no serial line near it is not compared")
    # phase B carried by the serial line alone, as on a 2.3 port: the CAN
    # frames stop 2 s into B, and the serial counters/flags still pass
    ok, lines = judge(sent, fresh, 30.0, counters, flags, (20, 30), (30, 40))
    can_b = [(t, v) for t, v in flags if not 22 <= t <= 30]
    ok2, lines2 = judge(sent, fresh, 30.0, counters, can_b, (20, 30),
                        (30, 40))
    check(ok and not ok2,
          "the same run judged on CAN frames that stop 2 s into phase B "
          "FAILS (why the serial line carries it) -> %s"
          % [x for x in lines2 if "FAIL" in x])
    print("\nbench_charger_silent self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--seconds", default="20,10,10",
                    help="phases A,B,C in seconds")
    ap.add_argument("--kvaser-channel", type=int, default=0)
    ap.add_argument("--port", default=None,
                    help="the X2's serial port: the phases are judged from "
                    "its diag line (spec 2.3 / 8.3)")
    ap.add_argument("--vehicle-acker", choices=("pcan", "none"),
                    default="pcan",
                    help="a node ACKing on the vehicle segment throughout, as "
                    "on the truck (default: the PCAN in normal mode)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.port:
        ap.error("--port is required: with the vehicle port listen-only "
                 "the CAN diag frames stop, and the serial diag line is the "
                 "only record of phase B")
    # with no --out, the default output goes where it went before the
    # move: SeaDrive's interposer-firmware/ folder, never this tree
    import paths
    here = None if args.out else paths.fixtures()
    out = args.out or os.path.join(here, "bench_charger_silent_%s.txt"
                                   % datetime.datetime.now()
                                   .strftime("%Y%m%dT%H%M%S"))
    import bench_charger_witness as bcw
    tee = bcw.Tee(out)
    sys.stdout = tee
    try:
        rc = run_bench(args)
    finally:
        sys.stdout = tee.out
        tee.f.close()
        print("saved verbatim to %s" % out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
