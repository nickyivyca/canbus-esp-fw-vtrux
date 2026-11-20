#!/usr/bin/env python3
"""Test of adapter_roles.py: every real bench adapter the harness opens is
opened by its canctl role, through the pinned can-tools' bench-role layer
(canbench.bench_layer), behind shared/bench's bench guard. No hardware, no
real canctl and no real bench log: a fake canctl (a script this test
writes) prints a made-up `canctl roles` table and refuses everything else,
a fake `ip` prints a made-up link, python-can's Bus is a stand-in while a
case runs, and the guard comes from a stand-in bench_lock.

    python3.13 -B charge-interposer/adapter_roles_test.py [--can-fail]

Each case names what it checks:
  1. resolve       with the real pinned canbench over the fake canctl:
                   canchg resolves to its own USB bus and address, not
                   canmain's (both gs_usb, libusb); kvaser to the netdev the
                   table names (not "kvaser"); pcanfd to its device id; a
                   name that is not a role (an index, a channel number, a
                   netdev, a serial) is refused before canctl runs; canctl
                   missing is refused, naming it
  2. layer         with the real pinned bench_layer: layer() sets its canctl
                   to CANCTL and its guard to _guard; pcanfd opens python-
                   can pcan by canctl's device id, canchg python-can gs_usb
                   by bus and address, the Linux fixes first, the guard run
                   first; with the guard refusing, nothing runs (not even
                   `canctl roles`) and nothing opens; a pick (channel,
                   device id, index, bus, address) is refused, nothing
                   opened; canchg found in socketcan mode is handed to
                   libusb through `canctl mode canchg libusb` on CANCTL (the
                   fake refuses it, so nothing opens); the real classifier:
                   a CRTL-only error frame is not a bus error on a SocketCAN
                   bus and is on any other
  3. wiring        with a stand-in layer: each opener asks open_bench for
                   its role, the default mode (None) or linuxcan for
                   kvaser_canlib, restore passed through (True by default),
                   the caller's keywords passed; a name that is not a role
                   refused before the layer is asked; bus_error is the
                   layer's
  4. guard         bench_guard is bench_lock.guard(bench_lock.default_log(),
                   HOLDER_ROLES), the two charge-interposer roles; _guard
                   calls it at every call and raises what it raises; a
                   bench_lock with no guard is BenchGuardMissing
  4a. real-guard   the pinned shared/bench's bench_lock.guard (2026.11.19)
                   on a scratch bench log: a free lock and gen-inhibit's
                   holder refused (NotHeld), this project's tester and
                   implementor under this process's pid passed; with no
                   stand-in, the guard is over bench_lock's default log
  5. kvaser-netdev kvaser_netdev() is the table's netdev in socketcan mode;
                   linuxcan mode, or socketcan with no netdev, refused
  6. scan          can-tools' canbench.bench_scan over every .py in the
                   project but firmware/ (tests too), compared with ALLOWED
                   (file, kind, word, count, why): any hit not allowed, an
                   allowed one seen a different number of times, or an
                   allowed one no longer there, fails; bench_scan's own
                   self_check passes; a planted file with each form is
                   caught; and the project's own forms bench_scan cannot
                   know (adapter_roles' openers given a pick, the pickers
                   removed on 2026-11-18) are caught too
  7. openers       each opener of a real adapter in the harness (OPENERS:
                   file, function, role) asks adapter_roles (or kvaser_link,
                   or the layer by its role) for its role, and none names
                   another role; a planted file with an opener on another
                   role, or none, is caught
--can-fail runs the cases on the unedited adapter_roles.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail.

Charge-interposer tester, 2026-11-18 (the charge-interposer reviewer's
adapter-roles assignment); 2026-11-19, the reviewer's bench-layer item:
can-tools pinned at ec7cefe9, adapter_roles a thin caller of
canbench.bench_layer, the scan canbench.bench_scan; the pin moved to
7a27cc94 the same day (the reviewer's echo item).
"""

import ast
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "adapter_roles.py")
CI_ROLES = ("tester (charge-interposer)", "implementor (charge-interposer)")

FIELDS = ("role", "kind", "mode", "attached", "usb", "busnum", "devnum", "driver", "netdev",
          "link", "tty", "chardev", "devid")
# canctl roles as madhouse prints it, its usb paths and numbers made up
BENCH = {
    "canmain": dict(kind="gs_usb", mode="libusb", attached=1, usb="1-3.2.3", busnum=1,
                    devnum=41, driver="none"),
    "canchg": dict(kind="gs_usb", mode="libusb", attached=1, usb="1-3.3", busnum=1, devnum=40,
                   driver="none"),
    "canx2": dict(kind="slcan", mode="-", attached=1, usb="1-3.4", busnum=1, devnum=10,
                  driver="cdc_acm", tty="/dev/ttyACM1"),
    "kvaser": dict(kind="kvaser", mode="socketcan", attached=1, usb="1-3.2.1", busnum=1,
                   devnum=17, driver="kvaser_usb", netdev="kvnet7", link="up"),
    "pcanfd": dict(kind="pcan", mode="-", attached=1, usb="1-3.2.2", busnum=1, devnum=11,
                   driver="pcan", chardev="pcanusbfd32", devid=3),
}

FAKE_CANCTL = r'''#!%s -B
import sys
here = %r
with open(here + "/calls.txt", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:] != ["roles"]:
    print("canctl: refused")
    sys.exit(2)
sys.stdout.write(open(here + "/roles.txt").read())
'''
# `ip -details -json link show <netdev>`: a CAN link, down, 500k
FAKE_IP = r'''#!%s -B
import json, sys
print(json.dumps([{"ifname": sys.argv[-1], "flags": ["NOARP"], "linkinfo": {"info_data": {
    "bittiming": {"bitrate": 500000}, "ctrlmode": [], "restart_ms": 0, "state": "STOPPED"}}}]))
'''


def table(**changes):
    """canctl roles text for BENCH with each role's fields changed as given
    ({role: {field: value}})."""
    lines = []
    for name, f in BENCH.items():
        v = dict(f, **changes.get(name, {}))
        vals = dict(role=name, **v)
        lines.append(" ".join("%s=%s" % (k, vals.get(k, "none")) for k in FIELDS))
    return "\n".join(lines) + "\n"


def load(path):
    spec = importlib.util.spec_from_file_location("adapter_roles", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _script(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text % sys.executable)
    os.chmod(path, 0o755)
    return path


class Canctl:
    """The fake canctl in work, patched into M; set(**changes) writes its
    table."""

    def __init__(self, M, work, **changes):
        self.work = work
        self.path = os.path.join(work, "canctl")
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_CANCTL % (sys.executable, work))
        os.chmod(self.path, 0o755)
        self.set(**changes)
        open(os.path.join(work, "calls.txt"), "w").close()
        M.CANCTL = self.path

    def set(self, **changes):
        with open(os.path.join(self.work, "roles.txt"), "w", encoding="utf-8") as f:
            f.write(table(**changes))

    def calls(self):
        with open(os.path.join(self.work, "calls.txt"), encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]


def pinned(name):
    import can_tools_pin
    can_tools_pin.use()
    return importlib.import_module("canbench." + name)


class Refusal(Exception):
    """The stand-in guard's refusal."""


class Lock:
    """A stand-in for shared/bench's bench_lock: guard(path, roles) records
    its arguments and returns a callable that counts its calls and raises
    Refusal while .refuse is set."""

    LOG = "/made/up/firmware/bench-log.jsonl"

    def __init__(self):
        self.made, self.calls, self.refuse = [], 0, False

    def default_log(self):
        return self.LOG

    def guard(self, path, roles):
        self.made.append((path, tuple(roles)))

        def g():
            self.calls += 1
            if self.refuse:
                raise Refusal("the bench lock is free (stand-in)")
        return g


def _raises(fn, *a, **kw):
    try:
        got = fn(*a, **kw)
    except Exception as e:                              # noqa: BLE001
        return e
    raise AssertionError("%s did not raise (got %r)" % (getattr(fn, "__name__", fn), got))


def case_resolve(M, work):
    c = Canctl(M, work)
    r = M.resolve("canchg")
    assert r.mode == "libusb" and r.interface == "gs_usb", (r.mode, r.interface)
    assert r.kwargs == {"channel": "gs_usb:canchg", "bus": 1, "address": 40}, r.kwargs
    r = M.resolve("canmain")
    assert r.kwargs["address"] == 41, r.kwargs
    r = M.resolve("kvaser")
    assert r.netdev == "kvnet7" and r.kwargs == {"channel": "kvnet7"}, (r.netdev, r.kwargs)
    r = M.resolve("pcanfd")
    assert r.interface == "pcan" and r.kwargs == {"device_id": 3}, r.kwargs
    assert c.calls() == ["roles"] * 4, c.calls()
    for bad in ("0", 0, "PCAN_USB" "BUS1", "can0", "kvnet7", "00CC00DD5555666677778888",
                "gs_usb", None, "CANCHG"):
        e = _raises(M.resolve, bad)
        assert isinstance(e, M.NotARole) and "not a bench role" in str(e), e
    assert c.calls() == ["roles"] * 4, "canctl ran for a name that is not a role"
    M.CANCTL = os.path.join(work, "no-canctl")
    e = _raises(M.resolve, "canchg")
    assert "not found" in str(e), e


class FakeCan:
    """Stands in for python-can's Bus while a case runs."""

    def __init__(self):
        self.opened = []

    def Bus(self, **kw):
        self.opened.append(kw)
        return Opened(kw)

    def report_bus(self, **kw):
        """Stands in for canbench's CanbenchPcanBus (pcanfd_report)."""
        kw = dict(kw, interface=CPB)
        self.opened.append(kw)
        return Opened(kw)


CPB = "Canbench" "PcanBus"


class Opened:
    """A bus python-can's Bus returned. send() is there because can-tools
    7a27cc94's open_role wraps a libusb gs_usb bus's send (its timeout)."""

    def __init__(self, kw):
        self.kw, self.shut, self.sent, self.inbox = kw, 0, [], []

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def recv(self, timeout=None):
        if self.inbox:
            return self.inbox.pop(0)
        time.sleep(timeout or 0)
        return None

    def shutdown(self):
        self.shut += 1


def _with_fake_can(fn):
    import can
    from canbench.live import linux_fixes
    import canbench.live.receiver as rcv
    from canbench.live import pcan_bus
    fc, applied, released = FakeCan(), [], []
    # canbench's PCAN class, named by string so bench_scan does not take this
    # stand-in for an open
    saved = (can.Bus, linux_fixes.apply, rcv.release_gs_usb, getattr(pcan_bus, CPB))
    can.Bus = fc.Bus
    setattr(pcan_bus, CPB, fc.report_bus)
    linux_fixes.apply = lambda: applied.append(len(fc.opened)) or {}
    rcv.release_gs_usb = lambda b: released.append(b)
    try:
        fn(fc, applied, released)
    finally:
        can.Bus, linux_fixes.apply, rcv.release_gs_usb = saved[:3]
        setattr(pcan_bus, CPB, saved[3])


def case_layer(M, work):
    L = pinned("bench_layer")
    c = Canctl(M, work)
    lock = Lock()
    M.BENCH_LOCK = lock
    saved = (L.SUDO, L.IP_SHOW, L.CANCTL, L.set_guard(None))
    L.SUDO = []                     # the fake canctl runs as this user
    L.IP_SHOW = [_script(os.path.join(work, "ip"), FAKE_IP)]

    def body(fc, applied, released):
        assert M.layer() is L and L.CANCTL == c.path, ("CANCTL not handed to the layer",
                                                       L.CANCTL)
        assert L._guard is M._guard, ("the guard not set", L._guard)
        b = M.pcanfd(500000, auto_reset=True)
        assert fc.opened == [{"interface": "pcan", "bitrate": 500000, "device_id": 3,
                              "auto_reset": True}], fc.opened
        assert applied == [0], ("the Linux fixes not applied before the open", applied)
        assert lock.calls >= 1 and lock.made[-1] == (Lock.LOG, CI_ROLES), (lock.calls,
                                                                            lock.made)
        assert b.bench_role.role.role == "pcanfd"
        # pcanfd_report: the same role, opened as canbench's CanbenchPcanBus
        # (bus_states="report"), the caller's keywords passed
        b = M.pcanfd_report(500000, auto_reset=True)
        assert fc.opened[-1] == {"interface": "CanbenchPcanBus", "bitrate": 500000,
                                 "device_id": 3, "auto_reset": True}, fc.opened
        assert b.bench_role.role.role == "pcanfd"
        b = M.canchg(250000)
        assert fc.opened[-1] == {"interface": "gs_usb", "bitrate": 250000,
                                 "channel": "gs_usb:canchg", "bus": 1, "address": 40}, fc.opened
        b.shutdown()
        assert b.shut == 1 and released == [b], ("libusb claim not released", released)
        # drain=True through adapter_roles.canchg reaches the pinned layer's
        # drain (can-tools 7a27cc94; vehicle_sim --mode replay on canchg, the
        # charge-interposer reviewer, 2026-11-19): what arrives is read and
        # counted by its thread, recv is refused, shutdown stops it. From
        # 262ba7b9 the drain keeps each frame read before the first send
        # (id, extended, data), so the stand-in echo carries them
        b = M.canchg(250000, drain=True)
        echo = type("Echo", (), {"is_error_frame": False, "is_rx": False,
                                 "arbitration_id": 0x123, "is_extended_id": False,
                                 "data": b"\x01"})()
        b.inbox += [echo, echo]
        t_end = time.monotonic() + 2
        while b.bench_drain["echoes"] < 2 and time.monotonic() < t_end:
            time.sleep(0.005)
        assert b.bench_drain["echoes"] == 2 and not b.inbox, ("the drain did not read",
                                                              b.bench_drain)
        e = _raises(b.recv, 0.0)
        assert isinstance(e, L.BenchLayerError) and "drain" in str(e), e
        b.shutdown()
        assert b.shut == 1 and not b.bench_drain["running"] and released[-1] is b, (
            "the drain not stopped, or the device not closed and released", b.bench_drain)
        e = _raises(M.pcanfd, 500000, drain=True)
        assert isinstance(e, L.BenchLayerError) and "drain is for a gs_usb role" in str(e), e
        assert c.calls() and set(c.calls()) == {"roles"}, c.calls()
        # the guard refusing: nothing run, nothing opened
        n, k = len(fc.opened), len(c.calls())
        lock.refuse = True
        for fn in (lambda: M.pcanfd(500000), lambda: M.canchg(500000),
                   lambda: M.open_bench("kvaser", 500000), lambda: M.kvaser_canlib(500000)):
            e = _raises(fn)
            assert isinstance(e, Refusal), e
        assert len(c.calls()) == k, ("canctl ran with the guard refusing", c.calls()[k:])
        lock.refuse = False
        for fn, kw in ((M.pcanfd, {"channel": "PCAN_USB" "BUS1"}), (M.pcanfd, {"device_id": 0}),
                       (M.canchg, {"index": 0}), (M.canchg, {"bus": 1}),
                       (M.canchg, {"address": 41}), (M.canchg, {"channel": "can0"})):
            e = _raises(fn, 500000, **kw)
            assert isinstance(e, L.BenchLayerError) and "refused" in str(e), e
        assert len(fc.opened) == n, ("opened on a refusal", fc.opened[n:])
        # canchg on the kernel's gs_usb: handed to libusb through CANCTL
        c.set(canchg={"mode": "socketcan", "driver": "gs_usb", "netdev": "canchg",
                      "link": "down"})
        e = _raises(M.canchg, 500000)
        assert "canctl mode canchg libusb failed (rc 2, refused)" in str(e), e
        assert "mode canchg libusb" in c.calls(), c.calls()
        assert len(fc.opened) == n, ("opened on a refusal", fc.opened[n:])
        c.set()
        e = _raises(M.open_bench, "kvnet7", 500000)
        assert isinstance(e, M.NotARole), e
    try:
        _with_fake_can(body)
    finally:
        L.SUDO, L.IP_SHOW, L.CANCTL = saved[:3]
        L.set_guard(saved[3])
    # the real classifier, through adapter_roles.bus_error
    E = pinned("errors")

    class Msg:
        is_error_frame = True
        arbitration_id = 0x20000000 | E.CAN_ERR_CRTL

    # the layer reads the class name (made by type() so the scan does not
    # take this stand-in for python-can's class)
    sc = type("Socketcan" + "Bus", (), {})()

    class PcanLike:
        pass
    assert not M.bus_error(sc, Msg), "a CRTL-only frame counted on SocketCAN"
    assert M.bus_error(PcanLike(), Msg), "an error frame not counted off SocketCAN"
    # a wrapper naming the SocketCAN bus as .inner (kvaser_link.WatchedBus)
    wrapped = type("WatchedBus", (), {"inner": sc})()
    assert not M.bus_error(wrapped, Msg), "a wrapped SocketCAN bus classified as the wrapper"
    # canbench's PCAN bus (pcanfd_report) reports in the Linux layout: a
    # state row (CRTL alone) is not an error row there, a bus error is
    from canbench.live import pcan_bus
    cpb = object.__new__(getattr(pcan_bus, CPB))
    assert not M.bus_error(cpb, Msg), "a CRTL-only frame counted on CanbenchPcanBus"
    Msg.arbitration_id = 0x20000000 | E.CAN_ERR_PROT | E.CAN_ERR_BUSERROR
    assert M.bus_error(sc, Msg), "a bus error not counted on SocketCAN"
    assert M.bus_error(wrapped, Msg), "a bus error not counted on a wrapped SocketCAN bus"
    assert M.bus_error(cpb, Msg), "a bus error not counted on CanbenchPcanBus"
    # lost_frames, on the rows can-tools' PCAN bus makes from PCAN-Basic's
    # statuses (errors.from_pcan_status) and the row it hands up when it has
    # reset a bus-off channel (CAN_ERR_RESTARTED): an overrun or a queue
    # overrun is frames lost (can-tools' errors.lost_frames); bus-off and the
    # restart after it are missed frames too (the reviewer's ruling,
    # 2026-11-20: the reader reads nothing while off); error passive, an
    # error warning, back to active, a bus error and a data frame are not
    import can
    for st, want in ((E.PCAN_ERROR_OVERRUN, "frames lost"),
                     (E.PCAN_ERROR_QOVERRUN, "frames lost"),
                     (E.PCAN_ERROR_BUSOFF, "bus-off"),
                     (E.PCAN_ERROR_BUSPASSIVE, None), (E.PCAN_ERROR_BUSHEAVY, None),
                     (E.PCAN_ERROR_OK, None)):
        row = E.from_pcan_status(st)
        assert M.missed(row) == want, ("missed", hex(st), row, M.missed(row), want)
        assert M.lost_frames(row) is (want is not None), ("lost_frames", hex(st), row, want)
    restarted = E.error_frame(E.CAN_ERR_RESTARTED, b"")
    assert M.missed(restarted) == "a restart after bus-off" and M.lost_frames(restarted), \
        ("a RESTARTED row not missed frames", M.missed(restarted))
    ack = E.error_frame(E.CAN_ERR_ACK, bytes(8))
    assert M.missed(ack) is None and M.lost_frames(ack) is False, "a bus error read as missed frames"
    data = can.Message(arbitration_id=0x7F4, data=bytes(8))
    assert M.lost_frames(data) is False and M.missed(data) is None
    assert M.errors() is E, "lost_frames not from the pinned canbench.errors"


class Rec:
    """A stand-in bench_layer recording open_bench and bus_error."""

    def __init__(self):
        self.got, self.guards, self.CANCTL = [], [], None

    def set_guard(self, fn):
        self.guards.append(fn)

    def open_bench(self, role, bitrate=None, mode=None, restore=True, **kw):
        self.got.append((role, bitrate, mode, restore, kw))
        return "bus"

    def bus_error(self, bus, msg):
        self.got.append(("bus_error", bus, msg))
        return "classified"


def case_wiring(M, work):
    R = Rec()
    M.LAYER = R
    M.BENCH_LOCK = Lock()
    assert M.pcanfd(500000, auto_reset=True) == "bus"
    assert M.pcanfd_report(500000, state="passive") == "bus"
    M.canchg(250000)
    M.kvaser_canlib(500000, receive_own_messages=False)
    M.open_bench("canmain", 500000, mode="socketcan", restore=False)
    M.open_bench("kvaser", 500000)
    assert R.got == [("pcanfd", 500000, None, True, {"auto_reset": True}),
                     ("pcanfd", 500000, None, True, {"bus_states": "report",
                                                     "state": "passive"}),
                     ("canchg", 250000, None, True, {}),
                     ("kvaser", 500000, "linuxcan", True, {"receive_own_messages": False}),
                     ("canmain", 500000, "socketcan", False, {}),
                     ("kvaser", 500000, None, True, {})], R.got
    assert R.guards and all(g is M._guard for g in R.guards), R.guards
    assert R.CANCTL == M.CANCTL, R.CANCTL
    n = len(R.got)
    for bad in ("can0", "kvnet7", 0, None, "PCAN"):
        e = _raises(M.open_bench, bad, 500000)
        assert isinstance(e, M.NotARole), e
    assert len(R.got) == n, ("the layer asked for a name that is not a role", R.got[n:])
    assert M.bus_error("b", "m") == "classified" and R.got[-1] == ("bus_error", "b", "m")
    # lost_frames is the errors module's, its answer made a bool
    M.ERRORS = type("E", (), {"lost_frames": staticmethod(lambda m: m == "overrun" and 1)})
    assert M.lost_frames("overrun") is True and M.lost_frames("passive") is False


def case_guard(M, work):
    lock = Lock()
    M.BENCH_LOCK = lock
    g = M.bench_guard()
    assert lock.made == [(Lock.LOG, CI_ROLES)], lock.made
    g()
    M._guard()
    M._guard()
    assert lock.calls == 3, ("_guard does not call the guard each time", lock.calls)
    lock.refuse = True
    e = _raises(M._guard)
    assert isinstance(e, Refusal), e

    class NoGuard:
        @staticmethod
        def default_log():
            return Lock.LOG
    M.BENCH_LOCK = NoGuard
    e = _raises(M._guard)
    assert isinstance(e, M.BenchGuardMissing), e


def case_real_guard(M, work):
    """The pinned shared/bench's bench_lock.guard (tag 2026.11.19 on), on a
    scratch bench log: refuses a free lock and another project's holder,
    passes this project's tester and implementor (a loan) under this
    process's pid; with no stand-in, bench_guard is the pinned module's
    guard over its default log for HOLDER_ROLES."""
    import shared_pin
    shared_pin.use_bench()
    from vtrux_bench import bench_lock as bl
    assert hasattr(bl, "guard"), "the pinned shared/bench has no bench_lock.guard"
    g = M.bench_guard()
    assert g.roles == CI_ROLES and g.path == bl.default_log(), (g.roles, g.path)
    log = os.path.join(work, "bench-log.jsonl")
    bl.init(log, CI_ROLES[0])
    M.BENCH_LOCK = type("BL", (), {"default_log": staticmethod(lambda: log),
                                   "guard": staticmethod(bl.guard)})
    e = _raises(M._guard)
    assert isinstance(e, bl.NotHeld) and "free" in str(e), e
    boards = {b: "not read: test" for b in bl.BOARDS}
    for who in CI_ROLES:
        bl.take(log, who, os.getpid())
        M._guard()
        bl.release(log, who, boards)
    bl.take(log, "tester (gen-inhibit)", os.getpid())
    e = _raises(M._guard)
    assert isinstance(e, bl.NotHeld) and "held by tester (gen-inhibit)" in str(e), e


def case_kvaser_netdev(M, work):
    c = Canctl(M, work)
    assert M.kvaser_netdev() == "kvnet7"
    c.set(kvaser={"netdev": "none", "link": "none"})
    e = _raises(M.kvaser_netdev)
    assert "no netdev" in str(e), e
    c.set(kvaser={"mode": "linuxcan", "driver": "kvaser_usb"})
    e = _raises(M.kvaser_netdev)
    assert "linuxcan" in str(e) or "socketcan" in str(e), e

    # a role table that resolved kvaser in another mode with a netdev
    # still named: refused on the mode alone (a stand-in bench_roles)
    class Other:
        RoleError = pinned("bench_roles").RoleError

        @staticmethod
        def canctl_roles_text(canctl):
            return ""

        @staticmethod
        def resolve(role, text=None):
            return type("R", (), {"mode": "linuxcan", "netdev": "kvnet7"})()
    M.BENCH_ROLES = Other
    e = _raises(M.kvaser_netdev)
    assert "linuxcan" in str(e), e


# ---- the scan -----------------------------------------------------------------

# (file under charge-interposer/ or "<tests>", bench_scan kind, a word its
# reason holds, count or None for any, why it is allowed). Everything else
# bench_scan reports fails the case.
ALLOWED = [
    ("kvaser_link.py", "bus-open", "'socketcan'", 1,
     "the Kvaser's one opener, open_bus: python-can socketcan on role kvaser's netdev, after "
     "IFF_UP reads up (kvaser_link_test checks it)"),
    ("bench_drivers/bench_vport.py", "adapter-class", "PcanBus", 1,
     "its self-test's object.__new__(PcanBus) on a fake PCAN-Basic, to run python-can's own "
     "PCAN reader on made-up rows: never opened"),
    ("bench_drivers/keepalive_probe.py", "pcan-channel", "PCAN_USB" "BUS", 2,
     "its self-test's refusal cases: --pcan-channel " "PCAN_USB" "BUS1 must be refused"),
    ("<tests>", "pcan-channel", "PCAN_USB" "BUS", None,
     "a test's refusal case, the PCAN channel a caller must not be able to give"),
]
# the project's own forms bench_scan cannot know: adapter_roles' openers
# given a pick, and the pickers removed on 2026-11-18 (adapter_fixes)
OWN_OPENERS = {"pcanfd", "pcanfd_report", "canchg", "kvaser_canlib", "open_bench", "resolve",
               "kvaser_netdev"}
PICK_KW = {"channel", "device_id", "index", "bus", "address", "serial", "interface"}
REMOVED = {"canchg_target", "find_canchg", "adapter_fixes"}
# adapter_roles.<opener>(...) calls outside tests: 16 on 2026-11-19, so a
# scan that finds far fewer is reading the wrong files
MIN_ROLE_CALLS = 15


def is_test(rel):
    base = os.path.basename(rel)
    return base.endswith("_test.py") or base.startswith("test_")


def project_files(root=HERE):
    """Every tracked .py under charge-interposer/ but firmware/, relative,
    with adapter_roles.py and this file however git has them."""
    r = subprocess.run(["git", "-C", root, "ls-files", "--", "."], capture_output=True,
                       text=True, check=True)
    out = {rel for rel in r.stdout.splitlines()
           if rel.endswith(".py") and not rel.startswith("firmware/")}
    out |= {"adapter_roles.py", "adapter_roles_test.py"}
    return sorted(rel for rel in out if os.path.exists(os.path.join(root, rel)))


def judge_hits(hits, allowed=ALLOWED):
    """-> lines, one per hit not allowed and per allow entry seen a wrong
    number of times (none: every hit allowed, every entry used)."""
    bad, seen = [], [0] * len(allowed)
    for h in hits:
        for i, (f, kind, word, _n, _why) in enumerate(allowed):
            where = is_test(h.path) if f == "<tests>" else h.path == f
            if where and h.kind == kind and word in h.why:
                seen[i] += 1
                break
        else:
            bad.append("not allowed: %s" % h)
    for (f, kind, word, n, _why), k in zip(allowed, seen):
        if (n is None and k == 0) or (n is not None and k != n):
            bad.append("allowed %s [%s] %s: seen %d times, %s allowed" % (
                f, kind, word, k, "some" if n is None else n))
    return bad


def own_forms(paths, root):
    """-> (hits ["path:line what"], adapter_roles opener calls seen): the
    project's own forms (OWN_OPENERS given a pick, REMOVED named)."""
    hits, n = [], 0
    for rel in paths:
        p = os.path.join(root, rel)
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in REMOVED:
                hits.append("%s:%d names %s" % (rel, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in REMOVED:
                hits.append("%s:%d names %s" % (rel, node.lineno, node.attr))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for nm in [a.name for a in node.names] + [getattr(node, "module", None) or ""]:
                    if nm.split(".")[-1] in REMOVED:
                        hits.append("%s:%d imports %s" % (rel, node.lineno, nm))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "adapter_roles" and node.func.attr in OWN_OPENERS):
                n += 1
                bad = sorted(k.arg for k in node.keywords if k.arg in PICK_KW)
                if bad:
                    hits.append("%s:%d adapter_roles.%s() given %s" % (
                        rel, node.lineno, node.func.attr, ", ".join(bad)))
    return hits, n


def case_scan(M, work):
    S = pinned("bench_scan")
    assert S.self_check() == [], S.self_check()
    rels = project_files()
    assert len(rels) > 60, len(rels)
    nontest = [r for r in rels if not is_test(r)]
    hits = S.scan_paths([os.path.join(HERE, r) for r in rels], HERE)
    bad = judge_hits(hits)
    assert not bad, bad
    own, n = own_forms(nontest, HERE)
    assert not own, own
    assert n >= MIN_ROLE_CALLS, "only %d adapter_roles opener calls found" % n
    # planted: each form is caught, and the allow list is exact
    d = os.path.join(work, "planted")
    os.makedirs(os.path.join(d, "bench_drivers"))
    src = {
        "x.py": ("import can\n"
                 "a = can.Bus(interface='pcan', channel=0)\n"
                 "b = can.interface.Bus(interface='gs_usb', index=0)\n"
                 "from can.interfaces.gs_usb import GsUsbBus\n"
                 "c = adapter_roles.pcanfd(500000, device_id=0)\n"
                 "d = adapter_roles.canchg(500000, index=0)\n"
                 "from canbench.live.receiver import detect_all_can_interfaces\n"
                 "e = adapter_fixes.canchg_target(0)\n"
                 "f = can.Bus(interface='udp_multicast', channel='239.0.2.1')\n"
                 "g = adapter_roles.pcanfd(500000, auto_reset=True)\n"
                 "h = can_mod.Bus(**kw)\n"
                 "i = bench_layer.open_bench('canchg', 500000, bus=1)\n"
                 "j = GsUsb.scan()\n"),
        # an allowed file with one more hit than allowed
        "kvaser_link.py": ("b = can_mod.Bus(interface='socketcan', channel=dev)\n"
                           "c = can_mod.Bus(interface='socketcan', channel='kvaser')\n"),
        "bench_drivers/keepalive_probe.py": "A = 'PCAN_USB' 'BUS1'\n",
        "y_test.py": "A = ['PCAN_USB' 'BUS1', 'PCAN_USB' 'BUS2']\nimport can\nb = can.Bus(interface='kvaser')\n",
    }
    for rel, text in src.items():
        with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
            f.write(text)
    hits = S.scan_paths([os.path.join(d, r) for r in sorted(src)], d)
    got = judge_hits(hits)
    xl = sorted({int(g.split(":")[2].split()[0]) for g in got
                 if g.startswith("not allowed: x.py:")})
    assert xl == [2, 3, 4, 7, 11, 12, 13], got
    assert any(g.startswith("allowed kvaser_link.py") and "seen 2 times, 1 allowed" in g
               for g in got), got
    assert any(g.startswith("allowed bench_drivers/keepalive_probe.py") and "seen 1 times" in g
               for g in got), got
    assert any(g.startswith("not allowed: y_test.py:3") for g in got), got
    assert not any("y_test.py:1" in g for g in got), ("a test's PCAN channel refused", got)
    assert any("bench_vport.py" in g and "seen 0 times" in g for g in got), (
        "an allow entry no longer used passes", got)
    own, n = own_forms(["x.py"], d)
    assert sorted(int(o.split(":")[1].split()[0]) for o in own) == [5, 6, 8, 8], own
    assert n == 3, n


# ---- the openers --------------------------------------------------------------

# (file under charge-interposer/, function or "<module>", role): every place
# the harness opens or resolves a real adapter, or changes its link or mode,
# with the role it must ask for
OPENERS = [
    ("bus.py", "open_pcan", "pcanfd"),
    ("bus.py", "open_gs_usb", "canchg"),
    ("bus.py", "open_kvaser", "kvaser"),
    ("kvaser_link.py", "netdev", "kvaser"),     # open_bus opens on it: kvaser_link_test
    ("kvaser_link.py", "set_link", "kvaser"),
    ("kvaser_link.py", "_recover_step", "kvaser"),
    ("canchg_mode.py", "start", "canchg"),
    ("canchg_mode.py", "end", "canchg"),
    ("diag_cadence.py", "start", "pcanfd"),
    ("bench_drivers/bench_usb_host.py", "open_pcan", "pcanfd"),
    ("bench_drivers/bench_usb_host.py", "open_innomaker", "canchg"),
    ("bench_drivers/bench_usb_host.py", "open_kvaser", "kvaser"),
    ("bench_drivers/bench_charger_witness.py", "open_gs_usb", "canchg"),
    ("bench_drivers/bench_topology.py", "open_gs_usb", "canchg"),
    ("bench_drivers/bench_topology.py", "<module>", "pcanfd"),
    ("bench_drivers/bench_rate_feasibility.py", "open_gs_usb", "canchg"),
    ("bench_drivers/bench_rate_feasibility.py", "<module>", "pcanfd"),
    ("bench_drivers/bench_rate_feasibility.py", "open_witness", "kvaser"),
    ("bench_drivers/bench_hold12.py", "proc_identifier", "pcanfd"),
    ("bench_drivers/bench_powerup_round.py", "__init__", "pcanfd"),
    ("bench_drivers/bench_vport.py", "_open_witness_reader", "pcanfd"),
    ("bench_drivers/bench_mcp_reset.py", "open_innomaker", "canchg"),
    ("bench_drivers/keepalive_probe.py", "open_all", "pcanfd"),
    ("bench_drivers/keepalive_probe.py", "open_all", "kvaser"),
    ("bench_drivers/reset_check.py", "open_all", "pcanfd"),
]
# how each role is asked for: the adapter_roles opener of that role, or a
# role call with it named (ROLE_CALLS), or (kvaser) kvaser_link's
ASKS = {"pcanfd": {"pcanfd", "pcanfd_report"}, "canchg": {"canchg"},
        "kvaser": {"kvaser_netdev", "kvaser_canlib", "open_bus", "set_link"}}
ROLE_CALLS = {"open_role", "resolve", "open_bench", "link_up", "link_down", "to_libusb",
              "to_socketcan", "set_mode", "canctl"}
ROLE_CONSTS = {"ROLE"}      # a module's ROLE constant names its role (kvaser_link, canchg_mode)
# the charge-interposer reviewer's PCAN item 2 (2026-11-20): the PCAN readers
# of the audit open the role with bus_states="report" (pcanfd_report), and
# bench_vport's witness stays on the default (pcanfd). (file, function)
REPORT_READERS = [
    ("bus.py", "open_pcan"),
    ("diag_cadence.py", "start"),
    ("bench_drivers/bench_usb_host.py", "open_pcan"),       # Rx, and rx_proc's ProcReader
    ("bench_drivers/bench_powerup_round.py", "__init__"),   # Witness
    ("bench_drivers/bench_rate_feasibility.py", "<module>"),
    ("bench_drivers/keepalive_probe.py", "open_all"),
    ("bench_drivers/reset_check.py", "open_all"),
    # the reviewer's ruling (4), 2026-11-20, after run 40e6
    ("bench_drivers/bench_hold12.py", "proc_identifier"),
    ("bench_drivers/bench_topology.py", "<module>"),
]
DEFAULT_READERS = [("bench_drivers/bench_vport.py", "_open_witness_reader")]
# opens the PCAN once with each option and checks both (ruling (4))
BOTH_READERS = [("bench_drivers/bench_layer_check.py", "observe")]
PCAN_OPENERS = {"pcanfd", "pcanfd_report"}


def _scope(tree, name):
    if name == "<module>":
        return [tree]
    return [x for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
            and x.name == name]


def asks(tree, scope_name, src_consts=None):
    """-> the set of roles the scope asks for, from its calls."""
    got = set()
    for sc in _scope(tree, scope_name):
        nodes = ast.walk(sc) if scope_name != "<module>" else [
            n for top in sc.body if not isinstance(top, (ast.FunctionDef, ast.ClassDef))
            for n in ast.walk(top)]
        for node in nodes:
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            a = node.func.attr
            for role, fns in ASKS.items():
                if a in fns:
                    got.add(role)
            if a in ROLE_CALLS and node.args:
                # canctl("usbreset", ROLE): the role is its second argument
                for arg in node.args[:2]:
                    if isinstance(arg, ast.Constant) and arg.value in ("canmain", "canchg",
                                                                       "canx2", "kvaser",
                                                                       "pcanfd"):
                        got.add(arg.value)
                    elif isinstance(arg, ast.Name) and arg.id in ROLE_CONSTS and src_consts:
                        got.add(src_consts.get(arg.id))
    return got


def _consts(tree):
    out = {}
    for top in tree.body:
        if isinstance(top, ast.Assign) and isinstance(top.value, ast.Constant):
            for t in top.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = top.value.value
    return out


def pcan_opens(tree, scope_name):
    """-> the PCAN openers (pcanfd, pcanfd_report) the scope calls."""
    got = set()
    for sc in _scope(tree, scope_name):
        nodes = ast.walk(sc) if scope_name != "<module>" else [
            n for top in sc.body if not isinstance(top, (ast.FunctionDef, ast.ClassDef))
            for n in ast.walk(top)]
        got |= {n.func.attr for n in nodes if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr in PCAN_OPENERS}
    return got


def check_bus_states(report, default, root, both=()):
    """-> a line per reader that opens the PCAN other than as ruled: each of
    report only through pcanfd_report, each of default only through
    pcanfd, each of both through both."""
    bad = []
    for want, readers in (({"pcanfd_report"}, report), ({"pcanfd"}, default),
                          ({"pcanfd", "pcanfd_report"}, both)):
        for rel, fn in readers:
            p = os.path.join(root, rel)
            with open(p, encoding="utf-8") as f:
                got = pcan_opens(ast.parse(f.read(), p), fn)
            if got != want:
                bad.append("%s %s opens the PCAN through %s, not %s"
                           % (rel, fn, sorted(got) or "nothing", " and ".join(sorted(want))))
    return bad


def check_openers(openers, root):
    bad = []
    for rel, fn, role in openers:
        p = os.path.join(root, rel)
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        if not _scope(tree, fn):
            bad.append("%s: no %s" % (rel, fn))
            continue
        got = asks(tree, fn, _consts(tree))
        if role not in got:
            bad.append("%s %s asks for %s, not %s" % (rel, fn, sorted(got) or "nothing", role))
    return bad


def case_openers(M, work):
    bad = check_openers(OPENERS, HERE)
    assert not bad, bad
    d = os.path.join(work, "planted")
    os.makedirs(d)
    with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as f:
        f.write("ROLE = 'canchg'\n"
                "def a():\n    return adapter_roles.canchg(500000)\n"
                "def b():\n    return adapter_roles.pcanfd(500000)\n"
                "def c():\n    return L.to_libusb(ROLE)\n"
                "def d():\n    return 1\n"
                "e = adapter_roles.open_bench('pcanfd', 1)\n"
                "def f():\n    return L.canctl('usbreset', 'kvaser')\n")
    got = check_openers([("x.py", "a", "canchg"), ("x.py", "b", "canchg"),
                         ("x.py", "c", "canchg"), ("x.py", "d", "pcanfd"),
                         ("x.py", "<module>", "pcanfd"), ("x.py", "zz", "pcanfd"),
                         ("x.py", "f", "kvaser"), ("x.py", "f", "canchg")], d)
    assert [g.split()[1] for g in got] == ["b", "d", "no", "f"], got
    # the bus states: the ruled readers report, bench_vport's witness does not
    bad = check_bus_states(REPORT_READERS, DEFAULT_READERS, HERE, BOTH_READERS)
    assert not bad, bad
    # can-fail on the real sources: each ruled reader, its pcanfd_report put
    # back to pcanfd in a copy, is caught; so is bench_vport's witness moved
    # to pcanfd_report, and bench_layer_check's second open taken out
    for rel, fn in REPORT_READERS + DEFAULT_READERS + BOTH_READERS:
        with open(os.path.join(HERE, rel), encoding="utf-8") as f:
            src = f.read()
        if (rel, fn) in DEFAULT_READERS:
            mut = src.replace("adapter_roles.pcanfd(", "adapter_roles.pcanfd_report(")
        elif (rel, fn) in BOTH_READERS:
            mut = src.replace("pcanfd_report(", "pcanfd(")
        else:
            mut = src.replace(".pcanfd_report(", ".pcanfd(")
        assert mut != src, (rel, "nothing to revert")
        mdir = os.path.join(d, "revert", os.path.dirname(rel))
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(d, "revert", rel), "w", encoding="utf-8") as f:
            f.write(mut)
        got = check_bus_states([x for x in REPORT_READERS if x[0] == rel],
                               [x for x in DEFAULT_READERS if x[0] == rel],
                               os.path.join(d, "revert"),
                               [x for x in BOTH_READERS if x[0] == rel])
        assert got, ("a reverted %s %s not caught" % (rel, fn))
    with open(os.path.join(d, "y.py"), "w", encoding="utf-8") as f:
        f.write("def r():\n    return adapter_roles.pcanfd_report(500000)\n"
                "def p():\n    return adapter_roles.pcanfd(500000)\n"
                "def both():\n    adapter_roles.pcanfd(1)\n    adapter_roles.pcanfd_report(1)\n")
    got = check_bus_states([("y.py", "r"), ("y.py", "p"), ("y.py", "both")],
                           [("y.py", "p"), ("y.py", "r")], d)
    assert [g.split()[1] for g in got] == ["p", "both", "r"], got


CASES = [case_resolve, case_layer, case_wiring, case_guard, case_real_guard, case_kvaser_netdev,
         case_scan,
         case_openers]

MUTANTS = [
    ("any name taken as a role", "    if role not in ROLES:", "    if False:"),
    ("canmain left out of the roles",
     'ROLES = ("canmain", "canchg", "canx2", "kvaser", "pcanfd")',
     'ROLES = ("canchg", "canx2", "kvaser", "pcanfd")'),
    ("the role table not from CANCTL",
     "    return br.resolve(role, text=br.canctl_roles_text(CANCTL))",
     "    return br.resolve(role, text=br.canctl_roles_text())"),
    ("the layer's canctl not CANCTL", "    L.CANCTL = CANCTL\n", ""),
    ("no guard set on the layer", "    L.set_guard(_guard)\n", ""),
    ("the guard made but not run", "    bench_guard()()", "    bench_guard()"),
    ("the implementor's loan left out",
     'HOLDER_ROLES = ("tester (charge-interposer)", "implementor (charge-interposer)")',
     'HOLDER_ROLES = ("tester (charge-interposer)",)'),
    ("the guard on another log", "    return bl.guard(bl.default_log(), HOLDER_ROLES)",
     '    return bl.guard("bench-log.jsonl", HOLDER_ROLES)'),
    ("a bench_lock with no guard not refused",
     '    if not hasattr(bl, "guard"):', "    if False:"),
    ("pcanfd opens another role", '    return open_bench("pcanfd", bitrate, **kw)',
     '    return open_bench("canmain", bitrate, **kw)'),
    ("canchg opens canmain", '    return open_bench("canchg", bitrate, **kw)',
     '    return open_bench("canmain", bitrate, **kw)'),
    ("kvaser_canlib in the default mode",
     '    return open_bench("kvaser", bitrate, mode="linuxcan", **kw)',
     '    return open_bench("kvaser", bitrate, **kw)'),
    ("restore always on",
     "    return layer().open_bench(role, bitrate, mode=mode, restore=restore, **kw)",
     "    return layer().open_bench(role, bitrate, mode=mode, restore=True, **kw)"),
    ("the mode dropped",
     "    return layer().open_bench(role, bitrate, mode=mode, restore=restore, **kw)",
     "    return layer().open_bench(role, bitrate, restore=restore, **kw)"),
    ("the caller's keywords dropped",
     "    return layer().open_bench(role, bitrate, mode=mode, restore=restore, **kw)",
     "    return layer().open_bench(role, bitrate, mode=mode, restore=restore)"),
    ("open_bench takes any name",
     "    _check(role)\n    return layer().open_bench(", "    return layer().open_bench("),
    ("kvaser_netdev in any mode", '    if res.mode != "socketcan" or not res.netdev:',
     "    if not res.netdev:"),
    ("every error frame a bus error", "    return L.bus_error(getattr(bus, \"inner\", bus), msg)",
     "    return bool(msg.is_error_frame)"),
    ("a wrapper classified as itself", "    return L.bus_error(getattr(bus, \"inner\", bus), msg)",
     "    return L.bus_error(bus, msg)"),
    ("pcanfd_report on the default bus states",
     '    return open_bench("pcanfd", bitrate, bus_states="report", **kw)',
     '    return open_bench("pcanfd", bitrate, **kw)'),
    ("pcanfd_report opens another role",
     '    return open_bench("pcanfd", bitrate, bus_states="report", **kw)',
     '    return open_bench("canmain", bitrate, bus_states="report", **kw)'),
    ("pcanfd_report drops the caller's keywords",
     '    return open_bench("pcanfd", bitrate, bus_states="report", **kw)',
     '    return open_bench("pcanfd", bitrate, bus_states="report")'),
    ("pcanfd reports too (bench_vport's witness off the default)",
     '    return open_bench("pcanfd", bitrate, **kw)\n',
     '    return open_bench("pcanfd", bitrate, bus_states="report", **kw)\n'),
    ("no frame ever lost", "    return missed(msg) is not None", "    return False"),
    ("lost frames read as any error frame", "    return missed(msg) is not None",
     "    return bool(msg.is_error_frame)"),
    ("an overrun not frames lost", "    if E.lost_frames(msg):\n        return \"frames lost\"",
     "    if False:\n        return \"frames lost\""),
    ("bus-off not missed frames", "    if state == \"busoff\":\n        return \"bus-off\"",
     "    if state == \"busoff\":\n        return None"),
    ("a restart after bus-off not missed frames",
     "    if state == \"restarted\":\n        return \"a restart after bus-off\"",
     "    if state == \"restarted\":\n        return None"),
    ("error passive read as missed frames", "        return \"a restart after bus-off\"\n    return None",
     "        return \"a restart after bus-off\"\n    return state and \"state\""),
    ("the errors stand-in ignored", "    if ERRORS is not None:\n        return ERRORS\n", ""),
]


def run_cases(path, base):
    bad = []
    for c in CASES:
        M = load(path)
        work = os.path.join(base, c.__name__)
        os.makedirs(work)
        try:
            c(M, work)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                          # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    if not sys.platform.startswith("linux"):
        print("adapter_roles_test: the bench roles are canctl's, on the Linux bench host only; "
              "nothing to test here")
        return 0
    with scratch_dir.run("adapter-roles-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nadapter_roles_test: %s" % ("all checks OK" if not bad
                                               else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited adapter_roles.py", flush=True)
        if run_cases(MODULE, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        with open(MODULE, encoding="utf-8") as f:
            src = f.read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, "adapter_roles.py")
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
