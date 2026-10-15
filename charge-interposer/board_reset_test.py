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
  - a board run's fault-vs-flow-drop order from the PCAN witness (device
    clock), resolved only past the margin (witness_order_cases);
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


def stimulus_cases(R):
    """The charger stimulus premise (reviewer, 2026-10-14): a gap over
    400 ms in the forwarded 0x18FFD4C0 inside a session is the bench's
    (VOID) only if charger_sim's stalls cover it AND nothing was lost."""
    def rows(gap_at=None, gap_s=0.55, state=1, drop=0):
        """Charger status every 100 ms from device 10 s to 30 s, host =
        device + 100; 0x7F4 every 1 s in `state`; a gap of gap_s at
        gap_at; `drop` frames removed elsewhere (a board loss)."""
        out, d = [], 10.0
        while d <= 30.0:
            if gap_at is not None and gap_at < d < gap_at + gap_s:
                d = round(d + 0.1, 4)
                continue
            out.append((d + 100.0, d, 0x18FFD4C0, bytes(8)))
            d = round(d + 0.1, 4)
        for k in range(10, 31):
            out.append((k + 100.0, float(k), 0x7F4,
                        bytes([4, 9, state, 0, 0, 0, 0, 0])))
        chg = [r for r in out if r[2] == 0x18FFD4C0]
        for r in chg[5:5 + drop]:
            out.remove(r)
        return out, len(chg)

    def log(sent, stalls=()):
        lines = ["12:00:00 chg STALL %d ms wall at sim t=1s host=%.6f  gc "
                 "collections during it gen0/1/2=(0, 0, 0)" % (ms, h)
                 for ms, h in stalls]
        if sent is not None:
            lines.append("12:00:30 chg status frames sent: %d (0x18FFD4C0)"
                         % sent)
        return "\n".join(lines) + "\n"
    # a 0.55 s gap from device 20.0 (host 120.0): stalls covering it
    cover = [(90, 120.09), (90, 120.18), (90, 120.27), (90, 120.36),
             (90, 120.45), (90, 120.54)]
    w, n = rows(gap_at=20.0)
    v, why = R.charger_stimulus(w, log(n, cover))
    check(v == "VOID" and "covered by charger_sim's own stalls" in why,
          "a stall-covered gap with nothing lost is the bench's: VOID -> %s"
          % why)
    v, why = R.charger_stimulus(w, log(n))
    check(v == "FAIL" and "not the bench's" in why,
          "the same gap with no stall to explain it: FAIL -> %s" % why)
    w2, n2 = rows()
    v, why = R.charger_stimulus(w2, log(n2))
    check(v == "OK" and "largest gap in the forwarded 0x18FFD4C0 100 ms"
          in why, "no gap over 400 ms: the premise holds -> %s" % why)
    w3, n3 = rows(gap_at=20.0, drop=1)
    v, why = R.charger_stimulus(w3, log(n3, cover))
    check(v == "FAIL" and "1 lost" in why,
          "a stall-covered gap with ONE frame missing: FAIL against the "
          "board -> %s" % why)
    w4, n4 = rows(gap_at=20.0, state=4)
    v, why = R.charger_stimulus(w4, log(n4))
    check(v == "OK", "a gap while TERMINATED is outside a session: not "
          "judged -> %s" % why)
    v, why = R.charger_stimulus(w, log(None, cover))
    check(v == "FAIL" and "no loss can be ruled out" in why,
          "no sent count in charger_sim's log: FAIL -> %s" % why)


def witness_order_cases(R):
    """A board run's fault-vs-flow-drop order (reviewer, 2026-10-14): from
    the PCAN witness, resolved only when the two events are further apart
    than the margin (20 ms, or a larger measured latency)."""
    import protocol as P
    fault = bytes(P.encode(P.BEL, 0x18FFD4C0, {
        "BELINV_inverterFault": 1}, "BELINV_statusMultiplexer", 3))
    clean = bytes(P.encode(P.BEL, 0x18FFD4C0, {}, "BELINV_statusMultiplexer",
                           3))
    flow1, flow0 = P.enc_master(1, P.MODE_CHARGER), P.enc_master(
        0, P.MODE_CHARGER)

    def rows(t_fault, t_drop):
        out = [(0.0, 99.0, 0x18EFC000, flow1), (0.0, 99.1, 0x18FFD4C0, clean)]
        if t_fault is not None:
            out.append((0.0, t_fault, 0x18FFD4C0, fault))
        if t_drop is not None:
            out.append((0.0, t_drop, 0x18EFC000, flow0))
        return out
    o = R.witness_fault_flow_order(rows(100.000, 100.450))
    check(o["resolved"] and o["t_flag"] < o["t_drop"]
          and abs(o["gap_ms"] - 450.0) < 0.01,
          "fault first, 450 ms apart: resolved, fault first -> %s" % o)
    o = R.witness_fault_flow_order(rows(100.500, 100.000))
    check(o["resolved"] and o["t_drop"] < o["t_flag"],
          "flow drop first, 500 ms apart: resolved, flow first")
    o = R.witness_fault_flow_order(rows(None, None))
    check(o["resolved"] is None and o["t_flag"] is None and
          o["t_drop"] is None, "neither event: nothing resolved")
    o = R.witness_fault_flow_order(rows(100.000, 100.010))
    check(o["resolved"] is False and o["margin_ms"] == 20.0,
          "10 ms apart, inside the 20 ms margin: UNRESOLVED -> %s" % o)
    o = R.witness_fault_flow_order(rows(100.000, 100.030),
                                   latency_max_s=0.050)
    check(o["resolved"] is False and o["margin_ms"] == 50.0,
          "30 ms apart with a 50 ms measured latency: UNRESOLVED")
    # through the check itself
    intp = "[949252] SAFE         TRIP a=8 b=0 c=0\n"
    diag = [(10.0, 0x7F4, bytes([4, 9, 5, 8, 0, 0, 0, 0])),
            (11.0, 0x7F6, bytes(8)), (12.0, 0x7F6, bytes(8))]
    ok, why = R._fault_or_flow_first(intp, "", "", diag, False,
                                     R.witness_fault_flow_order(
                                         rows(100.000, 100.010)))
    check(not ok and "order UNRESOLVED" in why and "gap 10.0 ms" in why,
          "the check FAILS an unresolved pair with its reason, never "
          "picking -> %s" % why)
    ok, why = R._fault_or_flow_first(intp, "", "", diag, False,
                                     R.witness_fault_flow_order(
                                         rows(100.000, 100.450)))
    check(ok and "gap 450.0 ms" in why and "witness" in why,
          "a resolved trip-first pair passes, printing the gap -> %s" % why)
    ok, why = R._fault_or_flow_first(intp, "", "", diag, False,
                                     R.witness_fault_flow_order(
                                         rows(100.500, 100.000)))
    check(not ok and "flow drop reached it first" in why,
          "a trip when the witness saw the flow drop first FAILS -> %s" % why)
    ok, why = R._fault_or_flow_first(intp, "", "", diag, False,
                                     (100000.0, 100450.0))
    check(ok and "gap 450.0 ms" in why,
          "the trace's order prints its gap too -> %s" % why)
    import diag_cadence as DC
    check(DC.VCU_PAGE00_ID == P.CMD_ID == 0x18EFC000,
          "the witness logs the VCU's page 00 under protocol's own id")


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
    witness_order_cases(R)
    stimulus_cases(R)
    td = R.teardown_order(True, True)
    check(td == ["settle", "tap", "witness"],
          "teardown: settle, then the tap, then the witness LAST -> %s" % td)
    check(td.index("witness") > td.index("tap") and R.END_SETTLE_S > 0.5,
          "...so the board's end-of-run trip (500 ms after vehicle_sim "
          "exits) is still on the witness (the old order stopped it first)")
    check(R.teardown_order(True, False) == ["tap"],
          "no witness: no settle, just the tap")

    print("\nboard_reset_test: %s" % ("all checks OK" if not FAILS else
                                     "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
