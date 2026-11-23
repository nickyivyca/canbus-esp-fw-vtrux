"""Spec 2.3 diagnostic: does a vehicle-port mode switch WITH an acknowledger
present add vehicle-side bus errors? (reviewer, 2026-10-15: the one
unexplained count in the saved runs -- re-run restart-no-standby's +204
across a listen-only -> normal switch, with the scenario's witness open in
ACK mode. A switch with an acknowledger present that adds errors would be
board-side.)

Roles (reviewer-approved swap: the Kvaser cannot be made passive):
  Kvaser     on the vehicle segment, NORMAL: acknowledges
  PCAN       on the vehicle segment, PASSIVE: the witness, logging every
             frame and error frame, and its own bus-state warnings
  Innomaker  on the charger segment: the charger stimulus
The WiCAN (mode 0) does not acknowledge (shown by vport_bench's step 0 on
the same day).

Each cycle: the Innomaker sends charger frames (extended, SA 0xC0,
bench_hold12.FILLER, 32-bit counter) at RATE fps for ON_S, so the port goes
normal and the board bridges them toward the vehicle; then CYCLE_S - ON_S
of silence, and 2 s after the last frame the port falls back to
listen-only. With ON_S = 3 and CYCLE_S = 10 the switches are 5 s apart,
and the board's status block comes every 5 s, so each status interval
holds exactly one switch: per-switch error counts without a finer counter.

THE KVASER ON KVASER_USB (tester, 2026-11-13; the reviewer's A): an UP
kvaser_usb link acknowledges with no socket open, so each phase sets the
Kvaser's link and reads it back first (phase_link): UP for ACK, DOWN for
NOACK, no socket opened. A link not as the phase needs is a VOID line with
the kernel's USB/CAN lines, and the run stops there (exit 1, no RESULT
line). Under CANlib, "closed" was enough for NOACK.

A PCAN READER THAT DIES (the charge-interposer reviewer, 2026-11-19): the
PCAN's receive thread runs under reader_death's guard, and a phase whose
reader died is VOID with the error, its rows not judged; no later phase
runs and no RESULT line follows (exit 1 with a VOID line: VOID). It was a
TEST FAULT, which the suite counted FAIL. Since 2026-11-20 (the reviewer's
PCAN item 2) the PCAN opens with bus states reported: its state rows are
not error rows (shown as "PCAN state row(s)", not judged), and a row
reporting frames lost VOIDs the phase the same way.

Phases:
  ACK     the Kvaser's link UP: CYCLES cycles, the measurement
  NOACK   the can-fail: the Kvaser's link DOWN (nothing acknowledges), CANFAIL
          cycles: errors must appear both on the PCAN (error frames) and in
          the board's LOSS veh err -- else neither instrument is shown able
          to see an error, and the phase is a TEST FAULT
Per status interval: the veh err delta, the switches in it (MODE line's
switches= delta) and the PCAN's error frames in it.

    py -3.14 bench_switch_errors.py --self-test
    py -3.14 bench_switch_errors.py --port COM3 --out <file.txt>

Tester, 2026-10-16. Bench image 756d6714, no hands.
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
import kvaser_link                 # noqa: E402  (the Kvaser on kvaser_usb)

import bench_hold12 as H                                     # noqa: E402

RATE = 20.0
ON_S = 3.0
CYCLE_S = 10.0
CYCLES = 20
CANFAIL = 3
SETTLE_S = 6.0          # one status block before the phase
ROWS_BASE = None        # run_bench: the PCAN's rows per phase go next to --out

RE_T = re.compile(r"^t=(\d+)s\s+state=")
RE_VEH = re.compile(r"veh\(rx=(\d+) tx=(\d+) err=(\d+) off=(\d+) peak=(\d+)\)")
RE_SW = re.compile(r"switches=(\d+)")


def samples(stamped):
    """Serial (host, line) rows -> [(host, uptime_s, veh_err, switches)]
    per status block (the t= line opens it; MODE and, sometimes, LOSS
    follow).

    A BLOCK WITH NO LOSS LINE READS veh err 0. The board prints LOSS only
    when something dropped, a port is bus-off or a port has bus errors
    (firmware/README.md; artifacts README "A firmware gap this exposed"),
    and on this image (756d6714) an errors-only LOSS line is shown printed:
    the 2026-10-15 re-runs logged `veh(rx=0 tx=0 err=6553 ...)`. The first
    version of this parser required a LOSS line and so read nothing at all
    on a clean, freshly booted board (2026-10-16 00:10 run, ACK phase)."""
    out, cur = [], None

    def close(c):
        if c is not None and c[3] is not None:
            out.append((c[0], c[1], c[2] if c[2] is not None else 0, c[3]))
    for h, ln in stamped:
        m = RE_T.match(ln.strip())
        if m:
            close(cur)
            cur = [h, int(m.group(1)), None, None]
            continue
        if cur is None:
            continue
        m = RE_SW.search(ln)
        if m and "MODE" in ln:
            cur[3] = int(m.group(1))
        m = RE_VEH.search(ln)
        if m:
            cur[2] = int(m.group(3))
    close(cur)
    return out


def intervals(smp, err_hosts):
    """-> [(t0_up, t1_up, d_err, d_switches, pcan_errors)] between
    consecutive status blocks; pcan_errors from the witness's error-frame
    host stamps falling in (h0, h1]."""
    out = []
    for a, b in zip(smp, smp[1:]):
        if b[1] < a[1]:
            out.append((a[1], b[1], None, None, None))     # a reboot
            continue
        pe = sum(1 for h in err_hosts if a[0] < h <= b[0])
        out.append((a[1], b[1], b[2] - a[2], b[3] - a[3], pe))
    return out


def judge(iv, acked):
    """-> (verdict, lines). ACK: report every interval with a switch; any
    error delta there is listed. NOACK (the can-fail): errors must appear
    in the board's count AND on the PCAN, else TEST FAULT."""
    lines = []
    if any(x[2] is None for x in iv):
        return "fault", ["TEST FAULT: the board rebooted during the phase"]
    sw = [x for x in iv if x[3]]
    if not sw:
        return "fault", ["TEST FAULT: no status interval held a switch -- the "
                         "stimulus did not switch the port"]
    tot_err = sum(x[2] for x in iv)
    tot_pe = sum(x[4] for x in iv)
    with_err = [x for x in sw if x[2]]
    lines.append("%d status interval(s), %d with a switch (%d switch(es) in "
                 "all); veh err +%d over the phase, %d in switch intervals; "
                 "PCAN error frames %d" % (len(iv), len(sw),
                                           sum(x[3] for x in sw), tot_err,
                                           sum(x[2] for x in sw), tot_pe))
    for x in with_err:
        lines.append("  t=%d..%ds: %d switch(es), veh err +%d, PCAN error "
                     "frames %d" % (x[0], x[1], x[3], x[2], x[4]))
    if acked:
        return ("errors" if tot_err or tot_pe else "clean"), lines
    if tot_err and tot_pe:
        return "canfail-ok", lines
    return "fault", lines + ["TEST FAULT: with nothing acknowledging, the "
                             "board's count moved +%d and the PCAN saw %d "
                             "error frame(s) -- both must, or the "
                             "instruments are not shown able to see an "
                             "error" % (tot_err, tot_pe)]


def self_test():
    fails = []

    def check(c, w):
        print("  %s  %s" % ("ok  " if c else "FAIL", w))
        if not c:
            fails.append(w)

    def blk(h, t, err, sw):
        return [(h, "t=%ds state=PASSTHROUGH | v2c=0 c2v=0" % t),
                (h, "  MODE veh=normal core wants normal | switches=%d "
                    "refused=0 suppressed=0" % sw),
                (h, "  LOSS veh(rx=0 tx=0 err=%d off=0 peak=0) chg(rx=0 tx=0 "
                    "err=0 off=0 ring=0/0) bridge=0 emit=0" % err)]
    st = (blk(1.0, 5, 100, 2) + blk(6.0, 10, 100, 3) + blk(11.0, 15, 100, 4)
          + blk(16.0, 20, 100, 5))
    smp = samples(st)
    check(len(smp) == 4 and smp[1] == (6.0, 10, 100, 3),
          "status blocks parse -> %s" % (smp[:2],))
    v, ln = judge(intervals(smp, []), acked=True)
    check(v == "clean", "ACK: switches, no error anywhere -> clean")

    def blk_noloss(h, t, sw):
        return blk(h, t, 0, sw)[:2]
    nl = (blk_noloss(1.0, 5, 2) + blk_noloss(6.0, 10, 3)
          + blk_noloss(11.0, 15, 4))
    smp_nl = samples(nl)
    check(len(smp_nl) == 3 and all(x[2] == 0 for x in smp_nl),
          "a block with no LOSS line reads veh err 0 (the board prints none "
          "when nothing is lost) -> %s" % (smp_nl,))
    v, ln = judge(intervals(smp_nl, []), acked=True)
    check(v == "clean", "can-fail of the 00:10 run's defect: blocks with no "
          "LOSS line still give switch intervals -> %s" % v)
    mixed = blk_noloss(1.0, 5, 2) + blk(6.0, 10, 204, 3)
    v, ln = judge(intervals(samples(mixed), []), acked=True)
    check(v == "errors" and any("+204" in x for x in ln),
          "no LOSS line, then a LOSS line with err=204 -> +204 found")
    st2 = blk(1.0, 5, 100, 2) + blk(6.0, 10, 304, 3) + blk(11.0, 15, 304, 4)
    v, ln = judge(intervals(samples(st2), []), acked=True)
    check(v == "errors" and any("+204" in x for x in ln),
          "can-fail: a +204 in a switch interval is found and listed")
    v, ln = judge(intervals(smp, [3.0, 7.0]), acked=True)
    check(v == "errors", "PCAN error frames alone are found too")
    v, ln = judge(intervals(samples(st2), [3.0, 7.0]), acked=False)
    check(v == "canfail-ok", "NOACK: errors in both instruments -> can-fail OK")
    v, ln = judge(intervals(samples(st2), []), acked=False)
    check(v == "fault", "NOACK: the board's count moved but the PCAN saw "
          "nothing -> TEST FAULT")
    v, ln = judge(intervals(smp, []), acked=False)
    check(v == "fault", "NOACK: nothing seen anywhere -> TEST FAULT, not clean")
    flat = blk(1.0, 5, 100, 2) + blk(6.0, 10, 100, 2)
    v, ln = judge(intervals(samples(flat), []), acked=True)
    check(v == "fault", "no switch in any interval -> TEST FAULT")
    rb = blk(1.0, 300, 100, 2) + blk(6.0, 5, 0, 1)
    v, ln = judge(intervals(samples(rb), []), acked=True)
    check(v == "fault", "a reboot mid-phase -> TEST FAULT")

    # 2026-11-13, the reviewer's A: the Kvaser's link per phase
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
    ok1, _ = phase_link(True, Link)
    ok2, _ = phase_link(False, Link)
    check(ok1 and ok2 and Link.calls == [True, False],
          "link: ACK brings it up, NOACK takes it down -> %s" % Link.calls)
    Link.fail = "the kvaser link reads up in sysfs, and this check needs it down"
    ok, ln = phase_link(False, Link)
    check(not ok and ln.startswith("VOID: kvaser link: the kvaser link reads up")
          and "error -71" in ln,
          "link: an UP link under NOACK is caught, a VOID line with the kernel's lines")
    Link.fail = None
    for ok, what in reader_death_checks(Link):
        check(ok, what)
    # 2026-11-23: the Innomaker by bench_charger_witness's opener (with
    # bench_rate_feasibility retired), and the reviewer's sweep ruling on
    # premise failures: the port or the Innomaker not opening is a VOID line
    import inspect
    pl = premise_line("the Innomaker (role canchg)", OSError("no device"))
    check(pl == "VOID: the Innomaker (role canchg) did not open (OSError: no device): "
          "no phase run", "premise_line: a VOID line -> %s" % pl)
    src = inspect.getsource(run_bench)
    i_hp = src.find("        hp = U.HostPort(")
    i_gs = src.find("        gs = bcw.open_gs_usb()\n    except Exception as e:")
    check(0 < i_hp < i_gs and src.count("log(premise_line(") == 2
          and src.find("return 3", i_hp) < i_gs
          and src.find("hp.close()", i_gs) < src.find("return 3", i_gs),
          "run_bench: the X2's port and the Innomaker (bench_charger_witness's "
          "opener) each a VOID line and exit 3 when they do not open, the port "
          "closed after an Innomaker that did not")
    print("\nbench_switch_errors self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def reader_death_checks(link):
    """-> [(ok, what)]. run_phases end to end with stand-ins: the PCAN gives
    three bridged frames after the baseline take(), then raises a PCAN
    status (a VOID line naming the error, no RESULT, no TEST FAULT, NOACK
    not run) or goes quiet (the three are counted and both phases judged,
    as before)."""
    import types
    import bench_usb_host as U
    import reader_death
    real = time

    class Port(object):
        stamped = []

        def mark(self, text):
            return real.perf_counter()

    class Inno(object):
        def recv(self, timeout=None):
            real.sleep(0.005)

        def send(self, msg, timeout=None):
            pass

    def frames():
        return [types.SimpleNamespace(timestamp=1.0, arbitration_id=H.FILLER,
                                      is_error_frame=False, is_rx=True,
                                      data=bytes([0, 0, 0, i, 0, 0, 0, 0]))
                for i in range(3)]

    class QuietBus(reader_death.RaisingBus):
        def recv(self, timeout=None):
            real.sleep(self.delay)
            if self.frames:
                return self.frames.pop(0)

    g = globals()
    saved = (U.open_pcan, g["SETTLE_S"], g["ON_S"], g["CYCLE_S"])
    out = []
    try:
        # frames at 40, 80, 120 ms, after the baseline take() at 20 ms; the
        # raise at 160 ms, well inside the phase's 540 ms
        g.update(SETTLE_S=0.02, ON_S=0.05, CYCLE_S=0.5)
        U.open_pcan = lambda acks: reader_death.RaisingBus(
            frames(), RuntimeError("A PCAN status 0x2 OVERRUN"), delay=0.04)
        link.calls = []
        logged = []
        v = run_phases(Port(), Inno(), logged.append, 1, link)
        voids = [x for x in logged if x.startswith("VOID")]
        out.append((v is None and len(voids) == 1 and "OVERRUN" in voids[0]
                    and "the pcan reader died" in voids[0]
                    and not any(x.startswith("RESULT") or "TEST FAULT" in x
                                for x in logged),
                    "CAN-FAIL: a PCAN reader that raises mid-phase: no verdict "
                    "on its rows, no TEST FAULT, one VOID line with the error: "
                    "%s" % voids))
        out.append((link.calls == [True],
                    "... and the NOACK phase is not run after it: %s" % link.calls))
        U.open_pcan = lambda acks: QuietBus(frames(), delay=0.04)
        link.calls = []
        logged = []
        v = run_phases(Port(), Inno(), logged.append, 1, link)
        seen = [x for x in logged if "3 seen complete" in x]
        out.append((v is not None and set(v) == {"ACK", "NOACK"}
                    and "void" not in v.values() and len(seen) == 2
                    and not any(x.startswith("VOID") for x in logged),
                    "the same rows from a reader that lived are counted, both "
                    "phases judged: %s" % v))
        # the reviewer's PCAN item 2 (2026-11-20): the PCAN opened with bus
        # states reported. An overrun row mid-phase VOIDs it as lost frames,
        # NOACK not run; a state row is not an error row, only shown
        R = reader_death.report_rows()
        U.open_pcan = lambda acks: reader_death.report_bus(
            frames() + [R["passive"], R["overrun"]], delay=0.04)
        link.calls = []
        logged = []
        v = run_phases(Port(), Inno(), logged.append, 1, link)
        voids = [x for x in logged if x.startswith("VOID")]
        out.append((v is None and len(voids) == 1 and "the pcan reader lost frames" in voids[0]
                    and link.calls == [True]
                    and not any(x.startswith("RESULT") for x in logged),
                    "CAN-FAIL: an overrun row mid-phase: the phase VOID as lost "
                    "frames, NOACK not run: %s" % voids))
        U.open_pcan = lambda acks: reader_death.report_bus(
            frames() + [R["passive"]], delay=0.04)
        link.calls = []
        logged = []
        v = run_phases(Port(), Inno(), logged.append, 1, link)
        ack = [x for x in logged if x.startswith("phase ACK:") and "seen complete" in x]
        out.append((v is not None and "void" not in v.values() and len(ack) == 1
                    and "3 seen complete" in ack[0] and "1 PCAN state row(s)" in ack[0]
                    and not any(x.startswith("VOID") for x in logged),
                    "a state row is shown, not counted as an error row, and the "
                    "phases judged: %s" % ack))
    finally:
        U.open_pcan, g["SETTLE_S"], g["ON_S"], g["CYCLE_S"] = saved
    return out


def phase_link(acked, link):
    """The Kvaser's link for a phase (2026-11-13, the reviewer's A): an UP
    kvaser_usb link acknowledges with no socket open, so ACK brings it up
    and NOACK takes it DOWN, each read back in sysfs; no socket is opened.
    -> (ok, line); not ok is a VOID line with the kernel's USB/CAN lines,
    and the phase does not run."""
    t0 = time.time()
    try:
        return True, link.set_link(bool(acked))
    except link.LinkError as e:
        return False, link.link_void(e, t0, time.time())


def phase(name, hp, gs, acked, cycles, log):
    import can
    import bench_usb_host as U
    pc = U.Rx(U.open_pcan(False), "pcan")
    gstop = threading.Event()

    def gdrain():
        while not gstop.is_set():
            gs.recv(timeout=0.05)
    gt = threading.Thread(target=gdrain, daemon=True)
    gt.start()
    import logging
    warn = [0]
    hnd = logging.Handler()
    hnd.emit = lambda rec: warn.__setitem__(0, warn[0] + 1)
    logging.getLogger("can.pcan").addHandler(hnd)
    log("phase %s: %s; settling one status block" % (
        name, "the Kvaser's link UP (acknowledging)" if acked else
        "the Kvaser's link DOWN (nothing acknowledges)"))
    time.sleep(SETTLE_S)
    h0 = hp.mark("%s start" % name)
    pc.take()
    msg = can.Message(arbitration_id=H.FILLER, is_extended_id=True,
                      data=bytearray(8))
    n = 0
    for c in range(cycles):
        end = time.perf_counter() + ON_S
        while time.perf_counter() < end:
            msg.data = bytearray(((n >> 24) & 255, (n >> 16) & 255,
                                  (n >> 8) & 255, n & 255, 0, 0, 0, 0))
            try:
                gs.send(msg, timeout=0.05)
                n += 1
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(1.0 / RATE)
        time.sleep(CYCLE_S - ON_S)
    time.sleep(SETTLE_S)
    h1 = hp.mark("%s end" % name)
    rows = pc.take()
    logging.getLogger("can.pcan").removeHandler(hnd)
    gstop.set()
    gt.join(1.0)
    pc.close()
    if ROWS_BASE:
        with open("%s_%s_pcan.txt" % (ROWS_BASE, name), "w",
                  encoding="utf-8") as f:
            for r in rows:
                f.write("%.6f %.6f %X %d %s\n" % (r[0], r[1], r[2],
                                                 1 if r[3] else 0,
                                                 r[4].hex()))
    if pc.death.error:
        # 2026-11-19, the charge-interposer reviewer: a reader that dies is
        # not a product result; the phase is VOID, its rows not judged
        log("phase %s: %d cycles, %d charger frames sent -> VOID" % (name, cycles, n))
        log(pc.death.void_line())
        return "void"
    smp = [x for x in samples(hp.stamped) if h0 - 5.5 <= x[0] <= h1]
    err_hosts = [r[1] for r in rows if r[3]]
    iv = intervals(smp, err_hosts)
    v, lines = judge(iv, acked)
    bridged = sum(1 for r in rows if r[2] == H.FILLER and not r[3])
    # the PCAN opens with bus states reported (bench_usb_host.open_pcan, the
    # reviewer's PCAN item 2, 2026-11-20): its states come up as state rows,
    # kept out of the error rows and counted in death.states, where python-can
    # logged a warning; both are shown, neither is judged
    log("phase %s: %d cycles, %d charger frames sent, %d seen complete on the "
        "vehicle wire by the PCAN, %d PCAN bus-state warning(s), %d PCAN state "
        "row(s) -> %s"
        % (name, cycles, n, bridged, warn[0], pc.death.states, v.upper()))
    for ln in lines:
        log("  " + ln)
    return v


def run_phases(hp, gs, log, cycles, link=kvaser_link):
    """ACK then NOACK. -> {phase: verdict}, or None when a phase was not run
    (its Kvaser link not as it needs) or not judged (its PCAN reader died,
    the reviewer's ruling of 2026-11-19): then a VOID line is logged, no
    later phase runs and no RESULT line follows; the exit code is 1 with a
    VOID line and no FAIL line, which script_judge reads VOID."""
    v = {}
    for name, acked, n in (("ACK", True, cycles), ("NOACK", False, CANFAIL)):
        ok, line = phase_link(acked, link)
        log(line)
        if not ok:
            log("phase %s not run: the Kvaser's link is not as the phase needs" % name)
            return None
        v[name] = phase(name, hp, gs, acked, n, log)
        if v[name] == "void":
            log("phase %s not judged: its PCAN reader died or lost frames" % name)
            return None
    return v


def premise_line(what, e):
    """A premise not met (the board's port or the Innomaker that did not
    open) as a VOID line, exit 3 (2026-11-23, the reviewer's sweep ruling: a
    premise failure prints a VOID line rather than a bare non-zero exit;
    before, a traceback, read FAIL)."""
    return "VOID: %s did not open (%s: %s): no phase run" % (what, type(e).__name__, e)


def run_bench(a):
    import bench_usb_host as U
    import bench_charger_witness as bcw
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    log("run at %s; charger frames 0x%08X at %.0f fps, %.0f s on / %.0f s "
        "cycle; ACK %d cycles (the Kvaser's link up, PCAN passive), NOACK %d "
        "(the Kvaser's link down)" % (datetime.datetime.now().isoformat(), H.FILLER,
                                      RATE, ON_S, CYCLE_S, a.cycles, CANFAIL))
    global ROWS_BASE
    ROWS_BASE = os.path.splitext(a.out)[0]
    try:
        hp = U.HostPort(a.port, os.path.splitext(a.out)[0] + "_serial.txt")
    except Exception as e:                                 # noqa: BLE001
        log(premise_line("the X2's port %s" % a.port, e))
        out.close()
        return 3
    # the Innomaker, role canchg (bench_charger_witness's opener;
    # bench_rate_feasibility's, the same, until 2026-11-23)
    try:
        gs = bcw.open_gs_usb()
    except Exception as e:                                 # noqa: BLE001
        log(premise_line("the Innomaker (role canchg)", e))
        hp.close()
        out.close()
        return 3
    try:
        v = run_phases(hp, gs, log, a.cycles)
    finally:
        gs.shutdown()
        hp.close()
    if v is None:
        out.close()
        return 1
    v1, v2 = v["ACK"], v["NOACK"]
    log("RESULT: ACK %s, NOACK (can-fail) %s" % (v1, v2))
    out.close()
    return 0 if v2 == "canfail-ok" and v1 != "fault" else 1


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("link: the same for both phases", "        return True, link.set_link(bool(acked))",
     "        return True, link.set_link(True)"),
    ("link: never set", "        return True, link.set_link(bool(acked))",
     "        return True, \"not set\""),
    ("link: a failure not VOID", "        return False, link.link_void(e, t0, time.time())",
     "        return True, str(e)"),
    # 2026-11-19, the charge-interposer reviewer: a dead reader is VOID
    ("reader death: a dead reader's rows judged",
     "    if pc.death.error:\n", "    if False:\n"),
    ("reader death: no VOID line", "        log(pc.death.void_line())\n", ""),
    ("reader death: the phases go on after it",
     "        if v[name] == \"void\":\n"
     "            log(\"phase %s not judged: its PCAN reader died or lost frames\" % name)\n"
     "            return None\n", ""),
    # 2026-11-20, the reviewer's PCAN item 2
    ("PCAN item 2: the state rows not shown",
     "        % (name, cycles, n, bridged, warn[0], pc.death.states, v.upper()))",
     "        % (name, cycles, n, bridged, warn[0], 0, v.upper()))"),
    # 2026-11-23, the reviewer's sweep ruling on premise failures
    ("premise: the X2's port not opening a traceback again",
     "    try:\n        hp = U.HostPort(a.port, os.path.splitext(a.out)[0] + \"_serial.txt\")\n"
     "    except Exception as e:                                 # noqa: BLE001\n",
     "    hp = U.HostPort(a.port, os.path.splitext(a.out)[0] + \"_serial.txt\")\n"
     "    if False:\n"),
    ("premise: the Innomaker not opening a traceback again",
     "    try:\n        gs = bcw.open_gs_usb()\n"
     "    except Exception as e:                                 # noqa: BLE001\n",
     "    gs = bcw.open_gs_usb()\n    if False:\n"),
    ("premise: the port left open after the Innomaker's failure",
     "        log(premise_line(\"the Innomaker (role canchg)\", e))\n        hp.close()\n",
     "        log(premise_line(\"the Innomaker (role canchg)\", e))\n"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--cycles", type=int, default=CYCLES)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if not a.out:
        ap.error("--out is required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
