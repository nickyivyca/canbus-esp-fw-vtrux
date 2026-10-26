#!/usr/bin/env python3
"""Proof by running that gi_vbus keeps the virtual bus on this host.

Gen-inhibit tester, 2026-10-08. Reading _confine_to_host is not evidence that
it works; this runs it.

  1. Refusals, on REAL python-can buses deliberately broken three ways:
     a forced non-zero TTL, a missing socket, a setsockopt that fails. Each
     must raise SystemExit naming firmware/esp-development.md. Plus a non-
     virtual transport, which gi_vbus must refuse rather than guess at.
  2. A clean open reads the TTL back as 0 from the socket itself.
  3. Each rig (bench_vehicle.py, live_bench.py) is run for a few seconds as
     its own process. Its stderr must carry gi_vbus's "TTL read back 0" line,
     a listener on its segment must count frames flowing, and this machine's
     network adapters' sent-multicast counters must stay flat meanwhile.
  4. Positive control for 3, so a flat counter means something: 100 datagrams
     sent deliberately with TTL 1 (to a group no bench uses) must show up on
     those counters. Without it, counters that never move would pass 3.
     100 small datagrams once is harmless; the 2026-09-01 incident was 66,450
     per second for twenty minutes.

Windows only for 3 and 4 (the counters come from Get-NetAdapterStatistics);
elsewhere they are reported SKIPPED and the exit status says so.

    py -3.14 test_gi_vbus.py          # exit 0 only if every check passes
"""
import os
import socket
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import can            # noqa: E402
import gi_vbus        # noqa: E402

GUIDE = "firmware/esp-development.md"
SPARE_GROUP, SPARE_PORT = "239.0.2.250", 43299   # used by no bench
RIGS = [
    ("bench_vehicle.py", ["--duration", "6", "--no-start"], "239.0.2.4", 43217),
    ("live_bench.py", ["--duration", "6", "--time-scale", "1"], "239.0.2.3", 43216),
]
MIN_FRAMES = 300            # the listener must see at least this many
MAX_ADAPTER_DELTA = 40      # background mDNS/SSDP allowance over a rig run
CONTROL_N = 100

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print("  [%s] %s%s" % ({True: "PASS", False: "FAIL", None: "SKIP"}[ok], name,
                           (" -- " + detail) if detail else ""), flush=True)


def ns(transport="virtual", channel="239.0.2.251", port=43298):
    return types.SimpleNamespace(transport=transport, channel=channel,
                                 port=port, bitrate=500000)


def refuses(fn):
    """(refused?, message). A refusal is SystemExit naming GUIDE."""
    try:
        fn()
    except SystemExit as e:
        msg = str(e.code)
        return GUIDE in msg or "only the virtual transport" in msg, msg.splitlines()[0]
    return False, "did not refuse"


class SetIsNoop:
    """A real socket whose setsockopt does nothing, so its TTL stays as opened."""
    def __init__(self, sock):
        self._s = sock

    def setsockopt(self, *a):
        pass

    def __getattr__(self, k):
        return getattr(self._s, k)


class SetFails(SetIsNoop):
    def setsockopt(self, *a):
        raise OSError("injected")


def patched_open(breaker):
    """Run gi_vbus.open_bus end to end with its python-can Bus broken."""
    made = []

    def bus_factory(**kw):
        kw["hop_limit"] = 1                     # the python-can default
        b = can.interface.Bus(**kw)
        made.append(b)
        breaker(b)
        return b
    fake_can = types.SimpleNamespace(interface=types.SimpleNamespace(Bus=bus_factory),
                                     __version__=can.__version__)
    real = gi_vbus.can
    gi_vbus.can = fake_can
    try:
        return refuses(lambda: gi_vbus.open_bus(ns()))
    finally:
        gi_vbus.can = real
        for b in made:
            try:
                b.shutdown()
            except Exception:
                pass
        while DROPPED:
            try:
                DROPPED.pop()._socket.close()
            except Exception:
                pass


def wrap(cls):
    def f(b):
        b._multicast._socket = cls(b._multicast._socket)
    return f


DROPPED = []


def drop_socket(b):
    DROPPED.append(b._multicast)      # kept so its socket can still be closed
    b._multicast = None


# ------------------------------------------------------------ 1 and 2

def part_refusals():
    print("1. refusals on real buses")
    ok, msg = patched_open(wrap(SetIsNoop))
    check("forced non-zero TTL (hop_limit=1, set ignored) refuses", ok, msg)
    ok, msg = patched_open(drop_socket)
    check("missing socket refuses (fails closed)", ok, msg)
    ok, msg = patched_open(wrap(SetFails))
    check("setsockopt failure refuses", ok, msg)
    ok, msg = refuses(lambda: gi_vbus.open_bus(ns(transport="pcan")))
    check("non-virtual transport refuses", ok, msg)

    print("2. clean open")
    b = gi_vbus.open_bus(ns())
    try:
        ttl = b._multicast._socket.getsockopt(socket.IPPROTO_IP,
                                              socket.IP_MULTICAST_TTL)
        check("TTL read back from the socket is 0", ttl == 0, "ttl=%d" % ttl)
    finally:
        b.shutdown()


# ------------------------------------------------------------ 3 and 4

def adapter_multicast():
    """{adapter: SentMulticastPackets}, or None off Windows."""
    if os.name != "nt":
        return None
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-NetAdapterStatistics | ForEach-Object { $_.Name + '|' + "
         "$_.SentMulticastPackets }"],
        capture_output=True, text=True, timeout=60).stdout
    d = {}
    for ln in out.splitlines():
        if "|" in ln:
            k, v = ln.rsplit("|", 1)
            if v.strip().isdigit():
                d[k.strip()] = int(v)
    return d


def delta(a, b):
    return {k: b[k] - a[k] for k in b if k in a}


class Listener(threading.Thread):
    def __init__(self, group, port):
        super().__init__(daemon=True)
        self.bus = gi_vbus.open_bus(ns(channel=group, port=port))
        self.n = 0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            if self.bus.recv(timeout=0.2) is not None:
                self.n += 1

    def close(self):
        self.stop.set()
        self.join(2)
        self.bus.shutdown()


def part_control():
    print("4. positive control: TTL-1 multicast must move the counters")
    before = adapter_multicast()
    if before is None:
        check("counters see deliberate TTL-1 multicast", None, "not Windows")
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    for _ in range(CONTROL_N):
        s.sendto(b"gi_vbus control", (SPARE_GROUP, SPARE_PORT))
        time.sleep(0.005)
    s.close()
    time.sleep(2.0)
    d = delta(before, adapter_multicast())
    best = max(d.values()) if d else 0
    ok = best >= CONTROL_N * 0.9
    check("counters see %d deliberate TTL-1 datagrams" % CONTROL_N, ok,
          "per-adapter delta %s" % d)
    return ok


def part_rigs(control_ok):
    print("3. each rig, run for real")
    for script, extra, group, port in RIGS:
        lis = Listener(group, port)
        lis.start()
        before = adapter_multicast()
        p = subprocess.Popen([sys.executable, os.path.join(HERE, script)] + extra,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        try:
            out, err = p.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
            check(script + " finished", False, "killed after 90 s")
        time.sleep(1.0)
        n_during = lis.n
        time.sleep(1.0)
        n_after = lis.n - n_during
        lis.close()
        after = adapter_multicast()
        ttl_lines = [ln for ln in err.splitlines() if ln.startswith("gi_vbus:")]
        check(script + " exits 0", p.returncode == 0, "rc=%s %s" % (
            p.returncode, err.strip().splitlines()[-1] if p.returncode and err.strip() else ""))
        check(script + " reports TTL read back 0",
              bool(ttl_lines) and all("read back 0 " in ln for ln in ttl_lines),
              "%d bus(es): %s" % (len(ttl_lines), ttl_lines[:1]))
        check(script + " frames flowed on %s:%d" % (group, port),
              n_during >= MIN_FRAMES, "%d frames seen" % n_during)
        check(script + " traffic stopped when it exited", n_after == 0,
              "%d frames in the 1 s after" % n_after)
        if before is None:
            check(script + " adapter counters flat", None, "not Windows")
            continue
        d = delta(before, after)
        worst = max(d.values()) if d else 0
        check(script + " adapter counters flat", control_ok and worst <= MAX_ADAPTER_DELTA,
              "max per-adapter sent-multicast delta %d over %d frames%s"
              % (worst, n_during, "" if control_ok else " (control failed, so no proof)"))


def main():
    print("gi_vbus proof, python-can %s, %s" % (can.__version__, sys.executable))
    part_refusals()
    control_ok = part_control()
    part_rigs(control_ok)
    bad = [n for n, ok in results if ok is False]
    skipped = [n for n, ok in results if ok is None]
    print("\n=== %s === %d pass, %d fail, %d skipped" % (
        "FAIL" if bad else ("INCOMPLETE" if skipped else "PASS"),
        sum(1 for _, ok in results if ok), len(bad), len(skipped)))
    return 1 if bad else (2 if skipped else 0)


if __name__ == "__main__":
    sys.exit(main())
