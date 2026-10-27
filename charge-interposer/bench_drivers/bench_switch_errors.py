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

Phases:
  ACK     the Kvaser open: CYCLES cycles, the measurement
  NOACK   the can-fail: the Kvaser CLOSED (nothing acknowledges), CANFAIL
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
sys.path.insert(0, HERE)

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
    print("\nbench_switch_errors self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def phase(name, hp, gs, acked, cycles, log):
    import can
    import bench_usb_host as U
    pc = U.Rx(U.open_pcan(False), "pcan")
    kv = None
    if acked:
        kv = can.Bus(interface="kvaser", channel=0, bitrate=H.BITRATE)
        kstop = threading.Event()

        def kdrain():
            while not kstop.is_set():
                kv.recv(timeout=0.05)
        kt = threading.Thread(target=kdrain, daemon=True)
        kt.start()
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
        name, "Kvaser open (acknowledging)" if acked else
        "Kvaser CLOSED (nothing acknowledges)"))
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
    alive = pc.t.is_alive()
    logging.getLogger("can.pcan").removeHandler(hnd)
    gstop.set()
    gt.join(1.0)
    if kv is not None:
        kstop.set()
        kt.join(1.0)
        kv.shutdown()
    pc.close()
    if ROWS_BASE:
        with open("%s_%s_pcan.txt" % (ROWS_BASE, name), "w",
                  encoding="utf-8") as f:
            for r in rows:
                f.write("%.6f %.6f %X %d %s\n" % (r[0], r[1], r[2],
                                                 1 if r[3] else 0,
                                                 r[4].hex()))
    smp = [x for x in samples(hp.stamped) if h0 - 5.5 <= x[0] <= h1]
    err_hosts = [r[1] for r in rows if r[3]]
    iv = intervals(smp, err_hosts)
    v, lines = judge(iv, acked)
    if not alive:
        v, lines = "fault", ["TEST FAULT: the PCAN's receive thread died"] + lines
    bridged = sum(1 for r in rows if r[2] == H.FILLER and not r[3])
    log("phase %s: %d cycles, %d charger frames sent, %d seen complete on the "
        "vehicle wire by the PCAN, %d PCAN bus-state warning(s) -> %s"
        % (name, cycles, n, bridged, warn[0], v.upper()))
    for ln in lines:
        log("  " + ln)
    return v


def run_bench(a):
    import bench_usb_host as U
    import bench_rate_feasibility as RF
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    log("run at %s; charger frames 0x%08X at %.0f fps, %.0f s on / %.0f s "
        "cycle; ACK %d cycles (Kvaser acknowledging, PCAN passive), NOACK %d "
        "(Kvaser closed)" % (datetime.datetime.now().isoformat(), H.FILLER,
                             RATE, ON_S, CYCLE_S, a.cycles, CANFAIL))
    global ROWS_BASE
    ROWS_BASE = os.path.splitext(a.out)[0]
    hp = U.HostPort(a.port, os.path.splitext(a.out)[0] + "_serial.txt")
    gs = RF.open_gs_usb()
    try:
        v1 = phase("ACK", hp, gs, True, a.cycles, log)
        v2 = phase("NOACK", hp, gs, False, CANFAIL, log)
    finally:
        gs.shutdown()
        hp.close()
    log("RESULT: ACK %s, NOACK (can-fail) %s" % (v1, v2))
    out.close()
    return 0 if v2 == "canfail-ok" and v1 != "fault" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--cycles", type=int, default=CYCLES)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.out:
        ap.error("--out is required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
