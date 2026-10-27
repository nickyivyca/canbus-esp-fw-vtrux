"""Spec 2 / 2.3 diagnostic: does a vehicle-bound frame still pending in the
vehicle port's transmit queue vanish, uncounted, when the port switches
normal -> listen-only? (reviewer, 2026-10-15, a hypothesis from the
implementor's account of the switch: a full TWAI teardown with nothing
draining the TX queue first.)

Two phases, each: the Innomaker sends charger frames (extended, SA 0xC0,
`bench_hold12.FILLER`, DLC 8, a 32-bit counter) at RATE fps for ON_S, so the
port goes normal and the board bridges them toward the vehicle segment; then
the charger stops, and 2 s later the port falls back to listen-only.
  NOACK   nothing on the vehicle segment acknowledges: the Kvaser is NOT
          opened (it cannot be silent), the PCAN is opened PASSIVE as the
          witness, the WiCAN (mode 0) does not acknowledge. The board's
          bridged frames sit retrying, so some are pending at the switch.
  ACK     the control: the same with the Kvaser open in normal mode, so
          every bridged frame is acknowledged and nothing is pending.
The board's serial status block (every 5 s) gives, before and after each
phase: c2v (frames forwarded toward the vehicle), the LOSS line's vehicle
tx drops and bridge drops, and the MODE line's suppressed count, switch
count and drained count. Every field on the LOSS line is a loss, and
`veh tx=` is the vehicle port's txDropped (firmware/README.md); `bridge=`
counts the subset refused at send, so it is reported, not subtracted.
Accounting (account(), pure; --self-test):
    unaccounted = c2v delta - delivered - veh tx delta - suppressed delta
                  - drained delta
where delivered is the bridged frames the PCAN saw complete. (The first
version also subtracted bridge= and double-counted; corrected 2026-10-15.) A positive
unaccounted count in NOACK with 0 in ACK supports the hypothesis; 0 in both
refutes it.

    py -3.14 bench_switch_pending.py --self-test
    py -3.14 bench_switch_pending.py --port COM3 --out <file.txt>

Tester, 2026-10-15. Bench image, no hands.
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

import bench_hold12 as H                                     # noqa: E402

RATE = 20.0
ON_S = 6.0
FALLBACK_WAIT_S = 4.0       # > 2 s: the port is listen-only by then
STATUS_WAIT_S = 6.0         # one 5 s status block, with margin

C2V_RE = re.compile(r"\bc2v=(\d+)")
VEH_RE = re.compile(r"veh\(rx=(\d+) tx=(\d+) err=(\d+) off=(\d+) peak=(\d+)\)")
BRIDGE_RE = re.compile(r"\bbridge=(\d+)")
MODE_RE = re.compile(r"switches=(\d+) refused=(\d+) suppressed=(\d+)")
DRAIN_RE = re.compile(r"drained=(\d+)")


def counters(lines):
    """The last status block's counters from serial (host, line) rows."""
    out = {}
    for _h, ln in lines:
        m = C2V_RE.search(ln)
        if m and "state=" in ln:
            out["c2v"] = int(m.group(1))
        m = VEH_RE.search(ln)
        if m:
            out["veh_tx"] = int(m.group(2))
            out["veh_err"] = int(m.group(3))
        m = BRIDGE_RE.search(ln)
        if m and "LOSS" in ln:
            out["bridge"] = int(m.group(1))
        m = MODE_RE.search(ln)
        if m:
            out["switches"] = int(m.group(1))
            out["suppressed"] = int(m.group(3))
        m = DRAIN_RE.search(ln)
        if m:
            out["drained"] = int(m.group(1))
    return out


def account(before, after, delivered):
    """-> (unaccounted, deltas dict)."""
    keys = ("c2v", "veh_tx", "bridge", "suppressed", "drained", "switches",
            "veh_err")
    d = {k: after.get(k, 0) - before.get(k, 0) for k in keys}
    un = d["c2v"] - delivered - d["veh_tx"] - d["suppressed"] - d["drained"]
    return un, d


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)
    b = dict(c2v=100, veh_tx=0, bridge=0, suppressed=0, drained=0,
             switches=4, veh_err=0)
    a = dict(c2v=220, veh_tx=0, bridge=0, suppressed=0, drained=0,
             switches=6, veh_err=0)
    un, d = account(b, a, delivered=120)
    check(un == 0, "every forwarded frame delivered -> 0 unaccounted")
    a2 = dict(a, veh_tx=110, bridge=80)
    un, d = account(b, a2, delivered=7)
    check(un == 3, "can-fail: 120 forwarded, 7 delivered, 110 tx drops (80 of "
          "them refused at send) -> 3 unaccounted (vanished) -> %d" % un)
    a3 = dict(a, veh_tx=110, bridge=80, drained=3)
    un, d = account(b, a3, delivered=7)
    check(un == 0, "the same 3 counted as drained -> 0 unaccounted")
    un, d = account(b, dict(a, veh_tx=113, bridge=113), delivered=7)
    check(un == 0, "bridge= is a subset of tx=, never subtracted twice")
    lines = [(1.0, "t=5s state=MONITOR | v2c=10 c2v=42 mod=0"),
             (1.0, "  MODE veh=normal core wants normal | switches=2 refused=0 "
                   "suppressed=5 | round=4/4 in 800us | boot=1us "
                   "switch=1/1us(last/max) drained=1"),
             (1.0, "  LOSS veh(rx=0 tx=9 err=3 off=0 peak=1) chg(rx=0 tx=0 err=0 "
                   "off=0 ring=0/0) bridge=4 emit=0")]
    c = counters(lines)
    check(c == dict(c2v=42, switches=2, suppressed=5, drained=1, veh_tx=9,
                    veh_err=3, bridge=4), "the status block parses -> %s" % c)
    print("\nbench_switch_pending self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def phase(name, hp, ack, log, gs):
    import can
    import bench_usb_host as U
    pc = U.Rx(U.open_pcan(False), "pcan")           # passive witness
    kv = None
    if ack:
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
    log("phase %s: waiting for a status block (baseline)" % name)
    time.sleep(STATUS_WAIT_S)
    before = counters(hp.stamped)
    pc.take()
    msg = can.Message(arbitration_id=H.FILLER, is_extended_id=True,
                      data=bytearray(8))
    t0 = hp.mark("%s: charger frames start" % name)
    n = 0
    end = time.perf_counter() + ON_S
    while time.perf_counter() < end:
        msg.data = bytearray(((n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255,
                              n & 255, 0, 0, 0, 0))
        try:
            gs.send(msg, timeout=0.05)
            n += 1
        except Exception:                              # noqa: BLE001
            pass
        time.sleep(1.0 / RATE)
    hp.mark("%s: charger stopped after %d frames" % (name, n))
    time.sleep(FALLBACK_WAIT_S + STATUS_WAIT_S)
    after = counters(hp.stamped)
    rows = pc.take()
    gstop.set()
    gt.join(1.0)
    if kv is not None:
        kstop.set()
        kt.join(1.0)
        kv.shutdown()
    pc.close()
    seen = sorted(set(H.seq_of(r[4].hex()[:6]) if False else
                      ((r[4][0] << 24) | (r[4][1] << 16) | (r[4][2] << 8) | r[4][3])
                      for r in rows if r[2] == H.FILLER and not r[3]
                      and len(r[4]) >= 4))
    errs = sum(1 for r in rows if r[3])
    un, d = account(before, after, len(seen))
    log("phase %s: %d charger frames sent; PCAN (passive) saw %d of them "
        "complete on the vehicle wire and %d error frames" % (name, n,
                                                              len(seen), errs))
    log("  board counters before %s" % before)
    log("  board counters after  %s" % after)
    log("  deltas %s" % d)
    log("  UNACCOUNTED (c2v - delivered - veh tx - suppressed - drained; "
        "bridge= is a subset of tx=): %d" % un)
    return un, d


def run_bench(a):
    import bench_usb_host as U
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    log("run at %s; charger frames 0x%08X at %.0f fps for %.0f s, then the "
        "2 s fallback; NOACK (Kvaser closed, PCAN passive) then ACK (Kvaser "
        "open)" % (datetime.datetime.now().isoformat(), H.FILLER, RATE, ON_S))
    import bench_rate_feasibility as RF
    hp = U.HostPort(a.port, os.path.splitext(a.out)[0] + "_serial.txt")
    # one Innomaker handle for both phases: an in-process gs_usb reopen
    # fails on this host (the first run's ACK phase died on it)
    gs = RF.open_gs_usb()
    try:
        un1, _d1 = phase("NOACK", hp, False, log, gs)
        un2, _d2 = phase("ACK", hp, True, log, gs)
    finally:
        gs.shutdown()
        hp.close()
    log("RESULT: unaccounted NOACK %d, ACK %d" % (un1, un2))
    out.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.out:
        ap.error("--out is required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
