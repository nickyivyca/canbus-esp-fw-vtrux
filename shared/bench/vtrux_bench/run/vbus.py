"""Virtual-bus slots for a run's own wall-clock checks (the charge-interposer
reviewer's job B, 2026-11-16; desk study B, AGENTS.md "One simulator bench
per set of ports").

A wall-clock check that names the virtual-bus segments it opens (Check.bus,
segment names of its suite's choosing) may run beside others of the same
run, up to the host's wallclock_slots, each on a bus of its own. This module
gives the engine four things, none of them project-specific:

  allocation(settings, slot, segments)
        {segment: (group, port)} for one slot, from settings.bus_alloc:
        group "239.0.<group_third + slot>.<i + 1>" and port
        port_base + slot * port_stride + i for the i-th segment. Handed to
        the check as the context key {bus_alloc} and the environment
        variable VTRUX_BUS_ALLOC, both "seg=group:port,seg=group:port"
        (format_alloc; parse_alloc reads it back).
  bound(ports)
        the UDP sockets bound to any of the ports now (ss -H -uanp): the
        check is refused if there is one, before anything of it starts.
  Monitor(alloc, token)
        joins each allocated segment as the check's own sockets do (bound to
        the port on every address, SO_REUSEADDR and SO_REUSEPORT, joined to
        the group), first listens (listen(): any datagram before the check
        starts refuses it), then records every datagram's sender port while
        the check runs. A sender port is the check's own when every process
        holding a socket on it, other than this runner, carries the check's
        token in its environment (VTRUX_CHECK_TOKEN, set by the engine and
        inherited by what the check starts), and at least one does; it is
        foreign otherwise, and so is a port no process could be found for.
        A socket on an allocated port held by a process without the token is
        foreign too. report() gives the counts per segment, each segment's
        rate, and the foreign senders and holders; any foreign datagram or
        holder makes the check VOID (the engine).

The rate is recorded for the comparison with a serial run (a symptom of
crossed buses is a rate many times the serial one: 5,780 fps against 351 on
2026-09-19); the judging of it is the comparison's, not this module's.

TTL 0 (hop_limit=0) and the LAN counters stay the suite's and the operator's
(AGENTS.md "A virtual CAN bus is not automatically local").

RULES: each is switched off by a mutant in tests/test_vbus.py, which must
then fail.
"""

import os
import re
import select
import socket
import struct
import subprocess
import threading
import time

RULES = {k: True for k in (
    "distinct_slots", "bound_check", "listen_first", "foreign_sender", "foreign_holder",
    "unattributed_foreign", "token_owner", "runner_excluded")}

TOKEN_ENV = "VTRUX_CHECK_TOKEN"
ALLOC_ENV = "VTRUX_BUS_ALLOC"
DEFAULTS = {"group_third": 3, "port_base": 43300, "port_stride": 10}
PID = re.compile(r"pid=(\d+)")


class BusError(Exception):
    pass


def alloc_config(settings):
    """settings.bus_alloc with its defaults, or None when the settings have
    none (no slot is allocated and wallclock_slots must be 1)."""
    cfg = settings.get("bus_alloc")
    if cfg is None:
        return None
    out = dict(DEFAULTS)
    out.update(cfg)
    for k in DEFAULTS:
        if not isinstance(out[k], int) or out[k] < 0:
            raise BusError("settings.bus_alloc.%s must be a whole number >= 0, not %r"
                           % (k, out[k]))
    return out


def allocation(settings, slot, segments):
    """{segment: (group, port)} for slot (0, 1, ...) and the check's segments
    in their order (see the module's docstring)."""
    cfg = alloc_config(settings)
    if cfg is None:
        raise BusError("no settings.bus_alloc")
    if len(set(segments)) != len(segments) or not segments:
        raise BusError("a check's bus names its segments once each: %r" % (segments,))
    if len(segments) > max(cfg["port_stride"], 1) and RULES["distinct_slots"]:
        raise BusError("%d segments do not fit bus_alloc.port_stride %d"
                       % (len(segments), cfg["port_stride"]))
    third = cfg["group_third"] + (slot if RULES["distinct_slots"] else 0)
    if third > 255 or len(segments) > 254:
        raise BusError("slot %d is past the groups bus_alloc gives" % slot)
    stride = cfg["port_stride"] if RULES["distinct_slots"] else 0
    out = {}
    for i, seg in enumerate(segments):
        port = cfg["port_base"] + slot * stride + i
        if port > 65535:
            raise BusError("slot %d is past the ports bus_alloc gives" % slot)
        out[seg] = ("239.0.%d.%d" % (third, i + 1), port)
    return out


def format_alloc(alloc):
    return ",".join("%s=%s:%d" % (s, g, p) for s, (g, p) in alloc.items())


def parse_alloc(text):
    """format_alloc's string back to {segment: (group, port)}."""
    out = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        try:
            seg, rest = part.split("=", 1)
            group, port = rest.rsplit(":", 1)
            out[seg.strip()] = (group.strip(), int(port))
        except ValueError:
            raise BusError("not a bus allocation: %r" % text)
    return out


def ss_lines(ss=("ss",)):
    r = subprocess.run(list(ss) + ["-H", "-uanp"], capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise BusError("ss -H -uanp: rc %s %s" % (r.returncode, r.stderr.strip()[:200]))
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def local_port(line):
    f = line.split()
    try:
        return int(f[3].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def holders(port, lines):
    """The pids holding a socket bound to port, from ss lines."""
    out = set()
    for ln in lines:
        if local_port(ln) == port:
            out |= {int(p) for p in PID.findall(ln)}
    return out


def bound(ports, lines=None):
    """The ss lines of UDP sockets bound to any of ports."""
    if not RULES["bound_check"]:
        return []
    lines = ss_lines() if lines is None else lines
    ports = set(ports)
    return [ln for ln in lines if local_port(ln) in ports]


def has_token(pid, token):
    """Whether process pid carries token in its environment (None if it
    cannot be read: gone, or not this user's)."""
    try:
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
    except OSError:
        return None
    return ("%s=%s" % (TOKEN_ENV, token)).encode() in env


def open_segment(group, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("", port))
    req = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("0.0.0.0"))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, req)
    s.setblocking(False)
    return s


class Monitor:
    """One check's isolation monitor (see the module's docstring)."""

    def __init__(self, alloc, token, poll_s=0.5, ss=("ss",), runner_pids=None):
        self.alloc = dict(alloc)
        self.token = token
        self.poll_s = poll_s
        self.ss = ss
        self.runner = set(runner_pids or {os.getpid()})
        self.socks = {}
        self.counts = {s: 0 for s in self.alloc}
        self.senders = {}            # port -> count
        self.sender_seg = {}         # port -> set of segments
        self.owner = {}              # port -> True / False (attributed)
        self.pid_own = {}            # pid -> True / False / None
        self.foreign_holders = {}    # pid -> port
        self.before = 0
        self.errors = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads = []
        self.t0 = self.t1 = None

    def open(self):
        for seg, (g, p) in self.alloc.items():
            self.socks[seg] = open_segment(g, p)
        return self

    def _read(self, timeout):
        socks = list(self.socks.values())
        r, _, _ = select.select(socks, [], [], timeout)
        got = []
        for s in r:
            seg = next(k for k, v in self.socks.items() if v is s)
            while True:
                try:
                    _data, addr = s.recvfrom(65535)
                except (BlockingIOError, InterruptedError):
                    break
                got.append((seg, addr[1]))
        return got

    def listen(self, seconds):
        """Datagrams heard on the segments over `seconds`, before the check
        starts (each one refuses it)."""
        end = time.monotonic() + seconds
        n = 0
        while time.monotonic() < end:
            n += len(self._read(max(0.0, min(0.2, end - time.monotonic()))))
        self.before = n if RULES["listen_first"] else 0
        return self.before

    def start(self):
        self.t0 = time.monotonic()
        for fn in (self._recv_loop, self._attr_loop):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def _recv_loop(self):
        while not self._stop.is_set():
            try:
                got = self._read(0.2)
            except (OSError, ValueError) as e:
                self.errors.append(repr(e))
                return
            with self._lock:
                for seg, port in got:
                    self.counts[seg] += 1
                    self.senders[port] = self.senders.get(port, 0) + 1
                    self.sender_seg.setdefault(port, set()).add(seg)

    def _own(self, pid):
        if pid not in self.pid_own or self.pid_own[pid] is None:
            self.pid_own[pid] = has_token(pid, self.token) if RULES["token_owner"] else True
        return self.pid_own[pid]

    def attribute(self):
        """One pass: attribute new sender ports and look for foreign holders
        of the allocated ports."""
        try:
            lines = ss_lines(self.ss)
        except (BusError, OSError, subprocess.SubprocessError) as e:
            self.errors.append(str(e))
            return
        with self._lock:
            pending = [p for p in self.senders if p not in self.owner]
        for port in pending:
            pids = holders(port, lines)
            if RULES["runner_excluded"]:
                pids -= self.runner
            if not pids:
                continue                  # not seen yet; tried again, then at the end
            own = [self._own(p) for p in pids]
            if all(o is not None for o in own):
                self.owner[port] = all(own)
        for seg, (_g, port) in self.alloc.items():
            for pid in holders(port, lines) - self.runner:
                if self._own(pid) is False:
                    self.foreign_holders.setdefault(pid, port)

    def _attr_loop(self):
        while not self._stop.wait(self.poll_s):
            self.attribute()

    def stop(self):
        self.attribute()
        self._stop.set()
        for t in self._threads:
            t.join(5)
        self.t1 = time.monotonic()
        for s in self.socks.values():
            try:
                s.close()
            except OSError:
                pass
        return self.report()

    def close(self):
        self._stop.set()
        for s in self.socks.values():
            try:
                s.close()
            except OSError:
                pass

    def report(self):
        """{counts, fps, senders, foreign {port: count}, unattributed {port:
        count}, foreign_holders {pid: port}, foreign_datagrams, void (why or
        None), errors}."""
        dur = max((self.t1 or time.monotonic()) - (self.t0 or time.monotonic()), 1e-9)
        foreign = {p: n for p, n in self.senders.items()
                   if self.owner.get(p) is False and RULES["foreign_sender"]}
        unattr = {p: n for p, n in self.senders.items() if p not in self.owner}
        bad = dict(foreign)
        if RULES["unattributed_foreign"]:
            bad.update(unattr)
        holders_ = self.foreign_holders if RULES["foreign_holder"] else {}
        whys = []
        if bad:
            whys.append("%d datagram(s) from %d sender port(s) not this check's (%s)" % (
                sum(bad.values()), len(bad), ", ".join(
                    "port %d x%d%s" % (p, n, "" if p in foreign else " unattributed")
                    for p, n in sorted(bad.items())[:5])))
        if holders_:
            whys.append("%d process(es) not this check's held an allocated port (%s)" % (
                len(holders_), ", ".join("pid %d on %d" % kv for kv in sorted(holders_.items())[:5])))
        return {"counts": dict(self.counts),
                "fps": {s: round(n / dur, 1) for s, n in self.counts.items()},
                "duration_s": round(dur, 1),
                "senders": {str(p): n for p, n in sorted(self.senders.items())},
                "foreign": {str(p): n for p, n in sorted(foreign.items())},
                "unattributed": {str(p): n for p, n in sorted(unattr.items())},
                "foreign_holders": {str(k): v for k, v in sorted(holders_.items())},
                "foreign_datagrams": sum(bad.values()),
                "void": "; ".join(whys) or None,
                "errors": self.errors[:5]}
