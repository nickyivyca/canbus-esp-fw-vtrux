"""Spec 2.3, listen-only puts no error frame on the vehicle segment: stage
(b) batch 1 step 3, check (c) (the charge-interposer reviewer's batch,
2026-11-27; design accepted 2026-11-28). After a provoked error, a vehicle
port in listen-only must drive no dominant error flag. This file is the
check's judge and its can-fail on the vport_bench image (--board vport).
The product image's form (the board in listen-only through its own
kept-driver switch) joins it with the ported image.

THE STIMULUS. The Kvaser, the only sender, sends at a wrong bit rate
(250k, then 1M) on the 500k vehicle segment. A 500k receiver sees its
frames as bus errors. A wrong rate here is a stimulus only, not a claim
about the product, whose port stays at 500k (the reviewer's tracker row
"Vehicle frames lost at the vehicle port's mode switch"). It is not spec
9's wrong-rate listen-only cases, which put the board at a wrong rate.

THE JUDGE is the sender's own error state, read from its link counters
(kvaser_link.link_counters) before and after each window:
  - every receiver silent: the Kvaser meets only ACK errors. Its TEC rises
    to 128, it goes error-passive, and there it stops: an error-passive
    transmitter's ACK error with no dominant bit in its passive error flag
    does not raise TEC (ISO 11898-1's fault confinement). error_passive
    grows, bus_off does not;
  - a receiver driving dominant error flags: they land in the Kvaser's own
    frames as bit errors, which do raise TEC, so it goes bus-off again and
    again (its link restarts after RESTART_MS). bus_off grows.
  A window whose Kvaser neither went error-passive nor bus-off showed no
  stimulus: a TEST FAULT, not judged.

THE ARMS (--board vport), at each wrong rate, in this order:
  driver   MODE listen: the driver installed listen-only (ESP-IDF writes
           REC 128 at start): a control, must be clean
  rec      MODE normal, LOMREG rec: listen-only by register with REC 128,
           the driver kept in normal mode, as shared/twai_port T14/T15's
           switch makes it: a control, must be clean
  norec    MODE normal, LOMREG norec: listen-only by register with no REC
           write, so the controller is error-active (REC below 128, read
           back), the LISTEN_ONLY_DOM errata: THE CAN-FAIL, must show
           bus-offs, or the judge is not shown able to separate
  rec2     LOMREG off, LOMREG rec: the second control, after the can-fail
Each arm's board state is read back (the reply, then STATUS) before its
window; a state not shown is a TEST FAULT. The Kvaser's link is down while
the board is set up, and down again after the window.

THE RIG. The PCAN stays closed (a closed PCAN drives nothing). Nothing on
the segment may acknowledge at 500k or drive error flags but the board:
the controls show it, since any other flagging node makes them FLAG too.

VERDICT. PASS: at every rate every control clean and the can-fail
separating. FAIL: a control flagged (the board's listen-only, or another
node, drove error flags) or the can-fail did not separate; each line says
which. VOID (exit 3): an arm's premise not met (the board's port, its
image, its state, the Kvaser's link or counters, or no stimulus); the
arms after it are not run.

    python3.13 -B bench_lom_errflag.py --self-test
    python3.13 -B bench_lom_errflag.py --can-fail
    python3.13 -B bench_lom_errflag.py --board vport --port /dev/<X2> \\
        --elf <vport_bench.elf> --out <file.txt>

Tester, 2026-11-28.
"""

import argparse
import datetime
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (the sibling drivers) first, then charge-interposer/
# (kvaser_link, serial_open): both found from this file, never from the
# working folder (port step 0, 2026-10-26).
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import kvaser_link                 # noqa: E402  (the Kvaser on kvaser_usb)

NODE_ID = 0x5A5
WRONG_RATES = (250000, 1000000)
WINDOW_S = 5.0
SEND_PERIOD_S = 0.02
RESTART_MS = 100
SILENT_REC = 128            # error-passive from here (REC >= 128)
KEYS = ("bus_off", "restarts", "error_passive", "bus_error")

# (name, label, board commands in order, expect flags)
VPORT_ARMS = [
    ("driver", "driver listen-only (MODE listen)", ["MODE listen"], False),
    ("rec", "register listen-only with REC 128 (LOMREG rec)",
     ["MODE normal", "LOMREG rec"], False),
    ("norec", "register listen-only with no REC write (LOMREG norec), the "
     "can-fail", ["MODE normal", "LOMREG norec"], True),
    ("rec2", "register listen-only with REC 128 again (LOMREG off, LOMREG rec)",
     ["LOMREG off", "LOMREG rec"], False),
]

CLEAN, FLAGS, SEPARATES, NOT_SHOWN, FAULT = (
    "CLEAN", "FLAGS", "SEPARATES", "NOT SHOWN", "FAULT")


# ---- pure parts -------------------------------------------------------------

def field(line, key):
    """key=value in a vport_bench reply, as a string, or None."""
    for f in (line or "").split():
        if f.startswith(key + "="):
            return f.split("=", 1)[1]
    return None


def int_field(line, key):
    try:
        return int(field(line, key))
    except (TypeError, ValueError):
        return None


def board_state(name, replies, status):
    """Is the board in arm `name`'s state? replies: {command: reply or None},
    status: the STATUS reply after them. -> (ok, why)."""
    for cmd, r in replies.items():
        if r is None or r.startswith("@ERR"):
            return False, "%s: %s" % (cmd, r or "no reply")
        if cmd.startswith("MODE ") and field(r, "driver") != "ESP_OK":
            return False, "%s: %s" % (cmd, r)
        if cmd.startswith("LOMREG ") and field(r, "reset") != "0":
            return False, "%s: the controller still in reset mode: %s" % (cmd, r)
    if status is None or field(status, "installed") != "1":
        return False, "STATUS: %s" % (status or "no reply")
    mode, lom, rec = field(status, "mode"), int_field(status, "lomreg"), \
        int_field(status, "rec_reg")
    want = {"driver": ("listen", 0), "rec": ("normal", 1), "norec": ("normal", 2),
            "rec2": ("normal", 1)}[name]
    if (mode, lom) != want or rec is None:
        return False, "STATUS reads mode=%s lomreg=%s rec_reg=%s, the arm needs " \
                      "mode=%s lomreg=%d" % (mode, lom, rec, want[0], want[1])
    if name == "norec" and rec >= SILENT_REC:
        return False, "REC reads %d with no REC write: the controller is not " \
                      "error-active, so the can-fail would test nothing" % rec
    if name != "norec" and rec != SILENT_REC:
        return False, "REC reads %d, not %d" % (rec, SILENT_REC)
    return True, "mode=%s lomreg=%d rec_reg=%d" % (mode, lom, rec)


def judge_arm(expect_flags, growth):
    """One window's Kvaser counter growth -> (outcome, text)."""
    bo, ep = growth["bus_off"], growth["error_passive"]
    if bo < 1 and ep < 1:
        return FAULT, ("TEST FAULT: no stimulus shown: the Kvaser went neither "
                       "error-passive nor bus-off (%s)" % growth_text(growth))
    if expect_flags:
        if bo >= 1:
            return SEPARATES, "the Kvaser went bus-off %d time(s): dominant error " \
                              "flags seen, as the can-fail must (%s)" % (bo, growth_text(growth))
        return NOT_SHOWN, "the Kvaser never went bus-off: the error-active " \
                          "listen-only showed no dominant error flag (%s)" % growth_text(growth)
    if bo >= 1:
        return FLAGS, "the Kvaser went bus-off %d time(s): dominant error flags " \
                      "on the segment (%s)" % (bo, growth_text(growth))
    return CLEAN, "no bus-off: no dominant error flag (%s)" % growth_text(growth)


def growth_text(g):
    return ", ".join("%s +%d" % (k, g[k]) for k in KEYS)


def verdict(results):
    """results: [(rate, name, label, expect_flags, outcome, text)] -> (rc,
    lines). rc 0 PASS, 1 FAIL, 3 VOID. A FAULT anywhere is VOID, whatever
    else was seen; then a FLAGS or NOT SHOWN is FAIL; else every rate must
    have each arm, clean controls and a separating can-fail."""
    faults = [r for r in results if r[4] == FAULT]
    if faults:
        r = faults[0]
        text = r[5][len("VOID: "):] if r[5].startswith("VOID: ") else r[5]
        return 3, ["VOID: %s at %d bit/s: %s" % (r[2], r[0], text)]
    lines = []
    for r in results:
        if r[4] == FLAGS:
            lines.append("FAIL: %s at %d bit/s: %s" % (r[2], r[0], r[5]))
        elif r[4] == NOT_SHOWN:
            lines.append("FAIL: the judge does not separate at %d bit/s: %s: %s"
                         % (r[0], r[2], r[5]))
    if lines:
        return 1, lines
    seen = dict(((r[0], r[1]), r[4]) for r in results)
    for rate in WRONG_RATES:
        for name, label, _c, expect in VPORT_ARMS:
            want = SEPARATES if expect else CLEAN
            if seen.get((rate, name)) != want:
                return 3, ["VOID: %s at %d bit/s was not run" % (label, rate)]
    return 0, ["PASS: at %s bit/s every listen-only control was clean and the "
               "error-active can-fail went bus-off: no dominant error flag from "
               "the board's listen-only, and the judge shown to separate"
               % " and ".join(str(r) for r in WRONG_RATES)]


# ---- the bench --------------------------------------------------------------

class Board(object):
    """vport_bench's console: one command, one '@' reply."""

    def __init__(self, ser, log):
        self.ser, self.log = ser, log

    def cmd(self, line, timeout=10.0):
        # the reply's tag is the command's word without its '?': HELLO? ->
        # @HELLO (run ht_ce_e97f0e6a waited for "@HELLO?" and went VOID)
        want = "@" + line.split()[0].rstrip("?")
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            b = self.ser.readline()
            if not b:
                continue
            ln = b.decode("ascii", "replace").rstrip("\r\n")
            if ln.startswith(want) or ln.startswith("@ERR"):
                self.log("    board> %s" % ln)
                return ln
        self.log("    board> (no reply to %s)" % line)
        return None


def send_window(bus, seconds, clock=time.monotonic, sleep=time.sleep, can_mod=None):
    """The Kvaser sends NODE_ID every SEND_PERIOD_S for seconds, a refused
    send counted, not retried. -> (accepted, refused)."""
    if can_mod is None:
        import can as can_mod
    acc = ref = seq = 0
    end = clock() + seconds
    while clock() < end:
        m = can_mod.Message(arbitration_id=NODE_ID, is_extended_id=False,
                            data=seq.to_bytes(4, "little") + b"\x5a\xa5\x00\x00")
        seq = (seq + 1) & 0xFFFFFFFF
        try:
            bus.send(m, timeout=0)
            acc += 1
        except Exception:                                  # noqa: BLE001
            ref += 1
        sleep(SEND_PERIOD_S)
    return acc, ref


def run_arm(board, link, rate, arm, log, sender=send_window, seconds=WINDOW_S):
    """One arm at one rate. -> (outcome, text). The Kvaser's link is down
    while the board is set up, up at `rate` for the window, down after."""
    name, label, cmds, expect = arm
    log("  %s at %d bit/s" % (label, rate))
    t0 = time.time()
    try:
        log("    " + link.set_link(False))
    except link.LinkError as e:
        return FAULT, link.link_void(e, t0, time.time())
    replies = dict((c, board.cmd(c)) for c in cmds)
    status = board.cmd("STATUS")
    ok, why = board_state(name, replies, status)
    log("    board state: %s" % why)
    if not ok:
        return FAULT, "TEST FAULT: the board's state not shown: " + why
    bus = None
    try:
        log("    " + link.set_link(True, restart_ms=RESTART_MS, bitrate=rate))
        c0 = link.link_counters()
        bus = link.open_bus(receive_own_messages=False, error_frames=False)
        acc, ref = sender(bus, seconds)
        c1 = link.link_counters()
        growth = link.counter_growth(c0, c1, KEYS)
    except link.LinkError as e:
        return FAULT, link.link_void(e, t0, time.time())
    finally:
        if bus is not None:
            bus.shutdown()
        try:
            log("    " + link.set_link(False))
        except link.LinkError as e:                         # noqa: F841
            log("    the Kvaser's link not taken down after the window: %s" % e)
    log("    the Kvaser: %d send(s) taken, %d refused" % (acc, ref))
    after = board.cmd("STATUS")
    log("    board after: rec_reg=%s lomreg=%s state=%s"
        % (field(after, "rec_reg"), field(after, "lomreg"), field(after, "state")))
    out, text = judge_arm(expect, growth)
    log("    %s: %s" % (out, text))
    return out, text


def run_arms(board, link, log, arms=VPORT_ARMS, rates=WRONG_RATES, **kw):
    """Every arm at every rate, stopping at the first FAULT. -> results."""
    results = []
    for rate in rates:
        for arm in arms:
            out, text = run_arm(board, link, rate, arm, log, **kw)
            results.append((rate, arm[0], arm[1], arm[3], out, text))
            if out == FAULT:
                return results
    return results


def elf_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def premise_line(what, e):
    return "VOID: %s did not open (%s: %s): no arm run" % (what, type(e).__name__, e)


def run_bench(a):
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    try:
        log("run at %s; board %s; the Kvaser sends 0x%03X every %.0f ms for %.0f s "
            "per window at %s bit/s, restart-ms %d; the PCAN closed"
            % (datetime.datetime.now().isoformat(), a.board, NODE_ID,
               SEND_PERIOD_S * 1000, WINDOW_S, " then ".join(map(str, WRONG_RATES)),
               RESTART_MS))
        import serial_open
        try:
            ser = serial_open.open_quiet(a.port, 115200, 0.2)
        except Exception as e:                             # noqa: BLE001
            log(premise_line("the X2's port %s" % a.port, e))
            return 3
        try:
            board = Board(ser, log)
            hello = board.cmd("HELLO?")
            want = elf_sha256(a.elf)
            if field(hello, "elf_sha256") != want:
                log("VOID: the board is not running %s (HELLO: %s; the ELF's sha256 %s): "
                    "no arm run" % (a.elf, hello or "no reply", want))
                return 3
            rc, lines = verdict(run_arms(board, kvaser_link, log))
        finally:
            ser.close()
        for ln in lines:
            log(ln)
        log("RESULT: %s" % {0: "PASS", 1: "FAIL", 3: "VOID"}[rc])
        return rc
    finally:
        out.close()


# ---- self-test --------------------------------------------------------------

class _FakeLink(object):
    """kvaser_link's calls, logged; counters grow by `growth` per window."""
    LinkError = kvaser_link.LinkError

    def __init__(self, growth, fail=None):
        self.growth, self.fail, self.calls = growth, fail or {}, []
        self.c = dict((k, 10) for k in KEYS)
        self.up = False

    def set_link(self, up, restart_ms=None, bitrate=None):
        self.calls.append(("up", bitrate, restart_ms) if up else ("down",))
        if ("up" if up else "down") in self.fail:
            raise self.LinkError(self.fail["up" if up else "down"])
        self.up = up
        return "link %s" % ("up" if up else "down")

    def link_counters(self):
        self.calls.append(("counters", self.up))
        return dict(self.c)

    def open_bus(self, receive_own_messages=True, error_frames=True):
        self.calls.append(("open", receive_own_messages, error_frames))
        link = self

        class Bus(object):
            def shutdown(self):
                link.calls.append(("shutdown",))
        return Bus()

    def counter_growth(self, c0, c1, keys):
        return kvaser_link.counter_growth(c0, c1, keys)

    def link_void(self, e, t0, t1):
        return "VOID: kvaser link: %s" % e

    def sent(self, bus, seconds):
        self.calls.append(("send", self.up))
        g = self.growth(self.calls) if callable(self.growth) else self.growth
        for k in KEYS:
            self.c[k] += g.get(k, 0)
        return 250, 0


class _FakeBoard(object):
    """vport_bench's replies to the arms' commands."""

    def __init__(self, rec_norec=0, bad=None):
        self.mode, self.lom, self.rec = "listen", 0, 128
        self.rec_norec, self.bad, self.cmds = rec_norec, bad or {}, []

    def cmd(self, line):
        self.cmds.append(line)
        if line in self.bad:
            return self.bad[line]
        s = " mode=%s bitrate=500k" % self.mode
        if line.startswith("MODE "):
            self.mode, self.lom = line.split()[1], 0
            self.rec = 128 if self.mode == "listen" else self.rec_norec
            return "@MODE driver=ESP_OK mode=%s bitrate=500k" % self.mode
        if line.startswith("LOMREG "):
            a = line.split()[1]
            self.lom = {"rec": 1, "norec": 2, "off": 0}[a]
            self.rec = {"rec": 128, "norec": self.rec, "off": 0}[a]
            return "@LOMREG %s lomreg=%d rec_reg=%d reset=0%s" % (a, self.lom, self.rec, s)
        if line == "STATUS":
            return "@STATUS installed=1 state=running rec_reg=%d lomreg=%d%s" % (
                self.rec, self.lom, s)
        return "@ERR unknown"


def self_test():
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    quiet = {"error_passive": 1, "bus_error": 300}
    loud = {"error_passive": 1, "bus_off": 12, "restarts": 12, "bus_error": 900}

    # judge_arm
    check(judge_arm(False, dict(quiet, bus_off=0, restarts=0))[0] == CLEAN,
          "control: error-passive, no bus-off -> CLEAN")
    check(judge_arm(False, dict(loud))[0] == FLAGS, "control: bus-offs -> FLAGS")
    check(judge_arm(True, dict(loud))[0] == SEPARATES, "can-fail: bus-offs -> SEPARATES")
    check(judge_arm(True, dict(quiet, bus_off=0, restarts=0))[0] == NOT_SHOWN,
          "can-fail: no bus-off -> NOT SHOWN")
    z = dict((k, 0) for k in KEYS)
    check(judge_arm(False, z)[0] == FAULT and judge_arm(True, z)[0] == FAULT,
          "no error-passive and no bus-off -> FAULT (no stimulus), either arm")
    check(judge_arm(False, dict(z, bus_off=1))[0] == FLAGS,
          "a bus-off with no error-passive count is still a stimulus, and flags")

    # board_state
    fb = _FakeBoard()
    rep = {"MODE listen": fb.cmd("MODE listen")}
    check(board_state("driver", rep, fb.cmd("STATUS"))[0], "driver arm's state read back")
    fb = _FakeBoard()
    rep = dict((c, fb.cmd(c)) for c in ("MODE normal", "LOMREG norec"))
    check(board_state("norec", rep, fb.cmd("STATUS"))[0], "norec arm's state read back")
    check(not board_state("rec", rep, fb.cmd("STATUS"))[0], "norec's state is not rec's")
    fb = _FakeBoard(rec_norec=128)
    rep = dict((c, fb.cmd(c)) for c in ("MODE normal", "LOMREG norec"))
    ok, why = board_state("norec", rep, fb.cmd("STATUS"))
    check(not ok and "not error-active" in why, "norec with REC already 128 -> refused: %s" % why)
    fb = _FakeBoard()
    rep = dict((c, fb.cmd(c)) for c in ("MODE normal", "LOMREG rec"))
    st = fb.cmd("STATUS").replace("rec_reg=128", "rec_reg=0")
    check(not board_state("rec", rep, st)[0], "rec arm with REC read 0 -> refused")
    st = fb.cmd("STATUS").replace("lomreg=1", "lomreg=0")
    check(not board_state("rec", rep, st)[0],
          "rec arm with STATUS lomreg=0 (REC right, the register listen-only not "
          "in force) -> refused")
    check(not board_state("rec", {"MODE normal": None}, fb.cmd("STATUS"))[0],
          "a command with no reply -> refused")
    check(not board_state("rec", {"LOMREG rec": "@LOMREG rec lomreg=1 rec_reg=128 reset=1"},
                          fb.cmd("STATUS"))[0], "still in reset mode -> refused")
    check(not board_state("driver", {"MODE listen": "@MODE driver=ESP_FAIL"},
                          "@STATUS installed=1 mode=listen lomreg=0 rec_reg=128")[0],
          "a failed MODE -> refused")
    check(not board_state("driver", {}, None)[0], "no STATUS -> refused")

    # run_arms: order, rates, counters around the window, the can-fail
    def per_arm(calls):
        # the board's state decides: loud only in the norec arm
        return loud if B.lom == 2 else dict(quiet, bus_off=0)
    B = _FakeBoard()
    L = _FakeLink(per_arm)
    res = run_arms(B, L, lambda s: None, sender=L.sent, seconds=0)
    rc, lines = verdict(res)
    check(rc == 0 and lines[0].startswith("PASS"), "a passive board, a loud can-fail "
          "-> PASS: %s" % lines)
    ups = [c for c in L.calls if c[0] == "up"]
    check([u[1] for u in ups] == [WRONG_RATES[0]] * 4 + [WRONG_RATES[1]] * 4
          and all(u[2] == RESTART_MS for u in ups),
          "each window's link up at its wrong rate with restart-ms %d" % RESTART_MS)
    seq = [c[0] for c in L.calls]
    w = seq.index("send")
    check(seq[w - 3:w + 4] == ["up", "counters", "open", "send", "counters", "shutdown", "down"],
          "counters read after up and before down, around the sends: %s" % seq[w - 3:w + 4])
    check(seq[0] == "down" and B.cmds[0] == "MODE listen",
          "the link down before the first board command")
    check(all(c[1] is False and c[2] is False for c in L.calls if c[0] == "open"),
          "the sender's socket takes no echoes and no error frames")
    check(B.cmds.count("LOMREG norec") == 2, "the can-fail at each rate")

    L = _FakeLink(lambda calls: dict(quiet, bus_off=0))
    rc, lines = verdict(run_arms(_FakeBoard(), L, lambda s: None, sender=L.sent, seconds=0))
    check(rc == 1 and "does not separate" in lines[0], "a quiet can-fail -> FAIL: %s" % lines)
    L = _FakeLink(lambda calls: loud)
    rc, lines = verdict(run_arms(_FakeBoard(), L, lambda s: None, sender=L.sent, seconds=0))
    check(rc == 1 and lines[0].startswith("FAIL: driver listen-only"),
          "a loud control -> FAIL naming it: %s" % lines)
    L = _FakeLink(lambda calls: {})
    res = run_arms(_FakeBoard(), L, lambda s: None, sender=L.sent, seconds=0)
    rc, lines = verdict(res)
    check(rc == 3 and len(res) == 1 and "no stimulus" in lines[0],
          "no stimulus -> VOID at the first arm, nothing after: %s" % lines)
    L = _FakeLink(per_arm, fail={"up": "canctl up failed"})
    B = _FakeBoard()
    rc, lines = verdict(run_arms(B, L, lambda s: None, sender=L.sent, seconds=0))
    check(rc == 3 and "canctl up failed" in lines[0] and lines[0].count("VOID") == 1,
          "a link failure -> one VOID line: %s" % lines)
    B = _FakeBoard(bad={"LOMREG rec": "@ERR LOMREG needs the driver installed"})
    L = _FakeLink(per_arm)
    res = run_arms(B, L, lambda s: None, sender=L.sent, seconds=0)
    rc, lines = verdict(res)
    check(rc == 3 and "state not shown" in lines[0] and sum(
        1 for c in L.calls if c[0] == "send") == 1 and len(res) == 2,
        "a board state not shown -> VOID, no window for that arm: %s" % lines)
    rc, lines = verdict([(WRONG_RATES[0], "driver", "x", False, CLEAN, "")])
    check(rc == 3 and "not run" in lines[0], "an arm missing -> VOID: %s" % lines)

    # send_window: a refused send counted, never raised
    class _Can(object):
        class Message(object):
            def __init__(self, **kw):
                self.kw = kw

    class _Bus(object):
        n = 0

        def send(self, m, timeout=None):
            self.n += 1
            if self.n % 2:
                raise OSError("ENOBUFS")
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s
    acc, ref = send_window(_Bus(), 1.0, clock, sleep, _Can)
    check(acc == 25 and ref == 25, "send_window: 50 sends in 1 s, refused ones counted "
          "-> %d taken, %d refused" % (acc, ref))
    # Board.cmd on a stand-in port: each command's own reply, stale and
    # other lines passed over (run ht_ce_e97f0e6a: HELLO? had no reply)
    class _Ser(object):
        def __init__(self, lines):
            self.lines, self.sent = [ln.encode("ascii") + b"\r\n" for ln in lines], []

        def write(self, b):
            self.sent.append(b)

        def flush(self):
            pass

        def readline(self):
            return self.lines.pop(0) if self.lines else b""
    got = []
    bd = Board(_Ser(["boot noise", "@STATUS stale", "@HELLO vport_bench elf_sha256=ab mode=listen",
                     "@LOMREG rec lomreg=1 rec_reg=128 reset=0 mode=normal"]), got.append)
    h = bd.cmd("HELLO?", timeout=0.5)
    check(h is not None and field(h, "elf_sha256") == "ab",
          "Board.cmd: HELLO? answered by its @HELLO line, the others passed over -> %s" % h)
    r = bd.cmd("LOMREG rec", timeout=0.5)
    check(r is not None and r.startswith("@LOMREG"), "Board.cmd: LOMREG -> %s" % r)
    bd = Board(_Ser(["@ERR LOMREG needs the driver"]), got.append)
    check((bd.cmd("LOMREG norec", timeout=0.5) or "").startswith("@ERR"),
          "Board.cmd: an @ERR is the reply")
    check(Board(_Ser([]), got.append).cmd("STATUS", timeout=0.05) is None,
          "Board.cmd: no reply -> None")
    pl = premise_line("the X2's port p", OSError("no device"))
    check(pl.startswith("VOID:"), "premise_line is a VOID line")

    print("\n%d check(s) failed" % len(fails) if fails else "\nbench_lom_errflag self-test: all OK")
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("judge: a can-fail with no bus-off separates", "        if bo >= 1:\n            return SEPARATES",
     "        if True:\n            return SEPARATES"),
    ("judge: a control's bus-off clean", "    if bo >= 1:\n        return FLAGS",
     "    if False:\n        return FLAGS"),
    ("judge: no stimulus judged", "    if bo < 1 and ep < 1:", "    if False:"),
    ("judge: error-passive alone is a stimulus no more", "    if bo < 1 and ep < 1:",
     "    if bo < 1:"),
    ("verdict: a FAULT not VOID", "    if faults:\n", "    if False:\n"),
    ("verdict: a missing arm passes", "            if seen.get((rate, name)) != want:",
     "            if False:"),
    ("verdict: NOT SHOWN passes", "        elif r[4] == NOT_SHOWN:", "        elif False:"),
    ("state: norec's REC not checked", "    if name == \"norec\" and rec >= SILENT_REC:",
     "    if False:"),
    ("state: the controls' REC not checked", "    if name != \"norec\" and rec != SILENT_REC:",
     "    if False:"),
    ("state: lomreg not checked", "    if (mode, lom) != want or rec is None:",
     "    if rec is None:"),
    ("state: still in reset accepted",
     "        if cmd.startswith(\"LOMREG \") and field(r, \"reset\") != \"0\":", "        if False:"),
    ("state: a failed MODE accepted",
     "        if cmd.startswith(\"MODE \") and field(r, \"driver\") != \"ESP_OK\":", "        if False:"),
    ("run: the window at 500k", "restart_ms=RESTART_MS, bitrate=rate)",
     "restart_ms=RESTART_MS)"),
    ("run: no restart-ms", "link.set_link(True, restart_ms=RESTART_MS, bitrate=rate)",
     "link.set_link(True, bitrate=rate)"),
    ("run: counters read before the link is up",
     "        log(\"    \" + link.set_link(True, restart_ms=RESTART_MS, bitrate=rate))\n"
     "        c0 = link.link_counters()\n",
     "        c0 = link.link_counters()\n"
     "        log(\"    \" + link.set_link(True, restart_ms=RESTART_MS, bitrate=rate))\n"),
    ("run: the board set up with the link up",
     "    try:\n        log(\"    \" + link.set_link(False))\n    except link.LinkError as e:\n"
     "        return FAULT, link.link_void(e, t0, time.time())\n", ""),
    ("run: a bad state still windowed", "    if not ok:\n        return FAULT, \"TEST FAULT",
     "    if False:\n        return FAULT, \"TEST FAULT"),
    ("run: the arms go on after a FAULT", "            if out == FAULT:\n                return results\n",
     ""),
    ("run: the socket takes echoes", "receive_own_messages=False, error_frames=False)",
     "receive_own_messages=True, error_frames=True)"),
    ("arms: the can-fail expected clean", "     \"can-fail\", [\"MODE normal\", \"LOMREG norec\"], True),",
     "     \"can-fail\", [\"MODE normal\", \"LOMREG norec\"], False),"),
    ("board: the '?' kept in the reply's tag", "        want = \"@\" + line.split()[0].rstrip(\"?\")",
     "        want = \"@\" + line.split()[0]"),
    ("board: an @ERR not taken as the reply",
     "            if ln.startswith(want) or ln.startswith(\"@ERR\"):",
     "            if ln.startswith(want):"),
    ("send: a refused send raised", "        except Exception:                                  # noqa: BLE001\n            ref += 1",
     "        except ZeroDivisionError:\n            ref += 1"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    ap.add_argument("--board", choices=("vport",), default="vport",
                    help="vport: the vport_bench image (the judge and its can-fail)")
    ap.add_argument("--port", help="the X2's USB serial port")
    ap.add_argument("--elf", help="the vport_bench ELF the board must be running")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if not (a.out and a.port and a.elf):
        ap.error("--port, --elf and --out are required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
