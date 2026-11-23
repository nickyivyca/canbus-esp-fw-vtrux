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

Since 2026-11-19 (a harness defect, the charge-interposer reviewer): it
exits 1 when it prints RESET PROVEN: NO, with a FAIL line, so the bench
suite's reset-check FAILs; it exited 0 either way before, so the check
could not fail. The judging is judge(), with no hardware, so the
self-test covers YES and each way to NO.

Since 2026-11-19 (the charge-interposer reviewer's PCAN ruling 1): the PCAN
reader runs under reader_death.guard, so a recv that raises (python-can
raises on most PCAN statuses) is kept with its text; the uptime wait stops
there, and the verdict is RESET PROVEN: NOT-PROVEN with that reason and a
VOID line, exit 3 (VOID), never an unexplained NO judged on the frames read
before. Since 2026-11-23 (the reviewer's reader_guard ruling) that reader
is can-tools' canbench.reader_guard.read_loop (the guard and screen that
were reader_death's), with drain=False: it stops at the first read after
stop, as before. The VOID line is still reader_death's; a missed-frames
row's text reads "MissedFrames: ..." (it read "LostFrames: ..."). From the
same day (the reviewer's sweep ruling of 2026-11-21 on premise failures)
the board's port or an adapter that does not open is one VOID line, exit 3
(NotOpened); before, the open's exception ended the run with a traceback,
read FAIL.

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
import reader_death                                         # noqa: E402

KA = 0x18FF5AC0
UPTIME_WAIT_S = 200.0     # for 0x7F7's uptime to reach 2 min
WATCH_S = 25.0            # after the reset


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
    # bus states reported (the reviewer's PCAN item 2, 2026-11-20): collect's
    # reader screens what it reads (canbench.reader_guard.screen)
    pc = adapter_roles.pcanfd_report(500000)
    tx = bcw.open_gs_usb()
    return tap, pc, tx


def run(a, out=print):
    """-> rc: 0 when the reset is proven (RESET PROVEN: YES), 1 when not, so
    the bench suite's script_judge reads it FAIL (it exited 0 either way
    until 2026-11-19), 3 when not judged (NOT-PROVEN: the PCAN reader
    died; or the board's port or an adapter did not open, NotOpened), which
    script_judge reads VOID by its VOID line."""
    try:
        got = collect(a, out)
    except NotOpened as e:
        # a failed premise prints a VOID line, never a bare traceback (the
        # reviewer's sweep ruling, 2026-11-21; 2026-11-23)
        out("VOID: %s; nothing was judged" % e)
        return 3
    ok, lines = judge(*got)
    for ln in lines:
        out(ln)
    return {True: 0, False: 1}.get(ok, 3)


def judge(rh, rows, frames, pre_up, uptime_of, reader_void=None):
    """-> (ok, lines). No hardware: rh the reset's host time (None if it was
    never sent), rows the serial log's (host t, text), frames the PCAN's
    (host t, id, data) for 0x7F4-0x7F7, pre_up 0x7F7's uptime before the
    reset, uptime_of(data) 0x7F7's uptime in minutes. Proven: the reset
    sent, a boot line after it, uptime >= 2 min before it and 0 in the
    first 0x7F7 after it, and the serial port not left NOT BACK.
    reader_void, the PCAN reader's VOID line when it died (reader_death):
    then ok is None, RESET PROVEN: NOT-PROVEN with the reason, never judged
    on the frames it read before (the charge-interposer reviewer's PCAN
    ruling 1, 2026-11-19)."""
    from run_scenario import BOOT_LINE
    lines = []
    out = lines.append
    out("reset sent at host %s" % ("%.6f" % rh if rh else "NEVER"))
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
    post = [(h - rh, uptime_of(d))
            for h, i, d in frames if i == 0x7F7 and rh and h > rh]
    out("0x7F7 after the reset (s, uptime min): %s" % post[:5])
    rnd = sorted((h - rh, i) for h, i, _d in frames if rh and h > rh)[:8]
    out("first status frames after the reset (s, id): %s"
        % ", ".join("%.3f:%X" % r for r in rnd))
    gap = [x for _dt, x in marks if x.startswith("# SERIAL BACK")]
    if reader_void:
        out("RESET PROVEN: NOT-PROVEN (the PCAN reader died or lost frames, so "
            "its 0x7F7 frames are not whole: not judged)")
        out(reader_void)
        return None, lines
    ok = (rh is not None and bool(boots) and pre_up is not None and pre_up >= 2
          and bool(post) and post[0][1] == 0 and not any(
              "NOT BACK" in x for _d, x in marks))
    out("RESET PROVEN: %s (boot line %s; uptime %s -> %s; serial %s)"
        % ("YES" if ok else "NO", "yes" if boots else "NO", pre_up,
           post[0][1] if post else "unseen",
           gap[0][2:] if gap else "never lost"))
    if not ok:
        out("FAIL  the reset not proven (RESET PROVEN: NO)")
    return ok, lines


class NotOpened(Exception):
    """collect(): the board's serial tap or an adapter did not open (a
    failed premise: run() prints a VOID line and exits 3)."""


def collect(a, out=print):
    """-> judge's arguments, from the bench: the board at a.port held up
    until 0x7F7's uptime reaches 2 min (up to 200 s), reset through
    run_scenario's SerialTap, then watched for 25 s."""
    import can
    import cantools
    db = cantools.database.load_file(paths.diag_dbc())
    stop = threading.Event()
    frames = []                       # (host t, id, data)
    try:
        tap, pc, tx = open_all(a.port, a.serial_log or default_serial_log())
    except Exception as e:                                 # noqa: BLE001
        raise NotOpened("the board's port %s or an adapter did not open (%s: %s)"
                        % (a.port, type(e).__name__, e))

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

    death = reader_death.Death("pcan")
    RG = reader_death.rg()

    def pcan_row(m):
        if 0x7F4 <= m.arbitration_id <= 0x7F7:
            frames.append((time.perf_counter(), m.arbitration_id,
                           bytes(m.data)))

    def pcan_rx():
        # canbench.reader_guard's loop (the reviewer's reader_guard ruling,
        # 2026-11-23): a state row is dropped; a raise, or a row reporting
        # frames lost (the reviewer's PCAN item 2), ends the thread with
        # the error kept, death.lost for the latter; no drain after stop
        RG.read_loop(pc, death, stop, pcan_row, read_s=0.1, drain=False)

    ths = [threading.Thread(target=f, daemon=True) for f in (keepalive, pcan_rx)]
    for t in ths:
        t.start()

    def uptime():
        u = [db.decode_message(0x7F7, d)["intp_uptime_min"]
             for _h, i, d in frames if i == 0x7F7]
        return u[-1] if u else None

    t0 = time.perf_counter()
    out("waiting for 0x7F7 uptime >= 2 min (up to %.0f s)" % UPTIME_WAIT_S)
    while (time.perf_counter() - t0 < UPTIME_WAIT_S and (uptime() or 0) < 2
           and death.error is None):
        time.sleep(1.0)
    pre_up = uptime()
    out("0x7F7 uptime before the reset: %s min (%.0f s waited)"
        % (pre_up, time.perf_counter() - t0))
    tap.reset_board()
    time.sleep(WATCH_S)
    stop.set()
    for t in ths:
        t.join(timeout=2.0)
    tap.stop()
    pc.shutdown()
    tx.shutdown()

    rows = []
    with open(tap.stamped_path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            h, _, text = ln.rstrip("\n").partition("\t")
            rows.append((float(h), text))
    return (tap.reset_host_t, rows, frames, pre_up,
            lambda d: db.decode_message(0x7F7, d)["intp_uptime_min"],
            death.void_line() if death.error else None)


def self_test():
    """No hardware: the X2's serial tap opens on the port given (through
    run_scenario's SerialTap, with the serial log given) and is started
    before any adapter opens; --port is required, and nothing defaults to a
    port; judge() proves a good reset and refuses each way it can fail, and
    run() exits 0 on YES and 1 on NO."""
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

    saved = (run_scenario.SerialTap, adapter_roles.pcanfd_report)
    run_scenario.SerialTap, adapter_roles.pcanfd_report = Tap, pcanfd
    seen = []
    try:
        rc = run(parse(["--port", "/dev/x2", "--serial-log", "x2.serial.log"]),
                 out=seen.append)
    finally:
        run_scenario.SerialTap, adapter_roles.pcanfd_report = saved
    # 2026-11-23, the reviewer's sweep ruling: a premise failure is a VOID line
    check(rc == 3 and len(seen) == 1 and seen[0].startswith(
        "VOID: the board's port /dev/x2 or an adapter did not open (RuntimeError: "
        "stop here") and seen[0].endswith("nothing was judged"),
          "CAN-FAIL: an adapter that does not open -> one VOID line, exit 3, "
          "nothing judged: %s %s" % (rc, seen))
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

    # the verdict (2026-11-19): judge() on stand-in rows and frames, then
    # run()'s exit code with collect() standing in for the bench. 0x7F7's
    # stand-in data is its uptime in its first byte.
    rows = [(90.0, "t=118s state=NORMAL"),
            (100.1, "# SERIAL LOST: device reports readiness to read"),
            (101.3, "# SERIAL BACK gap=1.200 s"),
            (101.4, "ESP-ROM:esp32s3-20210327"),
            (101.9, "Vtrux charge interposer bridge"),
            (103.0, "t=1s state=NORMAL")]
    frames = [(95.0, 0x7F7, bytes([2])), (96.0, 0x7F4, bytes(8)),
              (102.5, 0x7F4, bytes(8)), (103.0, 0x7F7, bytes([0])),
              (104.0, 0x7F7, bytes([0]))]
    good = (100.0, rows, frames, 2, lambda d: d[0])

    def with_(**kw):
        g = dict(zip(("rh", "rows", "frames", "pre_up", "uptime_of"), good))
        g.update(kw)
        return (g["rh"], g["rows"], g["frames"], g["pre_up"], g["uptime_of"])

    ok, lines = judge(*good)
    proven = [ln for ln in lines if ln.startswith("RESET PROVEN")]
    check(ok and proven and proven[0].startswith("RESET PROVEN: YES")
          and not any(ln.startswith("FAIL") for ln in lines),
          "a reset with a boot line, uptime 2 -> 0 and the port back is "
          "proven: %s" % proven)
    noes = [
        ("the reset never sent", with_(rh=None)),
        ("no boot line after the reset",
         with_(rows=[r for r in rows if r[0] < 101.35 or r[0] > 102])),
        ("a boot line only before the reset",
         with_(rows=[(80.0, "Vtrux charge interposer bridge")]
               + [r for r in rows if r[0] < 101.35 or r[0] > 102])),
        ("uptime unseen before the reset", with_(pre_up=None)),
        ("uptime 1 min before the reset", with_(pre_up=1)),
        ("no 0x7F7 after the reset",
         with_(frames=[f for f in frames if f[1] != 0x7F7 or f[0] < 100])),
        ("0x7F7's first uptime after the reset 3 min, not 0",
         with_(frames=[(95.0, 0x7F7, bytes([2])), (103.0, 0x7F7, bytes([3])),
                       (104.0, 0x7F7, bytes([0]))])),
        ("uptime 0 only before the reset",
         with_(frames=[(90.0, 0x7F7, bytes([0])), (95.0, 0x7F7, bytes([2])),
                       (103.0, 0x7F7, bytes([5]))])),
        ("the port NOT BACK",
         with_(rows=rows[:2] + [(130.1, "# SERIAL NOT BACK within 30 s")]
               + rows[3:])),
    ]
    for what, args in noes:
        ok, lines = judge(*args)
        proven = [ln for ln in lines if ln.startswith("RESET PROVEN")]
        check(not ok and proven and proven[0].startswith("RESET PROVEN: NO")
              and any(ln.startswith("FAIL") for ln in lines),
              "%s: not proven, with a FAIL line: %s" % (what, proven))
    saved_collect = collect
    try:
        for want, args in ((0, good), (1, with_(pre_up=1)), (1, with_(rh=None))):
            seen = []
            globals()["collect"] = lambda _a, _out=print, _r=args: _r
            rc = run(parse(["--port", "/dev/x2"]), out=seen.append)
            check(rc == want and seen == judge(*args)[1],
                  "run() exits %d (want %d) and prints judge's lines"
                  % (rc, want))
    finally:
        globals()["collect"] = saved_collect

    # 2026-11-19, the charge-interposer reviewer's PCAN ruling 1: a PCAN
    # reader that dies is NOT-PROVEN with the reason, never an unexplained NO
    void = "VOID: the pcan reader died at host 1.000 (RuntimeError: A PCAN status 0x10 BUSOFF)"
    ok, lines = judge(*(good + (void,)))
    proven = [ln for ln in lines if ln.startswith("RESET PROVEN")]
    check(ok is None and proven and proven[0].startswith("RESET PROVEN: NOT-PROVEN")
          and void in lines and not any(ln.startswith("FAIL") for ln in lines),
          "a dead PCAN reader: NOT-PROVEN with its VOID line, no FAIL line, "
          "even on frames that would prove it: %s" % proven)
    try:
        globals()["collect"] = lambda _a, _out=print: good + (void,)
        seen = []
        rc = run(parse(["--port", "/dev/x2"]), out=seen.append)
        check(rc == 3 and void in seen, "run() exits 3 (VOID) on it: %s" % rc)
    finally:
        globals()["collect"] = saved_collect
    for ok, what in collect_checks():
        check(ok, what)
    # 2026-11-23, the reviewer's reader_guard ruling: drain=False, explicit
    reader_death.drain_check(__file__, check)
    print("\nreset_check self-test: %s" % ("all checks OK" if not fails
                                          else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def collect_checks():
    """-> [(ok, what)]: collect() with stand-in adapters and short waits.
    The PCAN gives one 0x7F7, then raises a PCAN status (collect returns
    its VOID line, and the uptime wait stops at it) or goes quiet (no VOID
    line: the short frame list is judged, a NO)."""
    import types
    real = time
    # is_error_frame: canbench.reader_guard.screen reads it directly
    seven = types.SimpleNamespace(arbitration_id=0x7F7, is_error_frame=False, data=bytes(8))

    class Tap(object):
        def __init__(self, path):
            # always in the scratch folder, whatever path a mutant passes
            path = os.path.join(d, "stamped.log")
            self.stamped_path, self.reset_host_t = path, None
            open(path, "w").close()

        def reset_board(self):
            self.reset_host_t = real.perf_counter()

        def stop(self):
            pass

    class Tx(object):
        def send(self, msg, timeout=None):
            pass

        def recv(self, timeout=None):
            return None

        def shutdown(self):
            pass

    class QuietBus(reader_death.RaisingBus):
        def recv(self, timeout=None):
            if self.frames:
                return self.frames.pop(0)
            real.sleep(0.01)

    import scratch_dir
    g = globals()
    saved = (g["open_all"], g["time"], g["UPTIME_WAIT_S"], g["WATCH_S"])
    d = scratch_dir.make("reset-check-collect")
    out = []
    try:
        g.update(time=types.SimpleNamespace(sleep=lambda s: real.sleep(0.02),
                                            perf_counter=real.perf_counter),
                 UPTIME_WAIT_S=5.0, WATCH_S=0.05)
        a = parse(["--port", "/dev/x2", "--serial-log", os.path.join(d, "s.log")])
        R = reader_death.report_rows()
        for bus, dies in ((reader_death.RaisingBus(
                [seven], RuntimeError("A PCAN status 0x10 BUSOFF")), True),
                          (QuietBus([seven]), False),
                          # the PCAN item 2: bus states reported, an overrun row
                          (reader_death.report_bus([seven, R["passive"], R["overrun"]]),
                           "lost")):
            g["open_all"] = lambda port, log, _b=bus: (Tap(log), _b, Tx())
            t0 = real.perf_counter()
            got = collect(a, out=lambda s: None)
            took = real.perf_counter() - t0
            if dies == "lost":
                out.append((len(got) == 6 and got[5] is not None
                            and got[5].startswith("VOID: the pcan reader lost frames")
                            and took < 2.0 and judge(*got)[0] is None and run_rc(got) == 3
                            and any("NOT-PROVEN" in x for x in judge(*got)[1]),
                            "CAN-FAIL collect: an overrun row -> the VOID line says "
                            "frames were lost, the wait cut short (%.1f s), NOT-PROVEN, "
                            "exit 3: %s" % (took, got[5:])))
            elif dies:
                out.append((len(got) == 6 and got[5] is not None
                            and got[5].startswith("VOID: the pcan reader died")
                            and "BUSOFF" in got[5] and took < 2.0,
                            "CAN-FAIL collect: a PCAN reader that raises -> its "
                            "VOID line, the wait cut short (%.1f s): %s" % (took, got[5:])))
                out.append((judge(*got)[0] is None and run_rc(got) == 3,
                            "... judged NOT-PROVEN, exit 3"))
            else:
                out.append((len(got) == 6 and got[5] is None
                            and judge(*got)[0] is False,
                            "collect: the same frame from a reader that lived "
                            "-> no VOID line, judged (a NO)"))
    finally:
        g["open_all"], g["time"], g["UPTIME_WAIT_S"], g["WATCH_S"] = saved
        scratch_dir.remove(d)
    return out


def run_rc(collected):
    """run()'s exit code for collect()'s result (the self-test's)."""
    saved = globals()["collect"]
    try:
        globals()["collect"] = lambda _a, _out=print: collected
        return run(parse(["--port", "/dev/x2"]), out=lambda s: None)
    finally:
        globals()["collect"] = saved


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
    ("the tap never started", "    tap.start()\n    # bus states", "    # bus states"),
    ("the port optional",
     "    ap.add_argument(\"--port\", required=\"--self-test\" not in argv",
     "    ap.add_argument(\"--port\", required=False and \"--self-test\" not in argv"),
    ("the port defaulting to COM3",
     "                    help=\"the X2's USB Serial/JTAG port\")",
     "                    default=\"COM3\", help=\"the X2's USB Serial/JTAG port\")"),
    # the verdict (2026-11-19)
    ("the exit code 0 whatever the verdict",
     "    return {True: 0, False: 1}.get(ok, 3)", "    return 0"),
    ("run's verdict not judge's",
     "    ok, lines = judge(*got)",
     "    ok, lines = True, judge(*got)[1]"),
    ("no FAIL line on NO",
     "    if not ok:\n        out(\"FAIL", "    if False:\n        out(\"FAIL"),
    ("the boot line not needed",
     "rh is not None and bool(boots) and pre_up",
     "rh is not None and pre_up"),
    ("boot lines before the reset counted",
     "    boots = [(h - rh, x) for h, x in after if BOOT_LINE",
     "    boots = [(h - (rh or 0), x) for h, x in rows if BOOT_LINE"),
    ("any uptime before the reset enough",
     "pre_up >= 2", "pre_up >= 0"),
    ("the last 0x7F7 after the reset, not the first",
     "post[0][1] == 0 and", "post[-1][1] == 0 and"),
    ("0x7F7 from before the reset counted",
     "for h, i, d in frames if i == 0x7F7 and rh and h > rh]",
     "for h, i, d in frames if i == 0x7F7 and rh]"),
    ("the port NOT BACK ignored",
     "\"NOT BACK\" in x for _d, x in marks", "\"NOT BACK\" in x for _d, x in []"),
    # 2026-11-19, the charge-interposer reviewer's PCAN ruling 1
    ("reader death: the PCAN's recv unguarded",
     "        RG.read_loop(pc, death, stop, pcan_row, read_s=0.1, drain=False)",
     "        while not stop.is_set():\n"
     "            m = RG.screen(pc, pc.recv(0.1), death)\n"
     "            if m is not None:\n"
     "                pcan_row(m)"),
    ("reader death: judged on a dead reader's frames",
     "    if reader_void:\n", "    if False:\n"),
    ("reader death: NOT-PROVEN exits 1",
     "    return {True: 0, False: 1}.get(ok, 3)", "    return 0 if ok else 1"),
    ("reader death: collect drops the VOID line",
     "            death.void_line() if death.error else None)",
     "            None)"),
    # 2026-11-20, the reviewer's PCAN item 2
    ("PCAN item 2: the reader unscreened",
     "        RG.read_loop(pc, death, stop, pcan_row, read_s=0.1, drain=False)",
     "        RG.guard(death, stop,\n"
     "                 lambda: (lambda m: m is not None and pcan_row(m))(pc.recv(0.1)))"),
    # 2026-11-23, the reviewer's reader_guard ruling
    ("reader_guard: the reader drains after stop",
     "read_s=0.1, drain=False)", "read_s=0.1, drain=True)"),
    # 2026-11-23, the reviewer's sweep ruling: premise failures as VOID lines
    ("premise: an adapter that does not open raises",
     "    except Exception as e:                                 # noqa: BLE001\n"
     "        raise NotOpened(",
     "    except ZeroDivisionError as e:\n"
     "        raise NotOpened("),
    ("premise: NotOpened not caught by run", "    except NotOpened as e:",
     "    except ZeroDivisionError as e:"),
    ("premise: an adapter that does not open a bare line",
     "        out(\"VOID: %s; nothing was judged\" % e)",
     "        out(\"%s; nothing was judged\" % e)"),
    ("premise: an adapter that does not open exits 0",
     "        out(\"VOID: %s; nothing was judged\" % e)\n        return 3",
     "        out(\"VOID: %s; nothing was judged\" % e)\n        return 0"),
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
