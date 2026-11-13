#!/usr/bin/env python3
"""Test of kvaser_link.py: the Kvaser's link state per check, its one
opener, the TX stall rule, the recovery after a stall and the kernel's
lines on a VOID. No hardware, no real canctl and no real kernel log: a fake
canctl (a script this test writes) acts on a made-up /sys/class/net/kvaser,
a fake bus and clock drive the stall rule, and the kernel log is planted.

    python3.13 -B charge-interposer/kvaser_link_test.py [--can-fail]

Each case names what it checks:
  1. link-state     IFF_UP set reads "up", clear reads "down"; a missing
                    netdev or unparseable flags is LinkError (the check
                    VOID), never a state
  2. set-up         an ACK check: `canctl up kvaser 500000` (exactly that),
                    once, then IFF_UP read back
  3. set-down       a no-ACK check: `canctl down kvaser`, IFF_UP read back
                    clear
  4. not-taken      canctl exiting 0 but leaving the link UP under a no-ACK
                    check is caught (LinkError naming the read-back), as is
                    one left DOWN under an ACK check; canctl exiting 3 is
                    LinkError naming canctl, its rc and what it means; off
                    Linux it is LinkError and no canctl runs
  5. open-bus       python-can's socketcan on channel "kvaser", echoes kept,
                    after IFF_UP reads set; refused, opening nothing, when
                    the link is down or unreadable, when the channel is None
                    (a netdev by position) or another name, and off Linux
  6. echo-watch     every send echoed: sent count = sends, no stall; an
                    echo missing: no stall at 99 ms after send returned,
                    Stall past 100 ms with that time; ENOBUFS on send:
                    Stall; another send error raised as itself; frames
                    that are not own echoes go to on_rx; finish() waits for
                    a late echo and raises Stall for one that never comes
  7. kernel-lines   a planted kernel log: its usb 1-3.2*, kvaser_usb, pcan,
                    gs_usb and -71/EPROTO lines (and only those) on the
                    VOID line with their times, the window named; the
                    count of lines past KLOG_MAX said; an unreadable log
                    said, not taken as "none"
  8. recover        canctl down, usbreset, up 500000, in that order, and
                    IFF_UP read back: ok, no STOP; any step's failure, or
                    IFF_UP clear after it: not ok and STOP written, with the
                    reason, to the file VTRUX_KVASER_STOP names; with it
                    unset, not ok and saying the bench was not told
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
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scratch_dir                                      # noqa: E402

MODULE = os.path.join(HERE, "kvaser_link.py")
MIN_OPENERS = 12

FAKE_CANCTL = r'''
import json, os, sys
ctl, args = sys.argv[1], sys.argv[2:]
with open(ctl, encoding="utf-8") as f:
    c = json.load(f)
with open(c["log"], "a", encoding="utf-8") as f:
    f.write("canctl " + " ".join(args) + "\n")
ok = (args in (["down", "kvaser"], ["usbreset", "kvaser"])
      or (len(args) == 3 and args[:2] == ["up", "kvaser"] and args[2] in
          ("125000", "250000", "500000", "1000000")))
if not ok:
    print("canctl: refused")
    sys.exit(2)
rc = c.get("rc", {}).get(args[0], 0)
if c.get("act", {}).get(args[0], True) and rc == 0:
    p = c["flags"]
    flags = int(open(p).read().strip(), 16)
    if args[0] == "up":
        flags |= 1
    elif args[0] in ("down", "usbreset"):
        flags &= ~1
    with open(p, "w") as f:
        f.write("0x%x\n" % flags)
print("kvaser: %s" % args[0])
sys.exit(rc)
'''


def load(path):
    spec = importlib.util.spec_from_file_location("kvaser_link", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Bench:
    """A made-up /sys/class/net with netdev kvaser and a fake canctl on it,
    patched into the module M."""

    def __init__(self, M, work, up=False, **ctl):
        self.M, self.work = M, work
        self.net = os.path.join(work, "sys", "class", "net")
        os.makedirs(os.path.join(self.net, "kvaser"))
        self.flags = os.path.join(self.net, "kvaser", "flags")
        self.set_flags(0x40081 if up else 0x40080)
        self.log = os.path.join(work, "calls.txt")
        open(self.log, "w").close()
        self.ctl = os.path.join(work, "canctl.json")
        self.set(**ctl)
        script = os.path.join(work, "fake_canctl.py")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_CANCTL)
        M.CANCTL = [sys.executable, "-B", script, self.ctl]

    def set_flags(self, v):
        with open(self.flags, "w") as f:
            f.write("0x%x\n" % v)

    def set(self, **ctl):
        ctl.update(log=self.log, flags=self.flags)
        with open(self.ctl, "w", encoding="utf-8") as f:
            json.dump(ctl, f)

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


def case_set_up(M, work):
    b = Bench(M, work, up=False)
    line = M.set_link(True, b.net, "linux")
    assert b.calls() == ["canctl up kvaser 500000"], b.calls()
    assert M.link_state(b.net) == "up" and "read back" in line, line


def case_set_down(M, work):
    b = Bench(M, work, up=True)
    M.set_link(False, b.net, "linux")
    assert b.calls() == ["canctl down kvaser"], b.calls()
    assert M.link_state(b.net) == "down"


def case_not_taken(M, work):
    b = Bench(M, os.path.join(work, "a"), up=True, act={"down": False})
    e = _raises(M.LinkError, M.set_link, False, b.net, "linux")
    assert "reads up" in e and "needs it down" in e, e
    b = Bench(M, os.path.join(work, "b"), up=False, act={"up": False})
    e = _raises(M.LinkError, M.set_link, True, b.net, "linux")
    assert "reads down" in e and "needs it up" in e, e
    b = Bench(M, os.path.join(work, "c"), up=False, rc={"up": 3})
    e = _raises(M.LinkError, M.set_link, True, b.net, "linux")
    assert "canctl up kvaser 500000 failed (rc 3, did not take)" in e, e
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
    assert fc.opened == [{"interface": "socketcan", "channel": "kvaser",
                          "receive_own_messages": True}], fc.opened
    for ch in (None, "can0", "canmain", 0):
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
                                                             is_rx=False)))

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


def case_watched_bus(M, work):
    c = Clock()
    fb = FakeBus(c, lose={3})
    w = M.WatchedBus(fb, clock=c)
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
    assert v.startswith("VOID: kvaser link: planted; kernel USB/CAN lines 5"), v


PLANTED = """\
2026-11-13T10:20:12.000001-08:00 madhouse-debian kernel: Bluetooth: hci0: unrelated
2026-11-13T10:20:13.123456-08:00 madhouse-debian kernel: gs_usb 1-3.2.3:1.0 canmain: failed to xmit URB 0: -EPROTO
2026-11-13T10:20:14.000002-08:00 madhouse-debian kernel: usb 1-3.2.1: device descriptor read/64, error -71
2026-11-13T10:20:15.000003-08:00 madhouse-debian kernel: kvaser_usb 1-3.2.1:1.0 kvaser: Cannot send TX
2026-11-13T10:20:16.000004-08:00 madhouse-debian kernel: pcan32: PCAN-USB FD CAN1: msg lost: err -28
2026-11-13T10:20:17.000005-08:00 madhouse-debian kernel: usb 1-3.4: USB disconnect, device number 9
2026-11-13T10:20:18.000006-08:00 madhouse-debian kernel: e1000e: eno1 NIC Link is Up
"""


def case_kernel_lines(M, work):
    asked = []

    def journal(since, until):
        asked.append((since, until))
        return PLANTED
    t0, t1 = 1794590000.4, 1794590060.7
    kl = M.kernel_lines(t0, t1, journal)
    assert asked and asked[0][0] <= t0 and asked[0][1] >= t1, asked
    assert len(kl) == 5, kl
    assert not any("unrelated" in x or "eno1" in x for x in kl), kl
    assert any(x.startswith("2026-11-13T10:20:14.000002") and "error -71" in x for x in kl), kl
    v = M.void_line("kvaser TX stall: planted", t0, t1, journal)
    assert v.startswith("VOID: kvaser TX stall: planted; kernel USB/CAN lines 5 in the window"), v
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


def case_recover(M, work):
    b = Bench(M, os.path.join(work, "a"), up=True)
    stop = os.path.join(work, "a", "STOP")
    env = {M.STOP_ENV: stop}
    ok, line = M.recover(b.net, env, "linux")
    assert ok and b.calls() == ["canctl down kvaser", "canctl usbreset kvaser",
                                "canctl up kvaser 500000"], (ok, line, b.calls())
    assert not os.path.exists(stop)
    assert M.link_state(b.net) == "up"
    for n, ctl in enumerate(({"rc": {"usbreset": 3}}, {"rc": {"down": 2}},
                             {"rc": {"up": 2}}, {"act": {"up": False}})):
        d = os.path.join(work, "f%d" % n)
        b = Bench(M, d, up=True, **ctl)
        stop = os.path.join(d, "STOP")
        ok, line = M.recover(b.net, {M.STOP_ENV: stop}, "linux")
        assert not ok and "FAILED" in line and "STOP written" in line, (ctl, line)
        text = open(stop, encoding="utf-8").read()
        assert text.startswith("kvaser recovery failed after a TX stall: "), text
    b = Bench(M, os.path.join(work, "g"), up=True, rc={"usbreset": 3})
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


def scan(paths):
    """-> (hits ["path:line what"], kvaser_link.open_bus calls seen)."""
    hits, n = [], 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), p)
        mine = os.path.basename(p) == "kvaser_link.py"
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
                if v.value == "kvaser":
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


CASES = [case_link_state, case_set_up, case_set_down, case_not_taken, case_open_bus,
         case_echo_watch, case_watched_bus, case_kernel_lines, case_recover, case_stall_lines,
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
     "        raise LinkError(\"the %s link state cannot be read (%s: %s)\" % (NETDEV, p, e))",
     "        return \"down\""),
    ("set_link: the read-back dropped", "    require(up, root)\n    return \"%s link %s",
     "    return \"%s link %s"),
    ("set_link: canctl's exit code ignored",
     "    if rc != 0:\n        raise LinkError(\"canctl %s failed", "    if False:\n        raise LinkError(\"canctl %s failed"),
    ("set_link: down for up", "    args = (\"up\", NETDEV, str(BITRATE)) if up else (\"down\", NETDEV)",
     "    args = (\"down\", NETDEV)"),
    ("set_link: run off Linux",
     "    if not linux(platform):\n        raise LinkError(\"the Kvaser is opened on Linux only (netdev %s, canctl)",
     "    if False:\n        raise LinkError(\"the Kvaser is opened on Linux only (netdev %s, canctl)"),
    ("open_bus: the link not read", "    check_channel(channel)\n    require(True, root)",
     "    check_channel(channel)"),
    ("open_bus: any channel", "    if channel != NETDEV:", "    if False:"),
    ("open_bus: echoes dropped", "                       receive_own_messages=receive_own_messages)",
     "                       receive_own_messages=False)"),
    ("open_bus: by position", "    return can_mod.Bus(interface=\"socketcan\", channel=NETDEV,",
     "    return can_mod.Bus(interface=\"socketcan\", channel=None,"),
    ("kernel lines left off the VOID line",
     "    return \"VOID: %s; kernel USB/CAN lines %s\" % (cause, tail)",
     "    return \"VOID: %s\" % cause"),
    ("kernel lines: no filter", "        if KLOG_RE.search(ln):", "        if True:"),
    ("kernel lines: -71 not matched", "|-71\\b|EPROTO|", "|EPROTO|"),
    ("kernel lines: usb 1-3.2 not matched", "r\"usb 1-3\\.2|gs_usb|", "r\"gs_usb|"),
    ("kernel lines: an unreadable log read as none",
     "        return [\"(the kernel log could not be read: %s)\" % e]", "        return []"),
    ("kernel lines: the window cut short", "    since, until = int(t0) - 1, int(t1) + 2",
     "    since, until = int(t0) + 30, int(t1) + 2"),
    ("recover: usbreset skipped",
     "    steps = [(\"down\", NETDEV), (\"usbreset\", NETDEV), (\"up\", NETDEV, str(BITRATE))]",
     "    steps = [(\"down\", NETDEV), (\"up\", NETDEV, str(BITRATE))]"),
    ("recover: usbreset before down (canctl refuses an up netdev)",
     "    steps = [(\"down\", NETDEV), (\"usbreset\", NETDEV), (\"up\", NETDEV, str(BITRATE))]",
     "    steps = [(\"usbreset\", NETDEV), (\"down\", NETDEV), (\"up\", NETDEV, str(BITRATE))]"),
    ("recover: a failed step ignored", "        if rc != 0:\n            why = ",
     "        if False:\n            why = "),
    ("recover: no read-back", "    try:\n        require(True, root)\n    except LinkError as e:\n        return False",
     "    try:\n        pass\n    except LinkError as e:\n        return False"),
    ("recover: the bench never stopped",
     "            return False, \"kvaser recovery FAILED, %s; %s\" % (why, _stop(why, environ))",
     "            return False, \"kvaser recovery FAILED, %s\" % why"),
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
