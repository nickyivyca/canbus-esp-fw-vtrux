#!/usr/bin/env python3
"""Does `x2_hold.py pins` leave a running X2 image running?

The measured case for step 1 of the chain runner's X2 identify (suite_gen_inhibit.py),
which reads the pins with esptool --before no_reset on whatever the X2 runs (the
charge-interposer tester's suggestion, 2026-10-19: one measured case before relying on
it). Bench only, under the lock, X2 on its own image.

    py -3.14 -B gen-inhibit/tools/bench/x2_pins_noreset_check.py

Run from the reverse-it root. Version 2 (2026-10-19). Version 1 read the uptime on
0x7F7 and could not pass: 756d6714 is silent on CAN1 after its power-up round
(bench-holder.md), so a running X2 sends no 0x7F7. This version follows the
charge-interposer tester's measured figures on 756d6714: a reset sends the power-up
round (0x7F4-0x7F7, once each) about 0.32 s later, and the round waits up to ~50 ms for
an ACK. So the Kvaser is held open in NORMAL mode (ACKing) for the whole check, and:

  1. serial before: COM3 read passively (DTR/RTS low, nothing written) for --serial
     seconds (default 11, so at least two of the 5 s status lines); the app's 1 Hz
     "diag st=" line, the status line's "t=Ns" (seconds since boot) and any boot banner
     ("rst:0x.. (", "Vtrux charge interposer") are kept.
  2. CAN before: --quiet seconds with no 0x7F4-0x7F7 (a board mid-round is no premise).
  3. `x2_hold.py pins`.
  4. CAN from the start of the pin read to --after seconds past its end (default 3;
     boot plus round is ~0.32 s; the pin read itself takes seconds, so a reset in it
     would send its round before the read ends).
  5. serial after, as in 1.
  6. positive control: esptool chip-id with the default reset and a hard reset after
     (the auto-reset check), then --after seconds of CAN, which must carry the round.

    VOID          the control reset's round did not reach the Kvaser: the witness or
                  the window is broken, so silence in 4 means nothing
    INCONCLUSIVE  CAN not quiet in 2, a boot banner in 1, or no "diag st=" / "t=" in 1
    FAIL          a round frame in 3-4 (a reset); a boot banner in 5; no "diag st=" or no "t=" in 5 (the app
                  not heard: halted, or reset into ROM and held there, which sends no
                  round); or a "t=" in 5 below the last one in 1
    PASS          otherwise

Writes x2_pins_noreset_<stamp>.json in GI_RUNS_DIR or runs/. Gen-inhibit tester.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import bench_held  # noqa: E402  the bench lock is the bench log (2026-10-25)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
ROUND = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
DIAG_RX = re.compile(r"\bdiag st=")
T_RX = re.compile(r"\bt=(\d+)s\b")
BANNER_RX = re.compile(r"rst:0x[0-9a-fA-F]+ \(|Vtrux charge interposer")


def serial_facts(lines):
    """(diag line seen, [every t= value in order], boot banner seen) from the lines of one
    serial read. The first line of a read is often the tail of a line cut at the start;
    whole-word matches keep a fragment from reading as a value."""
    ts = [int(m.group(1)) for l in lines for m in T_RX.finditer(l)]
    return any(DIAG_RX.search(l) for l in lines), ts, any(BANNER_RX.search(l) for l in lines)


def judge(quiet, after, control, ser_before, ser_after):
    """(verdict, why). quiet, after, control: the CAN ids seen in steps 2, 4 and 6;
    ser_before, ser_after: the lines of steps 1 and 5."""
    missing = [i for i in ROUND if i not in control]
    if missing:
        return "VOID", "the control reset's round did not reach the Kvaser (missing %s)" % (
            ", ".join("0x%03X" % i for i in missing))
    if any(i in ROUND for i in quiet):
        return "INCONCLUSIVE", "the X2 was not quiet on CAN before the pin read"
    diag_b, t_b, boot_b = serial_facts(ser_before)
    if boot_b:
        return "INCONCLUSIVE", "a boot banner on serial before the pin read: the X2 had just reset"
    if not diag_b or not t_b:
        return "INCONCLUSIVE", "no %s on serial before the pin read" % ("diag st= line" if not diag_b else "t=")
    seen = sorted({i for i in after if i in ROUND})
    if seen:
        return "FAIL", "power-up round frames after the pin read (%s): the X2 reset" % (
            ", ".join("0x%03X" % i for i in seen))
    diag_a, t_a, boot_a = serial_facts(ser_after)
    if boot_a:
        return "FAIL", "a boot banner on serial after the pin read: the X2 reset"
    if not diag_a or not t_a:
        return "FAIL", "the app was not heard on serial after the pin read (no %s)" % (
            "diag st= line" if not diag_a else "t=")
    if min(t_a) < t_b[-1]:
        return "FAIL", "t=%ds after the pin read, t=%ds before: the X2 reset" % (min(t_a), t_b[-1])
    return "PASS", "no round after the pin read, t=%ds before, t=%d..%ds after; control round seen" % (
        t_b[-1], min(t_a), max(t_a))


def windows(seen, marks):
    """(quiet, after, control) CAN ids from [(time, id)] and the step marks: quiet from
    "quiet" to "pins"; after from "pins" (a reset during the read sends its round before
    the read ends) to "serial_after"; control from "control" to "end"."""
    between = lambda a, b: [i for t, i in seen if marks[a] <= t < marks[b]]
    return between("quiet", "pins"), between("pins", "serial_after"), between("control", "end")


class Witness(threading.Thread):
    """Drains the bus into (time, id) for the whole check."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.seen, self.stop = bus, [], threading.Event()

    def run(self):
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.1)
            if m is not None and not m.is_error_frame:
                self.seen.append((time.time(), m.arbitration_id))


def read_serial(port, seconds):
    """Lines from a passive read: DTR and RTS set low before the port opens, nothing written."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.2
    s.dtr = False
    s.rts = False
    s.open()
    buf, end = b"", time.time() + seconds
    try:
        while time.time() < end:
            buf += s.read(4096)
    finally:
        s.close()
    return buf.decode("utf-8", "replace").splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--serial", type=float, default=11.0, help="seconds per serial read (t= line every 5 s)")
    ap.add_argument("--quiet", type=float, default=3.0)
    ap.add_argument("--after", type=float, default=3.0)
    a = ap.parse_args()
    ok, who = bench_held.held_by_us()
    if not ok:
        print("REFUSED: the bench lock is not ours: %s" % (who,))
        return 2
    import can
    sys.path.insert(0, HERE)
    import x2_hold
    stamp = time.strftime("%Y%m%dT%H%M%S")
    bus = can.Bus(interface="kvaser", channel=0, bitrate=500000)
    w = Witness(bus)
    w.start()
    marks = {}
    try:
        ser_before = read_serial(a.port, a.serial)
        marks["quiet"] = time.time()
        time.sleep(a.quiet)
        marks["pins"] = time.time()
        p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "x2_hold.py"), "pins", "--port", a.port],
                           capture_output=True, text=True, env=dict(os.environ, GI_RUNS_DIR=RUNS))
        marks["after"] = time.time()
        time.sleep(a.after)
        marks["serial_after"] = time.time()
        ser_after = read_serial(a.port, a.serial)
        marks["control"] = time.time()
        crc, ctxt = x2_hold.esptool(a.port, "default_reset", "hard_reset", "chip-id")
        marks["control_after"] = time.time()
        time.sleep(a.after)
        marks["end"] = time.time()
    finally:
        w.stop.set()
        w.join(2)
        bus.shutdown()
    quiet, after, control = windows(w.seen, marks)
    verdict, why = judge(quiet, after, control, ser_before, ser_after)
    pins = (p.stdout + p.stderr).strip().splitlines()
    ids = lambda xs: ["0x%03X" % i for i in xs]
    rec = {"when": stamp, "version": 2, "verdict": verdict, "why": why,
           "can_quiet": ids(quiet), "can_after_pins": ids(after),
           "can_control": ids(control),
           "serial_before": serial_facts(ser_before), "serial_after": serial_facts(ser_after),
           "serial_before_tail": ser_before[-8:], "serial_after_tail": ser_after[-8:],
           "pins_rc": p.returncode, "pins_out": pins[-3:], "control_rc": crc,
           "control_tail": ctxt.strip().splitlines()[-3:],
           "marks": {k: round(v - marks["quiet"], 3) for k, v in marks.items()},
           "args": vars(a)}
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, "x2_pins_noreset_%s.json" % stamp)
    with open(path, "x", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    print("%s: %s (pins rc %s; control rc %s, %d frames)" % (verdict, why, p.returncode, crc, len(control)))
    print("wrote", path)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
