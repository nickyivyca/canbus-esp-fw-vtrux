"""Item 3 debug: the rx-order keep-alive (0x18FF5AC0, SA 0xC0) was counted by
the board (c2v +1) but never seen by the Kvaser. This sends keep-alives
from the Innomaker every 0.5 s for 6 s and records EVERYTHING the Kvaser
(normal mode) and the PCAN (listen-only) receive, and the board's serial
lines, so it shows whether the vehicle port went normal and what reached
the vehicle segment. The board's port is opened with DTR/RTS low (a close
then does not reset the X2). Recorded, not judged (bench_suite's
recorded_judge).

Since 2026-11-13 (tester; the charge-interposer reviewer's E and A): the
board's port and the PCAN's channel come from the command line (the bench
suite passes {serial_port} and {pcan_channel}; they were COM3 and
PCAN_USBBUS1 in the code), and the Kvaser is the kernel's kvaser_usb,
netdev kvaser: an ACK check, so its link is brought up and read back first
(kvaser_link.set_link), and a link that does not come up is one VOID line
with the kernel's USB/CAN lines, before any adapter opens. The Kvaser only
receives here (one frame every 0.5 s goes out, on the Innomaker), so no
stall rule and no reader process: nothing sends busily.

    python3.13 -B keepalive_probe.py --port /dev/... --pcan-channel PCAN_USBBUS1
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

KA = 0x18FF5AC0


def run(a, link=kvaser_link, opener=None, out=print):
    """-> rc. opener(port, pcan_channel) -> (ser, tx, kv, pc): the
    adapters (tests pass a stand-in that must not be reached)."""
    t_start = time.time()
    try:
        out(link.set_link(True))
    except link.LinkError as e:
        out(link.link_void(e, t_start, time.time()))
        return 1
    ser, tx, kv, pc = (opener or open_all)(a.port, a.pcan_channel)
    lines, got = [], []
    stop = threading.Event()

    def tap():
        buf = b""
        while not stop.is_set():
            buf += ser.read(512)
            while b"\n" in buf:
                ln, _, buf = buf.partition(b"\n")
                lines.append((time.perf_counter(), ln.decode("ascii", "replace").rstrip()))

    def drain(bus, name):
        while not stop.is_set():
            m = bus.recv(timeout=0.05)
            if m is not None:
                got.append((time.perf_counter(), name, m))
    import can
    ths = [threading.Thread(target=tap, daemon=True)] + [
        threading.Thread(target=drain, args=(b, n), daemon=True)
        for b, n in ((kv, "kvaser"), (pc, "pcan"), (tx, "innomaker"))]
    for t in ths:
        t.start()
    time.sleep(2.0)
    t0 = time.perf_counter()
    out("t0 %.6f; sending 0x%X every 0.5 s for 6 s" % (t0, KA))
    sent = 0
    while time.perf_counter() - t0 < 6.0:
        tx.send(can.Message(arbitration_id=KA, is_extended_id=True,
                            data=bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])), timeout=0.2)
        sent += 1
        time.sleep(0.5)
    time.sleep(3.0)
    stop.set()
    time.sleep(0.3)
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


def open_all(port, pcan_channel):
    import can
    import bench_charger_witness as bcw
    import serial_open
    ser = serial_open.open_quiet(port, 115200, 0.2)
    tx = bcw.open_gs_usb()
    kv = kvaser_link.open_bus(receive_own_messages=False)
    pc = can.Bus(interface="pcan", channel=pcan_channel, bitrate=500000,
                 state=can.BusState.PASSIVE)
    return ser, tx, kv, pc


def self_test():
    """No hardware: the link set before anything opens; a link that does
    not come up is a VOID line naming the kernel's lines, rc 1, nothing
    opened; the port and PCAN channel are required arguments."""
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

    def opener(port, ch):
        opened.append((port, ch))
        raise RuntimeError("stop here: the adapters would open")
    a = argparse.Namespace(port="/dev/x2", pcan_channel="PCAN_USBBUS1")
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
    check(Link.calls == [True] and opened == [("/dev/x2", "PCAN_USBBUS1")],
          "the link brought up (an ACK check) before the adapters open, on the port and "
          "channel given: %s %s" % (Link.calls, opened))
    for argv in ([], ["--port", "/dev/x2"], ["--pcan-channel", "PCAN_USBBUS1"]):
        try:
            parse(argv)
            check(False, "%s accepted without both --port and --pcan-channel" % argv)
        except SystemExit:
            check(True, "%s refused: --port and --pcan-channel are required" % argv)
    print("\nkeepalive_probe self-test: %s" % ("all checks OK" if not fails
                                              else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true")
    ap.add_argument("--port", required="--self-test" not in argv and "--can-fail" not in argv)
    ap.add_argument("--pcan-channel",
                    required="--self-test" not in argv and "--can-fail" not in argv)
    return ap.parse_args(argv)


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("the link never set", "        out(link.set_link(True))\n", "        pass\n"),
    ("a link failure not VOID",
     "        out(link.link_void(e, t_start, time.time()))\n        return 1",
     "        out(\"link: %s\" % e)\n        return 1"),
    ("a link failure goes on", "        out(link.link_void(e, t_start, time.time()))\n        return 1",
     "        out(link.link_void(e, t_start, time.time()))"),
    ("the port hard-coded", "    ser, tx, kv, pc = (opener or open_all)(a.port, a.pcan_channel)",
     "    ser, tx, kv, pc = (opener or open_all)(\"COM3\", \"PCAN_USBBUS1\")"),
    ("the port optional", "    ap.add_argument(\"--port\", required=\"--self-test\" not in argv",
     "    ap.add_argument(\"--port\", required=False and \"--self-test\" not in argv"),
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
