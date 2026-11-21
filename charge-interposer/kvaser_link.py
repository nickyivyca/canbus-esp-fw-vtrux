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
    read back before anything is sent. A state that cannot be set or read,
    or reads wrong after it is set, is LinkError: the check is VOID, never
    PASS or FAIL. Since 2026-11-19 (the charge-interposer reviewer's
    bench-layer item) the link is set and read back by can-tools' bench-
    role layer: bench_layer.link_up (canctl up, read back with `ip -details
    -json`: UP, 500000, not listen-only) and link_down (canctl down, read
    back not UP), behind shared/bench's bench guard (adapter_roles.layer).
    Before, set_link ran canctl itself and read IFF_UP in sysfs.
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
the socket is closed, then, through the bench-role layer, link_down
(canctl's usbreset refuses an up netdev), `canctl usbreset kvaser`, and
link_up at 500000, each read back. A failed recovery stops the bench: STOP,
with the reason, is
written to the file VTRUX_KVASER_STOP names (bench_suite.py sets it to the
run's own STOP file, so every later case is NOT-RUN "STOP: ...").

RESTART-MS AND THE KERNEL'S DROPS (the charge-interposer reviewer's ruling
Q1, 2026-11-20). set_link(True, restart_ms=N), recover(restart_ms=N) and
stall_lines(restart_ms=N) bring the link up with that bus-off restart, passed
to the layer's link_up and read back here too (a usbreset leaves 0, and with
0 a bus-off is for good); None leaves the link's own, as every caller but
bench_vport's node does. EchoWatch.finish(forgive=n) forgives up to n sends
that never echo once the bound passes (frames the kernel dropped while the
link was bus-off: bench_vport counts them from tx_dropped), and returns them;
more than n is the Stall. EchoWatch.finish(excuse=f) also returns every send
still unechoed whose key f excuses, however many, and counts only the others
against n (bench_vport's node excuses its sends from a wrong-rate span: the
charge-interposer reviewer's ruling (a), 2026-11-20).

THE KERNEL'S LINES (the reviewer's addendum, 2026-11-13). Both bench USB
hubs are bus-powered, and the GL850G holding the Kvaser, the PCAN and
canmain (1-3.2) has shown -71/EPROTO. So a VOID from a stall, a lost-frame
report or a failed link read-back carries, on its own VOID line, the
kernel's USB and CAN lines for the check's window with their times
(journalctl -k: KLOG_RE), so a power fault reads as one and not as a
product result. void_line() builds that line; kernel_lines() reads the log.
The owner swapped in a powered hub on 2026-11-16 and the bench's paths moved
(the WiCAN at 1-3.1, the X2 at 1-3.4, canchg at 1-3.3, the PCAN, canmain and
the Kvaser at 1-3.2.x), so at the reviewer's ruling the same day KLOG_RE
reads every device under the bench hub 1-3 by any driver, and the bench's
devices by ID (BENCH_USB_IDS), besides gs_usb, kvaser_usb, peak_usb, pcan,
-71, EPROTO, disconnects, "disabled by hub" and "error -32" (run e79d's
WiCAN failure: "usb 1-3.2-port4: disabled by hub (EMI?)", error -32).

THE OPENER (D). open_bus() is the only way a harness file opens the
Kvaser: python-can's socketcan on the netdev of bench role "kvaser", after
IFF_UP is read; a down or unreadable link is refused, and so is any channel
but the role's name (check_channel: a netdev picked by position, as
canbench's detection would, or none). CANlib's interface "kvaser"
(linuxcan's leaf) is refused on Linux by kvaser_link_test's scan. Off Linux
the Kvaser is refused: the bench host is madhouse-debian from port step 3.

THE ROLE (shared/bench SPEC section 8, 2026-11-18). The harness asks for the
Kvaser by its canctl role, ROLE: the layer's link_up, link_down and canctl
usbreset take it, and its netdev, which open_bus reads IFF_UP from and opens
the socket on, is the one canctl's role table names (netdev():
adapter_roles.kvaser_netdev, in socketcan mode on kvaser_usb), never a name
of the harness's own. A role that does not resolve is LinkError, as an
unreadable link is.

NEVER FROM A SELF-TEST. A driver's --self-test (and its --can-fail copies,
run the same way) touches no hardware: set_link and recover refuse in a
process whose command line has --self-test, before the layer is asked
(self_test_refusal).

The layer (LAYER), the role table (ROLES), the sysfs root, journalctl and
the clocks are module globals read at each call, so kvaser_link_test.py
points them at a stand-in layer, a stand-in role table, a made-up sysfs and
a planted kernel log.

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

ROLE = "kvaser"                     # canctl's role (shared/bench SPEC section 8)
ROLES = None                        # the role table: adapter_roles, or a stand-in (tests)
LAYER = None                        # the bench-role layer: adapter_roles.layer(), or a stand-in
BITRATE = 500000
STALL_S = 0.100                     # D5: no own echo this long after send returns
# recover()'s steps, in order: canctl's usbreset refuses an up netdev
RECOVER_STEPS = ("link_down", "usbreset", "link_up")
SYSFS_NET = "/sys/class/net"
IFF_UP = 0x1
STOP_ENV = "VTRUX_KVASER_STOP"
JOURNALCTL = ["journalctl", "-k", "-o", "short-iso-precise", "--no-pager"]
# The bench's USB devices by vendor:product, as the kernel's enumeration line
# names them ("New USB device found, idVendor=303a, idProduct=1001"), so a
# device that comes back on another port is still read (lsusb, 2026-11-16).
BENCH_USB_IDS = (
    ("303a", "1001"),       # Espressif USB serial/JTAG: the X2 and the WiCAN
    ("0bfd", "0120"),       # Kvaser Leaf Light v2
    ("0c72", "0012"),       # PEAK PCAN-USB FD
    ("1d50", "606f"),       # gs_usb (candleLight): canchg, canmain
)
# the kernel's USB and CAN lines a VOID names (the reviewer's addendum, and
# its widening of 2026-11-16 after the hub swap): any device under the bench
# hub 1-3, by any driver ("usb 1-3.1:", "cdc_acm 1-3.2.4:1.0:", "usb
# 1-3.2-port4:"); the bench's devices by ID; the CAN drivers; -71, EPROTO,
# disconnects, a port disabled by its hub, and error -32
KLOG_RE = re.compile(r"\b1-3\b|"
                     + "|".join(r"idVendor=%s, idProduct=%s" % p for p in BENCH_USB_IDS)
                     + r"|gs_usb|kvaser_usb|peak_usb|pcan|-71\b|EPROTO|disconnect"
                       r"|disabled by hub|error -32\b",
                     re.I)
KLOG_MAX = 12                       # lines on a VOID line; the count says how many more
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

def netdev():
    """The netdev of role ROLE, from canctl's role table (ROLES, else
    adapter_roles.kvaser_netdev: socketcan mode on kvaser_usb). LinkError
    if the role does not resolve to one."""
    roles = ROLES
    if roles is None:
        import adapter_roles as roles
    try:
        dev = roles.kvaser_netdev()
    except Exception as e:                              # noqa: BLE001
        raise LinkError("role %s does not resolve to a netdev: %s" % (ROLE, e))
    if not dev or os.sep in dev or dev in (".", ".."):
        raise LinkError("role %s resolves to netdev %r: refused" % (ROLE, dev))
    return dev


def link_state(root=None):
    """-> "up" or "down", from IFF_UP in sysfs on the role's netdev.
    LinkError if the role does not resolve, or the netdev is missing or its
    flags cannot be read."""
    root = root or SYSFS_NET
    dev = netdev()
    p = os.path.join(root, dev, "flags")
    try:
        with open(p, encoding="ascii") as f:
            raw = f.read().strip()
        flags = int(raw, 16)
    except (OSError, ValueError) as e:
        raise LinkError("the %s link state cannot be read (%s: %s)" % (dev, p, e))
    return "up" if flags & IFF_UP else "down"


def require(up, root=None):
    """LinkError unless the link reads up (up=True) or down (up=False)."""
    want = "up" if up else "down"
    got = link_state(root)
    if got != want:
        raise LinkError("the %s link reads %s in sysfs, and this check needs it %s"
                        % (ROLE, got, want))
    return got


def self_test_refusal(argv0=None):
    """A refusal line when this process is a driver's --self-test (or its
    --can-fail copies, run the same way), else None. A self-test touches no
    hardware: bench_vport's once ran a real `canctl down kvaser` through
    NoNode() (2026-11-13, caught the same hour; the bench lock was free and
    the link already down)."""
    if "--self-test" in (argv0 if argv0 is not None else sys.argv):
        return "refused: a --self-test never touches the Kvaser's link"
    return None


def _layer():
    """The bench-role layer with the guard set (adapter_roles.layer), or
    LAYER."""
    if LAYER is not None:
        return LAYER
    import adapter_roles
    return adapter_roles.layer()


def _readback(got):
    if not isinstance(got, dict):
        return "read back"
    return "read back %s%s%s" % ("UP" if got.get("up") else "not UP",
                                 (" at %s" % got["bitrate"]) if got.get("up") else "",
                                 (" restart-ms %s" % got["restart_ms"])
                                 if got.get("up") and got.get("restart_ms") is not None else "")


def _link_up(L, restart_ms):
    """The layer's link_up at BITRATE, with restart_ms when one is asked
    (None: the link keeps the restart-ms it has), and its restart-ms read
    back here too, not left to the layer alone (the charge-interposer
    reviewer's ruling Q1, 2026-11-20). -> the read-back."""
    got = L.link_up(ROLE, BITRATE, restart_ms=restart_ms)
    if restart_ms is not None and (not isinstance(got, dict)
                                   or got.get("restart_ms") != restart_ms):
        raise LinkError("restart-ms read back %s, asked %d" % (
            got.get("restart_ms") if isinstance(got, dict) else "nothing", restart_ms))
    return got


def set_link(up, root=None, platform=None, restart_ms=None):
    """Bring the link up at BITRATE (an ACK check) or take it down (a no-ACK
    check) through the bench-role layer (link_up / link_down: canctl, then
    the read-back). restart_ms: the bus-off restart the link comes up with,
    read back (bench_vport's node: NODE_RESTART_MS); None leaves it as the
    link has it. -> a line saying so; LinkError if the layer refuses or
    fails, or the read-back is not what was asked. root is not read (the
    layer reads the link back itself); it stays for the callers."""
    if not linux(platform):
        raise LinkError("the Kvaser is opened on Linux only (role %s, canctl): the bench "
                        "host is madhouse-debian from port step 3" % ROLE)
    why = self_test_refusal()
    if why:
        raise LinkError(why)
    step = "link_up" if up else "link_down"
    try:
        L = _layer()
        got = _link_up(L, restart_ms) if up else L.link_down(ROLE)
    except Exception as e:                              # noqa: BLE001
        raise LinkError("bench_layer.%s %s failed: %s: %s" % (step, ROLE, type(e).__name__, e))
    return "%s link %s (bench_layer.%s, %s)" % (ROLE, "up" if up else "down", step,
                                                _readback(got))


COUNTER_KEYS = ("bus_off", "restarts", "tx_dropped", "tx_aborted_errors")


def link_counters():
    """The link's controller and TX counters, read-only, through the layer's
    link_counters (can-tools 49eb7777: `ip -details -statistics -statistics
    -json`, no root): a dict with at least COUNTER_KEYS and state, cumulative
    since the netdev appeared (the charge-interposer reviewer's ruling Q1,
    2026-11-20). LinkError if the layer cannot read them."""
    try:
        got = dict(_layer().link_counters(ROLE))
    except Exception as e:                              # noqa: BLE001
        raise LinkError("bench_layer.link_counters %s failed: %s: %s"
                        % (ROLE, type(e).__name__, e))
    lack = [k for k in COUNTER_KEYS if not isinstance(got.get(k), int)]
    if lack:
        raise LinkError("bench_layer.link_counters %s lacks %s" % (ROLE, ", ".join(lack)))
    return got


def counter_growth(c0, c1):
    """Two link_counters reads -> {key: growth} for COUNTER_KEYS. LinkError
    when a counter went back (the netdev was made again in between, as a
    usbreset does), so the growth is not known."""
    back = [k for k in COUNTER_KEYS if c1[k] < c0[k]]
    if back:
        raise LinkError("the %s link's counters went back (%s): the netdev was made again "
                        "in between" % (ROLE, ", ".join("%s %d -> %d" % (k, c0[k], c1[k])
                                                         for k in back)))
    return dict((k, c1[k] - c0[k]) for k in COUNTER_KEYS)


def check_channel(channel):
    """The Kvaser is asked for by its role, "kvaser"; anything else (None:
    the first netdev found, by position; another name) is refused."""
    if channel != ROLE:
        raise LinkError("the Kvaser is opened by its role %r, not %r (a netdev picked by "
                        "position, or another adapter's, is refused)" % (ROLE, channel))
    return channel


def open_bus(channel=ROLE, receive_own_messages=True, root=None, can_mod=None,
             platform=None, error_frames=True):
    """python-can's socketcan Bus on role kvaser's netdev (netdev()), after
    its link reads up. Echoes are kept by default (EchoWatch counts them as
    "sent"). Error frames come in by default, as python-can opens; with
    error_frames=False the socket takes none (python-can's
    ignore_rx_error_frames), for a sender that reads nothing but its echoes:
    with nothing acknowledging, the error frames come at thousands a second
    and fill the socket's receive queue, and an echo that arrives on a full
    queue is lost (2026-11-20, the hand take for the reviewer's ruling (2))."""
    if not linux(platform):
        raise LinkError("the Kvaser is opened on Linux only (role %s): the bench host is "
                        "madhouse-debian from port step 3" % ROLE)
    check_channel(channel)
    dev = netdev()
    require(True, root)
    if can_mod is None:
        import can as can_mod
    return can_mod.Bus(interface="socketcan", channel=dev,
                       receive_own_messages=receive_own_messages,
                       ignore_rx_error_frames=not error_frames)


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

    def __init__(self, bus, stall_s=STALL_S, on_rx=None, clock=None, on_echo=None):
        self.bus, self.stall_s, self.on_rx = bus, stall_s, on_rx
        self.on_echo = on_echo          # (key, t send returned, t echo read)
        self.clock = clock or perf
        self.pending = collections.deque()
        self.calls = self.echoed = self.stray = 0
        self.echo_times = []
        # True while no ACK is expected by design (bench_vport's no-ACK and
        # wrong-rate windows; the reviewer, 2026-11-13): the bound is not
        # checked; rebase() when the ACK returns
        self.suspended = False
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
        hit = None
        with self._lock:
            for i, (tr, pk) in enumerate(self.pending):
                if pk == k:
                    del self.pending[i]
                    self.echoed += 1
                    hit = (tr, self.clock())
                    self.echo_times.append(hit[1])
                    break
            else:
                self.stray += 1
        if hit is not None and self.on_echo is not None:
            self.on_echo(k, hit[0], hit[1])

    def rebase(self, t):
        """The ACK is back at t: every pending send's age counts from t at
        the earliest, so frames held through a no-ACK window get the bound
        from the ACK's return (the reviewer, 2026-11-13)."""
        with self._lock:
            for e in self.pending:
                if e[0] is not None and e[0] < t:
                    e[0] = t

    def check(self):
        if self.suspended:
            return
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

    def finish(self, forgive=0, excuse=None):
        """Read until every send is echoed; Stall once the bound passes.
        forgive: how many sends the kernel is known to have dropped
        (bench_vport's node: tx_dropped's growth while it was bus-off in
        wrong-rate windows; the charge-interposer reviewer's ruling Q1,
        2026-11-20). excuse(key) -> True for a send the caller does not
        judge (bench_vport's node: its sends from a wrong-rate span, the
        charge-interposer reviewer's ruling (a), 2026-11-20). Once the bound
        passes, the excused sends still unechoed, however many, and up to
        `forgive` of the others leave pending and are returned, not a Stall;
        more of the others than that is the Stall, its text saying how many
        were excused and forgiven. Echoes are waited for either way, so an
        excused send that does echo is counted echoed.
        -> the forgiven and excused entries ([t send returned, key]), []
        when every send echoed."""
        while self.pending:
            try:
                self.poll(0.005)
            except Stall as e:
                with self._lock:
                    n = len(self.pending)
                    k = sum(1 for x in self.pending
                            if excuse is not None and excuse(x[1]))
                    if n - k <= forgive:
                        gone = list(self.pending)
                        self.pending.clear()
                        return gone
                if forgive or k:
                    raise Stall("%s; %s%d of them may be forgiven as dropped by the "
                                "kernel, %d unaccounted"
                                % (e, ("%d of them excused, " % k) if k else "", forgive,
                                   n - k - forgive), e.t)
                raise
        return []


class WatchedBus:
    """A Kvaser bus (opened with its echoes kept) with the stall rule, for a
    sender that treats it as a plain python-can bus (vehicle_sim): send()
    goes through EchoWatch, recv() returns only frames that are not own
    echoes, and either raises Stall once the bound passes. recv(timeout)
    may return None before timeout when only echoes arrived."""

    def __init__(self, bus, stall_s=STALL_S, clock=None):
        self.bus = bus
        # the python-can bus underneath, which adapter_roles.bus_error
        # classifies error frames on (a WatchedBus is no SocketCAN class)
        self.inner = bus
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


def _recover_step(L, step, restart_ms=None):
    if step == "link_down":
        return L.link_down(ROLE)
    if step == "usbreset":
        return L.canctl("usbreset", ROLE)
    if step == "link_up":
        return _link_up(L, restart_ms)
    raise LinkError("no recovery step %r" % step)


def recover(root=None, environ=None, platform=None, restart_ms=None):
    """After a stall, with the check's socket closed, through the bench-role
    layer: link_down, canctl usbreset, link_up at BITRATE (with restart_ms
    when one is asked: a usbreset leaves the netdev at 0), each read back
    by the layer (RECOVER_STEPS). -> (ok, line). Not ok: the bench is
    stopped (the STOP file VTRUX_KVASER_STOP names), and the line says so.
    root is not read; it stays for the callers."""
    if not linux(platform):
        return False, "kvaser recovery: Linux only"
    why = self_test_refusal()
    if why:
        return False, "kvaser recovery FAILED, %s; %s" % (why, _stop(why, environ))
    for step in RECOVER_STEPS:
        try:
            _recover_step(_layer(), step, restart_ms)
        except Exception as e:                          # noqa: BLE001
            why = "%s %s failed: %s: %s" % (step, ROLE, type(e).__name__, e)
            return False, "kvaser recovery FAILED, %s; %s" % (why, _stop(why, environ))
    return True, ("kvaser recovered: link_down, canctl usbreset, link_up %d%s, each read back "
                  "(bench_layer)" % (BITRATE, (" restart-ms %d" % restart_ms)
                                     if restart_ms is not None else ""))


def stall_lines(stall, t0_epoch, close=None, journal=None, root=None, environ=None,
                restart_ms=None):
    """A stall's record: the VOID line (with the kernel's lines from the
    check's start to now), then the recovery's line (recover, with
    restart_ms). close(): shuts the check's Kvaser socket first (recovery
    needs it closed)."""
    lines = [void_line("%s at %s" % (stall, clock_text(epoch_of(stall.t))),
                       t0_epoch, wall(), journal)]
    if close is not None:
        try:
            close()
        except Exception as e:                          # noqa: BLE001
            lines.append("(closing the Kvaser socket raised %s: %s)" % (type(e).__name__, e))
    ok, line = recover(root, environ, restart_ms=restart_ms)
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
