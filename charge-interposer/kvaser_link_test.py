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
  2b. restart-ms    (the reviewer's ruling Q1, 2026-11-20) set_link and
                    recover pass the restart-ms asked to the layer's link_up
                    and read it back themselves, on the line; a read-back not
                    as asked is LinkError (recover: not ok); stall_lines'
                    recovery asks it; none asked passes none
  2c. counters      link_counters: the layer's read-only link_counters for
                    role kvaser; its failure or a missing counter is
                    LinkError, never a 0; counter_growth subtracts, and a
                    counter gone back (the netdev made again) is LinkError
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
                    a late echo and raises Stall for one that never comes;
                    finish(forgive=n) returns up to n never echoed (the
                    kernel's drops while bus-off) and empties pending, and
                    one more is the Stall, saying how many were unaccounted;
                    finish(excuse=f) returns every excused send never
                    echoed, however many, counts only the others against n
                    and waits for an excused send's late echo (ruling (a))
  6a. canbench      (the reviewer's echo_watch ruling, 2026-11-23) the
                    watch underneath is the pinned canbench.echo_watch's,
                    with the bound STALL_S, on_rx, on_echo and
                    read_after_send passed; WatchedBus's is its
                    EchoWatchedBus, .inner the bus; each of SendRefused
                    (send) and EchoMissing (check, poll, finish, WatchedBus.
                    recv) is a Stall with "kvaser TX stall: " and canbench's
                    text, at its .t, and its stall_lines VOID line matches
                    run_scenario's KVASER_VOID (read from run_scenario.py),
                    so vehicle_sim's stall is still a case's NOT EXERCISED;
                    finish's forgiven/excused Stall in the old wording
                    (finish_text), any other text kept; _key is canbench's
                    key_of
  6b. no-read       read_after_send=False (bench_charger_silent's and
                    bench_topology's reader threads): send() never reads
                    the bus; saw() is True for an own echo (matched, the
                    pending send gone), False for a frame and an error
                    frame; check() is Stall past the bound and nothing at
                    99 ms; finish() waits for a reader thread's saw() calls
                    and is Stall for an echo no reader hands it
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
the VOID line. Since 2026-11-23 the echo bookkeeping is can-tools' (tested
in can-tools), so the edits of the stall rule are edits of how kvaser_link
hands it to canbench and maps what canbench raises; two old edits went with
the code they edited (see MUTANTS).

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
        self.restart_ms = 0             # the netdev's restart-ms (0 after a usbreset)
        self.restart_back = None        # set: the read-back's restart-ms, whatever was asked
        self.bitrate = 500000           # the netdev's bitrate, as the last link_up set it
        self.bitrate_back = None        # set: the read-back's bitrate, whatever was asked
        self.counters = {"netdev": NETDEV, "state": "ERROR-ACTIVE", "bus_off": 2, "restarts": 2,
                         "error_passive": 3, "error_warning": 3, "bus_error": 900,
                         "arbitration_lost": 0, "tx_dropped": 40, "tx_aborted_errors": 0,
                         "tx_errors": 0, "tx_packets": 5000}

    def _do(self, step, line, up):
        with open(self.b.log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if step in self.fail:
            raise LayerError(self.fail[step])
        flags = int(open(self.b.flags).read().strip(), 16)
        self.b.set_flags(flags | 1 if up else flags & ~1)
        rate = self.bitrate if self.bitrate_back is None else self.bitrate_back
        return {"up": up, "bitrate": rate if up else None,
                "restart_ms": self.restart_ms if self.restart_back is None else self.restart_back}

    def link_up(self, role, bitrate, restart_ms=None):
        if restart_ms is not None and "link_up" not in self.fail:
            self.restart_ms = restart_ms
        self.bitrate = bitrate
        return self._do("link_up", "link_up %s %s%s" % (
            role, bitrate, " restart-ms %s" % restart_ms if restart_ms is not None else ""), True)

    def link_down(self, role):
        return self._do("link_down", "link_down %s" % role, False)

    def link_counters(self, role):
        assert role == "kvaser", role
        if "link_counters" in self.fail:
            raise LayerError(self.fail["link_counters"])
        return dict(self.counters)

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


def _exc(exc, fn, *a, **kw):
    """_raises, returning the exception itself."""
    try:
        got = fn(*a, **kw)
    except exc as e:
        return e
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


def case_restart_ms(M, work):
    """bench_vport's node (the reviewer's ruling Q1, 2026-11-20): the link
    comes up with the restart-ms asked, passed to the layer and read back
    here too, on the line; a read-back that is not what was asked is
    LinkError even when the layer took it; recover's link_up asks it again
    (a usbreset leaves 0); none asked passes none, as before."""
    b = Bench(M, os.path.join(work, "a"), up=False)
    line = M.set_link(True, b.net, "linux", restart_ms=100)
    assert b.calls() == ["link_up kvaser 500000 restart-ms 100"], b.calls()
    assert "read back UP at 500000 restart-ms 100" in line, line
    b = Bench(M, os.path.join(work, "b"), up=False)
    b.layer.restart_back = 0
    e = _raises(M.LinkError, M.set_link, True, b.net, "linux", restart_ms=100)
    assert "restart-ms read back 0, asked 100" in e, e
    b = Bench(M, os.path.join(work, "c"), up=True)
    ok, line = M.recover(b.net, {}, "linux", restart_ms=100)
    assert ok and b.calls()[-1] == "link_up kvaser 500000 restart-ms 100", (line, b.calls())
    assert "link_up 500000 restart-ms 100" in line, line
    b = Bench(M, os.path.join(work, "d"), up=True)
    b.layer.restart_back = 7
    ok, line = M.recover(b.net, {}, "linux", restart_ms=100)
    assert not ok and "restart-ms read back 7, asked 100" in line, line
    b = Bench(M, os.path.join(work, "e"), up=True)
    st = M.Stall("kvaser TX stall: planted", M.perf())
    lines = M.stall_lines(st, M.wall() - 5, None, lambda s, u: "", b.net, {}, restart_ms=100)
    assert b.calls()[-1] == "link_up kvaser 500000 restart-ms 100", b.calls()
    b = Bench(M, os.path.join(work, "f"), up=False)
    M.set_link(True, b.net, "linux")
    assert b.calls() == ["link_up kvaser 500000"], b.calls()


def case_bitrate(M, work):
    """bench_lom_errflag's node at a wrong rate (2026-11-28): set_link's
    bitrate is passed to the layer and read back here too, on the line; a
    read-back at another rate is LinkError even when the layer took it;
    none asked is BITRATE and its read-back is the layer's alone, as
    before; recover still comes up at BITRATE."""
    b = Bench(M, os.path.join(work, "a"), up=False)
    line = M.set_link(True, b.net, "linux", restart_ms=100, bitrate=250000)
    assert b.calls() == ["link_up kvaser 250000 restart-ms 100"], b.calls()
    assert "read back UP at 250000 restart-ms 100" in line, line
    b = Bench(M, os.path.join(work, "b"), up=False)
    b.layer.bitrate_back = 500000
    e = _raises(M.LinkError, M.set_link, True, b.net, "linux", bitrate=1000000)
    assert "bitrate read back 500000, asked 1000000" in e, e
    b = Bench(M, os.path.join(work, "c"), up=False)
    b.layer.bitrate_back = 250000
    M.set_link(True, b.net, "linux")
    assert b.calls() == ["link_up kvaser 500000"], b.calls()
    b = Bench(M, os.path.join(work, "d"), up=True)
    ok, line = M.recover(b.net, {}, "linux", restart_ms=100)
    assert ok and b.calls()[-1] == "link_up kvaser 500000 restart-ms 100", (line, b.calls())


def case_counters(M, work):
    """link_counters (the reviewer's ruling Q1, 2026-11-20): the layer's
    read for role kvaser; its failure, or a counter missing, is LinkError,
    never a 0; counter_growth gives each counter's growth, and LinkError
    when one went back (the netdev made again)."""
    b = Bench(M, work, up=True)
    c0 = M.link_counters()
    assert c0["bus_off"] == 2 and c0["tx_dropped"] == 40, c0
    b.layer.counters.update(bus_off=3, restarts=3, tx_dropped=213, tx_aborted_errors=4)
    g = M.counter_growth(c0, M.link_counters())
    assert g == {"bus_off": 1, "restarts": 1, "tx_dropped": 173, "tx_aborted_errors": 4}, g
    del b.layer.counters["tx_dropped"]
    e = _raises(M.LinkError, M.link_counters)
    assert "lacks tx_dropped" in e, e
    b.layer.counters["tx_dropped"] = 0
    e = _raises(M.LinkError, M.counter_growth, c0, M.link_counters())
    assert "went back" in e and "tx_dropped 40 -> 0" in e, e
    # keys= (bench_lom_errflag, 2026-11-28): other counters the layer gives,
    # each growth its own; a key missing from either read is LinkError
    b.layer.counters.update(tx_dropped=213, error_passive=5, bus_error=1000)
    c1 = M.link_counters()
    g = M.counter_growth(c0, c1, keys=("error_passive", "bus_off"))
    assert g == {"error_passive": 2, "bus_off": 1}, g
    del c1["error_passive"]
    e = _raises(M.LinkError, M.counter_growth, c0, c1, ("error_passive",))
    assert "lack error_passive" in e, e
    b.layer.fail["link_counters"] = "ip: no such device"
    e = _raises(M.LinkError, M.link_counters)
    assert "ip: no such device" in e, e
    # the pinned can-tools layer has the read (pins.json, 49eb7777 on)
    import adapter_roles
    assert callable(getattr(adapter_roles._pinned_layer(), "link_counters", None)), \
        "the pinned canbench.bench_layer has no link_counters"


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
                          "receive_own_messages": True,
                          "ignore_rx_error_frames": False}], fc.opened
    assert b.roles.asked >= 1, "the netdev not taken from the role table"
    # error_frames=False: the socket takes no error frames (a sender that
    # reads only its echoes, bench_vport's node; 2026-11-20)
    fc = FakeCan()
    assert M.open_bus(root=b.net, can_mod=fc, platform="linux", error_frames=False) == "a bus"
    assert fc.opened == [{"interface": "socketcan", "channel": NETDEV,
                          "receive_own_messages": True,
                          "ignore_rx_error_frames": True}], fc.opened
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
    # finish(forgive=n) (the reviewer's ruling Q1, 2026-11-20): frames the
    # kernel dropped never echo; up to n left unechoed once the bound passes
    # are returned and leave pending, not a Stall; one more is the Stall,
    # saying how many were unaccounted; frames that do echo are waited for
    c = Clock()
    w = M.EchoWatch(FakeBus(c, delay=0.020, lose={1, 3}), clock=c)
    for i in range(5):
        w.send(Msg(0x7A9, bytes([i])))
    gone = w.finish(forgive=2)
    assert [g[1][2] for g in gone] == [b"\x01", b"\x03"] and w.echoed == 3, (gone, w.echoed)
    assert not w.pending and c() - 100.0 > 0.1, (w.pending, c())
    w.send(Msg(0x7A9, b"\x09"))
    assert w.finish(forgive=2) == [] and w.echoed == 4, w.echoed
    c = Clock()
    w = M.EchoWatch(FakeBus(c, lose={0, 1, 2}), clock=c)
    for i in range(3):
        w.send(Msg(0x7A9, bytes([i])))
    e = _raises(M.Stall, w.finish, forgive=2)
    assert "3 sends unechoed" in e and "2 of them may be forgiven" in e and "1 unaccounted" in e, e
    assert len(w.pending) == 3, w.pending
    # finish(excuse=f) (the reviewer's ruling (a), 2026-11-20): every
    # excused send left unechoed is returned, however many; only the others
    # count against forgive; an excused send that echoes is waited for
    c = Clock()
    w = M.EchoWatch(FakeBus(c, delay=0.020, lose={0, 1, 2, 3}), clock=c)
    for i in range(6):
        w.send(Msg(0x7A9, bytes([i])))
    gone = w.finish(excuse=lambda k: k[2][0] < 5)
    assert [g[1][2] for g in gone] == [bytes([i]) for i in range(4)] and w.echoed == 2, (
        gone, w.echoed)
    assert not w.pending, w.pending
    c = Clock()
    w = M.EchoWatch(FakeBus(c, lose={0, 1, 4, 5}), clock=c)
    for i in range(6):
        w.send(Msg(0x7A9, bytes([i])))
    gone = w.finish(forgive=2, excuse=lambda k: k[2][0] < 4)
    assert len(gone) == 4 and not w.pending, (gone, w.pending)
    c = Clock()
    w = M.EchoWatch(FakeBus(c, lose={0, 1, 4, 5}), clock=c)
    for i in range(6):
        w.send(Msg(0x7A9, bytes([i])))
    e = _raises(M.Stall, w.finish, excuse=lambda k: k[2][0] < 4)
    assert "2 of them excused" in e and "0 of them may be forgiven" in e \
        and "2 unaccounted" in e, e
    assert len(w.pending) == 4, w.pending
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
    for bus, n in ((SyncBus(), 200), (ThreadBus(), 20000)):
        _threads(M, bus, n)
    # read_after_send=False (2026-11-23): the reader hands each frame to
    # saw() and checks the bound, as bench_charger_silent's and
    # bench_topology's reader threads do
    for bus, n in ((SyncBus(), 200), (ThreadBus(), 20000)):
        _threads(M, bus, n, read_after_send=False)


def _threads(M, bus, n, read_after_send=True):
    import threading
    w = M.EchoWatch(bus, stall_s=5.0, read_after_send=read_after_send)
    errs, stop = [], threading.Event()

    def reader():
        try:
            while not stop.is_set():
                if read_after_send:
                    w.poll(0.0005)
                    continue
                m = bus.recv(0.0005)
                if m is not None and not w.saw(m):
                    errs.append("a frame not taken for an own echo: %r" % (m,))
                if m is None:
                    # once the queue is read out, as poll() checks (check()
                    # walks every pending send: after each of 20,000 frames
                    # it would starve this reader)
                    w.check()
        except Exception as e:                          # noqa: BLE001
            errs.append(repr(e))
    t = threading.Thread(target=reader)
    t.start()
    for i in range(n):
        w.send(Msg(0x7A9, i.to_bytes(3, "big")))
    if not read_after_send:
        # the watch reads nothing: finish() waits for the reader's saw()
        # calls, so it runs while the reader still reads
        try:
            w.finish()
        except M.Stall as e:
            errs.append(repr(e))
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
    w._wb._rx.append(Msg(0x410, b"\x00"))
    w.send(Msg(0x7A9, b"\x03"))
    c.t += 0.2
    _raises(M.Stall, w.recv, 0.0)
    v = M.link_void("planted", 1794590000.0, 1794590001.0, lambda s, u: PLANTED)
    assert v.startswith("VOID: kvaser link: planted; kernel USB/CAN lines 7"), v


def kvaser_void_re():
    """run_scenario.py's KVASER_VOID, read from its source (its re.compile
    argument), without importing run_scenario."""
    import re
    path = os.path.join(HERE, "run_scenario.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "KVASER_VOID"
                                                for t in node.targets):
            return re.compile(node.value.args[0].value)
    raise AssertionError("no KVASER_VOID in run_scenario.py")


def case_canbench(M, work):
    """The reviewer's echo_watch ruling (2026-11-23): canbench's watch does
    the bookkeeping, kvaser_link keeps Stall and its text."""
    import can_tools_pin
    can_tools_pin.use()
    from canbench import echo_watch as EW
    c = Clock()
    rx, ech = [], []
    w = M.EchoWatch(FakeBus(c), on_rx=lambda m, t: rx.append(m), clock=c,
                    on_echo=lambda *a: ech.append(a))
    assert type(w.w) is EW.EchoWatch and w.w.bound_s == M.STALL_S == w.stall_s, w.w
    assert w.w.read_after_send is True and w.w.clock is c, "the defaults not passed"
    assert M.EchoWatch(FakeBus(c), read_after_send=False).w.read_after_send is False, \
        "read_after_send=False not passed"
    msg = Msg(0x7A9, b"\x01")
    assert M._key(msg) == EW.key_of(msg), "_key is not canbench's key_of"
    w.send(msg)
    assert w.pending[0][1] == M._key(msg), (w.pending, M._key(msg))
    w.suspended = True
    assert w.w.suspended is True and w.suspended, "suspended not handed to canbench's watch"
    w.suspended = False
    t_back = c() + 1.0
    w.rebase(t_back)
    assert w.w.pending[0][0] == t_back, "rebase not handed to canbench's watch"
    c.t = t_back + 0.002
    w.poll(0.0)
    assert w.echoed == 1 and len(ech) == 1, "on_echo not passed: %r" % ech
    fb = FakeBus(c)
    w = M.EchoWatch(fb, on_rx=lambda m, t: rx.append(m.arbitration_id), clock=c)
    fb.offer(Msg(0x410, b"\x09"), c())
    w.poll(0.0)
    assert rx == [0x410], "on_rx not passed: %r" % rx
    wb = M.WatchedBus(FakeBus(c), clock=c)
    assert type(wb._wb) is EW.EchoWatchedBus and wb._wb.watch.bound_s == M.STALL_S, wb._wb
    assert wb._wb.watch.read_after_send is True and wb.watch.w is wb._wb.watch, wb.watch
    assert M.WatchedBus(FakeBus(c), read_after_send=False)._wb.watch.read_after_send is False
    # each canbench error a Stall, its text kept after "kvaser TX stall: ",
    # its time kept, and the VOID line vehicle_sim logs matched by KVASER_VOID
    rx_void = kvaser_void_re()
    stalls = []
    c = Clock()
    e = _exc(M.Stall, M.EchoWatch(FakeBus(c, send_exc=OSError(
        errno.ENOBUFS, "No buffer space available")), clock=c).send, Msg(0x7A9, b"\x00"))
    stalls.append(("send", e, "kvaser TX stall: send refused with ENOBUFS ("))
    for how in ("check", "poll", "finish", "recv"):
        c = Clock()
        fb = FakeBus(c, lose={0})
        if how == "recv":
            wb = M.WatchedBus(fb, clock=c)
            wb.send(Msg(0x7A9, b"\x00"))
            c.t += 0.2
            e = _exc(M.Stall, wb.recv, 0.0)
        else:
            w = M.EchoWatch(fb, clock=c)
            w.send(Msg(0x7A9, b"\x00"))
            if how == "finish":
                e = _exc(M.Stall, w.finish)
            else:
                c.t += 0.2
                e = _exc(M.Stall, getattr(w, how), *(() if how == "check" else (0.0,)))
        stalls.append((how, e, "kvaser TX stall: no own echo "))
    for how, e, head in stalls:
        assert isinstance(e.__cause__, EW.EchoWatchError), (how, e.__cause__)
        assert str(e) == "kvaser TX stall: %s" % e.__cause__ and str(e).startswith(head), \
            (how, str(e))
        assert e.t == e.__cause__.t and e.t >= 100.0, (how, e.t)
        b = Bench(M, os.path.join(work, how), up=True)
        lines = M.stall_lines(e, M.wall() - 5, None, lambda s, u: "", b.net, {})
        m = rx_void.search("12:00:00 veh " + lines[0])
        assert m and m.group(0).startswith("VOID: kvaser TX stall: "), (how, lines[0])
    assert "(bound 100 ms; 1 sends unechoed, 0 echoed)" in str(stalls[1][1]), str(stalls[1][1])
    # finish's old wording; other texts kept as they are
    c = Clock()
    w = M.EchoWatch(FakeBus(c, lose={0, 1, 2}), clock=c)
    for i in range(3):
        w.send(Msg(0x7A9, bytes([i])))
    e = _exc(M.Stall, w.finish, forgive=1, excuse=lambda k: k[2][0] == 0)
    assert str(e) ==("kvaser TX stall: %s; 1 of them excused, 1 of them may be forgiven as "
                      "dropped by the kernel, 1 unaccounted" % e.__cause__.__cause__), str(e)
    plain = EW.EchoMissing(1.0, 1, 0, "no own echo 101 ms; 1 of them excused, up to 0 "
                                      "forgiven, 1 unaccounted")
    assert M.finish_text(plain) == str(plain), "a text without its cause rewritten"


class CountingBus(FakeBus):
    """A FakeBus that counts its recv() calls."""

    def __init__(self, clock, **kw):
        FakeBus.__init__(self, clock, **kw)
        self.recvs = 0

    def recv(self, timeout=None):
        self.recvs += 1
        return FakeBus.recv(self, timeout)


def case_no_read(M, work):
    """read_after_send=False (the reviewer's echo_watch ruling, 2026-11-23):
    the caller's reader reads; saw() and check() are its calls."""
    c = Clock()
    fb = CountingBus(c, lose={2})
    w = M.EchoWatch(fb, clock=c, read_after_send=False)
    for i in range(3):
        w.send(Msg(0x7A9, bytes([i])))
    assert fb.recvs == 0, "send() read the bus (%d recv calls)" % fb.recvs
    t_ret = c()
    c.t += 0.002
    seen = [fb.recv(0.0) for _ in range(3)]
    assert [w.saw(m) for m in seen if m is not None] == [True, True], seen
    assert w.echoed == 2 and len(w.pending) == 1, (w.echoed, w.pending)
    assert w.saw(Msg(0x410, b"\x00")) is False, "a frame taken for an own echo"
    assert w.saw(Msg(0x20000004, bytes(8), is_rx=False, err=True)) is False, \
        "an error frame taken for an own echo"
    assert w.echoed == 2 and w.stray == 0, (w.echoed, w.stray)
    c.t = t_ret + 0.099
    w.check()
    c.t = t_ret + 0.1005
    e = _raises(M.Stall, w.check)
    assert "no own echo 100 ms" in e or "no own echo 101 ms" in e, e
    # finish waits for a reader's saw() calls, reading nothing itself
    import threading
    c = Clock()
    fb = CountingBus(c, delay=0.0)
    w = M.EchoWatch(fb, clock=lambda: c(), read_after_send=False)
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            m = fb.recv(0.0)
            if m is not None:
                w.saw(m)
            else:
                import time
                time.sleep(0.001)
    w.send(Msg(0x7A9, b"\x05"))
    t = threading.Thread(target=reader)
    t.start()
    try:
        assert w.finish() == [] and w.echoed == 1, (w.echoed, w.pending)
    finally:
        stop.set()
        t.join(5)
    c = Clock()
    fb = CountingBus(c, delay=0.0)
    w = M.EchoWatch(fb, clock=c, read_after_send=False)
    w.w.sleep = lambda s: setattr(c, "t", c.t + s)
    w.send(Msg(0x7A9, b"\x06"))
    n0 = fb.recvs
    _raises(M.Stall, w.finish)
    assert fb.recvs == n0, "finish() read the bus with read_after_send=False"


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


# the files that may still open CANlib's "kvaser" interface: none since
# 2026-11-23, when bench_rate_feasibility.py, a hand-run measurement,
# CANlib-only and refused on Linux (the reviewer's E, 2026-11-13), was
# retired with bench_pacer.py (the reviewer's pace ruling)
CANLIB_ONLY = set()


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
        if not os.path.exists(os.path.join(HERE, rel)):
            continue                 # deleted in the work tree, not yet in the index
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
    # the CANlib-only file (until 2026-11-23) has no exemption left: its
    # name opening "kvaser" is caught like any other
    only = os.path.join(work, "bench_rate_feasibility.py")
    with open(only, "w", encoding="utf-8") as f:
        f.write("a = adapter_fixes.bus(interface='kvaser', channel=0)\n"
                "b = can.Bus(interface='socketcan', channel='kvaser')\n")
    hits, n = scan([only])
    assert [int(h.split(":")[1].split()[0]) for h in hits] == [1, 2], hits


CASES = [case_link_state, case_set_up, case_restart_ms, case_bitrate, case_counters, case_self_test_guard, case_set_down, case_not_taken, case_open_bus,
         case_echo_watch, case_suspend, case_threads, case_watched_bus, case_canbench, case_no_read,
         case_kernel_lines, case_recover, case_stall_lines, case_scan]

# The stall rule's edits, since 2026-11-23 (the reviewer's echo_watch
# ruling: the bookkeeping is canbench.echo_watch's, tested in can-tools):
# each old edit of this module's own EchoWatch is now the edit of how it
# hands that part to canbench or maps what canbench raises, named as
# before where one exists. Two went with the code they edited, having no
# counterpart here: "finish: the forgiven left pending" (canbench clears
# its pending) and "a send noted only after it returned (its echo may come
# first)" (canbench notes it before bus.send).
MUTANTS = [
    ("a stall taken for a result: the bound never checked",
     "            self.w.check()\n", "            pass\n"),
    ("a stall taken for a result: poll's missing echo not a Stall",
     "            self.w.poll(timeout)\n        except ew().EchoMissing as e:\n"
     "            raise as_stall(e) from e\n",
     "            self.w.poll(timeout)\n        except ew().EchoMissing:\n            pass\n"),
    ("the bound off by ten", "STALL_S = 0.100 ", "STALL_S = 1.000 "),
    ("the bound not handed to canbench's watch",
     "            _watch = ew().EchoWatch(bus, stall_s,", "            _watch = ew().EchoWatch(bus, 1.0,"),
    ("WatchedBus: the bound not handed to canbench's bus",
     "        self._wb = ew().EchoWatchedBus(bus, stall_s,",
     "        self._wb = ew().EchoWatchedBus(bus, 1.0,"),
    ("ENOBUFS not a stall",
     "        except ew().SendRefused as e:\n            raise as_stall(e) from e\n",
     "        except ew().SendRefused:\n            raise\n"),
    ("every send error a stall", "        except ew().SendRefused as e:\n            raise as_stall(e)",
     "        except Exception as e:\n            raise Stall(\"kvaser TX stall: %s\" % e, 0.0)"),
    ("a stall's text without 'kvaser TX stall'",
     "    return Stall(\"kvaser TX stall: %s\" % e, e.t)", "    return Stall(str(e), e.t)"),
    ("a stall's time lost", "    return Stall(\"kvaser TX stall: %s\" % e, e.t)",
     "    return Stall(\"kvaser TX stall: %s\" % e, 0.0)"),
    ("finish() does not wait", "            return self.w.finish(forgive, excuse)",
     "            return []"),
    # finish(forgive) (the reviewer's ruling Q1, 2026-11-20)
    ("finish: nothing forgiven", "            return self.w.finish(forgive, excuse)",
     "            return self.w.finish(0, excuse)"),
    ("finish: any number forgiven", "            return self.w.finish(forgive, excuse)",
     "            return self.w.finish(10 ** 9, excuse)"),
    ("finish: canbench's wording on the Stall",
     "            raise Stall(\"kvaser TX stall: %s\" % finish_text(e), e.t) from e",
     "            raise Stall(\"kvaser TX stall: %s\" % e, e.t) from e"),
    ("finish_text: a text that is not its cause's plus the tail rewritten",
     "    if cause is None or m is None or text != str(cause) + m.group(0):",
     "    if m is None:"),
    # finish(excuse) (the reviewer's ruling (a), 2026-11-20)
    ("finish: excused sends judged", "            return self.w.finish(forgive, excuse)",
     "            return self.w.finish(forgive, None)"),
    ("finish: every send excused", "            return self.w.finish(forgive, excuse)",
     "            return self.w.finish(forgive, lambda k: True)"),
    # read_after_send (2026-11-23)
    ("read_after_send not handed to canbench's watch",
     "clock=clock or perf, read_after_send=read_after_send)",
     "clock=clock or perf, read_after_send=True)"),
    ("WatchedBus: read_after_send not handed to canbench's bus",
     "                                       read_after_send=read_after_send)",
     "                                       read_after_send=True)"),
    ("an echo counted for any frame (saw)", "        return self.w.saw(m)\n",
     "        self.w.saw(m)\n        return True\n"),
    ("the key not canbench's", "    return ew().key_of(m)",
     "    return (m.arbitration_id, bytes(m.data))"),
    # restart-ms (the reviewer's ruling Q1, 2026-11-20)
    ("restart-ms not passed to the layer", "    got = L.link_up(ROLE, rate, restart_ms=restart_ms)",
     "    got = L.link_up(ROLE, rate)"),
    ("restart-ms not read back here", "    if restart_ms is not None and (not isinstance(got, dict)",
     "    if False and (not isinstance(got, dict)"),
    ("recover: restart-ms not asked", "            _recover_step(_layer(), step, restart_ms)",
     "            _recover_step(_layer(), step)"),
    ("link_counters: the layer's failure taken as zeros",
     "        raise LinkError(\"bench_layer.link_counters %s failed: %s: %s\"\n"
     "                        % (ROLE, type(e).__name__, e))",
     "        got = dict((k, 0) for k in COUNTER_KEYS)"),
    ("link_counters: a missing counter accepted", "    if lack:\n        raise LinkError(\"bench_layer.link_counters",
     "    if False:\n        raise LinkError(\"bench_layer.link_counters"),
    ("counter_growth: a counter gone back accepted", "    if back:\n        raise LinkError(",
     "    if False:\n        raise LinkError("),
    ("counter_growth: the wrong way round", "    return dict((k, c1[k] - c0[k]) for k in keys)",
     "    return dict((k, c1[k]) for k in keys)"),
    ("stall_lines: restart-ms not asked", "    ok, line = recover(root, environ, restart_ms=restart_ms)",
     "    ok, line = recover(root, environ)"),
    ("frames other than echoes not handed on",
     "            _watch = ew().EchoWatch(bus, stall_s, on_rx=on_rx,",
     "            _watch = ew().EchoWatch(bus, stall_s, on_rx=None,"),
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
     "        got = _link_up(L, restart_ms, bitrate) if up else L.link_down(ROLE)",
     "        got = L.link_down(ROLE)"),
    ("set_link: another bitrate",
     "        got = _link_up(L, restart_ms, bitrate) if up else L.link_down(ROLE)",
     "        got = L.link_up(ROLE, 250000) if up else L.link_down(ROLE)"),
    # bitrate= (bench_lom_errflag's wrong-rate node, 2026-11-28)
    ("set_link: the bitrate asked not passed on",
     "        got = _link_up(L, restart_ms, bitrate) if up else L.link_down(ROLE)",
     "        got = _link_up(L, restart_ms) if up else L.link_down(ROLE)"),
    ("_link_up: the bitrate asked ignored", "    rate = BITRATE if bitrate is None else int(bitrate)",
     "    rate = BITRATE"),
    ("_link_up: the bitrate not read back here",
     "    if bitrate is not None and (not isinstance(got, dict) or got.get(\"bitrate\") != rate):",
     "    if False:"),
    ("counter_growth: a missing key taken", "    if lack:\n        raise LinkError(\"the %s link's counters lack",
     "    if False:\n        raise LinkError(\"the %s link's counters lack"),
    ("counter_growth: keys ignored", "    return dict((k, c1[k] - c0[k]) for k in keys)",
     "    return dict((k, c1[k] - c0[k]) for k in COUNTER_KEYS)"),
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
    ("open_bus: echoes dropped", "                       receive_own_messages=receive_own_messages,\n",
     "                       receive_own_messages=False,\n"),
    ("open_bus: error_frames=False ignored",
     "                       ignore_rx_error_frames=not error_frames)",
     "                       ignore_rx_error_frames=False)"),
    ("open_bus: error frames always off",
     "                       ignore_rx_error_frames=not error_frames)",
     "                       ignore_rx_error_frames=True)"),
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
    ("recover: up at another bitrate", "        return _link_up(L, restart_ms)",
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
    ("a self-test never refused", "    if \"--self-test\" in (argv0 if argv0 is not None else sys.argv):",
     "    if False:"),
    ("suspended ignored", "        self.w.suspended = v\n", "        pass\n"),
    ("rebase does nothing", "        self.w.rebase(t)\n", "        pass\n"),
    ("on_echo never called", " on_echo=on_echo,\n", " on_echo=None,\n"),
    ("WatchedBus: the bus underneath not named", "        self.inner = bus\n", ""),
    ("WatchedBus: send not watched", "        self.watch.send(msg, timeout=0.05 if timeout is None else timeout)",
     "        self.bus.send(msg)"),
    ("WatchedBus: recv's missing echo not a Stall",
     "            return self._wb.recv(timeout)\n        except ew().EchoMissing as e:\n"
     "            raise as_stall(e) from e\n",
     "            return self._wb.recv(timeout)\n        except ew().EchoMissing:\n            raise\n"),
    ("WatchedBus: its watch not canbench's bus's", "        self.watch = EchoWatch(bus, _watch=self._wb.watch)",
     "        self.watch = EchoWatch(bus)"),
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
