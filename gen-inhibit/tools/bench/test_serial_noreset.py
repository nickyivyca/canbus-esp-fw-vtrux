#!/usr/bin/env python3
"""Tests for serial_noreset.py and for every gen-inhibit tool that opens an ESP32
USB-Serial-JTAG console through it, against a model of the DTR and RTS lines. No
hardware, no pyserial: a stand-in `serial` module.

    python3.13 -B gen-inhibit/tools/bench/test_serial_noreset.py [--can-fail]

Gen-inhibit tester, 2026-11-11, for the gen-inhibit reviewer's condition of the first
gen-inhibit bench run on madhouse-debian: every gen-inhibit X2 serial open uses a
no-reset form on Linux and keeps its form on Windows, with a test that catches the old
order (serial_noreset.py has the measurement).

THE MODEL (the chip's side of one port). While the port is open, RTS asserted with DTR
not asserted holds the chip in reset; every state the lines pass through is kept, and a
reset is any such state. Opening on Linux: the kernel asserts both lines, then pyserial's
posix open() writes the configured DTR, then the configured RTS. Opening on Windows: both
lines take the configured values at the open. Closing drops both lines together. The
controls show that the model sees the old form's reset on Linux, does not see one on
Windows, and sees the wrong order of the second measured form.

The tools: x2_jammer.Console._open and x2_pins_noreset_check.read_serial (the X2), and
console_capture.ConsoleCapture._open and serial_boot_log.open_port (the WiCAN's C3, the
same USB-Serial-JTAG). Each must open on Linux with no reset, leave both lines deasserted,
and on Windows make exactly the old form's calls. python-can's slcan (B22's
`e4_device_calibration.py --flood-slcan`), whose open is python-can's, must open with
pyserial's defaults on Linux: no reset. x2_hold.py and x2_flash_custody.py open the port
only through esptool, which opens it with pyserial's defaults; they are not run here.

The modules are imported with REVERSE_IT_DIR pointed at a made-up root in this run's
scratch folder (they resolve runs/ at import), never the real one.

--can-fail runs test_serial_noreset_mutants.py.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if "--can-fail" in sys.argv[1:]:
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-B", os.path.join(HERE, "test_serial_noreset_mutants.py")]))

import gi_paths  # noqa: E402  scratch/ folders

ROOT = gi_paths.scratch("serial_noreset")          # removed at exit, pass or fail
for sub in ("canre", "projects"):
    os.makedirs(os.path.join(ROOT, sub), exist_ok=True)
os.environ["REVERSE_IT_DIR"] = ROOT                 # a made-up root: the modules resolve runs/ at import
os.environ.pop("GI_RUNS_DIR", None)

import serial_noreset as SN                         # noqa: E402
import x2_jammer                                    # noqa: E402
import x2_pins_noreset_check                        # noqa: E402
import console_capture                              # noqa: E402
import serial_boot_log                              # noqa: E402

FAILS = []
OKS = [0]
RESET = (False, True)       # (dtr, rts): RTS asserted, DTR not


def check(label, ok, detail=""):
    if ok:
        OKS[0] += 1
        print("  ok   %s" % label)
    else:
        FAILS.append(label)
        print("  FAIL %s  %s" % (label, detail))


class Lines:
    """The chip's view of one port's DTR and RTS (the module docstring)."""

    def __init__(self, os_name):
        self.os_name, self.state, self.history, self.ops = os_name, None, [], []

    def put(self, dtr, rts):
        self.state = (dtr, rts)
        self.history.append(self.state)

    def resets(self):
        return sum(1 for s in self.history if s == RESET)


def fake_serial(lines):
    """A stand-in for the pyserial module, every port on `lines`."""

    class SerialException(Exception):
        pass

    class Serial:
        def __init__(self, port=None, baudrate=9600, timeout=None, do_not_open=False, **kw):
            self.port, self.baudrate, self.timeout = port, baudrate, timeout
            self._dtr = self._rts = True        # pyserial's defaults
            self.is_open = False
            if port is not None and not do_not_open:
                self.open()

        @property
        def dtr(self):
            return self._dtr

        @dtr.setter
        def dtr(self, v):
            self._dtr = bool(v)
            lines.ops.append(("dtr", bool(v), self.is_open))
            if self.is_open:
                lines.put(self._dtr, lines.state[1])

        @property
        def rts(self):
            return self._rts

        @rts.setter
        def rts(self, v):
            self._rts = bool(v)
            lines.ops.append(("rts", bool(v), self.is_open))
            if self.is_open:
                lines.put(lines.state[0], self._rts)

        def open(self):
            lines.ops.append(("open",))
            if lines.os_name == "nt":
                lines.put(self._dtr, self._rts)     # both take the configured values at the open
            else:
                lines.put(True, True)               # the kernel asserts both
                lines.put(self._dtr, True)          # pyserial's posix open(): DTR first,
                lines.put(self._dtr, self._rts)     # then RTS
            self.is_open = True

        def close(self):
            if self.is_open:
                lines.ops.append(("close",))
                lines.put(False, False)             # both dropped together
            self.is_open = False

        def read(self, n=1):
            return b""

        def read_all(self):
            return b""

        def write(self, data):
            lines.ops.append(("write", bytes(data)))
            return len(data)

        def flush(self):
            pass

        def reset_input_buffer(self):
            pass

        def reset_output_buffer(self):
            pass

        in_waiting = 0

    def serial_for_url(url, *args, **kw):
        s = Serial(None, *args, **{k: v for k, v in kw.items() if k != "do_not_open"})
        s.port = url
        if not kw.get("do_not_open"):
            s.open()
        return s

    m = types.ModuleType("serial")
    m.Serial, m.serial_for_url, m.SerialException, m.VERSION = Serial, serial_for_url, SerialException, "stand-in"
    return m


def run(os_name, fn):
    """fn(serial module) with the stand-in in sys.modules and serial_noreset's os_name
    swapped; (lines, fn's result or "CRASH ...")."""
    lines = Lines(os_name)
    mod = fake_serial(lines)
    old_mod, old_os = sys.modules.get("serial"), SN.os_name
    sys.modules["serial"] = mod
    SN.os_name = lambda: os_name
    try:
        got = fn(mod)
    except Exception as e:      # noqa: BLE001  a crash fails its check, never the run
        got = "CRASH %s: %s" % (type(e).__name__, e)
    finally:
        SN.os_name = old_os
        if old_mod is None:
            sys.modules.pop("serial", None)
        else:
            sys.modules["serial"] = old_mod
    return lines, got


def old_form(mod):
    """The form the tools used until 2026-11-11."""
    s = mod.Serial()
    s.port, s.baudrate, s.timeout = "P", 115200, 0.1
    s.dtr = False
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    return s


OLD_OPS = [("dtr", False, False), ("rts", False, False), ("open",), ("dtr", False, True), ("rts", False, True)]


def model_controls():
    print("the model")
    lines, _ = run("posix", old_form)
    check("control: the old form (both pre-set low, then open) resets on Linux", lines.resets() >= 1, lines.history)
    lines, _ = run("nt", old_form)
    check("control: the old form does not reset on Windows", lines.resets() == 0 and lines.ops == OLD_OPS,
          (lines.history, lines.ops))

    def wrong_order(mod):
        s = mod.Serial()
        s.port = "P"
        s.open()
        s.dtr = False
        s.rts = False
        return s
    lines, _ = run("posix", wrong_order)
    check("control: defaults, then dtr=False, then rts=False resets on Linux", lines.resets() >= 1, lines.history)

    def url_preset(mod):
        s = mod.serial_for_url("P", do_not_open=True)
        s.dtr = False
        s.rts = False
        s.open()
        return s
    lines, _ = run("posix", url_preset)
    check("control: serial_for_url, both pre-set low, then open resets on Linux", lines.resets() >= 1, lines.history)
    lines, _ = run("posix", lambda mod: mod.serial_for_url("P"))
    check("control: pyserial's defaults alone do not reset on Linux", lines.resets() == 0 and lines.history,
          lines.history)


def no_reset_open(label, os_name, lines, port):
    """The checks every opener gets on Linux: open, no reset, both lines deasserted."""
    check("%s: open on Linux with no reset" % label,
          lines.resets() == 0 and getattr(port, "is_open", False), (lines.history, port))
    check("%s: both lines deasserted after the open on Linux" % label, lines.state == (False, False), lines.state)


def the_helper():
    print("serial_noreset.open_console")
    lines, s = run("posix", lambda mod: SN.open_console("P", 115200, 0.1))
    no_reset_open("open_console", "posix", lines, s)
    check("open_console: port, baud rate and timeout as given",
          (getattr(s, "port", None), getattr(s, "baudrate", None), getattr(s, "timeout", None)) == ("P", 115200, 0.1),
          s)
    check("open_console: on Linux opens with pyserial's defaults (nothing set before the open)",
          lines.ops[:1] == [("open",)], lines.ops)
    lines, s = run("nt", lambda mod: SN.open_console("P", 115200, 0.1))
    check("open_console: on Windows the old form's calls, unchanged", lines.ops == OLD_OPS and lines.resets() == 0,
          lines.ops)
    other = Lines("posix")          # a second stand-in, not the one in sys.modules
    lines, s = run("posix", lambda mod: SN.open_console("P", 9600, 0.5, serial_mod=fake_serial(other)))
    check("open_console: a serial_mod given is the one used",
          lines.ops == [] and other.ops[:1] == [("open",)] and other.resets() == 0, (lines.ops, other.ops))
    check("os_name() is os.name", SN.os_name() == os.name, SN.os_name())


def the_tools():
    for label, fn in (
            ("x2_jammer.Console._open", lambda mod: x2_jammer.Console("P")._open()),
            ("console_capture.ConsoleCapture._open", lambda mod: console_capture.ConsoleCapture("P", 115200)._open()),
            ("serial_boot_log.open_port", lambda mod: serial_boot_log.open_port("P", 115200))):
        print(label)
        lines, s = run("posix", fn)
        no_reset_open(label, "posix", lines, s)
        check("%s: on the port given" % label, getattr(s, "port", None) == "P", s)
        lines, s = run("nt", fn)
        check("%s: on Windows the old form's calls, unchanged" % label, lines.ops == OLD_OPS and lines.resets() == 0,
              lines.ops)
    label = "x2_pins_noreset_check.read_serial"
    print(label)
    lines, got = run("posix", lambda mod: x2_pins_noreset_check.read_serial("P", 0))
    check("%s: no reset on Linux, from the open to the close" % label,
          lines.resets() == 0 and isinstance(got, list) and ("open",) in lines.ops, (lines.history, got))
    check("%s: both lines deasserted before the read on Linux" % label,
          len(lines.history) >= 2 and lines.history[-2] == (False, False), lines.history)
    check("%s: closes the port and writes nothing" % label,
          ("close",) in lines.ops and not any(o[0] == "write" for o in lines.ops), lines.ops)
    lines, got = run("nt", lambda mod: x2_pins_noreset_check.read_serial("P", 0))
    check("%s: on Windows the old form's calls, unchanged" % label,
          lines.ops == OLD_OPS + [("close",)] and lines.resets() == 0, lines.ops)


def slcan():
    print("python-can's slcan (B22's --flood-slcan)")
    try:
        import can
        import can.interfaces.slcan as SL
    except ImportError as e:
        print("  NOT RUN (other env): python-can is not importable here (%s)" % e)
        return
    for os_name in ("posix", "nt"):
        def open_bus(mod):
            real = SL.serial
            SL.serial = mod
            try:
                bus = can.Bus(interface="slcan", channel="P", bitrate=500000, sleep_after_open=0)
                bus.shutdown()
            finally:
                SL.serial = real
            return bus
        lines, got = run(os_name, open_bus)
        first = lines.ops.index(("open",)) if ("open",) in lines.ops else None
        check("slcan (python-can %s) on %s: opens with pyserial's defaults, no reset"
              % (can.__version__, "Linux" if os_name == "posix" else "Windows"),
              not str(got).startswith("CRASH") and first == 0 and lines.resets() == 0, (got, lines.ops, lines.history))


def main():
    try:
        model_controls()
        the_helper()
        the_tools()
        slcan()
    finally:
        gi_paths.remove_tree(ROOT)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not FAILS else "FAIL", OKS[0], len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
