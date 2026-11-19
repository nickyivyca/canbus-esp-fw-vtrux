"""Step 4 item 5 (plan 4a): verify boot-mid-session's board reset on the bench
BEFORE any scored run uses it. The reset is run_scenario's own:
SerialTap.reset_board() (esptool HardReset over USB-Serial/JTAG, RTS 0.2 s).
Answers the plan's four questions: does the port re-enumerate (SerialTap
marks SERIAL LOST / BACK), how long is the serial gap, does the boot banner
survive (a boot line on serial after the reset), does 0x7F7's uptime drop
(>= 2 min before, 0 after).

Bench: the PCAN in NORMAL mode on CAN1 reads 0x7F4-0x7F7 and acknowledges
(so the power-up round after the reset completes); the Innomaker sends the
2.3 keep-alive 0x18FF5AC0 (extended, SA 0xC0, no DBC defines it) every
100 ms so the vehicle port is normal and the 1 Hz status frames flow.

Moved from reverse-it's runs/step4_nohands_20261014T1730/ into git with
the bench suite's scripts (port step 0, 2026-10-26). The diag DBC is
the git copy (paths.diag_dbc(); the SeaDrive copy it read before, schema
3, differs from it in the version line, comments and attributes, with
every signal's layout and scaling the same), and the serial log goes to
--serial-log, by default where it went before (that run folder).

Since 2026-11-18 (shared/bench SPEC section 8's adapter roles): the PCAN is
role pcanfd and the Innomaker role canchg, and (the charge-interposer
reviewer's follow-on to the adapter roles) the X2's port comes from the
command line, required, as the other drivers take it: the bench suite
passes {serial_port}, the settings' /dev/serial/by-id link. It was COM3
in the code.

    python3.13 -B reset_check.py --port /dev/... [--serial-log FILE] > reset_check.txt
    python3.13 -B reset_check.py --self-test | --can-fail
"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (bench_charger_witness) first, then charge-interposer/
# (paths, run_scenario): both found from this file.
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import paths                                                # noqa: E402

KA = 0x18FF5AC0


def default_serial_log():
    """Where the serial log went before the move: reverse-it's run folder."""
    return os.path.join(paths.fixtures(), "runs", "step4_nohands_20261014T1730",
                        "reset_check.serial.log")


def open_all(port, serial_log):
    """The X2's serial tap on its port, started; pcanfd (NORMAL mode) and
    canchg, each by its role (shared/bench SPEC section 8, 2026-11-18)."""
    import adapter_roles
    import bench_charger_witness as bcw
    import run_scenario
    tap = run_scenario.SerialTap(port, 115200, serial_log)
    tap.start()
    pc = adapter_roles.pcanfd(500000)
    tx = bcw.open_gs_usb()
    return tap, pc, tx


def run(a, out=print):
    """-> rc. The reset on the board at a.port, judged as before the move."""
    import can
    import cantools
    from run_scenario import BOOT_LINE
    db = cantools.database.load_file(paths.diag_dbc())
    stop = threading.Event()
    frames = []                       # (host t, id, data)
    tap, pc, tx = open_all(a.port, a.serial_log or default_serial_log())

    def keepalive():
        while not stop.is_set():
            try:
                tx.send(can.Message(arbitration_id=KA, is_extended_id=True,
                                    data=bytes([0xA5] + [0] * 7)), timeout=0.05)
            except Exception:                                  # noqa: BLE001
                pass
            m = tx.recv(timeout=0.0)
            while m is not None:
                m = tx.recv(timeout=0.0)
            time.sleep(0.1)

    def pcan_rx():
        while not stop.is_set():
            m = pc.recv(timeout=0.1)
            if m is not None and 0x7F4 <= m.arbitration_id <= 0x7F7:
                frames.append((time.perf_counter(), m.arbitration_id,
                               bytes(m.data)))

    ths = [threading.Thread(target=f, daemon=True) for f in (keepalive, pcan_rx)]
    for t in ths:
        t.start()

    def uptime():
        u = [db.decode_message(0x7F7, d)["intp_uptime_min"]
             for _h, i, d in frames if i == 0x7F7]
        return u[-1] if u else None

    t0 = time.perf_counter()
    out("waiting for 0x7F7 uptime >= 2 min (up to 200 s)")
    while time.perf_counter() - t0 < 200 and (uptime() or 0) < 2:
        time.sleep(1.0)
    pre_up = uptime()
    out("0x7F7 uptime before the reset: %s min (%.0f s waited)"
        % (pre_up, time.perf_counter() - t0))
    tap.reset_board()
    time.sleep(25.0)
    stop.set()
    for t in ths:
        t.join(timeout=2.0)
    tap.stop()
    pc.shutdown()
    tx.shutdown()

    rh = tap.reset_host_t
    out("reset sent at host %s" % ("%.6f" % rh if rh else "NEVER"))
    rows = []
    with open(tap.stamped_path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            h, _, text = ln.rstrip("\n").partition("\t")
            rows.append((float(h), text))
    after = [(h, x) for h, x in rows if rh and h >= rh]
    marks = [(h - rh, x) for h, x in after if x.startswith("# ")]
    boots = [(h - rh, x) for h, x in after if BOOT_LINE.search(x)]
    out("serial markers after the reset:")
    for dt, x in marks:
        out("  %+.3f s  %s" % (dt, x))
    out("boot lines after the reset: %d" % len(boots))
    for dt, x in boots[:4]:
        out("  %+.3f s  %s" % (dt, x[:100]))
    first_status = next(((h - rh, x) for h, x in after
                         if x.startswith("t=")), None)
    out("first status line after it: %s" % (first_status,))
    post = [(h - rh, db.decode_message(0x7F7, d)["intp_uptime_min"])
            for h, i, d in frames if i == 0x7F7 and rh and h > rh]
    out("0x7F7 after the reset (s, uptime min): %s" % post[:5])
    rnd = sorted((h - rh, i) for h, i, _d in frames if rh and h > rh)[:8]
    out("first status frames after the reset (s, id): %s"
        % ", ".join("%.3f:%X" % r for r in rnd))
    gap = [x for _dt, x in marks if x.startswith("# SERIAL BACK")]
    ok = (rh is not None and bool(boots) and pre_up is not None and pre_up >= 2
          and bool(post) and post[0][1] == 0 and not any(
              "NOT BACK" in x for _d, x in marks))
    out("RESET PROVEN: %s (boot line %s; uptime %s -> %s; serial %s)"
        % ("YES" if ok else "NO", "yes" if boots else "NO", pre_up,
           post[0][1] if post else "unseen",
           gap[0][2:] if gap else "never lost"))
    return 0


def self_test():
    """No hardware: the X2's serial tap opens on the port given (through
    run_scenario's SerialTap, with the serial log given) and is started
    before any adapter opens; --port is required, and nothing defaults to a
    port."""
    import adapter_roles
    import run_scenario
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    taps, events = [], []

    class Tap:
        def __init__(self, port, baud, log):
            taps.append((port, baud, log))

        def start(self):
            events.append("tap started")

    def pcanfd(*args, **kw):
        events.append("pcanfd")
        raise RuntimeError("stop here: the adapters would open")

    saved = (run_scenario.SerialTap, adapter_roles.pcanfd)
    run_scenario.SerialTap, adapter_roles.pcanfd = Tap, pcanfd
    try:
        try:
            run(parse(["--port", "/dev/x2", "--serial-log", "x2.serial.log"]),
                out=lambda s: None)
            check(False, "run went past the adapters' open")
        except RuntimeError:
            pass
    finally:
        run_scenario.SerialTap, adapter_roles.pcanfd = saved
    check(taps == [("/dev/x2", 115200, "x2.serial.log")],
          "the serial tap on the port given, with the serial log given: %s" % taps)
    check(events == ["tap started", "pcanfd"],
          "... started before the PCAN opens by its role: %s" % events)
    a = parse(["--port", "/dev/x2"])
    check(a.port == "/dev/x2" and a.serial_log is None,
          "--port alone is accepted, the serial log left to its default")
    check(parse(["--self-test"]).port is None, "no port by default")
    for argv in ([], ["--serial-log", "x.log"]):
        try:
            parse(argv)
            check(False, "%s accepted (--port is required)" % argv)
        except SystemExit:
            check(True, "%s refused: --port is required" % argv)
    print("\nreset_check self-test: %s" % ("all checks OK" if not fails
                                          else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true")
    ap.add_argument("--port", required="--self-test" not in argv and "--can-fail" not in argv,
                    help="the X2's USB Serial/JTAG port")
    ap.add_argument("--serial-log", default=None,
                    help="the board's serial log; default where it went before "
                         "the move, reverse-it's runs/step4_nohands_20261014T1730/")
    return ap.parse_args(argv)


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("the port hard-coded in run",
     "    tap, pc, tx = open_all(a.port, a.serial_log or default_serial_log())",
     "    tap, pc, tx = open_all(\"COM3\", a.serial_log or default_serial_log())"),
    ("the port hard-coded in the tap",
     "    tap = run_scenario.SerialTap(port, 115200, serial_log)",
     "    tap = run_scenario.SerialTap(\"COM3\", 115200, serial_log)"),
    ("the serial log given ignored",
     "    tap, pc, tx = open_all(a.port, a.serial_log or default_serial_log())",
     "    tap, pc, tx = open_all(a.port, \"reset_check.serial.log\")"),
    ("the tap never started", "    tap.start()\n    pc = ", "    pc = "),
    ("the port optional",
     "    ap.add_argument(\"--port\", required=\"--self-test\" not in argv",
     "    ap.add_argument(\"--port\", required=False and \"--self-test\" not in argv"),
    ("the port defaulting to COM3",
     "                    help=\"the X2's USB Serial/JTAG port\")",
     "                    default=\"COM3\", help=\"the X2's USB Serial/JTAG port\")"),
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
