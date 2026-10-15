"""The board reset for boot-mid-session on hardware: its own test, no
hardware (tester, 2026-10-13; reviewer's conditions the same day).

run_scenario's SerialTap sends esptool's USB-Serial-JTAG hard reset at the
scenario's time, survives the port re-enumerating, and board_reset_proof()
accepts the reset only with a boot line on serial after it AND 0x7F7's
uptime dropping to 0 from >= 2 min. Driven here against a fake pyserial:

  - a board that honours the reset (the port drops, re-enumerates, and
    the boot lines arrive) passes;
  - a board whose port IGNORES the reset fails (the reviewer's can-fail);
  - the line sequence is esptool's, and the reconnect does not reset the
    board a second time (DTR and RTS low before open);
  - a port that never comes back, uptime that cannot discriminate, uptime
    that does not drop, and no reset sent each fail.

Also the two other things run_scenario does differently for a real board
(tester, 2026-10-14, step 4 item 2):

  - the start order and the vehicle segment's acknowledger: charger_sim
    first, then vehicle_sim, with the PCAN witness opened ACTIVE (it
    acknowledges) before either; passive only on request. (vehicle_sim
    first, tried in 9aaf1e2, is refused: it left CAN1 and then CAN2 with
    nothing to acknowledge.)
  - SerialTap holds a line that readline() returned without its newline
    (its whole-call timeout ended mid-line) until the rest arrives, so a
    line is never written as two (partial_line_case, which fails on the
    SerialTap before this change).

Run: python board_reset_test.py   (exit 0 = all checks passed)
"""

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_scenario as R                                    # noqa: E402

FAILS = []


def check(cond, what):
    print("  %s  %s" % ("ok  " if cond else "FAIL", what))
    if not cond:
        FAILS.append(what)


class Board(object):
    """What the fake ports share: the line sequence seen, whether a reset
    has been seen, and how this board reacts to one."""

    def __init__(self, honours=True, comes_back=True, down_s=0.4):
        self.honours, self.comes_back, self.down_s = honours, comes_back, down_s
        self.lines_log = []          # (what, value, t)
        self.reset_at = None
        self.uptime = 300            # seconds before the reset
        self.opens = []              # (dtr, rts) at each open after the first
        self.booted = False

    def saw(self, what, value):
        self.lines_log.append((what, value, time.perf_counter()))
        rts = [v for w, v, _t in self.lines_log if w == "rts"]
        # RTS high then low with DTR low = reset (USB-Serial-JTAG)
        if self.honours and rts[-2:] == [True, False] and \
                self.reset_at is None:
            self.reset_at = time.perf_counter()


class FakePort(object):
    def __init__(self, board, port=None, baud=None, timeout=None):
        self.b = board
        self.port, self.baudrate, self.timeout = port, baud, timeout
        self._dtr, self._rts = True, True
        self.n = 0
        if port is not None:
            self.open(first=True)

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, v):
        self._dtr = v
        self.b.saw("dtr", v)

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, v):
        self._rts = v
        self.b.saw("rts", v)

    def open(self, first=False):
        b = self.b
        if not first:
            if b.reset_at is not None and (
                    not b.comes_back or
                    time.perf_counter() - b.reset_at < b.down_s):
                raise OSError("could not open port (re-enumerating)")
            b.opens.append((self._dtr, self._rts))
            self.after_reset = b.reset_at is not None
            self.n = 0

    def readline(self):
        b = self.b
        time.sleep(0.01)
        if b.reset_at is not None and not getattr(self, "after_reset", False):
            raise OSError("ClearCommError failed (the device went away)")
        self.n += 1
        if getattr(self, "after_reset", False):
            boot = [b"ESP-ROM:esp32s3-20210327\r\n",
                    b"rst:0x15 (USB_UART_CHIP_RESET),boot:0x29\r\n",
                    b"Vtrux charge interposer\r\n",
                    b"t=5s state=PASSTHROUGH vmax=0mV\r\n"]
            return boot[self.n - 1] if self.n <= len(boot) else b""
        return ("t=%ds state=MONITOR vmax=3400mV\r\n"
                % (b.uptime + self.n)).encode()

    def close(self):
        pass


class FakeSerialModule(object):
    def __init__(self, board):
        self.board = board

    def Serial(self, port=None, baud=None, timeout=None):
        return FakePort(self.board, port, baud, timeout)


def run_tap(board, reset_after=0.3, run_s=1.6):
    tmp = Path(tempfile.mkdtemp(dir=str(HERE)))
    try:
        tap = R.SerialTap("FAKE", 115200, tmp / "i.log")
        tap._serial_mod = FakeSerialModule(board)
        tap.start()
        threading.Timer(reset_after, tap.reset_board).start()
        time.sleep(run_s)
        tap.stop()
        rows = R.diag_cadence_rows(tap.stamped_path)
        return tap, rows, (tmp / "i.log").read_text()
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


class FakeDb(object):
    def decode_message(self, fid, body):
        return {"intp_uptime_min": body[7]}


def veh(pre_min, post_min, reset_rel):
    """vehicle_sim diagframe lines: 0x7F7 every 5 s, uptime byte 7."""
    out = []
    for t in range(0, 60, 5):
        u = pre_min if t < reset_rel else post_min
        out.append("diagframe t=%.1f id=0x7F7 data=%s"
                   % (t, bytes([0xfd, 0x7b, 0x19, 0x7f, 7, 0, 4, u]).hex()))
    return "\n".join(out)


class PiecesPort(FakePort):
    """A board whose lines readline() returns in pieces: the first comes
    back WITHOUT its newline, as pyserial's readline does when its
    whole-call timeout ends mid-line."""

    PIECES = [b"t=10s state=MON", b"ITOR vmax=3400mV\r\n",
              b"[10001] MONITOR      charge established a=-78 b=0 c=0\r\n",
              b"diag st=MONITOR trip=0 fla", b"gs=0xdf txf=0 silent=0 fw=9 ",
              b"schema=4\r\n"]

    def readline(self):
        time.sleep(0.01)
        self.n += 1
        return self.PIECES[self.n - 1] if self.n <= len(self.PIECES) else b""


class PiecesModule(object):
    def __init__(self, board):
        self.board = board

    def Serial(self, port=None, baud=None, timeout=None):
        return PiecesPort(self.board, port, baud, timeout)


def partial_line_case(R):
    """Takes the module, so the same case can be run against a SerialTap
    from before the change to show it fails there."""
    tmp = Path(tempfile.mkdtemp(dir=str(HERE)))
    try:
        tap = R.SerialTap("FAKE", 115200, tmp / "i.log")
        tap._serial_mod = PiecesModule(Board())
        tap.start()
        time.sleep(0.4)
        tap.stop()
        lines = [x for x in (tmp / "i.log").read_text().splitlines()
                 if not x.startswith("final state")]
        stamped = [x.split("\t", 1)[1] for x in
                   Path(tap.stamped_path).read_text().splitlines()]
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    want = ["t=10s state=MONITOR vmax=3400mV",
            "[10001] MONITOR      charge established a=-78 b=0 c=0",
            "diag st=MONITOR trip=0 flags=0xdf txf=0 silent=0 fw=9 schema=4"]
    check(lines == want and stamped == want,
          "a line returned in pieces is written whole, once, in both copies "
          "-> %s" % lines)
    return lines == want


def main():
    print("a board that honours the reset")
    b = Board()
    tap, rows, plain = run_tap(b)
    seq = [(w, v) for w, v, _t in b.lines_log]
    check(seq[:5] == [("dtr", False), ("rts", True), ("dtr", False),
                      ("rts", False), ("dtr", False)],
          "esptool's sequence: DTR low, RTS high (+ dummy DTR), RTS low "
          "(+ dummy DTR) -> %s" % seq[:5])
    t_rts = [t for w, v, t in b.lines_log if w == "rts"]
    check(len(t_rts) >= 2 and 0.18 <= t_rts[1] - t_rts[0] <= 0.35,
          "RTS held 0.2 s -> %.3f s" % (t_rts[1] - t_rts[0]))
    marks = [ln for _h, ln in rows if ln.startswith("#")]
    check(any(m.startswith("# RESET SENT") for m in marks)
          and any(m.startswith("# SERIAL LOST") for m in marks)
          and any(m.startswith("# SERIAL BACK gap=") for m in marks),
          "the stamped copy marks the reset, the loss and the return -> %s"
          % [m[:30] for m in marks])
    check(b.opens and all(o == (False, False) for o in b.opens),
          "every reconnect opens with DTR and RTS low (no second reset) "
          "-> %s" % b.opens)
    check("Vtrux charge interposer" in plain and "# RESET SENT" in plain,
          "the plain log carries the boot lines and the marker")
    rel = 20.0
    ok, why = R.board_reset_proof(rows, veh(4, 0, rel), tap.reset_host_t,
                                  tap.reset_host_t - rel, db=FakeDb())
    check(ok and "boot line" in why and "4 min before, 0 after" in why,
          "a boot line after the reset and uptime 4 -> 0 prove it -> %s"
          % why)

    print("a board whose port ignores the reset (the can-fail)")
    b2 = Board(honours=False)
    tap2, rows2, _p = run_tap(b2)
    ok, why = R.board_reset_proof(rows2, veh(4, 4, rel), tap2.reset_host_t,
                                  tap2.reset_host_t - rel, db=FakeDb())
    check(not ok and "no boot line" in why and "not 0" in why,
          "no boot line and uptime still 4 min: FAILS -> %s" % why)

    print("a port that never comes back")
    b3 = Board(comes_back=False)
    t0 = time.perf_counter()
    tap3, rows3, _p = run_tap(b3, run_s=1.0)
    check(time.perf_counter() - t0 < 6.0,
          "stop() does not hang while the tap is still reconnecting")
    ok, why = R.board_reset_proof(rows3, veh(4, 0, rel), tap3.reset_host_t,
                                  tap3.reset_host_t - rel, db=FakeDb())
    check(not ok and "no boot line" in why,
          "no serial after the reset fails -> %s" % why)

    print("the proof, on its own")
    h = 1000.0
    good = [(h - 5, "t=290s state=MONITOR"), (h, "# RESET SENT host=1000"),
            (h + 0.6, "# SERIAL BACK gap=0.500 s"),
            (h + 0.7, "Vtrux charge interposer"),
            (h + 5.0, "t=5s state=PASSTHROUGH")]
    ok, why = R.board_reset_proof(good, veh(4, 0, rel), h, h - rel,
                                  db=FakeDb())
    check(ok and "gap=0.500 s" in why and "t=5s" in why,
          "the reconnect gap and the first status line are reported -> %s"
          % why)
    ok, why = R.board_reset_proof(good, veh(1, 0, rel), h, h - rel,
                                  db=FakeDb())
    check(not ok and "cannot show a reset" in why,
          "uptime 1 min before the reset cannot discriminate: FAILS -> %s"
          % why)
    ok, why = R.board_reset_proof(good, veh(4, 0, rel), None, h - rel,
                                  db=FakeDb())
    check(not ok and "no reset was sent" in why, "no reset sent fails")
    pre_banner = [(h - 1, "Vtrux charge interposer")] + good[1:3]
    ok, why = R.board_reset_proof(pre_banner, veh(4, 0, rel), h, h - rel,
                                  db=FakeDb())
    check(not ok and "no boot line" in why,
          "a boot line from BEFORE the reset does not count -> %s" % why)
    check(R.BY_NAME["boot-mid-session"].board_reset_at_s == 20.0 and
          all(s.board_reset_at_s is None for s in R.SCENARIOS
              if s.name != "boot-mid-session"),
          "only boot-mid-session asks for a board reset")

    print("the start order, the acknowledger and the serial lines "
          "(2026-10-14)")
    order = R.start_order(True)
    check(order == ["charger", "vehicle"],
          "a real board: charger_sim first, then vehicle_sim, no "
          "interposer_sim -> %s" % order)
    check(order.index("charger") < order.index("vehicle"),
          "...the charger before the vehicle side (9aaf1e2's vehicle-first "
          "order is refused)")
    check(R.start_order(False) == ["charger", "interposer", "vehicle"],
          "simulated runs keep their order")
    import can
    import diag_cadence as DC
    check(DC.witness_bus_state(True) == can.BusState.ACTIVE,
          "the witness that acknowledges opens ACTIVE")
    check(DC.witness_bus_state(False) == can.BusState.PASSIVE,
          "...and the passive one PASSIVE")
    w = DC.DiagWitness("unused")
    check(w.acks is True,
          "a DiagWitness acknowledges unless asked not to")
    a = R.build_parser().parse_args(
        ["charger-fault-stop", "--external-interposer", "--diag-witness",
         "PCAN_USBBUS1"])
    check(a.diag_witness_mode == "acks",
          "a board run's witness acknowledges by default")
    a = R.build_parser().parse_args(
        ["charger-fault-stop", "--external-interposer", "--diag-witness",
         "PCAN_USBBUS1", "--diag-witness-mode", "passive"])
    check(a.diag_witness_mode == "passive", "...passive only on request")
    partial_line_case(R)

    print("\nboard_reset_test: %s" % ("all checks OK" if not FAILS else
                                     "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
