"""Harness test: the witnesses that count error frames count bus errors only (no bus).

Gen-inhibit tester, 2026-11-18, for the gen-inhibit reviewer's ruling of that day: on the Kvaser
through SocketCAN (kvaser_usb), B6, B12 and B22 count bus-error classes only, and keep
controller-state (CRTL, BUSOFF) and RESTARTED frames out of their counts, with a can-fail
showing that a real bus error still counts. The counting sites:
  - x2_jam_cases.Witness (B6): errs, and not_bus_errors for the frames left out;
  - restart_error_frames.witness_take (B12): errs, and not_bus;
  - e4_rig_calibration.bus_errors (B22 through e4_device_calibration, and the rig calibration).
Each is fed the same four messages from a fake SocketCAN witness:
  - the real frame: the first error frame of runs/kvaser_usb_measure_20261118T154231,
    id 0x20000288 (PROT | BUSERROR | CNT), data 0000020800000000. It must count (the can-fail).
  - CRTL | CNT, and RESTARTED, each with no bus-error class: left out, counted as left out;
  - a data frame: never an error.
And the same CRTL frame from a bus with no backend tag (CANlib, the PCAN) still counts there.
The classes are linux/can/error.h's, written out here, not taken from kvaser_bus.

    python3.13 -B gen-inhibit/tools/bench/test_error_frame_sites.py [--mutants]
SITE_DIR (default: this folder) is where the site files and kvaser_bus.py are loaded from;
--mutants runs this test on mutated copies there.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SITE_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

CRTL, PROT, BUSERROR, RESTARTED, CNT = 0x4, 0x8, 0x80, 0x100, 0x200
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SITE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def messages():
    import can
    return [can.Message(timestamp=1.0, arbitration_id=PROT | BUSERROR | CNT, is_error_frame=True,
                        is_extended_id=False, data=bytes.fromhex("0000020800000000")),
            can.Message(timestamp=2.0, arbitration_id=CRTL | CNT, is_error_frame=True, is_extended_id=False,
                        data=bytes(8)),
            can.Message(timestamp=3.0, arbitration_id=RESTARTED, is_error_frame=True, is_extended_id=False,
                        data=bytes(8)),
            can.Message(timestamp=4.0, arbitration_id=0x051, data=bytes(6))]


class FakeBus:
    """A witness bus that hands out the given messages, then nothing."""

    def __init__(self, msgs, backend="socketcan"):
        self.msgs = list(msgs)
        if backend:
            self.vtrux_backend = backend
            # bench_layer's classifier (2026-11-19) reads the backend from bench_role, as open_role sets it
            self.bench_role = types.SimpleNamespace(interface=backend)
        self.shut = False

    def recv(self, timeout=None):
        if self.msgs:
            return self.msgs.pop(0)
        if timeout:
            time.sleep(min(timeout, 0.01))
        return None

    def shutdown(self):
        self.shut = True


def kvaser_module(bus):
    """The real kvaser_bus (its bus_error, bus_error_class), with the opener replaced by one that
    hands out bus: nothing here opens an adapter."""
    kb = load("kvaser_bus")
    kb.kvaser_bus = lambda **kw: bus
    return kb


def site_x2_jam_cases():
    bus = FakeBus(messages())
    kvaser_module(bus)
    xj = load("x2_jam_cases")
    w = xj.Witness()
    w._next_status = float("inf")       # no CANlib status sampling on the fake
    w.start()
    deadline = time.time() + 5
    while bus.msgs and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    w.close()
    check("x2_jam_cases (B6): the real bus error counts (can-fail)", [e[0] for e in w.errs] == [1.0], w.errs)
    check("x2_jam_cases (B6): CRTL and RESTARTED left out of errs and counted as left out",
          w.not_bus_errors == 2, w.not_bus_errors)


def site_restart_error_frames():
    kb = kvaser_module(None)
    ref = load("restart_error_frames")
    for backend, want_errs, want_not in (("socketcan", [1.0], [2.0, 3.0]), (None, [1.0, 2.0, 3.0], [])):
        kv = FakeBus([], backend)
        got, errs, not_bus = [], [], []
        for m in messages():
            ref.witness_take(kv, m, got, errs, not_bus, kb.bus_error)
        label = "SocketCAN" if backend else "a bus with no backend tag"
        check("restart_error_frames (B12), %s: errs %s, left out %s, the data frame kept"
              % (label, want_errs, want_not),
              errs == want_errs and not_bus == want_not and [g[1] for g in got] == [0x051], (errs, not_bus, got))


def site_e4():
    kvaser_module(None)
    e4 = load("e4_rig_calibration")
    frames = [(m.timestamp, m.arbitration_id, bytes(m.data), bool(m.is_error_frame), False) for m in messages()]
    for backend, want, n_out in (("socketcan", [1.0], 2), (None, [1.0, 2.0, 3.0], 0)):
        w = types.SimpleNamespace(frames=frames, bus=FakeBus([], backend))
        errf, out = e4.bus_errors(w)
        label = "SocketCAN" if backend else "a bus with no backend tag"
        check("e4_rig_calibration.bus_errors (B22), %s: counts %s, leaves out %d" % (label, want, n_out),
              [f[0] for f in errf] == want and out == n_out, (errf, out))
    with open(os.path.join(SITE, "e4_device_calibration.py"), encoding="utf-8") as f:
        dev = f.read()
    check("e4_device_calibration (B22's tool) counts through e4_rig_calibration.bus_errors",
          "errf, not_bus = bus_errors(w)" in dev and "errf = [f for f in w.frames if f[3]]" not in dev)


MUTANTS = [
    ("x2_jam_cases.py", "                        if self.bus_error(m):\n", "                        if True:\n"),
    ("x2_jam_cases.py", "                        if self.bus_error(m):\n", "                        if False:\n"),
    ("x2_jam_cases.py", "                            self.not_bus_errors += 1\n", "                            pass\n"),
    ("restart_error_frames.py", "        (errs if bus_error(kv, m) else not_bus).append(m.timestamp)\n",
     "        errs.append(m.timestamp)\n"),
    ("restart_error_frames.py", "        (errs if bus_error(kv, m) else not_bus).append(m.timestamp)\n",
     "        (not_bus if bus_error(kv, m) else errs).append(m.timestamp)\n"),
    ("e4_rig_calibration.py", "    errf = [f for f in errs if bus_error_class(w.bus, f[1])]\n", "    errf = errs\n"),
    ("e4_rig_calibration.py", "    return errf, len(errs) - len(errf)\n", "    return errf, 0\n"),
    ("e4_device_calibration.py", "    errf, not_bus = bus_errors(w)",
     "    errf, not_bus = [f for f in w.frames if f[3]], 0"),
    ("kvaser_bus.py", "    return bool(can_id & BUS_ERROR_CLASSES)\n", "    return True\n"),
]


def mutants():
    base = gi_paths.scratch("error-frame-sites-mutants")
    killed = 0
    try:
        for n, (fn, old, new) in enumerate([(None, None, None)] + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if fn:
                p = os.path.join(d, fn)
                with open(p, encoding="utf-8") as f:
                    s = f.read()
                if s.count(old) != 1:
                    print("ERROR    m%d %s: edit matched %d times" % (n, fn, s.count(old)))
                    continue
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(s.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_error_frame_sites.py")],
                               capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, SITE_DIR=d, PYTHONDONTWRITEBYTECODE="1"))
            fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
            if fn is None:
                if r.returncode != 0:
                    print("FAILED   control\n" + r.stdout[-1500:] + r.stderr[-1500:])
                    return 1
                print("PASS     control")
            elif r.returncode != 0:
                killed += 1
                print("KILLED   m%d %s: %s  (%s)" % (n, fn, new.strip()[:60], (fails or [r.stderr.strip()[-100:]])[0][:100]))
            else:
                print("SURVIVED m%d %s: %s" % (n, fn, new.strip()[:60]))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d mutants killed" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


def main(argv):
    if "--mutants" in argv:
        return mutants()
    for site in (site_x2_jam_cases, site_restart_error_frames, site_e4):
        try:
            site()
        except Exception as e:      # noqa: BLE001  a site that raises is a FAIL, not a crash
            check(site.__name__ + " ran", False, "%s: %s" % (type(e).__name__, e))
    print("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
