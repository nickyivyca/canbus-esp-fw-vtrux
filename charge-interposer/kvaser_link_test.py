#!/usr/bin/env python3
"""Test of kvaser_link.py: the Kvaser's link state per check, its one
opener, the TX stall rule, the recovery after a stall and the kernel's
lines on a VOID. No hardware, no real canctl and no real kernel log: a
stand-in for can-tools' bench-role layer acts on a made-up
/sys/class/net/<the role's netdev>, a fake bus and clock drive the stall
rule, and the kernel log is planted.

    python3.13 -B charge-interposer/kvaser_link_test.py [--can-fail]

Each case names what it checks:
  1. link-state     IFF_UP set reads "up", clear reads "down"; a missing
                    netdev or unparseable flags is LinkError (the check
                    VOID), never a state; read on the netdev a stand-in
                    role table gives role kvaser (not named "kvaser", so a
                    sysfs holding only "kvaser" does not read); a role that
                    does not resolve, or resolves to nothing or a path, is
                    LinkError (shared/bench SPEC section 8, 2026-11-18)
  2. set-up         an ACK check: the layer's link_up(kvaser, 500000)
                    (exactly that), once, its read-back on the line; with
                    no stand-in, the layer is adapter_roles.layer()'s
  2a. self-test     a process run with --self-test: set_link refuses both
                    ways and recover fails, stopping the bench, with the
                    layer never asked
  3. set-down       a no-ACK check: the layer's link_down(kvaser)
  4. not-taken      the layer's failure (a read-back not as asked, canctl's
                    rc 3, the guard refusing) is LinkError naming the step
                    and what the layer said; off Linux it is LinkError and
                    the layer is not asked
  (the layer itself, canctl and its read-back with `ip`, is can-tools'
  bench_layer, tested there; since 2026-11-19 the reviewer's bench-layer
  item)
  5. open-bus       python-can's socketcan on the role table's netdev for
                    role kvaser, echoes kept, after IFF_UP reads set;
                    refused, opening nothing, when the role does not
                    resolve, when the link is down or unreadable, when the
                    channel is None (a netdev by position), another name or
                    the netdev itself rather than the role, and off Linux
  6. echo-watch     every send echoed: sent count = sends, no stall; an
                    echo missing: no stall at 99 ms after send returned,
                    Stall past 100 ms with that time; ENOBUFS on send:
                    Stall; another send error raised as itself; frames
                    that are not own echoes go to on_rx; finish() waits for
                    a late echo and raises Stall for one that never comes
  7. kernel-lines   a planted kernel log: its usb 1-3.2*, kvaser_usb, pcan,
                    gs_usb and -71/EPROTO lines (and only those) on the
                    VOID line with their times, the window named; after
                    the hub swap (2026-11-16), any driver's line for a
                    device under 1-3 (usb 1-3.1, cdc_acm 1-3.2.4), a bench
                    device's enumeration on another port by its ID, and
                    "disabled by hub" and "error -32" anywhere, but not
                    another bus's port 3 (usb 11-3) or a non-bench device's
                    ID on another port; the
                    count of lines past KLOG_MAX said; an unreadable log
                    said, not taken as "none"
  8. recover        the layer's link_down, canctl usbreset, link_up 500000,
                    in that order: ok, no STOP; any step's failure: not ok,
                    no later step, and STOP written, with the reason, to the
                    file VTRUX_KVASER_STOP names; with it unset, not ok and
                    saying the bench was not told
  9. stall-lines    a stall's record: the VOID line ("kvaser TX stall", its
                    time, the planted kernel line) first, the check's socket
                    closed before the first canctl call, the recovery's line
                    last
 10. scan           no harness .py (tests aside) opens CANlib's "kvaser"
                    interface, and none but kvaser_link.py names the
                    "socketcan" interface; at least MIN_OPENERS calls of
                    kvaser_link.open_bus; a planted file with each form is
                    caught, so the scan can fail
--can-fail runs the cases on the unedited kvaser_link.py first (the
control, which must pass), then on edited copies, one edit each; every edit
must make a case fail. The edits include a stall taken for a result (the
bound never checked), the read-back dropped, and the kernel lines left off
the VOID line.

Charge-interposer tester, 2026-11-13 (the charge-interposer reviewer's
rulings A, the stall rule, D and D5, and the kernel-log addendum).
"""

import ast
import errno
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "kvaser_link.py")
MIN_OPENERS = 8     # keepalive, usb_host, rx_order, topology, charger_silent,
                    # switch_loss, hold12, vport (bus.open_kvaser calls it as link.)

class LayerError(RuntimeError):
    """The stand-in layer's failure (bench_layer.BenchLayerError's place)."""


class FakeLayer:
    """A stand-in for canbench.bench_layer (adapter_roles.layer()) acting on
    the made-up sysfs: link_up(role, bitrate) sets IFF_UP, link_down and
    canctl("usbreset", role) clear it, each call written to the bench's
    log as "link_up kvaser 500000", "link_down kvaser", "canctl usbreset
    kvaser". fail={step: text}: that step raises LayerError(text) and
    changes nothing (a canctl that exits non-zero, a read-back that is not
    what was asked, the guard refusing)."""

    def __init__(self, bench, fail=None):
        self.b, self.fail = bench, dict(fail or {})

    def _do(self, step, line, up):
        with open(self.b.log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if step in self.fail:
            raise LayerError(self.fail[step])
        flags = int(open(self.b.flags).read().strip(), 16)
        self.b.set_flags(flags | 1 if up else flags & ~1)
        return {"up": up, "bitrate": 500000 if up else None}

    def link_up(self, role, bitrate, restart_ms=None):
        return self._do("link_up", "link_up %s %s" % (role, bitrate), True)

    def link_down(self, role):
        return self._do("link_down", "link_down %s" % role, False)

    def canctl(self, *args):
        assert args == ("usbreset", "kvaser"), args
        self._do("usbreset", "canctl " + " ".join(args), False)
        return "kvaser: usbreset"


def load(path):
    spec = importlib.util.spec_from_file_location("kvaser_link", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# the netdev the stand-in role table gives role kvaser: not the role's own
# name, so a netdev the harness named itself is caught (SPEC section 8)
NETDEV = "kvnet7"


class FakeRoles:
    """A stand-in for adapter_roles: role kvaser resolves to `dev`, or
    raises `err`."""

    def __init__(self, dev=NETDEV, err=None):
        self.dev, self.err, self.asked = dev, err, 0

    def kvaser_netdev(self):
        self.asked += 1
        if self.err:
            raise RuntimeError(self.err)
        return self.dev


class Bench:
    """A made-up /sys/class/net with the role table's netdev for kvaser, a
    stand-in role table and a stand-in layer on it, patched into the module
    M."""

    def __init__(self, M, work, up=False, fail=None):
        self.M, self.work = M, work
        self.net = os.path.join(work, "sys", "class", "net")
        os.makedirs(os.path.join(self.net, NETDEV))
        self.roles = M.ROLES = FakeRoles()
        self.flags = os.path.join(self.net, NETDEV, "flags")
        self.set_flags(0x40081 if up else 0x40080)
        self.log = os.path.join(work, "calls.txt")
        open(self.log, "w").close()
        self.layer = M.LAYER = FakeLayer(self, fail)

    def set_flags(self, v):
        with open(self.flags, "w") as f:
            f.write("0x%x\n" % v)

    def calls(self):
        with open(self.log, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]


def _raises(exc, fn, *a, **kw):
    try:
        got = fn(*a, **kw)
    except exc as e:
        return str(e)
    raise AssertionError("%s not raised (got %r)" % (exc.__name__, got))


def case_link_state(M, work):
    b = Bench(M, work, up=True)
    assert M.link_state(b.net) == "up"
    b.set_flags(0x40080)
    assert M.link_state(b.net) == "down"
    b.set_flags(0x1003)
    assert M.link_state(b.net) == "up"
    with open(b.flags, "w") as f:
        f.write("garbage\n")
    _raises(M.LinkError, M.link_state, b.net)
    os.unlink(b.flags)
    e = _raises(M.LinkError, M.link_state, b.net)
    assert "cannot be read" in e, e
    _raises(M.LinkError, M.link_state, os.path.join(work, "nothing"))
    # read on the role table's netdev, never one named here: a sysfs that
    # has only "kvaser" (the role's name) does not read
    os.makedirs(os.path.join(work, "named", "kvaser"))
    with open(os.path.join(work, "named", "kvaser", "flags"), "w") as f:
        f.write("0x40081\n")
    _raises(M.LinkError, M.link_state, os.path.join(work, "named"))
    # a role that does not resolve, or resolves to a path, is LinkError
    b.set_flags(0x40081)
    for roles in (FakeRoles(err="kvaser is not attached"), FakeRoles(dev=""),
                  FakeRoles(dev="../kvnet7"), FakeRoles(dev=None)):
        M.ROLES = roles
        e = _raises(M.LinkError, M.link_state, b.net)
        assert "role kvaser" in e, e


def case_set_up(M, work):
    b = Bench(M, work, up=False)
    line = M.set_link(True, b.net, "linux")
    assert b.calls() == ["link_up kvaser 500000"], b.calls()
    assert M.link_state(b.net) == "up" and "bench_layer.link_up, read back UP at 500000" in line, \
        line
    # with no LAYER, the layer is adapter_roles.layer()'s (the guard set there)
    M.LAYER = None
    saved = sys.modules.get("adapter_roles")
    sys.modules["adapter_roles"] = type("AR", (), {"layer": staticmethod(lambda: b.layer)})
    try:
        M.set_link(False, b.net, "linux")
    finally:
        if saved is None:
            del sys.modules["adapter_roles"]
        else:
            sys.modules["adapter_roles"] = saved
    assert b.calls()[-1] == "link_down kvaser", b.calls()


def case_self_test_guard(M, work):
    """A process run with --self-test never touches the link: set_link
    raises, recover fails and stops the bench, and the layer is never
    asked."""
    b = Bench(M, work, up=True)
    stop = os.path.join(work, "STOP")
    saved = list(sys.argv)
    sys.argv[:] = ["bench_x.py", "--self-test"]
    try:
        e = _raises(M.LinkError, M.set_link, False, b.net, "linux")
        e2 = _raises(M.LinkError, M.set_link, True, b.net, "linux")
        ok, line = M.recover(b.net, {M.STOP_ENV: stop}, "linux")
    finally:
        sys.argv[:] = saved
    assert "never touches" in e and "never touches" in e2 and b.calls() == [], (e, b.calls())
    assert not ok and "never touches" in line and os.path.exists(stop), line
    assert M.self_test_refusal(["x.py", "--self-test"]) and not M.self_test_refusal(["x.py"])


def case_set_down(M, work):
    b = Bench(M, work, up=True)
    line = M.set_link(False, b.net, "linux")
    assert b.calls() == ["link_down kvaser"], b.calls()
    assert M.link_state(b.net) == "down" and "read back not UP" in line, line


def case_not_taken(M, work):
    """The layer's failure (canctl's exit, a read-back not as asked, the
    guard) is LinkError naming the step and what the layer said."""
    b = Bench(M, os.path.join(work, "a"), up=True,
              fail={"link_down": "canctl down kvaser exited 0 but kvnet7 still reads UP"})
    e = _raises(M.LinkError, M.set_link, False, b.net, "linux")
    assert "bench_layer.link_down kvaser failed" in e and "still reads UP" in e, e
    b = Bench(M, os.path.join(work, "b"), up=False,
              fail={"link_up": "canctl up kvaser 500000 failed (rc 3, did not take): x"})
    e = _raises(M.LinkError, M.set_link, True, b.net, "linux")
    assert "bench_layer.link_up kvaser failed" in e and "rc 3, did not take" in e, e
    b = Bench(M, os.path.join(work, "c"), up=False, fail={"link_up": "the bench lock is free"})
    e = _raises(M.LinkError, M.set_link, True, b.net, "linux")
    assert "the bench lock is free" in e and M.link_state(b.net) == "down", e
    b = Bench(M, os.path.join(work, "d"), up=True)
    _raises(M.LinkError, M.set_link, False, b.net, "win32")
    assert b.calls() == [], b.calls()


class FakeCan:
    def __init__(self):
        self.opened = []

    def Bus(self, **kw):
        self.opened.append(kw)
        return "a bus"


def case_open_bus(M, work):
    b = Bench(M, work, up=True)
    fc = FakeCan()
    assert M.open_bus(root=b.net, can_mod=fc, platform="linux") == "a bus"
    assert fc.opened == [{"interface": "socketcan", "channel": NETDEV,
                          "receive_own_messages": True}], fc.opened
    assert b.roles.asked >= 1, "the netdev not taken from the role table"
    fc = FakeCan()
    M.ROLES = FakeRoles(err="kvaser is not attached")
    e = _raises(M.LinkError, M.open_bus, root=b.net, can_mod=fc, platform="linux")
    assert "role kvaser" in e and fc.opened == [], (e, fc.opened)
    M.ROLES = b.roles
    for ch in (None, "can0", "canmain", 0, NETDEV):
        fc = FakeCan()
        _raises(M.LinkError, M.open_bus, channel=ch, root=b.net, can_mod=fc, platform="linux")
        assert fc.opened == [], (ch, fc.opened)
    fc = FakeCan()
    _raises(M.LinkError, M.open_bus, root=b.net, can_mod=fc, platform="win32")
    assert fc.opened == []
    b.set_flags(0x40080)
    fc = FakeCan()
    e = _raises(M.LinkError, M.open_bus, root=b.net, can_mod=fc, platform="linux")
    assert "reads down" in e and fc.opened == [], (e, fc.opened)
    os.unlink(b.flags)
    _raises(M.LinkError, M.open_bus, root=b.net, can_mod=fc, platform="linux")
    assert fc.opened == []


class Msg:
    def __init__(self, aid, data, is_rx=True, ext=False, err=False):
        self.arbitration_id, self.data, self.is_rx = aid, bytes(data), is_rx
        self.is_extended_id, self.is_error_frame = ext, err


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeBus:
    """Echoes each sent frame `delay` s after the send on the fake clock,
    unless its first data byte is in `lose`; `rx` frames are offered too.
    recv(timeout) moves the clock on by timeout when nothing is due."""

    def __init__(self, clock, delay=0.0009, lose=(), send_exc=None):
        self.clock, self.delay, self.lose, self.send_exc = clock, delay, set(lose), send_exc
        self.queue = []

    def send(self, m, timeout=None):
        if self.send_exc is not None:
            raise self.send_exc
        if m.data[0] not in self.lose:
            self.queue.append((self.clock() + self.delay, Msg(m.arbitration_id, m.data,
                                                             is_rx=False,
                                                             ext=m.is_extended_id)))

    def offer(self, m, at):
        self.queue.append((at, m))

    def recv(self, timeout=None):
        self.queue.sort(key=lambda x: x[0])
        if self.queue and self.queue[0][0] <= self.clock():
            return self.queue.pop(0)[1]
        self.clock.t += timeout or 0.0
        if self.queue and self.queue[0][0] <= self.clock():
            return self.queue.pop(0)[1]
        return None


def case_echo_watch(M, work):
    c = Clock()
    fb = FakeBus(c)
    got = []
    w = M.EchoWatch(fb, on_rx=lambda m, t: got.append(m.arbitration_id), clock=c)
    fb.offer(Msg(0x7F4, b"\x01"), c())
    for i in range(20):
        w.send(Msg(0x7A9, bytes([i, 0])))
        c.t += 0.0005
        w.poll(0.0)
    w.finish()
    assert (w.calls, w.echoed, len(w.pending)) == (20, 20, 0), (w.calls, w.echoed, w.pending)
    assert got == [0x7F4], got
    # one echo lost: nothing at 99 ms, Stall past 100 ms
    c = Clock()
    fb = FakeBus(c, lose={5})
    w = M.EchoWatch(fb, clock=c)
    for i in range(8):
        w.send(Msg(0x7A9, bytes([i])))
    t_ret = c()
    c.t = t_ret + 0.099
    w.poll(0.0)
    assert w.echoed == 7 and len(w.pending) == 1, (w.echoed, w.pending)
    c.t = t_ret + 0.1005
    try:
        w.poll(0.0)
        raise AssertionError("a lost echo past 100 ms was not a stall")
    except M.Stall as e:
        assert "kvaser TX stall" in str(e) and "no own echo" in str(e), str(e)
        assert abs(e.t - (t_ret + 0.1005)) < 1e-9, e.t
    # finish(): a late echo (50 ms) is waited for; a lost one is a stall
    c = Clock()
    w = M.EchoWatch(FakeBus(c, delay=0.050), clock=c)
    w.send(Msg(0x7A9, b"\x00"))
    w.finish()
    assert w.echoed == 1
    c = Clock()
    w = M.EchoWatch(FakeBus(c, lose={0}), clock=c)
    w.send(Msg(0x7A9, b"\x00"))
    _raises(M.Stall, w.finish)
    assert c() - 100.0 > 0.1, c()
    # ENOBUFS on send is a stall; another send error is itself
    c = Clock()
    w = M.EchoWatch(FakeBus(c, send_exc=OSError(errno.ENOBUFS, "No buffer space available")),
                    clock=c)
    e = _raises(M.Stall, w.send, Msg(0x7A9, b"\x00"))
    assert "ENOBUFS" in e, e
    w = M.EchoWatch(FakeBus(c, send_exc=ValueError("bad frame")), clock=c)
    _raises(ValueError, w.send, Msg(0x7A9, b"\x00"))


def case_suspend(M, work):
    """on_echo gets (key, send returned, echo read); suspended, a missing
    echo is no stall; rebase(t) gives every pending send its bound from t
    (bench_vport's no-ACK windows; the reviewer, 2026-11-13)."""
    c = Clock()
    got = []
    fb = FakeBus(c, delay=0.002, lose={7})
    w = M.EchoWatch(fb, clock=c, on_echo=lambda k, tr, te: got.append((k[2][0], tr, te)))
    w.send(Msg(0x100, b"\x01"))
    t_ret = c()
    c.t += 0.003
    w.poll(0.0)
    assert got == [(1, t_ret, c())], got
    w.suspended = True
    w.send(Msg(0x100, b"\x07"))
    c.t += 5.0
    w.poll(0.0)                          # 5 s unechoed, suspended: no stall
    t_ack = c()
    w.suspended = False
    w.rebase(t_ack)
    c.t = t_ack + 0.099
    w.poll(0.0)
    c.t = t_ack + 0.1005
    e = _raises(M.Stall, w.poll, 0.0)
    assert "no own echo 100 ms" in e or "no own echo 101 ms" in e, e


class ThreadBus:
    """Echoes every send at once, for two threads: one sends, one reads."""

    def __init__(self):
        import queue
        self.q = queue.Queue()

    def send(self, m, timeout=None):
        self.q.put(Msg(m.arbitration_id, m.data, is_rx=False))

    def recv(self, timeout=None):
        import queue
        try:
            return self.q.get(timeout=timeout or 0.0001)
        except queue.Empty:
            return None


class SyncBus(ThreadBus):
    """send() returns only once the reader has taken its echo: the order a
    fast echo can take on the bench, made certain."""

    def __init__(self):
        import threading
        ThreadBus.__init__(self)
        self.taken = threading.Event()

    def send(self, m, timeout=None):
        self.taken.clear()
        ThreadBus.send(self, m, timeout)
        self.taken.wait(2.0)

    def recv(self, timeout=None):
        m = ThreadBus.recv(self, timeout)
        if m is not None:
            self.taken.set()
        return m


def case_threads(M, work):
    """One thread sends while another polls (bench_topology's drain): no
    error from a deque changed while it is searched, every echo matched;
    and an echo read before send() has returned is still matched."""
    import threading
    for bus, n in ((SyncBus(), 200), (ThreadBus(), 20000)):
        _threads(M, bus, n)


def _threads(M, bus, n):
    import threading
    w = M.EchoWatch(bus, stall_s=5.0)
    errs, stop = [], threading.Event()

    def reader():
        try:
            while not stop.is_set():
                w.poll(0.0005)
        except Exception as e:                          # noqa: BLE001
            errs.append(repr(e))
    t = threading.Thread(target=reader)
    t.start()
    for i in range(n):
        w.send(Msg(0x7A9, i.to_bytes(3, "big")))
    stop.set()
    t.join(10)
    try:
        w.finish()
    except M.Stall as e:
        errs.append(repr(e))
    assert not errs and w.echoed == n and w.stray == 0, (type(bus).__name__, errs[:2],
                                                         w.echoed, w.stray)


def case_watched_bus(M, work):
    c = Clock()
    fb = FakeBus(c, lose={3})
    w = M.WatchedBus(fb, clock=c)
    assert w.inner is fb, "the bus underneath not named as .inner (adapter_roles.bus_error)"
    fb.offer(Msg(0x410, b"\x09"), c())
    for i in range(3):
        w.send(Msg(0x18FF50E5, bytes([i]), ext=True))
    c.t += 0.002
    got = [w.recv(0.0) for _ in range(5)]
    assert [m.arbitration_id for m in got if m is not None] == [0x410], got
    assert w.watch.echoed == 3, w.watch.echoed
    w.send(Msg(0x18FF50E5, b"\x03", ext=True))
    c.t += 0.05
    assert w.recv(0.0) is None
    c.t += 0.06
    _raises(M.Stall, w.recv, 0.0)
    # a frame already buffered still checks the bound
    c = Clock()
    fb = FakeBus(c, lose={3})
    w = M.WatchedBus(fb, clock=c)
    w._rx.append(Msg(0x410, b"\x00"))
    w.send(Msg(0x7A9, b"\x03"))
    c.t += 0.2
    _raises(M.Stall, w.recv, 0.0)
    v = M.link_void("planted", 1794590000.0, 1794590001.0, lambda s, u: PLANTED)
    assert v.startswith("VOID: kvaser link: planted; kernel USB/CAN lines 7"), v


PLANTED = """\
2026-11-13T10:20:12.000001-08:00 madhouse-debian kernel: Bluetooth: hci0: unrelated
2026-11-13T10:20:13.123456-08:00 madhouse-debian kernel: gs_usb 1-3.2.3:1.0 canmain: failed to xmit URB 0: -EPROTO
2026-11-13T10:20:14.000002-08:00 madhouse-debian kernel: usb 1-3.2.1: device descriptor read/64, error -71
2026-11-13T10:20:15.000003-08:00 madhouse-debian kernel: kvaser_usb 1-3.2.1:1.0 kvaser: Cannot send TX
2026-11-13T10:20:16.000004-08:00 madhouse-debian kernel: pcan32: PCAN-USB FD CAN1: msg lost: err -28
2026-11-13T10:20:17.000005-08:00 madhouse-debian kernel: usb 1-3.4: USB disconnect, device number 9
2026-11-13T10:20:18.000006-08:00 madhouse-debian kernel: e1000e: eno1 NIC Link is Up
2026-11-13T10:20:19.000007-08:00 madhouse-debian kernel: usb 1-3.2.4: new full-speed USB device number 9 using xhci_hcd
2026-11-13T10:20:20.000008-08:00 madhouse-debian kernel: cdc_acm 1-5:1.0: urb 1 failed submission with -71
"""
# (the -71 line is off the bench hub 1-3 since 2026-11-16, when KLOG_RE took
# every line under 1-3, so -71 alone still reads it)


def case_kernel_lines(M, work):
    asked = []

    def journal(since, until):
        asked.append((since, until))
        return PLANTED
    t0, t1 = 1794590000.4, 1794590060.7
    kl = M.kernel_lines(t0, t1, journal)
    assert asked and asked[0][0] <= t0 and asked[0][1] >= t1, asked
    assert len(kl) == 7, kl
    assert any("usb 1-3.2.4: new full-speed" in x for x in kl), ("usb 1-3.2 alone", kl)
    assert any("cdc_acm 1-5:1.0: urb 1 failed submission with -71" in x for x in kl), \
        ("-71 alone", kl)
    assert not any("unrelated" in x or "eno1" in x for x in kl), kl
    assert any(x.startswith("2026-11-13T10:20:14.000002") and "error -71" in x for x in kl), kl
    v = M.void_line("kvaser TX stall: planted", t0, t1, journal)
    assert v.startswith("VOID: kvaser TX stall: planted; kernel USB/CAN lines 7 in the window"), v
    for want in ("usb 1-3.2.1: device descriptor read/64, error -71", "failed to xmit URB 0: -EPROTO",
                 "kvaser_usb 1-3.2.1:1.0", "pcan32", "USB disconnect"):
        assert want in v, (want, v)
    assert "\n" not in v, v
    many = "".join("2026-11-13T10:21:%02d.0-08:00 h kernel: usb 1-3.2.1: error -71\n" % i
                   for i in range(M.KLOG_MAX + 3))
    v = M.void_line("x", t0, t1, lambda s, u: many)
    assert "(3 more)" in v and v.count("error -71") == M.KLOG_MAX, v
    v = M.void_line("x", t0, t1, lambda s, u: "nothing here\n")
    assert "kernel USB/CAN lines none in the window" in v, v

    def broken(s, u):
        raise OSError("no journal")
    v = M.void_line("x", t0, t1, broken)
    assert "could not be read" in v and "none in the window" not in v, v
    # after the hub swap (the reviewer, 2026-11-16): each line below is read
    # by one rule alone, so each rule's loss fails here
    kl = M.kernel_lines(t0, t1, lambda s, u: POST_SWAP)
    for want, rule in (("usb 1-3.1: new full-speed", "a device under 1-3 by path"),
                       ("cdc_acm 1-3.2.4:1.0: ttyACM2", "another driver's line under 1-3"),
                       ("usb 1-3.2-port4: disabled by hub", "run e79d's line"),
                       ("usb 1-5-port2: disabled by hub", "disabled by hub off 1-3"),
                       ("usb 1-4: New USB device found, idVendor=303a", "a bench ID off 1-3"),
                       ("usb 1-5.2: device descriptor read/64, error -32", "error -32 off 1-3")):
        assert any(want in x for x in kl), (rule, want, kl)
    for not_want in ("usb 11-3.2", "idVendor=07d1", "usb 1-7.2"):
        assert not any(not_want in x for x in kl), (not_want, kl)
    assert len(kl) == 6, kl


POST_SWAP = """\
2026-11-16T10:00:01.000001-08:00 madhouse-debian kernel: usb 1-3.1: new full-speed USB device number 126 using xhci_hcd
2026-11-16T10:00:02.000002-08:00 madhouse-debian kernel: cdc_acm 1-3.2.4:1.0: ttyACM2: USB ACM device
2026-11-16T10:00:03.000003-08:00 madhouse-debian kernel: usb 1-3.2-port4: disabled by hub (EMI?), re-enabling...
2026-11-16T10:00:04.000004-08:00 madhouse-debian kernel: usb 1-5-port2: disabled by hub (EMI?), re-enabling...
2026-11-16T10:00:05.000005-08:00 madhouse-debian kernel: usb 1-4: New USB device found, idVendor=303a, idProduct=1001, bcdDevice= 1.01
2026-11-16T10:00:06.000006-08:00 madhouse-debian kernel: usb 1-5.2: device descriptor read/64, error -32
2026-11-16T10:00:07.000007-08:00 madhouse-debian kernel: usb 11-3.2: new high-speed USB device number 4 using xhci_hcd
2026-11-16T10:00:08.000008-08:00 madhouse-debian kernel: usb 1-10: New USB device found, idVendor=07d1, idProduct=3300, bcdDevice= 0.00
2026-11-16T10:00:09.000009-08:00 madhouse-debian kernel: usb 1-7.2: new full-speed USB device number 5 using xhci_hcd
"""


def case_recover(M, work):
    b = Bench(M, os.path.join(work, "a"), up=True)
    stop = os.path.join(work, "a", "STOP")
    env = {M.STOP_ENV: stop}
    ok, line = M.recover(b.net, env, "linux")
    assert ok and b.calls() == ["link_down kvaser", "canctl usbreset kvaser",
                                "link_up kvaser 500000"], (ok, line, b.calls())
    assert not os.path.exists(stop)
    assert M.link_state(b.net) == "up"
    for n, step in enumerate(("usbreset", "link_down", "link_up")):
        d = os.path.join(work, "f%d" % n)
        b = Bench(M, d, up=True, fail={step: "made-up failure of %s" % step})
        stop = os.path.join(d, "STOP")
        ok, line = M.recover(b.net, {M.STOP_ENV: stop}, "linux")
        assert not ok and "FAILED" in line and "STOP written" in line, (step, line)
        assert "made-up failure of %s" % step in line, line
        text = open(stop, encoding="utf-8").read()
        assert text.startswith("kvaser recovery failed after a TX stall: "), text
        assert "made-up failure of %s" % step in text, text
    b = Bench(M, os.path.join(work, "h"), up=True, fail={"link_down": "x"})
    M.recover(b.net, {}, "linux")
    assert b.calls() == ["link_down kvaser"], ("a step after a failed one", b.calls())
    b = Bench(M, os.path.join(work, "g"), up=True, fail={"usbreset": "x"})
    ok, line = M.recover(b.net, {}, "linux")
    assert not ok and "not told to stop" in line, line


def case_stall_lines(M, work):
    b = Bench(M, work, up=True)
    order = []
    orig = b.calls

    def close():
        order.append(("close", len(orig())))
    stop = os.path.join(work, "STOP")
    st = M.Stall("kvaser TX stall: no own echo 101 ms after send returned", M.perf())
    lines = M.stall_lines(st, M.wall() - 5, close, lambda s, u: PLANTED, b.net,
                          {M.STOP_ENV: stop})
    assert lines[0].startswith("VOID: kvaser TX stall: no own echo 101 ms"), lines
    assert "error -71" in lines[0] and " at " in lines[0], lines[0]
    assert order == [("close", 0)], ("the socket closed before any canctl call", order)
    assert lines[-1].startswith("kvaser recovered"), lines
    assert not os.path.exists(stop)


def _named(node, attr, mod):
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr == attr and isinstance(f.value, ast.Name)
            and f.value.id == mod)


# the one file that may still open CANlib's "kvaser" interface: a hand-run
# measurement, CANlib-only and refused on Linux (the reviewer's E,
# 2026-11-13; bench_drivers/README.md)
CANLIB_ONLY = {"bench_rate_feasibility.py"}


def scan(paths):
    """-> (hits ["path:line what"], kvaser_link.open_bus calls seen)."""
    hits, n = [], 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        mine = os.path.basename(p) == "kvaser_link.py"
        canlib_ok = os.path.basename(p) in CANLIB_ONLY
        for node in ast.walk(tree):
            pairs = []
            if isinstance(node, ast.Call):
                if _named(node, "open_bus", "kvaser_link"):
                    n += 1
                pairs = [(kw.arg, kw.value) for kw in node.keywords]
            elif isinstance(node, ast.Dict):
                pairs = [(k.value, v) for k, v in zip(node.keys, node.values)
                         if isinstance(k, ast.Constant)]
            for k, v in pairs:
                if k != "interface" or not isinstance(v, ast.Constant):
                    continue
                if v.value == "kvaser" and not canlib_ok:
                    hits.append("%s:%d interface='kvaser' (CANlib, linuxcan's leaf)"
                                % (p, node.lineno))
                elif v.value == "socketcan" and not mine:
                    hits.append("%s:%d interface='socketcan' outside kvaser_link"
                                % (p, node.lineno))
    return hits, n


def harness_files():
    r = subprocess.run(["git", "-C", HERE, "ls-files", "--", "."], capture_output=True,
                       text=True, check=True)
    out = []
    for rel in r.stdout.splitlines():
        base = os.path.basename(rel)
        if not rel.endswith(".py") or rel.startswith("firmware/"):
            continue
        if base.endswith("_test.py") or base.startswith("test_"):
            continue
        out.append(os.path.join(HERE, rel))
    if os.path.join(HERE, "kvaser_link.py") not in out:
        out.append(os.path.join(HERE, "kvaser_link.py"))   # before its first commit
    return out


def case_scan(M, work):
    files = harness_files()
    assert len(files) > 20, len(files)
    hits, n = scan(files)
    assert not hits, hits
    assert n >= MIN_OPENERS, "only %d kvaser_link.open_bus calls found" % n
    planted = os.path.join(work, "planted.py")
    with open(planted, "w", encoding="utf-8") as f:
        f.write("import can\n"
                "a = can.Bus(interface='kvaser', channel=0)\n"
                "b = adapter_fixes.bus(interface='kvaser', channel=0)\n"
                "c = can.Bus(interface='socketcan', channel='kvaser')\n"
                "kw = {'interface': 'socketcan'}\n"
                "d = kvaser_link.open_bus()\n"
                "e = can.Bus(interface='pcan')\n")
    hits, n = scan([planted])
    lines = sorted(int(h.split(":")[1].split()[0]) for h in hits)
    assert lines == [2, 3, 4, 5] and n == 1, (hits, n)
    # the CANlib-only file may open "kvaser", and nothing else may
    only = os.path.join(work, "bench_rate_feasibility.py")
    with open(only, "w", encoding="utf-8") as f:
        f.write("a = adapter_fixes.bus(interface='kvaser', channel=0)\n"
                "b = can.Bus(interface='socketcan', channel='kvaser')\n")
    hits, n = scan([only])
    assert [int(h.split(":")[1].split()[0]) for h in hits] == [2], hits


CASES = [case_link_state, case_set_up, case_self_test_guard, case_set_down, case_not_taken, case_open_bus,
         case_echo_watch, case_suspend, case_threads, case_watched_bus, case_kernel_lines, case_recover, case_stall_lines,
         case_scan]

MUTANTS = [
    ("a stall taken for a result: the bound never checked",
     "            if age > self.stall_s:", "            if False:"),
    ("the bound off by ten", "STALL_S = 0.100 ", "STALL_S = 1.000 "),
    ("ENOBUFS not a stall", "            if _enobufs(e):", "            if False:"),
    ("every send error a stall", "            if _enobufs(e):", "            if True:"),
    ("finish() does not wait", "        while self.pending:\n            self.poll(0.005)",
     "        return"),
    ("an echo counted for any frame", "        if getattr(m, \"is_rx\", True) or",
     "        if False and getattr(m, \"is_rx\", True) or"),
    ("frames other than echoes not handed on",
     "            if self.on_rx is not None:\n                self.on_rx(m, self.clock())",
     "            pass"),
    ("IFF_UP ignored: any flags read up", "    return \"up\" if flags & IFF_UP else \"down\"",
     "    return \"up\""),
    ("an unreadable state read as down",
     "        raise LinkError(\"the %s link state cannot be read (%s: %s)\" % (dev, p, e))",
     "        return \"down\""),
    # the role (shared/bench SPEC section 8, 2026-11-18)
    ("netdev: the role's own name, not the role table's",
     "        dev = roles.kvaser_netdev()\n", "        dev = ROLE\n"),
    ("netdev: an unresolved role taken as the role's name",
     "        raise LinkError(\"role %s does not resolve to a netdev: %s\" % (ROLE, e))",
     "        dev = ROLE"),
    ("netdev: a path or nothing accepted",
     "    if not dev or os.sep in dev or dev in (\".\", \"..\"):", "    if False:"),
    ("link_state: read on the role's name",
     "    p = os.path.join(root, dev, \"flags\")", "    p = os.path.join(root, ROLE, \"flags\")"),
    ("open_bus: opened on the role's name, not its netdev",
     "    return can_mod.Bus(interface=\"socketcan\", channel=dev,",
     "    return can_mod.Bus(interface=\"socketcan\", channel=ROLE,"),
    ("set_link: the layer's failure ignored",
     "        raise LinkError(\"bench_layer.%s %s failed: %s: %s\" % (step, ROLE, type(e).__name__, e))",
     "        got = None"),
    ("set_link: down for up",
     "        got = L.link_up(ROLE, BITRATE) if up else L.link_down(ROLE)",
     "        got = L.link_down(ROLE)"),
    ("set_link: another bitrate",
     "        got = L.link_up(ROLE, BITRATE) if up else L.link_down(ROLE)",
     "        got = L.link_up(ROLE, 250000) if up else L.link_down(ROLE)"),
    ("set_link: from a self-test", "    why = self_test_refusal()\n    if why:\n        raise LinkError(why)\n",
     ""),
    ("the layer not adapter_roles'", "    import adapter_roles\n    return adapter_roles.layer()",
     "    raise LinkError(\"no layer\")"),
    ("set_link: run off Linux",
     "    if not linux(platform):\n        raise LinkError(\"the Kvaser is opened on Linux only (role %s, canctl)",
     "    if False:\n        raise LinkError(\"the Kvaser is opened on Linux only (role %s, canctl)"),
    ("open_bus: the link not read", "    dev = netdev()\n    require(True, root)\n",
     "    dev = netdev()\n"),
    ("open_bus: any channel", "    if channel != ROLE:", "    if False:"),
    ("open_bus: echoes dropped", "                       receive_own_messages=receive_own_messages)",
     "                       receive_own_messages=False)"),
    ("open_bus: by position", "    return can_mod.Bus(interface=\"socketcan\", channel=dev,",
     "    return can_mod.Bus(interface=\"socketcan\", channel=None,"),
    ("kernel lines left off the VOID line",
     "    return \"VOID: %s; kernel USB/CAN lines %s\" % (cause, tail)",
     "    return \"VOID: %s\" % cause"),
    ("kernel lines: no filter", "        if KLOG_RE.search(ln):", "        if True:"),
    ("kernel lines: -71 not matched", "|-71\\b|EPROTO|", "|EPROTO|"),
    ("kernel lines: the bench hub 1-3 not matched by path",
     "KLOG_RE = re.compile(r\"\\b1-3\\b|\"", "KLOG_RE = re.compile(r\"(?!x)x|\""),
    ("kernel lines: 1-3 matched inside another path (usb 11-3)",
     "KLOG_RE = re.compile(r\"\\b1-3\\b|\"", "KLOG_RE = re.compile(r\"1-3|\""),
    ("kernel lines: the bench's IDs not matched",
     "+ \"|\".join(r\"idVendor=%s, idProduct=%s\" % p for p in BENCH_USB_IDS)", "+ \"(?!x)x\""),
    ("kernel lines: disabled by hub not matched",
     "r\"|disabled by hub|error -32\\b\",", "r\"|error -32\\b\","),
    ("kernel lines: error -32 not matched",
     "r\"|disabled by hub|error -32\\b\",", "r\"|disabled by hub\","),
    ("kernel lines: an unreadable log read as none",
     "        return [\"(the kernel log could not be read: %s)\" % e]", "        return []"),
    ("kernel lines: the window cut short", "    since, until = int(t0) - 1, int(t1) + 2",
     "    since, until = int(t0) + 30, int(t1) + 2"),
    ("recover: usbreset skipped",
     "RECOVER_STEPS = (\"link_down\", \"usbreset\", \"link_up\")",
     "RECOVER_STEPS = (\"link_down\", \"link_up\")"),
    ("recover: usbreset before down (canctl refuses an up netdev)",
     "RECOVER_STEPS = (\"link_down\", \"usbreset\", \"link_up\")",
     "RECOVER_STEPS = (\"usbreset\", \"link_down\", \"link_up\")"),
    ("recover: the link not brought up again",
     "RECOVER_STEPS = (\"link_down\", \"usbreset\", \"link_up\")",
     "RECOVER_STEPS = (\"link_down\", \"usbreset\")"),
    ("recover: up at another bitrate", "        return L.link_up(ROLE, BITRATE)",
     "        return L.link_up(ROLE, 250000)"),
    ("recover: a failed step ignored",
     "            why = \"%s %s failed: %s: %s\" % (step, ROLE, type(e).__name__, e)\n",
     "            continue\n"),
    ("recover: the bench never stopped",
     "            return False, \"kvaser recovery FAILED, %s; %s\" % (why, _stop(why, environ))\n"
     "    return True,",
     "            return False, \"kvaser recovery FAILED, %s\" % why\n    return True,"),
    ("recover: from a self-test",
     "    why = self_test_refusal()\n    if why:\n        return False,",
     "    why = None\n    if why:\n        return False,"),
    # (no mutant for the lock itself: a race without it shows only now and
    # then, 5 strays in 20,000 sends when it was found, so a mutant of it
    # would pass or fail by chance; the ordering below is deterministic)
    ("a send noted only after it returned (its echo may come first)",
     "        with self._lock:\n            self.pending.append(entry)\n        try:\n"
     "            self.bus.send(msg, timeout=timeout)\n",
     "        try:\n            self.bus.send(msg, timeout=timeout)\n"
     "            with self._lock:\n                self.pending.append(entry)\n"),
    ("a self-test never refused", "    if \"--self-test\" in (argv0 if argv0 is not None else sys.argv):",
     "    if False:"),
    ("suspended ignored", "        if self.suspended:\n            return\n", ""),
    ("rebase does nothing", "                if e[0] is not None and e[0] < t:\n                    e[0] = t",
     "                pass"),
    ("on_echo never called", "        if hit is not None and self.on_echo is not None:",
     "        if False:"),
    ("WatchedBus: the bus underneath not named", "        self.inner = bus\n", ""),
    ("WatchedBus: send not watched", "        self.watch.send(msg, timeout=0.05 if timeout is None else timeout)",
     "        self.bus.send(msg)"),
    ("WatchedBus: a buffered frame skips the bound", "        else:\n            self.watch.check()\n",
     "        else:\n            pass\n"),
    ("link_void: not a VOID line", "    return void_line(\"kvaser link: %s\" % err, t0, t1, journal)",
     "    return \"kvaser link: %s\" % err"),
    ("stall_lines: recovery with the socket open",
     "    if close is not None:\n        try:\n            close()",
     "    ok, line = recover(root, environ)\n    if close is not None:\n        try:\n            close()"),
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
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    with scratch_dir.run("kvaser-link-test") as base:
        if "--can-fail" not in argv:
            bad = run_cases(MODULE, os.path.join(base, "t"))
            print("\nkvaser_link_test: %s" % ("all checks OK" if not bad
                                             else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited kvaser_link.py", flush=True)
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
            p = os.path.join(d, "kvaser_link.py")
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
