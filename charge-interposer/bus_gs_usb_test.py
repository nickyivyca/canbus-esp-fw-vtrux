"""bus.open_gs_usb's own test, no hardware (tester, 2026-10-13).

The Innomaker (gs_usb) is the charger segment's only adapter, and step 4's
closed loop on the board runs charger_sim through it. bus.open_bus used to
auto-detect every real brand, which finds no gs_usb device (bringup,
2026-10-04), and python-can's gs_usb hands back the device's TX echoes as
received frames (is_rx False) whatever receive_own_messages says, so a sim
would read its own frames back. This drives open_gs_usb against a fake
GsUsbBus and checks each half can fail:

  - the device is opened by index, from --channel, default 0, and the
    auto-detection path is not touched;
  - echoes are drained and dropped by default, kept when asked for;
  - a recv() that sees only echoes inside its timeout returns None, not an
    echo and not a hang;
  - gs_usb needs no canbench: it opens with CAN_TOOLS_DIR unset, while
    kvaser is refused with the PathsError naming the setting;
  - the other brands open what canbench detected, in the shape can-tools
    1cd3942 gives it (charge-interposer tester, 2026-11-01, the pin move):
    an Adapter that unpacks as (interface, channel, desc), with .ids and a
    desc ending in the id list. open_bus takes the first adapter of the
    brand asked for, opens it by its channel (never from desc or .ids),
    and gives a PCAN auto_reset and a reset(); two PCANs that both carry
    pcan-id:0 (PEAK's default device number) still open by channel.

canbench is the pinned commit's, installed by can_tools_pin.py from a
stand-in can-tools clone at CAN_TOOLS_DIR (can_tools_pin_test.fixture),
built in a throwaway folder beside this file and removed, with the
machine's own canbench (the can-tools.pth) taken off sys.path first, so no
real clone and no adapter is needed.

Run: python bus_gs_usb_test.py  (exit 0 = all checks passed)
     python bus_gs_usb_test.py --can-fail
       runs this test against each mutant of bus.py in MUTANTS (a copy in
       a scratch_dir folder, loaded through BUS_UNDER_TEST; bus.py itself
       is never edited); every mutant must make it fail, and the unmutated
       copy must pass.
"""

import collections
import logging
import os
import subprocess
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import can                                                  # noqa: E402
import scratch_dir                                          # noqa: E402

# --can-fail runs this test on a mutated copy of bus.py: the child loads
# the module at BUS_UNDER_TEST as "bus" in place of the one beside it.
if os.environ.get("BUS_UNDER_TEST"):
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("bus", os.environ["BUS_UNDER_TEST"])
    busmod = _ilu.module_from_spec(_spec)
    sys.modules["bus"] = busmod
    _spec.loader.exec_module(busmod)
else:
    import bus as busmod                                    # noqa: E402

FAILS = []


def check(cond, what):
    print("  %s  %s" % ("ok  " if cond else "FAIL", what))
    if not cond:
        FAILS.append(what)


def msg(i, rx):
    return can.Message(arbitration_id=0x100 + i, data=[i], is_rx=rx,
                       is_extended_id=False)


class FakeGsUsb(can.BusABC):
    """Stands in for can.interfaces.gs_usb.GsUsbBus: records how it was
    opened and returns a scripted sequence from _recv_internal, as the real
    one does (one frame per call, None when the script is empty)."""
    opened = []

    def __init__(self, channel, bitrate=500000, index=None, **kw):
        FakeGsUsb.opened.append(dict(channel=channel, bitrate=bitrate,
                                     index=index))
        self.script = []
        self.channel_info = channel
        super().__init__(channel=channel, **kw)

    def _recv_internal(self, timeout):
        if self.script:
            return self.script.pop(0), False
        if timeout:
            time.sleep(min(timeout, 0.01))
        return None, False

    def send(self, m, timeout=None):
        pass

    def shutdown(self):
        super().shutdown()


def args(channel):
    return types.SimpleNamespace(transport="gs_usb", channel=channel,
                                 port=0, bitrate=500000)


class Adapter(collections.namedtuple("Adapter", "interface channel desc")):
    """The shape of canbench.live.receiver.Adapter at can-tools 1cd3942: it
    unpacks as (interface, channel, desc); ids name the physical adapter,
    and desc ends in them. Mirrored here because the stand-in clone's
    canbench is not the real one."""

    def __new__(cls, interface, channel, desc, ids=()):
        self = super().__new__(cls, interface, channel, desc)
        self.ids = tuple(ids)
        return self


# What 1cd3942 detects on a Windows bench with a two-channel Kvaser and two
# PCANs whose device numbers were never set (both pcan-id:0), and what it
# gives for a PCAN behind Linux's peak_usb driver when asked for 'pcan' (a
# socketcan entry, listed first).
DETECTED = [
    Adapter("socketcan", "can0",
            "socketcan can0 (peak_usb) [usb:1-2, socketcan:can0]",
            ("usb:1-2", "socketcan:can0")),
    Adapter("kvaser", 0, "kvaser channel 0 [serial:44544#1, serial:44544]",
            ("serial:44544#1", "serial:44544", "kvaser:0")),
    Adapter("pcan", "PCAN_USBBUS1", "pcan PCAN_USBBUS1 [pcan-id:0]",
            ("pcan-id:0", "pcan:PCAN_USBBUS1")),
    Adapter("pcan", "PCAN_USBBUS2", "pcan PCAN_USBBUS2 [pcan-id:0]",
            ("pcan-id:0", "pcan:PCAN_USBBUS2")),
]


class FakeBus:
    """Stands in for can.interface.Bus: records how it was opened."""
    opened = []

    def __init__(self, **kw):
        FakeBus.opened.append(dict(kw))
        self.resets = 0

    def reset(self):
        self.resets += 1

    def shutdown(self):
        pass


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def main():
    if sys.argv[1:] == ["--can-fail"]:
        return can_fail()
    import can_tools_pin_test as CT
    saved = os.environ.pop("CAN_TOOLS_DIR", None)
    base = scratch_dir.make("bus-gs-usb-test")
    path_in = CT.off_pth()
    try:
        # the pinned commit's canbench detects nothing
        fix = CT.fixture(base)
        with CT.pointed(fix) as ctp:
            ctp.install()
            os.environ.pop("CAN_TOOLS_DIR")
            return run(fix)
    finally:
        sys.path[:] = path_in
        scratch_dir.remove(base)
        if saved is not None:
            os.environ["CAN_TOOLS_DIR"] = saved


def run(fix):
    import paths
    print("CAN_TOOLS_DIR unset: gs_usb opens, kvaser is refused")
    real_open = busmod.open_gs_usb
    busmod.open_gs_usb = lambda ch, br, **kw: real_open(
        ch, br, _bus_cls=FakeGsUsb, **kw)
    try:
        FakeGsUsb.opened.clear()
        try:
            busmod.open_bus(args(None)).shutdown()
            err = None
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        check(err is None and len(FakeGsUsb.opened) == 1,
              "gs_usb opens with CAN_TOOLS_DIR unset -> %s"
              % (err or FakeGsUsb.opened))
        try:
            busmod.open_bus(types.SimpleNamespace(
                transport="kvaser", channel=None, port=0, bitrate=500000))
            refused = None
        except Exception as e:
            refused = "%s: %s" % (type(e).__name__, e)
        check(refused is not None and refused.startswith("PathsError")
              and "CAN_TOOLS_DIR is not set" in refused,
              "kvaser with CAN_TOOLS_DIR unset is refused, naming the "
              "setting -> %s" % refused)
        check("canbench" not in sys.modules,
              "... and no canbench was imported")
    finally:
        busmod.open_gs_usb = real_open
    os.environ["CAN_TOOLS_DIR"] = fix["clone"]

    print("open by index, not by auto-detection")
    # open_bus must never reach canbench's detection for gs_usb: replace it
    # with one that fails loudly if called
    cb = paths.import_canbench()
    check(os.path.dirname(os.path.dirname(os.path.abspath(cb.__file__)))
          == os.path.abspath(fix["folder"]),
          "canbench is the pinned commit's, installed from the stand-in "
          "clone at CAN_TOOLS_DIR")
    import canbench.live.receiver as rcv
    real_detect = rcv.detect_all_can_interfaces

    def no_detect(*a, **k):
        raise AssertionError("open_bus auto-detected a gs_usb device")
    rcv.detect_all_can_interfaces = no_detect
    real_open = busmod.open_gs_usb
    seen = {}

    def spy(channel, bitrate, **kw):
        seen.update(channel=channel, bitrate=bitrate, **kw)
        return real_open(channel, bitrate, _bus_cls=FakeGsUsb, **kw)
    busmod.open_gs_usb = spy
    try:
        FakeGsUsb.opened.clear()
        b = busmod.open_bus(args(None))
        check(FakeGsUsb.opened[-1]["index"] == 0
              and FakeGsUsb.opened[-1]["channel"] == "can0",
              "no --channel opens device index 0 -> %s" % FakeGsUsb.opened)
        b.shutdown()
        b = busmod.open_bus(args("1"))
        check(FakeGsUsb.opened[-1]["index"] == 1,
              "--channel 1 opens device index 1 -> %s"
              % FakeGsUsb.opened[-1])
        b.shutdown()
        check(seen.get("bitrate") == 500000,
              "the bitrate reaches the device -> %s" % seen)
    finally:
        busmod.open_gs_usb = real_open
        rcv.detect_all_can_interfaces = real_detect
    # and the detection path is still the one taken for other brands: the
    # guard above would have raised for them too
    rcv.detect_all_can_interfaces = no_detect
    try:
        try:
            busmod.open_bus(types.SimpleNamespace(
                transport="kvaser", channel=None, port=0, bitrate=500000))
            took_detect = False
        except AssertionError:
            took_detect = True
        check(took_detect, "kvaser still goes through auto-detection (the "
              "guard can fail)")
    finally:
        rcv.detect_all_can_interfaces = real_detect

    print("detected adapters (can-tools 1cd3942's Adapter): opened by channel")
    asked = []

    def detect(bitrate, brands=None, **kw):
        asked.append((bitrate, brands))
        return list(DETECTED)
    rcv.detect_all_can_interfaces = detect
    real_bus = busmod.can.interface.Bus
    busmod.can.interface.Bus = FakeBus
    log = logging.getLogger("bus_gs_usb_test.adapters")
    log.setLevel(logging.INFO)
    lines = _Lines()
    log.addHandler(lines)

    def real(transport, channel=None):
        FakeBus.opened.clear()
        return busmod.open_bus(types.SimpleNamespace(
            transport=transport, channel=channel, port=0, bitrate=500000),
            logger=log)
    try:
        b = real("kvaser")
        check(FakeBus.opened == [dict(interface="kvaser", channel=0,
                                      bitrate=500000,
                                      receive_own_messages=False)],
              "kvaser: the Adapter unpacks, and the kvaser opens on its "
              "channel 0, not a channel read from desc or .ids -> %s"
              % FakeBus.opened)
        check(asked[-1] == (500000, {"kvaser"}),
              "detection is asked for that brand alone -> %s" % (asked[-1],))
        check(any(ln == "Using real interface: kvaser channel 0 "
                  "[serial:44544#1, serial:44544]" for ln in lines.lines),
              "the log names the adapter by its whole desc, id list and all")
        b = real("pcan")
        check(FakeBus.opened == [dict(interface="pcan",
                                      channel="PCAN_USBBUS1", bitrate=500000,
                                      receive_own_messages=False,
                                      auto_reset=True)],
              "pcan: the first PCAN detected, not the socketcan entry "
              "listed before it, opened by channel with auto_reset -> %s"
              % FakeBus.opened)
        check(b.resets == 1, "... and reset() once at startup -> %d"
              % b.resets)
        b = real("pcan", "PCAN_USBBUS2")
        check([o.get("channel") for o in FakeBus.opened] == ["PCAN_USBBUS2"],
              "--channel PCAN_USBBUS2 opens the second PCAN, though both "
              "carry pcan-id:0 -> %s" % FakeBus.opened)
    finally:
        busmod.can.interface.Bus = real_bus
        rcv.detect_all_can_interfaces = real_detect
        log.removeHandler(lines)

    print("echoes: drained and dropped by default, kept when asked for")
    b = busmod.open_gs_usb("0", 500000, _bus_cls=FakeGsUsb)
    b.script = [msg(1, False), msg(2, True), msg(3, False), msg(4, False),
                msg(5, True)]
    got = [b.recv(timeout=0.2), b.recv(timeout=0.2)]
    check([m.arbitration_id for m in got] == [0x102, 0x105]
          and b.script == [],
          "recv() returns only the received frames, reading past three "
          "echoes -> %s" % [hex(m.arbitration_id) for m in got if m])
    b.shutdown()
    b = busmod.open_gs_usb("0", 500000, _bus_cls=FakeGsUsb,
                           receive_own_messages=True)
    b.script = [msg(1, False), msg(2, True)]
    got = [b.recv(timeout=0.2), b.recv(timeout=0.2)]
    check([(m.arbitration_id, m.is_rx) for m in got]
          == [(0x101, False), (0x102, True)],
          "receive_own_messages=True passes the echo through -> %s"
          % [(hex(m.arbitration_id), m.is_rx) for m in got if m])
    b.shutdown()
    # the mixin is what drops them: without it the echo comes back
    raw = FakeGsUsb(channel="can0", index=0)
    raw.script = [msg(1, False)]
    m = raw.recv(timeout=0.2)
    check(m is not None and not m.is_rx,
          "the bare fake returns the echo (so the drop above is the "
          "mixin's doing)")
    raw.shutdown()

    print("only echoes inside the timeout")
    b = busmod.open_gs_usb("0", 500000, _bus_cls=FakeGsUsb)
    b.script = [msg(i, False) for i in range(6)]
    t0 = time.monotonic()
    m = b.recv(timeout=0.1)
    dt = time.monotonic() - t0
    check(m is None and dt < 1.0,
          "six echoes and nothing received: None after %.3f s" % dt)
    b.shutdown()

    print("\nbus_gs_usb_test: %s" % ("all checks OK" if not FAILS else
                                     "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


# (what, text in bus.py, its replacement): each must make the test fail.
MUTANTS = [
    ("gs_usb loads canbench before it opens",
     '    if transport == "gs_usb":\n        return open_gs_usb(',
     '    if transport == "gs_usb":\n        import paths\n'
     '        paths.import_canbench()\n        return open_gs_usb('),
    ("a refused canbench is swallowed and detection skipped",
     '    paths.import_canbench()     # the pinned canbench',
     '    try:\n        paths.import_canbench()\n'
     '    except paths.PathsError:\n'
     '        raise RuntimeError("no kvaser interface detected")\n'
     '    pass     # the pinned canbench'),
    ("the first adapter of any brand is taken",
     "    matches = [i for i in interfaces if i[0] == transport]\n",
     "    matches = list(interfaces)\n"),
    ("the channel is read from the end of desc",
     "    if channel is not None:\n        ch = channel\n",
     "    ch = channel if channel is not None else desc.split()[-1].strip('[]')\n"),
    ("a PCAN opens without auto_reset",
     '        kwargs["auto_reset"] = True\n',
     '        pass\n'),
]


def can_fail():
    src = (HERE / "bus.py").read_text(encoding="utf-8")
    base = scratch_dir.make("bus-gs-usb-canfail")
    bad = 0
    try:
        for what, old, new in [("unmutated (must pass)", "", "")] + MUTANTS:
            if old and src.count(old) != 1:
                print("NOT EXERCISED  %s: its text is not in bus.py once" % what)
                bad += 1
                continue
            d = os.path.join(base, "m%02d" % len(os.listdir(base)))
            os.makedirs(d)
            p = os.path.join(d, "bus.py")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new) if old else src)
            r = subprocess.run([sys.executable, "-B", str(HERE / Path(__file__).name)],
                               env=dict(os.environ, BUS_UNDER_TEST=p),
                               capture_output=True, text=True, timeout=300)
            out = (r.stdout + r.stderr).splitlines()
            fails = [ln.strip() for ln in out if ln.strip().startswith("FAIL")]
            if not old:
                good = r.returncode == 0
                print("%s  %s (rc %d)" % ("ok      " if good else "BROKEN  ",
                                          what, r.returncode))
            else:
                good = r.returncode != 0
                print("%s  %s: %s" % ("caught  " if good else "SURVIVED", what,
                                      (fails or out[-1:] or ["no output"])[0][:150]))
            bad += not good
    finally:
        scratch_dir.remove(base)
    print("\nbus_gs_usb_test --can-fail: %s"
          % ("all %d mutants caught, the unmutated copy passes" % len(MUTANTS)
             if not bad else "%d FAILED" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
