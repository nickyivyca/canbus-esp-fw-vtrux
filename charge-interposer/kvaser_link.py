#!/usr/bin/env python3
"""The vehicle segment's Kvaser on the kernel's kvaser_usb (SocketCAN, netdev
kvaser): its link state per check, the one way the harness opens it, the TX
stall rule, the recovery after a stall, and the kernel's USB and CAN lines
for a VOID's window.

Charge-interposer tester, 2026-11-13, from the charge-interposer reviewer's
rulings the same day (A, the stall rule, D, D5, D6 and the kernel-log
addendum), after canctl's owner moved the Kvaser off linuxcan's leaf
(canctl's header: "Absent = socketcan (owner, 2026-11-13, after linuxcan's
leaf oopsed ...)"). Measured under a hand take the same day
(runs/kvaser_usb_measure*_20261113T10*, reverse-it interposer-firmware):

  - an UP kvaser link ACKs with no socket open; a DOWN one does not. So the
    link state is set per check, never left to whoever had the bench:
    an ACK check brings it up, a no-ACK check takes it down, and each is
    read back in sysfs (IFF_UP in /sys/class/net/kvaser/flags) before
    anything is sent. A state that cannot be read, or reads wrong after
    canctl, is LinkError: the check is VOID, never PASS or FAIL.
  - listen-only and berr are refused by kvaser_usb; no interposer check
    needs either (the inventory, 2026-11-13).
  - send() returns before the frame is on the wire, and python-can's
    socketcan stamps are software stamps (SO_TIMESTAMPNS), two per 1 ms USB
    frame. A paced 2000 fps run's own echo follows its send call by 0.87 ms
    median, 1.55 ms max (60 s, 120,001 frames).

THE STALL RULE (D5, D6). A sent frame whose own echo has not come back
within STALL_S (100 ms) of send() returning, or a send refused with ENOBUFS,
is a kvaser TX stall (the CAN replay tooling session saw one at 53 s under
two-bus load, recovered only by a USB reset). EchoWatch keeps that
bookkeeping: "sent" means its own echo arrived, and a stall raises Stall
with the time. A check that meets one is VOID "kvaser TX stall", never
PASS or FAIL, and before the next case the Kvaser is recovered (recover()):
the socket is closed, then `canctl down kvaser` (canctl's usbreset refuses
an up netdev), `canctl usbreset kvaser`, `canctl up kvaser 500000`, and IFF_UP
read back. A failed recovery stops the bench: STOP, with the reason, is
written to the file VTRUX_KVASER_STOP names (bench_suite.py sets it to the
run's own STOP file, so every later case is NOT-RUN "STOP: ...").

THE KERNEL'S LINES (the reviewer's addendum, 2026-11-13). Both bench USB
hubs are bus-powered, and the GL850G holding the Kvaser, the PCAN and
canmain (1-3.2) has shown -71/EPROTO. So a VOID from a stall, a lost-frame
report or a failed link read-back carries, on its own VOID line, the
kernel's USB and CAN lines for the check's window with their times
(journalctl -k: usb 1-3.2*, gs_usb, kvaser_usb, peak_usb, pcan, -71,
EPROTO, disconnects), so a power fault reads as one and not as a product
result. void_line() builds that line; kernel_lines() reads the log.

THE OPENER (D). open_bus() is the only way a harness file opens the
Kvaser: python-can's socketcan on channel "kvaser", named, after IFF_UP is
read; a down or unreadable link is refused, and so is any other channel
(check_channel: a netdev picked by position, as canbench's detection would,
or none). CANlib's interface "kvaser" (linuxcan's leaf) is refused on Linux
by kvaser_link_test's scan. Off Linux the Kvaser is refused: the bench host
is madhouse-debian from port step 3.

canctl, the sysfs root, journalctl and the clocks are module globals read at
each call, so kvaser_link_test.py points them at a fake canctl, a made-up
sysfs and a planted kernel log.

    python3.13 -B charge-interposer/kvaser_link.py state|up|down|recover
"""

import collections
import errno
import os
import re
import subprocess
import sys
import threading
import time

NETDEV = "kvaser"
BITRATE = 500000
STALL_S = 0.100                     # D5: no own echo this long after send returns
CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
CANCTL_TIMEOUT_S = 60
SYSFS_NET = "/sys/class/net"
IFF_UP = 0x1
STOP_ENV = "VTRUX_KVASER_STOP"
JOURNALCTL = ["journalctl", "-k", "-o", "short-iso-precise", "--no-pager"]
# the kernel's USB and CAN lines a VOID names (the reviewer's addendum)
KLOG_RE = re.compile(r"usb 1-3\.2|gs_usb|kvaser_usb|peak_usb|pcan|-71\b|EPROTO|disconnect",
                     re.I)
KLOG_MAX = 12                       # lines on a VOID line; the count says how many more
RC_MEANING = {2: "refused", 3: "did not take"}         # canctl's header
monotonic = time.monotonic
wall = time.time
perf = time.perf_counter


class LinkError(RuntimeError):
    """The kvaser link is not in the state the check needs, or cannot be
    read: the check is VOID."""


class Stall(RuntimeError):
    """A kvaser TX stall: ENOBUFS, or no own echo within STALL_S. .t is the
    perf_counter time it was called."""

    def __init__(self, what, t):
        RuntimeError.__init__(self, what)
        self.t = t


def linux(platform=None):
    return (platform or sys.platform).startswith("linux")


# ---- link state -------------------------------------------------------------

def link_state(root=None):
    """-> "up" or "down", from IFF_UP in sysfs. LinkError if the netdev is
    missing or its flags cannot be read."""
    root = root or SYSFS_NET
    p = os.path.join(root, NETDEV, "flags")
    try:
        with open(p, encoding="ascii") as f:
            raw = f.read().strip()
        flags = int(raw, 16)
    except (OSError, ValueError) as e:
        raise LinkError("the %s link state cannot be read (%s: %s)" % (NETDEV, p, e))
    return "up" if flags & IFF_UP else "down"


def require(up, root=None):
    """LinkError unless the link reads up (up=True) or down (up=False)."""
    want = "up" if up else "down"
    got = link_state(root)
    if got != want:
        raise LinkError("the %s link reads %s in sysfs, and this check needs it %s"
                        % (NETDEV, got, want))
    return got


def _canctl(*args):
    """-> (rc or None if it could not run, its last output line)."""
    argv = list(CANCTL) + list(args)
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=CANCTL_TIMEOUT_S)
        out, rc = p.stdout.decode("utf-8", "replace"), p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "timed out after %d s" % CANCTL_TIMEOUT_S, None
    except OSError as e:
        out, rc = "could not run: %s" % e, None
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return rc, (lines[-1] if lines else "no output")


def _rc_text(rc):
    return "rc %s%s" % (rc, (", %s" % RC_MEANING[rc]) if rc in RC_MEANING else "")


def set_link(up, root=None, platform=None):
    """Bring the link up at BITRATE (an ACK check) or take it down (a no-ACK
    check) through canctl, then read it back in sysfs. -> a line saying
    so; LinkError if canctl fails or the read-back is not what was asked."""
    if not linux(platform):
        raise LinkError("the Kvaser is opened on Linux only (netdev %s, canctl): the bench "
                        "host is madhouse-debian from port step 3" % NETDEV)
    args = ("up", NETDEV, str(BITRATE)) if up else ("down", NETDEV)
    rc, last = _canctl(*args)
    if rc != 0:
        raise LinkError("canctl %s failed (%s): %s" % (" ".join(args), _rc_text(rc), last))
    require(up, root)
    return "%s link %s (canctl %s, IFF_UP read back)" % (NETDEV, "up" if up else "down",
                                                          " ".join(args))


def check_channel(channel):
    """The Kvaser is netdev "kvaser", named; anything else (None: the first
    netdev found, by position; another name) is refused."""
    if channel != NETDEV:
        raise LinkError("the Kvaser is opened as netdev %r by name, not %r (a netdev picked "
                        "by position, or another adapter's, is refused)" % (NETDEV, channel))
    return channel


def open_bus(channel=NETDEV, receive_own_messages=True, root=None, can_mod=None,
             platform=None):
    """python-can's socketcan Bus on netdev kvaser, after the link reads up.
    Echoes are kept by default (EchoWatch counts them as "sent")."""
    if not linux(platform):
        raise LinkError("the Kvaser is opened on Linux only (netdev %s): the bench host is "
                        "madhouse-debian from port step 3" % NETDEV)
    check_channel(channel)
    require(True, root)
    if can_mod is None:
        import can as can_mod
    return can_mod.Bus(interface="socketcan", channel=NETDEV,
                       receive_own_messages=receive_own_messages)


# ---- the stall rule -----------------------------------------------------------

def _key(m):
    return (m.arbitration_id, bool(m.is_extended_id), bytes(m.data))


def _enobufs(e):
    for x in (e, getattr(e, "__cause__", None), getattr(e, "__context__", None)):
        if x is None:
            continue
        if getattr(x, "errno", None) == errno.ENOBUFS or getattr(x, "error_code", None) \
                == errno.ENOBUFS:
            return True
        if "No buffer space" in str(x) or "ENOBUFS" in str(x):
            return True
    return False


class EchoWatch:
    """The stall rule's bookkeeping on one Kvaser bus opened with its echoes
    kept. send() sends and notes when send() returned; poll() reads what is
    waiting, matches own echoes (is_rx False) to the sends in order, hands
    every other frame to on_rx(msg, t_host), and raises Stall once the oldest
    unechoed send is older than stall_s. finish() waits out the last echoes.
    .echoed is the sent count (D3: "sent" means its own echo arrived)."""

    def __init__(self, bus, stall_s=STALL_S, on_rx=None, clock=None):
        self.bus, self.stall_s, self.on_rx = bus, stall_s, on_rx
        self.clock = clock or perf
        self.pending = collections.deque()
        self.calls = self.echoed = self.stray = 0
        self.echo_times = []
        # one thread may send while another reads (bench_topology's drain)
        self._lock = threading.Lock()

    def send(self, msg, timeout=0.05):
        # noted BEFORE the send, its time set once send() returns: a reader
        # on another thread may take the echo before send() has returned
        entry = [None, _key(msg)]
        with self._lock:
            self.pending.append(entry)
        try:
            self.bus.send(msg, timeout=timeout)
        except Exception as e:                          # noqa: BLE001
            with self._lock:
                for i, x in enumerate(self.pending):
                    if x is entry:
                        del self.pending[i]
                        break
            if _enobufs(e):
                t = self.clock()
                raise Stall("kvaser TX stall: send refused with ENOBUFS (%s)" % e, t)
            raise
        entry[0] = self.clock()
        with self._lock:
            self.calls += 1

    def _take(self, m):
        if getattr(m, "is_rx", True) or getattr(m, "is_error_frame", False):
            if self.on_rx is not None:
                self.on_rx(m, self.clock())
            return
        k = _key(m)
        with self._lock:
            for i, (_, pk) in enumerate(self.pending):
                if pk == k:
                    del self.pending[i]
                    self.echoed += 1
                    self.echo_times.append(self.clock())
                    return
            self.stray += 1

    def check(self):
        with self._lock:
            ts = [e[0] for e in self.pending if e[0] is not None]
            oldest = min(ts) if ts else None
            n_pending, n_echoed = len(self.pending), self.echoed
        if oldest is not None:
            t = self.clock()
            age = t - oldest
            if age > self.stall_s:
                raise Stall("kvaser TX stall: no own echo %.0f ms after send returned "
                            "(bound %.0f ms; %d sends unechoed, %d echoed)"
                            % (age * 1000.0, self.stall_s * 1000.0, n_pending,
                               n_echoed), t)

    def poll(self, timeout=0.0):
        m = self.bus.recv(timeout)
        while m is not None:
            self._take(m)
            m = self.bus.recv(0.0)
        self.check()

    def finish(self):
        """Read until every send is echoed; Stall once the bound passes."""
        while self.pending:
            self.poll(0.005)


class WatchedBus:
    """A Kvaser bus (opened with its echoes kept) with the stall rule, for a
    sender that treats it as a plain python-can bus (vehicle_sim): send()
    goes through EchoWatch, recv() returns only frames that are not own
    echoes, and either raises Stall once the bound passes. recv(timeout)
    may return None before timeout when only echoes arrived."""

    def __init__(self, bus, stall_s=STALL_S, clock=None):
        self.bus = bus
        self._rx = collections.deque()
        self.watch = EchoWatch(bus, stall_s, on_rx=lambda m, t: self._rx.append(m),
                               clock=clock)

    def send(self, msg, timeout=None):
        self.watch.send(msg, timeout=0.05 if timeout is None else timeout)

    def recv(self, timeout=None):
        if not self._rx:
            self.watch.poll(timeout or 0.0)
        else:
            self.watch.check()
        return self._rx.popleft() if self._rx else None

    def shutdown(self):
        self.bus.shutdown()

    def __getattr__(self, name):
        return getattr(self.bus, name)


# ---- the kernel's lines ------------------------------------------------------

def epoch_of(t_perf):
    """A perf_counter time as epoch seconds (for the kernel log)."""
    return wall() - perf() + t_perf


def clock_text(t_epoch):
    """Epoch seconds as local HH:MM:SS.mmm."""
    ms = int(round((t_epoch % 1) * 1000)) % 1000
    return "%s.%03d" % (time.strftime("%H:%M:%S", time.localtime(int(t_epoch))), ms)


def kernel_lines(t0, t1, journal=None):
    """The kernel's USB and CAN lines (KLOG_RE) between epoch t0 and t1, with
    their times. journal: a stand-in, (since, until) -> the log's text
    (tests). An unreadable log is one line saying so."""
    since, until = int(t0) - 1, int(t1) + 2
    try:
        if journal is not None:
            text = journal(since, until)
        else:
            p = subprocess.run(JOURNALCTL + ["--since", "@%d" % since, "--until", "@%d" % until],
                               capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                return ["(the kernel log could not be read: journalctl rc %d: %s)"
                        % (p.returncode, (p.stderr or p.stdout).strip()[-200:])]
            text = p.stdout
    except Exception as e:                              # noqa: BLE001
        return ["(the kernel log could not be read: %s)" % e]
    out = []
    for ln in text.splitlines():
        if KLOG_RE.search(ln):
            out.append(ln.replace(" madhouse-debian kernel:", "").strip())
    return out


def void_line(cause, t0, t1, journal=None):
    """"VOID: <cause>; kernel USB/CAN lines <window>: ..." on one line, so
    the bench suite's detail carries them. t0, t1 epoch seconds."""
    kl = kernel_lines(t0, t1, journal)
    win = "%s..%s" % (time.strftime("%H:%M:%S", time.localtime(t0)),
                      time.strftime("%H:%M:%S", time.localtime(t1)))
    if not kl:
        tail = "none in the window %s" % win
    else:
        more = len(kl) - KLOG_MAX
        tail = "%d in the window %s: %s%s" % (len(kl), win, " | ".join(kl[:KLOG_MAX]),
                                             (" | (%d more)" % more) if more > 0 else "")
    return "VOID: %s; kernel USB/CAN lines %s" % (cause, tail)


def link_void(err, t0, t1, journal=None):
    """The VOID line for a link state not set or not read back."""
    return void_line("kvaser link: %s" % err, t0, t1, journal)


# ---- recovery -----------------------------------------------------------------

def _stop(why, environ=None):
    env = os.environ if environ is None else environ
    p = env.get(STOP_ENV, "")
    if not p:
        return "no %s set: the bench is not told to stop" % STOP_ENV
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write("kvaser recovery failed after a TX stall: %s\n" % why)
    except OSError as e:
        return "the STOP file %s could not be written: %s" % (p, e)
    return "STOP written to %s" % p


def recover(root=None, environ=None, platform=None):
    """After a stall, with the check's socket closed: canctl down, usbreset,
    up at BITRATE, IFF_UP read back. -> (ok, line). Not ok: the bench is
    stopped (the STOP file VTRUX_KVASER_STOP names), and the line says so."""
    if not linux(platform):
        return False, "kvaser recovery: Linux only"
    steps = [("down", NETDEV), ("usbreset", NETDEV), ("up", NETDEV, str(BITRATE))]
    for args in steps:
        rc, last = _canctl(*args)
        if rc != 0:
            why = "canctl %s failed (%s): %s" % (" ".join(args), _rc_text(rc), last)
            return False, "kvaser recovery FAILED, %s; %s" % (why, _stop(why, environ))
    try:
        require(True, root)
    except LinkError as e:
        return False, "kvaser recovery FAILED, %s; %s" % (e, _stop(str(e), environ))
    return True, "kvaser recovered: canctl down, usbreset, up %d, IFF_UP read back" % BITRATE


def stall_lines(stall, t0_epoch, close=None, journal=None, root=None, environ=None):
    """A stall's record: the VOID line (with the kernel's lines from the
    check's start to now), then the recovery's line. close(): shuts the
    check's Kvaser socket first (recovery needs it closed)."""
    lines = [void_line("%s at %s" % (stall, clock_text(epoch_of(stall.t))),
                       t0_epoch, wall(), journal)]
    if close is not None:
        try:
            close()
        except Exception as e:                          # noqa: BLE001
            lines.append("(closing the Kvaser socket raised %s: %s)" % (type(e).__name__, e))
    ok, line = recover(root, environ)
    lines.append(line)
    return lines


def main(argv):
    if argv == ["state"]:
        try:
            print(link_state())
            return 0
        except LinkError as e:
            print("UNREADABLE: %s" % e)
            return 1
    if argv in (["up"], ["down"]):
        try:
            print(set_link(argv[0] == "up"))
            return 0
        except LinkError as e:
            print("FAILED: %s" % e)
            return 1
    if argv == ["recover"]:
        ok, line = recover()
        print(line)
        return 0 if ok else 1
    print("usage: kvaser_link.py state|up|down|recover")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
