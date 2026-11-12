#!/usr/bin/env python3
"""Test of serial_open.py: the order of the X2 port's line changes from the
open on, on a fake pyserial port that records them. No hardware.

    py -3.14 -B charge-interposer/serial_open_test.py [--can-fail]       (Windows)
    python3.13 -B charge-interposer/serial_open_test.py [--can-fail]     (madhouse)

Each case names what it checks:
  1. order-posix  Linux: open() with pyserial's defaults, then RTS low, then
                  DTR low (the measured no-reset order, adapter
                  characterisation step 7, 2026-11-11); nothing set first
  2. order-nt     Windows: DTR low, RTS low, then open() (unchanged)
  3. lines-low    both systems leave the port open with DTR and RTS low
  4. serialtap    run_scenario.SerialTap's reconnect (_open(False)) goes
                  through open_quiet, in this host's order; its first open
                  stays a plain pyserial open
  5. sites        every harness file that held the lines low calls
                  serial_open.open_quiet: run_scenario.py, bench_identify.py,
                  bench_drivers/keepalive_probe.py, bench_usb_host.py,
                  bench_charger_silent.py
  6. no-preset    no other harness .py (tests aside) builds a port with
                  serial.Serial() and no arguments, the form that presets
                  lines before open(); and the scan finds one planted in a
                  scratch file, so it can fail
--can-fail runs the cases on the unedited serial_open.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail.

Charge-interposer tester, 2026-11-11.
"""

import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "serial_open.py")
SITES = ["run_scenario.py", "bench_identify.py", "bench_drivers/keepalive_probe.py",
         "bench_drivers/bench_usb_host.py", "bench_drivers/bench_charger_silent.py"]
BARE = re.compile(r"\bSerial\(\s*\)")


class FakePort:
    """Records each line change as ("pre"|"post", name, value) and open()."""

    def __init__(self, *a, **k):
        object.__setattr__(self, "events", [])
        object.__setattr__(self, "is_open", False)
        object.__setattr__(self, "lines", {"dtr": True, "rts": True})
        if a or k.get("port"):
            self.open()

    def __setattr__(self, name, value):
        if name in ("dtr", "rts"):
            self.events.append(("post" if self.is_open else "pre", name, value))
            self.lines[name] = value
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        if name in ("dtr", "rts"):
            return self.lines[name]
        raise AttributeError(name)

    def open(self):
        object.__setattr__(self, "is_open", True)
        self.events.append(("open",))


class FakeSerialModule:
    Serial = FakePort


def load(path):
    spec = importlib.util.spec_from_file_location("serial_open", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def case_order_posix(M, work):
    s = M.open_quiet("/dev/x", 115200, 0.2, serial_mod=FakeSerialModule, os_name="posix")
    want = [("open",), ("post", "rts", False), ("post", "dtr", False)]
    assert s.events == want, s.events


def case_order_nt(M, work):
    s = M.open_quiet("COM3", 115200, 0.2, serial_mod=FakeSerialModule, os_name="nt")
    want = [("pre", "dtr", False), ("pre", "rts", False), ("open",)]
    assert s.events == want, s.events


def case_lines_low(M, work):
    for name in ("posix", "nt"):
        s = M.open_quiet("p", 115200, 0.2, serial_mod=FakeSerialModule, os_name=name)
        assert s.is_open and s.lines == {"dtr": False, "rts": False}, (name, s.lines)
        assert (s.port, s.baudrate, s.timeout) == ("p", 115200, 0.2), name


def case_serialtap(M, work):
    sys.modules["serial_open"] = M
    try:
        import run_scenario
        tap = run_scenario.SerialTap("/dev/x", 115200, os.path.join(work, "tap.log"))
        tap._serial_mod = FakeSerialModule
        re_open = tap._open(False)
        host = "nt" if os.name == "nt" else "posix"
        want = {"posix": [("open",), ("post", "rts", False), ("post", "dtr", False)],
                "nt": [("pre", "dtr", False), ("pre", "rts", False), ("open",)]}[host]
        assert re_open.events == want, (host, re_open.events)
        first = tap._open(True)
        assert first.events == [("open",)], first.events
    finally:
        sys.modules.pop("serial_open", None)


def case_sites(M, work):
    for rel in SITES:
        with open(os.path.join(HERE, rel), encoding="utf-8") as f:
            text = f.read()
        assert "serial_open.open_quiet(" in text, rel


def scan(paths):
    """[path:line] of every bare Serial() in paths."""
    hits = []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            for n, ln in enumerate(f, 1):
                if BARE.search(ln) and not ln.lstrip().startswith("#"):
                    hits.append("%s:%d" % (p, n))
    return hits


def harness_files():
    r = subprocess.run(["git", "-C", HERE, "ls-files", "--", "."], capture_output=True,
                       text=True, check=True)
    out = []
    for rel in r.stdout.splitlines():
        base = os.path.basename(rel)
        if not rel.endswith(".py") or rel.startswith("firmware/"):
            continue
        if base in ("serial_open.py",) or base.endswith("_test.py") or base.startswith("test_"):
            continue
        out.append(os.path.join(HERE, rel))
    return out


def case_no_preset(M, work):
    files = harness_files()
    assert len(files) > 20, len(files)
    hits = scan(files)
    assert not hits, hits
    planted = os.path.join(work, "planted.py")
    with open(planted, "w", encoding="utf-8") as f:
        f.write("import serial\ns = serial.Serial()\ns.dtr, s.rts = False, False\ns.open()\n")
    assert scan([planted]) == [planted + ":2"], scan([planted])


CASES = [case_order_posix, case_order_nt, case_lines_low, case_serialtap, case_sites,
         case_no_preset]

MUTANTS = [
    ("Linux presets the lines before open (the order that resets the X2)",
     "        s.open()\n        s.rts = False\n        s.dtr = False\n",
     "        s.dtr = False\n        s.rts = False\n        s.open()\n"),
    ("Linux clears DTR before RTS after open",
     "        s.open()\n        s.rts = False\n        s.dtr = False\n",
     "        s.open()\n        s.dtr = False\n        s.rts = False\n"),
    ("Linux leaves DTR high",
     "        s.open()\n        s.rts = False\n        s.dtr = False\n",
     "        s.open()\n        s.rts = False\n"),
    ("Windows opens with the lines high",
     "        s.dtr = False\n        s.rts = False\n        s.open()\n",
     "        s.open()\n"),
    ("the system test inverted", '    if system(os_name) == "nt":', '    if system(os_name) != "nt":'),
    ("the timeout dropped", "s.port, s.baudrate, s.timeout = port, baudrate, timeout",
     "s.port, s.baudrate = port, baudrate"),
]


def run_cases(path, base):
    M = load(path)
    bad = []
    for c in CASES:
        work = os.path.join(base, c.__name__)
        os.makedirs(work)
        try:
            c(M, work)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                          # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:240]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    with scratch_dir.run("serial-open-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nserial_open_test: %s" % ("all checks OK" if not bad
                                             else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited serial_open.py", flush=True)
        if run_cases(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        src = open(MODULE, encoding="utf-8").read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, "serial_open.py")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_cases(p, os.path.join(d, "t"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
