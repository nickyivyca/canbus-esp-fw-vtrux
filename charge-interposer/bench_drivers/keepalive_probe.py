"""Item 3 debug: the rx-order keep-alive (0x18FF5AC0, SA 0xC0) was counted by
the board (c2v +1) but never seen by the Kvaser. This sends keep-alives
from the Innomaker every 0.5 s for 6 s and records EVERYTHING the Kvaser
(normal mode) and the PCAN (listen-only) receive, and the board's serial
lines, so it shows whether the vehicle port went normal and what reached
the vehicle segment. The board's port is opened with DTR/RTS low (a close
then does not reset the X2). Recorded, not judged (bench_suite's
recorded_judge).

Since 2026-11-13 (tester; the charge-interposer reviewer's E and A): the
board's port comes from the command line (the bench suite passes
{serial_port}; it was COM3 in the code), and the Kvaser is the kernel's
kvaser_usb, role kvaser: an ACK check, so its link is brought up and read
back first (kvaser_link.set_link), and a link that does not come up is one
VOID line with the kernel's USB/CAN lines, before any adapter opens. The
Kvaser only receives here (one frame every 0.5 s goes out, on the
Innomaker), so no stall rule and no reader process: nothing sends busily.

Since 2026-11-18 (shared/bench SPEC section 8's adapter roles): every
adapter by its role, the PCAN as pcanfd (adapter_roles.pcanfd), so the
--pcan-channel the bench suite passed from 2026-11-13 is gone and refused.

Since 2026-11-19 (the charge-interposer reviewer's PCAN ruling 1): each
adapter's reader runs under reader_death.guard, so a recv that raises
(python-can raises on most PCAN statuses) is kept with its text; the run
then prints a VOID line per dead reader instead of the counts, and exits 3,
which recorded_judge reads VOID.

Since 2026-11-20 (the reviewer's PCAN item 2): the PCAN opens with
bus_states="report" (adapter_roles.pcanfd_report), and each reader passes
what it reads through reader_death.screen: a state row is not counted, and
a row reporting frames lost stops that reader, VOID as above.

    python3.13 -B keepalive_probe.py --port /dev/...
    python3.13 -B keepalive_probe.py --self-test | --can-fail
"""
import argparse
import collections
import os
import sys
import threading
import time

# Moved from reverse-it's runs/step4_nohands_20261014T1730/ into git with
# the bench suite's scripts (port step 0, 2026-10-26): bench_drivers/
# (bench_charger_witness), found from this file.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import kvaser_link                                          # noqa: E402
import reader_death                                         # noqa: E402

KA = 0x18FF5AC0
# (settle, send for, after, send every) in seconds
TIMING = (2.0, 6.0, 3.0, 0.5)


def run(a, link=kvaser_link, opener=None, out=print):
    """-> rc. opener(port) -> (ser, tx, kv, pc): the adapters (tests pass a
    stand-in that must not be reached)."""
    t_start = time.time()
    try:
        out(link.set_link(True))
    except link.LinkError as e:
        out(link.link_void(e, t_start, time.time()))
        return 1
    ser, tx, kv, pc = (opener or open_all)(a.port)
    lines, got = [], []
    stop = threading.Event()

    def tap():
        buf = b""
        while not stop.is_set():
            buf += ser.read(512)
            while b"\n" in buf:
                ln, _, buf = buf.partition(b"\n")
                lines.append((time.perf_counter(), ln.decode("ascii", "replace").rstrip()))

    deaths = {n: reader_death.Death(n) for n in ("kvaser", "pcan", "innomaker")}

    def drain(bus, name):
        def step():
            # a state row is not counted; a row reporting frames lost stops
            # the reader and VOIDs the record (the reviewer's PCAN item 2)
            m = reader_death.screen(bus, bus.recv(timeout=0.05), deaths[name])
            if m is not None:
                got.append((time.perf_counter(), name, m))
        # a raise ends the thread with the error kept (reader_death)
        reader_death.guard(deaths[name], stop, step)
    import can
    ths = [threading.Thread(target=tap, daemon=True)] + [
        threading.Thread(target=drain, args=(b, n), daemon=True)
        for b, n in ((kv, "kvaser"), (pc, "pcan"), (tx, "innomaker"))]
    for t in ths:
        t.start()
    settle_s, send_s, after_s, every_s = TIMING
    time.sleep(settle_s)
    t0 = time.perf_counter()
    out("t0 %.6f; sending 0x%X every %.1f s for %.0f s" % (t0, KA, every_s, send_s))
    sent = 0
    while time.perf_counter() - t0 < send_s:
        tx.send(can.Message(arbitration_id=KA, is_extended_id=True,
                            data=bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])), timeout=0.2)
        sent += 1
        time.sleep(every_s)
    time.sleep(after_s)
    stop.set()
    time.sleep(0.3)
    dead = [deaths[n] for n in ("kvaser", "pcan", "innomaker") if deaths[n].error]
    if dead:
        # a reader that died: the record is VOID with its error, not printed
        # as what reached each segment (the charge-interposer reviewer's PCAN
        # ruling 1, 2026-11-19)
        for d in dead:
            out(d.void_line())
        for b in (tx, kv, pc):
            b.shutdown()
        ser.close()
        return 3
    out("sent %d" % sent)
    cnt = collections.Counter()
    for h, name, m in got:
        cnt[(name, "%X" % m.arbitration_id, m.is_extended_id, getattr(m, "is_rx", None))] += 1
    for k, v in sorted(cnt.items()):
        out("  %-9s id %-9s ext %-5s is_rx %-5s x%d" % (k + (v,)))
    out("first frames per adapter (host t - t0):")
    seen = set()
    for h, name, m in got:
        if name not in seen:
            seen.add(name)
            out("  %s %.3f %X %s" % (name, h - t0, m.arbitration_id, m.data.hex()))
    out("serial lines from t0-1 s:")
    for h, ln in lines:
        if h >= t0 - 1.0 and ("vehicle port" in ln or "MODE" in ln or "LOSS" in ln
                              or ln.startswith("t=") or ln.startswith("[")):
            out("  %+.3f %s" % (h - t0, ln[:200]))
    for b in (tx, kv, pc):
        b.shutdown()
    ser.close()
    return 0


def open_all(port):
    """The board's port; canchg, kvaser and pcanfd, each by its role."""
    import can
    import adapter_roles
    import bench_charger_witness as bcw
    import serial_open
    ser = serial_open.open_quiet(port, 115200, 0.2)
    tx = bcw.open_gs_usb()
    kv = kvaser_link.open_bus(receive_own_messages=False)
    pc = adapter_roles.pcanfd_report(500000, state=can.BusState.PASSIVE)
    return ser, tx, kv, pc


def self_test():
    """No hardware: the link set before anything opens; a link that does
    not come up is a VOID line naming the kernel's lines, rc 1, nothing
    opened; the port is a required argument, and a PCAN channel is refused
    (the PCAN is opened by its role)."""
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    class Link:
        LinkError = kvaser_link.LinkError
        calls = []
        fail = None

        @classmethod
        def set_link(cls, up):
            cls.calls.append(up)
            if cls.fail:
                raise kvaser_link.LinkError(cls.fail)
            return "kvaser link up"

        @staticmethod
        def link_void(e, t0, t1):
            return kvaser_link.link_void(e, t0, t1, lambda s, u: (
                "2026-11-13T12:00:00.1-08:00 h kernel: usb 1-3.2.1: error -71\n"))

    opened = []

    def opener(port):
        opened.append(port)
        raise RuntimeError("stop here: the adapters would open")
    a = argparse.Namespace(port="/dev/x2")
    Link.fail = "the kvaser link reads down in sysfs, and this check needs it up"
    outl = []
    rc = run(a, Link, opener, outl.append)
    check(rc == 1 and Link.calls == [True] and not opened,
          "a link that does not come up: rc 1, nothing opened (%s, %s)" % (rc, opened))
    check(len(outl) == 1 and outl[0].startswith("VOID: kvaser link: the kvaser link reads "
                                                "down") and "error -71" in outl[0],
          "... one VOID line with the kernel's lines: %s" % outl[-1:])
    Link.fail, Link.calls = None, []
    try:
        run(a, Link, opener, outl.append)
    except RuntimeError:
        pass
    check(Link.calls == [True] and opened == ["/dev/x2"],
          "the link brought up (an ACK check) before the adapters open, on the port "
          "given: %s %s" % (Link.calls, opened))
    check(parse(["--port", "/dev/x2"]).port == "/dev/x2", "--port alone is accepted")
    for argv, why in (([], "--port is required"),
                      (["--pcan-channel", "PCAN_USBBUS1"], "--port is required"),
                      (["--port", "/dev/x2", "--pcan-channel", "PCAN_USBBUS1"],
                       "a PCAN channel is refused: the PCAN is opened by its role")):
        try:
            parse(argv)
            check(False, "%s accepted (%s)" % (argv, why))
        except SystemExit:
            check(True, "%s refused: %s" % (argv, why))

    # 2026-11-19, the charge-interposer reviewer's PCAN ruling 1: run() with
    # stand-in adapters, the timings short; the PCAN raises a PCAN status
    # mid-window, or lives
    import types

    class Ser(object):
        def read(self, n):
            time.sleep(0.01)
            return b""

        def close(self):
            pass

    class Quiet(reader_death.RaisingBus):
        def recv(self, timeout=None):
            if self.frames:
                return self.frames.pop(0)
            time.sleep(0.005)

    def frame():
        return types.SimpleNamespace(arbitration_id=0x7F4, is_extended_id=False,
                                     is_rx=True, data=bytes(8))
    global TIMING
    saved_timing = TIMING
    try:
        TIMING = (0.05, 0.1, 0.05, 0.05)
        for dies in (True, False):
            pcan = (reader_death.RaisingBus([frame()], RuntimeError(
                "A PCAN status 0x40000 BUSPASSIVE"), delay=0.03) if dies
                    else Quiet([frame()]))
            outl = []
            rc = run(a, Link, lambda port, _p=pcan: (Ser(), Quiet([]), Quiet([]), _p),
                     outl.append)
            voids = [x for x in outl if x.startswith("VOID")]
            if dies:
                check(rc == 3 and len(voids) == 1 and "the pcan reader died" in voids[0]
                      and "BUSPASSIVE" in voids[0]
                      and not any(x.startswith("sent ") for x in outl),
                      "CAN-FAIL: a PCAN reader that raises mid-window: rc 3, one VOID "
                      "line with the error, no counts: rc %s %s" % (rc, voids))
            else:
                check(rc == 0 and not voids and any(x.startswith("sent ") for x in outl),
                      "the same with the PCAN reader alive: rc 0, the counts printed")
        # the PCAN item 2: bus states reported. A state row is not counted;
        # an overrun row VOIDs the record as lost frames
        R = reader_death.report_rows()
        for lost in (True, False):
            pcan = reader_death.report_bus(
                [R["frame"], R["passive"]] + ([R["overrun"]] if lost else []), delay=0.03)
            outl = []
            rc = run(a, Link, lambda port, _p=pcan: (Ser(), Quiet([]), Quiet([]), _p),
                     outl.append)
            voids = [x for x in outl if x.startswith("VOID")]
            if lost:
                check(rc == 3 and len(voids) == 1 and "the pcan reader lost frames" in voids[0]
                      and not any(x.startswith("sent ") for x in outl),
                      "CAN-FAIL: an overrun row: rc 3, one VOID line saying frames were "
                      "lost, no counts: rc %s %s" % (rc, voids))
            else:
                rows = [x for x in outl if x.strip().startswith("pcan ") and " x" in x]
                check(rc == 0 and not voids and len(rows) == 1 and "7F4" in rows[0],
                      "a state row is not counted: the PCAN's only count is its frame: %s"
                      % rows)
    finally:
        TIMING = saved_timing
    print("\nkeepalive_probe self-test: %s" % ("all checks OK" if not fails
                                              else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true")
    ap.add_argument("--port", required="--self-test" not in argv and "--can-fail" not in argv)
    return ap.parse_args(argv)


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("the link never set", "        out(link.set_link(True))\n", "        pass\n"),
    ("a link failure not VOID",
     "        out(link.link_void(e, t_start, time.time()))\n        return 1",
     "        out(\"link: %s\" % e)\n        return 1"),
    ("a link failure goes on", "        out(link.link_void(e, t_start, time.time()))\n        return 1",
     "        out(link.link_void(e, t_start, time.time()))"),
    ("the port hard-coded", "    ser, tx, kv, pc = (opener or open_all)(a.port)",
     "    ser, tx, kv, pc = (opener or open_all)(\"COM3\")"),
    ("a PCAN channel accepted again",
     "    return ap.parse_args(argv)",
     "    ap.add_argument(\"--pcan-channel\")\n    return ap.parse_args(argv)"),
    ("the port optional", "    ap.add_argument(\"--port\", required=\"--self-test\" not in argv",
     "    ap.add_argument(\"--port\", required=False and \"--self-test\" not in argv"),
    # 2026-11-19, the charge-interposer reviewer's PCAN ruling 1
    ("reader death: the readers unguarded",
     "        reader_death.guard(deaths[name], stop, step)",
     "        while not stop.is_set():\n            step()"),
    ("reader death: a dead reader's counts printed",
     "    if dead:\n        # a reader", "    if False:\n        # a reader"),
    ("reader death: a dead reader exits 0",
     "        ser.close()\n        return 3", "        ser.close()\n        return 0"),
    # 2026-11-20, the reviewer's PCAN item 2
    ("PCAN item 2: the readers unscreened",
     "            m = reader_death.screen(bus, bus.recv(timeout=0.05), deaths[name])",
     "            m = bus.recv(timeout=0.05)"),
]


def main(argv):
    a = parse(argv)
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    return run(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
