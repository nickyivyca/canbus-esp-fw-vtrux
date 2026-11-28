"""shared/twai_port T19 on the X2's S3, the commands with no gen-inhibit
counterpart (the charge-interposer reviewer's stage (b) batch 1 step 2,
2026-11-27/28): T14, T15 and T17 (the kept-driver switch), T12 (the restart
stop, with the S3's stall of the other core) judged; reset mode on a frame
left in the buffer measured. The image is the interposer's s3probe variant,
whose T19 app takes one serial command per line and answers one JSON line
(shared/twai_port test_app/README.md, "The T19 app on the S3: serial
commands"). The txabort phases and m1-m3 are not here: they wait for the
probe analysis to move into shared/twai_port (the reviewer, 2026-11-27).

THE RIG. The vehicle segment holds the X2's TWAI port (CAN1), the Kvaser
and the PCAN, and nothing else (the same rig as bench_lom_errflag).
  ack    the PCAN open at 500k: an acknowledging node, and the stimulus
         sender; the Kvaser up at 500k as the witness (it acknowledges too),
         reading every frame and error frame with its receive time
  noack  the Kvaser's link down and the PCAN closed: no node acknowledges,
         so a frame the board sends is resent by its controller until
         something takes it out of the buffer
Every arm sets its rig before its first command.

THE ARMS, in this order (ARMS):
  info          noack. info and regs answer 200; info's idf is v5.4.2, the
                version T19 names, and no driver is installed at boot
                (port_core -1). A premise: anything else is VOID
  switch        ack. T14, T15: switch to=lom, regs, switch to=normal.
                Each switch's report: result 0, the mode asked for,
                rec_written and rec_read_back 128 into listen-only and 0 out
                of it; regs in listen-only: the controller's LOM bit set and
                the driver still installed (T14 keeps it)
  held          noack, then ack. T17: switch to=lom n=8 with no node
                acknowledging, so the first frame is in the buffer and seven
                are queued behind it. All 8 queued; the report: held 8,
                head_aborted, abort_timed_out false, driver_tx_stuck false;
                the hook gives each frame once, ID 0x7FD, tags 0x7100 to
                0x7107; tx_discarded_switch grows by 8 and tx_failed by 0
                (each frame counted once), mtx_after 0. Then the rig goes to
                ack, switch to=normal, and the Kvaser must see no 0x7FD
                frame in the next HELD_WATCH_S: a removed frame is never
                sent (T17: they cannot be sent in listen-only; the switch
                removes them)
  switchloop    ack, the PCAN sending 0x5A6 every LOOP_PERIOD_S. T14, T15:
                switchloop k=LOOP_K period_ms=LOOP_MS. Every switch result 0
                with rec_read_back 128 into listen-only and 0 out of it and
                driver_tx_stuck false; rx_lost, rx_lost_queue_full,
                rx_lost_overrun and rx_lost_restart 0 (no queue discarded,
                no frame lost at a switch); tx_discarded_switch 0. A premise:
                every window received frames, so each switch had traffic
                around it
  resetprobe-*  noack. MEASURED, not judged: reset mode entered on a frame
                left in the buffer (at=ts0 or ts1, leave=normal or lom,
                abort=0 or 1). Premise: entered, attempts >= att, no
                wait_timeout. Each answer is recorded whole. A line
                RELEASED WITHOUT AN INTERRUPT is printed when, after the
                wait, the buffer reads free (st_wait TBS) while the driver
                still counts a frame to send (mtx_wait > 0) and no transmit
                alert came (tx_alerts "none"): the reviewer asked to hear of
                that at once (driver_tx_stuck)
  restart-*     T12 on the S3: restart how=restart with load idle (ack),
                queued n=4 (noack), noack (noack), core (ack), flood (ack,
                the PCAN flooding 0x100) and how=panic load=noack (noack).
                Before each: g4 clear=1, then g4 must read the stall record
                not valid (so a stale record cannot pass). After the answer
                line the board must boot again (its @HELLO line) within
                BOOT_S, then g4:
                  reset_reason 3 (software) for restart, 4 (panic) for panic;
                  g4 not null, entries one more than before (G4 counts every
                  entry), did_twai 1 (the stop ran), in_reset 0 (the bus was
                  up), wait_us <= 1000 (T12's 1 ms bound), and timed_out 0
                  for idle;
                  the stall record valid, other = 1 - core, and the other
                  core stalled (stall_called 1 or stalled_at_entry 1), and
                  entered_reset 1 (T12: reset mode entered);
                  queued and noack: tec > 0 (the premise: the board's frames
                  went unacknowledged, so the buffer was in use);
                  ack loads: the Kvaser saw no error frame from the command
                  to the boot (no frame cut on the wire), and for core no
                  0x7FB frame after the boot line before g4 (the other
                  core's sender did not outlive the stop)

VERDICT. PASS: every judged arm passed, every resetprobe arm measured. FAIL:
a judged check did not hold; each line says which (observed vs spec). VOID
(exit 3): a premise not met (the port, the image, a rig step, a missing
answer, an arm's premise); the arms after it are not run.

    python3.13 -B bench_t19.py --self-test
    python3.13 -B bench_t19.py --can-fail
    python3.13 -B bench_t19.py --port /dev/<X2> --elf <interposer_s3probe.elf> \\
        --out <file.txt> [--arms info,switch,...]
    (or --elf-sha256 <hex> in place of --elf, as the bench suite passes it)

Tester, 2026-11-27 (PST).
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

BITRATE = 500000
RESTART_MS = 100
IDF = "v5.4.2"
HELD_N = 8
HELD_ID = 0x7FD
HELD_TAG0 = 0x7100
HELD_WATCH_S = 0.5
LOOP_K = 20
LOOP_MS = 100
LOOP_ID = 0x5A6
LOOP_PERIOD_S = 0.002
CORE_ID = 0x7FB
FLOOD_ID = 0x100
BOOT_S = 15.0
WAIT_BOUND_US = 1000
TBS = 4
RESET_SW, RESET_PANIC = 3, 4

PASS, FAIL, VOID, MEASURED = "PASS", "FAIL", "VOID", "MEASURED"

RESETPROBE = [
    ("resetprobe-ts0-a1", "at=ts0 att=1"),
    ("resetprobe-ts0-a3", "at=ts0 att=3"),
    ("resetprobe-ts1-d20", "at=ts1 att=1 delay_us=20"),
    ("resetprobe-ts1-d100", "at=ts1 att=1 delay_us=100"),
    ("resetprobe-lom", "at=ts0 att=1 leave=lom"),
    ("resetprobe-lom-abort", "at=ts0 att=1 leave=lom abort=1"),
]
# (name, how, load, extra arguments, rig)
RESTARTS = [
    ("restart-idle", "restart", "idle", "", "ack"),
    ("restart-queued", "restart", "queued", " n=4", "noack"),
    ("restart-noack", "restart", "noack", "", "noack"),
    ("restart-core", "restart", "core", " period_us=1000", "ack"),
    ("restart-flood", "restart", "flood", "", "ack"),
    ("panic-noack", "panic", "noack", "", "noack"),
]
ARM_NAMES = (["info", "switch", "held", "switchloop"] + [r[0] for r in RESETPROBE]
             + [r[0] for r in RESTARTS])


# ---- pure parts -------------------------------------------------------------

def parse_answer(line, cmd):
    """The T19 app's JSON answer to `cmd`, or None if `line` is not it."""
    s = (line or "").strip()
    if not s.startswith("{"):
        return None
    try:
        a = json.loads(s)
    except ValueError:
        return None
    if not isinstance(a, dict) or a.get("cmd") != cmd:
        return None
    return a


def hello_fields(line):
    """@HELLO s3probe version=.. elf_sha256=.. idf=.. -> {key: value}, or
    None if the line is not a HELLO line."""
    if not (line or "").startswith("@HELLO"):
        return None
    out = {}
    for f in line.split()[1:]:
        if "=" in f:
            k, v = f.split("=", 1)
            out[k] = v
    return out


def ok200(a):
    return isinstance(a, dict) and a.get("status") == 200


def judge_info(info, regs):
    """-> (outcome, [lines]). A premise only: VOID or PASS."""
    if not ok200(info):
        return VOID, ["info did not answer 200: %s" % short(info)]
    if not ok200(regs):
        return VOID, ["regs did not answer 200: %s" % short(regs)]
    if not str(info.get("idf", "")).startswith(IDF):
        return VOID, ["the image was built with ESP-IDF %r, T19 names %s"
                      % (info.get("idf"), IDF)]
    if info.get("port_core") != -1 or info.get("installed") not in (False, 0):
        return VOID, ["a driver is installed before any command (port_core %r, installed %r)"
                      % (info.get("port_core"), info.get("installed"))]
    return PASS, ["info: build %s, idf %s, reset_reason %s; regs answered"
                  % (info.get("build"), info.get("idf"), info.get("reset_reason"))]


def switch_report_lines(a, to, rec):
    """The checks of one switch answer's report: [failure lines]."""
    bad = []
    if not ok200(a):
        return ["switch to=%s did not answer 200: %s" % (to, short(a))]
    r = a.get("report") or {}
    if r.get("result") != 0:
        bad.append("switch to=%s: result %r, not 0 (ESP_OK)" % (to, r.get("result")))
    want_mode = {"lom": ("lom", "listen", "listen_only", "listen-only"),
                 "normal": ("normal",)}[to]
    if str(a.get("mode")) not in want_mode:
        bad.append("switch to=%s: mode %r after it" % (to, a.get("mode")))
    if r.get("rec_written") != rec or r.get("rec_read_back") != rec:
        bad.append("switch to=%s: rec_written %r, rec_read_back %r; T15 wants %d both"
                   % (to, r.get("rec_written"), r.get("rec_read_back"), rec))
    return bad


def judge_switch(lom, regs, normal):
    """T14, T15. -> (outcome, [lines])."""
    for a, c in ((lom, "switch to=lom"), (regs, "regs"), (normal, "switch to=normal")):
        if a is None:
            return VOID, ["no answer to %s" % c]
    bad = switch_report_lines(lom, "lom", 128) + switch_report_lines(normal, "normal", 0)
    if not ok200(regs):
        bad.append("regs in listen-only did not answer 200: %s" % short(regs))
    else:
        if regs.get("lom") not in (1, True):
            bad.append("regs in listen-only: the LOM bit reads %r, T14 reaches "
                       "listen-only through the controller's registers" % regs.get("lom"))
        if regs.get("drv") is None:
            bad.append("regs in listen-only: the driver is not installed; T14 keeps it")
    if bad:
        return FAIL, bad
    return PASS, ["into listen-only: rec %d read back, LOM set, driver kept; out: rec %d"
                  % (lom["report"]["rec_read_back"], normal["report"]["rec_read_back"])]


def judge_held(before, held, ids_after):
    """T17. before: the last switch answer before this one (for the counter
    growth); held: the switch to=lom n=8 answer; ids_after: the frame IDs the
    Kvaser saw after the switch back to normal. -> (outcome, [lines])."""
    if held is None or before is None:
        return VOID, ["no answer to the switch with frames queued"]
    if not ok200(held):
        return FAIL, ["switch to=lom n=%d did not answer 200: %s" % (HELD_N, short(held))]
    if held.get("queued") != HELD_N or held.get("queue_refused") not in (0, None):
        return VOID, ["queued %r, queue_refused %r: the %d frames were not all queued, "
                      "so the hold tests less than it should"
                      % (held.get("queued"), held.get("queue_refused"), HELD_N)]
    bad = switch_report_lines(held, "lom", 128)
    r = held.get("report") or {}
    if r.get("held") != HELD_N:
        bad.append("report.held %r, not %d: every frame removed is counted"
                   % (r.get("held"), HELD_N))
    if r.get("head_aborted") is not True:
        bad.append("report.head_aborted %r: the frame in the buffer (no node "
                   "acknowledging) was not aborted" % r.get("head_aborted"))
    if r.get("abort_timed_out") is not False:
        bad.append("report.abort_timed_out %r" % r.get("abort_timed_out"))
    if r.get("driver_tx_stuck") is not False:
        bad.append("report.driver_tx_stuck %r: the buffer read empty while the "
                   "driver still counted a frame" % r.get("driver_tx_stuck"))
    hook = held.get("hook") or []
    want = [[HELD_ID, HELD_TAG0 + i] for i in range(HELD_N)]
    got = sorted([list(h) for h in hook], key=lambda h: h[1] if len(h) > 1 else -1)
    if got != want or held.get("hook_lost") not in (0, None):
        bad.append("hook %s (hook_lost %r): T17 wants each of the %d frames once, "
                   "ID 0x%03X, tags 0x%04X..0x%04X"
                   % (hook, held.get("hook_lost"), HELD_N, HELD_ID, HELD_TAG0,
                      HELD_TAG0 + HELD_N - 1))
    dd = delta(before, held, "tx_discarded_switch")
    df = delta(before, held, "tx_failed")
    if dd != HELD_N:
        bad.append("tx_discarded_switch grew by %r, not %d" % (dd, HELD_N))
    if df != 0:
        bad.append("tx_failed grew by %r: a frame the switch removed counted as "
                   "failed too (T17: each frame counted once)" % df)
    if held.get("mtx_after") != 0:
        bad.append("mtx_after %r, not 0: the driver still holds frames"
                   % held.get("mtx_after"))
    sent = [i for i in ids_after if i == HELD_ID]
    if sent:
        bad.append("the Kvaser saw %d frame(s) 0x%03X after the switch back to normal: "
                   "a removed frame went out" % (len(sent), HELD_ID))
    if bad:
        return FAIL, bad
    return PASS, ["%d held, each handed back once by tag, counted once as discarded "
                  "at a switch, none sent later" % HELD_N]


def delta(a0, a1, key):
    try:
        return int(a1[key]) - int(a0[key])
    except (KeyError, TypeError, ValueError):
        return None


def judge_switchloop(a):
    """T14, T15 over LOOP_K switches. -> (outcome, [lines])."""
    if a is None:
        return VOID, ["no answer to switchloop"]
    if not ok200(a):
        return FAIL, ["switchloop did not answer 200: %s" % short(a)]
    sw = a.get("switches") or []
    win = a.get("rx_windows") or []
    if len(sw) != LOOP_K or len(win) != LOOP_K + 1:
        return VOID, ["%d switches and %d windows reported, %d and %d asked"
                      % (len(sw), len(win), LOOP_K, LOOP_K + 1)]
    if any((w or 0) < 1 for w in win):
        return VOID, ["a window received nothing (%s): no traffic around every "
                      "switch, so the loss counters prove less" % win]
    bad = []
    for i, s in enumerate(sw):
        # [t_us, to_lom, switch_us, result, held, rec_read_back,
        #  status_at_reset, head_aborted, driver_tx_stuck]
        if len(s) < 9:
            bad.append("switch %d: %s has too few fields" % (i, s))
            continue
        to_lom, result, rec, stuck = s[1], s[3], s[5], s[8]
        want = 128 if to_lom in (1, True) else 0
        if result != 0:
            bad.append("switch %d (to %s): result %r" % (i, "lom" if want else "normal", result))
        if rec != want:
            bad.append("switch %d (to %s): rec_read_back %r, T15 wants %d"
                       % (i, "lom" if want else "normal", rec, want))
        if stuck not in (0, False):
            bad.append("switch %d: driver_tx_stuck %r" % (i, stuck))
    for k in ("rx_lost", "rx_lost_queue_full", "rx_lost_overrun", "rx_lost_restart",
              "tx_discarded_switch"):
        if a.get(k) != 0:
            bad.append("%s %r over the run, not 0" % (k, a.get(k)))
    if bad:
        return FAIL, bad
    return PASS, ["%d switches, rec right each time, %d frames received, none lost "
                  "(windows %s)" % (LOOP_K, a.get("rx_frames", -1), win)]


def measure_resetprobe(a, att):
    """-> (outcome, [lines]). MEASURED or VOID, with the release line."""
    if a is None:
        return VOID, ["no answer to resetprobe"]
    if not ok200(a):
        return VOID, ["resetprobe did not answer 200: %s" % short(a)]
    if a.get("entered") not in (1, True) or (a.get("attempts") or 0) < att \
            or a.get("wait_timeout") in (1, True):
        return VOID, ["the premise did not hold: entered %r, attempts %r (att %d), "
                      "wait_timeout %r" % (a.get("entered"), a.get("attempts"), att,
                                           a.get("wait_timeout"))]
    lines = ["entered after %s attempt(s): st_reset 0x%02x mtx_reset %s; after the wait "
             "st_wait 0x%02x mtx_wait %s tx_alerts %s; second frame: err %s, out %s"
             % (a.get("attempts"), int(a.get("st_reset") or 0), a.get("mtx_reset"),
                int(a.get("st_wait") or 0), a.get("mtx_wait"), a.get("tx_alerts"),
                a.get("second_tx_err"), a.get("second"))]
    if released_without_interrupt(a):
        lines.append("RELEASED WITHOUT AN INTERRUPT: the buffer reads free after the wait "
                     "while the driver still counts %s frame(s) and no transmit alert came"
                     % a.get("mtx_wait"))
    return MEASURED, lines


def released_without_interrupt(a):
    return (bool(int(a.get("st_wait") or 0) & TBS) and (a.get("mtx_wait") or 0) > 0
            and a.get("tx_alerts") == "none")


def judge_restart(how, load, cmd_answer, g4_before, booted, g4_after, errors, core_after):
    """T12 on the S3. errors: error frames the Kvaser saw from the command to
    the boot (None: no witness, a noack rig); core_after: 0x7FB frames after
    the boot line (None: not watched). -> (outcome, [lines])."""
    if cmd_answer is None or not ok200(cmd_answer):
        return VOID, ["the restart command did not answer 200: %s" % short(cmd_answer)]
    if not booted:
        return FAIL, ["the board did not boot again (no @HELLO) within %.0f s" % BOOT_S]
    if g4_after is None or not ok200(g4_after):
        return VOID, ["no g4 answer after the boot: %s" % short(g4_after)]
    bad = []
    want_rr = RESET_PANIC if how == "panic" else RESET_SW
    if g4_after.get("reset_reason") != want_rr:
        bad.append("reset_reason %r, not %d" % (g4_after.get("reset_reason"), want_rr))
    g, g0 = g4_after.get("g4"), (g4_before or {}).get("g4")
    if not isinstance(g, dict):
        bad.append("g4 record %r: the guard wrote none" % g)
        g = {}
    else:
        e0 = (g0 or {}).get("entries", 0) if isinstance(g0, dict) else 0
        if g.get("entries") != e0 + 1:
            bad.append("g4 entries %r, before %r: G4 counts every entry"
                       % (g.get("entries"), e0))
        if g.get("did_twai") != 1:
            bad.append("g4 did_twai %r: the TWAI stop did not run" % g.get("did_twai"))
        if g.get("in_reset") != 0:
            bad.append("g4 in_reset %r: the controller was already in reset mode, so the "
                       "stop was not tested on a running bus" % g.get("in_reset"))
        w = g.get("wait_us")
        if not isinstance(w, int) or w > WAIT_BOUND_US:
            bad.append("g4 wait_us %r, over T12's bound of %d us" % (w, WAIT_BOUND_US))
        if load == "idle" and g.get("timed_out") != 0:
            bad.append("g4 timed_out %r on an idle bus" % g.get("timed_out"))
        if load in ("queued", "noack") and not (g.get("tec") or 0) > 0:
            return VOID, ["g4 tec %r: the board's frames were not shown unacknowledged, "
                          "so the stop did not meet a busy buffer" % g.get("tec")]
    s = g4_after.get("stall") or {}
    if s.get("valid") is not True:
        bad.append("the stall record is not valid after the restart: the S3 stop wrote none")
    else:
        if s.get("other") != 1 - (s.get("core") if s.get("core") in (0, 1) else -9):
            bad.append("stall: core %r, other %r" % (s.get("core"), s.get("other")))
        if not (s.get("stall_called") == 1 or s.get("stalled_at_entry") == 1):
            bad.append("stall: the other core was not stalled (stall_called %r, "
                       "stalled_at_entry %r); T12 stalls it first"
                       % (s.get("stall_called"), s.get("stalled_at_entry")))
        if s.get("entered_reset") != 1:
            bad.append("stall: entered_reset %r: reset mode not entered"
                       % s.get("entered_reset"))
    if errors:
        bad.append("the Kvaser saw %d error frame(s) from the command to the boot: a "
                   "frame cut on the wire" % errors)
    if core_after:
        bad.append("the Kvaser saw %d frame(s) 0x%03X after the boot line: the other "
                   "core's sender outlived the stop" % (core_after, CORE_ID))
    if bad:
        return FAIL, bad
    return PASS, ["reset_reason %s; g4 entries %s, wait_us %s, timed_out %s, aborts %s; "
                  "stall core %s other %s called %s at_entry %s"
                  % (g4_after.get("reset_reason"), g.get("entries"), g.get("wait_us"),
                     g.get("timed_out"), g.get("aborts"), s.get("core"), s.get("other"),
                     s.get("stall_called"), s.get("stalled_at_entry"))]


def short(a):
    t = json.dumps(a) if a is not None else "no answer"
    return t if len(t) <= 200 else t[:200] + "..."


def verdict(results, wanted):
    """results: [(name, outcome, lines)] in order; wanted: the arm names asked
    for. -> (rc, lines). VOID anywhere is VOID; else any FAIL is FAIL; else
    every wanted arm must have run."""
    for name, out, lines in results:
        if out == VOID:
            return 3, ["VOID: %s: %s" % (name, "; ".join(lines))]
    fails = ["FAIL: %s: %s" % (n, ln) for n, out, lines in results if out == FAIL
             for ln in lines]
    if fails:
        return 1, fails
    ran = [n for n, _o, _l in results]
    missing = [n for n in wanted if n not in ran]
    if missing:
        return 3, ["VOID: %s not run" % ", ".join(missing)]
    rel = [n for n, out, lines in results if any(ln.startswith("RELEASED") for ln in lines)]
    tail = ("; RELEASED WITHOUT AN INTERRUPT in %s" % ", ".join(rel)) if rel else ""
    return 0, ["PASS: %d judged arm(s) passed, %d resetprobe arm(s) measured%s"
               % (sum(1 for _n, o, _l in results if o == PASS),
                  sum(1 for _n, o, _l in results if o == MEASURED), tail)]


# ---- the bench --------------------------------------------------------------

class Board(object):
    """The T19 app's console: one line in, one JSON line out; boot lines
    (@HELLO) seen on the way are kept."""

    def __init__(self, ser, log):
        self.ser, self.log, self.hellos = ser, log, []
        self.buf = b""

    def _line(self):
        """One whole line, or None. pyserial's readline() returns a part line
        when its timeout runs out, and the app sends a long command's answer
        in pieces (switchloop: its start, then the rest after the work), so
        the bytes are kept until the newline (test_app/README.md; run
        bench_t19_1d4221c9's switchloop had no answer from this)."""
        self.buf += self.ser.readline()
        if b"\n" not in self.buf:
            return None
        b, self.buf = self.buf.split(b"\n", 1)
        ln = b.decode("ascii", "replace").rstrip("\r")
        h = hello_fields(ln)
        if h is not None:
            self.hellos.append((time.monotonic(), h))
            self.log("    board> %s" % ln)
        return ln

    def ask(self, line, timeout=10.0):
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()
        self.log("    > %s" % line)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            ln = self._line()
            if ln is None:
                continue
            a = parse_answer(ln, line)
            if a is not None:
                self.log("    < %s" % ln)
                return a
        self.log("    < (no answer to %s in %.0f s)" % (line, timeout))
        return None

    def hello(self, timeout=5.0):
        """HELLO? -> its fields, or None."""
        self.ser.write(b"HELLO?\n")
        self.ser.flush()
        end = time.monotonic() + timeout
        n = len(self.hellos)
        while time.monotonic() < end:
            self._line()
            if len(self.hellos) > n:
                return self.hellos[-1][1]
        return None

    def wait_boot(self, since, timeout=BOOT_S):
        """True once a @HELLO line came after `since` (monotonic)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(t > since for t, _h in self.hellos):
                return True
            self._line()
        return any(t > since for t, _h in self.hellos)


class Rig(object):
    """The vehicle segment's two adapters, set to ack or noack."""

    def __init__(self, log, link=None, roles=None):
        if link is None:
            import kvaser_link as link
        if roles is None:
            import adapter_roles as roles
        self.link, self.roles, self.log = link, roles, log
        self.kind = None
        self.kv = self.pc = None
        self.seen = []                       # (monotonic, id, is_error)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = self._sender = None
        self.sent = 0

    def set(self, kind):
        self.stop_send()
        self._close()
        if kind == "noack":
            self.log("    rig noack: " + self.link.set_link(False))
        else:
            self.log("    rig ack: " + self.link.set_link(True, restart_ms=RESTART_MS,
                                                          bitrate=BITRATE))
            self.kv = self.link.open_bus(receive_own_messages=False, error_frames=True)
            self.pc = self.roles.pcanfd(BITRATE)
            self._stop.clear()
            self._reader = threading.Thread(target=self._read, daemon=True)
            self._reader.start()
        self.kind = kind

    def _read(self):
        while not self._stop.is_set():
            try:
                m = self.kv.recv(0.1)
            except Exception:                             # noqa: BLE001
                return
            if m is not None:
                with self._lock:
                    self.seen.append((time.monotonic(), m.arbitration_id,
                                      bool(m.is_error_frame)))

    def since(self, t0):
        with self._lock:
            return [s for s in self.seen if s[0] >= t0]

    def send(self, can_id, period_s, flood=False):
        """The PCAN sends can_id every period_s (flood: back to back) until
        stop_send()."""
        import can
        self.sent = 0

        def run():
            seq = 0
            while not self._send_stop.is_set():
                m = can.Message(arbitration_id=can_id, is_extended_id=False,
                                data=seq.to_bytes(4, "little") + b"\xab\x19\x00\x00")
                try:
                    self.pc.send(m, timeout=0.01)
                    self.sent += 1
                    seq += 1
                except Exception:                         # noqa: BLE001
                    pass
                if not flood:
                    time.sleep(period_s)
        self._send_stop = threading.Event()
        self._sender = threading.Thread(target=run, daemon=True)
        self._sender.start()

    def stop_send(self):
        if self._sender is not None:
            self._send_stop.set()
            self._sender.join(2.0)
            self._sender = None

    def _close(self):
        self._stop.set()
        if self._reader is not None:
            self._reader.join(2.0)
            self._reader = None
        for b in (self.kv, self.pc):
            if b is not None:
                try:
                    b.shutdown()
                except Exception:                         # noqa: BLE001
                    pass
        self.kv = self.pc = None

    def close(self):
        self.stop_send()
        self._close()
        try:
            self.log("    rig closed: " + self.link.set_link(False))
        except Exception as e:                            # noqa: BLE001
            self.log("    rig: the Kvaser's link not taken down: %s" % e)


def run_arms(board, rig, log, wanted, clock=time.monotonic, sleep=time.sleep,
             boot_s=BOOT_S):
    """Each wanted arm in ARMS order, stopping at the first VOID. ->
    results [(name, outcome, lines)]."""
    results = []

    def done(name, out, lines):
        for ln in lines:
            log("    %s: %s" % (out, ln))
        results.append((name, out, lines))
        return out != VOID

    def rig_set(kind):
        try:
            rig.set(kind)
            return True
        except Exception as e:                            # noqa: BLE001
            done("rig", VOID, ["the rig did not go to %s: %s: %s" % (kind, type(e).__name__, e)])
            return False
    last_switch = None
    for name in [n for n in ARM_NAMES if n in wanted]:
        log("  arm %s" % name)
        if name == "info":
            if not rig_set("noack"):
                return results
            if not done(name, *judge_info(board.ask("info"), board.ask("regs"))):
                return results
        elif name == "switch":
            if not rig_set("ack"):
                return results
            lom = board.ask("switch to=lom")
            regs = board.ask("regs")
            normal = board.ask("switch to=normal")
            last_switch = normal
            if not done(name, *judge_switch(lom, regs, normal)):
                return results
        elif name == "held":
            if not rig_set("noack"):
                return results
            before = last_switch or board.ask("switch to=normal")
            held = board.ask("switch to=lom n=%d" % HELD_N)
            if not rig_set("ack"):
                return results
            t0 = clock()
            last_switch = board.ask("switch to=normal")
            sleep(HELD_WATCH_S)
            ids = [i for _t, i, err in rig.since(t0) if not err]
            if not done(name, *judge_held(before, held, ids)):
                return results
        elif name == "switchloop":
            if not rig_set("ack"):
                return results
            rig.send(LOOP_ID, LOOP_PERIOD_S)
            sleep(0.2)
            a = board.ask("switchloop k=%d period_ms=%d" % (LOOP_K, LOOP_MS),
                          timeout=LOOP_K * LOOP_MS / 1000.0 + 10)
            rig.stop_send()
            log("    the PCAN sent %d frame(s) 0x%03X" % (rig.sent, LOOP_ID))
            if not done(name, *judge_switchloop(a)):
                return results
        elif name.startswith("resetprobe"):
            args = dict(RESETPROBE)[name]
            if not rig_set("noack"):
                return results
            att = int([w for w in args.split() if w.startswith("att=")][0][4:])
            if not done(name, *measure_resetprobe(board.ask("resetprobe " + args), att)):
                return results
        else:
            _n, how, load, extra, kind = [r for r in RESTARTS if r[0] == name][0]
            if not rig_set(kind):
                return results
            board.ask("g4 clear=1")
            g0 = board.ask("g4")
            if g0 is None or (g0.get("stall") or {}).get("valid") is not False:
                done(name, VOID, ["the stall record not cleared before the restart: %s"
                                  % short(g0)])
                return results
            if load == "flood":
                rig.send(FLOOD_ID, 0, flood=True)
                sleep(0.2)
            t0 = clock()
            a = board.ask("restart how=%s load=%s%s" % (how, load, extra))
            booted = board.wait_boot(t0, boot_s)
            tb = clock()
            rig.stop_send()
            errs = core = None
            if kind == "ack":
                errs = sum(1 for t, _i, err in rig.since(t0) if err and t <= tb)
            g1 = board.ask("g4")
            if kind == "ack" and load == "core":
                core = sum(1 for t, i, err in rig.since(tb) if i == CORE_ID and not err)
            if not done(name, *judge_restart(how, load, a, g0, booted, g1, errs, core)):
                return results
    return results


def elf_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_bench(a):
    out = open(a.out, "w", encoding="utf-8")

    def log(s):
        print(s)
        out.write(s + "\n")
        out.flush()
    wanted = a.arms.split(",") if a.arms else list(ARM_NAMES)
    unknown = [n for n in wanted if n not in ARM_NAMES]
    try:
        log("run at %s; bench_t19 arms %s" % (datetime.datetime.now().isoformat(),
                                              ",".join(wanted)))
        if unknown:
            log("VOID: unknown arm(s) %s" % ",".join(unknown))
            return 3
        import serial_open
        try:
            ser = serial_open.open_quiet(a.port, 115200, 0.2)
        except Exception as e:                            # noqa: BLE001
            log("VOID: the X2's port %s did not open (%s: %s): no arm run"
                % (a.port, type(e).__name__, e))
            return 3
        rig = None
        try:
            board = Board(ser, log)
            h = board.hello()
            want = a.elf_sha256 or elf_sha256(a.elf)
            if (h or {}).get("elf_sha256") != want:
                log("VOID: the board is not running the image whose ELF sha256 is %s "
                    "(HELLO %s): no arm run" % (want, h))
                return 3
            rig = Rig(log)
            rc, lines = verdict(run_arms(board, rig, log, wanted), wanted)
        finally:
            if rig is not None:
                rig.close()
            ser.close()
        for ln in lines:
            log(ln)
        log("RESULT: %s" % {0: "PASS", 1: "FAIL", 3: "VOID"}[rc])
        return rc
    finally:
        out.close()


# ---- self-test --------------------------------------------------------------

class _FakeApp(object):
    """The T19 app's answers as the README gives them, for a board that meets
    the spec; `bad` breaks one thing by name."""

    def __init__(self, bad=None):
        self.bad = set(bad or [])
        self.mode, self.rec, self.discarded, self.failed = "normal", 0, 0, 0
        self.entries, self.stall_valid, self.rig = 0, False, None
        self.rr = 1
        self.lines, self.pending_boot = [], False

    def answer(self, line):
        w = line.split()
        cmd, kv = w[0], dict(x.split("=", 1) for x in w[1:])
        a = {"cmd": line, "core": 1, "t0_us": 1, "status": 200, "t1_us": 2}
        if cmd == "info":
            a.update(build="d6a29e6b", idf="v5.4.1" if "idf" in self.bad else "v5.4.2",
                     port_core=-1, installed=False, mode="normal", reset_reason=self.rr)
        elif cmd == "regs":
            a.update(st=12, lom=0 if "lom-bit" in self.bad else int(self.mode == "lom"),
                     tec=0, rec=self.rec,
                     drv=None if "drv-gone" in self.bad else {"state": 1, "mtx": 0, "mrx": 0})
        elif cmd == "switch":
            to, n = kv["to"], int(kv.get("n", 0))
            rec = 128 if to == "lom" else 0
            if "rec" in self.bad:
                rec = 0
            # with a node acknowledging (the rig ack), the queued frames go
            # out before the switch, so nothing is held
            held = 0 if (self.rig is not None and self.rig.kind == "ack") else n
            hook = [[HELD_ID, HELD_TAG0 + i] for i in range(held)]
            if "hook-dup" in self.bad and held:
                hook[1] = hook[0]
            disc = held
            if "double-count" in self.bad and n:
                self.failed += 1
            self.discarded += disc
            self.mode = to
            a.update(queued=n, queue_refused=0, switch_us=40, mode=to,
                     report={"seq": 1, "result": 0, "held": held, "head_aborted": bool(held),
                             "abort_wait_us": 3, "abort_timed_out": False,
                             "driver_tx_stuck": "stuck" in self.bad,
                             "rec_written": 128 if to == "lom" else 0, "rec_read_back": rec,
                             "tec_read_back": 0},
                     hook=hook, hook_lost=0, st_after=12,
                     mtx_after=0, tx_discarded_switch=self.discarded, tx_failed=self.failed)
        elif cmd == "switchloop":
            k = int(kv["k"])
            sw = [[i * 100000, (i % 2) == 0, 40, 0, 0,
                   (128 if (i % 2) == 0 else 0) if "loop-rec" not in self.bad else 0,
                   12, False, False] for i in range(k)]
            a.update(switches=sw, rx_windows=[0 if "quiet" in self.bad else 50] * (k + 1),
                     rx_frames=50 * (k + 1), rx_lost=1 if "loop-lost" in self.bad else 0,
                     rx_lost_queue_full=0, rx_lost_overrun=0, rx_lost_restart=0,
                     bus_errors=0, tx_discarded_switch=0)
        elif cmd == "resetprobe":
            rel = "release" in self.bad
            a.update(attempts=int(kv.get("att", 1)) + 1, entered=True, freed_before=False,
                     wait_timeout=False, st_reset=0, mtx_reset=1,
                     st_wait=TBS if rel else 0, mtx_wait=1 if rel else 0,
                     tx_alerts="none" if rel else "failed", second_tx_err=0, second=False)
        elif cmd == "g4":
            if kv.get("clear") == "1" and "stale" not in self.bad:
                self.stall_valid = False
            g = None if not self.entries else {
                "entries": self.entries, "twai_clk": 1, "twai_rst": 0, "in_reset": 0,
                "st_entry": 12, "tec": 128, "rec": 0, "did_twai": 1, "st_reset": 12,
                "wait_us": 1200 if "slow" in self.bad else 5, "timed_out": 0,
                "did_stdby": 1, "aborts": 1}
            a.update(reset_reason=self.rr, g4=g,
                     stall={"valid": self.stall_valid, "core": 1, "other": 0,
                            "options0": 0, "sw_cpu_stall": 0, "stalled_at_entry": 0,
                            "stall_called": 0 if "no-stall" in self.bad else 1,
                            "entered_reset": 1})
        elif cmd == "restart":
            a.update(how=kv["how"], load=kv.get("load", "idle"), port_core=1,
                     restart_at_us=5)
            if "no-boot" not in self.bad:
                self.entries += 1
                self.stall_valid = True
                self.rr = RESET_PANIC if kv["how"] == "panic" else RESET_SW
                self.pending_boot = True
        return a


class _FakeSer(object):
    def __init__(self, app):
        self.app, self.out = app, []

    def write(self, b):
        line = b.decode("ascii").strip()
        if line == "HELLO?":
            self.out.append("@HELLO s3probe version=x elf_sha256=%s idf=v5.4.2" % ("ab" * 32))
            return
        self.out.append(json.dumps(self.app.answer(line)))
        if self.app.pending_boot:
            self.app.pending_boot = False
            self.out.append("ESP-ROM:esp32s3")
            self.out.append("@HELLO s3probe version=x elf_sha256=%s idf=v5.4.2" % ("ab" * 32))

    def flush(self):
        pass

    def readline(self):
        return (self.out.pop(0) + "\r\n").encode("ascii") if self.out else b""


class _FakeRig(object):
    """The rig's calls logged; the Kvaser sees what `seen` gives per arm."""

    def __init__(self, extra=None, fail=None):
        self.calls, self.extra, self.fail = [], extra or {}, fail
        self.sent = 0
        self.kind = None

    def set(self, kind):
        self.calls.append(("set", kind))
        if self.fail == kind:
            raise RuntimeError("canctl refused")
        self.kind = kind

    def send(self, can_id, period_s, flood=False):
        self.calls.append(("send", can_id, flood))
        self.sent = 1000

    def stop_send(self):
        self.calls.append(("stop",))

    def since(self, t0):
        return list(self.extra.get(self.kind, []))


def self_test():
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)

    def run(bad=None, wanted=None, rig=None):
        app = _FakeApp(bad)
        b = Board(_FakeSer(app), lambda s: None)
        r = rig or _FakeRig()
        app.rig = r
        res = run_arms(b, r, lambda s: None, wanted or list(ARM_NAMES),
                       sleep=lambda s: None, boot_s=0.3)
        return verdict(res, wanted or list(ARM_NAMES)), res, r

    (rc, lines), res, r = run()
    check(rc == 0 and lines[0].startswith("PASS"), "a board that meets the spec -> PASS: %s" % lines)
    check([n for n, _o, _l in res] == list(ARM_NAMES), "every arm ran, in order")
    check(sum(1 for _n, o, _l in res if o == MEASURED) == len(RESETPROBE),
          "every resetprobe arm MEASURED, none judged")
    sets = [c[1] for c in r.calls if c[0] == "set"]
    check(sets[:4] == ["noack", "ack", "noack", "ack"], "rigs: info noack, switch ack, held "
          "noack then ack: %s" % sets[:4])
    check(("send", LOOP_ID, False) in r.calls and ("send", FLOOD_ID, True) in r.calls,
          "the switchloop stimulus and the restart flood sent")
    for bad, want in (("lom-bit", "switch: regs in listen-only: the LOM bit"),
                      ("drv-gone", "switch: regs in listen-only: the driver is not installed"),
                      ("rec", "switch: switch to=lom: rec_written 128, rec_read_back 0"),
                      ("hook-dup", "held: hook"),
                      ("double-count", "held: tx_failed grew by 1"),
                      ("stuck", "held: report.driver_tx_stuck True"),
                      ("loop-rec", "switchloop: switch 0 (to lom): rec_read_back 0"),
                      ("loop-lost", "switchloop: rx_lost 1"),
                      ("slow", "restart-idle: g4 wait_us 1200"),
                      ("no-stall", "restart-idle: stall: the other core was not stalled")):
        (rc, lines), _res, _r = run([bad])
        check(rc == 1 and any(ln.startswith("FAIL: " + want) for ln in lines),
              "%s -> FAIL naming it: %s" % (bad, lines[:2]))
    for bad, want in (("idf", "VOID: info: the image was built with ESP-IDF"),
                      ("quiet", "VOID: switchloop: a window received nothing"),
                      # the first restart is the first to leave a valid
                      # record, so a clear that does nothing shows at the second
                      ("stale", "VOID: restart-queued: the stall record not cleared"),
                      ("no-boot", "FAIL: restart-idle: the board did not boot again")):
        (rc, lines), res, _r = run([bad])
        check(lines[0].startswith(want), "%s -> %s: %s" % (bad, want.split(":")[0], lines[0]))
    (rc, lines), res, _r = run(["idf"])
    check(rc == 3 and len(res) == 1, "a VOID stops the arms after it")
    (rc, lines), res, _r = run(["release"])
    check(rc == 0 and "RELEASED WITHOUT AN INTERRUPT" in lines[0]
          and all(any(ln.startswith("RELEASED") for ln in l) for n, _o, l in res
                  if n.startswith("resetprobe")),
          "the buffer free with the driver still counting and no alert -> the "
          "RELEASED line in each resetprobe arm and in the verdict: %s" % lines)
    (rc, lines), _res, _r = run(rig=_FakeRig(extra={"ack": [(0, HELD_ID, False)]}),
                                wanted=["switch", "held"])
    check(rc == 1 and any("held: the Kvaser saw 1 frame(s) 0x7FD" in ln for ln in lines),
          "a held frame on the wire after the switch back -> FAIL: %s" % lines)
    (rc, lines), _res, _r = run(rig=_FakeRig(extra={"ack": [(0, 0, True)]}),
                                wanted=["restart-idle"])
    check(rc == 1 and any("error frame" in ln for ln in lines),
          "an error frame around an ack restart -> FAIL: %s" % lines)
    (rc, lines), _res, _r = run(rig=_FakeRig(extra={"ack": [(0, CORE_ID, False)]}),
                                wanted=["restart-core"])
    check(rc == 1 and any("outlived the stop" in ln for ln in lines),
          "the other core's frame after the boot -> FAIL: %s" % lines)
    (rc, lines), res, _r = run(rig=_FakeRig(fail="ack"), wanted=["switch"])
    check(rc == 3 and "rig did not go to ack" in lines[0], "a rig failure -> VOID: %s" % lines)
    check(verdict([("info", PASS, ["x"])], ["info", "switch"])[0] == 3,
          "an arm asked for and not run -> VOID")

    # judge_restart directly: the premise and the boundaries
    g4b = {"status": 200, "g4": {"entries": 4}}
    g4a = {"status": 200, "reset_reason": RESET_SW,
           "g4": {"entries": 5, "did_twai": 1, "in_reset": 0, "wait_us": WAIT_BOUND_US,
                  "timed_out": 1, "tec": 0, "aborts": 0},
           "stall": {"valid": True, "core": 0, "other": 1, "stall_called": 0,
                     "stalled_at_entry": 1, "entered_reset": 1}}
    ca = {"status": 200}
    check(judge_restart("restart", "flood", ca, g4b, True, g4a, 0, None)[0] == PASS,
          "wait_us at the 1 ms bound, timed out on a flood, the other core already "
          "stalled -> PASS")
    check(judge_restart("restart", "noack", ca, g4b, True, g4a, None, None)[0] == VOID,
          "noack with tec 0 -> VOID (premise)")
    check(judge_restart("panic", "flood", ca, g4b, True, g4a, 0, None)[0] == FAIL,
          "a panic arm with reset_reason software -> FAIL")
    bad_e = dict(g4a, g4=dict(g4a["g4"], entries=6))
    check(judge_restart("restart", "flood", ca, g4b, True, bad_e, 0, None)[0] == FAIL,
          "entries grew by 2 -> FAIL")
    bad_o = dict(g4a, stall=dict(g4a["stall"], other=0))
    check(judge_restart("restart", "flood", ca, g4b, True, bad_o, 0, None)[0] == FAIL,
          "other == core -> FAIL")
    bad_r = dict(g4a, stall=dict(g4a["stall"], entered_reset=0))
    check(judge_restart("restart", "flood", ca, g4b, True, bad_r, 0, None)[0] == FAIL,
          "entered_reset 0 -> FAIL")
    bad_i = dict(g4a, g4=dict(g4a["g4"], in_reset=1))
    check(judge_restart("restart", "flood", ca, g4b, True, bad_i, 0, None)[0] == FAIL,
          "in_reset 1 -> FAIL")
    bad_d = dict(g4a, g4=dict(g4a["g4"], did_twai=0))
    check(judge_restart("restart", "flood", ca, g4b, True, bad_d, 0, None)[0] == FAIL,
          "did_twai 0 -> FAIL")
    idle_to = dict(g4a, g4=dict(g4a["g4"], timed_out=1))
    check(judge_restart("restart", "idle", ca, g4b, True, idle_to, 0, None)[0] == FAIL,
          "timed out on an idle bus -> FAIL")

    # parse_answer and hello_fields
    check(parse_answer('{"cmd":"regs","status":200}', "regs") == {"cmd": "regs", "status": 200},
          "parse_answer: its own answer")
    check(parse_answer('{"cmd":"info","status":200}', "regs") is None,
          "parse_answer: another command's answer is not this one's")
    check(parse_answer("garbage {", "regs") is None, "parse_answer: not JSON")
    check(hello_fields("@HELLO s3probe elf_sha256=ab")["elf_sha256"] == "ab",
          "hello_fields reads elf_sha256")

    # Board reads to the newline: pyserial's readline() gives a part line at
    # its timeout, and the app sends a long answer in pieces
    class _PieceSer(object):
        def __init__(self, parts):
            self.parts = list(parts)

        def write(self, b):
            pass

        def flush(self):
            pass

        def readline(self):
            return self.parts.pop(0) if self.parts else b""
    ans = '{"cmd":"switchloop k=2","status":200,"switches":[]}'
    pb = Board(_PieceSer([ans[:20].encode("ascii"), b"", b"",
                          ans[20:].encode("ascii") + b"\r\n"]), lambda s: None)
    got = pb.ask("switchloop k=2", timeout=0.5)
    check(got is not None and got.get("status") == 200,
          "Board.ask: an answer sent in pieces is read whole: %s" % got)
    pb = Board(_PieceSer([b"@HELLO s3probe elf_sha256=ab\r\n" + ans.encode("ascii") + b"\r\n"]),
               lambda s: None)
    got = pb.ask("switchloop k=2", timeout=0.5)
    check(got is not None and len(pb.hellos) == 1,
          "Board.ask: two lines in one read are both taken: %s, %d hello(s)" % (got, len(pb.hellos)))

    # measure_resetprobe premise
    check(measure_resetprobe({"status": 200, "entered": True, "attempts": 0}, 1)[0] == VOID,
          "resetprobe with fewer attempts than att -> VOID")

    print("\n%d check(s) failed" % len(fails) if fails else "\nbench_t19 self-test: all OK")
    return 1 if fails else 0


# --can-fail (script_can_fail.py): each edit must make --self-test fail
MUTANTS = [
    ("board: a part line taken as a line",
     "        if b\"\\n\" not in self.buf:\n            return None\n"
     "        b, self.buf = self.buf.split(b\"\\n\", 1)\n",
     "        b, self.buf = self.buf, b\"\"\n        if not b:\n            return None\n"),
    ("board: the rest of a read dropped",
     "        b, self.buf = self.buf.split(b\"\\n\", 1)\n",
     "        b, self.buf = self.buf.split(b\"\\n\", 1)[0], b\"\"\n"),
    ("info: the IDF not checked", "    if not str(info.get(\"idf\", \"\")).startswith(IDF):",
     "    if False:"),
    ("switch: the LOM bit not checked", "        if regs.get(\"lom\") not in (1, True):",
     "        if False:"),
    ("switch: the driver kept not checked", "        if regs.get(\"drv\") is None:",
     "        if False:"),
    ("switch: rec_read_back not checked",
     "    if r.get(\"rec_written\") != rec or r.get(\"rec_read_back\") != rec:",
     "    if r.get(\"rec_written\") != rec:"),
    ("held: the hook not checked",
     "    if got != want or held.get(\"hook_lost\") not in (0, None):", "    if False:"),
    ("held: tx_failed growth not checked", "    if df != 0:", "    if False:"),
    ("held: driver_tx_stuck not checked",
     "    if r.get(\"driver_tx_stuck\") is not False:", "    if False:"),
    ("held: the witness not read", "    if sent:\n", "    if False:\n"),
    ("loop: rec not checked", "        if rec != want:\n", "        if False:\n"),
    ("loop: loss counters not checked", "        if a.get(k) != 0:", "        if False:"),
    ("loop: a quiet window judged", "    if any((w or 0) < 1 for w in win):", "    if False:"),
    ("resetprobe: the release never flagged",
     "    if released_without_interrupt(a):", "    if False:"),
    ("restart: the wait bound loosened", "w > WAIT_BOUND_US:", "w > WAIT_BOUND_US * 2:"),
    ("restart: the stall not checked",
     "        if not (s.get(\"stall_called\") == 1 or s.get(\"stalled_at_entry\") == 1):",
     "        if False:"),
    ("restart: entered_reset not checked", "        if s.get(\"entered_reset\") != 1:",
     "        if False:"),
    ("restart: entries not counted", "        if g.get(\"entries\") != e0 + 1:",
     "        if False:"),
    ("restart: in_reset not checked", "        if g.get(\"in_reset\") != 0:", "        if False:"),
    ("restart: did_twai not checked", "        if g.get(\"did_twai\") != 1:", "        if False:"),
    ("restart: the reset reason not checked",
     "    if g4_after.get(\"reset_reason\") != want_rr:", "    if False:"),
    ("restart: other == core accepted",
     "        if s.get(\"other\") != 1 - (s.get(\"core\") if s.get(\"core\") in (0, 1) else -9):",
     "        if False:"),
    ("restart: the noack premise not checked",
     "        if load in (\"queued\", \"noack\") and not (g.get(\"tec\") or 0) > 0:",
     "        if False:"),
    ("restart: idle timeout accepted", "        if load == \"idle\" and g.get(\"timed_out\") != 0:",
     "        if False:"),
    ("restart: error frames not judged", "    if errors:\n", "    if False:\n"),
    ("restart: the other core's sender not judged", "    if core_after:\n", "    if False:\n"),
    ("restart: no boot accepted", "    if not booted:\n", "    if False:\n"),
    ("restart: a stale stall record accepted",
     "            if g0 is None or (g0.get(\"stall\") or {}).get(\"valid\") is not False:",
     "            if g0 is None:"),
    ("verdict: VOID passes", "        if out == VOID:\n", "        if False:\n"),
    ("verdict: a missing arm passes", "    if missing:\n", "    if False:\n"),
    ("run: the arms go on after a VOID", "        return out != VOID", "        return True"),
    ("held: rig ack before the switch with frames",
     "            before = last_switch or board.ask(\"switch to=normal\")\n"
     "            held = board.ask(\"switch to=lom n=%d\" % HELD_N)\n"
     "            if not rig_set(\"ack\"):\n                return results\n",
     "            if not rig_set(\"ack\"):\n                return results\n"
     "            before = last_switch or board.ask(\"switch to=normal\")\n"
     "            held = board.ask(\"switch to=lom n=%d\" % HELD_N)\n"),
    ("parse: another command's answer taken",
     "    if not isinstance(a, dict) or a.get(\"cmd\") != cmd:", "    if not isinstance(a, dict):"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--can-fail", action="store_true",
                    help="run the self-test under each of MUTANTS")
    ap.add_argument("--port", help="the X2's USB serial port")
    ap.add_argument("--elf", help="the s3probe ELF the board must be running")
    ap.add_argument("--elf-sha256", help="or that ELF's sha256 (the suite's image identity)")
    ap.add_argument("--arms", help="comma-separated arm names (default: all, in order)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    if not (a.out and a.port and (a.elf or a.elf_sha256)):
        ap.error("--port, --elf or --elf-sha256, and --out are required")
    return run_bench(a)


if __name__ == "__main__":
    sys.exit(main())
