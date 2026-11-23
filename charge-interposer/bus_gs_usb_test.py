"""bus.open_bus's real-adapter path, its own test, no hardware (tester,
2026-10-13; by bench role since 2026-11-18).

Every real adapter bus.open_bus opens is asked for by its canctl role
(shared/bench SPEC section 8, 2026-11-18; adapter_roles.py): --channel names
the role, bus.ROLE_OF says which role each brand may name, and nothing is
detected or picked by position. python-can's gs_usb hands back the device's
TX echoes as received frames (is_rx False) whatever receive_own_messages
says, so a sim would read its own frames back; open_gs_usb's echo filter
(drop_echoes) drops them. This drives open_bus against stand-ins and checks
each half can fail:

  - check_role: pcan names pcanfd, kvaser and socketcan name kvaser, gs_usb
    names canchg; no role (None), an index ("0", "1"), a PCAN channel
    (PCAN_USBBUS1), canmain, another brand's role, and ixxat (no bench
    role), are refused by open_bus with ValueError before anything opens,
    and open_gs_usb and open_pcan refuse a wrong role themselves too;
  - gs_usb is role canchg through adapter_roles.canchg (bitrate, the
    layer's default mode, libusb), with no index, bus, address, serial or
    channel from the
    harness, the bus it opened returned; its echoes are drained and dropped
    by default, kept when asked for, and a recv() that sees only echoes
    inside its timeout returns None, not an echo and not a hang; one
    recv(0) retires every queued echo, and returns the frame behind them,
    up to 256 echoes after its deadline (2026-11-19);
  - drain=True (2026-11-19, for a caller that only sends) asks the layer
    for its drain on canchg, with no echo filter; on any other transport,
    or with receive_own_messages, it is refused before anything opens;
  - pcan is role pcanfd through adapter_roles.pcanfd_report (the layer's
    default mode, pcan, with bus_states="report": the reviewer's PCAN item
    2, 2026-11-20), with auto_reset and a reset() at startup;
  - the Kvaser (transport kvaser or socketcan; 2026-11-13, the
    charge-interposer reviewer's A, D and D6) is role kvaser: bus.open_kvaser
    brings its link up (kvaser_link.set_link) BEFORE it opens the role with
    its echoes kept, and returns a kvaser_link.WatchedBus (the stall rule;
    a stand-in here since 2026-11-23, when the real one moved onto can-tools'
    canbench.echo_watch); echoes asked for, another bit rate, or a link that
    does not come up is refused with nothing opened;
  - none of it imports canbench's detection, or canbench at all with the
    stand-ins in place.

adapter_roles' own canchg and pcanfd run here, over a stand-in for
can-tools' canbench.bench_layer (FakeBR, set as adapter_roles.LAYER while
they run; since 2026-11-19): what reaches it is what adapter_roles would
hand the pinned layer. adapter_roles_test.py tests adapter_roles against the
real pinned canbench and a fake canctl.

Run: python bus_gs_usb_test.py  (exit 0 = all checks passed)
     python bus_gs_usb_test.py --can-fail
       runs this test against each mutant of bus.py in MUTANTS (a copy in
       a scratch_dir folder, loaded through BUS_UNDER_TEST; bus.py itself
       is never edited); every mutant must make it fail, and the unmutated
       copy must pass.
"""

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
import adapter_roles                                        # noqa: E402

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

    def __init__(self, channel, bitrate=500000, **kw):
        self.opened = dict(channel=channel, bitrate=bitrate, **kw)
        self.shut = False
        self.script = []
        self.channel_info = channel
        super().__init__(channel=channel)

    def _recv_internal(self, timeout):
        if self.script:
            return self.script.pop(0), False
        if timeout:
            time.sleep(min(timeout, 0.01))
        return None, False

    def send(self, m, timeout=None):
        pass

    def shutdown(self):
        self.shut = True
        super().shutdown()


class FakePcan(object):
    def __init__(self, **kw):
        self.opened, self.resets = dict(kw), 0

    def reset(self):
        self.resets += 1

    def shutdown(self):
        pass


class FakeBR(object):
    """Stands in for canbench.bench_layer (adapter_roles.LAYER, since
    2026-11-19): records each open_bench call (role, bitrate, mode as asked,
    None for the layer's default; restore; the keywords) and returns a
    FakeGsUsb for a gs_usb role, a FakePcan for any other."""

    calls = []
    CANCTL = None

    @staticmethod
    def set_guard(fn):
        return None

    @classmethod
    def open_bench(cls, role, bitrate=None, mode=None, restore=True, **kw):
        cls.calls.append((role, bitrate, mode, restore, dict(kw)))
        if role in ("canchg", "canmain"):
            return FakeGsUsb("gs_usb:%s" % role, bitrate, bus=1, address=40)
        return FakePcan(role=role, bitrate=bitrate, **kw)


def _with_layer(fn):
    def call(bitrate, **kw):
        adapter_roles.LAYER = FakeBR
        try:
            return fn(bitrate, **kw)
        finally:
            adapter_roles.LAYER = None
    return call


# bus.open_gs_usb / open_pcan's _roles: adapter_roles' own canchg, pcanfd
# and pcanfd_report, over FakeBR
ROLES = types.SimpleNamespace(canchg=_with_layer(adapter_roles.canchg),
                              pcanfd=_with_layer(adapter_roles.pcanfd),
                              pcanfd_report=_with_layer(adapter_roles.pcanfd_report))


def args(transport, channel, bitrate=500000):
    return types.SimpleNamespace(transport=transport, channel=channel,
                                 port=0, bitrate=bitrate)


def main():
    if sys.argv[1:] == ["--can-fail"]:
        return can_fail()
    return run()


def run():
    gone = [m for m in sys.modules if m == "canbench" or m.startswith("canbench.")]
    for m in gone:
        del sys.modules[m]
    real = (busmod.open_gs_usb, busmod.open_pcan)
    busmod.open_gs_usb = lambda ch, br, **kw: real[0](ch, br, _roles=ROLES, **kw)
    busmod.open_pcan = lambda ch, br, **kw: real[1](ch, br, _roles=ROLES, **kw)
    try:
        return _run(real)
    finally:
        busmod.open_gs_usb, busmod.open_pcan = real


def _try(fn, *a, **kw):
    del FakeBR.calls[:]
    try:
        return fn(*a, **kw), None
    except Exception as e:                              # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


def _open(transport, channel, **kw):
    return _try(busmod.open_bus, args(transport, channel), **kw)


def _run(real):
    print("check_role: --channel names the brand's bench role, nothing else")
    for t, ch in (("pcan", "pcanfd"), ("kvaser", "kvaser"), ("socketcan", "kvaser"),
                  ("gs_usb", "canchg")):
        check(busmod.check_role(t, ch) == ch, "%s %s accepted" % (t, ch))
    for t, ch in (("gs_usb", None), ("gs_usb", "0"), ("gs_usb", "1"), ("gs_usb", "canmain"),
                  ("gs_usb", "kvaser"), ("pcan", None), ("pcan", "PCAN_USBBUS1"),
                  ("pcan", "kvaser"), ("pcan", "canchg"), ("ixxat", None),
                  ("ixxat", "pcanfd"), ("kvaser", "pcanfd")):
        b, err = _open(t, ch)
        check(b is None and err and err.startswith("ValueError") and FakeBR.calls == [],
              "%s %r: refused before anything opens -> %s %s" % (t, ch, err, FakeBR.calls))
    # each opener checks its role itself too, called without open_bus
    for fn, ch in ((real[0], "canmain"), (real[0], "0"), (real[1], "PCAN_USBBUS1"),
                   (real[1], "canchg")):
        b, err = _try(fn, ch, 500000, _roles=ROLES)
        check(b is None and err and err.startswith("ValueError") and FakeBR.calls == [],
              "%s(%r) alone: refused, nothing opened -> %s" % (fn.__name__, ch, err))

    print("gs_usb: role canchg in libusb mode, its echoes filtered")
    b, err = _open("gs_usb", "canchg")
    check(err is None and FakeBR.calls == [("canchg", 500000, None, True, {})],
          "opened by role canchg, libusb mode, no index, bus, address, serial or channel "
          "from the harness -> %s %s" % (err, FakeBR.calls))
    check(type(b) is FakeGsUsb and b.opened["channel"] == "gs_usb:canchg",
          "the bus returned is the one the role opened -> %r" % (b,))
    b.script = [msg(1, False), msg(2, True), msg(3, False), msg(4, False), msg(5, True)]
    got = [b.recv(timeout=0.2), b.recv(timeout=0.2)]
    check([m.arbitration_id for m in got if m] == [0x102, 0x105] and b.script == [],
          "recv() returns only the received frames, reading past three echoes -> %s"
          % [hex(m.arbitration_id) for m in got if m])
    b.shutdown()
    b, err = _open("gs_usb", "canchg", receive_own_messages=True)
    b.script = [msg(1, False), msg(2, True)]
    got = [b.recv(timeout=0.2), b.recv(timeout=0.2)]
    check([(m.arbitration_id, m.is_rx) for m in got if m] == [(0x101, False), (0x102, True)],
          "receive_own_messages=True passes the echo through -> %s"
          % [(hex(m.arbitration_id), m.is_rx) for m in got if m])
    b.shutdown()
    raw = FakeGsUsb(channel="gs_usb:canchg")
    raw.script = [msg(1, False)]
    m = raw.recv(timeout=0.2)
    check(m is not None and not m.is_rx,
          "the bare fake returns the echo (so the drop above is the filter's doing)")
    raw.shutdown()
    raw = FakeGsUsb(channel="gs_usb:canchg")
    busmod.drop_echoes(raw)
    raw.script = [msg(1, False), msg(2, True)]
    m = raw.recv(timeout=0.2)
    check(m is not None and m.arbitration_id == 0x102 and type(raw) is FakeGsUsb,
          "drop_echoes on an open bus: the echo dropped, the bus's class untouched")
    raw.shutdown()
    b, err = _open("gs_usb", "canchg")
    b.script = [msg(i, False) for i in range(6)]
    t0 = time.monotonic()
    m = b.recv(timeout=0.1)
    dt = time.monotonic() - t0
    check(m is None and dt < 1.0, "six echoes and nothing received: None after %.3f s" % dt)
    b.shutdown()

    class Endless(FakeGsUsb):
        def _recv_internal(self, timeout):
            time.sleep(0.001)
            return msg(9, False), False
    import threading
    e = busmod.drop_echoes(Endless(channel="gs_usb:canchg"))
    got = []
    t = threading.Thread(target=lambda: got.append(e.recv(timeout=0.1)), daemon=True)
    t0 = time.monotonic()
    t.start()
    t.join(2.0)
    check(not t.is_alive() and got == [None],
          "echoes without end: None once the timeout is up (%.3f s), not a hang"
          % (time.monotonic() - t0))
    if not t.is_alive():
        e.shutdown()

    # one recv(0) retires every queued echo and reaches the frame behind
    # them, up to ECHOES_PAST_DEADLINE echoes after its deadline (the
    # charge-interposer reviewer, 2026-11-19; it returned None at the first)
    check(busmod.ECHOES_PAST_DEADLINE == 256, "the cap is 256 echoes after the deadline -> %s"
          % busmod.ECHOES_PAST_DEADLINE)
    b, err = _open("gs_usb", "canchg")
    b.script = [msg(i % 200, False) for i in range(200)]
    m = b.recv(timeout=0.0)
    check(m is None and b.script == [],
          "recv(0) with 200 echoes queued: None, every echo retired -> %d left"
          % len(b.script))
    b.script = [msg(i % 200, False) for i in range(200)] + [msg(7, True), msg(8, False)]
    m = b.recv(timeout=0.0)
    check(m is not None and m.arbitration_id == 0x107 and len(b.script) == 1,
          "recv(0) with a frame behind 200 echoes: that frame, in the same call -> %s"
          % (m and hex(m.arbitration_id)))
    b.script = [msg(i % 200, False) for i in range(300)]
    m = b.recv(timeout=0.0)
    check(m is None and len(b.script) == 300 - 257,
          "recv(0) with 300 echoes queued: None after 257 (the cap), %d left" % len(b.script))
    b.shutdown()

    print("gs_usb drain=True: send only, the layer's drain reads it")
    b, err = _open("gs_usb", "canchg", drain=True)
    check(err is None and FakeBR.calls == [("canchg", 500000, None, True, {"drain": True})],
          "drain=True asked of the layer for role canchg -> %s %s" % (err, FakeBR.calls))
    check(err is None and not hasattr(b, "_keep_echoes"),
          "... and no echo filter on it (recv is the drain's) -> %r" % (b,))
    if b is not None:
        b.shutdown()
    b, err = _open("gs_usb", "canchg")
    check(err is None and FakeBR.calls == [("canchg", 500000, None, True, {})]
          and hasattr(b, "_keep_echoes"),
          "drain not asked for: no drain, the echo filter -> %s %s" % (err, FakeBR.calls))
    if b is not None:
        b.shutdown()
    for t, ch, kw in (("pcan", "pcanfd", {}), ("kvaser", "kvaser", {}), ("virtual", None, {}),
                      ("gs_usb", "canchg", {"receive_own_messages": True})):
        b, err = _open(t, ch, drain=True, **kw)
        check(b is None and err and err.startswith("ValueError") and FakeBR.calls == [],
              "drain=True on %s %s: refused before anything opens -> %s" % (t, kw, err))

    print("pcan: role pcanfd, bus states reported, auto_reset, reset() at startup")
    b, err = _open("pcan", "pcanfd")
    check(err is None and FakeBR.calls == [("pcanfd", 500000, None, True,
                                            {"bus_states": "report",
                                             "receive_own_messages": False,
                                             "auto_reset": True})],
          "opened by role pcanfd, pcan mode, bus_states=report, auto_reset, no channel or "
          "device id from the harness -> %s %s" % (err, FakeBR.calls))
    check(err is None and b.resets == 1, "... and reset() once at startup -> %s"
          % (b and b.resets))

    print("the Kvaser: role kvaser on kvaser_usb, link up first")
    import kvaser_link
    calls = []

    class Watched:
        """kvaser_link.WatchedBus's stand-in: since 2026-11-23 the real one
        is over can-tools' canbench.echo_watch (kvaser_link_test tests it),
        and nothing here imports canbench."""

        def __init__(self, bus):
            self.inner = bus

    class Link:
        ROLE, BITRATE = kvaser_link.ROLE, kvaser_link.BITRATE
        LinkError, WatchedBus = kvaser_link.LinkError, Watched
        check_channel = staticmethod(kvaser_link.check_channel)
        fail = None

        @staticmethod
        def set_link(up):
            calls.append(("set_link", up))
            if Link.fail:
                raise kvaser_link.LinkError(Link.fail)
            return "kvaser link up"

        @staticmethod
        def open_bus(channel, receive_own_messages, can_mod=None):
            calls.append(("open_bus", channel, receive_own_messages))
            return FakePcan(interface="socketcan", channel=channel)

    real_kv = busmod.open_kvaser
    busmod.open_kvaser = lambda ch, br, **kw: real_kv(ch, br, _link=Link, **kw)

    def kv(transport="kvaser", channel="kvaser", bitrate=500000, **kw):
        del calls[:]
        try:
            return busmod.open_bus(args(transport, channel, bitrate), **kw), None
        except Exception as e:                      # noqa: BLE001
            return None, "%s: %s" % (type(e).__name__, e)
    try:
        b, err = kv()
        check(err is None and calls == [("set_link", True), ("open_bus", "kvaser", True)],
              "kvaser: the link brought up, then role kvaser opened with its echoes kept "
              "-> %s %s" % (err, calls))
        check(isinstance(b, Watched) and isinstance(b.inner, FakePcan),
              "... and the bus returned keeps the stall rule (link.WatchedBus over the bus "
              "opened) -> %r" % (b,))
        b, err = kv("socketcan")
        check(err is None and calls[-1] == ("open_bus", "kvaser", True),
              "socketcan, channel kvaser: the same opener -> %s %s" % (err, calls))
        b, err = kv(receive_own_messages=True)
        check(err and "receive_own_messages" in err and calls == [],
              "echoes asked for: refused (they are the stall rule's) -> %s" % err)
        b, err = kv(bitrate=250000)
        check(err and "250000" in err and calls == [],
              "another bit rate: refused -> %s" % err)
        Link.fail = "the kvaser link reads down"
        b, err = kv()
        check(err and "LinkError" in err and calls == [("set_link", True)],
              "a link that does not come up: refused, nothing opened -> %s %s"
              % (err, calls))
        # the Kvaser's own channel check, under open_bus's: a call that skips
        # check_role still reaches no adapter on another name
        Link.fail = None
        del calls[:]
        try:
            busmod.open_kvaser("can0", 500000)
            err = None
        except Exception as e:                      # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, e)
        check(err and "LinkError" in err and calls == [],
              "open_kvaser on another name: refused by kvaser_link.check_channel, nothing "
              "set or opened -> %s %s" % (err, calls))
    finally:
        Link.fail = None
        busmod.open_kvaser = real_kv

    print("no detection, no canbench")
    check(not [m for m in sys.modules if m == "canbench" or m.startswith("canbench.")],
          "no canbench module was imported by any of it")

    print("\nbus_gs_usb_test: %s" % ("all checks OK" if not FAILS else
                                     "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


# (what, text in bus.py, its replacement): each must make the test fail.
MUTANTS = [
    ("check_role: any channel", "    if channel not in ok:", "    if False:"),
    ("gs_usb may name canmain", '"gs_usb": ("canchg",)}', '"gs_usb": ("canchg", "canmain")}'),
    ("open_bus does not check the role (ixxat reaches the PCAN)",
     "    check_role(transport, channel)\n\n", "\n"),
    ("open_pcan does not check the role", '    check_role("pcan", channel)\n', ""),
    ("open_gs_usb does not check the role", '    check_role("gs_usb", channel)\n', ""),
    ("the old detection path again (first pcan found)",
     "    return open_pcan(channel, bitrate, receive_own_messages=receive_own_messages,\n"
     "                     logger=logger)\n",
     "    from canbench.live.receiver import detect_all_can_interfaces\n"
     "    return detect_all_can_interfaces(bitrate, brands={transport})[0]\n"),
    ("the PCAN by its channel number",
     "    bus = roles.pcanfd_report(bitrate, receive_own_messages=receive_own_messages,\n"
     "                              auto_reset=True)\n",
     "    bus = roles.pcanfd_report(bitrate, receive_own_messages=receive_own_messages,\n"
     "                              auto_reset=True, channel=\"PCAN_USBBUS1\")\n"),
    ("a PCAN opens without auto_reset",
     "                              auto_reset=True)\n", "                              )\n"),
    ("the PCAN on the default bus states (raised)",
     "    bus = roles.pcanfd_report(bitrate,", "    bus = roles.pcanfd(bitrate,"),
    ("no reset() at startup", "        bus.reset()\n", "        pass\n"),
    ("gs_usb without the echo filter",
     "    return drop_echoes(roles.canchg(bitrate), receive_own_messages)\n",
     "    return roles.canchg(bitrate)\n"),
    ("gs_usb echoes always kept", "    bus._keep_echoes = bool(keep)\n",
     "    bus._keep_echoes = True\n"),
    ("gs_usb echoes never kept",
     "    return drop_echoes(roles.canchg(bitrate), receive_own_messages)\n",
     "    return drop_echoes(roles.canchg(bitrate))\n"),
    ("the echo filter without its timeout",
     "            if end is not None and _t.monotonic() >= end:\n",
     "            if False:\n"),
    ("the echo filter without its cap",
     "                if late > ECHOES_PAST_DEADLINE:\n                    return None, False\n",
     "                pass\n"),
    ("one echo per recv past the deadline (the old filter)",
     "ECHOES_PAST_DEADLINE = 256\n", "ECHOES_PAST_DEADLINE = 0\n"),
    ("a cap of 100 echoes", "ECHOES_PAST_DEADLINE = 256\n", "ECHOES_PAST_DEADLINE = 100\n"),
    ("a cap of 1000 echoes", "ECHOES_PAST_DEADLINE = 256\n", "ECHOES_PAST_DEADLINE = 1000\n"),
    ("drain not passed to open_gs_usb", "                           drain=drain, logger=logger)",
     "                           logger=logger)"),
    ("drain not asked of the layer", "        return roles.canchg(bitrate, drain=True)\n",
     "        return drop_echoes(roles.canchg(bitrate), False)\n"),
    ("drain on any transport", '    if drain and transport != "gs_usb":\n', "    if False:\n"),
    ("drain with echoes asked for",
     "        if receive_own_messages:\n            raise ValueError(\"gs_usb %s: drain",
     "        if False:\n            raise ValueError(\"gs_usb %s: drain"),
    ("gs_usb by index",
     "    return drop_echoes(roles.canchg(bitrate), receive_own_messages)\n",
     "    return drop_echoes(roles.canchg(bitrate, index=0), receive_own_messages)\n"),
    ("the Kvaser not through open_kvaser",
     '    if transport in ("kvaser", "socketcan"):\n        return open_kvaser(',
     '    if False:\n        return open_kvaser('),
    ("the Kvaser's link not brought up", "    line = link.set_link(True)\n",
     '    line = "not set"\n'),
    ("the Kvaser opened before its link",
     "    line = link.set_link(True)\n    if logger:\n"
     "        logger.info(\"Opening the Kvaser: %s\", line)\n"
     "    b = link.open_bus(channel=ch, receive_own_messages=True, can_mod=_can_mod)\n",
     "    b = link.open_bus(channel=ch, receive_own_messages=True, can_mod=_can_mod)\n"
     "    line = link.set_link(True)\n"),
    ("the Kvaser by position", "    ch = link.check_channel(channel)\n",
     "    ch = channel\n"),
    ("the Kvaser without the stall rule", "    return link.WatchedBus(b)\n", "    return b\n"),
    ("the Kvaser's echoes not kept",
     "    b = link.open_bus(channel=ch, receive_own_messages=True, can_mod=_can_mod)\n",
     "    b = link.open_bus(channel=ch, receive_own_messages=False, can_mod=_can_mod)\n"),
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
