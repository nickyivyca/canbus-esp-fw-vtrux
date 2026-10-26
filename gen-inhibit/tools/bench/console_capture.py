"""WiCAN console capture that SURVIVES a chip reset: reopens the port and keeps logging.

Gen-inhibit tester, 2026-10-08, for the reset-rate measurement. The WiCAN's
only console is USB-Serial-JTAG (COM4 on NICKY-XPS; its USB serial number is
its MAC). That USB device resets with the chip, so the port drops
and re-enumerates, and serial_boot_log.py -- which opens once -- stops at the
drop and never sees the ROM banner ("rst:0x..") or a panic line that follows.
This class records the drop and the re-open as events and keeps reading.

WHAT A QUIET CONSOLE MEANS: NOTHING. The firmware turns logging off after
startup, there is no UART and no core dump, and a panic or brownout line races
the port dropping. So the capture is evidence only when it CATCHES something;
a reset is counted from the device's post-state, never from the console.
Whether this capture catches a ROM banner at all is measured by --selftest,
and that result bounds how much any later quiet console could ever mean.

ATTACH WITHOUT RESET: DTR and RTS are deasserted before open (pyserial asserts
both by default, and on the C3 they drive reset/boot-select), as in
serial_boot_log.py. Lines are redacted with redact_secrets before they are
kept, because the boot log prints the device's config including passwords.

  py -3.14 gen-inhibit/tools/bench/console_capture.py --selftest
      opens COM4, puts the WiCAN in mode 3, pulses RTS for 200 ms (esptool's
      USB-JTAG hard-reset timing), and reports: did the post-state show a reset
      (mode 0, tx_ok 0, drv null), did the port drop and come back, and did any
      ROM banner line arrive. Writes runs/console_selftest_<stamp>.json.
"""
import argparse
import json
import os
import re
import sys
import threading
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact_secrets import redact                               # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()
BANNER_RE = re.compile(r"rst:0x[0-9a-fA-F]+|ESP-ROM|Brownout|Guru Meditation|"
                       r"panic|abort\(\)|Backtrace|boot:0x", re.I)


class ConsoleCapture(threading.Thread):
    """Reads the port; on any serial error closes, retries every 50 ms."""

    def __init__(self, port="COM4", baud=115200):
        super().__init__(daemon=True)
        self.port, self.baud = port, baud
        self.stop_flag = threading.Event()
        self.lines = []        # (perf, text)
        self.events = []       # (perf, "open" / "lost: ..." / "reopened")
        self.lock = threading.Lock()
        self.ser = None

    def _open(self):
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = self.port, self.baud, 0.1
        s.dtr = False
        s.rts = False
        s.open()
        s.dtr = False
        s.rts = False
        return s

    def _ev(self, what):
        with self.lock:
            self.events.append((round(time.perf_counter(), 4), what))

    def pulse_rts(self, seconds=0.2):
        """Deliberate reset, for the self-test only."""
        s = self.ser
        if s is None:
            return False
        # esptool's USB-JTAG hard reset, with its Windows detail: a DTR change
        # only reaches the device when RTS is next written, so RTS is written
        # again after DTR. A bare RTS pulse (first self-test, 2026-10-08
        # 00:08) did not reset the chip.
        s.dtr = False
        s.rts = True
        s.dtr = False
        s.rts = True
        time.sleep(seconds)
        s.rts = False
        s.dtr = False
        s.rts = False
        self._ev("rts pulsed %.3f s (dtr low, rts re-written)" % seconds)
        return True

    def run(self):
        pending = b""
        first = True
        while not self.stop_flag.is_set():
            if self.ser is None:
                try:
                    self.ser = self._open()
                    self._ev("open" if first else "reopened")
                    first = False
                except Exception:
                    time.sleep(0.05)
                    continue
            try:
                chunk = self.ser.read(4096)
            except Exception as e:
                self._ev("lost: %s" % type(e).__name__)
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                continue
            if not chunk:
                continue
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                txt = redact(raw.decode("latin-1").rstrip("\r"))
                with self.lock:
                    self.lines.append((round(time.perf_counter(), 4), txt))
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass

    def banner_lines(self, t_from=None, t_to=None):
        with self.lock:
            return [(t, x) for t, x in self.lines if BANNER_RE.search(x)
                    and (t_from is None or t >= t_from)
                    and (t_to is None or t <= t_to)]


def selftest():
    import lowid_flood_skip_bench as LF
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "what": "deliberate reset: RTS 200 ms with DTR low"}
    p = LF.page()
    if p is None or p.get("mode") != 0:
        print("FAIL: device must be in mode 0")
        return 1
    cc = ConsoleCapture()
    cc.start()
    time.sleep(1.5)
    LF.dev_post("/gen_inhibit_set?mode=3")
    time.sleep(1.5)
    before = LF.page()
    rec["before"] = {k: (before or {}).get(k) for k in ("mode", "tx_ok")}
    rec["before"]["drv_null"] = (before or {}).get("drv") is None
    t_pulse = time.perf_counter()
    rec["pulsed"] = cc.pulse_rts(0.2)
    after = None
    t_end = time.perf_counter() + 25.0
    while time.perf_counter() < t_end:
        time.sleep(1.0)
        after = LF.page()
        if after is not None and time.perf_counter() - t_pulse > 4.0:
            break
    time.sleep(2.0)
    cc.stop_flag.set()
    cc.join(timeout=3.0)
    rec["after"] = {k: (after or {}).get(k) for k in ("mode", "tx_ok")}
    rec["after"]["drv_null"] = (after or {}).get("drv") is None
    reset = (after is not None and after.get("mode") == 0
             and (after.get("tx_ok") or 0) <= (rec["before"].get("tx_ok") or 0)
             and after.get("drv") is None)
    rec["reset_by_post_state"] = bool(reset)
    rec["port_events_rel_pulse_s"] = [(round(t - t_pulse, 3), e)
                                      for t, e in cc.events]
    rec["banner_lines_rel_pulse_s"] = [(round(t - t_pulse, 3), x)
                                       for t, x in cc.banner_lines(t_pulse)]
    rec["all_lines_after_pulse"] = sum(1 for t, _ in cc.lines if t >= t_pulse)
    try:
        LF.dev_post("/gen_inhibit_set?mode=0")
    except Exception:
        pass
    print(json.dumps({k: rec[k] for k in ("before", "after",
                                          "reset_by_post_state",
                                          "port_events_rel_pulse_s",
                                          "all_lines_after_pulse")}, indent=1))
    for t, x in rec["banner_lines_rel_pulse_s"][:12]:
        print("  banner %+.3f  %s" % (t, x[:110]))
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, "console_selftest_%s.json" % stamp)
    with open(path, "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=1)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    sys.exit(selftest() if a.selftest else 0)
